# Proteus

Version control for PostgreSQL schemas.

Proteus is a planned web app for branching schemas, reviewing changes, resolving
merge conflicts, and applying those changes to a real database. It is named after
the shape-changing figure from Greek mythology.

The primary setup is self-hosted with Docker Compose, on your machine or private
server. A hosted sandbox on E2E Networks will let reviewers try the same app with
sample databases. Self-hosted use will not depend on that demo service.

## Status

The product direction is approved and the implementation specs are written.
The app, Docker setup, and hosted demo are not built yet.

## Planned workflow

1. Connect PostgreSQL and save the starting schema.
2. Create a schema-only branch, review draft edits, and apply a revision.
3. Compare the branch with main and resolve conflicts.
4. Apply the merge to main and record the resulting database revision.

The stack is Python, FastAPI, Psycopg 3, React, TypeScript, and PostgreSQL 17,
packaged with Docker Compose.

Schema changes made outside Proteus after import are out of scope. Normal
application reads and writes remain supported. The project must apply real schema
changes and be tested with tables holding roughly 5 GB of data.

## Project notes

- [Working and commit rules](AGENTS.md)
- [Decisions and tradeoffs](decisions.md)
- [Requirements and delivery order](docs/requirements.md)
- [Architecture](docs/architecture.md)
- [UI design](docs/design.md)
- [PostgreSQL support limits](docs/compatibility.md)
