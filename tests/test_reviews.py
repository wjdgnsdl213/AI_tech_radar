"""Calendar boundaries and manual review contracts, using an isolated real DB."""
from datetime import datetime, timezone
import importlib.util

import pytest
from sqlalchemy import create_engine
from src.db import metadata, items, digests


def reviews():
    assert importlib.util.find_spec("src.reviews"), "API-free review module is missing"
    from src import reviews as module
    return module


@pytest.fixture
def engine():
    e = create_engine("sqlite://")
    metadata.create_all(e)
    with e.begin() as c:
        c.execute(items.insert(), [
            {"id": i, "source": "test", "source_id": str(i), "title": title,
             "summary": "상권 데이터 활용", "url": "https://example.com/" + str(i),
             "published_at": datetime.fromisoformat(date).replace(tzinfo=timezone.utc),
             "collected_at": datetime(2026, 9, 11, tzinfo=timezone.utc), "kept": True,
             "cross_score": 10.0}
            for i, title, date in [
                (1, "AI 상권", "2026-08-31T14:59:59"),
                (2, "AI 상권", "2026-08-31T15:00:00"),
                (3, "데이터 플랫폼", "2026-09-02T03:00:00"),
                (4, "미래 기사", "2026-09-30T16:00:00"),
                (5, "AI 상권", "2026-08-02T03:00:00"),
                (6, "비교 범위 밖", "2026-08-20T03:00:00"),
            ]])
    yield e
    e.dispose()


NOW = datetime(2026, 9, 11, 3, tzinfo=timezone.utc)


def test_month_boundaries_and_equal_elapsed_comparison(engine, monkeypatch):
    m = reviews()
    monkeypatch.setattr(m, "read_editorial", lambda period: None)
    d = m.review_data(engine, "monthly", "2026-09", NOW)
    assert d["kept"] == 2  # KST Sep 1 starts Aug 31 15:00 UTC
    assert d["previous"]["kept"] == 1  # August 20 is outside equal elapsed days
    assert d["status"] == "in_progress"
    assert d["editorial"] is None
    assert {a["id"] for a in d["articles"]} == {2, 3}


def test_current_month_without_articles_is_still_visible(engine, monkeypatch):
    m = reviews()
    monkeypatch.setattr(m, "read_editorial", lambda period: None)
    d = m.review_data(engine, "monthly", "2026-11", datetime(2026, 11, 2, tzinfo=timezone.utc))
    assert d["period"] == "2026-11"
    assert d["kept"] == 0
    assert d["status"] == "in_progress"


def test_iso_year_boundary():
    w = reviews().period_window("weekly", "2026-W01", NOW)
    assert w["start"].isoformat() == "2025-12-29T00:00:00+09:00"
    assert w["end"].isoformat() == "2026-01-05T00:00:00+09:00"


@pytest.mark.parametrize("kind,period", [("weekly", "2026-W54"), ("monthly", "../secret"),
                                         ("monthly", "2026-13"), ("weekly", "2026-09")])
def test_bad_period_rejected(kind, period):
    with pytest.raises(ValueError):
        reviews().period_window(kind, period, NOW)


def test_manual_review_wins_over_legacy_and_keeps_cutoff(engine, monkeypatch):
    m = reviews()
    with engine.begin() as c:
        c.execute(digests.insert().values(week="2026-09", lead="older", body={}, generated_at=NOW))
    editorial = {"period": "2026-09", "title": "직접 리뷰", "summary": "직접 작성", "as_of": "2026-09-10",
                 "generated_at": "2026-09-11", "final": False, "sections": [], "tasks": [], "sources": []}
    monkeypatch.setattr(m, "read_editorial", lambda period: editorial if period == "2026-09" else None)
    d = m.review_data(engine, "monthly", "2026-09", NOW)
    assert d["editorial"]["summary"] == "직접 작성"
    assert d["editorial"]["as_of"] == "2026-09-10"


def test_ended_period_is_not_final_without_editorial_signoff(engine, monkeypatch):
    m = reviews()
    monkeypatch.setattr(m, "read_editorial", lambda period: None)
    assert m.review_data(engine, "monthly", "2026-08", NOW)["status"] == "closed"


def test_issue_timeline_is_chronological_and_does_not_include_future(engine):
    d = reviews().issue_data(engine, "AI", 60, NOW)
    assert [a["id"] for a in d["articles"]] == [5, 1, 2]
    assert d["total"] == 3


def test_review_files_reject_unsafe_links():
    with pytest.raises(ValueError):
        reviews().validate_editorial({"period": "2026-09", "summary": "test", "as_of": "2026-09-11",
                                      "sources": [{"id": 1, "title": "test", "url": "javascript:alert(1)"}]})


def test_manual_mode_never_reaches_database_or_api(monkeypatch):
    from src import insight
    from argparse import Namespace
    def forbidden(*args, **kwargs):
        pytest.fail("manual review generation must not access DB or model")
    monkeypatch.setattr(insight, "get_engine", forbidden)
    cfg = {"insight": {"review_mode": "manual"}}
    args = Namespace(month="2026-09", week="2026-W37", dry_run=False)
    assert insight.run_l2(cfg, args) is None
    assert insight.run_l3(cfg, args) is None
    assert insight.run_week_tasks(cfg, args.week) == []


def test_l1_preserves_directly_authored_insights(engine, monkeypatch):
    """정기 Claude 실행은 사람이 직접 작성한 요약을 대상에서 제외한다."""
    from src import insight
    from argparse import Namespace
    from sqlalchemy import select

    with engine.begin() as c:
        c.execute(items.update().where(items.c.id == 2).values(
            insight="· 직접 작성한 요약", insight_model="codex-manual"))
    monkeypatch.setattr(insight, "get_engine", lambda: engine)
    cfg = {"insight": {"l1_model": "claude-haiku", "l1_max_items": 10,
                       "l1_max_tokens": 100, "fail_open": True},
           "sources": {}, "axes": {}}
    args = Namespace(limit=None, reg=False, regenerate=False, dry_run=False)
    # 다른 항목도 자동 해설 대상으로 잡히지 않도록 모두 수동 표식 처리한다.
    with engine.begin() as c:
        c.execute(items.update().where(items.c.id != 2).values(
            insight="· 기존 요약", insight_model="codex-manual"))
    assert insight.run_l1(cfg, args) == 0
    with engine.connect() as c:
        assert c.execute(select(items.c.insight).where(items.c.id == 2)).scalar_one() == "· 직접 작성한 요약"


def test_month_report_uses_calendar_window_and_real_comparison(engine, monkeypatch):
    from src import report
    monkeypatch.setattr(report, "get_engine", lambda: engine)
    monkeypatch.setattr(reviews(), "read_editorial", lambda period: None)
    d = report.collect("2026-09", {"axes": {"ai": {"label": "AI"}}}, now=NOW)
    assert d["kept"] == 2
    assert d["comparison"]["previous"]["kept"] == 1
    assert "〔예시〕" not in report.render_md(d)
    assert "3·4절은 예시" not in report.render_md(d)
    assert "3·4절은 예시" not in report.render_html(d)


def test_digest_resave_preserves_task_notes(engine, monkeypatch):
    from src import digest
    monkeypatch.setattr(digest, "get_engine", lambda: engine)
    from sqlalchemy import select
    with engine.begin() as c:
        c.execute(digests.insert().values(week="2026-W37", lead="keep", body={"tasks": [{"title": "keep task"}]}, generated_at=NOW))
    digest.save({"week": "2026-W37", "crossing": [], "by_axis": {}, "regulatory": [], "total_kept": 2})
    with engine.connect() as c:
        row = c.execute(select(digests).where(digests.c.week == "2026-W37")).mappings().one()
    assert row["body"]["tasks"] == [{"title": "keep task"}]
    assert row["lead"] == "keep"


def test_authored_report_preserves_structure(engine, monkeypatch):
    from src import report
    monkeypatch.setattr(report, 'get_engine', lambda: engine)
    data = report.collect('2026-09', {'axes': {'ai': {'label': 'AI'}}}, now=NOW)
    rendered = report.render_html(data)
    assert '<strong>정책·서비스</strong>' in rendered
    assert '<h3>지속되는 흐름 1.' in rendered
    assert '<ol class="brief-sources">' in rendered
    assert '**정책·서비스**' not in rendered
    assert '### 지속되는 흐름 1.' in report.render_md(data)


def test_current_authored_reviews_use_bullets():
    for period in ('2026-W36', '2026-W37', '2026-09'):
        data = reviews().read_editorial(period)
        for text in [data['summary'], *[s['body'] for s in data['sections']]]:
            assert all(line.startswith('- **') for line in text.splitlines())
            assert '중간 리뷰' not in text


def test_review_aggregates_kst_days_and_axes_without_fetching_every_timestamp(engine, monkeypatch):
    from sqlalchemy import event
    from src.db import item_axes
    m = reviews()
    monkeypatch.setattr(m, 'read_editorial', lambda period: {'summary':'authored', 'sections':[]})
    with engine.begin() as c:
        c.execute(item_axes.insert(), [dict(item_id=2, axis='ai'), dict(item_id=3, axis='ai'),
                                     dict(item_id=3, axis='bigdata'), dict(item_id=5, axis='smallbiz')])
    statements = []
    @event.listens_for(engine, 'before_cursor_execute')
    def record(conn, cursor, statement, parameters, context, many):
        statements.append(statement)
    data = m.review_data(engine, 'monthly', '2026-09', NOW)
    assert data['series'] == [{'date':'2026-09-01', 'count':1}, {'date':'2026-09-02', 'count':1}]
    assert data['kept'] == 2 and data['previous']['kept'] == 1
    assert data['data_as_of'] == '2026-09-02T12:00+09:00'
    axes = {a['axis']:a for a in data['axes']}
    assert axes['ai']['n'] == 2 and axes['ai']['share'] == 100
    assert axes['bigdata']['n'] == 1 and axes['bigdata']['share'] == 50
    assert axes['smallbiz']['previous'] == 1
    assert len(statements) <= 4
