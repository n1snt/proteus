#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  printf 'usage: %s https://proteus.example.com\n' "$0" >&2
  exit 64
fi

base_url="${1%/}"
curl --fail --show-error --silent --max-time 10 --retry 5 --retry-all-errors \
  "$base_url/api/health"
printf '\nDeployment health endpoint responded.\n'
