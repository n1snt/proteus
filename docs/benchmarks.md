# Large-table benchmark

Measured locally on 2026-09-17 using the real catalog, planner, and executor.
This is one machine's result, not a latency guarantee for the deployment node.

## Dataset and environment

- 4,325,376 rows, each with a random 1,024-byte binary payload and scalar fields.
- 5,063,278,592 physical data bytes before changes, excluding all indexes.
- 97,181,696 index bytes before changes. No TOAST payload data was needed.
- PostgreSQL 17.11 in Docker Desktop on macOS ARM64.
- Host: 10 logical CPUs and 32 GiB RAM. Docker VM: 10 CPUs and about 7.65 GiB RAM.
- PostgreSQL shared buffers: 128 MB; maintenance work memory: 64 MB.
- Python 3.12.13 ran the benchmark client on the host over a loopback port.

The loader used roughly 8 MB COPY batches. It took 74.43 seconds to create the
measured dataset. Loading is benchmark setup, not part of schema branching.

## Results

| Operation | Result |
| --- | --- |
| Schema-only branch creation | 0.090 seconds for schema execution and verification in an already created empty database |
| Catalog capture on large source | 10.76 to 11.67 ms across three samples |
| Catalog capture on empty branch | 10.69 to 12.52 ms across three samples |
| Semantic diff on large source snapshot | 0.35 to 0.55 ms |
| Three-way merge on captured snapshot | 0.55 to 0.60 ms |
| Plan generation | 0.30 to 0.31 ms |
| Column rename | 1.051 seconds including lock acquisition, receipts, and verification |
| Add nullable column | 0.043 seconds including execution and verification |
| Concurrent index build | 8.870 seconds with an active read/write workload |
| Validate bad existing data | Rejected; retained NOT VALID phase and marked execution 'needs_attention' |
| Retry validation after fixing data | 2.137 seconds, using the same execution ID |
| Integer-to-bigint conversion | 25.529 seconds; a new physical relation file confirmed the rewrite |
| Lock contention | Timed out as expected; same-ID retry succeeded after releasing the blocker |
| Interrupted concurrent index | Active receipt survived cancellation; same-ID recovery completed |

During the concurrent index build:

- 6,405 point reads completed with no errors; p95 latency was 2.305 ms.
- 3,553 inserts completed with no errors; p95 latency was 3.824 ms.
- Maximum observed latencies were 27.169 ms for reads and 181.611 ms for writes.

The Python process high-water memory mark reached 49,709,056 bytes. Loader and
engine observations are recorded separately. These are process-wide high-water
marks, not measurements of the memory allocated by one function.

## What the run proves

The schema workflows did not copy table rows into branches. The empty and populated
databases took similar time to introspect because they had the same small schema.
The actual executor applied changes, wrote receipts, and verified final structure.
Invalid data, partial validation, lock contention, and cancellation were exercised.

The branch timing excludes database provisioning and the HTTP/UI path. Diff and
merge timings use an already captured, small schema. The read workload uses point
lookups, not analytical scans. The rewrite benchmark reports blocking work honestly
and does not demonstrate universal zero-downtime conversion.

The run's interruption was task cancellation, not a server power failure. Separate
integration tests cover the gap between target commit and metadata publication.

## Reproduce

Start the local sandbox and run:

~~~sh
PROTEUS_SANDBOX_URL='postgresql://postgres:proteus_local@127.0.0.1:55433/proteus_sandbox' \
  uv run python benchmarks/large_table.py --target-bytes 5000000000 \
  --operation-timeout-seconds 900 --report .local/benchmark-5gb.json
~~~

The runner creates two new, uniquely named databases and removes them after the
run. Allow enough disk space for the source, indexes, WAL, and table rewrites.
The complete measured local artifact is '.local/benchmark-5gb-final.json'. A
portable result summary is [stored here](../benchmarks/results/2026-09-17.json).
