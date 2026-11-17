"""Shared fixtures for all Puspa tests (unified layout).

- `test_dsn`: Postgres test DSN (env PUSPA_TEST_DSN or default peer-auth).
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
import os
import secrets
import sys
from pathlib import Path

import psycopg
import pytest

from puspa.accounts import create_schema as create_account_schema  # noqa: E402
from puspa.accounts.access import create_agent_schema  # noqa: E402
from puspa.graphs import create_schema as create_graphs_schema, reset_tables as reset_graphs  # noqa: E402
from puspa.ledger import Ledger  # noqa: E402
from puspa.memory import create_schema as create_memory_schema  # noqa: E402
from puspa.runtime import create_schema as create_runtime_schema, reset_tables as reset_runtime  # noqa: E402
from puspa.workflow.schema import create_schema as create_workflow_schema, reset_tables as reset_workflow  # noqa: E402

# Belt + braces: if a local nanobot clone exists (./nanobot), import from
# there; otherwise nanobot comes from the installed dependency (git pin
# in requirements.txt). The Path.exists() guard is what makes both paths
# work without changing code.
_NANOBOT_LOCAL = Path(__file__).resolve().parent.parent / "nanobot"
if _NANOBOT_LOCAL.exists():
    sys.path.insert(0, str(_NANOBOT_LOCAL))

DEFAULT_DSN = "dbname=puspa_test user=root host=/var/run/postgresql"

MEMORY_TABLES = (
    "profile_facts", "episodic", "procedures", "slice_grants",
    "summaries", "style_examples", "onboarding", "data_consents",
    "used_email_links",
)


@pytest.fixture(scope="session")
def test_dsn():
    return os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN)


@pytest.fixture(autouse=True)
def _reset_tables(test_dsn):
    create_account_schema(test_dsn)
    Ledger(test_dsn).create_schema()
    create_agent_schema(test_dsn)
    create_memory_schema(test_dsn)
    create_runtime_schema(test_dsn)
    create_graphs_schema(test_dsn)
    create_workflow_schema(test_dsn)
    with psycopg.connect(test_dsn) as conn:
        conn.execute(
            "TRUNCATE users, sessions, vault, agents, events, "
            + ", ".join(MEMORY_TABLES) + " CASCADE"
        )
        conn.commit()
    reset_runtime(test_dsn)
    reset_graphs(test_dsn)
    reset_workflow(test_dsn)
    yield
    with psycopg.connect(test_dsn) as conn:
        conn.execute(
            "TRUNCATE users, sessions, vault, agents, events, "
            + ", ".join(MEMORY_TABLES) + " CASCADE"
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
    (skill_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    return skill_dir


@pytest.fixture
def make_user(test_dsn):
    """Factory: create a user (+ its mandatory Puspa agent). Returns a dict."""
    from puspa.accounts.users import create_user
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
    agent = {"user_id": "user-1", "agent_id": "agen-1", "subagent_id": None,
            "tools": ["read", "tulis"], "domains": ["tokopedia.com"],
            "connectors": {"gmail": ["read"]},
            "anggaran": {"step_max": 25, "token_max": 100000, "rupiah_max": 2000},
            "exp": now + 3600, "iat": now, "parent": None}
    sub = dict(agent, subagent_id="sub-1", tools=["read"],
               anggaran={"step_max": 15, "token_max": 20000, "rupiah_max": 1000},
               parent="agen-1")
    return {"signing_key": signing_key, "agents": agent, "sub": sub}


@pytest.fixture
def agent_kedua(test_dsn):
    """A second user-created agent (for cross-agent procedure reuse)."""
    from puspa.runtime.agents import create_agent

    return create_agent("agen-kedua", dsn=test_dsn)


@pytest.fixture
def permission_user_penuh():
    return {
        "connectors": ["gmail", "calendar"],
        "scope": ["gmail.readonly", "gmail.compose", "calendar.events"],
        "domain": ["tokopedia.com", "shopee.co.id", "blibli.com",
                   "situs_provider_bill"],
        "batas_belanja": 1000000,
    }
