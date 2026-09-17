"""Bounded durable job worker for provisioning and schema execution."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from typing import Any

import psycopg
from psycopg import sql

from . import planner, schema
from .service import EMPTY_SNAPSHOT, Service, _safe_error
from .storage import Storage

logger = logging.getLogger(__name__)


class JobWorker:
    def __init__(self, storage: Storage, service: Service) -> None:
        self.storage = storage
        self.service = service
        self.worker_id = str(uuid.uuid4())
        self._stopping = asyncio.Event()

    async def run(self) -> None:
        while not self._stopping.is_set():
            try:
                lock = await self.storage.acquire_worker()
            except Exception:
                await asyncio.sleep(0.5)
                continue
            if lock is None:
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._stopping.wait(), timeout=0.5)
                continue
            try:
                await self.storage.recover_jobs()
                while not self._stopping.is_set():
                    try:
                        # A lost session also loses the singleton lock.
                        await lock.execute('SELECT 1')
                        job = await self.storage.claim_job(self.worker_id)
                        if job is None:
                            await self._cleanup_expired()
                            with contextlib.suppress(asyncio.TimeoutError):
                                await asyncio.wait_for(self._stopping.wait(), timeout=0.5)
                            continue
                        await self._run_job(job)
                    except Exception:
                        logger.warning('Worker metadata connection interrupted; reconnecting')
                        await asyncio.sleep(0.5)
                        break
            finally:
                with contextlib.suppress(psycopg.Error):
                    await self.storage.release_worker(lock)

    def stop(self) -> None:
        self._stopping.set()

    async def _cleanup_expired(self) -> None:
        resources = await self.storage.cleanup_expired_workspaces()
        by_workspace: dict[str, list[dict[str, Any]]] = {}
        for resource in resources:
            by_workspace.setdefault(str(resource['workspace_id']), []).append(resource)
        for workspace_id, environments in by_workspace.items():
            deleted = True
            for environment in environments:
                database_name = environment['database_name']
                if not database_name or not database_name.startswith('proteus_'):
                    deleted = False
                    break
                try:
                    async with await psycopg.AsyncConnection.connect(
                        self.service.settings.sandbox_url
                    ) as conn:
                        await conn.set_autocommit(True)
                        async with conn.cursor() as cursor:
                            await cursor.execute(
                                sql.SQL('DROP DATABASE IF EXISTS {}').format(
                                    sql.Identifier(database_name)
                                )
                            )
                except Exception:
                    deleted = False
                    break
            if deleted:
                await self.storage.delete_expired_workspace(workspace_id)

    async def _run_job(self, job: dict[str, Any]) -> None:
        try:
            if job['kind'] in {'import', 'demo'}:
                result = await self._run_import(job)
            elif job['kind'] == 'branch':
                result = await self._run_branch(job)
            elif job['kind'] in {'commit', 'merge'}:
                result = await self._run_revision(job)
            else:
                raise RuntimeError('Unknown job type')
            await self.storage.finish_job(job, status='succeeded', result=result)
        except _JobAlreadyFinished:
            return
        except _TargetCommittedError:
            await self.storage.finish_job(
                job,
                status='needs_attention',
                error='Target changes committed, but metadata publication needs recovery',
            )
            return
        except Exception as error:
            if await self.storage.target_commit_recorded(str(job['id'])):
                await self.storage.finish_job(
                    job, status='needs_attention', error=_safe_error(error)
                )
                return
            candidate = job['payload'].get('candidate_revision')
            if candidate:
                await self.storage.fail_revision(candidate)
            await self.storage.finish_job(job, status='failed', error=_safe_error(error))

    async def _run_import(self, job: dict[str, Any]) -> dict[str, Any]:
        payload = dict(job['payload'])
        records = payload.get('records')
        if records is None:
            records = {
                'project_id': str(uuid.uuid4()),
                'branch_id': str(uuid.uuid4()),
                'revision_id': str(uuid.uuid4()),
                'environment_id': str(uuid.uuid4()),
            }
            payload['records'] = records
            await self.storage.update_job_payload(str(job['id']), payload)
        if job['kind'] == 'demo':
            await self.storage.update_job(str(job['id']), stage='provisioning')
            database_name, dsn = await self.service.create_sandbox_database(str(job['id']))
            if not await self.service.demo_fixture_loaded(dsn):
                await self.service.load_demo_fixture(dsn)
            is_demo = True
        else:
            database_name = None
            dsn = self.storage.open(payload['dsn'])
            is_demo = False
        await self.storage.update_job(str(job['id']), stage='checking')
        inspected = await self.service.introspect(dsn, payload['schema_name'])
        if inspected['unsupported']:
            objects = '; '.join(
                f'{item["object"]} {item["name"]}: {item["reason"]}'
                for item in inspected['unsupported']
            )
            raise ValueError(f'Unsupported objects prevent import: {objects}')
        snapshot = schema.validate_snapshot(inspected['snapshot'])
        await self.storage.update_job(str(job['id']), stage='initializing')
        if not await self.service.tracking_initialized(dsn, records['environment_id']):
            await self.service.initialize_tracking(
                dsn, records['environment_id'], records['revision_id'], snapshot
            )
        if not await self.storage.project_exists(records['project_id']):
            records = await self.storage.create_imported_project(
                job,
                dsn=dsn,
                snapshot=snapshot,
                schema_name=payload['schema_name'],
                is_demo=is_demo,
                database_name=database_name,
                checksum=schema.fingerprint(snapshot),
                records=records,
            )
        return {'project_id': records['project_id'], 'branch_id': records['branch_id']}

    async def _run_branch(self, job: dict[str, Any]) -> dict[str, Any]:
        main = await self.storage.get_branch(str(job['workspace_id']), str(job['branch_id']))
        payload = job['payload']
        # The fork point is immutable, so a newer main head does not invalidate
        # a queued or resumed schema-only branch creation.
        snapshot = await self.storage.revision_snapshot(payload['base_revision'])
        await self.storage.update_job(str(job['id']), stage='provisioning')
        records = await self.storage.provisioned_branch(str(job['id']))
        if records is None:
            database_name, dsn = self.service.sandbox_database_details(str(job['id']))
            records = await self.storage.create_branch(job, dsn, database_name)
        else:
            database_name = records['database_name']
            dsn = await self.storage.decrypt_dsn(records['encrypted_dsn'])
        plan = planner.build_plan(EMPTY_SNAPSHOT, snapshot, main['schema_name'], online=False)
        await self.service.create_sandbox_database(str(job['id']))
        if not await self.service.tracking_initialized(dsn, records['environment_id']):
            await self.storage.update_job(str(job['id']), stage='initializing')
            await self.service.initialize_tracking(
                dsn, records['environment_id'], payload['base_revision'], EMPTY_SNAPSHOT
            )

        async def progress(stage: str, steps: list[dict[str, Any]]) -> None:
            await self.storage.update_job(str(job['id']), stage=stage, steps=steps)

        result = await self.service.execute(
            dsn,
            main['schema_name'],
            records['environment_id'],
            job,
            {
                'base_revision': payload['base_revision'],
                'candidate_revision': payload['base_revision'],
                'snapshot': snapshot,
                'plan': plan,
            },
            progress,
        )
        if result['status'] != 'succeeded':
            raise RuntimeError(result.get('error') or 'Branch schema creation failed')
        await self.storage.set_branch_status(records['branch_id'], 'ready')
        await self.storage.mark_environment_ready(records['environment_id'])
        return {'branch_id': records['branch_id']}

    async def _run_revision(self, job: dict[str, Any]) -> dict[str, Any]:
        branch = await self.storage.get_branch(str(job['workspace_id']), str(job['branch_id']))
        payload = job['payload']
        candidate = payload['candidate_revision']
        if str(branch['head_revision']) == candidate and await self.storage.revision_is_committed(
            candidate
        ):
            return {'revision_id': candidate}
        expected = (
            payload['target_revision'] if job['kind'] == 'merge' else payload['base_revision']
        )
        if str(branch['head_revision']) != expected:
            raise RuntimeError('Branch head changed before execution')
        if job['kind'] == 'merge':
            source = await self.storage.get_branch(
                str(job['workspace_id']), payload['source_branch_id']
            )
            if str(source['head_revision']) != payload['source_revision']:
                raise RuntimeError('Source branch changed before execution')
        dsn = await self.storage.decrypt_dsn(branch['encrypted_dsn'])

        async def progress(stage: str, steps: list[dict[str, Any]]) -> None:
            await self.storage.update_job(str(job['id']), stage=stage, steps=steps)

        result = await self.service.execute(
            dsn,
            branch['schema_name'],
            str(branch['environment_id']),
            job,
            payload,
            progress,
        )
        if result['status'] != 'succeeded':
            if result.get('status') == 'needs_attention':
                await self.storage.finish_job(
                    job,
                    status='needs_attention',
                    error=result.get('error') or 'Database operation needs attention',
                )
                raise _JobAlreadyFinished()
            raise ValueError(result.get('error') or 'Schema execution failed')
        try:
            await self.storage.mark_target_committed(str(job['id']))
            await self.storage.complete_revision(job, candidate, payload.get('draft_version'))
        except Exception as error:
            raise _TargetCommittedError() from error
        return {'revision_id': candidate}


class _JobAlreadyFinished(Exception):
    """Prevent the outer worker from replacing a terminal attention status."""


class _TargetCommittedError(Exception):
    """Target receipts succeeded but the separate metadata publish did not."""
