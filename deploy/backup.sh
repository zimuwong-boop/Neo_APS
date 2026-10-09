#!/bin/bash
set -euo pipefail
: "${1:?Supply a dump output path}"
export PGPASSWORD="$DB_PASSWORD"
pg_dump -h 127.0.0.1 -U "$DB_USER" -d "$DB_NAME" -Fc -f "$1"
pg_restore --list "$1" >/dev/null
echo 'Backup created and archive verified.'
