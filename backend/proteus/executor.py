"""Execute planner output with target-local receipts and restart recovery."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Awaitable, Callable
from typing import Any

import psycopg
from psycopg import sql

from . import catalog
from .schema import fingerprint

Progress = Callable[[str, list[dict[str, Any]]], Awaitable[None]]
_LOCK_WAIT_SECONDS = 5.0


def _json_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(',', ':'), default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _fingerprint(snapshot: dict[str, Any]) -> str:
    return fingerprint(snapshot)


def _step_hash(step: dict[str, Any]) -> str:
    return _json_hash({'sql': step['sql'], 'operation': step.get('operation')})


async def _fetchone(conn: Any, query: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    async with conn.cursor(row_factory=psycopg.rows.dict_row) as cursor:
        await cursor.execute(query, params)
        return await cursor.fetchone()


async def _progress(callback: Progress | None, stage: str, steps: list[dict[str, Any]]) -> None:
    if callback is not None:
        await callback(stage, steps)


async def _ensure_tracking_schema(conn: Any) -> None:
    await conn.execute('CREATE SCHEMA IF NOT EXISTS "_proteus"')
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS "_proteus".state (
            singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
            environment_id text NOT NULL,
            last_completed_revision text,
            expected_fingerprint text,
            last_completed_execution text,
            active_execution text,
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
        """
    )
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS "_proteus".migrations (
            execution_id text PRIMARY KEY,
            environment_id text NOT NULL,
            from_revision text,
            to_revision text NOT NULL,
            plan_checksum text NOT NULL,
            snapshot_fingerprint text NOT NULL,
            state text NOT NULL CHECK (state IN ('active', 'succeeded', 'failed', 'needs_attention')),
            error text,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
        """
    )
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS "_proteus".migration_steps (
            execution_id text NOT NULL REFERENCES "_proteus".migrations(execution_id),
            step_id text NOT NULL,
            step_order integer NOT NULL,
            operation_checksum text NOT NULL,
            state text NOT NULL CHECK (state IN ('pending', 'applied')),
            detail jsonb,
            applied_at timestamptz,
            PRIMARY KEY (execution_id, step_id)
        )
        """
    )


async def initialize_tracking(
    conn: Any, environment_id: str, revision_id: str, snapshot: dict[str, Any]
) -> None:
    """Create a baseline target receipt. Existing tracking is never overwritten."""
    async with conn.transaction():
        await _ensure_tracking_schema(conn)
        existing = await _fetchone(
            conn, 'SELECT environment_id FROM "_proteus".state WHERE singleton'
        )
        if existing is not None:
            if existing['environment_id'] != environment_id:
                raise ValueError('target is already tracked by another environment')
            raise ValueError('target tracking is already initialized')
        await conn.execute(
            """
            INSERT INTO "_proteus".state
                (singleton, environment_id, last_completed_revision, expected_fingerprint)
            VALUES (true, %s, %s, %s)
            """,
            (environment_id, revision_id, _fingerprint(snapshot)),
        )


async def _acquire_lock(conn: Any, environment_id: str) -> None:
    deadline = time.monotonic() + _LOCK_WAIT_SECONDS
    while True:
        row = await _fetchone(
            conn,
            'SELECT pg_try_advisory_lock(hashtextextended(%s, 0)) AS acquired',
            (environment_id,),
        )
        if row and row['acquired']:
            await conn.commit()
            return
        await conn.rollback()
        if time.monotonic() >= deadline:
            raise TimeoutError('timed out waiting for the target execution lock')
        await asyncio.sleep(0.1)


async def _release_lock(conn: Any, environment_id: str) -> None:
    try:
        await conn.execute('SELECT pg_advisory_unlock(hashtextextended(%s, 0))', (environment_id,))
        await conn.commit()
    except Exception:
        # Closing the dedicated session releases its advisory locks.
        pass


async def _set_limits(conn: Any, statement_timeout: str = '5min') -> None:
    await conn.execute("SELECT set_config('lock_timeout', '5s', true)")
    await conn.execute("SELECT set_config('statement_timeout', %s, true)", (statement_timeout,))


async def _prepare_execution(
    conn: Any,
    environment_id: str,
    execution_id: str,
    from_revision: str | None,
    to_revision: str,
    snapshot: dict[str, Any],
    plan: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Make durable pending receipts and reject a competing target execution."""
    steps = plan.get('steps', [])
    plan_checksum = _json_hash(steps)
    created_tracking = False
    async with conn.transaction():
        await _ensure_tracking_schema(conn)
        state = await _fetchone(conn, 'SELECT * FROM "_proteus".state WHERE singleton')
        if state is None:
            # An empty sandbox has no baseline receipt. A zero-step request is a
            # baseline import; otherwise the first plan starts from an empty state.
            # A baseline is not complete until its current database schema verifies.
            # Starting it at the candidate revision would leave a false receipt if
            # verification fails before any DDL is applied.
            initial_revision = from_revision if steps else None
            initial_fingerprint = _fingerprint({'tables': []})
            await conn.execute(
                """INSERT INTO "_proteus".state
                   (environment_id, last_completed_revision, expected_fingerprint)
                   VALUES (%s, %s, %s)""",
                (environment_id, initial_revision, initial_fingerprint),
            )
            state = await _fetchone(conn, 'SELECT * FROM "_proteus".state WHERE singleton')
            created_tracking = True
        if state['environment_id'] != environment_id:
            raise ValueError('target is already tracked by another environment')
        if state['active_execution'] not in {None, execution_id}:
            raise RuntimeError('another execution is active for this target')
        existing = await _fetchone(
            conn, 'SELECT * FROM "_proteus".migrations WHERE execution_id = %s', (execution_id,)
        )
        if existing is not None:
            if (
                existing['plan_checksum'] != plan_checksum
                or existing['to_revision'] != to_revision
                or existing['from_revision'] != from_revision
                or existing['snapshot_fingerprint'] != _fingerprint(snapshot)
            ):
                raise ValueError('execution ID belongs to a different plan')
            if existing['state'] == 'succeeded':
                return existing, []
            if state['last_completed_revision'] != from_revision:
                raise ValueError('target revision changed since this execution was prepared')
        else:
            if (
                not (created_tracking and not steps)
                and state['last_completed_revision'] != from_revision
            ):
                raise ValueError('target revision does not match the plan starting revision')
            await conn.execute(
                """
                INSERT INTO "_proteus".migrations
                    (execution_id, environment_id, from_revision, to_revision, plan_checksum,
                     snapshot_fingerprint, state)
                VALUES (%s, %s, %s, %s, %s, %s, 'active')
                """,
                (
                    execution_id,
                    environment_id,
                    from_revision,
                    to_revision,
                    plan_checksum,
                    _fingerprint(snapshot),
                ),
            )
            for order, step in enumerate(steps):
                await conn.execute(
                    """INSERT INTO "_proteus".migration_steps
                       (execution_id, step_id, step_order, operation_checksum, state)
                       VALUES (%s, %s, %s, %s, 'pending')""",
                    (execution_id, step['id'], order, _step_hash(step)),
                )
            existing = await _fetchone(
                conn, 'SELECT * FROM "_proteus".migrations WHERE execution_id = %s', (execution_id,)
            )
        await conn.execute(
            """UPDATE "_proteus".state SET active_execution = %s, updated_at = clock_timestamp()
               WHERE singleton""",
            (execution_id,),
        )
        pending = await _fetch_steps(conn, execution_id, steps)
    return existing, pending


async def _fetch_steps(
    conn: Any, execution_id: str, plan_steps: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    cursor = await conn.execute(
        """SELECT step_id, state FROM "_proteus".migration_steps
           WHERE execution_id = %s ORDER BY step_order""",
        (execution_id,),
    )
    states = {row['step_id']: row['state'] for row in await cursor.fetchall()}
    return [{**step, 'state': states.get(step['id'], 'pending')} for step in plan_steps]


async def _mark_step(
    conn: Any, execution_id: str, step: dict[str, Any], detail: dict[str, Any]
) -> None:
    await conn.execute(
        """UPDATE "_proteus".migration_steps
           SET state = 'applied', detail = %s, applied_at = clock_timestamp()
           WHERE execution_id = %s AND step_id = %s""",
        (json.dumps(detail), execution_id, step['id']),
    )


async def _verify(conn: Any, schema_name: str, snapshot: dict[str, Any]) -> None:
    result = await catalog.introspect(conn, schema_name, reference=snapshot)
    if result['unsupported']:
        raise ValueError(
            'result contains unsupported objects: ' + json.dumps(result['unsupported'])
        )
    if _fingerprint(result['snapshot']) != _fingerprint(snapshot):
        raise ValueError('result schema does not match the planned snapshot')


async def _complete(
    conn: Any, execution_id: str, to_revision: str, snapshot: dict[str, Any]
) -> None:
    await conn.execute(
        """UPDATE "_proteus".migrations
           SET state = 'succeeded', error = NULL, updated_at = clock_timestamp()
           WHERE execution_id = %s""",
        (execution_id,),
    )
    await conn.execute(
        """UPDATE "_proteus".state
           SET last_completed_revision = %s, expected_fingerprint = %s,
               last_completed_execution = %s, active_execution = NULL, updated_at = clock_timestamp()
           WHERE singleton""",
        (to_revision, _fingerprint(snapshot), execution_id),
    )


async def _migration_state(conn: Any, execution_id: str) -> str | None:
    row = await _fetchone(
        conn,
        'SELECT state FROM "_proteus".migrations WHERE execution_id = %s',
        (execution_id,),
    )
    return row['state'] if row is not None else None


async def _has_applied_steps(conn: Any, execution_id: str) -> bool:
    row = await _fetchone(
        conn,
        """SELECT EXISTS (
               SELECT 1 FROM "_proteus".migration_steps
               WHERE execution_id = %s AND state = 'applied'
           ) AS applied""",
        (execution_id,),
    )
    return bool(row and row['applied'])


async def _record_failure(conn: Any, execution_id: str, error: str, partial: bool) -> bool:
    """Record a failure without replacing a receipt that already committed."""
    async with conn.transaction():
        await _ensure_tracking_schema(conn)
        state = 'needs_attention' if partial else 'failed'
        cursor = await conn.execute(
            """UPDATE "_proteus".migrations SET state = %s, error = %s, updated_at = clock_timestamp()
               WHERE execution_id = %s AND state <> 'succeeded'
               RETURNING execution_id""",
            (state, error, execution_id),
        )
        if await cursor.fetchone() is None:
            return False
        if not partial:
            await conn.execute(
                """UPDATE "_proteus".state SET active_execution = NULL, updated_at = clock_timestamp()
                   WHERE singleton AND active_execution = %s""",
                (execution_id,),
            )
        return True


def _is_concurrent_index(step: dict[str, Any]) -> bool:
    operation = step.get('operation') or {}
    return operation.get('kind') in {'create_index', 'drop_index'} and not step.get(
        'transactional', True
    )


async def _index_matches(
    conn: Any, schema_name: str, operation: dict[str, Any]
) -> tuple[bool, bool]:
    """Return whether a named index has this operation's supported definition and is valid."""
    row = await _fetchone(
        conn,
        """
        SELECT i.indisvalid, i.indisready, i.indisunique, tc.relname AS table_name,
               array_agg(a.attname ORDER BY key.n) AS columns
        FROM pg_class ic
        JOIN pg_namespace ins ON ins.oid = ic.relnamespace
        JOIN pg_index i ON i.indexrelid = ic.oid
        JOIN pg_class tc ON tc.oid = i.indrelid
        JOIN pg_namespace tns ON tns.oid = tc.relnamespace
        LEFT JOIN LATERAL unnest(i.indkey) WITH ORDINALITY key(attnum, n) ON true
        LEFT JOIN pg_attribute a ON a.attrelid = tc.oid AND a.attnum = key.attnum
        WHERE ins.nspname = %s AND ic.relname = %s
        GROUP BY i.indisvalid, i.indisready, i.indisunique, tc.relname, tns.nspname
        """,
        (schema_name, operation['name']),
    )
    if row is None:
        return False, False
    matches = (
        row['columns'] == operation.get('columns', [])
        and row['indisunique'] == bool(operation.get('unique', False))
        and row['table_name'] == operation.get('table')
    )
    return matches, bool(row['indisvalid'] and row['indisready'])


async def _run_concurrent_step(
    conn: Any, schema_name: str, execution_id: str, step: dict[str, Any]
) -> None:
    operation = step.get('operation') or {}
    kind = operation.get('kind')
    if kind not in {'create_index', 'drop_index'}:
        raise ValueError(
            'non-transactional plan step is not a supported concurrent index operation'
        )
    await conn.set_autocommit(True)
    try:
        await conn.execute("SELECT set_config('lock_timeout', '5s', false)")
        await conn.execute("SELECT set_config('statement_timeout', '30min', false)")
        if kind == 'create_index':
            matches, valid = await _index_matches(conn, schema_name, operation)
            if valid and matches:
                await conn.set_autocommit(False)
                async with conn.transaction():
                    await _mark_step(conn, execution_id, step, {'recovered': True})
                return
            if matches and not valid:
                # An invalid index with this exact name, table and keys can only be
                # cleaned because it matches the pending operation's definition.
                await conn.execute(
                    sql.SQL('DROP INDEX CONCURRENTLY {}.{}').format(
                        sql.Identifier(schema_name), sql.Identifier(operation['name'])
                    )
                )
            elif await _index_exists(conn, schema_name, operation['name']):
                # A different same-name index is an ownership conflict, not ours to drop.
                raise ValueError('index name is occupied by a different definition')
            await conn.execute(step['sql'])
            matches, valid = await _index_matches(conn, schema_name, operation)
            if not matches or not valid:
                raise ValueError('concurrent index did not reach a valid expected definition')
        else:
            matches, _ = await _index_matches(conn, schema_name, operation)
            if matches:
                await conn.execute(step['sql'])
            elif not await _index_exists(conn, schema_name, operation['name']):
                pass
            else:
                raise ValueError('index name belongs to a different definition')
        await conn.set_autocommit(False)
        async with conn.transaction():
            await _mark_step(conn, execution_id, step, {'executed': True})
    finally:
        if conn.autocommit:
            await conn.execute("SELECT set_config('lock_timeout', '0', false)")
            await conn.execute("SELECT set_config('statement_timeout', '0', false)")
            await conn.set_autocommit(False)


async def _index_exists(conn: Any, schema_name: str, name: str) -> bool:
    row = await _fetchone(
        conn,
        """SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
           WHERE n.nspname = %s AND c.relname = %s AND c.relkind = 'i' """,
        (schema_name, name),
    )
    return row is not None


async def execute_plan(
    dsn: str,
    schema_name: str,
    environment_id: str,
    execution_id: str,
    from_revision: str | None,
    to_revision: str,
    snapshot: dict[str, Any],
    plan: dict[str, Any],
    progress: Progress | None = None,
) -> dict[str, Any]:
    """Apply one exact plan. A repeated execution ID resumes its own receipts only."""
    steps = plan.get('steps', [])
    status_steps = [
        {
            'id': step['id'],
            'state': 'pending',
            'description': step['description'],
            'sql': step['sql'],
            'impact': step.get('impact', 'metadata'),
        }
        for step in steps
    ]
    async with await psycopg.AsyncConnection.connect(
        dsn, row_factory=psycopg.rows.dict_row
    ) as conn:
        try:
            await _progress(progress, 'checking', status_steps)
            await _acquire_lock(conn, environment_id)
            migration, persisted_steps = await _prepare_execution(
                conn,
                environment_id,
                execution_id,
                from_revision,
                to_revision,
                snapshot,
                plan,
            )
            if migration['state'] == 'succeeded':
                for status in status_steps:
                    status['state'] = 'applied'
                await _progress(progress, 'verifying', status_steps)
                return {'status': 'succeeded'}
            for status, persisted in zip(status_steps, persisted_steps, strict=False):
                status['state'] = persisted['state']
            multi_phase = any(
                _is_concurrent_index(step)
                or step.get('operation', {}).get('kind') == 'validate_constraint'
                for step in steps
            )
            if not multi_phase:
                await _progress(progress, 'applying', status_steps)
                async with conn.transaction():
                    await _set_limits(conn)
                    for step in persisted_steps:
                        if step['state'] == 'applied':
                            continue
                        await conn.execute(step['sql'])
                        await _mark_step(conn, execution_id, step, {'executed': True})
                        next(item for item in status_steps if item['id'] == step['id'])['state'] = (
                            'applied'
                        )
                    await _progress(progress, 'verifying', status_steps)
                    await _verify(conn, schema_name, snapshot)
                    await _complete(conn, execution_id, to_revision, snapshot)
                return {'status': 'succeeded'}

            for step in persisted_steps:
                if step['state'] == 'applied':
                    continue
                stage = (
                    'validating'
                    if step.get('operation', {}).get('kind') == 'validate_constraint'
                    else 'applying'
                )
                await _progress(progress, stage, status_steps)
                if _is_concurrent_index(step):
                    await _run_concurrent_step(conn, schema_name, execution_id, step)
                else:
                    async with conn.transaction():
                        await _set_limits(conn)
                        await conn.execute(step['sql'])
                        await _mark_step(conn, execution_id, step, {'executed': True})
                next(item for item in status_steps if item['id'] == step['id'])['state'] = 'applied'
            await _progress(progress, 'verifying', status_steps)
            async with conn.transaction():
                await _set_limits(conn)
                await _verify(conn, schema_name, snapshot)
                await _complete(conn, execution_id, to_revision, snapshot)
            return {'status': 'succeeded'}
        except Exception as error:
            message = str(error)
            partial = False
            try:
                await conn.rollback()
                migration_state = await _migration_state(conn, execution_id)
                if migration_state == 'succeeded':
                    return {'status': 'succeeded'}
                # This reads committed target receipts, so a completed NOT VALID
                # phase is never reported as a full rollback after a later failure.
                if migration_state is not None:
                    partial = await _has_applied_steps(conn, execution_id)
                    if not await _record_failure(conn, execution_id, message, partial):
                        return {'status': 'succeeded'}
                    recorded = await _fetch_steps(conn, execution_id, steps)
                    for status, saved in zip(status_steps, recorded, strict=False):
                        status['state'] = saved['state']
                    await conn.commit()
                    await _progress(
                        progress, 'needs_attention' if partial else 'failed', status_steps
                    )
            except Exception:
                # A lost session can leave a commit outcome unknown. Do not claim a
                # rollback or replace its receipt; a same-ID retry reconciles it.
                return {'status': 'needs_attention', 'error': message}
            return {'status': 'needs_attention' if partial else 'failed', 'error': message}
        finally:
            await _release_lock(conn, environment_id)
