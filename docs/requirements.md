# Requirements

Status: core local workflows implemented. Tests and the 5 GB benchmark have run;
the hosted URL and remote repository are pending owner-provided access. See
[testing](testing.md) and [benchmarks](benchmarks.md) for evidence.

Proteus helps an engineer change a PostgreSQL schema on an isolated branch,
understand differences, resolve conflicts, and merge into a shared main database.
It is a web app for the
[Zamp project round](https://app.notion.com/p/zampfinance/Engineering-Project-Round-303edc94e67f805490dafccfc4f6f35d).

## Product contract

- Import an existing schema as the starting revision of 'main'.
- Make tracked schema changes through Proteus after import. External schema
  changes are unsupported; normal application reads and writes are supported.
- Create feature branches with real, schema-only databases. Do not copy rows.
- Collect structured edits in a draft. Apply a reviewed batch to create a revision.
- Compare revisions and merge feature branches into main using their shared base.
- Apply generated changes to the real target, verify the result, and record it.
- Explain partial execution and failures without claiming that lost data can be
  recovered from schema history.

See [compatibility](compatibility.md) for the initial database and object limits.

## Distribution

Self-hosted Docker Compose is the primary setup. Users run the app locally or on
their own server and open its web UI. Their connections, credentials, and schema
history stay within their deployment. Core workflows do not call our demo service.

Host the same app on E2E Networks for the assignment's testable URL. This demo uses
isolated sample databases. It does not accept users' external database credentials.
Provide a sample database locally too, and lead the README setup with local use.

## User workflows and acceptance criteria

| ID | Workflow | Acceptance criteria |
| --- | --- | --- |
| R-01 | Start a demo | A visitor gets an isolated, writable sample workspace without supplying database credentials. |
| R-02 | Connect a database | A self-hosted deployment accepts connection fields or a URL, tests access, reports unsupported objects, and imports a baseline. |
| R-03 | Create a branch | A branch has a real isolated database with the supported schema and no source rows. The UI states this before creation. |
| R-04 | Edit a schema | Supported table, column, constraint, and index changes can be collected, reviewed, changed, or discarded before execution. |
| R-05 | Commit changes | A saved draft survives refresh. Successful execution advances the branch once; failed execution does not create a completed revision. |
| R-06 | Compare revisions | The diff shows semantic changes, including renames, rather than only raw SQL differences. |
| R-07 | Merge compatible changes | Independent valid changes from the source and target survive in the merged schema. |
| R-08 | Resolve conflicts | The UI shows base, source, and target values, explains the conflict, and validates the selected result. |
| R-09 | Review impact | Before applying, the user can inspect SQL, affected objects, data-loss effects, and expected lock, scan, or rewrite behavior. |
| R-10 | Follow execution | Closing or refreshing the browser does not lose the job. The UI shows persisted stages and actual outcomes. |
| R-11 | Recover interrupted work | Restart recovery checks target receipts and step outcomes before retrying. Partial work is shown and blocks unrelated target changes. |
| R-12 | Use a large table | The benchmark uses a measured table of at least 5 GB and reports resource use and database behavior for representative operations. |
| R-13 | Run the project | A clean checkout starts through the documented Docker setup, offers sample data, and completes core workflows without contacting the hosted demo. |
| R-14 | Review the submission | The hosted URL works, the repository explains the design, and decision entries match the actual implementation. |

## Schema changes

Cover all operation categories named in the brief:

- Create and drop tables.
- Add, drop, rename, and change the type of columns.
- Change nullability and defaults.
- Add, remove, and replace supported constraints and indexes.

Dependency effects must be part of the plan. Do not hide an unsupported object,
guess at a destructive rename, or use an unexplained cascade to force a change.

## Branch and commit behavior

- Main represents the connected database and receives merges.
- Feature branches start from a committed main revision.
- Changes to main use a feature branch and merge rather than the draft editor.
- A branch can contain multiple revisions before merging.
- Draft edits are stored separately from committed snapshots and real database
  state. Unsaved input must be clearly distinguished from a saved draft.
- Bind draft application to its original branch head and draft version.
- Bind merge application to the selected source and target heads and resolution
  version. If either head changes, require a new preview.
- The first release supports feature-to-main merges, not arbitrary merge targets.

The architecture defines how a merged branch is closed and how new work starts.

## Large-table acceptance

Use at least 5,000,000,000 bytes of physical table data, including TOAST storage
for large values but excluding indexes. Report the base table, TOAST, and index
sizes separately. Do not infer size from row count or uncompressed source text.

Compare the same schema on small and large datasets. Record:

1. Catalog capture, branch creation, diff, and merge planning time.
2. Application memory during these operations.
3. Column rename and nullable-column addition behavior.
4. Concurrent index build duration and read/write latency during the build.
5. Constraint validation with both valid and invalid existing rows.
6. A supported type conversion that rewrites data.
7. Lock contention, bounded waiting, and restart recovery.

Metadata workflows must not read or copy table rows. Long-running database work
must not hold an HTTP request open or prevent the UI from showing status.
Report hardware, configuration, repeated timings, and workload details. Set any
numeric latency targets before the benchmark run and report misses honestly.

## Evaluation evidence

| Criterion | Evidence |
| --- | --- |
| Problem framing | Explicit ownership contract, schema-only branch semantics, and published support limits |
| Product thinking | A complete connect, branch, edit, compare, merge, and verify workflow |
| UX | Useful connection errors, semantic diffs, clear conflict choices, and truthful progress |
| Engineering | Stable identities, three-way merging, ordered plans, and recoverable execution |
| Tests | Real database failures, merge edge cases, and focused API integration flows |
| Documentation | Clean setup instructions, current architecture, and a running decision log |
| Velocity | Early end-to-end milestones followed by measured improvements |
| Depth | Rename-aware merging and demonstrated large-table execution behavior |

## Delivery order

1. Project setup, local services, and baseline import.
2. Branch creation and one reviewed column change applied end to end.
3. Remaining supported edits, revisions, and semantic diffs.
4. Three-way merge, conflict resolution, and real main-database application.
5. Execution recovery, large-table strategies, and benchmark evidence.
6. UI refinement, clean-install checks, hosted demo, and submission walkthrough.

Each milestone includes its relevant tests and docs. Deploy an early working
version and update it as the workflow grows. Do not defer all testing or UI work
to the final milestone.

## Out of scope

- External schema change detection and reconciliation.
- Row history, row copying, or data merging between branches.
- Other database engines and unsupported PostgreSQL objects.
- Arbitrary SQL editing, rebase, cherry-pick, and arbitrary branch merges.
- Universal zero-downtime migrations or automatic recovery of deleted data.
- Team permissions, enterprise login, extra themes, and GitHub synchronization.

## Delivery inputs still needed

The owner set a 12-hour delivery window at implementation start. Develop and
verify locally first; SSH access and the GitHub remote will be provided later.
The node size and public URL can be selected after the first resource measurements.
