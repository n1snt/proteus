"""Run the PostgreSQL large-table benchmark outside the normal test suite."""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import os
import platform
import re
import resource
import statistics
import sys
import time
import traceback
import uuid
from pathlib import Path
from typing import Any

import psycopg
from proteus import catalog, diff, executor, merge, planner, schema
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row

DATABASE_PREFIX = 'proteus_benchmark_'
SCHEMA_NAME = 'public'
SIZE_QUERY = """
SELECT
    pg_relation_size(c.oid) + pg_relation_size(c.oid, 'fsm')
        + pg_relation_size(c.oid, 'vm') + pg_relation_size(c.oid, 'init')
        AS base_table_bytes,
    COALESCE(pg_relation_size(NULLIF(c.reltoastrelid, 0)), 0)
        + COALESCE(pg_relation_size(NULLIF(c.reltoastrelid, 0), 'fsm'), 0)
        + COALESCE(pg_relation_size(NULLIF(c.reltoastrelid, 0), 'vm'), 0)
        + COALESCE(pg_relation_size(NULLIF(c.reltoastrelid, 0), 'init'), 0)
        AS toast_table_bytes,
    pg_indexes_size(c.oid) AS base_index_bytes,
    COALESCE(pg_indexes_size(NULLIF(c.reltoastrelid, 0)), 0) AS toast_index_bytes,
    pg_total_relation_size(c.oid) AS total_relation_bytes
FROM pg_class AS c
WHERE c.oid = 'public.large_events'::regclass
"""


class BenchmarkFailure(RuntimeError):
    """A benchmark prerequisite or required product operation failed."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--admin-dsn',
        default=os.environ.get('PROTEUS_SANDBOX_URL'),
        help='PostgreSQL admin connection string. Defaults to PROTEUS_SANDBOX_URL.',
    )
    parser.add_argument(
        '--database',
        default=f'{DATABASE_PREFIX}{uuid.uuid4().hex[:12]}',
        help=f'New source database name. It must start with {DATABASE_PREFIX!r}.',
    )
    parser.add_argument('--target-bytes', type=int, default=5_000_000_000)
    parser.add_argument('--payload-bytes', type=int, default=1024)
    parser.add_argument('--copy-batch-bytes', type=int, default=8 * 1024 * 1024)
    parser.add_argument('--schema-samples', type=int, default=3)
    parser.add_argument('--operation-timeout-seconds', type=float, default=1_800)
    parser.add_argument('--interrupt-delay-seconds', type=float, default=0.1)
    parser.add_argument('--report', type=Path, default=Path('.local/large-table-benchmark.json'))
    parser.add_argument('--keep-database', action='store_true')
    args = parser.parse_args()

    if not args.admin_dsn:
        parser.error('--admin-dsn or PROTEUS_SANDBOX_URL is required')
    if args.target_bytes <= 0 or args.copy_batch_bytes <= 0:
        parser.error('--target-bytes and --copy-batch-bytes must be positive')
    if not 256 <= args.payload_bytes <= 1_500:
        parser.error('--payload-bytes must be between 256 and 1500 to keep rows inline')
    if args.schema_samples < 1 or args.operation_timeout_seconds <= 0:
        parser.error('--schema-samples and --operation-timeout-seconds must be positive')
    if args.interrupt_delay_seconds < 0:
        parser.error('--interrupt-delay-seconds cannot be negative')
    if not re.fullmatch(rf'{DATABASE_PREFIX}[a-z0-9_]+', args.database):
        parser.error(
            f'--database must start with {DATABASE_PREFIX!r} and use lowercase safe characters'
        )
    if args.report.is_absolute() or args.report.parts[:1] != ('.local',):
        parser.error('--report must be a relative path under .local/')
    return args


def elapsed_seconds(started: float) -> float:
    return round(time.perf_counter() - started, 6)


def error_detail(error: BaseException) -> dict[str, str]:
    return {'type': type(error).__name__, 'message': str(error)}


def process_max_rss_bytes() -> int:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return value if sys.platform == 'darwin' else value * 1024


def read_cgroup_limit(path: Path) -> str | int | None:
    try:
        value = path.read_text().strip()
    except OSError:
        return None
    if value == 'max':
        return 'unlimited'
    try:
        return int(value)
    except ValueError:
        return value


def hardware_details() -> dict[str, Any]:
    memory_bytes: int | None = None
    try:
        memory_bytes = os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES')
    except (AttributeError, OSError, ValueError):
        pass
    cpu_limit = None
    try:
        quota, period = Path('/sys/fs/cgroup/cpu.max').read_text().split()
        cpu_limit = 'unlimited' if quota == 'max' else int(quota) / int(period)
    except (OSError, ValueError):
        pass
    return {
        'platform': platform.platform(),
        'python': sys.version,
        'host_cpu_count': os.cpu_count(),
        'host_memory_bytes': memory_bytes,
        'container_memory_limit_bytes': read_cgroup_limit(Path('/sys/fs/cgroup/memory.max')),
        'container_cpu_limit': cpu_limit,
    }


def branch_database_name(database: str) -> str:
    name = f'{database}_branch'
    if len(name) > 63:
        raise BenchmarkFailure('benchmark database name leaves no room for the branch suffix')
    return name


async def database_sizes(conn: psycopg.AsyncConnection[Any]) -> dict[str, int]:
    row = await (await conn.execute(SIZE_QUERY)).fetchone()
    if row is None:
        raise BenchmarkFailure('large_events is missing')
    keys = (
        'base_table_bytes',
        'toast_table_bytes',
        'base_index_bytes',
        'toast_index_bytes',
        'total_relation_bytes',
    )
    if isinstance(row, dict):
        sizes = {key: int(row[key]) for key in keys}
    else:
        sizes = dict(zip(keys, row, strict=True))
    sizes['physical_data_bytes_excluding_indexes'] = (
        sizes['base_table_bytes'] + sizes['toast_table_bytes']
    )
    sizes['all_index_bytes'] = sizes['base_index_bytes'] + sizes['toast_index_bytes']
    return sizes


async def create_database(admin_dsn: str, database: str) -> str:
    async with await psycopg.AsyncConnection.connect(admin_dsn, autocommit=True) as conn:
        exists = await (
            await conn.execute('SELECT 1 FROM pg_database WHERE datname = %s', (database,))
        ).fetchone()
        if exists:
            raise BenchmarkFailure(f'refusing to use existing database {database!r}')
        await conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database)))
    parameters = conninfo_to_dict(admin_dsn)
    parameters['dbname'] = database
    return make_conninfo(**parameters)


async def drop_database(admin_dsn: str, database: str) -> None:
    if not database.startswith(DATABASE_PREFIX):
        raise BenchmarkFailure('refusing to remove a database outside the benchmark prefix')
    async with await psycopg.AsyncConnection.connect(admin_dsn, autocommit=True) as conn:
        await conn.execute(
            sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(database))
        )


async def create_loaded_table(
    conn: psycopg.AsyncConnection[Any], args: argparse.Namespace
) -> dict[str, Any]:
    await conn.execute(
        """
        CREATE TABLE public.large_events (
            id bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            observed_at timestamp with time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
            event_code integer NOT NULL,
            priority integer NOT NULL DEFAULT 0,
            payload bytea NOT NULL
        )
        """
    )
    await conn.commit()

    rows_per_batch = max(1, args.copy_batch_bytes // args.payload_bytes)
    started = time.perf_counter()
    rss_before = process_max_rss_bytes()
    inserted_rows = 0
    next_size_check = 0
    sizes = await database_sizes(conn)
    while sizes['physical_data_bytes_excluding_indexes'] < args.target_bytes:
        async with conn.cursor() as cursor:
            async with cursor.copy(
                'COPY public.large_events (event_code, payload) FROM STDIN'
            ) as copy:
                for _ in range(rows_per_batch):
                    payload = os.urandom(args.payload_bytes)
                    await copy.write_row((int.from_bytes(payload[:4], 'big', signed=True), payload))
        await conn.commit()
        inserted_rows += rows_per_batch
        if inserted_rows * args.payload_bytes >= next_size_check:
            sizes = await database_sizes(conn)
            next_size_check += 64 * 1024 * 1024

    sizes = await database_sizes(conn)
    return {
        'status': 'succeeded',
        'seconds': elapsed_seconds(started),
        'rows_inserted': inserted_rows,
        'payload_bytes_per_row': args.payload_bytes,
        'copy_batch_bytes': args.copy_batch_bytes,
        'process_max_rss_bytes_before_loader': rss_before,
        'process_max_rss_bytes_after_loader': process_max_rss_bytes(),
        'sizes': sizes,
    }


def table(snapshot: dict[str, Any]) -> dict[str, Any]:
    return next(item for item in snapshot['tables'] if item['name'] == 'large_events')


def column(snapshot: dict[str, Any], name: str) -> dict[str, Any]:
    return next(item for item in table(snapshot)['columns'] if item['name'] == name)


def add_column(snapshot: dict[str, Any], name: str, data_type: str) -> dict[str, Any]:
    result = copy.deepcopy(snapshot)
    table(result)['columns'].append(
        {
            'id': str(uuid.uuid4()),
            'name': name,
            'data_type': data_type,
            'nullable': True,
            'default': None,
            'identity': None,
        }
    )
    return result


def add_index(snapshot: dict[str, Any], name: str, column_name: str) -> dict[str, Any]:
    result = copy.deepcopy(snapshot)
    table(result)['indexes'].append(
        {
            'id': str(uuid.uuid4()),
            'name': name,
            'columns': [column(result, column_name)['id']],
            'unique': False,
        }
    )
    return result


def add_check(snapshot: dict[str, Any], name: str, column_name: str) -> dict[str, Any]:
    result = copy.deepcopy(snapshot)
    table(result)['constraints'].append(
        {
            'id': str(uuid.uuid4()),
            'name': name,
            'kind': 'check',
            'columns': [column(result, column_name)['id']],
            'expression': f'{column_name} >= 0',
        }
    )
    return result


def rename_column(snapshot: dict[str, Any], old_name: str, new_name: str) -> dict[str, Any]:
    result = copy.deepcopy(snapshot)
    column(result, old_name)['name'] = new_name
    return result


def change_type(snapshot: dict[str, Any], name: str, data_type: str) -> dict[str, Any]:
    result = copy.deepcopy(snapshot)
    column(result, name)['data_type'] = data_type
    return result


async def catalog_snapshot(
    conn: psycopg.AsyncConnection[Any], reference: dict[str, Any] | None = None
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    result = await catalog.introspect(conn, SCHEMA_NAME, reference=reference)
    return result['snapshot'], result['unsupported']


async def target_receipt(dsn: str, execution_id: str) -> dict[str, Any] | None:
    async with await psycopg.AsyncConnection.connect(dsn, row_factory=dict_row) as conn:
        migration = await (
            await conn.execute(
                'SELECT state, error FROM "_proteus".migrations WHERE execution_id = %s',
                (execution_id,),
            )
        ).fetchone()
        if migration is None:
            return None
        steps = await (
            await conn.execute(
                'SELECT state, count(*) AS count FROM "_proteus".migration_steps '
                'WHERE execution_id = %s GROUP BY state ORDER BY state',
                (execution_id,),
            )
        ).fetchall()
    return {'state': migration['state'], 'error': migration['error'], 'steps': steps}


async def execute_plan(
    args: argparse.Namespace,
    dsn: str,
    environment_id: str,
    execution_id: str,
    from_revision: str | None,
    to_revision: str,
    snapshot: dict[str, Any],
    plan: dict[str, Any],
    progress: executor.Progress | None = None,
) -> dict[str, Any]:
    return await asyncio.wait_for(
        executor.execute_plan(
            dsn,
            SCHEMA_NAME,
            environment_id,
            execution_id,
            from_revision,
            to_revision,
            snapshot,
            plan,
            progress,
        ),
        timeout=args.operation_timeout_seconds,
    )


async def apply_product_change(
    args: argparse.Namespace,
    dsn: str,
    environment_id: str,
    from_revision: str,
    to_revision: str,
    before: dict[str, Any],
    after: dict[str, Any],
    execution_id: str | None = None,
    progress: executor.Progress | None = None,
) -> dict[str, Any]:
    plan = planner.build_plan(before, after, SCHEMA_NAME, online=True)
    execution_id = execution_id or str(uuid.uuid4())
    started = time.perf_counter()
    result = await execute_plan(
        args,
        dsn,
        environment_id,
        execution_id,
        from_revision,
        to_revision,
        after,
        plan,
        progress,
    )
    receipt = await target_receipt(dsn, execution_id)
    return {
        'status': result['status'],
        'seconds': elapsed_seconds(started),
        'error': result.get('error'),
        'steps': len(plan['steps']),
        'warnings': plan['warnings'],
        'execution_id': execution_id,
        'receipt': receipt,
    }


async def schema_metrics(
    dsn: str, label: str, samples: int, reference: dict[str, Any]
) -> dict[str, Any]:
    metrics: list[dict[str, Any]] = []
    rss_before = process_max_rss_bytes()
    for _ in range(samples):
        async with await psycopg.AsyncConnection.connect(dsn) as conn:
            catalog_started = time.perf_counter()
            snapshot, unsupported = await catalog_snapshot(conn, reference)
            catalog_seconds = elapsed_seconds(catalog_started)
        if unsupported:
            raise BenchmarkFailure(f'{label} catalog has unsupported objects: {unsupported}')
        candidate = rename_column(snapshot, 'priority', 'priority_measurement')
        diff_started = time.perf_counter()
        changes = diff.diff_snapshots(snapshot, candidate)
        diff_seconds = elapsed_seconds(diff_started)
        merge_started = time.perf_counter()
        merged = merge.merge_snapshots(snapshot, candidate, snapshot)
        merge_seconds = elapsed_seconds(merge_started)
        plan_started = time.perf_counter()
        plan = planner.build_plan(snapshot, merged['snapshot'], SCHEMA_NAME, online=True)
        metrics.append(
            {
                'catalog_seconds': catalog_seconds,
                'diff_seconds': diff_seconds,
                'merge_seconds': merge_seconds,
                'plan_seconds': elapsed_seconds(plan_started),
                'changes': len(changes),
                'plan_steps': len(plan['steps']),
            }
        )
    return {
        'status': 'succeeded',
        'dataset': label,
        'samples': metrics,
        'process_max_rss_bytes_before_engine': rss_before,
        'process_max_rss_bytes_after_engine': process_max_rss_bytes(),
    }


def latency_summary(samples: list[float], errors: list[dict[str, str]]) -> dict[str, Any]:
    result: dict[str, Any] = {'completed': len(samples), 'errors': errors}
    if samples:
        ordered = sorted(samples)
        result.update(
            {
                'mean_ms': round(statistics.fmean(samples) * 1_000, 3),
                'p50_ms': round(ordered[len(ordered) // 2] * 1_000, 3),
                'p95_ms': round(
                    ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))] * 1_000, 3
                ),
                'max_ms': round(max(samples) * 1_000, 3),
            }
        )
    return result


async def read_workload(
    dsn: str, stop: asyncio.Event, samples: list[float], errors: list[dict[str, str]], max_id: int
) -> None:
    async with await psycopg.AsyncConnection.connect(dsn) as conn:
        await conn.execute("SET statement_timeout = '5s'")
        while not stop.is_set():
            started = time.perf_counter()
            try:
                await conn.execute(
                    'SELECT 1 FROM public.large_events WHERE id = %s',
                    (1 + (len(samples) % max_id),),
                )
                await conn.commit()
                samples.append(time.perf_counter() - started)
            except Exception as error:
                await conn.rollback()
                errors.append(error_detail(error))


async def write_workload(
    dsn: str, stop: asyncio.Event, samples: list[float], errors: list[dict[str, str]]
) -> None:
    async with await psycopg.AsyncConnection.connect(dsn) as conn:
        await conn.execute("SET statement_timeout = '5s'")
        while not stop.is_set():
            started = time.perf_counter()
            try:
                await conn.execute(
                    'INSERT INTO public.large_events (event_code, payload) VALUES (%s, %s)',
                    (len(samples), os.urandom(1_024)),
                )
                await conn.commit()
                samples.append(time.perf_counter() - started)
            except Exception as error:
                await conn.rollback()
                errors.append(error_detail(error))


async def executor_index_workload(
    args: argparse.Namespace,
    dsn: str,
    environment_id: str,
    from_revision: str,
    to_revision: str,
    before: dict[str, Any],
    after: dict[str, Any],
) -> dict[str, Any]:
    async with await psycopg.AsyncConnection.connect(dsn) as conn:
        row = await (await conn.execute('SELECT max(id) FROM public.large_events')).fetchone()
        max_id = int(row[0]) if row and row[0] else 1
    stop = asyncio.Event()
    reads: list[float] = []
    writes: list[float] = []
    read_errors: list[dict[str, str]] = []
    write_errors: list[dict[str, str]] = []
    workers = [
        asyncio.create_task(read_workload(dsn, stop, reads, read_errors, max_id)),
        asyncio.create_task(write_workload(dsn, stop, writes, write_errors)),
    ]
    await asyncio.sleep(0.1)
    try:
        result = await apply_product_change(
            args, dsn, environment_id, from_revision, to_revision, before, after
        )
    finally:
        stop.set()
        await asyncio.gather(*workers)
    result['read_latency'] = latency_summary(reads, read_errors)
    result['write_latency'] = latency_summary(writes, write_errors)
    return result


async def interrupted_executor_recovery(
    args: argparse.Namespace,
    dsn: str,
    environment_id: str,
    from_revision: str,
    to_revision: str,
    before: dict[str, Any],
    after: dict[str, Any],
) -> dict[str, Any]:
    plan = planner.build_plan(before, after, SCHEMA_NAME, online=True)
    execution_id = str(uuid.uuid4())
    applying = asyncio.Event()

    async def progress(stage: str, _: list[dict[str, Any]]) -> None:
        if stage == 'applying':
            applying.set()

    task = asyncio.create_task(
        execute_plan(
            args,
            dsn,
            environment_id,
            execution_id,
            from_revision,
            to_revision,
            after,
            plan,
            progress,
        )
    )
    try:
        await asyncio.wait_for(applying.wait(), timeout=10)
        await asyncio.sleep(args.interrupt_delay_seconds)
        if task.done():
            completed = await task
            return {
                'status': 'skipped',
                'reason': 'concurrent index completed before the requested interruption',
                'completed_result': completed,
                'receipt': await target_receipt(dsn, execution_id),
            }
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await asyncio.sleep(0.1)
        interrupted_receipt = await target_receipt(dsn, execution_id)
        recovered = await execute_plan(
            args,
            dsn,
            environment_id,
            execution_id,
            from_revision,
            to_revision,
            after,
            plan,
        )
        return {
            'status': 'succeeded' if recovered['status'] == 'succeeded' else 'failed',
            'interruption': 'task cancellation after executor persisted the active execution',
            'interrupted_receipt': interrupted_receipt,
            'recovered_result': recovered,
            'recovered_receipt': await target_receipt(dsn, execution_id),
        }
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


async def executor_lock_timeout_recovery(
    args: argparse.Namespace,
    dsn: str,
    environment_id: str,
    from_revision: str,
    to_revision: str,
    before: dict[str, Any],
    after: dict[str, Any],
) -> dict[str, Any]:
    plan = planner.build_plan(before, after, SCHEMA_NAME, online=True)
    execution_id = str(uuid.uuid4())
    async with await psycopg.AsyncConnection.connect(dsn) as holder:
        await holder.execute('LOCK TABLE public.large_events IN ACCESS EXCLUSIVE MODE')
        blocked = await execute_plan(
            args,
            dsn,
            environment_id,
            execution_id,
            from_revision,
            to_revision,
            after,
            plan,
        )
        blocked_receipt = await target_receipt(dsn, execution_id)
        await holder.rollback()
    recovered = await execute_plan(
        args,
        dsn,
        environment_id,
        execution_id,
        from_revision,
        to_revision,
        after,
        plan,
    )
    return {
        'status': 'succeeded'
        if blocked['status'] == 'failed' and recovered['status'] == 'succeeded'
        else 'failed',
        'blocked_result': blocked,
        'blocked_receipt': blocked_receipt,
        'recovered_result': recovered,
        'recovered_receipt': await target_receipt(dsn, execution_id),
    }


async def run_benchmark(args: argparse.Namespace, report: dict[str, Any]) -> None:
    branch_database = branch_database_name(args.database)
    report['databases'] = {'source': args.database, 'branch': branch_database}
    source_dsn: str | None = None
    branch_dsn: str | None = None
    try:
        source_dsn = await create_database(args.admin_dsn, args.database)
        branch_dsn = await create_database(args.admin_dsn, branch_database)
        async with await psycopg.AsyncConnection.connect(source_dsn, row_factory=dict_row) as conn:
            report['operations']['load'] = await create_loaded_table(conn, args)
            snapshot, unsupported = await catalog_snapshot(conn)
            report['catalog_before_execution'] = {'unsupported': unsupported}
            if unsupported:
                raise BenchmarkFailure(
                    f'benchmark schema contains unsupported objects: {unsupported}'
                )
            await executor.initialize_tracking(conn, 'benchmark-source', 'baseline', snapshot)

        empty = {'tables': []}
        branch_plan = planner.build_plan(empty, snapshot, SCHEMA_NAME, online=True)
        branch_execution = str(uuid.uuid4())
        branch_started = time.perf_counter()
        branch_result = await execute_plan(
            args,
            branch_dsn,
            'benchmark-branch',
            branch_execution,
            None,
            'baseline',
            snapshot,
            branch_plan,
        )
        branch_receipt = await target_receipt(branch_dsn, branch_execution)
        report['operations']['branch_schema_creation'] = {
            'status': branch_result['status'],
            'seconds': elapsed_seconds(branch_started),
            'steps': len(branch_plan['steps']),
            'result': branch_result,
            'receipt': branch_receipt,
        }
        if branch_result['status'] != 'succeeded':
            raise BenchmarkFailure('executor did not create the schema-only branch')

        report['schema_metrics'] = {
            'small_schema_only_branch': await schema_metrics(
                branch_dsn, 'small_schema_only_branch', args.schema_samples, snapshot
            ),
            'large_populated_source': await schema_metrics(
                source_dsn, 'large_populated_source', args.schema_samples, snapshot
            ),
        }

        current = snapshot
        revision = 'baseline'
        renamed = rename_column(current, 'observed_at', 'captured_at')
        rename_result = await apply_product_change(
            args, source_dsn, 'benchmark-source', revision, 'rename', current, renamed
        )
        report['operations']['rename_column'] = rename_result
        if rename_result['status'] != 'succeeded':
            raise BenchmarkFailure('executor rename failed')
        current, revision = renamed, 'rename'

        nullable = add_column(current, 'note', 'text')
        nullable_result = await apply_product_change(
            args, source_dsn, 'benchmark-source', revision, 'nullable', current, nullable
        )
        report['operations']['add_nullable_column'] = nullable_result
        if nullable_result['status'] != 'succeeded':
            raise BenchmarkFailure('executor nullable-column addition failed')
        current, revision = nullable, 'nullable'

        indexed = add_index(current, 'large_events_event_code_idx', 'event_code')
        index_result = await executor_index_workload(
            args, source_dsn, 'benchmark-source', revision, 'index', current, indexed
        )
        report['operations']['concurrent_index'] = index_result
        if index_result['status'] != 'succeeded':
            raise BenchmarkFailure('executor concurrent index failed')
        current, revision = indexed, 'index'

        quality_column = add_column(current, 'quality_score', 'integer')
        quality_column_result = await apply_product_change(
            args,
            source_dsn,
            'benchmark-source',
            revision,
            'quality_column',
            current,
            quality_column,
        )
        report['operations']['add_constraint_column'] = quality_column_result
        if quality_column_result['status'] != 'succeeded':
            raise BenchmarkFailure('executor constraint-column addition failed')
        current, revision = quality_column, 'quality_column'

        async with await psycopg.AsyncConnection.connect(source_dsn) as conn:
            await conn.execute(
                'UPDATE public.large_events SET quality_score = -1 '
                'WHERE id = (SELECT min(id) FROM public.large_events)'
            )
            await conn.commit()
        constrained = add_check(current, 'large_events_quality_score_check', 'quality_score')
        constraint_execution = str(uuid.uuid4())
        constraint_failure = await apply_product_change(
            args,
            source_dsn,
            'benchmark-source',
            revision,
            'constraint',
            current,
            constrained,
            execution_id=constraint_execution,
        )
        async with await psycopg.AsyncConnection.connect(source_dsn) as conn:
            await conn.execute(
                'UPDATE public.large_events SET quality_score = NULL WHERE quality_score = -1'
            )
            await conn.commit()
        constraint_retry = await apply_product_change(
            args,
            source_dsn,
            'benchmark-source',
            revision,
            'constraint',
            current,
            constrained,
            execution_id=constraint_execution,
        )
        report['operations']['constraint_validation'] = {
            'status': 'succeeded'
            if constraint_failure['status'] == 'needs_attention'
            and constraint_retry['status'] == 'succeeded'
            else 'failed',
            'invalid_existing_data': constraint_failure,
            'retry_after_data_fix': constraint_retry,
        }
        if report['operations']['constraint_validation']['status'] != 'succeeded':
            raise BenchmarkFailure(
                'executor constraint failure and retry did not behave as expected'
            )
        current, revision = constrained, 'constraint'

        async with await psycopg.AsyncConnection.connect(source_dsn) as conn:
            before_node = await (
                await conn.execute(
                    "SELECT relfilenode FROM pg_class WHERE oid = 'public.large_events'::regclass"
                )
            ).fetchone()
        retyped = change_type(current, 'priority', 'bigint')
        type_result = await apply_product_change(
            args, source_dsn, 'benchmark-source', revision, 'type', current, retyped
        )
        async with await psycopg.AsyncConnection.connect(source_dsn) as conn:
            after_node = await (
                await conn.execute(
                    "SELECT relfilenode FROM pg_class WHERE oid = 'public.large_events'::regclass"
                )
            ).fetchone()
        type_result['rewrite_observed'] = bool(
            before_node and after_node and before_node[0] != after_node[0]
        )
        report['operations']['type_conversion'] = type_result
        if type_result['status'] != 'succeeded':
            raise BenchmarkFailure('executor type conversion failed')
        current, revision = retyped, 'type'

        interrupted = add_index(current, 'large_events_priority_idx', 'priority')
        interruption_result = await interrupted_executor_recovery(
            args,
            source_dsn,
            'benchmark-source',
            revision,
            'interrupted_index',
            current,
            interrupted,
        )
        report['operations']['interrupted_execution_recovery'] = interruption_result
        if interruption_result['status'] == 'succeeded' or (
            interruption_result['status'] == 'skipped'
            and interruption_result['completed_result']['status'] == 'succeeded'
        ):
            current, revision = interrupted, 'interrupted_index'
        elif interruption_result['status'] != 'skipped':
            raise BenchmarkFailure('interrupted executor recovery failed')

        locked = add_column(current, 'lock_probe', 'integer')
        lock_result = await executor_lock_timeout_recovery(
            args, source_dsn, 'benchmark-source', revision, 'lock', current, locked
        )
        report['operations']['lock_timeout_and_recovery'] = lock_result
        if lock_result['status'] != 'succeeded':
            raise BenchmarkFailure('executor lock timeout recovery failed')
        current = locked

        async with await psycopg.AsyncConnection.connect(source_dsn) as conn:
            final_snapshot, final_unsupported = await catalog_snapshot(conn, current)
            report['catalog_after_execution'] = {'unsupported': final_unsupported}
            if final_unsupported:
                raise BenchmarkFailure(
                    f'executor result has unsupported objects: {final_unsupported}'
                )
            if schema.fingerprint(final_snapshot) != schema.fingerprint(current):
                raise BenchmarkFailure(
                    'executor result snapshot does not match the final planned snapshot'
                )
            report['sizes_after_operations'] = await database_sizes(conn)
        report['status'] = 'succeeded'
    except Exception as error:
        report['status'] = 'failed'
        report['fatal_error'] = error_detail(error)
        report['fatal_traceback'] = traceback.format_exc()
    finally:
        report['finished_at_epoch_seconds'] = time.time()
        if not args.keep_database:
            for database in (branch_database, args.database):
                try:
                    await drop_database(args.admin_dsn, database)
                except Exception as error:
                    report.setdefault('cleanup_errors', {})[database] = error_detail(error)
            report['databases_removed'] = not bool(report.get('cleanup_errors'))


def main() -> None:
    args = parse_args()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        'status': 'running',
        'target_bytes': args.target_bytes,
        'payload_bytes': args.payload_bytes,
        'schema_samples': args.schema_samples,
        'operation_timeout_seconds': args.operation_timeout_seconds,
        'started_at_epoch_seconds': time.time(),
        'hardware': hardware_details(),
        'operations': {},
    }
    asyncio.run(run_benchmark(args, report))
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
    print(f'Wrote benchmark report to {args.report}')
    if report['status'] != 'succeeded':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
