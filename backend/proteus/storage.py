"""Explicit PostgreSQL storage for workspaces, history, and durable jobs."""

from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json

from .config import Settings


class StorageError(Exception):
    """Expected storage failure that can be exposed as a safe API error."""


class NotFound(StorageError):
    pass


class Conflict(StorageError):
    pass


class IdempotencyConflict(Conflict):
    pass


def new_id() -> str:
    return str(uuid.uuid4())


def _uuid(value: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError, AttributeError):
        raise NotFound('Resource not found') from None


def canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def _json(value: Any) -> Json:
    return Json(value)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class Storage:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def connection(self) -> psycopg.AsyncConnection[dict[str, Any]]:
        return await psycopg.AsyncConnection.connect(
            self.settings.database_url,
            row_factory=dict_row,
            connect_timeout=self.settings.connection_timeout_seconds,
        )

    async def migrate(self) -> None:
        migration = Path(__file__).parent.parent / 'migrations' / '001_metadata.sql'
        async with await self.connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended('proteus-migrations', 0))"
                )
                await cursor.execute(migration.read_text())

    async def create_session(self) -> tuple[str, dict[str, Any]]:
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode('utf-8')).hexdigest()
        workspace_id = new_id()
        session_id = new_id()
        expiry = (
            _utc_now() + timedelta(hours=self.settings.demo_ttl_hours)
            if self.settings.mode == 'demo'
            else None
        )
        async with await self.connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    """INSERT INTO workspaces (id, mode, expires_at)
                    VALUES (%s, %s, %s)""",
                    (workspace_id, self.settings.mode, expiry),
                )
                await cursor.execute(
                    """INSERT INTO sessions (id, workspace_id, token_hash, expires_at)
                    VALUES (%s, %s, %s, %s)""",
                    (session_id, workspace_id, token_hash, expiry),
                )
        return token, {'workspace_id': workspace_id, 'mode': self.settings.mode}

    async def session_for_token(self, token: str) -> dict[str, Any] | None:
        token_hash = hashlib.sha256(token.encode('utf-8')).hexdigest()
        async with await self.connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    """SELECT s.workspace_id, w.mode
                    FROM sessions s JOIN workspaces w ON w.id = s.workspace_id
                    WHERE s.token_hash = %s
                      AND (s.expires_at IS NULL OR s.expires_at > now())
                      AND (w.expires_at IS NULL OR w.expires_at > now())""",
                    (token_hash,),
                )
                session = await cursor.fetchone()
                if session is None:
                    return None
                await cursor.execute(
                    'UPDATE sessions SET last_seen_at = now() WHERE token_hash = %s', (token_hash,)
                )
                await cursor.execute(
                    'UPDATE workspaces SET last_seen_at = now() WHERE id = %s',
                    (session['workspace_id'],),
                )
                return session

    async def cleanup_expired_workspaces(self) -> list[dict[str, Any]]:
        """Return sandbox resources that are safe for the worker to remove."""
        async with await self.connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    """SELECT e.id, w.id AS workspace_id, e.database_name, e.encrypted_dsn
                    FROM environments e
                    JOIN projects p ON p.id = e.project_id
                    JOIN workspaces w ON w.id = p.workspace_id
                    WHERE w.expires_at <= now()
                      AND e.is_sandbox
                      AND NOT EXISTS (
                          SELECT 1 FROM jobs j
                          WHERE j.workspace_id = w.id AND j.status IN ('queued', 'running')
                      )"""
                )
                resources = list(await cursor.fetchall())
                return resources

    async def delete_expired_workspace(self, workspace_id: str) -> None:
        async with await self.connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    """DELETE FROM workspaces w WHERE w.id = %s AND w.expires_at <= now()
                    AND NOT EXISTS (
                        SELECT 1 FROM jobs j
                        WHERE j.workspace_id = w.id AND j.status IN ('queued', 'running')
                    )""",
                    (workspace_id,),
                )

    async def list_projects(self, workspace_id: str) -> list[dict[str, Any]]:
        async with await self.connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    """SELECT id, name, schema_name, is_demo, created_at
                    FROM projects WHERE workspace_id = %s ORDER BY created_at DESC""",
                    (workspace_id,),
                )
                return list(await cursor.fetchall())

    async def project_capacity_available(self, workspace_id: str) -> bool:
        async with await self.connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    """SELECT
                        (SELECT count(*) FROM projects WHERE workspace_id = %s)
                        + (SELECT count(*) FROM jobs WHERE workspace_id = %s
                           AND project_id IS NULL AND (
                               (kind = 'import' AND status IN ('queued', 'running'))
                               OR (kind = 'demo' AND (status IN ('queued', 'running') OR payload ? 'records'))
                           )) AS count""",
                    (workspace_id, workspace_id),
                )
                return (await cursor.fetchone())['count'] < self.settings.max_projects

    async def get_project(self, workspace_id: str, project_id: str) -> dict[str, Any]:
        project_id = _uuid(project_id)
        async with await self.connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    """SELECT id, name, schema_name, is_demo, created_at
                    FROM projects WHERE id = %s AND workspace_id = %s""",
                    (project_id, workspace_id),
                )
                project = await cursor.fetchone()
        if project is None:
            raise NotFound('Project not found')
        return project

    async def project_exists(self, project_id: str) -> bool:
        async with await self.connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute('SELECT 1 FROM projects WHERE id = %s', (project_id,))
                return await cursor.fetchone() is not None

    async def get_project_details(self, workspace_id: str, project_id: str) -> dict[str, Any]:
        project = await self.get_project(workspace_id, project_id)
        async with await self.connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    """SELECT id, project_id, name, is_main, head_revision, base_revision, status, created_at
                    FROM branches WHERE project_id = %s ORDER BY is_main DESC, created_at""",
                    (project_id,),
                )
                branches = list(await cursor.fetchall())
                await cursor.execute(
                    """SELECT id, kind, status, stage, steps, error, project_id, branch_id,
                              created_at, updated_at, result
                    FROM jobs WHERE workspace_id = %s AND project_id = %s
                    ORDER BY created_at DESC LIMIT 50""",
                    (workspace_id, project_id),
                )
                jobs = list(await cursor.fetchall())
        return {'project': project, 'branches': branches, 'jobs': jobs}

    async def get_branch(self, workspace_id: str, branch_id: str) -> dict[str, Any]:
        branch_id = _uuid(branch_id)
        async with await self.connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    """SELECT b.*, p.workspace_id, p.schema_name, p.is_demo, e.encrypted_dsn,
                              e.database_name, e.is_sandbox
                    FROM branches b
                    JOIN projects p ON p.id = b.project_id
                    JOIN environments e ON e.id = b.environment_id
                    WHERE b.id = %s AND p.workspace_id = %s""",
                    (branch_id, workspace_id),
                )
                branch = await cursor.fetchone()
        if branch is None:
            raise NotFound('Branch not found')
        return branch

    async def branch_view(self, workspace_id: str, branch_id: str) -> dict[str, Any]:
        branch_id = _uuid(branch_id)
        branch = await self.get_branch(workspace_id, branch_id)
        async with await self.connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    'SELECT snapshot FROM revisions WHERE id = %s', (branch['head_revision'],)
                )
                revision = await cursor.fetchone()
                await cursor.execute(
                    """SELECT snapshot, base_revision, version, updated_at
                    FROM drafts WHERE branch_id = %s""",
                    (branch_id,),
                )
                draft = await cursor.fetchone()
        public_branch = _branch_public(branch)
        snapshot = revision['snapshot']
        if draft is None:
            draft = {
                'snapshot': snapshot,
                'base_revision': branch['head_revision'],
                'version': 0,
                'updated_at': branch['created_at'],
            }
        return {'branch': public_branch, 'snapshot': snapshot, 'draft': draft}

    async def save_draft(
        self,
        workspace_id: str,
        branch_id: str,
        snapshot: dict[str, Any],
        base_revision: str,
        version: int,
    ) -> dict[str, Any]:
        branch_id = _uuid(branch_id)
        async with await self.connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    """SELECT b.head_revision, b.status, b.is_main
                    FROM branches b JOIN projects p ON p.id = b.project_id
                    WHERE b.id = %s AND p.workspace_id = %s FOR UPDATE""",
                    (branch_id, workspace_id),
                )
                branch = await cursor.fetchone()
                if branch is None:
                    raise NotFound('Branch not found')
                if branch['is_main']:
                    raise Conflict('Create a feature branch before editing main')
                if branch['status'] != 'ready':
                    raise Conflict('Branch is not available for draft edits')
                if str(branch['head_revision']) != base_revision:
                    raise Conflict('Draft base revision is stale')
                await cursor.execute(
                    """SELECT version FROM drafts WHERE branch_id = %s FOR UPDATE""", (branch_id,)
                )
                current = await cursor.fetchone()
                expected = current['version'] if current else 0
                if version != expected:
                    raise Conflict('Draft version is stale')
                next_version = expected + 1
                await cursor.execute(
                    """INSERT INTO drafts (branch_id, snapshot, base_revision, version)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (branch_id) DO UPDATE SET snapshot = EXCLUDED.snapshot,
                        base_revision = EXCLUDED.base_revision, version = EXCLUDED.version,
                        updated_at = now()
                    RETURNING snapshot, base_revision, version, updated_at""",
                    (branch_id, _json(snapshot), base_revision, next_version),
                )
                return await cursor.fetchone()

    async def delete_draft(self, workspace_id: str, branch_id: str) -> None:
        branch_id = _uuid(branch_id)
        branch = await self.get_branch(workspace_id, branch_id)
        if branch['is_main'] or branch['status'] != 'ready':
            raise Conflict('Branch is not available for draft edits')
        async with await self.connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute('DELETE FROM drafts WHERE branch_id = %s', (branch_id,))

    async def draft_for_branch(
        self, workspace_id: str, branch_id: str
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        branch_id = _uuid(branch_id)
        branch = await self.get_branch(workspace_id, branch_id)
        async with await self.connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    'SELECT snapshot FROM revisions WHERE id = %s', (branch['head_revision'],)
                )
                head = await cursor.fetchone()
                await cursor.execute('SELECT * FROM drafts WHERE branch_id = %s', (branch_id,))
                draft = await cursor.fetchone()
        if draft is None:
            draft = {
                'branch_id': branch_id,
                'snapshot': head['snapshot'],
                'base_revision': branch['head_revision'],
                'version': 0,
            }
        return branch, draft

    async def history(self, workspace_id: str, project_id: str) -> list[dict[str, Any]]:
        project_id = _uuid(project_id)
        await self.get_project(workspace_id, project_id)
        async with await self.connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    """SELECT id, branch_id, message, parents, created_at, kind
                    FROM revisions WHERE project_id = %s AND status = 'committed'
                    ORDER BY created_at DESC""",
                    (project_id,),
                )
                return list(await cursor.fetchall())

    async def create_job(
        self,
        workspace_id: str,
        kind: str,
        payload: dict[str, Any],
        *,
        project_id: str | None = None,
        branch_id: str | None = None,
        request_id: str | None = None,
        plan_id: str | None = None,
    ) -> dict[str, Any]:
        payload_hash = canonical_hash(payload)
        async with await self.connection() as conn:
            async with conn.cursor() as cursor:
                if kind in {'import', 'demo'}:
                    await cursor.execute(
                        'SELECT id FROM workspaces WHERE id = %s FOR UPDATE', (workspace_id,)
                    )
                    if await cursor.fetchone() is None:
                        raise NotFound('Workspace not found')
                    await cursor.execute(
                        """SELECT
                            (SELECT count(*) FROM projects WHERE workspace_id = %s)
                            + (SELECT count(*) FROM jobs WHERE workspace_id = %s
                               AND project_id IS NULL AND (
                                   (kind = 'import' AND status IN ('queued', 'running'))
                                   OR (kind = 'demo' AND (status IN ('queued', 'running') OR payload ? 'records'))
                               )) AS count""",
                        (workspace_id, workspace_id),
                    )
                    if (await cursor.fetchone())['count'] >= self.settings.max_projects:
                        raise Conflict('Project limit reached for this workspace')
                if request_id:
                    await cursor.execute(
                        """SELECT id, payload_hash FROM jobs
                        WHERE workspace_id = %s AND request_id = %s""",
                        (workspace_id, request_id),
                    )
                    old = await cursor.fetchone()
                    if old:
                        if old['payload_hash'] != payload_hash:
                            raise IdempotencyConflict(
                                'Request ID was reused with a different payload'
                            )
                        return {'id': old['id'], 'existing': True}
                job_id = new_id()
                await cursor.execute(
                    """INSERT INTO jobs
                    (id, workspace_id, project_id, branch_id, kind, payload, payload_hash, request_id, plan_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (
                        job_id,
                        workspace_id,
                        project_id,
                        branch_id,
                        kind,
                        _json(payload),
                        payload_hash,
                        request_id,
                        plan_id,
                    ),
                )
                return {'id': job_id, 'existing': False}

    async def queue_branch_job(
        self, workspace_id: str, project_id: str, name: str
    ) -> dict[str, Any]:
        async with await self.connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    """SELECT b.id, b.head_revision, b.status FROM branches b
                    JOIN projects p ON p.id = b.project_id
                    WHERE b.project_id = %s AND b.is_main AND p.workspace_id = %s FOR UPDATE""",
                    (project_id, workspace_id),
                )
                main = await cursor.fetchone()
                if main is None:
                    raise NotFound('Project not found')
                if main['status'] != 'ready':
                    raise Conflict('Main branch is busy')
                await cursor.execute(
                    """SELECT count(*) AS count FROM branches WHERE project_id = %s""",
                    (project_id,),
                )
                count = (await cursor.fetchone())['count']
                await cursor.execute(
                    """SELECT count(*) AS count FROM jobs WHERE project_id = %s AND kind = 'branch'
                    AND status IN ('queued', 'running')""",
                    (project_id,),
                )
                if count + (await cursor.fetchone())['count'] >= self.settings.max_branches:
                    raise Conflict('Branch limit reached for this project')
                await cursor.execute(
                    'SELECT 1 FROM branches WHERE project_id = %s AND name = %s', (project_id, name)
                )
                if await cursor.fetchone():
                    raise Conflict('A branch with this name already exists')
                job_id = new_id()
                payload = {'name': name, 'base_revision': str(main['head_revision'])}
                await cursor.execute(
                    """INSERT INTO jobs
                    (id, workspace_id, project_id, branch_id, kind, payload, payload_hash)
                    VALUES (%s, %s, %s, %s, 'branch', %s, %s)""",
                    (
                        job_id,
                        workspace_id,
                        project_id,
                        main['id'],
                        _json(payload),
                        canonical_hash(payload),
                    ),
                )
                return {'id': job_id}

    async def queue_revision_job(
        self,
        workspace_id: str,
        branch_id: str,
        kind: str,
        payload: dict[str, Any],
        request_id: str,
        *,
        source_branch_id: str | None = None,
    ) -> dict[str, Any]:
        branch_id = _uuid(branch_id)
        payload_hash = canonical_hash(payload)
        async with await self.connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    """SELECT id, payload_hash FROM jobs WHERE workspace_id = %s AND request_id = %s""",
                    (workspace_id, request_id),
                )
                existing = await cursor.fetchone()
                if existing:
                    if existing['payload_hash'] != payload_hash:
                        raise IdempotencyConflict('Request ID was reused with a different payload')
                    return {'id': existing['id'], 'existing': True}
                await cursor.execute(
                    """SELECT b.*, p.workspace_id FROM branches b JOIN projects p ON p.id = b.project_id
                    WHERE b.id = %s AND p.workspace_id = %s FOR UPDATE""",
                    (branch_id, workspace_id),
                )
                target = await cursor.fetchone()
                if target is None:
                    raise NotFound('Branch not found')
                if target['status'] != 'ready':
                    raise Conflict('Branch is busy')
                expected_target = payload.get('target_revision') or payload.get('base_revision')
                if str(target['head_revision']) != expected_target:
                    raise Conflict('Branch head is stale')
                if kind == 'commit':
                    await cursor.execute(
                        'SELECT * FROM drafts WHERE branch_id = %s FOR UPDATE', (branch_id,)
                    )
                    draft = await cursor.fetchone()
                    if (
                        draft is None
                        or draft['version'] != payload['draft_version']
                        or str(draft['base_revision']) != payload['base_revision']
                        or canonical_hash(draft['snapshot']) != canonical_hash(payload['snapshot'])
                    ):
                        raise Conflict('Draft changed before commit was accepted')
                if source_branch_id:
                    await cursor.execute(
                        """SELECT b.* FROM branches b JOIN projects p ON p.id = b.project_id
                        WHERE b.id = %s AND p.workspace_id = %s FOR UPDATE""",
                        (source_branch_id, workspace_id),
                    )
                    source = await cursor.fetchone()
                    if source is None:
                        raise NotFound('Source branch not found')
                    if source['status'] != 'ready':
                        raise Conflict('Source branch is busy')
                    if str(source['head_revision']) != payload['source_revision']:
                        raise Conflict('Source branch head is stale')
                    await cursor.execute(
                        "UPDATE branches SET status = 'busy' WHERE id = %s", (source_branch_id,)
                    )
                await cursor.execute(
                    "UPDATE branches SET status = 'busy' WHERE id = %s", (branch_id,)
                )
                job_id = new_id()
                stored_payload = dict(payload)
                plan_id: str | None = None
                if kind in {'commit', 'merge'}:
                    revision_id, plan_id = new_id(), new_id()
                    stored_payload['candidate_revision'] = revision_id
                    await cursor.execute(
                        """INSERT INTO revisions
                        (id, project_id, branch_id, message, parents, snapshot, checksum, kind)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                        (
                            revision_id,
                            target['project_id'],
                            branch_id,
                            payload['message'],
                            _json(payload['parents']),
                            _json(payload['snapshot']),
                            payload['snapshot_checksum'],
                            kind,
                        ),
                    )
                    await cursor.execute(
                        """INSERT INTO plans
                        (id, project_id, branch_id, source_revision, target_revision, candidate_revision, plan, checksum)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                        (
                            plan_id,
                            target['project_id'],
                            branch_id,
                            payload.get('source_revision'),
                            payload.get('target_revision'),
                            revision_id,
                            _json(payload['plan']),
                            canonical_hash(payload['plan']),
                        ),
                    )
                await cursor.execute(
                    """INSERT INTO jobs
                    (id, workspace_id, project_id, branch_id, kind, payload, payload_hash, request_id, plan_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (
                        job_id,
                        workspace_id,
                        target['project_id'],
                        branch_id,
                        kind,
                        _json(stored_payload),
                        payload_hash,
                        request_id,
                        plan_id,
                    ),
                )
                return {'id': job_id, 'existing': False}

    async def idempotent_revision_job(
        self,
        workspace_id: str,
        request_id: str,
        branch_id: str,
        kind: str,
        request: dict[str, Any],
    ) -> str | None:
        async with await self.connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    """SELECT id, kind, branch_id, payload FROM jobs
                    WHERE workspace_id = %s AND request_id = %s""",
                    (workspace_id, request_id),
                )
                job = await cursor.fetchone()
        if job is None:
            return None
        payload = job['payload']
        same_branch = (
            payload.get('source_branch_id') == branch_id
            if kind == 'merge'
            else str(job['branch_id']) == branch_id
        )
        if (
            job['kind'] != kind
            or not same_branch
            or any(payload.get(key) != value for key, value in request.items())
        ):
            raise IdempotencyConflict('Request ID was reused with a different payload')
        return str(job['id'])

    async def get_job(self, workspace_id: str, job_id: str) -> dict[str, Any]:
        job_id = _uuid(job_id)
        async with await self.connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    """SELECT id, kind, status, stage, steps, error, project_id, branch_id, plan_id,
                              created_at, updated_at, result, payload
                    FROM jobs WHERE id = %s AND workspace_id = %s""",
                    (job_id, workspace_id),
                )
                job = await cursor.fetchone()
        if job is None:
            raise NotFound('Job not found')
        return job

    async def retry_job(self, workspace_id: str, job_id: str) -> dict[str, Any]:
        job_id = _uuid(job_id)
        async with await self.connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    'SELECT * FROM jobs WHERE id = %s AND workspace_id = %s FOR UPDATE',
                    (job_id, workspace_id),
                )
                job = await cursor.fetchone()
                if job is None:
                    raise NotFound('Job not found')
                if job['status'] not in {'failed', 'needs_attention'}:
                    raise Conflict('Only failed or interrupted jobs can be retried')
                if job['kind'] in {'commit', 'merge'}:
                    payload = job['payload']
                    expected = {
                        str(job['branch_id']): payload.get('target_revision')
                        or payload['base_revision']
                    }
                    if payload.get('source_branch_id'):
                        expected[payload['source_branch_id']] = payload['source_revision']
                    for branch_id in sorted(expected):
                        await cursor.execute(
                            'SELECT * FROM branches WHERE id = %s FOR UPDATE', (branch_id,)
                        )
                        branch = await cursor.fetchone()
                        if branch['status'] not in {'ready', 'needs_attention'}:
                            raise Conflict('A branch is already busy or merged')
                        allowed = {expected[branch_id]}
                        if branch_id == str(job['branch_id']):
                            allowed.add(payload['candidate_revision'])
                        if str(branch['head_revision']) not in allowed:
                            raise Conflict(
                                'The branch changed. Prepare a new plan instead of retrying.'
                            )
                        await cursor.execute(
                            "UPDATE branches SET status = 'busy' WHERE id = %s", (branch_id,)
                        )
                await cursor.execute(
                    """UPDATE jobs SET status = 'queued', stage = 'recovering', error = NULL,
                    worker_id = NULL, updated_at = now() WHERE id = %s""",
                    (job_id,),
                )
                return {'id': job_id}

    async def provisioned_branch(self, job_id: str) -> dict[str, Any] | None:
        async with await self.connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    """SELECT b.id AS branch_id, e.id AS environment_id, e.database_name, e.encrypted_dsn
                    FROM environments e JOIN branches b ON b.environment_id = e.id
                    WHERE e.provision_job_id = %s""",
                    (job_id,),
                )
                return await cursor.fetchone()

    async def recover_jobs(self) -> None:
        async with await self.connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    """UPDATE jobs SET status = 'queued', stage = 'recovering', worker_id = NULL,
                        claimed_at = NULL, updated_at = now() WHERE status = 'running'"""
                )

    async def acquire_worker(self) -> psycopg.AsyncConnection[dict[str, Any]] | None:
        conn = await self.connection()
        await conn.set_autocommit(True)
        async with conn.cursor() as cursor:
            await cursor.execute(
                "SELECT pg_try_advisory_lock(hashtextextended('proteus-worker', 0)) AS locked"
            )
            if (await cursor.fetchone())['locked']:
                return conn
        await conn.close()
        return None

    async def release_worker(self, conn: psycopg.AsyncConnection[dict[str, Any]]) -> None:
        try:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    "SELECT pg_advisory_unlock(hashtextextended('proteus-worker', 0))"
                )
        finally:
            await conn.close()

    async def claim_job(self, worker_id: str) -> dict[str, Any] | None:
        async with await self.connection() as conn:
            async with conn.transaction():
                async with conn.cursor() as cursor:
                    await cursor.execute(
                        """SELECT id FROM jobs WHERE status = 'queued' ORDER BY created_at
                        FOR UPDATE SKIP LOCKED LIMIT 1"""
                    )
                    row = await cursor.fetchone()
                    if row is None:
                        return None
                    await cursor.execute(
                        """UPDATE jobs SET status = 'running', worker_id = %s, claimed_at = now(),
                            stage = CASE WHEN stage = 'recovering' THEN 'recovering' ELSE 'checking' END,
                            updated_at = now() WHERE id = %s
                        RETURNING *""",
                        (worker_id, row['id']),
                    )
                    return await cursor.fetchone()

    async def update_job(
        self,
        job_id: str,
        *,
        stage: str,
        steps: list[dict[str, Any]] | None = None,
    ) -> None:
        async with await self.connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    """UPDATE jobs SET stage = %s, steps = COALESCE(%s::jsonb, steps), updated_at = now()
                    WHERE id = %s""",
                    (stage, _json(steps) if steps is not None else None, job_id),
                )

    async def update_job_payload(self, job_id: str, payload: dict[str, Any]) -> None:
        async with await self.connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    'UPDATE jobs SET payload = %s, updated_at = now() WHERE id = %s',
                    (_json(payload), job_id),
                )

    async def mark_target_committed(self, job_id: str) -> None:
        await self.update_job(job_id, stage='publishing')

    async def target_commit_recorded(self, job_id: str) -> bool:
        async with await self.connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    "SELECT stage = 'publishing' AS committed FROM jobs WHERE id = %s", (job_id,)
                )
                row = await cursor.fetchone()
                return bool(row and row['committed'])

    async def finish_job(
        self,
        job: dict[str, Any],
        *,
        status: str,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        async with await self.connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    """UPDATE jobs SET status = %s, stage = %s, result = %s, error = %s,
                        updated_at = now() WHERE id = %s""",
                    (
                        status,
                        'complete'
                        if status == 'succeeded'
                        else 'needs_attention'
                        if status == 'needs_attention'
                        else 'failed',
                        _json(result or {}),
                        error,
                        job['id'],
                    ),
                )
                if job['branch_id']:
                    branch_status = 'needs_attention' if status == 'needs_attention' else 'ready'
                    await cursor.execute(
                        'UPDATE branches SET status = %s WHERE id = %s',
                        (branch_status, job['branch_id']),
                    )
                if job['kind'] == 'merge' and job['payload'].get('source_branch_id'):
                    source_status = (
                        'merged'
                        if status == 'succeeded'
                        else 'needs_attention'
                        if status == 'needs_attention'
                        else 'ready'
                    )
                    await cursor.execute(
                        'UPDATE branches SET status = %s WHERE id = %s',
                        (source_status, job['payload']['source_branch_id']),
                    )

    async def create_imported_project(
        self,
        job: dict[str, Any],
        *,
        dsn: str,
        snapshot: dict[str, Any],
        schema_name: str,
        is_demo: bool,
        database_name: str | None,
        checksum: str,
        records: dict[str, str] | None = None,
    ) -> dict[str, str]:
        records = records or {
            'project_id': new_id(),
            'environment_id': new_id(),
            'branch_id': new_id(),
            'revision_id': new_id(),
        }
        project_id = records['project_id']
        environment_id = records['environment_id']
        branch_id = records['branch_id']
        revision_id = records['revision_id']
        async with await self.connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    """INSERT INTO projects (id, workspace_id, name, schema_name, is_demo, encrypted_dsn)
                    VALUES (%s, %s, %s, %s, %s, %s)""",
                    (
                        project_id,
                        job['workspace_id'],
                        job['payload']['name'],
                        schema_name,
                        is_demo,
                        self.settings.fernet.encrypt(dsn.encode('utf-8')),
                    ),
                )
                await cursor.execute(
                    """INSERT INTO environments
                    (id, project_id, database_name, encrypted_dsn, is_sandbox, provision_job_id)
                    VALUES (%s, %s, %s, %s, %s, %s)""",
                    (
                        environment_id,
                        project_id,
                        database_name,
                        self.settings.fernet.encrypt(dsn.encode('utf-8')),
                        is_demo,
                        job['id'],
                    ),
                )
                await cursor.execute(
                    """INSERT INTO branches (id, project_id, environment_id, name, is_main)
                    VALUES (%s, %s, %s, 'main', true)""",
                    (branch_id, project_id, environment_id),
                )
                await cursor.execute(
                    """INSERT INTO revisions
                    (id, project_id, branch_id, message, snapshot, checksum, kind, status)
                    VALUES (%s, %s, %s, 'Baseline import', %s, %s, 'baseline', 'committed')""",
                    (revision_id, project_id, branch_id, _json(snapshot), checksum),
                )
                await cursor.execute(
                    """UPDATE branches SET head_revision = %s, base_revision = %s WHERE id = %s""",
                    (revision_id, revision_id, branch_id),
                )
                await cursor.execute(
                    'UPDATE jobs SET project_id = %s WHERE id = %s', (project_id, job['id'])
                )
        return records

    async def create_branch(
        self,
        job: dict[str, Any],
        dsn: str,
        database_name: str,
    ) -> dict[str, str]:
        environment_id, branch_id = new_id(), new_id()
        payload = job['payload']
        async with await self.connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    """INSERT INTO environments
                    (id, project_id, database_name, encrypted_dsn, is_sandbox, provision_job_id, provision_status)
                    VALUES (%s, %s, %s, %s, true, %s, 'pending')""",
                    (
                        environment_id,
                        job['project_id'],
                        database_name,
                        self.settings.fernet.encrypt(dsn.encode()),
                        job['id'],
                    ),
                )
                await cursor.execute(
                    """INSERT INTO branches
                    (id, project_id, environment_id, name, head_revision, base_revision, status)
                    VALUES (%s, %s, %s, %s, %s, %s, 'busy')""",
                    (
                        branch_id,
                        job['project_id'],
                        environment_id,
                        payload['name'],
                        payload['base_revision'],
                        payload['base_revision'],
                    ),
                )
        return {'branch_id': branch_id, 'environment_id': environment_id}

    async def mark_environment_ready(self, environment_id: str) -> None:
        async with await self.connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    "UPDATE environments SET provision_status = 'ready' WHERE id = %s",
                    (environment_id,),
                )

    async def insert_plan_and_revision(
        self,
        job_id: str,
        branch_id: str,
        *,
        snapshot: dict[str, Any],
        message: str,
        kind: str,
        parents: list[str],
        plan: dict[str, Any],
        source_revision: str | None = None,
        target_revision: str | None = None,
    ) -> str:
        revision_id, plan_id = new_id(), new_id()
        async with await self.connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute('SELECT project_id FROM branches WHERE id = %s', (branch_id,))
                branch = await cursor.fetchone()
                if branch is None:
                    raise NotFound('Branch not found')
                await cursor.execute(
                    """INSERT INTO revisions
                    (id, project_id, branch_id, message, parents, snapshot, checksum, kind)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                    (
                        revision_id,
                        branch['project_id'],
                        branch_id,
                        message,
                        _json(parents),
                        _json(snapshot),
                        canonical_hash(snapshot),
                        kind,
                    ),
                )
                await cursor.execute(
                    """INSERT INTO plans
                    (id, project_id, branch_id, source_revision, target_revision, candidate_revision, plan, checksum)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                    (
                        plan_id,
                        branch['project_id'],
                        branch_id,
                        source_revision,
                        target_revision,
                        revision_id,
                        _json(plan),
                        canonical_hash(plan),
                    ),
                )
                await cursor.execute(
                    'UPDATE jobs SET plan_id = %s WHERE id = %s', (plan_id, job_id)
                )
        return revision_id

    async def complete_revision(
        self, job: dict[str, Any], revision_id: str, draft_version: int | None
    ) -> None:
        async with await self.connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    "UPDATE revisions SET status = 'committed' WHERE id = %s", (revision_id,)
                )
                await cursor.execute(
                    'UPDATE branches SET head_revision = %s WHERE id = %s',
                    (revision_id, job['branch_id']),
                )
                if draft_version is not None:
                    await cursor.execute(
                        'DELETE FROM drafts WHERE branch_id = %s AND version = %s',
                        (job['branch_id'], draft_version),
                    )

    async def revision_is_committed(self, revision_id: str) -> bool:
        async with await self.connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    "SELECT status = 'committed' AS committed FROM revisions WHERE id = %s",
                    (revision_id,),
                )
                row = await cursor.fetchone()
                return bool(row and row['committed'])

    async def set_branch_status(self, branch_id: str, status: str) -> None:
        async with await self.connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    'UPDATE branches SET status = %s WHERE id = %s', (status, branch_id)
                )

    async def fail_revision(self, revision_id: str) -> None:
        async with await self.connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    "UPDATE revisions SET status = 'failed' WHERE id = %s", (revision_id,)
                )

    async def revision_snapshot(self, revision_id: str) -> dict[str, Any]:
        revision_id = _uuid(revision_id)
        async with await self.connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute('SELECT snapshot FROM revisions WHERE id = %s', (revision_id,))
                revision = await cursor.fetchone()
        if revision is None:
            raise NotFound('Revision not found')
        return revision['snapshot']

    async def decrypt_dsn(self, value: bytes) -> str:
        return self.settings.fernet.decrypt(value).decode('utf-8')

    def seal(self, value: str) -> str:
        return self.settings.fernet.encrypt(value.encode('utf-8')).decode('ascii')

    def open(self, value: str) -> str:
        return self.settings.fernet.decrypt(value.encode('ascii')).decode('utf-8')


def _branch_public(branch: dict[str, Any]) -> dict[str, Any]:
    names = (
        'id',
        'project_id',
        'name',
        'is_main',
        'head_revision',
        'base_revision',
        'status',
        'created_at',
    )
    return {name: branch[name] for name in names}
