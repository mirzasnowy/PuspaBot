"""Fixture bersama tes Fase 1.

- `sys.path`: direktori Fase 1 DULU (paket `puspa` Fase 1 dengan
  `extend_path`), lalu direktori Fase 0 (subpaket `puspa.ledger`,
  `puspa.accounts`, ...). JANGAN balik urutannya.
- Fixture autouse: terapkan skema Fase 0 (accounts + ledger + agen)
  + skema memori Fase 1, lalu kosongkan semua tabel tiap tes.
- `vault_master_key` / `backup_key`: kunci ephemeral per tes.
- `make_user`: buat pengguna + daftarkan agen Puspa-nya.
"""

import base64
import os
import secrets
import sys
from pathlib import Path

import psycopg
import pytest

_FASE1 = Path(__file__).resolve().parent.parent
_FASE0 = _FASE1.parent / "fase-0"
if not _FASE0.is_dir():
    # Layout repo (GitHub): file Fase 0 ada di root repo, bukan di fase-0/.
    _FASE0 = _FASE1.parent
# Tanpa guard `if not in sys.path`: pytest sudah menaruh direktori
# fase-1 di sys.path (posisi tengah) — guard akan melewatkan insert
# dan fase-0 yang di-insert ke 0 akan menang saat `import puspa`.
# Urutan akhir WAJIB: fase-1 dulu, lalu fase-0.
for _p in (str(_FASE0), str(_FASE1)):
    while _p in sys.path:
        sys.path.remove(_p)
sys.path.insert(0, str(_FASE0))
sys.path.insert(0, str(_FASE1))

from puspa.accounts import create_schema as create_account_schema  # noqa: E402
from puspa.accounts.access import create_agent_schema, register_agent  # noqa: E402
from puspa.ledger import Ledger  # noqa: E402
from puspa.memory import create_schema as create_memory_schema  # noqa: E402

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
    with psycopg.connect(test_dsn) as conn:
        conn.execute(
            "TRUNCATE pengguna, sesi, vault, agen, kejadian, "
            + ", ".join(TABLES_FASE1) + " CASCADE"
        )
        conn.commit()
    yield
    with psycopg.connect(test_dsn) as conn:
        conn.execute(
            "TRUNCATE pengguna, sesi, vault, agen, kejadian, "
            + ", ".join(TABLES_FASE1) + " CASCADE"
        )
        conn.commit()


@pytest.fixture
def vault_master_key(monkeypatch):
    """Master key vault ephemeral untuk tes ini saja."""
    key = base64.urlsafe_b64encode(os.urandom(32)).decode()
    monkeypatch.setenv("PUSPA_VAULT_MASTER_KEY", key)
    return key


@pytest.fixture
def backup_key(monkeypatch):
    """Kunci cadangan Fernet ephemeral untuk tes ini saja."""
    key = base64.urlsafe_b64encode(os.urandom(32)).decode()
    monkeypatch.setenv("PUSPA_BACKUP_KEY", key)
    return key


@pytest.fixture
def email_link_secret(monkeypatch):
    secret = secrets.token_urlsafe(32)
    monkeypatch.setenv("PUSPA_EMAIL_LINK_SECRET", secret)
    return secret


@pytest.fixture
def make_user(test_dsn):
    """Factory: buat pengguna (+ agen Puspa terdaftar). Kembalikan dict."""
    from puspa.accounts.users import create_user

    counter = {"n": 0}

    def _make(email=None):
        counter["n"] += 1
        email = email or f"user{counter['n']}@contoh.id"
        user_id = create_user(email, secrets.token_urlsafe(24), dsn=test_dsn)
        agen_id = str(__import__("uuid").uuid4())
        register_agent(agen_id, user_id, dsn=test_dsn)
        return {"user_id": user_id, "email": email, "agen_id": agen_id}

    return _make


@pytest.fixture
def ledger(test_dsn):
    return Ledger(test_dsn)
