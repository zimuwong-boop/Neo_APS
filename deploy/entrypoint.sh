#!/bin/bash
set -Eeuo pipefail
: "${DJANGO_SECRET_KEY:?Required}" "${DB_NAME:?Required}" "${DB_USER:?Required}" "${DB_PASSWORD:?Required}"
export DB_HOST=127.0.0.1 DB_PORT=5432
PG_PID=""
WEB_PID=""
cleanup() {
    trap - EXIT TERM INT
    set +e
    if [[ -n "$WEB_PID" ]]; then
        kill -TERM "$WEB_PID" 2>/dev/null
        wait "$WEB_PID" 2>/dev/null
    fi
    if [[ -n "$PG_PID" ]]; then
        gosu postgres pg_ctl -D "$PGDATA" -m fast -w -t 25 stop
        wait "$PG_PID" 2>/dev/null
    fi
}
trap cleanup EXIT
trap 'exit 0' TERM INT
mkdir -p "$PGDATA" /var/run/postgresql
chown postgres:postgres /var/lib/neo-aps "$PGDATA" /var/run/postgresql
chmod 700 "$PGDATA"
if [[ ! -f "$PGDATA/PG_VERSION" ]]; then
    if [[ -n "$(ls -A "$PGDATA")" ]]; then
        echo 'Database directory is not empty; refusing initialization.' >&2
        exit 1
    fi
    gosu postgres initdb -D "$PGDATA" --encoding=UTF8 --locale=C.UTF-8 --auth-local=peer --auth-host=scram-sha-256
fi
if [[ "$(cat "$PGDATA/PG_VERSION")" != "17" ]]; then
    echo 'Database major version mismatch.' >&2
    exit 1
fi
gosu postgres postgres -D "$PGDATA" -c listen_addresses=127.0.0.1 -c logging_collector=off &
PG_PID=$!
ready=0
for attempt in {1..60}; do
    if gosu postgres pg_isready -q; then ready=1; break; fi
    kill -0 "$PG_PID" || exit 1
    sleep 1
done
[[ "$ready" == "1" ]] || { echo 'Database readiness timeout.' >&2; exit 1; }
gosu postgres python /app/deploy/init-db.py
if [[ "${MAINTENANCE_MODE:-0}" == "1" ]]; then
    wait "$PG_PID"
    exit 1
fi
gosu neoapp python /app/backend/manage.py migrate --noinput
cd /app/backend
gosu neoapp gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 2 --graceful-timeout 20 --access-logfile - --error-logfile - &
WEB_PID=$!
set +e
wait -n "$PG_PID" "$WEB_PID"
exit 1
