# Development

Status: local setup instructions. The containerized app has been started locally
with 'docker compose up -d --build app'.

## Requirements

- Docker Desktop or Docker Engine with Docker Compose.
- For source development: Python 3.12 and uv, plus Node.js 22 and npm.

Copy the local-only example before starting services:

~~~sh
cp .env.example .env
~~~

The values in '.env.example' are intentionally local development defaults. They
include a known database password and an insecure application key. Never reuse
them for a server or public demo.

## Local services

For source development, start PostgreSQL first. This starts only the metadata
and sandbox services and does not build the application image:

~~~sh
docker compose up -d metadata sandbox
~~~

The metadata database is available at '127.0.0.1:55432' as database 'proteus'.
The sandbox database is available at '127.0.0.1:55433' as database
'proteus_sandbox'. Both use user 'postgres' and the local password from '.env'.

The sandbox service applies [the demo fixture](../fixtures/demo.sql) only when
its named volume is first created. Resetting its volume removes the sample data:

~~~sh
docker compose down
docker volume rm proteus_sandbox_data
docker compose up -d sandbox
~~~

Do not run the volume removal command when the sandbox contains work that must
be kept.

## Run the application

Install backend dependencies from the lock file:

~~~sh
uv sync --frozen --all-groups
~~~

Install and run the frontend development server in another terminal:

~~~sh
npm --prefix web ci
npm --prefix web run dev
~~~

The Vite server listens on port 5173 and proxies '/api' to port 8000. To run the
backend directly, load the local environment and start its documented entry
point:

~~~sh
set -a
source .env
set +a
uv run uvicorn proteus.main:app --reload --host 127.0.0.1 --port 8000
~~~

For the containerized local application, build the Node 22 frontend stage and
the Python 3.12 runtime stage together:

~~~sh
docker compose up --build app
~~~

Compose makes the app available only at '127.0.0.1:8000'. Inside Compose, the
app uses the service names 'metadata' and 'sandbox'. A backend process running
on the host instead uses the loopback URLs in '.env'.

## Useful commands

Show service health and logs:

~~~sh
docker compose ps
docker compose logs -f app
~~~

Stop containers while retaining database volumes:

~~~sh
docker compose down
~~~

See [testing](testing.md) for checks and the separate large-table benchmark.

## Connect your own database

Choose a connection URL or use the host, port, database, user, password, and TLS
fields. Test access before import to see unsupported objects or missing privileges.
The role needs owner access to tracked tables, schema USAGE and CREATE, and
database CREATE for the tracking schema. The sandbox alone needs CREATEDB.

- Another Compose service: use its service name and container port.
- PostgreSQL on the host: use 'host.docker.internal'. The Compose app includes a
  host-gateway mapping for Linux. PostgreSQL must listen on an address reachable
  from Docker and allow the connection in its host access rules.
- A remote server: use its reachable hostname and the appropriate TLS mode.

Keep the same 'PROTEUS_SECRET_KEY' across restarts; it decrypts saved connections.
Workspace access uses a browser cookie. Demo sessions expire, while local session
cookies last 30 days. Keep the self-hosted interface on your machine or trusted
private network; a team login system is outside this version.
