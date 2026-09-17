"""Explore views must share KST calendar dates and stable pagination."""
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, select
from src.db import metadata, items, kw_item


@pytest.fixture
def db(monkeypatch):
    from web import api
    engine = create_engine('sqlite://')
    metadata.create_all(engine)
    dates = ['2026-08-31T14:59:59', '2026-08-31T15:00:00',
             '2026-09-01T14:59:59.999999', '2026-09-01T15:00:00']
    with engine.begin() as conn:
        conn.execute(items.insert(), [
            dict(id=i, source='test', source_id=str(i), title='AI 상권', summary='데이터',
                 url=f'https://example.com/{i}', published_at=datetime.fromisoformat(date).replace(tzinfo=timezone.utc),
                 collected_at=datetime(2026, 9, 2), kept=True, cross_score=float(i))
            for i, date in enumerate(dates, 1)])
        conn.execute(kw_item.insert(), [dict(item_id=i, keyword='AI') for i in range(1, 5)])
    monkeypatch.setattr(api, 'get_engine', lambda: engine)
    yield engine
    engine.dispose()


def test_search_and_export_use_full_kst_day(db):
    from web.api import search_conds
    from web.server import _search_conds
    with db.connect() as conn:
        for condition in (search_conds, _search_conds):
            rows = conn.execute(select(items.c.id).where(condition('AI', '', '2026-09-01', '2026-09-01', 1))).scalars().all()
            assert rows == [2, 3]


def test_rising_compound_matches_index_with_shared_filters(db):
    from web.api import search_conds
    from web.server import _search_conds
    with db.begin() as conn:
        conn.execute(kw_item.insert(), [dict(item_id=i, keyword='전문가AI') for i in (1, 2, 4)])
        conn.execute(items.update().where(items.c.id == 2).values(title='전문가 AI 공개'))
    with db.connect() as conn:
        for condition in (search_conds, _search_conds):
            result = conn.execute(select(items.c.id).where(condition('전문가AI', '', '2026-09-01', '2026-09-01', 1))).scalars().all()
            assert result == [2]


def test_timeline_and_relevance_share_results_but_not_order(db):
    from web.api import search
    args = dict(q='AI', axis='', since='2026-09-01', until='2026-09-01', kept_only=1, page=1, size=50)
    chronological = search(**args, order='oldest')
    relevance = search(**args, order='relevance')
    assert [r['id'] for r in chronological['items']] == [2, 3]
    assert [r['id'] for r in relevance['items']] == [3, 2]
    assert chronological['total'] == relevance['total'] == 2
    assert [r['published'] for r in chronological['items']] == ['2026-09-01', '2026-09-01']


def test_chronological_orders_put_unknown_dates_last(db):
    from web.api import search
    with db.begin() as conn:
        conn.execute(items.insert(), dict(
            id=10, source='test', source_id='10', title='AI undated', summary='',
            url='https://example.com/10', published_at=None,
            collected_at=datetime(2026, 9, 2), kept=True, cross_score=10.0))

    args = dict(q='AI', axis='', since='', until='', kept_only=1, page=1, size=50)
    assert [r['id'] for r in search(**args, order='oldest')['items']] == [1, 2, 3, 4, 10]
    assert [r['id'] for r in search(**args, order='newest')['items']] == [4, 3, 2, 1, 10]


def test_equal_sort_keys_page_stably(db):
    from web.api import search
    tied_at = datetime(2026, 9, 1, tzinfo=timezone.utc)
    with db.begin() as conn:
        conn.execute(items.insert(), [
            dict(id=i, source='test', source_id=str(i), title='tied', summary='',
                 url=f'https://example.com/{i}', published_at=tied_at,
                 collected_at=datetime(2026, 9, 2), kept=True, cross_score=5.0)
            for i in (20, 21, 22)
        ])

    args = dict(q='tied', axis='', since='', until='', kept_only=1, size=1)
    assert [search(**args, page=p, order='oldest')['items'][0]['id'] for p in (1, 2, 3)] == [20, 21, 22]
    assert [search(**args, page=p, order='newest')['items'][0]['id'] for p in (1, 2, 3)] == [22, 21, 20]
    assert [search(**args, page=p, order='relevance')['items'][0]['id'] for p in (1, 2, 3)] == [22, 21, 20]


def test_keyword_articles_honor_selected_period_and_total(db):
    from web.api import keyword
    result = keyword('AI', limit=1, since='2026-09-01', until='2026-09-01')
    assert result['total'] == 2
    assert [r['id'] for r in result['items']] == [3]
    assert result['items'][0]['published'] == '2026-09-01'


def test_keyword_articles_keep_the_shared_axis_filter(db):
    from src.db import item_axes
    from web.api import keyword
    with db.begin() as conn:
        conn.execute(item_axes.insert(), dict(item_id=2, axis='ai'))
    result = keyword('AI', limit=40, since='2026-09-01', until='2026-09-01', axis='ai')
    assert result['total'] == 1
    assert [r['id'] for r in result['items']] == [2]


def test_keyword_normalizes_null_kept_and_score_before_ordering(db):
    from web.api import keyword
    with db.begin() as conn:
        conn.execute(items.insert(), [
            dict(id=30, source='test', source_id='30', title='kept null score', summary='',
                 url='https://example.com/30', published_at=datetime(2026, 9, 1),
                 collected_at=datetime(2026, 9, 2), kept=True, cross_score=None),
            dict(id=31, source='test', source_id='31', title='kept negative score', summary='',
                 url='https://example.com/31', published_at=datetime(2026, 9, 1),
                 collected_at=datetime(2026, 9, 2), kept=True, cross_score=-1.0),
            dict(id=32, source='test', source_id='32', title='not kept', summary='',
                 url='https://example.com/32', published_at=datetime(2026, 9, 1),
                 collected_at=datetime(2026, 9, 2), kept=False, cross_score=100.0),
            dict(id=33, source='test', source_id='33', title='unknown kept', summary='',
                 url='https://example.com/33', published_at=datetime(2026, 9, 1),
                 collected_at=datetime(2026, 9, 2), kept=None, cross_score=200.0),
        ])
        conn.execute(kw_item.insert(), [
            dict(item_id=i, keyword='nullable') for i in (30, 31, 32, 33)
        ])

    result = keyword('nullable', limit=20)
    assert [r['id'] for r in result['items']] == [30, 31, 33, 32]
    assert result['kept'] == 2


def test_literal_search_does_not_treat_percent_as_wildcard(db):
    from web.api import search_conds
    with db.connect() as conn:
        assert conn.execute(select(items.c.id).where(search_conds('%', '', '', '', 1))).all() == []


def test_csv_displays_the_same_kst_dates_as_search(db, monkeypatch):
    import asyncio
    import csv
    import io
    from web import server
    monkeypatch.setattr(server, 'get_engine', lambda: db)
    response = server.search_csv(q='AI', axis='', since='2026-09-01', until='2026-09-01', kept_only=1, limit=2000)
    async def collect_body():
        return ''.join([chunk async for chunk in response.body_iterator])
    rows = list(csv.reader(io.StringIO(asyncio.run(collect_body()).lstrip('\ufeff'))))
    assert len(rows) == 3
    assert [r[0] for r in rows[1:]] == ['2026-09-01', '2026-09-01']


def test_search_count_is_reused_across_pages_and_orders_but_not_filters(db):
    from sqlalchemy import event
    from web.api import search
    counts = []
    @event.listens_for(db, 'before_cursor_execute')
    def record(conn, cursor, statement, parameters, context, many):
        if statement.startswith('SELECT count('):
            counts.append(statement)
    args = dict(q='상권', axis='', since='', until='', kept_only=1, size=1)
    assert search(**args, page=1, order='oldest')['total'] == 4
    assert search(**args, page=2, order='oldest')['total'] == 4
    assert search(**args, page=1, order='newest')['total'] == 4
    assert len(counts) == 1
    assert search(**{**args, 'since':'2026-09-01'}, page=1, order='oldest')['total'] == 3
    assert len(counts) == 2


def test_search_union_deduplicates_all_match_sources_and_keeps_unselected_articles(db):
    from web.api import search
    with db.begin() as conn:
        conn.execute(items.update().where(items.c.id == 1).values(title='match-term', summary='match-term'))
        conn.execute(items.update().where(items.c.id == 2).values(title='other', summary='match-term'))
        conn.execute(items.update().where(items.c.id == 3).values(title='other', summary='', kept=False))
        conn.execute(kw_item.insert(), [dict(item_id=i, keyword='match-term') for i in (1, 3)])
    args = dict(q='match-term', axis='', since='', until='', page=1, size=25, order='oldest')
    selected = search(**args, kept_only=1)
    all_rows = search(**args, kept_only=0)
    assert [r['id'] for r in selected['items']] == [1, 2]
    assert selected['total'] == 2
    assert [r['id'] for r in all_rows['items']] == [1, 2, 3]
    assert all_rows['total'] == 3
