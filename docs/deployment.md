# Deployment

Status: deployment configuration and operator procedure. No remote deployment is
claimed by this document.

## Public demo stack

The local [Compose file](../compose.yaml) starts the app on loopback and publishes
the two PostgreSQL development ports on loopback. The
[demo overlay](../compose.demo.yaml) uses the same app image, sets
'PROTEUS_MODE=demo', requires secure cookies, removes all app and database host
ports, and adds Caddy as the public HTTPS entry point.

Caddy obtains and renews certificates for 'PROTEUS_DOMAIN'. Before starting it:

- Point the domain's A or AAAA record at the server.
- Allow inbound TCP ports 80 and 443, plus UDP port 443 for HTTP/3.
- Keep the Docker host and its persistent volumes on storage with enough room for
  PostgreSQL data, indexes, and migration rewrites.

## Secrets and startup

Use an operator-controlled shell, secret store, or untracked environment file.
The following example generates fresh URL-safe hexadecimal values. It does not
provide reusable production credentials:

~~~sh
export PROTEUS_DOMAIN="proteus.example.com"
export PROTEUS_SECRET_KEY="$(openssl rand -hex 32)"
export PROTEUS_POSTGRES_PASSWORD="$(openssl rand -hex 32)"
export PROTEUS_DATABASE_URL="postgresql://postgres:${PROTEUS_POSTGRES_PASSWORD}@metadata:5432/proteus"
export PROTEUS_SANDBOX_URL="postgresql://postgres:${PROTEUS_POSTGRES_PASSWORD}@sandbox:5432/proteus_sandbox"
~~~

Choose the image tag before a release if the default local image name is not
appropriate:

~~~sh
export PROTEUS_IMAGE="registry.example.com/proteus:version"
~~~

Validate the merged configuration before creating containers. Docker Compose
must support the '!reset' tag used to clear inherited ports:

~~~sh
docker compose -f compose.yaml -f compose.demo.yaml config -q
docker compose -f compose.yaml -f compose.demo.yaml up -d --build --wait
~~~

Do not use '.env.example' or its development secret on a public host. The demo
overlay fails configuration when the domain, app secret, database password, or
internal database URLs are not supplied.

The app is not separately published in demo mode. Caddy alone exposes the public
ports and proxies to the app over the Compose network. Metadata and sandbox
PostgreSQL remain reachable only by other containers on that network.

Default demo limits are two projects per workspace, five branches per project,
and six hours per session. The shared demo also caps project reservations at 40.
The local mode has separate, larger defaults. Set the related environment values
explicitly if you need different per-workspace limits.

The app trusts 'X-Forwarded-Proto' only from loopback or the dedicated
'172.28.0.0/16' Compose network. This lets same-origin session checks see HTTPS
behind Caddy without trusting headers from the public network. If an operator
changes 'PROTEUS_NETWORK_SUBNET', they must rebuild the image with the matching
trusted network before deployment.

The image entrypoint validates demo secrets before Uvicorn starts. Both secrets
must contain at least 32 characters. It also verifies that the two internal URLs
use the matching URI-escaped PostgreSQL password and expected Compose host names.
The documented hexadecimal password is URI-safe. For another password format,
percent-encode it in each URL while leaving 'PROTEUS_POSTGRES_PASSWORD' unescaped.

## Smoke check

After DNS, HTTPS certificate issuance, and the application health check succeed,
run the optional external check from a machine that can reach the domain:

~~~sh
bash deploy/smoke.sh https://proteus.example.com
~~~

The script checks only the documented API health endpoint. It does not create a
demo workspace or prove migration behavior.

## Data and recovery

Named volumes retain the metadata and sandbox PostgreSQL data across container
restarts. Caddy certificate and configuration data also use named volumes in demo
mode. Keep regular, encrypted backups outside the Docker host. A basic logical
metadata dump command is:

~~~sh
docker compose -f compose.yaml -f compose.demo.yaml exec -T metadata \
  pg_dump -U postgres -d proteus > proteus-metadata.sql
~~~

Take equivalent sandbox backups when demo workspaces must be retained. Test a
restore on an isolated host before relying on a backup. A restore changes the
metadata database and must be done only after stopping the app and deciding how
to handle any target databases with active Proteus executions.

For a failed app container, inspect service state and logs first:

~~~sh
docker compose -f compose.yaml -f compose.demo.yaml ps
docker compose -f compose.yaml -f compose.demo.yaml logs --tail=200 app
~~~

Restarting the app does not delete the database volumes:

~~~sh
docker compose -f compose.yaml -f compose.demo.yaml restart app
~~~

If PostgreSQL storage is lost, restore metadata and sandbox data from backups,
then follow the application's recorded job recovery flow before accepting new
schema work. Do not rerun an interrupted target migration solely because a
container restarted.
