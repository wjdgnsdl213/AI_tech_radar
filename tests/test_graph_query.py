"""Whitespace/case variants must select the same representative graph."""
import pytest
from sqlalchemy import create_engine

from src.db import metadata, kw_meta, kw_neighbor


@pytest.fixture
def graph_db(monkeypatch):
    from web import api
    engine = create_engine('sqlite://')
    metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(kw_meta.insert(), [
            dict(keyword='모두의AI', df=50, axis='ai'),
            dict(keyword='모두의 AI', df=20, axis='ai'),
            dict(keyword='지원사업', df=80, axis='smallbiz'),
            dict(keyword='다른주제', df=40, axis='ai'),
        ])
        conn.execute(kw_neighbor.insert(), [
            dict(keyword='모두의AI', neighbor='지원사업', npmi=.8, cooc=12, df=80),
            dict(keyword='모두의 AI', neighbor='다른주제', npmi=.7, cooc=10, df=40),
        ])
    monkeypatch.setattr(api, 'get_engine', lambda: engine)
    yield engine
    engine.dispose()


def test_spacing_and_case_select_identical_graph(graph_db):
    from web.api import _ego
    expected = _ego('모두의AI', 1, 12, 0, 46)
    assert expected['center'] == '모두의AI'
    assert len(expected['nodes']) == 2
    for query in ('모두의 AI', ' 모두의  ai ', '모두의\tAI', '모두의\u00a0AI', '모두의\u3000AI'):
        assert _ego(query, 1, 12, 0, 46) == expected


def test_equal_frequency_has_a_stable_representative(graph_db):
    from web.api import _ego
    with graph_db.begin() as conn:
        conn.execute(kw_meta.update().where(kw_meta.c.keyword == '모두의 AI').values(df=50))
    assert _ego('모두의 AI', 1, 12, 0, 46) == _ego('모두의AI', 1, 12, 0, 46)


def test_nonspacing_changes_are_not_merged(graph_db):
    from web.api import _ego
    assert _ego('모두의-AI', 1, 12, 0, 46)['empty'] is True


def test_variants_share_graph_cache(monkeypatch):
    from web import api
    entries = {}
    calls = []
    def cached(key, fn):
        if key not in entries:
            entries[key] = fn()
        return entries[key]
    monkeypatch.setattr(api.ego_cache, 'get_or_call', cached)
    monkeypatch.setattr(api, '_ego', lambda *args: calls.append(args) or {'center':'모두의AI'})
    for query in ('모두의 AI', '모두의AI', ' 모두의 ai '):
        api.ego(query, 1, 12, 0, 46)
    assert len(calls) == 1
