# Decisions

Proteus is a web app for PostgreSQL schema branching, diffs, and merges, built for
the [Zamp project round](https://app.notion.com/p/zampfinance/Engineering-Project-Round-303edc94e67f805490dafccfc4f6f35d).
The brief requires a deployed web app, real database changes, and smooth operation
with tables holding roughly 5 GB of data.

This log records choices and their tradeoffs. 'Accepted' records an agreed choice,
not completed implementation. 'Proposed' records a design that still needs review.
No application code or benchmarks exist yet.

## D-001: Name the project Proteus

Date: 2026-09-17. Status: Accepted.

**Choice:** Use Proteus, named after the shape-changing figure in Greek mythology.
The connection is schema evolution. Use 'proteus' as the repository folder name.

**Alternatives:** Weft, Splice, Versatus, and Vertumnus.

**Reason:** The owner preferred a mythological name with a stronger sound than
Weft. Proteus fits that preference and the project's purpose.

**Tradeoff and cut:** The name is used by other products. This choice does not
claim unique branding or an available domain. A custom domain is not required.

## D-002: Use Python for the backend

Date: 2026-09-17. Status: Accepted.

**Choice:** Use Python and keep the code easy to explain and change. FastAPI and
Psycopg 3 are the API framework and PostgreSQL driver. They were first proposed
with the Python choice and accepted in the later design approval.

**Alternatives:** Go, which was the first recommendation, and an all-TypeScript
stack.

**Reason:** Development speed and the owner's ability to explain the project are
more useful here than matching the hiring team's backend language. PostgreSQL
will perform index builds, constraint checks, and table rewrites. Python will
work mainly with schema metadata and coordinate execution.

**Tradeoff and cut:** Accept Python's runtime and CPU limits. Do not move whole
tables through application memory or add a second backend language.

**Validation:** Measure schema processing and application memory. The 5 GB table
requirement still needs a real database benchmark.

## D-003: Own schema changes after baseline import

Date: 2026-09-17. Status: Accepted.

**Choice:** Import an existing schema as a baseline. After import, tracked schema
changes must go through Proteus. Normal application data reads and writes remain
supported.

**Alternatives:** Detect external DDL, infer renames, and reconcile those changes
with branch history.

**Reason:** External change handling adds ambiguity and work beyond the core
branch, diff, and merge workflow. Explicit ownership keeps the history model
clear.

**Tradeoff and cut:** Manual DDL or another migration tool can break Proteus's
assumptions. External change detection and reconciliation are out of scope. A
stored revision marker does not prove that the live schema was not edited.

**Validation:** Still verify our own migration results and test execution failures,
concurrent data activity, and recovery after interruption.

## D-004: Record applied revisions in each managed database

Date: 2026-09-17. Status: Accepted.

**Choice:** Keep branch history in Proteus's metadata store and record applied
revisions and migration execution history in each managed database. Reserve an
application-owned schema for these records and exclude it from user schema diffs.

**Alternatives:** Store all execution state centrally, or use only a single
increasing migration version number.

**Reason:** Central history describes which revisions exist. Target-side records
describe what was applied to a database. Revision IDs and parent links support
branching better than a single global sequence number.

**Tradeoff and cut:** Proteus needs permission to store tracking records on each
target. A revision is schema history, not a backup of deleted data.

**Validation:** Test a crash after target commit but before the central status
update. The accepted design commits transactional DDL with its target receipt.
Multi-phase operations need separate progress and recovery checks. The target
records and recovery rules are specified in [architecture](docs/architecture.md)
and still need implementation and tests.

## D-005: Use schema-only branch databases

Date: 2026-09-17. Status: Accepted.

Initially proposed during planning; accepted when the owner approved the design
recommendations. Acceptance does not mean the database behavior has been tested.

**Choice:** Give each branch an isolated PostgreSQL database containing a copy of
the supported schema, without copying source rows. Store immutable snapshots and
explicit changes, with stable object IDs for renames. Use a three-way merge of
the base, source, and target revisions.

**Alternatives:** Copy all data for each branch, keep branches only as metadata,
or compare SQL text without a structured schema model.

**Reason:** Real branch databases allow changes to be executed and checked. Avoiding
row copies keeps branch creation tied mainly to schema size. Object identities
preserve rename intent during comparison and merging.

**Tradeoff and cut:** Empty branches cannot prove that existing target rows satisfy
a new type or constraint. Row versioning and data merges are out of scope.

**Validation:** Review the supported PostgreSQL objects, prototype branch creation,
and test rename conflicts and target data failures. These checks remain pending.

## D-006: Ship a Docker-based web app

Date: 2026-09-17. Status: Accepted.

**Choice:** Containerize the app and its required services. Use Docker Compose for
the multi-service setup and target an E2E Networks node for the hosted deployment.

**Alternatives:** A client-only tool or a CLI as the main submission, and manual
host setup.

**Reason:** The assignment requires a testable web URL. A repeatable local setup
also lets a reviewer run the project on their own machine.

**Tradeoff and cut:** A small node has limited memory and disk. Start with one
application and PostgreSQL rather than a distributed service setup. React and
TypeScript are the accepted frontend stack, with Vite for development and builds.
Use separate metadata and sandbox PostgreSQL services. The application serves the
built frontend and API behind a reverse proxy in the hosted setup.

**Validation:** Test setup from a clean environment and measure resource use before
choosing the node size. No deployment or startup command is available yet.

## D-007: Use structured drafts and feature-to-main merges

Date: 2026-09-17. Status: Accepted.

**Choice:** Collect edits in a saved draft, review the generated plan, and apply
the batch to the branch database before publishing a revision. Merge feature
branches into main using their common base and explicit conflict resolutions.

**Alternatives:** Execute every form edit immediately, expose an arbitrary SQL
editor, or support every Git-style history operation from the start.

**Reason:** A draft gives the user a clear review point. Structured changes retain
rename intent and are easier to validate. One merge direction keeps the core
workflow small enough to build and explain well.

**Tradeoff and cut:** Main changes go through branches. Rebase, cherry-pick, and
arbitrary branch merge targets are excluded. The initial lifecycle closes merged
source branches; later work starts from the latest main revision.

**Validation:** Test saved drafts, stale-head rejection, duplicate apply requests,
compatible changes, conflicting renames, and dependency conflicts. Verify that
failed execution does not publish a completed revision.

## D-008: Publish a narrow PostgreSQL support contract

Date: 2026-09-17. Status: Accepted.

**Choice:** Target PostgreSQL 17 and one selected application schema. Cover the
brief's operation categories for ordinary tables, supported scalar types,
constraints, and B-tree indexes. Define exact limits in
[compatibility](docs/compatibility.md).

**Alternatives:** Support all PostgreSQL objects or silently ignore unmodeled
features during import.

**Reason:** Complete behavior for a clear subset is more useful than an incomplete
claim of full compatibility. Import, diff, planning, and verification must agree
on the same schema model.

**Tradeoff and cut:** Some real databases will need unsupported features removed
from the selected schema before import. Arbitrary expressions, partitioning,
extensions, and procedural database objects are outside the first release.

**Validation:** Round-trip every supported definition through real PostgreSQL.
Test unsupported-object reporting and each supported type conversion separately.

## D-009: Use durable PostgreSQL jobs in one application

Date: 2026-09-17. Status: Accepted.

**Choice:** Persist jobs in the metadata database and run a bounded worker loop
inside the application process. Use explicit Psycopg transactions and poll job
status from the frontend. Keep pure schema and merge functions synchronous.

**Alternatives:** Run migrations inside HTTP requests, rely on in-memory background
tasks, or add a broker and separate worker service immediately.

**Reason:** Database work can outlast a request or process. Persisted jobs retain
intent and status without adding another service to the small deployment.

**Tradeoff and cut:** Keep one API process initially. Queue storage alone does not
provide exactly-once execution; target locks, receipts, and recovery checks are
required. Use direct SQL rather than an ORM for the schema engine.

**Validation:** Test browser disconnects, process restarts, duplicate requests,
target lock contention, and the gap between target commit and central completion.

## D-010: Favor an immediate demo and one polished UI

Date: 2026-09-17. Status: Accepted.

**Choice:** Offer an isolated hosted sample workspace and support configured
external connections in local or private deployments. Use customers, invoices,
invoice items, and payments as the demo dataset. Build one light workbench theme.

**Alternatives:** Require database credentials before the reviewer can try the
app, use one shared mutable demo, or spend time on extra themes and dashboards.

**Reason:** Reviewers need a quick path to real branching and merging. Isolated
workspaces avoid visitors overwriting one another. A focused interface gives more
time to make diffs, conflicts, and execution states clear.

**Tradeoff and cut:** The public demo cannot target arbitrary remote databases.
Workspace expiry and resource limits are needed on a small node. Team accounts
and fine-grained team permissions are outside this version.

**Validation:** Run the full flow from a fresh browser, verify workspace isolation,
and review keyboard access, narrow layouts, errors, and partial execution states.

## D-011: Make large-table behavior explicit and measurable

Date: 2026-09-17. Status: Accepted.

**Choice:** Keep schema workflows independent of row volume. Use concurrent index
builds and staged constraint checks where supported. Run rewrite-heavy changes as
tracked jobs with their lock and rewrite effects disclosed before execution.

**Alternatives:** Copy rows for branches, wrap every migration in one transaction,
or claim that every supported change can run without blocking writes.

**Reason:** PostgreSQL operations have different transaction and lock requirements.
The 5 GB requirement calls for evidence and a responsive workflow, not a blanket
claim that all DDL is fast or non-blocking.

**Tradeoff and cut:** Multi-phase migrations can leave partial state. Some type
changes need blocking work and additional disk space. Universal zero-downtime
conversion and automatic restoration of deleted data are excluded.

**Validation:** Use the physical-size benchmark and concurrent workload defined in
[requirements](docs/requirements.md). Record timings, memory, table sizes, lock
behavior, failures, and restart outcomes. No benchmark results exist yet.

## D-012: Make self-hosting primary and provide a hosted demo

Date: 2026-09-17. Status: Accepted.

**Choice:** Make Docker Compose on the user's machine or private server the main
way to use Proteus, similar to a self-hosted database administration tool. Run the
same app on E2E Networks with isolated sample databases for reviewers. This
clarifies the deployment priorities in D-006 and the demo scope in D-010.

**Alternatives:** Make Proteus a hosted service that manages user database
connections, or provide only local setup with no testable hosted URL.

**Reason:** Users can keep credentials and schema history in their own deployment.
The hosted demo still meets the assignment's URL requirement and lets reviewers
try real changes without installing the app.

**Tradeoff and cut:** Maintain local setup and a small demo deployment, but share
the application code and image. Demo visitors use sample databases only. Core
self-hosted workflows must not depend on our demo server or an E2E account.

**Validation:** Test the local workflow with no connection to the hosted service.
Check container networking guidance against real setup, and verify the hosted
sample workflow and workspace isolation separately. These checks remain pending.
