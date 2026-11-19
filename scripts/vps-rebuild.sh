#!/usr/bin/env bash
# Puspa VPS rebuild — one command, fresh machine to working tree (E-02).
#
# Rebuilds a machine from scratch: installs Python 3.12 + PostgreSQL 16
# (+ pgvector) + Docker, creates the Postgres roles and databases,
# fetches the Puspa tree, installs the pinned requirements, applies
# infra/schema_core.sql, and verifies RLS. These are the same steps the
# coordinator does by hand after every VM replacement (see
# docs/vps-validation.md and the Postgres recovery notes in the
# coordinator pipeline doc) — here as one script, timed, so the
# "machine replaced" drill is a single run plus a stopwatch read.
#
# Usage (on a fresh Ubuntu 24.04 machine, as root):
#   curl -O https://raw.githubusercontent.com/mirzasnowy/puspa-ai/main/scripts/vps-rebuild.sh
#   bash vps-rebuild.sh
#
# Options:
#   --dry-run            print every step without executing anything
#   --repo-url URL       git remote (default: https://github.com/mirzasnowy/puspa-ai.git)
#   --branch BRANCH      branch to check out (default: main)
#   --target-dir DIR     where the tree lives (default: $HOME/puspa)
#   --app-db NAME        application database name (default: puspa)
#   --help               print usage and exit
#
# Notes:
# - Auth is peer auth everywhere (OS user = Postgres role). No
#   credentials are created, read, or stored by this script.
# - The script is idempotent: re-running it re-applies the same
#   guarded steps (role/DB creation is guarded, schema_core.sql is
#   idempotent, an existing tree is pulled instead of re-cloned).
# - This script does NOT start the app: `docker compose up` is the
#   first step of the VPS validation procedure
#   (docs/vps-validation.md), run after this rebuild finishes.
# - apt-get update can take ~10 minutes through a slow proxy; the
#   per-step timings below make that visible instead of silent.
set -euo pipefail

REPO_URL="https://github.com/mirzasnowy/puspa-ai.git"
BRANCH="main"
TARGET_DIR="$HOME/puspa"
APP_DB="puspa"
DRY_RUN=0

usage() {
    echo "Usage: bash vps-rebuild.sh [--dry-run] [--repo-url URL] [--branch BRANCH]"
    echo "                          [--target-dir DIR] [--app-db NAME]"
    echo "Rebuild a fresh Ubuntu 24.04 machine into a working Puspa tree."
}

while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRY_RUN=1; shift ;;
        --repo-url) REPO_URL="$2"; shift 2 ;;
        --branch) BRANCH="$2"; shift 2 ;;
        --target-dir) TARGET_DIR="$2"; shift 2 ;;
        --app-db) APP_DB="$2"; shift 2 ;;
        --help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done

if [ "$(id -u)" -eq 0 ]; then
    SUDO=()
else
    SUDO=(sudo)
fi

ROOT_DSN="dbname=postgres user=root host=/var/run/postgresql"
APP_DSN="dbname=${APP_DB} user=root host=/var/run/postgresql"
LOG_FILE="$HOME/vps-rebuild.log"
STARTED_AT="$(date +%s)"
STEP_INDEX=0
STEP_TOTAL=11

log() {
    local line="$1"
    echo "$line"
    if [ "$DRY_RUN" -eq 0 ]; then
        echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $line" >> "$LOG_FILE"
    fi
}

# Run a command, or print it in dry-run mode. Never executes anything
# when --dry-run is set, so the drill can be rehearsed safely.
run() {
    if [ "$DRY_RUN" -eq 1 ]; then
        echo "[dry-run] $*"
    else
        "$@"
    fi
}

# Run one numbered step with timing. The step name prints before the
# step runs so a killed run still shows where it stopped.
step() {
    local name="$1"
    local func="$2"
    STEP_INDEX=$((STEP_INDEX + 1))
    local step_start
    step_start="$(date +%s)"
    log "STEP ${STEP_INDEX}/${STEP_TOTAL}: ${name}"
    "$func"
    local step_end
    step_end="$(date +%s)"
    log "STEP ${STEP_INDEX}/${STEP_TOTAL}: ${name} — done in $((step_end - step_start))s"
}

install_system_packages() {
    export DEBIAN_FRONTEND=noninteractive
    run "${SUDO[@]}" apt-get update
    run "${SUDO[@]}" apt-get install -y git curl python3.12 python3.12-venv \
        postgresql-16 postgresql-16-pgvector docker.io docker-compose-v2
}

start_postgres_cluster() {
    if run "${SUDO[@]}" pg_lsclusters | grep -q "16.*main.*online"; then
        echo "PostgreSQL 16/main cluster already online."
    else
        run "${SUDO[@]}" pg_ctlcluster 16 main start || echo "pg_ctlcluster start deferred (check pg_lsclusters)."
    fi
}

create_role_root() {
    # Role root must exist BEFORE infra/schema_core.sql runs: the
    # schema connects as root. Guarded, so re-runs are no-ops.
    run "${SUDO[@]}" su postgres -c "psql -v ON_ERROR_STOP=1 -d postgres" <<'SQL'
SELECT 'CREATE ROLE root LOGIN SUPERUSER'
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'root')
\gexec
SQL
}

fetch_puspa_tree() {
    if [ -d "$TARGET_DIR/.git" ]; then
        run git -C "$TARGET_DIR" fetch origin
        run git -C "$TARGET_DIR" checkout "$BRANCH"
        run git -C "$TARGET_DIR" pull --ff-only origin "$BRANCH"
    else
        run git clone --branch "$BRANCH" "$REPO_URL" "$TARGET_DIR"
    fi
}

install_python_deps() {
    if [ ! -x "$TARGET_DIR/.venv/bin/python" ]; then
        run python3.12 -m venv "$TARGET_DIR/.venv"
    fi
    run "$TARGET_DIR/.venv/bin/pip" install --upgrade pip
    run "$TARGET_DIR/.venv/bin/pip" install -r "$TARGET_DIR/requirements.txt"
}

create_app_database() {
    run "${SUDO[@]}" psql "$ROOT_DSN" -v ON_ERROR_STOP=1 <<SQL
SELECT 'CREATE DATABASE ${APP_DB} OWNER root'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = '${APP_DB}')
\gexec
SQL
}

apply_core_schema() {
    # Creates puspa_test + applies ledger/accounts schemas to it.
    # The \ir paths inside are relative to the file, so run with -f
    # on the real path — never via a /tmp copy.
    run "${SUDO[@]}" psql "$ROOT_DSN" -v ON_ERROR_STOP=1 \
        -f "$TARGET_DIR/infra/schema_core.sql"
}

apply_app_schemas() {
    run "${SUDO[@]}" psql "$APP_DSN" -v ON_ERROR_STOP=1 \
        -f "$TARGET_DIR/puspa/ledger/schema.sql"
    run "${SUDO[@]}" psql "$APP_DSN" -v ON_ERROR_STOP=1 \
        -f "$TARGET_DIR/puspa/accounts/schema.sql"
}

verify_rls() {
    if [ "$DRY_RUN" -eq 1 ]; then
        echo "[dry-run] check RLS on app + test databases (expect 3/3 tables with RLS)"
        return 0
    fi
    for dsn in "$APP_DSN" "dbname=puspa_test user=root host=/var/run/postgresql"; do
        local count
        count="$(run "${SUDO[@]}" psql "$dsn" -tA -c \
            "SELECT count(*) FROM pg_class WHERE relname IN ('users','sessions','vault') AND relrowsecurity;")"
        if [ "$count" != "3" ]; then
            echo "RLS check FAILED on ${dsn}: ${count}/3 tables have RLS enabled." >&2
            exit 1
        fi
        echo "RLS enabled on users, sessions, vault (${dsn})."
    done
}

verify_docker() {
    run docker --version
    run docker compose version
}

offline_deploy_validation() {
    export NO_PROXY=localhost,127.0.0.1
    export no_proxy=localhost,127.0.0.1
    run env -C "$TARGET_DIR" "$TARGET_DIR/.venv/bin/python" -m pytest \
        tests/test_teams_deploy.py -q
}

main() {
    log "Puspa VPS rebuild started (repo=${REPO_URL} branch=${BRANCH} target=${TARGET_DIR} app-db=${APP_DB})"
    step "Install system packages (apt, Python 3.12, PostgreSQL 16 + pgvector, Docker)" install_system_packages
    step "Start PostgreSQL cluster" start_postgres_cluster
    step "Create role root (superuser, peer auth)" create_role_root
    step "Fetch Puspa tree (clone or pull)" fetch_puspa_tree
    step "Create Python virtualenv and install requirements" install_python_deps
    step "Create databases (app + test)" create_app_database
    step "Apply core schema (infra/schema_core.sql)" apply_core_schema
    step "Apply ledger + accounts schemas to the app database" apply_app_schemas
    step "Verify RLS on app and test databases" verify_rls
    step "Verify Docker and Compose" verify_docker
    step "Offline deploy validation (Dockerfile + compose pytest)" offline_deploy_validation
    local ended_at
    ended_at="$(date +%s)"
    local total=$((ended_at - STARTED_AT))
    log "Puspa VPS rebuild finished in ${total}s ($((total / 60))m $((total % 60))s). Next: docs/vps-validation.md step 1 (docker compose up)."
}

main
