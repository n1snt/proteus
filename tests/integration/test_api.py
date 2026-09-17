"""Real PostgreSQL coverage for the durable API workflow."""

from __future__ import annotations

import asyncio
import copy
import os
import uuid

import httpx
import psycopg
import pytest
from proteus.config import Settings
from proteus.main import create_app
from psycopg import conninfo, sql


async def _available(settings: Settings) -> bool:
    try:
        async with await psycopg.AsyncConnection.connect(settings.database_url, connect_timeout=1):
            async with await psycopg.AsyncConnection.connect(
                settings.sandbox_url, connect_timeout=1
            ):
                return True
    except psycopg.OperationalError:
        return False


@pytest.fixture
async def api_settings():
    settings = Settings(
        database_url=os.environ.get(
            'PROTEUS_DATABASE_URL', 'postgresql://postgres:proteus_local@localhost:55432/proteus'
        ),
        sandbox_url=os.environ.get(
            'PROTEUS_SANDBOX_URL', 'postgresql://postgres:proteus_local@localhost:55433/postgres'
        ),
        mode='local',
        static_dir=None,
    )
    if not await _available(settings):
        pytest.skip('metadata and sandbox PostgreSQL services are not available')
    admin_dsn = settings.database_url
    database_name = 'proteus_api_test_' + uuid.uuid4().hex
    async with await psycopg.AsyncConnection.connect(admin_dsn, autocommit=True) as conn:
        await conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database_name)))
    parameters = conninfo.conninfo_to_dict(admin_dsn)
    parameters['dbname'] = database_name
    settings.database_url = conninfo.make_conninfo(**parameters)
    try:
        yield settings
    finally:
        owned_databases = set()
        async with await psycopg.AsyncConnection.connect(settings.database_url) as conn:
            exists = await (
                await conn.execute("SELECT to_regclass('public.environments')")
            ).fetchone()
            if exists[0]:
                rows = await (
                    await conn.execute('SELECT database_name FROM environments WHERE is_sandbox')
                ).fetchall()
                owned_databases.update(row[0] for row in rows if row[0])
                jobs = await (
                    await conn.execute(
                        "SELECT id FROM jobs WHERE kind = 'demo' AND payload ? 'records'"
                    )
                ).fetchall()
                owned_databases.update('proteus_' + str(row[0]).replace('-', '') for row in jobs)
        async with await psycopg.AsyncConnection.connect(
            settings.sandbox_url, autocommit=True
        ) as conn:
            for name in owned_databases:
                await conn.execute(
                    sql.SQL('DROP DATABASE IF EXISTS {} WITH (FORCE)').format(sql.Identifier(name))
                )
        async with await psycopg.AsyncConnection.connect(admin_dsn, autocommit=True) as conn:
            await conn.execute(
                sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(database_name))
            )


async def _wait_for_job(client: httpx.AsyncClient, job_id: str) -> dict[str, object]:
    for _ in range(100):
        response = await client.get(f'/api/jobs/{job_id}')
        response.raise_for_status()
        job = response.json()
        if job['status'] not in {'queued', 'running'}:
            return job
        await asyncio.sleep(0.1)
    pytest.fail(f'job {job_id} did not finish')


async def _create_demo_project(client: httpx.AsyncClient, headers: dict[str, str]) -> str:
    queued = await client.post(
        '/api/projects/demo', json={'name': f'api_{uuid.uuid4().hex[:12]}'}, headers=headers
    )
    assert queued.status_code == 202
    job = await _wait_for_job(client, queued.json()['job_id'])
    assert job['status'] == 'succeeded', job.get('error')
    return job['result']['project_id']


async def _create_branch(
    client: httpx.AsyncClient, project_id: str, name: str, headers: dict[str, str]
) -> str:
    queued = await client.post(
        f'/api/projects/{project_id}/branches', json={'name': name}, headers=headers
    )
    assert queued.status_code == 202
    job = await _wait_for_job(client, queued.json()['job_id'])
    assert job['status'] == 'succeeded', job.get('error')
    return job['result']['branch_id']


async def _rename_customer_name(
    client: httpx.AsyncClient, branch_id: str, name: str, headers: dict[str, str]
) -> str:
    view = (await client.get(f'/api/branches/{branch_id}')).json()
    snapshot = copy.deepcopy(view['draft']['snapshot'])
    customers = next(table for table in snapshot['tables'] if table['name'] == 'customers')
    next(column for column in customers['columns'] if column['name'] == 'name')['name'] = name
    saved = await client.put(
        f'/api/branches/{branch_id}/draft',
        json={
            'snapshot': snapshot,
            'base_revision': view['draft']['base_revision'],
            'version': view['draft']['version'],
        },
        headers=headers,
    )
    assert saved.status_code == 200
    queued = await client.post(
        f'/api/branches/{branch_id}/commit',
        json={
            'message': f'Rename customer name to {name}',
            'version': saved.json()['version'],
            'request_id': str(uuid.uuid4()),
        },
        headers=headers,
    )
    job = await _wait_for_job(client, queued.json()['job_id'])
    assert job['status'] == 'succeeded', job.get('error')
    return job['result']['revision_id']


@pytest.mark.integration
async def test_demo_branch_draft_and_commit_are_persisted(api_settings: Settings) -> None:
    app = create_app(api_settings)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    headers = {'origin': 'http://testserver'}
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url='http://testserver') as client:
            session = await client.get('/api/session')
            assert session.status_code == 200
            repeated_session = await client.get('/api/session')
            assert repeated_session.status_code == 200
            assert repeated_session.json() == session.json()

            name = f'api_{uuid.uuid4().hex[:12]}'
            queued = await client.post('/api/projects/demo', json={'name': name}, headers=headers)
            assert queued.status_code == 202
            project_job = await _wait_for_job(client, queued.json()['job_id'])
            assert project_job['status'] == 'succeeded', project_job.get('error')
            project_id = project_job['result']['project_id']

            branch_request = await client.post(
                f'/api/projects/{project_id}/branches',
                json={'name': 'feature_api'},
                headers=headers,
            )
            assert branch_request.status_code == 202
            branch_job = await _wait_for_job(client, branch_request.json()['job_id'])
            assert branch_job['status'] == 'succeeded', branch_job.get('error')
            branch_id = branch_job['result']['branch_id']

            branch = await client.get(f'/api/branches/{branch_id}')
            assert branch.status_code == 200
            view = branch.json()
            snapshot = copy.deepcopy(view['draft']['snapshot'])
            invoice = next(table for table in snapshot['tables'] if table['name'] == 'invoices')
            next(column for column in invoice['columns'] if column['name'] == 'amount_cents')[
                'name'
            ] = 'amount_due_cents'
            snapshot['tables'][0]['columns'].append(
                {
                    'id': str(uuid.uuid4()),
                    'name': 'api_note',
                    'data_type': 'text',
                    'nullable': True,
                    'default': None,
                    'identity': None,
                }
            )
            saved = await client.put(
                f'/api/branches/{branch_id}/draft',
                json={
                    'snapshot': snapshot,
                    'base_revision': view['draft']['base_revision'],
                    'version': view['draft']['version'],
                },
                headers=headers,
            )
            assert saved.status_code == 200
            preview = await client.post(f'/api/branches/{branch_id}/preview', headers=headers)
            assert preview.status_code == 200
            assert preview.json()['steps']

            request_id = f'commit-{uuid.uuid4()}'
            commit = await client.post(
                f'/api/branches/{branch_id}/commit',
                json={
                    'message': 'Add API note',
                    'version': saved.json()['version'],
                    'request_id': request_id,
                },
                headers=headers,
            )
            assert commit.status_code == 202
            duplicate = await client.post(
                f'/api/branches/{branch_id}/commit',
                json={
                    'message': 'Add API note',
                    'version': saved.json()['version'],
                    'request_id': request_id,
                },
                headers=headers,
            )
            assert duplicate.json() == commit.json()
            committed = await _wait_for_job(client, commit.json()['job_id'])
            assert committed['status'] == 'succeeded', committed.get('error')
            replay = await client.post(
                f'/api/branches/{branch_id}/commit',
                json={
                    'message': 'Add API note',
                    'version': saved.json()['version'],
                    'request_id': request_id,
                },
                headers=headers,
            )
            assert replay.status_code == 202
            assert replay.json() == commit.json()

            final = await client.get(f'/api/branches/{branch_id}')
            assert final.json()['branch']['head_revision'] == committed['result']['revision_id']
            async with httpx.AsyncClient(
                transport=transport, base_url='http://testserver'
            ) as stranger:
                await stranger.get('/api/session')
                assert (await stranger.get(f'/api/branches/{branch_id}')).status_code == 404


@pytest.mark.integration
async def test_second_divergent_branch_merges_after_resolving_an_advanced_main_conflict(
    api_settings: Settings,
) -> None:
    app = create_app(api_settings)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    headers = {'origin': 'http://testserver'}
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url='http://testserver') as client:
            assert (await client.get('/api/session')).status_code == 200
            project_id = await _create_demo_project(client, headers)
            left = await _create_branch(client, project_id, 'feature_left', headers)
            right = await _create_branch(client, project_id, 'feature_right', headers)
            left_revision = await _rename_customer_name(client, left, 'left_name', headers)
            right_revision = await _rename_customer_name(client, right, 'right_name', headers)

            first_preview = (
                await client.post(f'/api/branches/{left}/merge-preview', json={}, headers=headers)
            ).json()
            first_merge = await client.post(
                f'/api/branches/{left}/merge',
                json={
                    'message': 'Merge left rename',
                    'source_revision': left_revision,
                    'target_revision': first_preview['target_revision'],
                    'resolutions': {},
                    'request_id': str(uuid.uuid4()),
                },
                headers=headers,
            )
            first_job = await _wait_for_job(client, first_merge.json()['job_id'])
            assert first_job['status'] == 'succeeded', first_job.get('error')

            second_preview_response = await client.post(
                f'/api/branches/{right}/merge-preview', json={}, headers=headers
            )
            assert second_preview_response.status_code == 200
            second_preview = second_preview_response.json()
            resolutions = {conflict['id']: 'source' for conflict in second_preview['conflicts']}
            assert resolutions
            second_merge = await client.post(
                f'/api/branches/{right}/merge',
                json={
                    'message': 'Merge resolved right rename',
                    'source_revision': right_revision,
                    'target_revision': second_preview['target_revision'],
                    'resolutions': resolutions,
                    'request_id': str(uuid.uuid4()),
                },
                headers=headers,
            )
            assert second_merge.status_code == 202
            second_job = await _wait_for_job(client, second_merge.json()['job_id'])
            assert second_job['status'] == 'succeeded', second_job.get('error')

            details = (await client.get(f'/api/projects/{project_id}')).json()
            main = next(branch for branch in details['branches'] if branch['is_main'])
            main_snapshot = (await client.get(f'/api/branches/{main["id"]}')).json()['snapshot']
            customers = next(
                table for table in main_snapshot['tables'] if table['name'] == 'customers'
            )
            assert any(column['name'] == 'right_name' for column in customers['columns'])


@pytest.mark.integration
async def test_target_commit_recovers_after_metadata_publication_fails(
    api_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_app(api_settings)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    headers = {'origin': 'http://testserver'}
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url='http://testserver') as client:
            await client.get('/api/session')
            project_id = await _create_demo_project(client, headers)
            branch_id = await _create_branch(client, project_id, 'publication-recovery', headers)
            view = (await client.get(f'/api/branches/{branch_id}')).json()
            snapshot = copy.deepcopy(view['draft']['snapshot'])
            customers = next(table for table in snapshot['tables'] if table['name'] == 'customers')
            next(column for column in customers['columns'] if column['name'] == 'name')['name'] = (
                'legal_name'
            )
            saved = await client.put(
                f'/api/branches/{branch_id}/draft',
                json={
                    'snapshot': snapshot,
                    'base_revision': view['draft']['base_revision'],
                    'version': 0,
                },
                headers=headers,
            )
            assert saved.status_code == 200
            original = app.state.storage.complete_revision

            async def publication_failure(*args, **kwargs):
                raise RuntimeError('Simulated metadata connection failure after target commit')

            monkeypatch.setattr(app.state.storage, 'complete_revision', publication_failure)
            queued = await client.post(
                f'/api/branches/{branch_id}/commit',
                json={
                    'message': 'Rename with interrupted metadata publication',
                    'version': saved.json()['version'],
                    'request_id': str(uuid.uuid4()),
                },
                headers=headers,
            )
            assert queued.status_code == 202
            job_id = queued.json()['job_id']
            interrupted = await _wait_for_job(client, job_id)
            assert interrupted['status'] == 'needs_attention'
            blocked = (await client.get(f'/api/branches/{branch_id}')).json()
            assert blocked['branch']['status'] == 'needs_attention'
            assert blocked['branch']['head_revision'] == view['branch']['head_revision']

            monkeypatch.setattr(app.state.storage, 'complete_revision', original)
            retry = await client.post(f'/api/jobs/{job_id}/retry', headers=headers)
            assert retry.status_code == 202
            assert retry.json()['job_id'] == job_id
            recovered = await _wait_for_job(client, job_id)
            assert recovered['status'] == 'succeeded', recovered.get('error')
            final = (await client.get(f'/api/branches/{branch_id}')).json()
            assert final['branch']['head_revision'] == recovered['result']['revision_id']
            assert final['branch']['status'] == 'ready'
            table = next(
                item for item in final['snapshot']['tables'] if item['name'] == 'customers'
            )
            assert any(column['name'] == 'legal_name' for column in table['columns'])
