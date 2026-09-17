# Implementation contract

This contract coordinates the API, UI, and schema engine. Update it when actual
interfaces change. All endpoints are under '/api'. IDs are UUID strings and times
are UTC ISO strings. Errors use a JSON 'detail' string and an appropriate status.

## Schema format

Snapshot: an object with a 'tables' array. Each table has 'id', 'name', 'columns',
'constraints', and 'indexes'. All object IDs remain stable across renames.

- Column: 'id', 'name', 'data_type', 'nullable', 'default', 'identity'. Defaults
  are SQL expression strings or null; identity is 'always', 'by_default', or null.
- Constraint: 'id', 'name', 'kind' ('primary_key', 'unique', 'foreign_key', 'check'),
  'columns' (column IDs), optional 'reference_table' (table ID), 'reference_columns'
  (column IDs), 'on_delete', 'on_update', and 'expression' (SQL string or null).
- Index: 'id', 'name', 'columns' (column IDs), 'unique' (boolean).

Absent optional lists default to empty arrays. SQL expressions are validated by
the engine before planning; the API does not execute unvalidated input.

## HTTP API

All fetches use same-origin session cookies. The backend creates a scoped session
on 'GET /session'. Hosted demo mode rejects external connection requests.

| Method and path | Request | Response |
| --- | --- | --- |
| GET /session | None | {workspace_id, mode, version} |
| GET /health | None | {status} |
| GET /projects | None | Array of projects |
| POST /projects/demo | {name?} | {job_id} |
| POST /connections/test | {dsn, schema?} | {ok, server_version, schemas, unsupported} |
| POST /projects | {name, dsn, schema} | {job_id} |
| GET /projects/{id} | None | {project, branches, jobs} |
| POST /projects/{id}/branches | {name} | {job_id} |
| GET /branches/{id} | None | {branch, snapshot, draft} |
| PUT /branches/{id}/draft | {snapshot, base_revision, version} | Draft |
| DELETE /branches/{id}/draft | None | {ok: true} |
| POST /branches/{id}/preview | None | {changes, steps, warnings} |
| POST /branches/{id}/commit | {message, version, request_id} | {job_id} |
| POST /branches/{id}/merge-preview | {resolutions?} | Merge preview |
| POST /branches/{id}/merge | {message, source_revision, target_revision, resolutions, request_id} | {job_id} |
| GET /projects/{id}/history | None | Array of revisions, newest first |
| GET /jobs/{id} | None | Job |
| POST /jobs/{id}/retry | None | {job_id} |

Project: 'id', 'name', 'schema_name', 'is_demo', 'created_at'.

Branch: 'id', 'project_id', 'name', 'is_main', 'head_revision', 'base_revision',
'status' ('ready', 'busy', 'merged', 'needs_attention'), 'created_at'.

Draft: 'snapshot', 'base_revision', 'version', 'updated_at'. An untouched draft
has version 0 and contains the branch's current snapshot. The UI maintains its
local edits and sends the whole draft snapshot on save.

Revision: 'id', 'branch_id', 'message', 'parents', 'created_at', 'kind'.

Durable-work POST endpoints return HTTP 202 with the job ID.

Job: 'id', 'kind', 'status' ('queued', 'running', 'succeeded', 'failed',
'needs_attention'), 'stage', 'steps', 'error', 'project_id', 'branch_id',
'created_at', 'updated_at', 'result'. Project and branch IDs can be null during
provisioning. Result may include the newly created project and branch IDs; prefer
those for navigation because a branch-creation job starts from the main branch.

Change: 'id', 'kind' ('added', 'removed', 'modified'), 'object_type', 'table_name',
'object_name', 'summary', 'before', 'after'.

Plan step: 'id', 'sql', 'description', 'transactional', 'impact', 'operation'.
Impact is 'metadata', 'scan', 'rewrite', 'destructive', or 'index'.

Merge preview: 'base_revision', 'source_revision', 'target_revision', 'snapshot',
'changes', 'conflicts', 'steps', 'warnings'. Each conflict has 'id', 'object_type',
'object_name', 'property', 'reason', 'base', 'source', 'target', and 'resolution'.
The resolutions object maps conflict IDs to 'source' or 'target'. Unresolved
conflicts prevent execution. Property-level resolutions preserve other changes.

## Python engine interface

The engine uses JSON-compatible dictionaries at module boundaries. Typed helpers
may be used internally. Service code calls these public functions:

- 'schema.validate_snapshot(snapshot)': return normalized snapshot or raise ValueError.
- 'schema.fingerprint(snapshot)': stable structural hash, independent of catalog IDs.
- 'diff.diff_snapshots(before, after)': list of changes.
- 'merge.merge_snapshots(base, source, target, resolutions=None)': object with
  'snapshot' and 'conflicts'.
- 'planner.build_plan(before, after, schema_name, online=True)': object with
  'steps' and 'warnings'.
- 'catalog.introspect(conn, schema_name, reference=None)': async, return
  'snapshot', 'unsupported', and 'server_version'. A reference maps stable IDs
  by the expected object names after our own execution.
- 'executor.initialize_tracking(conn, environment_id, revision_id, snapshot)': async.
- 'executor.execute_plan(dsn, schema_name, environment_id, execution_id,
  from_revision, to_revision, snapshot, plan, progress=None)': async; verify and
  apply with target records. Return {status, error?}. Progress is an async callback
  receiving a stage string and a list of step-status dictionaries.

The API owns sandbox database creation and calls the same plan/executor to build
branch schemas from an empty snapshot. It owns metadata, sessions, jobs, and
central history. The executor owns target DDL, locks, receipts, and recovery.

## Runtime contract

- Python package: 'backend/proteus'; entry point: 'proteus.main:app'.
- Application port: 8000. Vite development port: 5173, proxying '/api' to 8000.
- Frontend output: 'web/dist', copied to '/app/static' in the image.
- Environment: 'PROTEUS_DATABASE_URL', 'PROTEUS_SANDBOX_URL', 'PROTEUS_MODE'
  ('local' or 'demo'), 'PROTEUS_SECRET_KEY', 'PROTEUS_STATIC_DIR'.
- Optional: 'PROTEUS_COOKIE_SECURE', 'PROTEUS_DEMO_TTL_HOURS',
  'PROTEUS_MAX_PROJECTS', 'PROTEUS_MAX_BRANCHES'.
- Local database port mappings: metadata 55432 and sandbox 55433, bound to loopback.
- Backend tooling uses 'uv'; Python 3.12 or newer. Frontend tooling uses npm.
