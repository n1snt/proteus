"""FastAPI entry point for the session-scoped Proteus API."""

import asyncio
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware

from . import __version__, schema
from .config import Settings, get_settings
from .jobs import JobWorker
from .service import Service
from .storage import Conflict, IdempotencyConflict, NotFound, Storage

COOKIE_NAME = 'proteus_session'


class DemoProjectRequest(BaseModel):
    name: str = 'Demo'


class ConnectionRequest(BaseModel):
    dsn: str = Field(min_length=1, max_length=4096)
    schema_name: str = Field(default='public', alias='schema')


class ProjectRequest(ConnectionRequest):
    name: str = Field(min_length=1, max_length=63)


class BranchRequest(BaseModel):
    name: str = Field(min_length=1, max_length=63)


class DraftRequest(BaseModel):
    snapshot: dict[str, Any]
    base_revision: str
    version: int = Field(ge=0)


class CommitRequest(BaseModel):
    message: str = Field(min_length=1, max_length=500)
    version: int = Field(ge=0)
    request_id: str = Field(min_length=1, max_length=200)


class MergePreviewRequest(BaseModel):
    resolutions: dict[str, str] | None = None


class MergeRequest(BaseModel):
    message: str = Field(min_length=1, max_length=500)
    source_revision: str
    target_revision: str
    resolutions: dict[str, str] = Field(default_factory=dict)
    request_id: str = Field(min_length=1, max_length=200)


class SameOriginMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Any) -> Response:
        if request.url.path.startswith('/api/') and request.method not in {
            'GET',
            'HEAD',
            'OPTIONS',
        }:
            origin = request.headers.get('origin')
            expected = f'{request.url.scheme}://{request.headers.get("host", "")}'
            if origin != expected:
                return JSONResponse(
                    {'detail': 'Request must use this site as its origin'}, status_code=403
                )
        return await call_next(request)


def create_app(settings: Settings | None = None) -> FastAPI:
    configured = settings or get_settings()
    storage = Storage(configured)
    service = Service(storage, configured)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await storage.migrate()
        worker = JobWorker(storage, service)
        task = asyncio.create_task(worker.run(), name='proteus-job-worker')
        app.state.storage = storage
        app.state.service = service
        app.state.worker = worker
        try:
            yield
        finally:
            worker.stop()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    app = FastAPI(title='Proteus API', version=__version__, lifespan=lifespan)
    app.add_middleware(SameOriginMiddleware)

    @app.exception_handler(NotFound)
    async def not_found(_: Request, error: NotFound) -> JSONResponse:
        return JSONResponse({'detail': str(error)}, status_code=404)

    @app.exception_handler(IdempotencyConflict)
    async def idempotency_conflict(_: Request, error: IdempotencyConflict) -> JSONResponse:
        return JSONResponse({'detail': str(error)}, status_code=409)

    @app.exception_handler(Conflict)
    async def conflict(_: Request, error: Conflict) -> JSONResponse:
        return JSONResponse({'detail': str(error)}, status_code=409)

    @app.exception_handler(ValueError)
    async def invalid(_: Request, error: ValueError) -> JSONResponse:
        return JSONResponse({'detail': str(error)}, status_code=400)

    @app.exception_handler(RequestValidationError)
    async def validation(_: Request, error: RequestValidationError) -> JSONResponse:
        first = error.errors()[0]
        location = '.'.join(str(part) for part in first['loc'] if part != 'body')
        detail = first['msg'] if not location else f'{location}: {first["msg"]}'
        return JSONResponse({'detail': detail}, status_code=422)

    @app.exception_handler(Exception)
    async def unexpected(_: Request, __: Exception) -> JSONResponse:
        return JSONResponse({'detail': 'Internal server error'}, status_code=500)

    async def workspace(request: Request) -> dict[str, Any]:
        token = request.cookies.get(COOKIE_NAME)
        if not token:
            raise HTTPException(
                status_code=401, detail='A session is required. Call GET /api/session first.'
            )
        session = await storage.session_for_token(token)
        if session is None:
            raise HTTPException(
                status_code=401, detail='Session expired. Call GET /api/session to start again.'
            )
        return session

    Workspace = Annotated[dict[str, Any], Depends(workspace)]

    @app.get('/api/health')
    async def health() -> dict[str, str]:
        return {'status': 'ok'}

    @app.get('/api/session')
    async def session(request: Request) -> Response:
        token = request.cookies.get(COOKIE_NAME)
        existing = await storage.session_for_token(token) if token else None
        if existing:
            return JSONResponse(
                {
                    'workspace_id': str(existing['workspace_id']),
                    'mode': existing['mode'],
                    'version': __version__,
                }
            )
        token, created = await storage.create_session()
        response = JSONResponse(
            {
                'workspace_id': str(created['workspace_id']),
                'mode': created['mode'],
                'version': __version__,
            }
        )
        response.set_cookie(
            COOKIE_NAME,
            token,
            httponly=True,
            samesite='lax',
            secure=configured.cookie_secure,
            path='/',
            max_age=configured.demo_ttl_hours * 3600
            if configured.mode == 'demo'
            else 30 * 24 * 3600,
        )
        return response

    @app.get('/api/projects')
    async def projects(current: Workspace) -> list[dict[str, Any]]:
        return await storage.list_projects(str(current['workspace_id']))

    @app.post('/api/projects/demo', status_code=202)
    async def create_demo(body: DemoProjectRequest, current: Workspace) -> dict[str, str]:
        job_id = await service.queue_demo(str(current['workspace_id']), body.name)
        return {'job_id': job_id}

    @app.post('/api/connections/test')
    async def test_connection(body: ConnectionRequest, _: Workspace) -> dict[str, Any]:
        return await service.test_connection(body.dsn, body.schema_name)

    @app.post('/api/projects', status_code=202)
    async def create_project(body: ProjectRequest, current: Workspace) -> dict[str, str]:
        job_id = await service.queue_import(
            str(current['workspace_id']), body.name, body.dsn, body.schema_name
        )
        return {'job_id': job_id}

    @app.get('/api/projects/{project_id}')
    async def project(project_id: str, current: Workspace) -> dict[str, Any]:
        return await storage.get_project_details(str(current['workspace_id']), project_id)

    @app.post('/api/projects/{project_id}/branches', status_code=202)
    async def create_branch(
        project_id: str, body: BranchRequest, current: Workspace
    ) -> dict[str, str]:
        await storage.get_project(str(current['workspace_id']), project_id)
        job = await storage.queue_branch_job(
            str(current['workspace_id']),
            project_id,
            service.validate_label(body.name, 'Branch name'),
        )
        return {'job_id': str(job['id'])}

    @app.get('/api/branches/{branch_id}')
    async def branch(branch_id: str, current: Workspace) -> dict[str, Any]:
        return await storage.branch_view(str(current['workspace_id']), branch_id)

    @app.put('/api/branches/{branch_id}/draft')
    async def save_draft(branch_id: str, body: DraftRequest, current: Workspace) -> dict[str, Any]:
        _, previous = await storage.draft_for_branch(str(current['workspace_id']), branch_id)
        snapshot = schema.validate_snapshot(
            schema.rebind_unchanged_checks(previous['snapshot'], body.snapshot)
        )
        return await storage.save_draft(
            str(current['workspace_id']), branch_id, snapshot, body.base_revision, body.version
        )

    @app.delete('/api/branches/{branch_id}/draft')
    async def delete_draft(branch_id: str, current: Workspace) -> dict[str, bool]:
        await storage.delete_draft(str(current['workspace_id']), branch_id)
        return {'ok': True}

    @app.post('/api/branches/{branch_id}/preview')
    async def preview(branch_id: str, current: Workspace) -> dict[str, Any]:
        return await service.preview_draft(str(current['workspace_id']), branch_id)

    @app.post('/api/branches/{branch_id}/commit', status_code=202)
    async def commit(branch_id: str, body: CommitRequest, current: Workspace) -> dict[str, str]:
        job_id = await service.queue_commit(
            str(current['workspace_id']), branch_id, body.message, body.version, body.request_id
        )
        return {'job_id': job_id}

    @app.post('/api/branches/{branch_id}/merge-preview')
    async def merge_preview(
        branch_id: str, body: MergePreviewRequest, current: Workspace
    ) -> dict[str, Any]:
        return await service.merge_preview(
            str(current['workspace_id']), branch_id, body.resolutions
        )

    @app.post('/api/branches/{branch_id}/merge', status_code=202)
    async def merge(branch_id: str, body: MergeRequest, current: Workspace) -> dict[str, str]:
        job_id = await service.queue_merge(
            str(current['workspace_id']),
            branch_id,
            body.message,
            body.source_revision,
            body.target_revision,
            body.resolutions,
            body.request_id,
        )
        return {'job_id': job_id}

    @app.get('/api/projects/{project_id}/history')
    async def history(project_id: str, current: Workspace) -> list[dict[str, Any]]:
        return await storage.history(str(current['workspace_id']), project_id)

    @app.get('/api/jobs/{job_id}')
    async def job(job_id: str, current: Workspace) -> dict[str, Any]:
        record = await storage.get_job(str(current['workspace_id']), job_id)
        record.pop('payload', None)
        return record

    @app.post('/api/jobs/{job_id}/retry', status_code=202)
    async def retry(job_id: str, current: Workspace) -> dict[str, str]:
        retried = await storage.retry_job(str(current['workspace_id']), job_id)
        return {'job_id': str(retried['id'])}

    @app.api_route('/api/{path:path}', methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'])
    async def api_not_found(path: str) -> JSONResponse:
        return JSONResponse({'detail': 'API endpoint not found'}, status_code=404)

    static_dir = configured.static_dir
    if static_dir and static_dir.is_dir():
        index = static_dir / 'index.html'

        @app.get('/{path:path}', include_in_schema=False)
        async def frontend(path: str) -> Response:
            candidate = static_dir / path
            if (
                path
                and candidate.is_file()
                and candidate.resolve().is_relative_to(static_dir.resolve())
            ):
                return FileResponse(candidate)
            if index.is_file():
                return FileResponse(index)
            return JSONResponse({'detail': 'Frontend is not built'}, status_code=404)

    return app


app = create_app()
