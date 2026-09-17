# Architecture

Status: implementation specification for the approved direction. The app is not
built yet. Database behavior and recovery claims must be verified by tests.

See [requirements](requirements.md), [compatibility](compatibility.md), and the
[decision log](../decisions.md) for scope and reasoning.

## System shape

Use one Python application with clear internal modules. FastAPI serves the API;
React and TypeScript provide the browser UI. Psycopg 3 handles PostgreSQL access.

    Browser
      |
    HTTPS entry point
      |
    Application: static frontend + API + bounded job worker
      |                    |                         |
    Metadata database    Sandbox PostgreSQL       Connected target
                         |                       (private deployments)
                         Demo and branch databases

Docker Compose runs the app, metadata PostgreSQL, sandbox PostgreSQL, and a
reverse proxy for hosted HTTPS. Main is a demo database or a connected private
database. Sandbox and target use PostgreSQL 17 for the first release.

### Deployment modes

- Self-hosted is the primary mode. The app, metadata, and sandbox run on the user's
  machine or server. The target can be an existing database reachable from that
  deployment; it does not need to move into Proteus's Compose setup.
- The hosted demo runs the same application image on E2E Networks with isolated
  sample workspaces, expiry, and resource limits. It serves the review workflow.
- Local users can also start with sample data. Self-hosted core workflows need no
  demo-service connection, hosted account, or E2E-specific API.
- Use configuration for the mode and connection policy. Enforce demo restrictions
  in the backend as well as the UI. Avoid separate implementations of core behavior.

Publish the local web interface on the host's loopback address by default. Explain
container-to-container and container-to-host database connections in setup docs.
The HTTPS reverse proxy is needed for the public demo, not the local quickstart.

### Process model

Start with one API process and one job worker loop. Each job owns its connections.
Do not accidentally create multiple worker loops by increasing API process count.
Use asynchronous database I/O at the edges and ordinary functions for schema,
diff, and merge logic. Database operations run in PostgreSQL, not on fetched rows.

## Module boundaries

| Module | Owns |
| --- | --- |
| 'api' | Request validation, access checks, and response models |
| 'connections' | Connection settings, secrets, access tests, and capabilities |
| 'catalog' | Catalog reads and unsupported-object detection |
| 'schema' | Typed schema objects, stable identities, and structural validation |
| 'versioning' | Immutable revisions, parent links, branches, and drafts |
| 'diff' | Semantic comparison between snapshots |
| 'merge' | Three-way merge and conflict resolutions |
| 'planner' | Ordered operations, SQL, transaction phases, and impact labels |
| 'executor' | Target locks, database execution, receipts, and verification |
| 'jobs' | Durable queue, progress, and recovery scheduling |
| 'storage' | Explicit SQL for application metadata |

Use Pydantic at API boundaries and typed dataclasses or enums for core models.
The merge engine accepts snapshots and returns a candidate schema and conflicts.
It does not open a connection or update a branch. Avoid an ORM in the schema
engine and keep database transaction ownership visible.

## Data and authority

The metadata database owns project history and planned work. Each target owns
the record of what execution actually committed there. There is no shared
transaction across the metadata and target databases.

### Central records

| Record | Main contents |
| --- | --- |
| Workspace | Access scope, demo expiry, and project membership |
| Project | Connection reference, tracked schema, and main branch |
| Environment | Target identity, branch database location, and operational state |
| Branch | Name, environment, current head, base revision, and lifecycle state |
| Revision | Unique ID, parent IDs, snapshot, change intent, message, and checksum |
| Draft | Branch, base revision, structured operations, and edit version |
| Plan | Pinned heads, resolved result, ordered steps, impact, and checksum |
| Job | Plan reference, request key, progress, outcome, and worker ownership |

Use unique revision IDs and parent links rather than a global migration number.
A normal revision has one parent. A merge has the previous main head and source
head as parents. Store a candidate revision before execution so a target receipt
can always refer to it; show it as committed history only after success.

### Target records

Reserve the '_proteus' schema and exclude it from tracked application objects.

- 'state': tracked environment ID, last completed revision, expected structural
  checksum, last completed execution, and active execution if any.
- 'migrations': execution ID, starting and intended revisions, plan checksum,
  state, timestamps, and error details.
- 'migration_steps': ordered step IDs, operation checksums, states, and outcome
  details needed for recovery.

These records do not detect manual DDL. They express Proteus's own history and
execution state. Before new work, reconcile any unfinished execution rather than
trusting only the last completed revision marker.

## Baseline import and branches

1. Test connectivity, PostgreSQL version, and required privileges.
2. Read the selected application schema and check support and dependencies.
3. Require unsupported objects or dependencies to be resolved before import.
4. Assign stable IDs, save the immutable baseline, and initialize target tracking.
5. Publish the main branch after target initialization succeeds.

If target tracking already exists, do not silently overwrite it. Reconnect to its
known project or report the mismatch. The import assumes no concurrent external
schema changes. Ordinary data writes can continue.

A feature branch starts from a committed main revision. Provision an empty
sandbox database, create supported objects in dependency order, verify them, and
initialize fresh tracking records at that revision. Do not clone the source rows,
sequence counters, or target execution journal.

Separate sandbox provisioning credentials from normal execution credentials.
The connected main database does not need database-creation privileges. Branch
provisioning is a durable job: use a recorded environment ID to identify a database
after a crash and clean up only resources known to belong to that job.

After a successful merge, mark the source branch merged and read-only. Start new
work from current main. This keeps the initial history model simple. Preserve its
snapshots even if its physical database is removed later. A demo expiry job must
not remove a database with active work.

## Canonical schema and object identity

Model tables, columns, types and type parameters, defaults, nullability,
constraints, indexes, and dependencies. A rename changes a name, not an object ID.
Store supported expressions as structured values with references to column IDs.
Do not infer arbitrary expressions or renames using string replacement.

Catalog normalization must be deterministic. Normalize type aliases and sort
unordered collections. Preserve meaningful key order in composite indexes and
constraints. Exclude database OIDs, row counts, physical column positions, and
current sequence counters from semantic comparisons. Treat constraint-owned
indexes as part of their constraints, not duplicate independent changes.

Keep lineage identity separate from the structural checksum used to verify a
database. Different environments have different catalog IDs. Resolve names and
supported definitions against the expected model when verifying our own changes.

## Draft to revision

1. Save structured edits against the current branch head and draft edit version.
2. Compute the candidate snapshot without changing the database.
3. Validate dependencies, display the diff, and build a plan.
4. On apply, require a message and bind the job to this exact draft version.
5. Persist the candidate revision, immutable plan, and job in one metadata
   transaction. An idempotency key makes identical repeated apply requests return
   that job. Reject reuse of the key with a different request body.
6. Execute and verify the change in the branch database.
7. Advance the head once, mark the revision committed, and clear the applied draft.

Reject stale draft writes rather than overwriting newer edits. Freeze the submitted
draft while its job is active. A failed job keeps the draft and its explanation.
An incomplete multi-phase job blocks new work until recovery completes.

## Three-way merge

Inputs are the shared base, source head, and main head. The first version uses a
unique merge base; feature branches are created from main and merged only once.

For each tracked object and property:

- Source equals base: keep the target value.
- Target equals base: take the source value.
- Both sides have the same change: keep one result.
- Both sides change it differently: emit a conflict.

Then validate the whole result. Compatible individual properties can still form
an invalid schema, such as a new default that does not fit a new column type.

| Source | Target | Result |
| --- | --- | --- |
| Add one column | Add a different column | Combine |
| Rename a column | Change its nullability | Combine if valid |
| Rename a column | Rename it differently | Conflict |
| Drop a column | Change that column | Conflict |
| Add a foreign key | Drop its referenced table | Dependency conflict |
| Change a type | Change its default | Check the combined definition |
| Add a named object | Add an equivalent named object | Reconcile identity and keep one definition |

Independently added objects have different IDs. Match equivalent additions by
scope, name, and supported definition, then remap their dependent references.
Same-name additions with different definitions are conflicts.

Resolutions refer to object IDs and pinned revisions. Recompute and validate the
candidate after every resolution. Never accept a whole object from one side if
that silently removes compatible changes from the other side.

When accepting a merge job, check both heads and reserve the source branch and
target environment in one metadata transaction. Block source commits and competing
target jobs while the merge is active. Release reservations only after success,
complete rollback, or recovery. A partial outcome retains the reservation.

The main migration is the difference between current main and the resolved result.
It is not a blind replay of source SQL. A change working on an empty branch says
nothing about whether existing main rows satisfy the resulting schema.

## Planning and execution

A plan includes expected revisions, candidate revision, ordered steps, SQL,
transaction groups, preconditions, verification rules, and impact classifications.
Quote identifiers through Psycopg composition and bind values where supported.
Generate expression SQL only from supported structured models.

Use dependency order: create referenced objects before dependents and remove or
replace dependents before their referenced objects. Cyclic foreign keys can be
added after the tables exist. Include implicit dependency effects in the preview.

### Transactional changes

1. Hold a target-scoped advisory lock on the execution connection.
2. Reconcile unfinished target work and check the expected starting revision.
3. Set bounded lock waiting and an operation-appropriate statement time limit.
4. Run the DDL and verify its supported schema result in the transaction.
5. Write the target receipt and revision update in that same transaction.
6. Commit, then publish central success and the new branch head.

A target failure rolls back that phase and its revision update. Record the error
after rollback. A crash after target commit is recovered from the receipt, not by
rerunning the migration. A lost connection during commit has an unknown outcome
until the worker reconnects and reads the target record.

### Multi-phase changes

Concurrent index builds cannot run in a transaction block. Persist the plan and
mark the execution active, then run these steps on a dedicated autocommit
connection. Hold the target advisory lock on the same connection across phases.
Transactional steps commit their receipts with their changes.

After interruption, a missing completion receipt for a non-transactional step
does not prove failure. Inspect the specific object, definition, and validity.
For an invalid index, use a documented cleanup/retry strategy only if the index
is known to belong to this operation. Never drop an unrelated same-name object.

Keep the last completed revision until every phase succeeds. Show the active
execution alongside it: the physical schema can contain partial changes. Advance
the revision only after full verification. Block other target changes while
repair is needed. Schema history cannot undo already deleted row values.

### Worker and API lifecycle

The API returns an accepted job ID after persisting the request. A small worker
claims persisted work, opens its own connections, and records progress. Poll job
status from the browser while it is active. FastAPI request background tasks are
not the durable job store.

Use a database claim to prevent duplicate job ownership and the target advisory
lock to prevent overlapping execution. A timed-out worker lease alone is not
permission to rerun DDL: another connection can still be executing it. Recovery
must acquire the target lock and reconcile actual receipts first.

Do not hold an open metadata transaction during a long target operation. Avoid
automatic DDL retries after connection errors. Use explicit retry rules per step.
On shutdown, stop accepting new work and close connections carefully. Session
locks must be released before returning any reusable connection to a pool.

Job outcomes distinguish 'succeeded', 'failed', and 'needs_attention'. Progress
stages include 'checking', 'applying', 'validating', 'verifying', and 'recovering'.
Keep lock waiting and database progress as step details. Do not invent completion
percentages where PostgreSQL does not provide reliable progress.

## HTTP boundaries

Group endpoints around connections, projects, branches, drafts, comparisons,
merge plans, jobs, and history. Generate the OpenAPI description from typed API
models and use it to keep frontend request types aligned.

Return useful field errors for invalid edits, a conflict response for stale
versions, and an accepted response with a job ID for durable work. Scope access
checks to the workspace on every request. Do not expose raw credentials in errors,
API responses, logs, or browser storage.

The hosted demo creates a scoped session and isolated sample database. Demo users
can access only their own resources and cannot supply arbitrary remote targets.
Private deployments use an operator-configured access mechanism and can manage
their own database connections. Persist secrets encrypted with a deployment key
using an established library; keep that key out of the database and repository.

## Verification boundaries

- Unit tests cover pure schema, diff, merge, and plan behavior.
- PostgreSQL integration tests verify supported DDL and actual failure outcomes.
- Crash tests cover both transactional receipts and non-transactional gaps.
- Browser tests cover one complete clean merge and one resolved conflict.
- Benchmarks use the physical-size and workload requirements in the product spec.

External DDL detection is not part of these checks. Post-execution introspection
verifies Proteus's own work; it does not import or reconcile outside changes.

## Reference behavior

- [Psycopg transactions](https://www.psycopg.org/psycopg3/docs/basic/transactions.html)
- [FastAPI lifespan](https://fastapi.tiangolo.com/advanced/events/)
- [PostgreSQL 17 ALTER TABLE](https://www.postgresql.org/docs/17/sql-altertable.html)
- [PostgreSQL 17 CREATE INDEX](https://www.postgresql.org/docs/17/sql-createindex.html)
