"""Read-only search query timings and PostgreSQL execution plans."""
from time import perf_counter
from sqlalchemy import event, select, func
from src.db import get_engine, items
from web.api import search, search_conds

engine = get_engine()

@event.listens_for(engine, 'before_cursor_execute')
def start(conn, cursor, statement, parameters, context, executemany):
    context.started = perf_counter()

@event.listens_for(engine, 'after_cursor_execute')
def stop(conn, cursor, statement, parameters, context, executemany):
    print(f'{perf_counter()-context.started:.3f}s {statement[:100]}')

begin = perf_counter()
result = search(q='SKT', axis='', since='', until='', kept_only=1, page=1, size=25, order='newest')
print(f"Search total: {perf_counter()-begin:.3f}s; {result['total']} matches")
if engine.dialect.name == 'postgresql':
    where = search_conds('SKT', '', '', '', 1)
    with engine.connect() as conn:
        for stmt in (select(func.count()).select_from(items).where(where),
                     select(items.c.id).where(where).order_by(items.c.published_at.desc().nullslast(), items.c.id.desc()).limit(25)):
            sql = str(stmt.compile(dialect=engine.dialect, compile_kwargs={'literal_binds':True}))
            for row in conn.exec_driver_sql('EXPLAIN ' + sql):
                print(row[0])
