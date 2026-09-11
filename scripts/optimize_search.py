"""Add PostgreSQL substring search indexes without blocking normal writes."""
from src.db import get_engine


def main():
    engine = get_engine()
    if engine.dialect.name != 'postgresql':
        raise SystemExit('This migration requires PostgreSQL.')
    with engine.connect().execution_options(isolation_level='AUTOCOMMIT') as conn:
        conn.exec_driver_sql('CREATE EXTENSION IF NOT EXISTS pg_trgm')
        for column in ('title', 'summary'):
            name = f'ix_items_{column}_kept_trgm'
            print(f'Building {name}...', flush=True)
            conn.exec_driver_sql(f'CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} '
                                 f'ON items USING gin ({column} gin_trgm_ops) WHERE kept IS TRUE')
            valid = conn.exec_driver_sql(
                'SELECT indisvalid FROM pg_index WHERE indexrelid = %s::regclass', (name,)
            ).scalar_one()
            if not valid:
                raise RuntimeError(f'{name} is invalid; inspect before retrying.')
            print(f'{name}: valid', flush=True)
        conn.exec_driver_sql('ANALYZE items')


if __name__ == '__main__':
    main()
