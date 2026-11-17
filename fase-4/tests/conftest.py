"""Fixture bersama tes Fase 4.

- `sys.path`: direktori Fase 4 DULU, lalu Fase 3, Fase 2, Fase 1, Fase 0
  (paket `puspa` namespace, `extend_path`). JANGAN balik urutannya.
- Fixture autouse: terapkan skema Fase 0 + memori Fase 1 + skema
  Fase 2 + skema Fase 3 + skema Fase 4, lalu kosongkan semua tabel tiap tes.
- `make_user`: buat pengguna + agen Puspa-nya.
- `izin_user_penuh`: izin user contoh (gmail + calendar + domain marketplace).
"""

import os
import sys
from pathlib import Path

import psycopg
import pytest

_FASE4 = Path(__file__).resolve().parent.parent
_FASE3 = _FASE4.parent / "fase-3"
_FASE2 = _FASE4.parent / "fase-2"
_FASE1 = _FASE4.parent / "fase-1"
_FASE0 = _FASE4.parent / "fase-0"
if not _FASE0.is_dir():
    # Layout repo (GitHub): file Fase 0 ada di root repo, bukan di fase-0/.
    _FASE0 = _FASE4.parent
for _p in (str(_FASE0), str(_FASE1), str(_FASE2), str(_FASE3), str(_FASE4)):
    while _p in sys.path:
        sys.path.remove(_p)
sys.path.insert(0, str(_FASE0))
sys.path.insert(0, str(_FASE1))
sys.path.insert(0, str(_FASE2))
sys.path.insert(0, str(_FASE3))
sys.path.insert(0, str(_FASE4))

from puspa.accounts import create_schema as create_account_schema  # noqa: E402
from puspa.accounts.access import create_agent_schema, register_agent  # noqa: E402
from puspa.graphs import create_schema as create_fase3_schema, reset_tables as reset_fase3  # noqa: E402
from puspa.ledger import Ledger  # noqa: E402
from puspa.memory import create_schema as create_memory_schema  # noqa: E402
from puspa.runtime import create_schema as create_fase2_schema, reset_tables as reset_fase2  # noqa: E402
from puspa.workflow.schema import create_schema as create_fase4_schema, reset_tables as reset_fase4  # noqa: E402

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
    create_fase3_schema(test_dsn)
    create_fase4_schema(test_dsn)
    with psycopg.connect(test_dsn) as conn:
        conn.execute(
            "TRUNCATE pengguna, sesi, vault, agen, kejadian, "
            + ", ".join(TABLES_FASE1) + " CASCADE"
        )
        conn.commit()
    reset_fase2(test_dsn)
    reset_fase3(test_dsn)
    reset_fase4(test_dsn)
    yield
    with psycopg.connect(test_dsn) as conn:
        conn.execute(
            "TRUNCATE pengguna, sesi, vault, agen, kejadian, "
            + ", ".join(TABLES_FASE1) + " CASCADE"
        )
        conn.commit()
    reset_fase2(test_dsn)
    reset_fase3(test_dsn)
    reset_fase4(test_dsn)


@pytest.fixture
def make_user(test_dsn):
    import secrets

    from puspa.accounts.users import create_user
    from puspa.runtime.agents import ensure_puspa_agent

    counter = {"n": 0}

    def _buat():
        counter["n"] += 1
        user_id = create_user(f"user{counter['n']}@contoh.id",
                              secrets.token_urlsafe(24), dsn=test_dsn)
        agen_id = ensure_puspa_agent(user_id, dsn=test_dsn)
        return user_id, agen_id

    return _buat


@pytest.fixture
def izin_user_penuh():
    return {
        "konektor": ["gmail", "calendar"],
        "scope": ["gmail.readonly", "gmail.compose", "calendar.events"],
        "domain": ["tokopedia.com", "shopee.co.id", "blibli.com",
                   "situs_provider_tagihan"],
        "batas_belanja": 1000000,
    }
