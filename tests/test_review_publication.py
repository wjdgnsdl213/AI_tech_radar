"""Publication cutoff applies to current and legacy briefing routes."""
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine

from src.db import digests, items, metadata
from src import reviews
from web import api


@pytest.fixture
def publication_db(monkeypatch):
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    monkeypatch.setattr(api, "get_engine", lambda: engine)
    monkeypatch.setattr(reviews, "LATEST_REVIEW_WEEK", "2026-W39")
    monkeypatch.setitem(api.CFG["web"], "latest_review_week", "2026-W39")
    with engine.begin() as conn:
        for i, week in enumerate(("2026-W38", "2026-W39", "2026-W40", "2026-W41"), 1):
            conn.execute(items.insert().values(source="test", source_id=str(i),
                published_week=week, kept=True, collected_at=datetime.now(timezone.utc)))
            conn.execute(digests.insert().values(week=week, generated_at=datetime.now(timezone.utc),
                body={"tasks": [{"title": week}]}))
    yield engine
    engine.dispose()


def test_legacy_week_lists_stop_at_publication_cutoff(publication_db):
    assert [w["week"] for w in api.weeks(limit=1)["weeks"]] == ["2026-W39"]
    weeks = [w["week"] for month in api.months()["months"] for w in month["weeks"]]
    assert weeks == ["2026-W39", "2026-W38"]


def test_current_review_and_saved_tasks_hide_later_weeks(publication_db):
    now = datetime(2026, 10, 7, tzinfo=timezone.utc)
    for period in ("", "2026-W40", "2026-W41"):
        result = reviews.review_data(publication_db, "weekly", period, now)
        assert result["period"] == "2026-W39"
        assert result["end"] == "2026-09-27"
    periods = reviews.available_periods(publication_db, "weekly", now)
    assert periods[0]["period"] == "2026-W39"
    assert all(p["period"] <= "2026-W39" for p in periods)
    assert all(reviews.visible_review_period(t["period"]) for t in reviews.task_candidates(publication_db))
