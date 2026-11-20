"""Migration tests (E-08, plan section 5.3).

What is covered here, and what is not:

- The runner on scratch databases (created and dropped by these
  tests): apply-all, idempotency, targeted apply, rollback round
  trip with seeded rows, unknown-target refusal.
- The wiring: every ``create_schema`` entry point applies up to its
  own migration, so the loaders are thin aliases over the runner.
- The committed snapshot: migrations applied to an empty database
  dump to exactly ``infra/schema_snapshot.sql`` (order-insensitive,
  comments stripped). This is the CI check from plan section 5.3.
- RLS isolation on top of a migrated database (two users, teams,
  rooms): the migrated schema enforces the same isolation the old
  loaders produced.
- The scanner: no Python code outside ``puspa/migrations`` creates
  tables (the SQLite ``state.db`` schema is the one exception: that
  database is rebuildable by design).
- The dev Postgres compose file: pinned image, loopback-only port,
  healthcheck.

One-time equivalence proof (old loaders vs migrations) was done
before the loaders were rewired, by comparing ``pg_dump -s`` of a
database built the old way with one built by the migrations: the
only difference was the ``schema_migrations`` table itself (3
statements). That comparison cannot run live anymore — the old
loaders are gone — so it is recorded in docs/migrations.md, and
the snapshot test above is the guard that keeps the schema honest
from here on.

The scratch databases are separate from ``puspa_test_w2`` (the
shared test database): these tests create ``puspa_migtest_*``
databases as the same superuser the suite connects as, and drop
them afterwards.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections import Counter
from pathlib import Path

import psycopg
import pytest

from puspa.migrations import (
    MigrationError,
    apply_migrations,
    list_migrations,
    migration_status,
    rollback_migrations,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT_PATH = REPO_ROOT / "infra" / "schema_snapshot.sql"
VERSIONS_DIR = REPO_ROOT / "puspa" / "migrations" / "versions"
DEV_COMPOSE_PATH = REPO_ROOT / "docker-compose.dev.yml"

DEFAULT_DSN = "dbname=puspa_test user=root host=/var/run/postgresql"

#: Every table the migrations must create (from information_schema
#: after a full apply). ``schema_migrations`` is the bookkeeping
#: table the runner itself owns.
EXPECTED_TABLES = {
    "agent_connectors",
    "agent_goal_models",
    "agent_meta",
    "agent_revocations",
    "agents",
    "belief_versions",
    "billing_usage_rows",
    "block_requests",
    "bot_access_grants",
    "bot_access_requests",
    "canary_runs",
    "canonical_products",
    "cassettes",
    "causal_hypotheses",
    "channel_deliveries",
    "conflict_records",
    "data_consents",
    "delegated_task_steps",
    "delegated_tasks",
    "draft_approvals",
    "drafts",
    "embedding_canary_baselines",
    "embedding_reembed_jobs",
    "embedding_state",
    "entitlements",
    "episodic",
    "event_receipts",
    "events",
    "evidence",
    "evidence_cache",
    "false_reports",
    "handoffs",
    "invitations",
    "kill_switch",
    "listing_links",
    "loop_corrections",
    "loop_experiences",
    "marketplace_audit",
    "marketplace_installs",
    "marketplace_versions",
    "meeting_minutes",
    "mockups",
    "model_settings",
    "module_rows",
    "module_tables",
    "module_versions",
    "modules",
    "monthly_credits",
    "morning_notes",
    "onboarding",
    "onboarding_hint_seen",
    "prepared_payments",
    "price_series",
    "procedure_uses",
    "procedure_versions",
    "procedures",
    "profile_facts",
    "push_subscriptions",
    "read_budgets",
    "room_channel_links",
    "room_invites",
    "room_job_queue",
    "room_members",
    "room_memories",
    "room_money_approvals",
    "room_research_links",
    "room_splits",
    "room_tasks",
    "rooms",
    "schema_migrations",
    "sessions",
    "shared_graph_private_records",
    "shared_price_observations",
    "shared_seller_flags",
    "slice_grants",
    "source_readings",
    "split_payments",
    "split_settlements",
    "split_shares",
    "style_examples",
    "summaries",
    "system_heartbeats",
    "takeover_holds",
    "tasks",
    "team_members",
    "teams",
    "token_metrics",
    "token_revocations",
    "tos_checks",
    "trigger_runs",
    "triggers",
    "used_email_links",
    "users",
    "vault",
    "watchlist",
    "work_items",
    "workflow_approvals",
    "workflow_library",
    "workflow_runs",
    "workflow_versions",
    "workflows",
    "workspace_moves",
    "world_beliefs",
}

#: DDL statements no Python code may hold (the SQLite state.db
#: schema is the one exception: that database is rebuildable).
FORBIDDEN_DDL = (
    "CREATE TABLE",
    "CREATE INDEX",
    "ALTER TABLE",
    "DROP TABLE",
    "CREATE POLICY",
    "CREATE OR REPLACE FUNCTION",
    "CREATE EXTENSION",
)


def _dsn_parts(dsn: str) -> dict[str, str]:
    parts: dict[str, str] = {}
    for item in dsn.split():
        if "=" in item:
            key, _, value = item.partition("=")
            parts[key] = value
    return parts


def _maintenance_dsn(dsn: str) -> str:
    """The same connection, pointed at the maintenance database."""
    parts = _dsn_parts(dsn)
    parts["dbname"] = "postgres"
    return " ".join(f"{key}={value}" for key, value in parts.items())


@pytest.fixture
def scratch_dsn(request, test_dsn):
    """A fresh empty database for one test; dropped afterwards.

    The name carries the process id so a worker run and the central
    suite (or two workers) can use the same cluster at the same time
    without dropping each other's scratch database mid-test.
    """
    name = (f"puspa_migtest_{os.getpid()}_{request.node.name.replace('[', '_').replace(']', '')}")[
        :63
    ]
    dsn = f"dbname={name} " + " ".join(
        f"{key}={value}" for key, value in _dsn_parts(test_dsn).items() if key != "dbname"
    )
    with psycopg.connect(_maintenance_dsn(test_dsn), autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{name}"')
        conn.execute(f'CREATE DATABASE "{name}"')
    yield dsn
    with psycopg.connect(_maintenance_dsn(test_dsn), autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{name}"')


def _tables(dsn: str) -> set[str]:
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
        ).fetchall()
    return {row[0] for row in rows}


def _split_statements(text: str) -> list[str]:
    """Split pg_dump output into statements (dollar-quote aware)."""
    statements: list[str] = []
    current: list[str] = []
    i, n = 0, len(text)
    dollar_tag: str | None = None
    in_string = False
    while i < n:
        ch = text[i]
        if dollar_tag is not None:
            current.append(ch)
            if text.startswith(dollar_tag, i):
                current.extend(dollar_tag[1:])
                i += len(dollar_tag)
                dollar_tag = None
            else:
                i += 1
            continue
        if in_string:
            current.append(ch)
            if ch == "'":
                if text.startswith("''", i):
                    current.append("'")
                    i += 2
                    continue
                in_string = False
            i += 1
            continue
        if ch == "'":
            in_string = True
            current.append(ch)
            i += 1
            continue
        if ch == "$":
            match = re.match(r"\$[A-Za-z_][A-Za-z0-9_]*\$|\$\$", text[i:])
            if match:
                dollar_tag = match.group(0)
                current.append(dollar_tag)
                i += len(dollar_tag)
                continue
            current.append(ch)
            i += 1
            continue
        if ch == ";":
            current.append(ch)
            stmt = "".join(current).strip()
            if stmt:
                statements.append(stmt)
            current = []
            i += 1
            continue
        current.append(ch)
        i += 1
    tail = "".join(current).strip()
    if tail:
        statements.append(tail)
    return statements


def _normalize_dump(text: str) -> Counter:
    """pg_dump -s output as a multiset of statements.

    Drops comment lines (pg_dump headers carry version strings),
    ``\\restrict`` / ``\\unrestrict`` lines (random token per dump),
    and blank lines, then sorts: pg_dump orders objects by
    dependency + creation order, so two builds of the same schema in
    a different statement order dump the same statements in a
    different order.
    """
    lines = []
    for line in text.splitlines():
        if line.startswith("--"):
            continue
        if line.startswith("\\"):
            continue
        if not line.strip():
            continue
        lines.append(line)
    return Counter(_split_statements("\n".join(lines)))


def _pg_dump(dsn: str) -> str:
    if shutil.which("pg_dump") is None:
        pytest.skip("pg_dump is not installed on this host")
    parts = _dsn_parts(dsn)
    env = dict(os.environ)
    if "password" in parts:
        env["PGPASSWORD"] = parts["password"]
    result = subprocess.run(
        ["pg_dump", "-s", dsn],
        capture_output=True,
        check=False,
        text=True,
        env=env,
        timeout=120,
    )
    assert result.returncode == 0, f"pg_dump failed: {result.stderr}"
    return result.stdout


# --- runner -----------------------------------------------------------------


def test_migration_files_are_numbered_and_reversible():
    """27 migrations, versions 0001-0027, each with up + down."""
    migrations = list_migrations()
    assert [m.version for m in migrations] == [f"{n:04d}" for n in range(1, 28)]
    assert len({m.name for m in migrations}) == 27
    for migration in migrations:
        assert migration.up_sql, f"{migration.name}: empty up"
        assert migration.down_sql, f"{migration.name}: empty down"
        assert not migration.irreversible, f"{migration.name}: marked irreversible"


def test_apply_all_to_empty_db_creates_every_table(scratch_dsn):
    applied = apply_migrations(scratch_dsn)
    assert applied == [f"{n:04d}" for n in range(1, 28)]
    assert _tables(scratch_dsn) == EXPECTED_TABLES
    with psycopg.connect(scratch_dsn) as conn:
        (count,) = conn.execute("SELECT count(*) FROM schema_migrations").fetchone()
    assert count == 27


def test_apply_is_idempotent(scratch_dsn):
    assert len(apply_migrations(scratch_dsn)) == 27
    assert apply_migrations(scratch_dsn) == []
    assert apply_migrations(scratch_dsn, target="0006") == []


def test_targeted_apply_stops_at_target(scratch_dsn):
    applied = apply_migrations(scratch_dsn, target="0001")
    assert applied == ["0001"]
    tables = _tables(scratch_dsn)
    assert {"users", "sessions", "vault", "agents", "events"} <= tables
    assert "tasks" not in tables
    assert "teams" not in tables


def test_unknown_target_is_refused(scratch_dsn):
    with pytest.raises(MigrationError):
        apply_migrations(scratch_dsn, target="9999")
    with pytest.raises(MigrationError):
        rollback_migrations(scratch_dsn, target="9999")


def test_status_reports_pending_then_applied(scratch_dsn):
    before = migration_status(scratch_dsn)
    assert len(before) == 27
    assert all(not row["applied"] for row in before)
    apply_migrations(scratch_dsn, target="0003")
    after = migration_status(scratch_dsn)
    applied = {row["version"] for row in after if row["applied"]}
    assert applied == {"0001", "0002", "0003"}
    assert all(row["applied_at"] for row in after if row["applied"])


def test_rollback_round_trip_with_seeded_rows(scratch_dsn):
    """Down drops the tables (and their data); up brings them back empty."""
    apply_migrations(scratch_dsn)
    with psycopg.connect(scratch_dsn) as conn:
        conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES "
            "('11111111-1111-1111-1111-111111111111', 'seed@example.com', 'x')"
        )
        conn.execute(
            "INSERT INTO agents (agent_id, user_id) VALUES "
            "('seed-agent', '11111111-1111-1111-1111-111111111111')"
        )
        conn.execute(
            "INSERT INTO conflict_records (id, user_id, agent_id, kind, "
            "tool_name, beneficiary) VALUES "
            "('22222222-2222-2222-2222-222222222222', "
            "'11111111-1111-1111-1111-111111111111', 'seed-agent', "
            "'seller_vs_user', 'buy', 'seller')"
        )
        conn.execute("INSERT INTO work_items (id) VALUES ('work-seed')")
        conn.commit()
    rolled_back = rollback_migrations(scratch_dsn, target="0019")
    assert rolled_back == ["0027", "0026", "0025", "0024", "0023", "0022", "0021", "0020"]
    tables = _tables(scratch_dsn)
    assert "token_revocations" not in tables
    assert "embedding_state" not in tables
    assert "embedding_reembed_jobs" not in tables
    assert "embedding_canary_baselines" not in tables
    assert "conflict_records" in tables
    assert "work_items" in tables
    with psycopg.connect(scratch_dsn) as conn:
        (kept,) = conn.execute("SELECT count(*) FROM work_items").fetchone()
    assert kept == 1
    assert apply_migrations(scratch_dsn) == [
        "0020",
        "0021",
        "0022",
        "0023",
        "0024",
        "0025",
        "0026",
        "0027",
    ]
    with psycopg.connect(scratch_dsn) as conn:
        (revocations,) = conn.execute("SELECT count(*) FROM token_revocations").fetchone()
        (users,) = conn.execute("SELECT count(*) FROM users").fetchone()
    assert revocations == 0
    assert users == 1


def test_full_rollback_to_0001_and_back(scratch_dsn):
    """Every down section runs: roll back to 0001, then re-apply all.

    This is the test that catches down-section dependency bugs (a
    function dropped while a policy still depends on it, a column
    a down section forgot): the one-migration rollback above only
    exercises the newest down section.
    """
    apply_migrations(scratch_dsn)
    with psycopg.connect(scratch_dsn) as conn:
        conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES "
            "('11111111-1111-1111-1111-111111111111', 'seed@example.com', 'x')"
        )
        conn.execute(
            "INSERT INTO agents (agent_id, user_id) VALUES "
            "('seed-agent', '11111111-1111-1111-1111-111111111111')"
        )
        conn.execute(
            "INSERT INTO conflict_records (id, user_id, agent_id, kind, "
            "tool_name, beneficiary) VALUES "
            "('22222222-2222-2222-2222-222222222222', "
            "'11111111-1111-1111-1111-111111111111', 'seed-agent', "
            "'seller_vs_user', 'buy', 'seller')"
        )
        conn.commit()
    rolled_back = rollback_migrations(scratch_dsn, target="0001")
    assert rolled_back == [f"{n:04d}" for n in range(27, 1, -1)]
    tables = _tables(scratch_dsn)
    assert tables == {
        "agents",
        "events",
        "kill_switch",
        "schema_migrations",
        "sessions",
        "users",
        "vault",
    }
    with psycopg.connect(scratch_dsn) as conn:
        (valid_at_columns,) = conn.execute(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_name = 'events' AND column_name = 'valid_at'"
        ).fetchone()
        (users,) = conn.execute("SELECT count(*) FROM users").fetchone()
    assert valid_at_columns == 0
    assert users == 1
    assert apply_migrations(scratch_dsn) == [f"{n:04d}" for n in range(2, 28)]
    assert _tables(scratch_dsn) == EXPECTED_TABLES
    with psycopg.connect(scratch_dsn) as conn:
        (conflicts,) = conn.execute("SELECT count(*) FROM conflict_records").fetchone()
        (users,) = conn.execute("SELECT count(*) FROM users").fetchone()
    assert conflicts == 0
    assert users == 1


# --- wiring: every loader applies up to its own migration -------------------


def test_loader_targets(scratch_dsn):
    """Each create_schema entry point lands on its migration version."""
    from puspa.accounts import create_schema as create_account_schema
    from puspa.accounts.access import create_agent_schema
    from puspa.billing.ledger import create_schema as create_billing_schema
    from puspa.channels.schema import create_schema as create_channels_schema
    from puspa.drafting.schema import create_schema as create_drafting_schema
    from puspa.graphs import create_schema as create_graphs_schema
    from puspa.graphs.shared import create_shared_schema
    from puspa.ledger import Ledger
    from puspa.marketplace.store import create_schema as create_marketplace_schema
    from puspa.meetings.schema import create_schema as create_meetings_schema
    from puspa.memory import create_schema as create_memory_schema
    from puspa.memory.loop_store import create_schema as create_loop_schema
    from puspa.mockup.store import create_schema as create_mockup_schema
    from puspa.morning.notes import create_schema as create_morning_notes_schema
    from puspa.onboarding.hints import create_schema as create_hints_schema
    from puspa.payments.schema import create_schema as create_payments_schema
    from puspa.policy.conflicts import create_schema as create_conflicts_schema
    from puspa.policy.revocations import RevocationList
    from puspa.pwa.live_view import TakeoverStore
    from puspa.pwa.model_settings import create_schema as create_model_settings_schema
    from puspa.rooms.job_queue import create_schema as create_queue_schema
    from puspa.rooms.schema import create_schema as create_rooms_schema
    from puspa.runtime import create_schema as create_runtime_schema
    from puspa.scheduler.claim import ensure_work_items_schema
    from puspa.scheduler.receipts import ensure_receipts_schema
    from puspa.teams.schema import create_schema as create_teams_schema
    from puspa.workflow.schema import create_schema as create_workflow_schema
    from puspa.world.store import (
        apply_agent_migration,
        apply_bitemporal_migration,
        apply_causal_migration,
    )
    from puspa.world.store import (
        create_schema as create_world_schema,
    )

    def recorded() -> set[str]:
        with psycopg.connect(scratch_dsn) as conn:
            return {
                row[0] for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
            }

    steps = [
        (lambda: create_account_schema(scratch_dsn), "0001"),
        (lambda: Ledger(scratch_dsn).create_schema(), "0001"),
        (lambda: create_agent_schema(scratch_dsn), "0001"),
        (lambda: create_runtime_schema(scratch_dsn), "0003"),
        (lambda: create_graphs_schema(scratch_dsn), "0004"),
        (lambda: create_shared_schema(scratch_dsn), "0004"),
        (lambda: create_workflow_schema(scratch_dsn), "0005"),
        (lambda: create_world_schema(scratch_dsn), "0006"),
        (lambda: apply_bitemporal_migration(scratch_dsn), "0006"),
        (lambda: apply_causal_migration(scratch_dsn), "0006"),
        (lambda: apply_agent_migration(scratch_dsn), "0006"),
        (lambda: create_loop_schema(scratch_dsn), "0007"),
        (lambda: create_channels_schema(scratch_dsn), "0008"),
        (lambda: create_teams_schema(scratch_dsn), "0009"),
        (lambda: create_rooms_schema(scratch_dsn), "0010"),
        (lambda: create_meetings_schema(scratch_dsn), "0011"),
        (lambda: create_drafting_schema(scratch_dsn), "0012"),
        (lambda: create_payments_schema(scratch_dsn), "0013"),
        (lambda: create_billing_schema(scratch_dsn), "0013"),
        (lambda: create_marketplace_schema(scratch_dsn), "0014"),
        (lambda: create_mockup_schema(scratch_dsn), "0015"),
        (lambda: create_model_settings_schema(scratch_dsn), "0016"),
        (lambda: create_hints_schema(scratch_dsn), "0017"),
        (lambda: ensure_work_items_schema(scratch_dsn), "0018"),
        (lambda: ensure_receipts_schema(scratch_dsn), "0018"),
        (lambda: create_conflicts_schema(scratch_dsn), "0019"),
        (lambda: RevocationList(scratch_dsn).create_schema(), "0020"),
        # Memory lands on 0023 (0023_memory_rights), not 0002.
        (lambda: create_memory_schema(scratch_dsn), "0023"),
        # The room job queue lands on 0024 and the morning
        # notes on 0025; the takeover holds own the newest
        # migrations (0026_takeover_holds +
        # 0027_takeover_check_dedup) and its loader runs last.
        (lambda: create_queue_schema(scratch_dsn), "0024"),
        (lambda: create_morning_notes_schema(scratch_dsn), "0025"),
        (lambda: TakeoverStore(scratch_dsn).create_schema(), "0027"),
    ]
    for step, expected_max in steps:
        step()
        assert max(recorded()) == expected_max, (
            f"after {step}, expected max applied {expected_max}, got {sorted(recorded())}"
        )
    assert _tables(scratch_dsn) == EXPECTED_TABLES


# --- snapshot ----------------------------------------------------------------


def test_migrated_schema_matches_committed_snapshot(scratch_dsn):
    """Migrations on an empty DB dump to the committed snapshot."""
    assert SNAPSHOT_PATH.is_file(), "infra/schema_snapshot.sql is missing"
    apply_migrations(scratch_dsn)
    live = _normalize_dump(_pg_dump(scratch_dsn))
    committed = _normalize_dump(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    assert live == committed, (
        "migrated schema drifted from infra/schema_snapshot.sql:\n"
        f"  only live: {sorted((live - committed).elements())[:5]}\n"
        f"  only committed: {sorted((committed - live).elements())[:5]}\n"
        "Regenerate the snapshot (see its header) after a migration lands."
    )


# --- RLS on the migrated schema ----------------------------------------------


def test_rls_isolation_on_migrated_schema(scratch_dsn):
    """Two users, a team, a room: the app role sees only its own rows."""
    from puspa.accounts import rls
    from puspa.accounts.users import create_user

    apply_migrations(scratch_dsn)
    user_a = create_user("mig-a@example.com", "pw-a", dsn=scratch_dsn)
    user_b = create_user("mig-b@example.com", "pw-b", dsn=scratch_dsn)
    with psycopg.connect(scratch_dsn) as conn:
        conn.execute(
            "INSERT INTO vault (user_id, name, ciphertext) VALUES "
            "(%s, 'gmail_token', 'ct-a'), (%s, 'gmail_token', 'ct-b')",
            (user_a, user_b),
        )
        conn.execute(
            "INSERT INTO agents (agent_id, user_id) VALUES ('agent-a', %s), ('agent-b', %s)",
            (user_a, user_b),
        )
        conn.execute(
            "INSERT INTO teams (id, name, created_by) VALUES "
            "('33333333-3333-3333-3333-333333333333', 'team-a', %s)",
            (user_a,),
        )
        conn.execute(
            "INSERT INTO team_members (team_id, user_id, role) VALUES "
            "('33333333-3333-3333-3333-333333333333', %s, 'owner')",
            (user_a,),
        )
        conn.execute(
            "INSERT INTO rooms (id, name, created_by) VALUES "
            "('44444444-4444-4444-4444-444444444444', 'room-a', %s)",
            (user_a,),
        )
        conn.execute(
            "INSERT INTO room_members (room_id, user_id, role) VALUES "
            "('44444444-4444-4444-4444-444444444444', %s, 'owner')",
            (user_a,),
        )
        conn.commit()

    app_conn = psycopg.connect(scratch_dsn)
    app_conn.execute("SET ROLE puspa_app")
    app_conn.commit()
    try:
        with app_conn.transaction(), rls.as_user(app_conn, user_a):
            (vault_a,) = app_conn.execute("SELECT count(*) FROM vault").fetchone()
            (teams_a,) = app_conn.execute("SELECT count(*) FROM teams").fetchone()
            (rooms_a,) = app_conn.execute("SELECT count(*) FROM rooms").fetchone()
        assert (vault_a, teams_a, rooms_a) == (1, 1, 1)
        with app_conn.transaction(), rls.as_user(app_conn, user_b):
            (vault_b,) = app_conn.execute("SELECT count(*) FROM vault").fetchone()
            (teams_b,) = app_conn.execute("SELECT count(*) FROM teams").fetchone()
            (rooms_b,) = app_conn.execute("SELECT count(*) FROM rooms").fetchone()
        assert (vault_b, teams_b, rooms_b) == (1, 0, 0)
        # Fail-closed with no user set: nothing is visible.
        with app_conn.transaction():
            (vault_none,) = app_conn.execute("SELECT count(*) FROM vault").fetchone()
        assert vault_none == 0
    finally:
        app_conn.close()


# --- scanner: no table DDL in Python code --------------------------------------


def test_no_table_ddl_in_python_code():
    """Only the migration runner (bookkeeping table) and the SQLite
    state.db schema may hold DDL; every other .py file must not."""
    violations: list[str] = []
    for path in sorted((REPO_ROOT / "puspa").rglob("*.py")):
        rel = str(path.relative_to(REPO_ROOT))
        if "puspa/sessions/" in rel:
            continue  # SQLite state.db: rebuildable by design, not Postgres
        lines = [
            line
            for line in path.read_text(encoding="utf-8").splitlines()
            if not line.strip().startswith("#")
        ]
        text = "\n".join(lines)
        if rel == "puspa/migrations/runner.py":
            found = re.findall(r"CREATE TABLE", text, flags=re.IGNORECASE)
            assert found == ["CREATE TABLE"], (
                "the runner may hold exactly one CREATE TABLE: the schema_migrations bootstrap"
            )
            assert "schema_migrations" in text
            continue
        for statement in FORBIDDEN_DDL:
            if re.search(statement, text, flags=re.IGNORECASE):
                violations.append(f"{rel}: holds {statement!r}")
    assert not violations, "table DDL found in Python code (move it to a migration):\n" + "\n".join(
        violations
    )


def test_no_python_code_reads_schema_sql_files():
    """Only the migration runner reads .sql files (its own versions)."""
    violations = []
    for path in sorted((REPO_ROOT / "puspa").rglob("*.py")):
        rel = str(path.relative_to(REPO_ROOT))
        if rel.startswith("puspa/migrations/"):
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if ".sql" in line and ("read_text" in line or "open(" in line):
                violations.append(f"{rel}: {line.strip()}")
    assert not violations, "Python code outside puspa/migrations reads a .sql file:\n" + "\n".join(
        violations
    )


# --- dev Postgres compose file --------------------------------------------------


def test_dev_compose_uses_pinned_postgres_image():
    """docker-compose.dev.yml: pinned image, loopback port, healthcheck."""
    import yaml

    assert DEV_COMPOSE_PATH.is_file(), "docker-compose.dev.yml is missing"
    data = yaml.safe_load(DEV_COMPOSE_PATH.read_text(encoding="utf-8"))
    services = data["services"]
    assert "db" in services, "dev compose must define a db service"
    db = services["db"]
    image = db["image"]
    assert ":" in image, f"db image must be pinned with a tag: {image}"
    tag = image.rsplit(":", 1)[1]
    assert tag not in ("latest", "pg16", "16"), (
        f"db image tag must be an exact version, not {tag!r}"
    )
    assert image.startswith("pgvector/pgvector:"), (
        f"db image must be the pgvector image (episodic embeddings need it), got {image}"
    )
    ports = [str(p) for p in db.get("ports", [])]
    assert ports, "db must publish its port for local development"
    assert all(p.startswith("127.0.0.1:") for p in ports), (
        f"db ports must bind loopback only, got {ports}"
    )
    assert "healthcheck" in db, "db needs a healthcheck (pg_isready)"
    env = db.get("environment", {})
    assert env.get("POSTGRES_DB") == "puspa_test", (
        "dev database must be puspa_test (the name the suite expects)"
    )
