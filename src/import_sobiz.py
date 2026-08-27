"""sobiz 프로젝트 DB에서 소상공인 축 baseline을 가져온다.

네이버 뉴스 API는 최근분만 주기 때문에(sobiz의 고질적 한계) 소상공인 축은
백필 경로가 없다. 그런데 sobiz가 주간 배치로 `news_articles`를 누적해뒀고,
그건 precision 92% 필터를 통과시킨 데이터라 품질도 검증돼 있다. 그대로 가져온다.

sobiz 스키마 → 이 프로젝트 스키마 매핑:
    link        → url  (+ source_id: sobiz는 link가 PK라 그대로 식별자로 쓴다)
    title       → title
    description → summary
    pub_date    → published_at   ★ first_seen(수집일)이 아니라 pub_date(발행일)를 쓴다
    query/press/source/category → meta

실행:
  python -m src.import_sobiz
  python -m src.import_sobiz --since 2025-06-01 --dry-run
"""

from __future__ import annotations

import argparse
import hashlib
import sqlite3
import sys
from pathlib import Path

from src.db import get_engine, init_db, load_config, upsert_items
from src.sources.base import clean_text, now_iso

# Windows 콘솔(cp949)에서 특수문자 출력 깨짐 방지
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

SOURCE_NAME = "sobiz_news"


def _source_id(link: str) -> str:
    """link가 길고 쿼리스트링이 붙어 있어 해시로 안정적인 짧은 ID를 만든다."""
    return hashlib.sha1(link.encode("utf-8")).hexdigest()[:16]


def main() -> None:
    parser = argparse.ArgumentParser(description="sobiz trends.db → 소상공인 축 baseline 임포트")
    parser.add_argument("--db", default=None, help="sobiz trends.db 경로 (기본: config)")
    parser.add_argument("--since", default=None, help="이 발행일 이후만 (YYYY-MM-DD)")
    parser.add_argument("--dry-run", action="store_true", help="적재 없이 건수만 확인")
    args = parser.parse_args()

    cfg = load_config()
    imp = cfg.get("import_sobiz", {})
    src_path = Path(args.db or imp.get("db_path", "../news_keyword/data/trends.db"))
    since = args.since or imp.get("since", "2025-01-01")
    table = imp.get("table", "news_articles")

    if not src_path.exists():
        sys.exit(
            f"sobiz DB를 찾을 수 없습니다: {src_path.resolve()}\n"
            "config.yaml의 import_sobiz.db_path를 확인하거나 --db로 지정하세요."
        )

    print(f"소스 DB : {src_path.resolve()}")
    print(f"대상 표 : {table}  (발행일 {since} 이후)")

    src = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True)  # 원본은 읽기 전용으로 연다
    src.row_factory = sqlite3.Row
    rows = src.execute(
        f"SELECT link, query, title, description, pub_date, press, source, category "
        f"FROM {table} WHERE pub_date >= ? ORDER BY pub_date",
        (since,),
    ).fetchall()
    src.close()

    print(f"조회 결과: {len(rows):,}건")
    if args.dry_run:
        if rows:
            print(f"  발행일 범위: {rows[0]['pub_date'][:10]} ~ {rows[-1]['pub_date'][:10]}")
        print("(--dry-run이라 적재하지 않음)")
        return
    if not rows:
        return

    collected = now_iso()
    items = [
        {
            "source": SOURCE_NAME,
            "source_id": _source_id(r["link"]),
            "title": clean_text(r["title"]),
            "summary": clean_text(r["description"]),
            "url": r["link"],
            "published_at": r["pub_date"],      # ★ 발행일. first_seen(수집일)이 아니다
            "collected_at": collected,
            "meta": {
                "query": r["query"],
                "press": r["press"],
                "origin": r["source"],          # sobiz 내 출처: naver | scrap
                "category": r["category"],
                "via": "import_sobiz",
            },
        }
        for r in rows
    ]

    engine = get_engine()
    init_db(engine)
    new = upsert_items(items, engine)

    print(f"✅ 신규 {new:,}건 적재 (중복 {len(items) - new:,}건은 기존 유지)")
    print(f"   발행일 범위: {rows[0]['pub_date'][:10]} ~ {rows[-1]['pub_date'][:10]}")
    print("   다음: python -m src.db --summary")


if __name__ == "__main__":
    main()
