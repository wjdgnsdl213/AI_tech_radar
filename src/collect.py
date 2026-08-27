"""일일 수집 드라이버 — 매일 1회 실행되는 반복 수집.

`backfill.py`(1회성 대량)와 역할이 다르다. 여기서는 두 가지를 한다:

  1) 전방 수집 — 새로 올라온 것을 가져온다. RSS/API 한 번 호출이라 부담이 없다.
  2) 과거분 소량 보충 — 하루 예산(daily_crawl_limit)만큼만 과거로 내려간다.
     체크포인트가 남으므로 며칠에 걸쳐 축적된다.

GeekNews 과거분 수집 원칙:
    서버가 403/429로 거부하면 **그 실행을 즉시 끝낸다.** 재시도·우회하지 않는다.
    커서는 남으므로 다음 날 이어서 받는다. 차단이 잦으면 daily_crawl_limit을 낮춘다.

실행:
  python -m src.collect                      # 전체 소스
  python -m src.collect --source geeknews
  python -m src.collect --no-crawl           # 전방 수집만 (과거분 보충 생략)
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

from src.backfill import Checkpoint
from src.db import get_engine, init_db, load_config, upsert_items
from src.sources.geeknews import GeekNewsSource

# Windows 콘솔(cp949)에서 특수문자 출력 깨짐 방지
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

BATCH_SIZE = 50   # geeknews.fetch()가 한 번에 처리하는 id 수


def _user_agent(cfg: dict[str, Any]) -> str:
    contact = os.getenv("CONTACT_EMAIL", "unknown")
    tpl = cfg.get("backfill", {}).get(
        "user_agent", "ai-tech-radar/0.1 (contact: {contact})")
    return tpl.format(contact=contact)


def _append_raw(path: Path, items: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for it in items:
            f.write(json.dumps(it.to_dict(), ensure_ascii=False) + "\n")


def collect_geeknews(cfg: dict[str, Any], allow_crawl: bool) -> int:
    src_cfg = cfg["sources"]["geeknews"]
    if not src_cfg.get("enabled", True):
        print("⏭  geeknews: 비활성화됨")
        return 0

    common = {"user_agent": _user_agent(cfg)}
    src = GeekNewsSource(src_cfg, common)
    engine = get_engine()
    init_db(engine)
    raw_dir = Path(cfg["backfill"]["out_dir"])
    total_new = 0

    # ── ① 전방 수집 (RSS) — 항상 실행. 배포용 공개 채널이라 부담이 없다 ──
    print(f"\n{'=' * 60}\n▶ GeekNews 전방 수집 (RSS)\n{'=' * 60}")
    recent = src.fetch_recent()
    if recent:
        _append_raw(raw_dir / "geeknews_rss.jsonl", recent)
        new = upsert_items([i.to_dict() for i in recent], engine)
        total_new += new
        print(f"  피드 {len(recent)}건 → 신규 {new}건")
    else:
        print("  ⚠ 피드를 읽지 못했습니다")

    # ── ② 과거분 소량 보충 (일일 예산 내에서) ──
    if not allow_crawl or not src_cfg.get("backfill", False):
        print("\n⏭  과거분 보충 생략")
        return total_new

    budget = int(src_cfg.get("daily_crawl_limit", 0))
    if budget <= 0:
        return total_new

    print(f"\n{'=' * 60}\n▶ GeekNews 과거분 보충 (오늘 예산 {budget}건)\n{'=' * 60}")

    ck = Checkpoint(Path(cfg["backfill"]["checkpoint_dir"]) / "geeknews.json")
    task = ""
    state = ck.get(task)
    if state.get("done"):
        print("  ✅ 수집 범위 상한에 도달했습니다 — 더 내려갈 곳 없음")
        return total_new

    cursor = state.get("cursor")
    span_years = float(src_cfg.get("backfill_years", 1))
    until = datetime.now(timezone.utc)
    since = until - timedelta(days=365 * span_years)

    used = 0
    while used < budget:
        batch = src.fetch(since, until, cursor)
        if batch.items:
            _append_raw(raw_dir / "geeknews_backfill.jsonl", batch.items)
            new = upsert_items([i.to_dict() for i in batch.items], engine)
            total_new += new
            print(f"      +{len(batch.items):>3}건 (신규 {new:>3})  {batch.note}")

        ck.update(task, batch.cursor, len(batch.items),
                  done=batch.cursor is None, since=since, resume_cursor=cursor)
        used += BATCH_SIZE

        if batch.cursor is None:
            print("  ✅ 수집 범위 상한 도달 — 과거분 완료")
            break
        if "거부" in batch.note:
            # 서버가 거부했다. 오늘은 여기까지. 커서가 남았으니 내일 이어간다.
            print("  ⛔ 오늘은 여기까지 — 내일 같은 지점에서 재개합니다")
            break
        cursor = batch.cursor
    else:
        print(f"  ⏸ 오늘 예산 {budget}건 소진 — 커서 {cursor}에서 내일 재개")

    return total_new


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="일일 수집 (전방 + 과거분 소량)")
    parser.add_argument("--source", default="all", choices=["all", "geeknews"],
                        help="수집할 소스")
    parser.add_argument("--no-crawl", action="store_true",
                        help="과거분 보충 없이 전방 수집만")
    args = parser.parse_args()

    cfg = load_config()
    run_date = datetime.now().strftime("%Y-%m-%d")
    print(f"일일 수집 시작 — {run_date}")

    total = 0
    if args.source in ("all", "geeknews"):
        total += collect_geeknews(cfg, allow_crawl=not args.no_crawl)

    print(f"\n{'=' * 60}\n■ 오늘 신규 {total:,}건 적재")
    print("  확인: python -m src.db --summary")


if __name__ == "__main__":
    main()
