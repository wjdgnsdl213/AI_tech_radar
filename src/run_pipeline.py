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
import subprocess
import sys
import time

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


def main() -> None:
    parser = argparse.ArgumentParser(description="파이프라인 배치")
    parser.add_argument("--daily", action="store_true", help="수집 + 처리")
    parser.add_argument("--weekly", action="store_true", help="해설 + 다이제스트 + 메일")
    parser.add_argument("--all", action="store_true", help="일간 + 주간")
    parser.add_argument("--dry-run", action="store_true", help="메일을 보내지 않는다")
    args = parser.parse_args()

    do_daily = args.daily or args.all or not (args.daily or args.weekly or args.all)
    do_weekly = args.weekly or args.all

    t0 = time.time()
    print(f"파이프라인 시작 — {time.strftime('%Y-%m-%d %H:%M')}")
    log: list[tuple[str, bool, float]] = []
    if do_daily:
        log += run_phase(DAILY, "일간 — 수집 · 처리", args.dry_run)
    if do_weekly:
        log += run_phase(WEEKLY, "주간 — 해설 · 다이제스트 · 발송", args.dry_run)

    print(f"\n\n{'=' * 60}\n■ 배치 요약  (총 {(time.time() - t0) / 60:.1f}분)\n{'=' * 60}")
    for label, ok, elapsed in log:
        print(f"  {'✅' if ok else '❌'}  {label:<26}{elapsed:>7.0f}초")
    failed = [l for l, ok, _ in log if not ok]
    if failed:
        print(f"\n⚠ 실패 {len(failed)}개: {', '.join(failed)}")
        sys.exit(1)
    print("\n✅ 정상 완료")


if __name__ == "__main__":
    main()
