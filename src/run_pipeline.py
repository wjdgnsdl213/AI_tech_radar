"""파이프라인 오케스트레이터 — 일간/주간 배치를 한 번에 돌린다.

각 단계는 기존처럼 독립 모듈(`python -m src.X`)을 subprocess로 부른다.
"단계별 스크립트는 독립 실행 가능해야 한다"는 원칙(CLAUDE.md)을 그대로 지키면서
순서만 여기서 관리하는 구조다.

★ 일간과 주간을 나누는 이유
    수집은 소급되지 않는다. 네이버는 쿼리당 최근 1,000건이 상한이라 오늘 안 받으면
    오늘 기사는 영영 못 받는다. 반면 해설·다이제스트는 언제 돌려도 같은 결과가 나온다.
    그래서 수집·처리는 매일, 비용이 드는 해설과 발송은 주 1회로 나눈다.

      일간   collect → prefilter → filter → score        (매일, 몇 분)
      주간   insight(L1/L2) → digest → mailer            (주 1회, LLM 비용 발생)

★ 실패해도 뒤 단계를 판단해서 진행한다
    수집이 실패해도 기존 데이터로 처리·다이제스트는 나가야 한다(fail-open).
    다만 처리 단계가 실패하면 그 뒤는 의미가 없으므로 멈춘다.

실행:
  python -m src.run_pipeline --daily       # 수집 + 처리
  python -m src.run_pipeline --weekly      # 해설 + 다이제스트 + 메일
  python -m src.run_pipeline --all
  python -m src.run_pipeline --weekly --dry-run   # 메일을 보내지 않고 미리보기
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

# Windows 콘솔(cp949)에서 특수문자 출력 깨짐 방지
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

# (모듈, 표시명, 인자, 실패 시 중단할까)
#   수집 실패는 넘어간다 — 기존 데이터로도 다이제스트는 나가야 한다.
#   처리 실패는 멈춘다 — 축·점수가 없으면 뒤 단계가 무의미하다.
DAILY = [
    ("collect",   "수집 (소스 순회)",      [], False),
    ("prefilter", "축 키워드 prefilter",   [], True),
    ("filter",    "임베딩 필터",           [], True),
    ("score",     "교차 점수",             ["--top", "0"], True),
    # 키워드 추출은 통과 항목이 바뀔 때마다 다시 해야 한다(5초). 실패해도
    # 다이제스트는 📈 섹션만 빠진 채 나가므로 막지 않는다.
    ("extract",   "키워드 추출",           ["--top", "0"], False),
]
WEEKLY = [
    # 급상승 CSV는 빅데이터팀이 자기 분석에 재활용하는 산출물이다(PLAN §3-B).
    # 다이제스트는 자체적으로 trend를 계산하므로 이 단계가 실패해도 상관없다.
    ("trend",   "급상승 키워드 CSV", ["--out", "reports/"], False),
    ("insight", "AI 해설 (L1/L2)", [], False),   # LLM 장애여도 다이제스트는 나간다
    ("digest",  "다이제스트 생성",  ["--out", "reports/"], True),
    ("mailer",  "메일 발송",        [], False),
]


# ── 중복 실행 방지 ──────────────────────────────────────────────────
# 일간(매일 06:00)과 주간(월요일 07:00)은 서로 겹칠 수 있다. 일간이 길어지거나
# PC가 꺼져 있어 밀린 작업이 한꺼번에 깨어나면 두 프로세스가 같은 DB를 만진다.
# 특히 filter는 임베딩 캐시 파일(npz)을 통째로 다시 쓰므로 동시에 돌면 깨진다.
LOCK = Path(__file__).resolve().parents[1] / "data" / ".pipeline.lock"
# 가장 긴 배치가 8분이므로 2시간이면 죽은 것이 확실하다. 더 길게 잡으면
# 정전 등으로 남은 잠금이 그만큼 오래 뒷 실행을 막는다 — 특히 일간(06:00)이
# 하드 크래시하면 주간(월 07:30)까지 같이 건너뛴다.
STALE_AFTER = timedelta(hours=2)


def acquire_lock() -> bool:
    """잠금을 잡으면 True. 이미 돌고 있으면 False.

    PID 생존 확인은 OS마다 다르고 PID는 재사용된다. 대신 **나이**로 판단한다 —
    가장 긴 주간 배치도 한 시간이면 끝나므로 6시간이면 죽은 것이 확실하다.
    """
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    if LOCK.exists():
        try:
            age = datetime.now() - datetime.fromtimestamp(LOCK.stat().st_mtime)
        except OSError:
            age = STALE_AFTER + timedelta(seconds=1)
        if age < STALE_AFTER:
            print(f"⏭  다른 배치가 실행 중입니다 ({LOCK.read_text(errors='replace').strip()}, "
                  f"{age.total_seconds() / 60:.0f}분 경과) — 이번 실행은 건너뜁니다.")
            return False
        print(f"⚠ 오래된 잠금을 회수합니다 ({age.total_seconds() / 3600:.1f}시간 전).")
    LOCK.write_text(f"pid={os.getpid()} start={datetime.now():%Y-%m-%d %H:%M:%S}",
                    encoding="utf-8")
    return True


def release_lock() -> None:
    try:
        LOCK.unlink(missing_ok=True)
    except OSError:
        pass


def run_step(module: str, label: str, extra: list[str]) -> tuple[bool, float]:
    print(f"\n{'=' * 60}\n▶ {label}  (src.{module})\n{'=' * 60}")
    t0 = time.time()
    # 하위 프로세스 출력을 그대로 흘려보내 스케줄러 로그에 남긴다
    result = subprocess.run([sys.executable, "-m", f"src.{module}", *extra])
    elapsed = time.time() - t0
    ok = result.returncode == 0
    print(f"{'✅' if ok else '❌'} {label} ({elapsed:.0f}초, exit={result.returncode})")
    return ok, elapsed


def run_phase(steps, name: str, dry_run: bool) -> list[tuple[str, bool, float]]:
    print(f"\n\n{'#' * 60}\n#  {name}\n{'#' * 60}")
    log: list[tuple[str, bool, float]] = []
    for module, label, extra, blocking in steps:
        args = list(extra)
        if dry_run and module == "mailer":
            args.append("--dry-run")
        ok, elapsed = run_step(module, label, args)
        log.append((label, ok, elapsed))
        if not ok and blocking:
            print(f"\n⚠ {label} 실패 — 뒤 단계는 의미가 없어 중단합니다.")
            break
        if not ok:
            print(f"  ↳ 실패했지만 뒤 단계를 계속합니다 (이 단계는 필수가 아님)")
    return log


def _run(args) -> int:
    """실제 배치. 종료 코드를 돌려준다 (잠금 해제는 호출자가 책임진다)."""
    do_daily = args.daily or args.all or not (args.daily or args.weekly or args.all)
    do_weekly = args.weekly or args.all

    t0 = time.time()
    print(f"파이프라인 시작 — {time.strftime('%Y-%m-%d %H:%M')}")
    log: list[tuple[str, bool, float]] = []
    if do_daily:
        log += run_phase(DAILY, "일간 — 수집 · 처리", args.dry_run)
    if do_weekly:
        log += run_phase(WEEKLY, "주간 — 해설 · 다이제스트 · 발송", args.dry_run)

    print()
    print()
    print("=" * 60)
    print(f"■ 배치 요약  (총 {(time.time() - t0) / 60:.1f}분)")
    print("=" * 60)
    for label, ok, elapsed in log:
        print(f"  {'✅' if ok else '❌'}  {label:<26}{elapsed:>7.0f}초")
    failed = [l for l, ok, _ in log if not ok]
    if failed:
        print()
        print(f"⚠ 실패 {len(failed)}개: {', '.join(failed)}")
        return 1
    print()
    print("✅ 정상 완료")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="파이프라인 배치")
    parser.add_argument("--daily", action="store_true", help="수집 + 처리")
    parser.add_argument("--weekly", action="store_true", help="해설 + 다이제스트 + 메일")
    parser.add_argument("--all", action="store_true", help="일간 + 주간")
    parser.add_argument("--dry-run", action="store_true", help="메일을 보내지 않는다")
    parser.add_argument("--no-lock", action="store_true",
                        help="중복 실행 방지를 끈다 (손으로 돌릴 때)")
    args = parser.parse_args()

    if args.no_lock:
        sys.exit(_run(args))

    if not acquire_lock():
        return          # 겹친 건 오류가 아니다 — 종료 코드 0으로 조용히 끝낸다
    # ★ finally로 반드시 푼다.
    #   잠금이 남으면 이후 실행이 전부 "다른 배치가 실행 중"으로 건너뛰는데,
    #   그때 종료 코드는 0이라 **작업 스케줄러에는 성공으로 보인다.**
    #   아무것도 안 돌면서 성공으로 보이는 게 가장 알아채기 어려운 실패다
    #   (실측: 해제 코드가 빠져 있어 스케줄러 첫 실행이 통째로 건너뛰어졌다).
    try:
        code = _run(args)
    finally:
        release_lock()
    sys.exit(code)


if __name__ == "__main__":
    main()
