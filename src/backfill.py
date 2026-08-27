"""백필 드라이버 — 1회성 대량 수집.

`collect.py`(매일 반복, 수백 건, 분 단위)와 성격이 완전히 달라 별도 스크립트로 둔다.
백필은 수만 건 · 시간 단위 작업이라 **중간에 죽어도 이어서 돌 수 있어야 한다.**
(sobiz에서 파이프라인이 임베딩 필터 중간에 죽어 처음부터 다시 돌려야 했던 문제)

체크포인트 규약:
    data/checkpoints/{source}.json 에 작업(task)별 커서를 저장한다.
    **배치를 디스크에 쓴 다음에** 커서를 갱신한다 — 순서가 반대면 저장 실패 시 유실된다.

실행:
  python -m src.backfill --source hn
  python -m src.backfill --source geeknews --years 1
  python -m src.backfill --source all
  python -m src.backfill --source hn --reset      # 체크포인트 지우고 처음부터
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from src.db import get_engine, init_db, load_config, upsert_items
from src.sources.base import Source
from src.sources.geeknews import GeekNewsSource
from src.sources.hackernews import HackerNewsSource

# Windows 콘솔(cp949)에서 특수문자 출력 깨짐 방지.
# line_buffering: 백필은 몇 시간짜리라 파일로 리다이렉트해도 진행 상황이 바로 보여야 한다
# (기본 블록 버퍼링이면 프로세스가 끝날 때까지 로그가 텅 비어 있다).
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

# --source 인자 → 어댑터 클래스 + config 키
REGISTRY: dict[str, tuple[type[Source], str]] = {
    "hn": (HackerNewsSource, "hackernews"),
    "geeknews": (GeekNewsSource, "geeknews"),
}


class Checkpoint:
    """작업별 커서를 파일에 보관한다. 프로세스가 죽어도 여기서 이어간다.

    done 플래그만으로는 부족하다 — "어느 기간까지" 완료했는지를 같이 기록해야 한다.
    (1개월 테스트로 done이 찍힌 뒤 3년을 요청하면 전부 건너뛰는 버그를 겪었다)
    그래서 covered_since를 남기고, 나중에 더 과거를 요청하면 resume_cursor에서 이어간다.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.data: dict[str, Any] = {}
        if path.exists():
            self.data = json.loads(path.read_text(encoding="utf-8"))

    def get(self, task: str) -> dict[str, Any]:
        return self.data.get(task, {})

    def update(self, task: str, cursor: str | None, collected: int, done: bool,
               since: datetime, resume_cursor: str | None = None) -> None:
        prev = self.data.get(task, {})
        entry = {
            "cursor": cursor,
            "collected": prev.get("collected", 0) + collected,
            "done": done,
            "covered_since": since.isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if done:
            # 나중에 더 과거를 요청하면 여기서부터 이어 내려간다
            entry["resume_cursor"] = resume_cursor or prev.get("resume_cursor")
        self.data[task] = entry
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def plan(self, task: str, since: datetime) -> tuple[bool, str | None, str]:
        """(건너뛸까, 시작 커서, 사유)를 결정한다."""
        st = self.get(task)
        if not st:
            return False, None, "시작"
        if not st.get("done"):
            return False, st.get("cursor"), f"커서 {st.get('cursor')}에서 재개"

        covered = st.get("covered_since")
        if covered:
            try:
                if since >= datetime.fromisoformat(covered):
                    return True, None, f"이미 완료 ({st.get('collected', 0):,}건)"
            except ValueError:
                pass
        # 요청 범위가 기존보다 과거로 넓어졌다 → 못 채운 구간만 이어서 가져온다
        return False, st.get("resume_cursor"), "범위 확대 — 이전 완료 지점부터 이어서"


def run_source(key: str, cfg: dict[str, Any], years: float | None, reset: bool) -> int:
    """한 소스를 백필한다. 수집 총 건수를 반환."""
    cls, cfg_key = REGISTRY[key]
    src_cfg = cfg["sources"][cfg_key]
    if not src_cfg.get("enabled", True):
        print(f"⏭  {cfg_key}: config에서 비활성화됨")
        return 0

    common = dict(cfg.get("backfill", {}))
    contact = os.getenv("CONTACT_EMAIL", "unknown")
    common["user_agent"] = common.get(
        "user_agent", "ai-tech-radar/0.1 (contact: {contact})"
    ).format(contact=contact)

    ck_path = Path(cfg["backfill"]["checkpoint_dir"]) / f"{cfg_key}.json"
    if reset and ck_path.exists():
        ck_path.unlink()
        print(f"  체크포인트 초기화: {ck_path}")
    ck = Checkpoint(ck_path)

    span_years = years if years is not None else float(src_cfg.get("backfill_years", 3))
    until = datetime.now(timezone.utc)
    since = until - timedelta(days=365 * span_years)

    out_dir = Path(cfg["backfill"]["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{cfg_key}_backfill.jsonl"

    engine = get_engine()
    init_db(engine)

    base = cls(src_cfg, common)
    total = 0
    print(f"\n{'=' * 60}\n▶ {cfg_key} 백필  ({since:%Y-%m-%d} ~ {until:%Y-%m-%d})\n{'=' * 60}")

    for task in base.tasks():
        label = task or "기본"
        skip, cursor, reason = ck.plan(task, since)
        if skip:
            print(f"  ✅ [{label}] {reason} — 건너뜀")
            continue
        print(f"  ▷ [{label}] {reason}")

        src = base.with_task(task)
        task_total = 0
        last_cursor = cursor    # 완료 시 '이어갈 지점'으로 기록해둔다
        while True:
            batch = src.fetch(since, until, cursor)

            if batch.items:
                # ★ 먼저 저장하고, 그 다음에 커서를 갱신한다
                with open(out_path, "a", encoding="utf-8") as f:
                    for item in batch.items:
                        f.write(json.dumps(item.to_dict(), ensure_ascii=False) + "\n")
                new = upsert_items([i.to_dict() for i in batch.items], engine)
                task_total += len(batch.items)
                total += len(batch.items)
                print(f"      +{len(batch.items):>4}건 (신규 {new:>4}) 누적 {task_total:>6,}  {batch.note}")

            ck.update(task, batch.cursor, len(batch.items), done=batch.cursor is None,
                      since=since, resume_cursor=last_cursor)
            if batch.cursor is None:
                print(f"  ✅ [{label}] 완료 — {task_total:,}건")
                break
            last_cursor = cursor = batch.cursor

    print(f"\n{cfg_key} 백필 총 {total:,}건 → {out_path}")
    return total


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="1회성 대량 백필 (체크포인트 재개 지원)")
    parser.add_argument("--source", required=True,
                        choices=[*REGISTRY.keys(), "all"], help="백필할 소스")
    parser.add_argument("--years", type=float, default=None,
                        help="수집 기간(년). 기본값은 소스별 config")
    parser.add_argument("--reset", action="store_true",
                        help="체크포인트를 지우고 처음부터 다시")
    args = parser.parse_args()

    cfg = load_config()
    targets = list(REGISTRY.keys()) if args.source == "all" else [args.source]

    grand = 0
    for key in targets:
        try:
            grand += run_source(key, cfg, args.years, args.reset)
        except KeyboardInterrupt:
            # 체크포인트가 저장돼 있으므로 같은 명령으로 이어서 돌리면 된다
            print(f"\n\n⚠ 중단됨. 체크포인트가 저장돼 있으니 같은 명령으로 재개하세요.")
            sys.exit(130)

    print(f"\n{'=' * 60}\n■ 백필 전체 완료 — 총 {grand:,}건")
    print("  다음: python -m src.db --summary 로 누적 현황 확인")


if __name__ == "__main__":
    main()
