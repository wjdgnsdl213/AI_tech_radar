"""Read-only timings for the actual explore queries; never prints DB credentials."""
from time import perf_counter

from sqlalchemy import event

from src.db import get_engine
from web.api import keyword, search, regulatory


def main() -> None:
    engine = get_engine()
    timings: list[float] = []

    @event.listens_for(engine, 'before_cursor_execute')
    def begin(conn, cursor, statement, parameters, context, many):
        context.profile_started = perf_counter()

    @event.listens_for(engine, 'after_cursor_execute')
    def end(conn, cursor, statement, parameters, context, many):
        timings.append(perf_counter() - context.profile_started)

    args = dict(axis='', since='2026-09-01', until='2026-09-07',
                kept_only=1, page=1, size=25, order='relevance')
    for q in ('AI', 'SKT', '소상공인'):
        timings.clear()
        start = perf_counter()
        result = search(q=q, **args)
        elapsed = perf_counter() - start
        queries = list(timings)
        start = perf_counter()
        search(q=q, **args)
        cached = perf_counter() - start
        indexed = keyword(q, limit=25, axis='', since=args['since'], until=args['until'])
        print(dict(q=q, seconds=round(elapsed, 3), cached_seconds=round(cached, 5),
                   sql_seconds=[round(t, 3) for t in queries],
                   search_total=result['total'], indexed_total=indexed['total']))
    start = perf_counter()
    regulatory(q='AI', limit=3, days=0)
    print('regulatory_AI_seconds', round(perf_counter() - start, 3))


if __name__ == '__main__':
    main()
