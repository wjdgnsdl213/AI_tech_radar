"""Every low-frequency rising topic must retain its article links."""
from datetime import datetime

import pytest
from sqlalchemy import create_engine

from src.db import items, kw_item, metadata
from src.extract import item_keywords


@pytest.mark.parametrize("weekly_min", [3, 5])
def test_new_rising_topic_remains_searchable_after_index_build(monkeypatch, weekly_min):
    from src import index_build
    from web import api

    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    item_keywords.create(engine)
    keyword = "신규AI주제"
    ids = list(range(1, weekly_min + 1))
    with engine.begin() as conn:
        conn.execute(items.insert(), [dict(
            id=i, source="test", source_id=str(i), title="신규 AI 주제 발표",
            summary="관련 보도", kept=True, published_at=datetime(2026, 9, 28),
            collected_at=datetime(2026, 9, 29),
        ) for i in ids])
        conn.execute(item_keywords.insert(), [dict(
            item_id=i, keyword=keyword, week="2026-W40", kept=True,
        ) for i in ids])
    monkeypatch.setattr(index_build, "kw_engine", lambda: engine)
    monkeypatch.setattr(index_build, "load_config", lambda: {"trend": {"min_weekly_freq": weekly_min}})
    monkeypatch.setattr(api, "get_engine", lambda: engine)
    links = index_build.build_items({keyword: weekly_min})
    if links:
        with engine.begin() as conn:
            conn.execute(kw_item.insert(), links)
    result = api.search.__wrapped__(q=keyword, axis="", since="", until="", kept_only=1,
                        page=1, size=50, order="oldest")
    assert result["total"] == weekly_min
    assert [row["id"] for row in result["items"]] == ids
    engine.dispose()
