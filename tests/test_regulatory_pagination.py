"""All regulatory posts remain reachable, independently of relevance filtering."""
from datetime import datetime, timezone
import inspect

from sqlalchemy import create_engine
from src.db import metadata, items
from web import api


def test_regulatory_pages_are_disjoint_and_keep_unranked_summaries(monkeypatch):
    engine = create_engine('sqlite://')
    metadata.create_all(engine)
    monkeypatch.setattr(api, 'get_engine', lambda: engine)
    monkeypatch.setattr(api, 'CFG', {'sources': {'law': {'regulatory': True}}})
    with engine.begin() as conn:
        conn.execute(items.insert(), [dict(id=i, source='law', source_id=str(i),
            title=f'법령 {i}', summary='제개정 내용', url=f'https://example.org/{i}',
            published_at=datetime(2026, 9, i, tzinfo=timezone.utc),
            collected_at=datetime(2026, 9, 13, tzinfo=timezone.utc), kept=False,
            insight='· AI 요약\n▸ 확인 필요: 데이터 — 보관 기간 확인') for i in range(1, 6)])
    first = api._regulatory(2, 0, '')
    kwargs = {'offset': 2} if 'offset' in inspect.signature(api._regulatory).parameters else {}
    second = api._regulatory(2, 0, '', **kwargs)
    assert [row['id'] for row in first['items']] == [5, 4]
    assert [row['id'] for row in second['items']] == [3, 2]
    assert second['total'] == 5
    assert second['items'][0]['insight'] == '· AI 요약'
    assert second['items'][0]['checklist'] == [{'label': '데이터', 'detail': '보관 기간 확인'}]
    engine.dispose()
