"""Application workflows that join metadata storage to the schema engine."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import psycopg
from psycopg import conninfo, sql

from . import catalog, diff, executor, merge, planner, schema
from .config import Settings
from .storage import Conflict, Storage

EMPTY_SNAPSHOT: dict[str, list[Any]] = {'tables': []}


class Service:
    def __init__(self, storage: Storage, settings: Settings) -> None:
        self.storage = storage
        self.settings = settings

    def validate_schema_name(self, value: str) -> str:
        value = value.strip()
        if not value or '\x00' in value or len(value.encode('utf-8')) > 63:
            raise ValueError('Schema name must be a PostgreSQL identifier of at most 63 bytes')
        if value in {'_proteus', 'pg_catalog', 'information_schema'} or value.startswith('pg_'):
            raise ValueError('That schema is reserved by PostgreSQL or Proteus')
        return value

    @staticmethod
    def validate_label(value: str, label: str, maximum: int = 63) -> str:
        value = value.strip()
        if not value or '\x00' in value or len(value.encode('utf-8')) > maximum:
            raise ValueError(f'{label} must be between 1 and {maximum} bytes')
        return value

    async def test_connection(self, dsn: str, schema_name: str) -> dict[str, Any]:
        if self.settings.mode == 'demo':
            raise Conflict('External connections are disabled in demo mode')
        self.validate_schema_name(schema_name)
        try:
            async with await psycopg.AsyncConnection.connect(
                dsn, connect_timeout=self.settings.connection_timeout_seconds
            ) as conn:
                result = await self._inspect_target(conn, schema_name)
                async with conn.cursor() as cursor:
                    await cursor.execute(
                        """SELECT nspname FROM pg_namespace
                        WHERE nspname !~ '^pg_' AND nspname <> 'information_schema'
                        ORDER BY nspname"""
                    )
                    schemas = [row[0] async for row in cursor]
        except Exception as error:
            raise ValueError(_safe_error(error)) from None
        return {
            'ok': not result['unsupported'],
            'server_version': f'{result["server_version"] // 10000}.{result["server_version"] % 10000}',
            'schemas': schemas,
            'unsupported': result['unsupported'],
        }

    async def queue_import(self, workspace_id: str, name: str, dsn: str, schema_name: str) -> str:
        if self.settings.mode == 'demo':
            raise Conflict('External connections are disabled in demo mode')
        name = self.validate_label(name, 'Project name')
        schema_name = self.validate_schema_name(schema_name)
        if not await self.storage.project_capacity_available(workspace_id):
            raise Conflict('Project limit reached for this workspace')
        job = await self.storage.create_job(
            workspace_id,
            'import',
            {'name': name, 'schema_name': schema_name, 'dsn': self.storage.seal(dsn)},
        )
        return str(job['id'])

    async def queue_demo(self, workspace_id: str, name: str) -> str:
        name = self.validate_label(name, 'Project name')
        if not await self.storage.project_capacity_available(workspace_id):
            raise Conflict('Project limit reached for this workspace')
        job = await self.storage.create_job(
            workspace_id, 'demo', {'name': name, 'schema_name': 'public'}
        )
        return str(job['id'])

    async def preview_draft(self, workspace_id: str, branch_id: str) -> dict[str, Any]:
        branch, draft = await self.storage.draft_for_branch(workspace_id, branch_id)
        base = await self.storage.revision_snapshot(str(branch['head_revision']))
        try:
            snapshot = schema.validate_snapshot(draft['snapshot'])
            changes = diff.diff_snapshots(base, snapshot)
            plan = planner.build_plan(base, snapshot, branch['schema_name'])
        except ValueError as error:
            raise ValueError(str(error)) from None
        return {'changes': changes, 'steps': plan['steps'], 'warnings': plan['warnings']}

    async def queue_commit(
        self, workspace_id: str, branch_id: str, message: str, version: int, request_id: str
    ) -> str:
        message = self.validate_label(message, 'Commit message', 500)
        existing = await self.storage.idempotent_revision_job(
            workspace_id,
            request_id,
            branch_id,
            'commit',
            {'message': message, 'draft_version': version},
        )
        if existing is not None:
            return str(existing)
        branch, draft = await self.storage.draft_for_branch(workspace_id, branch_id)
        if branch['is_main']:
            raise Conflict('Create a feature branch before changing main')
        if draft['version'] != version:
            raise Conflict('Draft version is stale')
        if str(draft['base_revision']) != str(branch['head_revision']):
            raise Conflict('Draft base revision is stale')
        try:
            snapshot = schema.validate_snapshot(draft['snapshot'])
            before = await self.storage.revision_snapshot(str(branch['head_revision']))
            plan = planner.build_plan(before, snapshot, branch['schema_name'])
        except ValueError as error:
            raise ValueError(str(error)) from None
        payload = {
            'message': message,
            'snapshot': snapshot,
            'snapshot_checksum': schema.fingerprint(snapshot),
            'plan': plan,
            'base_revision': str(branch['head_revision']),
            'parents': [str(branch['head_revision'])],
            'draft_version': version,
        }
        job = await self.storage.queue_revision_job(
            workspace_id, branch_id, 'commit', payload, request_id
        )
        return str(job['id'])

    async def merge_preview(
        self, workspace_id: str, source_branch_id: str, resolutions: dict[str, str] | None
    ) -> dict[str, Any]:
        source = await self.storage.get_branch(workspace_id, source_branch_id)
        if source['is_main']:
            raise Conflict('Main cannot be merged into itself')
        project = await self.storage.get_project(workspace_id, str(source['project_id']))
        details = await self.storage.get_project_details(workspace_id, str(project['id']))
        target = next(branch for branch in details['branches'] if branch['is_main'])
        base = await self.storage.revision_snapshot(str(source['base_revision']))
        source_snapshot = await self.storage.revision_snapshot(str(source['head_revision']))
        target_snapshot = await self.storage.revision_snapshot(str(target['head_revision']))
        try:
            merged = merge.merge_snapshots(base, source_snapshot, target_snapshot, resolutions)
            changes = diff.diff_snapshots(target_snapshot, merged['snapshot'])
            plan = planner.build_plan(target_snapshot, merged['snapshot'], project['schema_name'])
        except ValueError as error:
            raise ValueError(str(error)) from None
        return {
            'base_revision': source['base_revision'],
            'source_revision': source['head_revision'],
            'target_revision': target['head_revision'],
            'snapshot': merged['snapshot'],
            'changes': changes,
            'conflicts': merged['conflicts'],
            'steps': plan['steps'],
            'warnings': plan['warnings'],
        }

    async def queue_merge(
        self,
        workspace_id: str,
        source_branch_id: str,
        message: str,
        source_revision: str,
        target_revision: str,
        resolutions: dict[str, str],
        request_id: str,
    ) -> str:
        message = self.validate_label(message, 'Merge message', 500)
        existing = await self.storage.idempotent_revision_job(
            workspace_id,
            request_id,
            source_branch_id,
            'merge',
            {
                'message': message,
                'source_revision': source_revision,
                'target_revision': target_revision,
                'resolutions': resolutions,
            },
        )
        if existing is not None:
            return str(existing)
        source = await self.storage.get_branch(workspace_id, source_branch_id)
        if source['is_main']:
            raise Conflict('Main cannot be merged into itself')
        project_id = str(source['project_id'])
        details = await self.storage.get_project_details(workspace_id, project_id)
        target = next(branch for branch in details['branches'] if branch['is_main'])
        if (
            str(source['head_revision']) != source_revision
            or str(target['head_revision']) != target_revision
        ):
            raise Conflict('Source or target revision is stale. Create a new merge preview.')
        preview = await self.merge_preview(workspace_id, source_branch_id, resolutions)
        if any(conflict['resolution'] is None for conflict in preview['conflicts']):
            raise Conflict('Resolve every merge conflict before applying the merge')
        payload = {
            'message': message,
            'snapshot': preview['snapshot'],
            'snapshot_checksum': schema.fingerprint(preview['snapshot']),
            'plan': {'steps': preview['steps'], 'warnings': preview['warnings']},
            'base_revision': str(source['base_revision']),
            'source_revision': source_revision,
            'target_revision': target_revision,
            'parents': [target_revision, source_revision],
            'source_branch_id': source_branch_id,
            'resolutions': resolutions,
        }
        job = await self.storage.queue_revision_job(
            workspace_id,
            str(target['id']),
            'merge',
            payload,
            request_id,
            source_branch_id=source_branch_id,
        )
        return str(job['id'])

    def sandbox_database_details(self, job_id: str) -> tuple[str, str]:
        database_name = f'proteus_{job_id.replace("-", "")}'
        params = conninfo.conninfo_to_dict(self.settings.sandbox_url)
        params['dbname'] = database_name
        return database_name, conninfo.make_conninfo(**params)

    async def create_sandbox_database(self, job_id: str) -> tuple[str, str]:
        database_name, database_dsn = self.sandbox_database_details(job_id)
        async with await psycopg.AsyncConnection.connect(
            self.settings.sandbox_url, connect_timeout=self.settings.connection_timeout_seconds
        ) as conn:
            await conn.set_autocommit(True)
            async with conn.cursor() as cursor:
                try:
                    await cursor.execute(
                        sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database_name))
                    )
                except psycopg.errors.DuplicateDatabase:
                    # This deterministic name may be reached after a worker restart.
                    pass
        return database_name, database_dsn

    async def load_demo_fixture(self, dsn: str) -> None:
        candidates = (
            Path('/app/fixtures/demo.sql'),
            Path(__file__).parents[2] / 'fixtures' / 'demo.sql',
        )
        fixture = next((path for path in candidates if path.is_file()), None)
        if fixture is None:
            raise RuntimeError('Demo fixture is not available')
        async with await psycopg.AsyncConnection.connect(
            dsn, connect_timeout=self.settings.connection_timeout_seconds
        ) as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(fixture.read_text())

    async def demo_fixture_loaded(self, dsn: str) -> bool:
        async with await psycopg.AsyncConnection.connect(
            dsn, connect_timeout=self.settings.connection_timeout_seconds
        ) as conn:
            async with conn.cursor() as cursor:
                await cursor.execute("SELECT to_regclass('public.customers') IS NOT NULL")
                row = await cursor.fetchone()
                return bool(row[0])

    async def tracking_initialized(self, dsn: str, environment_id: str) -> bool:
        try:
            async with await psycopg.AsyncConnection.connect(
                dsn, connect_timeout=self.settings.connection_timeout_seconds
            ) as conn:
                async with conn.cursor() as cursor:
                    await cursor.execute(
                        'SELECT environment_id FROM "_proteus".state WHERE singleton'
                    )
                    row = await cursor.fetchone()
                    return row is not None and row[0] == str(environment_id)
        except psycopg.errors.UndefinedTable:
            return False

    async def introspect(self, dsn: str, schema_name: str) -> dict[str, Any]:
        async with await psycopg.AsyncConnection.connect(
            dsn, connect_timeout=self.settings.connection_timeout_seconds
        ) as conn:
            return await self._inspect_target(conn, schema_name)

    async def _inspect_target(self, conn: Any, schema_name: str) -> dict[str, Any]:
        result = await catalog.introspect(conn, schema_name)
        if result['unsupported']:
            return result
        cursor = await conn.execute(
            "SELECT has_database_privilege(current_database(), 'CREATE'), "
            "has_schema_privilege(%s, 'USAGE') AND has_schema_privilege(%s, 'CREATE')",
            (schema_name, schema_name),
        )
        can_track, can_edit = await cursor.fetchone()
        if not can_track or not can_edit:
            result['unsupported'].append(
                {
                    'object': 'access',
                    'name': schema_name,
                    'reason': 'The role needs database CREATE for tracking, plus schema USAGE and CREATE.',
                }
            )
        cursor = await conn.execute(
            'SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace '
            "WHERE n.nspname = %s AND c.relkind = 'r' AND NOT pg_has_role(c.relowner, 'USAGE')",
            (schema_name,),
        )
        for row in await cursor.fetchall():
            result['unsupported'].append(
                {
                    'object': 'access',
                    'name': row[0],
                    'reason': 'The connected role needs owner access to change this table.',
                }
            )
        return result

    async def initialize_tracking(
        self, dsn: str, environment_id: str, revision_id: str, snapshot: dict[str, Any]
    ) -> None:
        async with await psycopg.AsyncConnection.connect(
            dsn, connect_timeout=self.settings.connection_timeout_seconds
        ) as conn:
            await executor.initialize_tracking(
                conn, str(environment_id), str(revision_id), snapshot
            )

    async def execute(
        self,
        dsn: str,
        schema_name: str,
        environment_id: str,
        job: dict[str, Any],
        payload: dict[str, Any],
        progress: Any,
    ) -> dict[str, Any]:
        from_revision = payload.get('target_revision') or payload.get('base_revision')
        return await executor.execute_plan(
            self._with_timeout(dsn),
            schema_name,
            str(environment_id),
            str(job['id']),
            from_revision,
            payload['candidate_revision'],
            payload['snapshot'],
            payload['plan'],
            progress,
        )

    def _with_timeout(self, dsn: str) -> str:
        parameters = conninfo.conninfo_to_dict(dsn)
        parameters.setdefault('connect_timeout', str(self.settings.connection_timeout_seconds))
        return conninfo.make_conninfo(**parameters)


def _safe_error(error: Exception) -> str:
    if isinstance(error, psycopg.OperationalError):
        return 'Database connection failed'
    if isinstance(error, psycopg.Error):
        return f'Database operation failed ({error.sqlstate or "unknown SQLSTATE"})'
    if isinstance(error, ValueError):
        return str(error)[:1000]
    return 'Job failed unexpectedly'
