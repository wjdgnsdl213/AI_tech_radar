"""직접 리뷰를 작성할 때 읽을 근거를 출력한다. 모델 호출·DB 쓰기 없음."""
from __future__ import annotations

import json
import argparse
from datetime import datetime, timedelta, timezone
from sqlalchemy import func, select
from src.db import digests, get_engine, items
from src.reviews import KST


def main() -> None:
    parser = argparse.ArgumentParser()
    today = datetime.now(KST).date()
    parser.add_argument("--start", default=str(today - timedelta(days=today.weekday())))
    parser.add_argument("--end", default=str(today + timedelta(days=1)))
    parser.add_argument("--limit", type=int, default=18)
    args = parser.parse_args()
    with get_engine().connect() as conn:
        for start, end in ((args.start, args.end),):
            where = (items.c.kept.is_(True),
                     items.c.published_at >= datetime.fromisoformat(start).replace(tzinfo=KST).astimezone(timezone.utc),
                     items.c.published_at < min(datetime.now(timezone.utc), datetime.fromisoformat(end).replace(tzinfo=KST).astimezone(timezone.utc)))
            rows = conn.execute(select(items.c.id, items.c.title, items.c.summary,
                                       items.c.url, items.c.source, items.c.published_at)
                                .where(*where).order_by(items.c.cross_score.desc(),
                                                       items.c.published_at.desc()).limit(args.limit)).mappings().all()
            print(json.dumps({"start": start, "end_exclusive": end,
                              "total": conn.execute(select(func.count()).select_from(items).where(*where)).scalar_one(),
                              "articles": [dict(r) for r in rows]}, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
