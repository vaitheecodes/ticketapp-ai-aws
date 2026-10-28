#!/usr/bin/env bash
# A throwaway local PostgreSQL for the test suite, using the Homebrew
# server binaries (no Docker needed). Data lives in .local/pgdata (git-
# ignored); nothing outside that folder is touched.
#
#   scripts/pg_local.sh start    # init on first use, start on 127.0.0.1:5433
#   scripts/pg_local.sh stop
#   scripts/pg_local.sh status
#   scripts/pg_local.sh reset    # stop and delete the data folder
#   scripts/pg_local.sh env      # print the variables the tests use
#
# The password below exists only for this local, loopback-only test
# server. Never reuse it anywhere else.
set -euo pipefail
# macOS: without a valid LC_ALL the server aborts at startup
# ("postmaster became multithreaded during startup").
export LC_ALL="${LC_ALL:-C}"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DATA="${PG_LOCAL_DATA:-$ROOT/.local/pgdata}"
PORT="${PG_LOCAL_PORT:-5433}"
USER_NAME="ticketapp"
PASSWORD="ticketapp-local-test-only"
DB_NAME="ticketapp_test"
URL="postgresql://$USER_NAME:$PASSWORD@127.0.0.1:$PORT/$DB_NAME"

need() { command -v "$1" >/dev/null || { echo "$1 not found (brew install postgresql@18)" >&2; exit 1; }; }

case "${1:-}" in
  start)
    need initdb; need pg_ctl
    if [ ! -f "$DATA/PG_VERSION" ]; then
      mkdir -p "$DATA"
      pwfile="$(mktemp)"; printf '%s\n' "$PASSWORD" > "$pwfile"
      initdb -D "$DATA" -U "$USER_NAME" --pwfile="$pwfile" --auth=scram-sha-256 \
        -E UTF8 --locale=C >/dev/null
      rm -f "$pwfile"
    fi
    if pg_ctl -D "$DATA" status >/dev/null 2>&1; then
      echo "already running on port $PORT"
    else
      pg_ctl -D "$DATA" -l "$DATA/server.log" -w \
        -o "-p $PORT -c listen_addresses=127.0.0.1 -k $DATA" start >/dev/null
      echo "started on 127.0.0.1:$PORT ($(postgres --version))"
    fi
    PGPASSWORD="$PASSWORD" psql -h 127.0.0.1 -p "$PORT" -U "$USER_NAME" -d postgres -tAc \
      "SELECT 1 FROM pg_database WHERE datname = '$DB_NAME'" | grep -q 1 \
      || PGPASSWORD="$PASSWORD" createdb -h 127.0.0.1 -p "$PORT" -U "$USER_NAME" "$DB_NAME"
    ;;
  stop)
    need pg_ctl
    pg_ctl -D "$DATA" -m fast -w stop >/dev/null && echo "stopped"
    ;;
  status)
    need pg_ctl
    pg_ctl -D "$DATA" status
    ;;
  reset)
    need pg_ctl
    pg_ctl -D "$DATA" -m fast -w stop >/dev/null 2>&1 || true
    rm -rf "$DATA" && echo "deleted $DATA"
    ;;
  env)
    echo "export DB_BACKEND=postgres"
    echo "export TEST_DATABASE_URL=$URL"
    ;;
  *)
    sed -n '2,13p' "$0"; exit 2
    ;;
esac
