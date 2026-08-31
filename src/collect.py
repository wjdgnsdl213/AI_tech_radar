"""일일 수집 엔진 — 등록된 소스 어댑터를 순회하며 매일 1회 실행한다.

`backfill.py`(1회성 대량)와 역할이 다르다. 여기서는 두 가지를 한다:

  1) 전방 수집 — 새로 올라온 것을 가져온다. RSS/API 한 번 호출이라 부담이 없다.
  2) 과거분 소량 보충 — 하루 예산만큼만 과거로 내려간다(GeekNews 전용).
     체크포인트가 남으므로 며칠에 걸쳐 축적된다.

★ 왜 매일 돌려야 하는가 — 수집은 소급되지 않는다
    네이버 뉴스 API는 쿼리당 최근 1,000건까지만 준다. 오늘 안 받으면 오늘 기사는
    영영 못 받는다. 반면 필터·교차점수·AI 해설은 DB에 원본만 쌓여 있으면 나중에
    전부 소급 적용된다. 그래서 처리 단계가 아직 없어도 수집부터 켜두는 게 맞다.

설계 원칙 — 소스별 실패 격리
    소스가 늘어나면 하나가 죽어서 나머지가 멈추는 게 가장 흔한 사고다.
    각 소스를 try/except로 감싸고, 실패해도 다음 소스를 계속 돌린 뒤 마지막에
    요약으로 보고한다. 종료 코드는 실패가 있으면 1 (스케줄러가 감지할 수 있게).

실행:
  python -m src.collect                       # 등록된 전체 소스
  python -m src.collect --source naver_news
  python -m src.collect --no-crawl            # 전방 수집만 (GeekNews 과거분 생략)
  python -m src.collect --force               # interval_days 무시하고 강제 실행
  python -m src.collect --days 7              # 전방 수집 기간 덮어쓰기
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from dotenv import load_dotenv

from src.backfill import Checkpoint
from src.db import get_engine, init_db, load_config, upsert_items
from src.sources.base import Item, Source
from src.sources.geeknews import GeekNewsSource
from src.sources.hackernews import HackerNewsSource
from src.sources.naver_news import NaverNewsSource

# Windows 콘솔(cp949)에서 특수문자 출력 깨짐 방지
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

GEEKNEWS_BATCH = 50     # geeknews.fetch()가 한 번에 처리하는 id 수
DEFAULT_DAYS = 3        # 전방 수집 기본 기간 — 하루 걸러도 구멍이 안 나게 여유를 둔다
DEFAULT_MAX_PAGES = 20  # 쿼리당 페이지 상한 (폭주 방지)


# ── 공통 유틸 ───────────────────────────────────────────────────────
def _user_agent(cfg: dict[str, Any]) -> str:
    contact = os.getenv("CONTACT_EMAIL", "unknown")
    tpl = cfg.get("backfill", {}).get(
        "user_agent", "ai-tech-radar/0.1 (contact: {contact})")
    return tpl.format(contact=contact)


def _append_raw(path: Path, items: list[Item]) -> None:
    """원본 jsonl에 덧붙인다. DB 적재 전에 먼저 쓴다 — 스키마가 바뀌어도
    ingest_raw로 재적재할 수 있어야 하고, 네트워크 재수집은 되돌릴 수 없다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for it in items:
            f.write(json.dumps(it.to_dict(), ensure_ascii=False) + "\n")


def _stamp_regulatory(items: list[Item], src_cfg: dict[str, Any]) -> None:
    """규제 소스에서 온 항목에 표식을 남긴다 (F7 규제 알림).

    판정은 **소스 기반**이다 — 개인정보위·국회 의안 같은 1차 출처에서 온 항목은
    전부 규제로 본다. 제목 키워드('고시·개정·시행')로 거르면 "규제 샌드박스 선정
    기업" 같은 오탐이 섞인다.

    지금은 meta에 남긴다. 전용 컬럼으로 올리면 기존 radar.db에 ALTER가 필요해지고
    (SQLAlchemy create_all은 이미 있는 테이블에 컬럼을 추가하지 않는다) 마이그레이션
    부담이 생긴다. digest 단계를 만들 때 함께 승격시킨다.
    """
    if not src_cfg.get("regulatory"):
        return
    for it in items:
        it.meta["regulatory"] = True


class RunState:
    """소스별 마지막 실행 시각. interval_days 판정에 쓴다.

    소스마다 갱신 주기가 다르다 — 뉴스는 매일이지만 고시·릴리스는 하루에 몇 건도
    안 올라와서 매일 때리면 rate limit만 축낸다.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.data: dict[str, Any] = {}
        if path.exists():
            try:
                self.data = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                self.data = {}

    def due(self, source: str, interval_days: float) -> tuple[bool, str]:
        """(실행할까, 건너뛰는 사유)"""
        if interval_days <= 0:
            return True, ""
        last = self.data.get(source, {}).get("last_run")
        if not last:
            return True, ""
        try:
            elapsed = datetime.now(timezone.utc) - datetime.fromisoformat(last)
        except ValueError:
            return True, ""
        interval = timedelta(days=interval_days)
        if elapsed < interval:
            left = interval - elapsed
            return False, (f"주기 {interval_days}일 미도래 "
                           f"(남은 {left.days}일 {left.seconds // 3600}시간)")
        return True, ""

    def mark(self, source: str, new_items: int) -> None:
        self.data[source] = {
            "last_run": datetime.now(timezone.utc).isoformat(),
            "last_new": new_items,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")


# ── 소스별 수집기 ───────────────────────────────────────────────────
def collect_adapter(cls: type[Source], cfg_key: str, cfg: dict[str, Any],
                    days: float) -> int:
    """어댑터 인터페이스(tasks/with_task/fetch)만 쓰는 범용 전방 수집기.

    최근 days일 구간을 작업(쿼리)별로 훑는다. 백필과 달리 체크포인트를 남기지 않는다 —
    구간이 짧고 매일 다시 돌기 때문에 재개할 게 없다. 중복은 DB의
    UNIQUE(source, source_id)가 막는다.
    """
    src_cfg = cfg["sources"][cfg_key]
    common = {"user_agent": _user_agent(cfg)}
    base = cls(src_cfg, common)

    engine = get_engine()
    raw_path = Path(cfg["backfill"]["out_dir"]) / f"{cfg_key}_daily.jsonl"
    until = datetime.now(timezone.utc)
    since = until - timedelta(days=days)
    max_pages = int(src_cfg.get("max_pages", DEFAULT_MAX_PAGES))

    print(f"  구간 {since:%Y-%m-%d} ~ {until:%Y-%m-%d}")
    total_new = 0
    for task in base.tasks():
        label = task or "기본"
        src = base.with_task(task)
        cursor: str | None = None
        got = new = 0
        for _ in range(max_pages):
            batch = src.fetch(since, until, cursor)
            if batch.items:
                _stamp_regulatory(batch.items, src_cfg)
                _append_raw(raw_path, batch.items)
                new += upsert_items([i.to_dict() for i in batch.items], engine)
                got += len(batch.items)
            if batch.cursor is None:
                break
            cursor = batch.cursor
        else:
            # 상한에 걸렸다 = 구간을 다 못 훑었다. 쿼리가 너무 넓거나 days가 길다는 신호
            print(f"    ⚠ [{label}] 페이지 상한 {max_pages} 도달 — 구간을 다 못 훑었을 수 있음")
        total_new += new
        print(f"    [{label}] {got:>4}건 → 신규 {new:>4}")
    return total_new


def collect_geeknews(cfg: dict[str, Any], days: float, allow_crawl: bool) -> int:
    """GeekNews 전용 — RSS 전방 수집 + 일일 예산 내 과거분 보충.

    과거분은 개인 운영 사이트를 직접 훑는 일이라 범용 수집기에 넣지 않고 분리해뒀다.
    서버가 403/429로 거부하면 그 실행을 즉시 끝낸다. 재시도·우회하지 않는다.
    커서는 남으므로 다음 날 이어서 받는다. 차단이 잦으면 daily_crawl_limit을 낮춘다.
    """
    src_cfg = cfg["sources"]["geeknews"]
    common = {"user_agent": _user_agent(cfg)}
    src = GeekNewsSource(src_cfg, common)
    engine = get_engine()
    raw_dir = Path(cfg["backfill"]["out_dir"])
    total_new = 0

    # ── ① 전방 수집 (RSS) — 항상 실행. 배포용 공개 채널이라 부담이 없다 ──
    recent = src.fetch_recent()
    if recent:
        _stamp_regulatory(recent, src_cfg)
        _append_raw(raw_dir / "geeknews_rss.jsonl", recent)
        new = upsert_items([i.to_dict() for i in recent], engine)
        total_new += new
        print(f"    RSS 피드 {len(recent)}건 → 신규 {new}건")
    else:
        print("    ⚠ 피드를 읽지 못했습니다")

    # ── ② 과거분 소량 보충 (일일 예산 내에서) ──
    if not allow_crawl or not src_cfg.get("backfill", False):
        print("    ⏭ 과거분 보충 생략")
        return total_new

    budget = int(src_cfg.get("daily_crawl_limit", 0))
    if budget <= 0:
        return total_new

    ck = Checkpoint(Path(cfg["backfill"]["checkpoint_dir"]) / "geeknews.json")
    task = ""
    state = ck.get(task)
    if state.get("done"):
        print("    ✅ 수집 범위 상한 도달 — 더 내려갈 곳 없음")
        return total_new

    print(f"    과거분 보충 (오늘 예산 {budget}건)")
    cursor = state.get("cursor")
    span_years = float(src_cfg.get("backfill_years", 1))
    until = datetime.now(timezone.utc)
    since = until - timedelta(days=365 * span_years)

    used = 0
    while used < budget:
        batch = src.fetch(since, until, cursor)
        if batch.items:
            _stamp_regulatory(batch.items, src_cfg)
            _append_raw(raw_dir / "geeknews_backfill.jsonl", batch.items)
            new = upsert_items([i.to_dict() for i in batch.items], engine)
            total_new += new
            print(f"      +{len(batch.items):>3}건 (신규 {new:>3})  {batch.note}")

        ck.update(task, batch.cursor, len(batch.items),
                  done=batch.cursor is None, since=since, resume_cursor=cursor)
        used += GEEKNEWS_BATCH

        if batch.cursor is None:
            print("    ✅ 수집 범위 상한 도달 — 과거분 완료")
            break
        if "거부" in batch.note:
            # 서버가 거부했다. 오늘은 여기까지. 커서가 남았으니 내일 이어간다.
            print("    ⛔ 오늘은 여기까지 — 내일 같은 지점에서 재개합니다")
            break
        cursor = batch.cursor
    else:
        print(f"    ⏸ 오늘 예산 {budget}건 소진 — 커서 {cursor}에서 내일 재개")

    return total_new


# ── 등록부 ──────────────────────────────────────────────────────────
# 여기에 한 줄 추가하면 매일 수집 대상이 된다. 순서 = 실행 순서.
# 키가 필요 없는 소스를 앞에 둬서 .env가 없어도 뭔가는 수집되게 한다.
Collector = Callable[[dict[str, Any], float, bool], int]

REGISTRY: dict[str, Collector] = {
    "geeknews":   lambda cfg, days, crawl: collect_geeknews(cfg, days, crawl),
    "hackernews": lambda cfg, days, crawl: collect_adapter(
        HackerNewsSource, "hackernews", cfg, days),
    "naver_news": lambda cfg, days, crawl: collect_adapter(
        NaverNewsSource, "naver_news", cfg, days),
}


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="일일 수집 엔진 (소스 순회 + 실패 격리)")
    parser.add_argument("--source", default="all",
                        choices=["all", *REGISTRY.keys()], help="수집할 소스")
    parser.add_argument("--no-crawl", action="store_true",
                        help="과거분 보충 없이 전방 수집만")
    parser.add_argument("--force", action="store_true",
                        help="interval_days를 무시하고 강제 실행")
    parser.add_argument("--days", type=float, default=None,
                        help="전방 수집 기간(일). 기본은 config의 collect.days")
    args = parser.parse_args()

    cfg = load_config()
    ccfg = cfg.get("collect", {})
    default_days = float(args.days if args.days is not None
                         else ccfg.get("days", DEFAULT_DAYS))

    engine = get_engine()
    init_db(engine)   # 스키마 보장(멱등)
    state = RunState(Path(cfg["backfill"]["checkpoint_dir"]) / "collect_state.json")

    targets = list(REGISTRY.keys()) if args.source == "all" else [args.source]
    print(f"일일 수집 시작 — {datetime.now():%Y-%m-%d %H:%M}  (대상 {len(targets)}개)")

    # (소스, 상태, 신규건수, 소요초)
    log: list[tuple[str, str, int, float]] = []
    for key in targets:
        src_cfg = cfg.get("sources", {}).get(key, {})
        print(f"\n{'=' * 60}\n▶ {key}\n{'=' * 60}")

        if not src_cfg.get("enabled", True):
            print("  ⏭ config에서 비활성화됨")
            log.append((key, "skip", 0, 0.0))
            continue

        if not args.force:
            ok, reason = state.due(key, float(src_cfg.get("interval_days", 1)))
            if not ok:
                print(f"  ⏭ {reason}")
                log.append((key, "skip", 0, 0.0))
                continue

        days = float(src_cfg.get("days", default_days))
        t0 = time.time()
        try:
            new = REGISTRY[key](cfg, days, not args.no_crawl)
            state.mark(key, new)
            log.append((key, "ok", new, time.time() - t0))
        except KeyboardInterrupt:
            print("\n⚠ 사용자 중단")
            log.append((key, "fail", 0, time.time() - t0))
            break
        except Exception as exc:
            # ★ 실패 격리 — 이 소스만 접고 다음 소스를 계속 돌린다
            print(f"  ❌ 실패: {type(exc).__name__}: {exc}")
            log.append((key, "fail", 0, time.time() - t0))

    # ── 요약 ──
    total = sum(n for _, st, n, _ in log if st == "ok")
    mark = {"ok": "✅", "fail": "❌", "skip": "⏭"}
    print(f"\n\n{'=' * 60}\n■ 수집 요약\n{'=' * 60}")
    for key, st, n, sec in log:
        print(f"  {mark[st]}  {key:<14}{n:>7,}건  {sec:>6.0f}초")
    print(f"\n  오늘 신규 {total:,}건 적재")
    print("  확인: python -m src.db --summary")

    failed = [k for k, st, _, _ in log if st == "fail"]
    if failed:
        print(f"\n⚠ 실패 소스 {len(failed)}개: {', '.join(failed)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
