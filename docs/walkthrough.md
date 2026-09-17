# Demo and interview walkthrough

## First run

1. Start the app with 'docker compose up -d --build --wait'.
2. Open 'http://localhost:8000' and choose 'Try sample database'.
3. Wait for provisioning, then open the workspace.
4. Create a branch named 'customer-rename'. Existing rows are not copied.
5. Select 'customers', edit 'name', and rename it to 'legal_name'.
6. Save the draft, preview the SQL, and choose 'Apply and commit'.
7. Enter a message and wait for the job to complete.
8. Return to the branch, compare to main, and apply the merge.
9. Open main and inspect its schema and history.

## Conflicting changes

Create two branches from the same main revision before changing either branch.
Rename the same column differently on each branch and commit both. Merge the
first branch. Comparing the second now shows the common value, source change,
and main change. Choose the desired name and apply the resolved merge.

The API integration suite checks this exact history shape against PostgreSQL.
The UI review is manual.

## Explain the system

- A branch combines an immutable schema history with an isolated database.
- Stable object IDs preserve rename intent. Names alone are not identities.
- A three-way merge uses base, source, and target to identify independent changes.
- The planner compares main with the resolved result, rather than replaying source
  SQL blindly.
- PostgreSQL performs row scans, index builds, and rewrites. Python works with
  schema metadata and coordinates execution.
- Target receipts commit with transactional DDL. If central publication fails,
  retry reads the receipt instead of applying the DDL again.
- Concurrent indexes and staged validation need multiple phases. A failed later
  phase can leave real changes, so the target stays blocked until recovery.
- Schema history cannot restore deleted data. External DDL is outside scope.

## Useful code paths

| Topic | Start here |
| --- | --- |
| API request | 'backend/proteus/main.py' |
| Draft or merge preparation | 'backend/proteus/service.py' |
| Metadata and branch reservations | 'backend/proteus/storage.py' |
| Background execution | 'backend/proteus/jobs.py' |
| Object identity and expressions | 'backend/proteus/schema.py' |
| Three-way merge | 'backend/proteus/merge.py' |
| SQL ordering | 'backend/proteus/planner.py' |
| Transaction receipts and recovery | 'backend/proteus/executor.py' |

Use the [benchmark report](benchmarks.md) when discussing performance. Explain its
dataset, measured operations, and limits rather than quoting timings as promises.
