"""Read-only per-row evidence, including original columns and full sequence state.

Run with the project's normal POSTGRES_* settings. SNAPSHOT_COLUMNS optionally
contains the original column projection, so migrations cannot hide rewritten data.
The emitted evidence can contain identifiers and must remain private.
"""
from __future__ import annotations

import hashlib
import json
import os


def compare_history(before, after, *, allow_new_rows=False, allow_sequence_advance=False):
    errors = []
    for table, previous in before['tables'].items():
        if table == 'alembic_version':
            continue
        current = after['tables'].get(table)
        if current is None or before['columns'][table] != after['columns'].get(table):
            errors.append(f'{table}: missing table/original column projection')
            continue
        old_rows, new_rows = previous['rows'], current['rows']
        for key, digest in old_rows.items():
            if new_rows.get(key) != digest:
                errors.append(f'{table}: historical row changed/missing: {key}')
        if not allow_new_rows and old_rows != new_rows:
            errors.append(f'{table}: row set changed')
    for name, previous in before['sequences'].items():
        current = after['sequences'].get(name)
        if current is None:
            errors.append(f'{name}: sequence missing')
            continue
        stable = set(previous) - {'last_value', 'is_called'}
        if any(previous[k] != current[k] for k in stable):
            errors.append(f'{name}: sequence definition changed')
        if not allow_sequence_advance and previous != current:
            errors.append(f'{name}: sequence state changed')
        elif allow_sequence_advance and current['last_value'] < previous['last_value']:
            errors.append(f'{name}: sequence moved backwards')
    if not after['ledger'].get('valid'):
        errors.append('Ledger integrity validation failed')
    if not allow_new_rows and before['ledger']['head_hash'] != after['ledger']['head_hash']:
        errors.append('Ledger head changed')
    return errors


def snapshot():
    from sqlalchemy import create_engine, inspect, text
    from sqlalchemy.orm import Session
    from app.config import PostgresSettings
    from app.repositories.ledger import LedgerRepository

    engine = create_engine(PostgresSettings.from_env().sqlalchemy_url)
    projection = json.loads(os.environ.get('SNAPSHOT_COLUMNS', 'null'))
    with engine.connect().execution_options(isolation_level='REPEATABLE READ') as connection:
        with connection.begin():
            connection.execute(text('SET TRANSACTION READ ONLY'))
            connection.execute(text("SET LOCAL TIME ZONE 'UTC'"))
            inspector = inspect(connection)
            actual = {table: [c['name'] for c in inspector.get_columns(table)]
                      for table in sorted(inspector.get_table_names(schema='public'))}
            columns = actual if projection is None else projection
            quote = engine.dialect.identifier_preparer.quote
            tables = {}
            for table, names in columns.items():
                if table not in actual or not set(names) <= set(actual[table]):
                    raise ValueError('Missing original table/columns: ' + table)
                primary = inspector.get_pk_constraint(table)['constrained_columns']
                if not set(primary) <= set(names):
                    raise ValueError('Original projection omits primary key: ' + table)
                query = ('SELECT row_to_json(original)::text FROM (SELECT '
                         + ', '.join(quote(name) for name in names)
                         + ' FROM public.' + quote(table) + ') original')
                values = sorted(connection.scalars(text(query)))
                rows = {}
                for value in values:
                    data = json.loads(value)
                    digest = hashlib.sha256(value.encode('utf-8')).hexdigest()
                    key = json.dumps([data[c] for c in primary], separators=(',', ':')) if primary else digest
                    if key in rows:
                        raise ValueError('Duplicate row identity: ' + table)
                    rows[key] = digest
                tables[table] = dict(count=len(values), rows=rows, primary_key=primary,
                    sha256=hashlib.sha256('\n'.join(values).encode('utf-8')).hexdigest())
            sequences = {}
            sequence_rows = connection.execute(text(
                "SELECT sequencename,start_value,min_value,max_value,increment_by,cycle,cache_size "
                "FROM pg_sequences WHERE schemaname='public' ORDER BY sequencename"
            )).mappings().all()
            for row in sequence_rows:
                name = row['sequencename']
                state = connection.execute(text('SELECT last_value,is_called FROM public.'
                                                + quote(name))).mappings().one()
                sequences[name] = {**dict(row), **dict(state)}
            return dict(database_version=connection.scalar(text('SELECT version()')),
                revisions=sorted(connection.scalars(text('SELECT version_num FROM alembic_version'))),
                columns=columns, actual_columns=actual, tables=tables, sequences=sequences,
                ledger=LedgerRepository().verify(Session(bind=connection)))


if __name__ == '__main__':
    print(json.dumps(snapshot(), ensure_ascii=False, sort_keys=True))
