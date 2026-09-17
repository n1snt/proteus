# Testing

Status: 32 focused cases passed locally on PostgreSQL 17.11, with no skipped
integration tests. Backend lint, frontend type checks, and production builds pass.

UI checks are manual. There is no Playwright or automated browser suite. Keep
unit and PostgreSQL tests focused on distinct behavior and failure cases.

## Fast checks

Install the locked backend dependencies, then run lint and unit tests:

~~~sh
uv sync --frozen --all-groups
uv run ruff check .
uv run pytest -m "not integration"
~~~

Run frontend checks from the locked npm dependency tree:

~~~sh
npm --prefix web ci
npm --prefix web run check
npm --prefix web run build
~~~

## PostgreSQL integration tests

The integration marker needs both PostgreSQL 17 services. Start them with the
local defaults, then use the URLs from '.env' when running tests on the host:

~~~sh
docker compose up -d metadata sandbox
set -a
source .env
set +a
uv run pytest -m integration
~~~

Metadata uses port 55432. Sandbox uses port 55433. Tests must create only
resources they own and must clean them up. Do not point integration tests at a
shared or production database.

CI starts separate PostgreSQL 17 metadata and sandbox services on ports 5432 and
5433. It runs the unit marker selection and the integration marker selection as
separate commands. The 5 GB benchmark is deliberately not part of CI.

## Demo fixture

The sandbox container loads [demo.sql](../fixtures/demo.sql) into
'proteus_sandbox' on its first initialization. To load the same fixture into an
empty database manually:

~~~sh
psql "postgresql://postgres:proteus_local@127.0.0.1:55433/proteus_sandbox" \
  -f fixtures/demo.sql
~~~

The fixture has ordinary customer, invoice, invoice-item, and payment tables. It
uses identity keys, simple foreign keys, non-negative single-column checks, and
literal or current-timestamp defaults.

## Large-table benchmark

'benchmarks/large_table.py' creates a new database whose name begins with
'proteus_benchmark_'. It refuses an existing database and removes the database
after the run unless '--keep-database' is set. The supplied sandbox role must be
allowed to create and drop databases.

Run the full benchmark only on a disposable PostgreSQL instance with more than
5 GB of available data disk space and room for rewrite and index work:

~~~sh
docker compose up -d sandbox
PROTEUS_SANDBOX_URL="postgresql://postgres:proteus_local@127.0.0.1:55433/proteus_sandbox" \
  uv run python benchmarks/large_table.py
~~~

The default target is 5,000,000,000 physical data bytes. The default 1,024-byte
'os.urandom' payload keeps the representative rows inline. COPY loads about 8 MB
per batch, avoiding a large application row buffer. The script checks base and
TOAST relation sizes, not a row-count estimate. It records base data, TOAST data,
base indexes, TOAST indexes, and total relation size separately. Its target
excludes all index bytes.

For a smoke run, lower the target explicitly. This is not evidence for the 5 GB
requirement:

~~~sh
PROTEUS_SANDBOX_URL="postgresql://postgres:proteus_local@127.0.0.1:55433/proteus_sandbox" \
  uv run python benchmarks/large_table.py --target-bytes 50000000
~~~

The JSON report defaults to '.local/large-table-benchmark.json'. The script
rejects a report path outside '.local', which is ignored by Git. It exits nonzero
after a required failure but still writes the report and attempts to remove both
benchmark databases. It stops before execution when catalog introspection reports
unsupported objects.

It creates a source database and an empty branch database with the engine's
'build_plan' and 'execute_plan' functions. It records executor receipts for
branch creation, rename, nullable-column addition, concurrent index build with
read and write latency samples, constraint failure then retry using the same
execution ID, an integer-to-bigint conversion, executor lock-timeout recovery,
and an attempted interrupted concurrent-index recovery. A fast smoke run can
finish the interruption before cancellation. That case is marked 'skipped', not
as a recovery success.

The report repeats catalog, diff, merge, and planning metrics for the schema-only
branch and the populated source. Loader and engine process maximum resident-set
observations are separate. They are process high-water marks, not a claim that
an operation used only that much memory. Host CPU and memory plus any visible
cgroup limits are included for later comparison.

Review each report before making performance claims. It contains one run's timing
and host settings, not a performance guarantee or a comparison across machines.
