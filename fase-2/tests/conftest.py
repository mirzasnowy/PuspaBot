"""Fixture bersama tes Fase 2.

- `sys.path`: direktori Fase 2 DULU, lalu Fase 1, lalu Fase 0 (paket
  `puspa` namespace, `extend_path`). JANGAN balik urutannya.
- Fixture autouse: terapkan skema Fase 0 (accounts + ledger + agen)
  + skema memori Fase 1 + skema Fase 2, lalu kosongkan semua tabel
  tiap tes.
- `make_user`: buat pengguna + agen Puspa-nya (Wajib, otomatis).
"""

import base64
import os
import secrets
import sys
from pathlib import Path

import psycopg
import pytest

_FASE2 = Path(__file__).resolve().parent.parent
_FASE1 = _FASE2.parent / "fase-1"
_FASE0 = _FASE2.parent / "fase-0"
if not _FASE0.is_dir():
    # Layout repo (GitHub): file Fase 0 ada di root repo, bukan di fase-0/.
    _FASE0 = _FASE2.parent
for _p in (str(_FASE0), str(_FASE1), str(_FASE2)):
    while _p in sys.path:
        sys.path.remove(_p)
sys.path.insert(0, str(_FASE0))
sys.path.insert(0, str(_FASE1))
sys.path.insert(0, str(_FASE2))

from puspa.accounts import create_schema as create_account_schema  # noqa: E402
from puspa.accounts.access import create_agent_schema, register_agent  # noqa: E402
from puspa.ledger import Ledger  # noqa: E402
from puspa.memory import create_schema as create_memory_schema  # noqa: E402
from puspa.runtime import create_schema as create_fase2_schema, reset_tables as reset_fase2  # noqa: E402

DEFAULT_DSN = "dbname=puspa_test user=root host=/var/run/postgresql"

TABLES_FASE1 = (
    "fakta_profil", "episodik", "prosedur", "pemberian_irisan",
    "ringkasan", "contoh_gaya", "onboarding", "persetujuan_data",
    "tautan_email_digunakan",
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
    create_fase2_schema(test_dsn)
    with psycopg.connect(test_dsn) as conn:
        conn.execute(
            "TRUNCATE pengguna, sesi, vault, agen, kejadian, "
            + ", ".join(TABLES_FASE1) + " CASCADE"
        )
        conn.commit()
    reset_fase2(test_dsn)
    yield
    with psycopg.connect(test_dsn) as conn:
        conn.execute(
            "TRUNCATE pengguna, sesi, vault, agen, kejadian, "
            + ", ".join(TABLES_FASE1) + " CASCADE"
        )
        conn.commit()
    reset_fase2(test_dsn)


@pytest.fixture
def vault_master_key(monkeypatch):
    key = base64.urlsafe_b64encode(os.urandom(32)).decode()
    monkeypatch.setenv("PUSPA_VAULT_MASTER_KEY", key)
    return key


@pytest.fixture
def make_user(test_dsn):
    """Factory: buat pengguna (+ agen Puspa wajib terdaftar)."""
    from puspa.accounts.users import create_user
    from puspa.runtime.agents import ensure_puspa_agent

    counter = {"n": 0}

    def _make(email=None):
        counter["n"] += 1
        email = email or f"user{counter['n']}@contoh.id"
        user_id = create_user(email, secrets.token_urlsafe(24), dsn=test_dsn)
        agen_id = ensure_puspa_agent(user_id, dsn=test_dsn)
        return {"user_id": user_id, "email": email, "agen_id": agen_id}

    return _make


@pytest.fixture
def ledger(test_dsn):
    return Ledger(test_dsn)


@pytest.fixture
def token_payloads():
    """Payload token agen + sub-agen (subset) untuk tes kolam."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    signing_key = Ed25519PrivateKey.generate()
    import time

    now = int(time.time())
    agen = {"user_id": "user-1", "agen_id": "agen-1", "sub_agen_id": None,
            "tools": ["baca", "tulis"], "domains": ["tokopedia.com"],
            "konektor": {"gmail": ["baca"]},
            "anggaran": {"langkah_max": 25, "token_max": 100000, "rupiah_max": 2000},
            "exp": now + 3600, "iat": now, "parent": None}
    sub = dict(agen, sub_agen_id="sub-1", tools=["baca"],
               anggaran={"langkah_max": 15, "token_max": 20000, "rupiah_max": 1000},
               parent="agen-1")
    return {"signing_key": signing_key, "agen": agen, "sub": sub}
