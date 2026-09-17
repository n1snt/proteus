#!/bin/sh
set -eu

if [ "${PROTEUS_MODE:-local}" = "demo" ]; then
    : "${PROTEUS_SECRET_KEY:?PROTEUS_SECRET_KEY is required in demo mode}"
    : "${PROTEUS_POSTGRES_PASSWORD:?PROTEUS_POSTGRES_PASSWORD is required in demo mode}"
    : "${PROTEUS_DATABASE_URL:?PROTEUS_DATABASE_URL is required in demo mode}"
    : "${PROTEUS_SANDBOX_URL:?PROTEUS_SANDBOX_URL is required in demo mode}"
    if [ "${#PROTEUS_SECRET_KEY}" -lt 32 ] || [ "${#PROTEUS_POSTGRES_PASSWORD}" -lt 32 ]; then
        printf '%s\n' 'Demo secrets must each contain at least 32 characters.' >&2
        exit 64
    fi
    python - <<'PY'
import os
import sys
from urllib.parse import unquote, urlsplit

expected = {
    'PROTEUS_DATABASE_URL': ('metadata', 'proteus'),
    'PROTEUS_SANDBOX_URL': ('sandbox', 'proteus_sandbox'),
}
password = os.environ['PROTEUS_POSTGRES_PASSWORD']
for variable, (host, database) in expected.items():
    value = os.environ[variable]
    parsed = urlsplit(value)
    valid = (
        parsed.scheme in {'postgresql', 'postgres'}
        and parsed.hostname == host
        and parsed.port == 5432
        and unquote(parsed.username or '') == 'postgres'
        and unquote(parsed.password or '') == password
        and parsed.path == f'/{database}'
    )
    if not valid:
        print(f'{variable} must use the matching URI-escaped database password for {host}.', file=sys.stderr)
        raise SystemExit(64)
PY
fi

exec "$@"
