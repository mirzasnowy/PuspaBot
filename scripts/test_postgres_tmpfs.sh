#!/bin/sh
# Throwaway test Postgres on tmpfs, fsync off (E-17, plan section 9.1).
#
# The shared dev cluster cannot run with fsync off (cluster-wide
# setting, and other workers depend on it). For timing runs that
# need the plan's prescribed setup — test Postgres on tmpfs with
# fsync off — this script starts a throwaway Postgres 16 cluster
# in /dev/shm (RAM, gone on reboot) and prints the DSN to use:
#
#   ./scripts/test_postgres_tmpfs.sh start
#   PUSPA_TEST_DSN="dbname=puspa_test user=root host=/dev/shm/puspa-test-pg port=54333" \
#       python -m pytest -n 4 -m "not live"
#   ./scripts/test_postgres_tmpfs.sh stop
#
# The cluster is trust-auth, local-only (no TCP listener), and
# holds only throwaway test data. pgvector is installed on this
# host, so the migrations apply here exactly as on the main
# cluster. `stop` removes the data directory.
#
# Usage: test_postgres_tmpfs.sh start|stop|status

set -eu

PG_BIN=/usr/lib/postgresql/16/bin
DATA_DIR=/dev/shm/puspa-test-pg
PORT=54333
SOCKET_DIR=/dev/shm/puspa-test-pg
DSN="dbname=puspa_test user=root host=${SOCKET_DIR} port=${PORT}"

as_postgres() {
    sudo -u postgres "$@"
}

running() {
    as_postgres "$PG_BIN/pg_ctl" -D "$DATA_DIR" status >/dev/null 2>&1
}

do_start() {
    if [ -d "$DATA_DIR" ] && running; then
        echo "already running"
        echo "PUSPA_TEST_DSN=\"$DSN\""
        return 0
    fi
    if [ ! -d "$DATA_DIR" ]; then
        as_postgres "$PG_BIN/initdb" -D "$DATA_DIR" -U postgres --auth=trust >/dev/null
        cat >> "$DATA_DIR/postgresql.conf" <<EOF
# Throwaway test cluster (E-17): tmpfs + fsync off.
port = $PORT
listen_addresses = ''
unix_socket_directories = '$SOCKET_DIR'
fsync = off
synchronous_commit = off
full_page_writes = off
shared_buffers = 64MB
max_connections = 20
# WAL must stay far below /dev/shm (794MB on this VM): the default
# max_wal_size is 1GB, so a full suite fills the tmpfs and every
# test setup fails with DiskFull (observed: pg_wal alone at 288MB).
max_wal_size = 64MB
min_wal_size = 32MB
checkpoint_timeout = 60s
EOF
    fi
    as_postgres "$PG_BIN/pg_ctl" -D "$DATA_DIR" -l "$DATA_DIR/server.log" start >/dev/null
    for _ in $(seq 1 30); do
        if as_postgres "$PG_BIN/pg_isready" -h "$SOCKET_DIR" -p "$PORT" -q; then
            break
        fi
        sleep 1
    done
    as_postgres /usr/bin/psql -h "$SOCKET_DIR" -p "$PORT" -U postgres -d postgres -v ON_ERROR_STOP=1 <<'SQL' >/dev/null
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'root') THEN
        CREATE ROLE root LOGIN SUPERUSER;
    END IF;
END $$;
SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'puspa_test' AND pid <> pg_backend_pid();
DROP DATABASE IF EXISTS puspa_test;
CREATE DATABASE puspa_test OWNER root;
SQL
    echo "started on tmpfs (fsync off)"
    echo "PUSPA_TEST_DSN=\"$DSN\""
}

do_stop() {
    if [ -d "$DATA_DIR" ]; then
        if running; then
            as_postgres "$PG_BIN/pg_ctl" -D "$DATA_DIR" -m fast stop >/dev/null || true
        fi
        rm -rf "$DATA_DIR"
    fi
    echo "stopped; data directory removed"
}

do_status() {
    if [ -d "$DATA_DIR" ] && running; then
        echo "running"
        echo "PUSPA_TEST_DSN=\"$DSN\""
    else
        echo "not running"
    fi
}

case "${1:-}" in
    start) do_start ;;
    stop) do_stop ;;
    status) do_status ;;
    *) echo "usage: $0 start|stop|status" >&2; exit 2 ;;
esac
