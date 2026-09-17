# Proteus

Version control for PostgreSQL schemas.

Proteus is a planned web app for branching schemas, reviewing changes, resolving
merge conflicts, and applying those changes to a real database. It is named after
the shape-changing figure from Greek mythology.

## Status

Planning stage. This repository contains the project rules and initial decisions.
The app, Docker setup, and hosted demo are not built yet.

## Planned workflow

1. Connect PostgreSQL and save the starting schema.
2. Create a schema branch and make changes.
3. Compare branches and resolve conflicts.
4. Apply the merge and record the resulting database revision.

The backend will use Python. The proposed stack is FastAPI, Psycopg 3, React,
TypeScript, and PostgreSQL, packaged with Docker Compose.

Schema changes made outside Proteus after import are out of scope. Normal
application reads and writes remain supported. The project must apply real schema
changes and be tested with tables holding roughly 5 GB of data.

## Project notes

- [Working and commit rules](AGENTS.md)
- [Decisions and tradeoffs](decisions.md)
