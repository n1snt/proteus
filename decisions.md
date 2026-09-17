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
Psycopg 3 are the proposed API framework and PostgreSQL driver.

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
update. The proposed design commits transactional DDL with its target receipt.
Multi-phase operations need separate progress and recovery checks. Exact tables
and recovery behavior remain to be designed and tested.

## D-005: Use schema-only branch databases

Date: 2026-09-17. Status: Proposed.

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
and test rename conflicts and target data failures before accepting this design.

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
TypeScript are the proposed frontend stack. Exact service layout is still open.

**Validation:** Test setup from a clean environment and measure resource use before
choosing the node size. No deployment or startup command is available yet.
