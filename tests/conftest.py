"""Shared fixtures for all Puspa tests (unified layout).

- `test_dsn`: test DSN (env PUSPA_TEST_DSN, else one SQLite file
  per pytest-xdist worker or per solo run, copied from a migrated
  template; a Postgres DSN in the env keeps the old per-worker
  database cloning instead).
- Autouse fixture: create all 7 schemas (accounts + ledger + agent +
  memory + runtime + graphs + workflow), then empty every table before
  and after each test.
- `vault_master_key` / `backup_key` / `email_link_secret`: ephemeral
  per-test keys.
- `ephemeral_keys`: a fresh Ed25519 key pair per test session.
- `tmp_skill_dir`: a temporary skill directory (unsigned SKILL.md).
- `make_user`: create a user (+ its mandatory Puspa agent). Returns a dict
  {"user_id", "email", "agen_id"}.
- `ledger`: a Ledger instance.
- `token_payloads`: agent + subagent token payloads (subset) for pool tests.
- `agen_kedua`: a second user-created agent (cross-agent procedure reuse).
- `izin_user_penuh`: a full example user permission set.
"""

import base64
import contextlib
import os
import secrets
import sys
from pathlib import Path

import pytest
from worker_db import (
    DEFAULT_SQLITE_PATH,
    create_sqlite_worker_db,
    create_worker_db,
    drop_run_sqlite_worker_dbs,
    drop_run_worker_dbs,
    drop_sqlite_worker_db,
    drop_worker_db,
    ensure_sqlite_template_path,
    ensure_template_dsn,
    new_run_id,
)

from puspa.db import connect as _db_connect
from puspa.db import is_sqlite_dsn

from puspa.graphs import create_schema as create_graphs_schema
from puspa.graphs import reset_tables as reset_graphs
from puspa.ledger import Ledger
from puspa.memory import create_schema as create_memory_schema
from puspa.runtime import create_schema as create_runtime_schema
from puspa.runtime import reset_tables as reset_runtime
from puspa.workflow.schema import create_schema as create_workflow_schema
from puspa.workflow.schema import reset_tables as reset_workflow

# Belt + braces: if a local nanobot clone exists (./nanobot), import from
# there; otherwise nanobot comes from the installed dependency (git pin
# in requirements.txt). The Path.exists() guard is what makes both paths
# work without changing code.
_NANOBOT_LOCAL = Path(__file__).resolve().parent.parent / "nanobot"
if _NANOBOT_LOCAL.exists():
    sys.path.insert(0, str(_NANOBOT_LOCAL))

DEFAULT_DSN = DEFAULT_SQLITE_PATH

# Per-worker databases (E-17, plan section 9.1; SQLite files since
# COW-10): one database per pytest-xdist worker, copied from a
# migrated template file (see tests/worker_db.py). The controller
# generates one run id per invocation and hands it to every worker;
# each worker copies the template into its own file before any
# fixture runs, and removes it at session end. A SOLO run (no -n)
# gets its own copy too (built lazily by the `test_dsn` fixture),
# so it never shares a file with another run. When PUSPA_TEST_DSN
# names a Postgres database, the old per-worker database cloning
# runs instead, exactly as before.
_RUN_ID_KEY = pytest.StashKey[str]()
_WORKER_DB_KEY = pytest.StashKey[tuple[str, str, str]]()
_CONTROLLER_RUN_KEY = pytest.StashKey[tuple[str, str]]()
_SOLO_DB_KEY = pytest.StashKey[str]()
_NEEDS_DB_KEY = pytest.StashKey[bool]()


def pytest_configure(config):
    """Set up per-worker databases for an xdist run (E-17)."""
    if hasattr(config, "workerinput"):
        # xdist worker: copy the template into a private file (or
        # clone the template database for a Postgres DSN) and point
        # this process at it before any fixture runs.
        run_id = str(config.workerinput.get("puspa_run_id", ""))
        worker_id = str(config.workerinput["workerid"])
        base_dsn = os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN)
        if is_sqlite_dsn(base_dsn):
            worker_dsn = create_sqlite_worker_db(base_dsn, worker_id, run_id)
        else:
            worker_dsn = create_worker_db(base_dsn, worker_id, run_id)
        os.environ["PUSPA_TEST_DSN"] = worker_dsn
        config.stash[_WORKER_DB_KEY] = (base_dsn, worker_id, run_id)
    elif getattr(config.option, "numprocesses", None):
        # xdist controller: one run id for every worker, and the
        # template ready before the first worker spawns.
        run_id = new_run_id()
        base_dsn = os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN)
        config.stash[_RUN_ID_KEY] = run_id
        config.stash[_CONTROLLER_RUN_KEY] = (base_dsn, run_id)
        if is_sqlite_dsn(base_dsn):
            ensure_sqlite_template_path()
        else:
            ensure_template_dsn(base_dsn)


def pytest_configure_node(node):
    """Hand the controller's run id to each worker (E-17)."""
    node.workerinput["puspa_run_id"] = node.config.stash.get(_RUN_ID_KEY, "")


def pytest_unconfigure(config):
    """Drop worker databases at session end (best effort, E-17).

    Workers drop their own database; the controller then drops
    every database of the run, because a worker's own drop
    sometimes never runs (xdist may SIGTERM a slow worker before
    its session ends). The controller runs after xdist tore the
    workers down, so no worker is alive to reconnect. A solo run
    removes its own copy (built by the `test_dsn` fixture).
    """
    worker = config.stash.get(_WORKER_DB_KEY, None)
    if worker is not None:
        base_dsn, worker_id, run_id = worker
        # Teardown must never fail the run.
        with contextlib.suppress(Exception):
            if is_sqlite_dsn(base_dsn):
                drop_sqlite_worker_db(os.environ.get("PUSPA_TEST_DSN", ""))
            else:
                drop_worker_db(base_dsn, worker_id, run_id)
    run = config.stash.get(_CONTROLLER_RUN_KEY, None)
    if run is not None:
        base_dsn, run_id = run
        with contextlib.suppress(Exception):
            if is_sqlite_dsn(base_dsn):
                drop_run_sqlite_worker_dbs(base_dsn, run_id)
            else:
                drop_run_worker_dbs(base_dsn, run_id)
    solo = config.stash.get(_SOLO_DB_KEY, None)
    if solo is not None:
        with contextlib.suppress(Exception):
            drop_sqlite_worker_db(solo)


def _uses_test_dsn(item) -> bool:
    """True when the test's own fixture closure needs the database.

    The walk starts from the fixtures the test function itself
    requests (its parameters, plus ``usefixtures`` markers, plus
    the module-level autouse fixtures that apply to it) and
    follows their dependencies. The two GLOBAL autouse fixtures
    (the per-test table reset and the role setup, both in this
    conftest) are deliberately NOT followed: they run for every
    test, so following them would classify everything as
    database-backed. A module-level autouse fixture that needs
    the database (for example the team/room schema fixtures in
    tests/teams_helpers.py and tests/rooms_helpers.py) IS
    followed, so the tests that run under it classify correctly.
    A test that touches the database without any fixture at all — a
    library call that reads ``PUSPA_TEST_DSN`` from the
    environment, like the session single-writer lock — is still
    misclassified as unit and fails loudly instead of silently
    sharing a database; those tests carry an explicit
    ``integration`` marker in their file instead.
    """
    import inspect

    info = getattr(item, "_fixtureinfo", None)
    if info is None:
        return True  # unknown: assume the database is needed
    func = getattr(item, "function", None)
    if func is None:
        return True
    wanted = set(inspect.signature(func).parameters)
    for marker in item.iter_markers("usefixtures"):
        wanted.update(marker.args)
    autouse = getattr(item.session._fixturemanager, "_getautousenames", None)
    if callable(autouse):
        wanted.update(
            name for name in autouse(item) if name not in {"_reset_tables", "_ensure_app_role"}
        )
    seen: set[str] = set()
    while wanted - seen:
        name = (wanted - seen).pop()
        seen.add(name)
        for fixturedef in info.name2fixturedefs.get(name, []):
            wanted.update(fixturedef.argnames)
    return "test_dsn" in seen or "scratch_dsn" in seen


def _test_needs_db(item) -> bool:
    """True when this test needs a live database."""
    if item.get_closest_marker("integration") is not None:
        return True
    if item.get_closest_marker("unit") is not None:
        return False
    return _uses_test_dsn(item)


def pytest_collection_modifyitems(session, config, items):
    """Split the markers (E-17, plan section 9.1).

    - ``live``: tests that need a real external service (a Docker
      daemon, a Chromium binary, a live model key). Marked
      explicitly in their files; they run in the nightly lane,
      never in a PR lane.
    - ``integration``: tests whose fixture closure needs Postgres.
    - ``unit``: everything else — no database, no network.

    Tests with no explicit marker are classified from their
    fixture closure. The split is recorded on the config in
    ``pytest_collection_finish`` (below) so the session fixtures
    can skip the database entirely when no collected test needs
    it (``pytest -m unit`` runs with no Postgres at all).
    """
    for item in items:
        markers = {marker.name for marker in item.iter_markers()}
        if not markers & {"unit", "integration", "live"}:
            if _uses_test_dsn(item):
                item.add_marker(pytest.mark.integration)
            else:
                item.add_marker(pytest.mark.unit)


def pytest_collection_finish(session):
    """Record whether any selected test needs the database (E-17).

    This runs after ``-m`` deselection, so the flag reflects the
    tests that will actually run — not the whole suite. (Computing
    it in ``pytest_collection_modifyitems`` reads the unfiltered
    list: a ``pytest -m unit`` run would still try to create the
    ``puspa_app`` role and fail when no database exists.)
    """
    session.config.stash[_NEEDS_DB_KEY] = any(_test_needs_db(item) for item in session.items)


MEMORY_TABLES = (
    "profile_facts",
    "episodic",
    "procedures",
    "slice_grants",
    "summaries",
    "style_examples",
    "onboarding",
    "data_consents",
    "used_email_links",
    "durable_memory",
    "narrative_journal",
    "turn_commit_retries",
    "memory_import_batches",
    "memory_import_items",
    "memory_import_quarantine",
    "memory_forget_list",
    "longterm_tiers",
)


@pytest.fixture(scope="session")
def test_dsn(pytestconfig):
    """The test DSN: env, worker file, or a fresh solo copy.

    An explicit ``PUSPA_TEST_DSN`` (SQLite path or Postgres DSN)
    always wins. Under xdist the worker file was already put in
    the env by ``pytest_configure``. A solo run builds its own
    copy from the migrated template here (lazily, so a
    ``pytest -m unit`` run never creates a database file) and
    removes it in ``pytest_unconfigure``.
    """
    dsn = os.environ.get("PUSPA_TEST_DSN")
    if dsn:
        return dsn
    if not pytestconfig.stash.get(_NEEDS_DB_KEY, True):
        return DEFAULT_DSN
    dsn = create_sqlite_worker_db(None, "solo", new_run_id())
    pytestconfig.stash[_SOLO_DB_KEY] = dsn
    # Publish it: library calls and CLIs that read PUSPA_TEST_DSN
    # from the environment (instead of taking the fixture) must
    # land on this same file, never on the Postgres default.
    os.environ["PUSPA_TEST_DSN"] = dsn
    return dsn


@pytest.fixture(scope="session", autouse=True)
def _ensure_app_role(test_dsn, pytestconfig):
    """Ensure the puspa_app role exists BEFORE any test runs.

    (create_schema below grants to puspa_app only when the role
    already exists, so the role has to exist first. Same as
    migration 0001_core.)

    The role used to be created in tests/test_rls.py, so a fresh
    database failed tests that ran alphabetically before it (the
    ledger tests use SET ROLE puspa_app). In conftest, the role is
    guaranteed before any test file runs.

    The CREATE is wrapped in an exception guard instead of a
    pre-check: under xdist, several workers create the role at the
    same time, and a check-then-create race would raise
    duplicate_object for the loser. Skipped entirely when no
    collected test needs the database (``pytest -m unit``), and
    skipped on SQLite (no roles there: ownership is enforced in
    code, D-SQLite-5).
    """
    if not pytestconfig.stash.get(_NEEDS_DB_KEY, True):
        return
    if is_sqlite_dsn(test_dsn):
        return
    with _db_connect(test_dsn) as conn:
        conn.execute(
            "DO $$ BEGIN "
            "CREATE ROLE puspa_app NOLOGIN; "
            "EXCEPTION WHEN duplicate_object THEN NULL; "
            "END $$"
        )
        conn.commit()


@pytest.fixture(autouse=True)
def _reset_tables(request, test_dsn):
    """Create schemas and empty every table before/after each test.

    Unit tests (marker ``unit``) never touch the database, so the
    whole setup is skipped for them: a ``pytest -m unit`` run
    needs no database at all. Every other test gets the schemas
    plus a clean slate, exactly as before.
    """
    if not _test_needs_db(request.node):
        yield
        return
    # The account / agent schemas live behind the SessionStore
    # port (puspa.accounts.ports), so these imports are lazy.
    from puspa.accounts.ports import create_account_schema, create_agent_schema

    create_account_schema(test_dsn)
    Ledger(test_dsn).create_schema()
    create_agent_schema(test_dsn)
    create_memory_schema(test_dsn)
    create_runtime_schema(test_dsn)
    create_graphs_schema(test_dsn)
    create_workflow_schema(test_dsn)
    with _db_connect(test_dsn) as conn:
        if is_sqlite_dsn(test_dsn):
            # SQLite has no TRUNCATE: the translator turns it into
            # DELETEs, and deleting `agents` cascades into
            # `agent_meta`, which fires the delete-guard trigger on
            # the mandatory Puspa row (Postgres TRUNCATE bypasses
            # DELETE triggers, so the wipe never met the guard
            # there). This reset is a test-only wipe, so it clears
            # the guard flag first; the guard still blocks
            # ordinary DELETEs of the Puspa row.
            conn.execute("UPDATE agent_meta SET required = 0 WHERE required")
        conn.execute(
            "TRUNCATE users, sessions, vault, agents, events, "
            + ", ".join(MEMORY_TABLES)
            + " CASCADE"
        )
        conn.commit()
    reset_runtime(test_dsn)
    reset_graphs(test_dsn)
    reset_workflow(test_dsn)
    yield
    with _db_connect(test_dsn) as conn:
        if is_sqlite_dsn(test_dsn):
            conn.execute("UPDATE agent_meta SET required = 0 WHERE required")
        conn.execute(
            "TRUNCATE users, sessions, vault, agents, events, "
            + ", ".join(MEMORY_TABLES)
            + " CASCADE"
        )
        conn.commit()
    reset_runtime(test_dsn)
    reset_graphs(test_dsn)
    reset_workflow(test_dsn)


@pytest.fixture
def vault_master_key(monkeypatch):
    """Ephemeral vault master key for this test only."""
    key = base64.urlsafe_b64encode(os.urandom(32)).decode()
    monkeypatch.setenv("PUSPA_VAULT_MASTER_KEY", key)
    return key


@pytest.fixture
def backup_key(monkeypatch):
    """Ephemeral Fernet backup key for this test only."""
    key = base64.urlsafe_b64encode(os.urandom(32)).decode()
    monkeypatch.setenv("PUSPA_BACKUP_KEY", key)
    return key


@pytest.fixture
def email_link_secret(monkeypatch):
    secret = secrets.token_urlsafe(32)
    monkeypatch.setenv("PUSPA_EMAIL_LINK_SECRET", secret)
    return secret


@pytest.fixture(scope="session")
def ephemeral_keys():
    """A fresh Ed25519 key pair per test session.

    Keys are NEVER written to a file; they only live in the test
    process memory. Returns (signing_key, public_key).
    """
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
    )

    signing_key = Ed25519PrivateKey.generate()
    return signing_key, signing_key.public_key()


@pytest.fixture
def tmp_skill_dir(tmp_path):
    """Temporary skill directory: SKILL.md + unsigned manifest.json."""
    import json

    skill_dir = tmp_path / "skill-contoh"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# Skill Contoh\n\nContoh.\n", encoding="utf-8")
    manifest = {
        "name": "example",
        "version": "0.1.0",
        "tools": ["read_price", "search_listings"],
        "domains": ["tokopedia.com"],
        "connectors": {"gmail": ["read"]},
        "author": "puspa",
    }
    (skill_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return skill_dir


@pytest.fixture
def make_user(test_dsn):
    """Factory: create a user (+ its mandatory Puspa agent). Returns a dict."""
    from puspa.accounts.ports import create_user
    from puspa.runtime.agents import ensure_puspa_agent

    counter = {"n": 0}

    def _make(email=None):
        counter["n"] += 1
        email = email or f"user{counter['n']}@contoh.id"
        user_id = create_user(email, secrets.token_urlsafe(24), dsn=test_dsn)
        agent_id = ensure_puspa_agent(user_id, dsn=test_dsn)
        return {"user_id": user_id, "email": email, "agent_id": agent_id}

    return _make


@pytest.fixture
def ledger(test_dsn):
    return Ledger(test_dsn)


@pytest.fixture
def token_payloads():
    """Agent + subagent token payloads (subset) for pool tests."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    signing_key = Ed25519PrivateKey.generate()
    import time

    now = int(time.time())
    agent = {
        "user_id": "user-1",
        "agent_id": "agen-1",
        "subagent_id": None,
        "tools": ["read", "tulis"],
        "domains": ["tokopedia.com"],
        "connectors": {"gmail": ["read"]},
        "budget": {"step_max": 25, "token_max": 100000, "rupiah_max": 2000},
        "exp": now + 3600,
        "iat": now,
        "parent": None,
    }
    sub = dict(
        agent,
        subagent_id="sub-1",
        tools=["read"],
        budget={"step_max": 15, "token_max": 20000, "rupiah_max": 1000},
        parent="agen-1",
    )
    return {"signing_key": signing_key, "agents": agent, "sub": sub}


@pytest.fixture
def agent_kedua(test_dsn):
    """A second user-created agent (for cross-agent procedure reuse)."""
    from puspa.runtime.agents import create_agent

    return lambda user_id: create_agent(user_id, "agen-kedua", dsn=test_dsn)


@pytest.fixture
def permission_user_penuh():
    return {
        "connectors": ["gmail", "calendar"],
        "scope": ["gmail.readonly", "gmail.compose", "calendar.events"],
        "domain": ["tokopedia.com", "shopee.co.id", "blibli.com", "site_provider_bill"],
        "spending_limit": 1000000,
    }
