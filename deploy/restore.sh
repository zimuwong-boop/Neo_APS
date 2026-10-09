#!/bin/bash
set -euo pipefail
: "${1:?Supply a dump path}"
[[ "${MAINTENANCE_MODE:-0}" == "1" ]] || { echo 'Restore requires maintenance mode.' >&2; exit 1; }
export PGPASSWORD="$DB_PASSWORD"
count=$(psql -h 127.0.0.1 -U "$DB_USER" -d "$DB_NAME" -Atc "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")
[[ "$count" == "0" ]] || { echo 'Target database must be empty.' >&2; exit 1; }
pg_restore -h 127.0.0.1 -U "$DB_USER" -d "$DB_NAME" --no-owner --exit-on-error --single-transaction "$1"
echo 'Backup restored to empty database.'
