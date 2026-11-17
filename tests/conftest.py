"""Fixture bersama tes Fase 0.

- `test_dsn`: DSN Postgres tes (env PUSPA_TEST_DSN atau default peer-auth).
- Fixture autouse: terapkan skema accounts lalu kosongkan tabel
  pengguna/sesi/vault sebelum dan sesudah tiap tes (isolasi antar tes).
- `vault_master_key`: master key vault ephemeral (acak per tes, hanya di
  env proses tes — tidak pernah ditulis ke file).

CATATAN koordinator: subagen lain (ledger/policy/...) kemungkinan menambah
fixture mereka sendiri di file ini (mis. `ephemeral_keys`, `tmp_skill_dir`
di CONTRACTS.md); gabungkan, jangan timpa.
"""

import base64
import os
import sys
from pathlib import Path

import psycopg
import pytest

from puspa.accounts import create_schema

# Sabuk + pengaman: kalau clone lokal nanobot ada (./nanobot), impor dari
# sana; kalau tidak ada (fresh clone), nanobot dipakai dari dependency
# yang terinstal (git pin di requirements.txt). Guard Path.exists()
# inilah yang membuat kedua jalur itu jalan tanpa mengubah kode.
_nanobot_local = Path(__file__).resolve().parent.parent / "nanobot"
if _nanobot_local.exists():
    sys.path.insert(0, str(_nanobot_local))

DEFAULT_DSN = "dbname=puspa_test user=root host=/var/run/postgresql"


@pytest.fixture(scope="session")
def test_dsn():
    return os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN)


@pytest.fixture(autouse=True)
def _reset_account_tables(test_dsn):
    create_schema(test_dsn)
    with psycopg.connect(test_dsn) as conn:
        conn.execute("TRUNCATE pengguna, sesi, vault CASCADE")
        conn.commit()
    yield
    with psycopg.connect(test_dsn) as conn:
        conn.execute("TRUNCATE pengguna, sesi, vault CASCADE")
        conn.commit()


@pytest.fixture
def vault_master_key(monkeypatch):
    """Master key vault ephemeral untuk tes ini saja."""
    key = base64.urlsafe_b64encode(os.urandom(32)).decode()
    monkeypatch.setenv("PUSPA_VAULT_MASTER_KEY", key)
    return key


@pytest.fixture(scope="session")
def ephemeral_keys():
    """Pasangan kunci Ed25519 baru per sesi tes (definisi 2.6).

    Kunci TIDAK PERNAH ditulis ke file; hanya hidup di memori proses
    tes. Kembalikan (signing_key, public_key).
    """
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
    )

    signing_key = Ed25519PrivateKey.generate()
    return signing_key, signing_key.public_key()


@pytest.fixture
def tmp_skill_dir(tmp_path):
    """Direktori skill sementara: SKILL.md + manifest.json unsigned."""
    import json

    skill_dir = tmp_path / "skill-contoh"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# Skill Contoh\n\nContoh.\n", encoding="utf-8")
    manifest = {
        "name": "contoh",
        "version": "0.1.0",
        "tools": ["baca_harga", "cari_listing"],
        "domains": ["tokopedia.com"],
        "konektor": {"gmail": ["baca"]},
        "author": "puspa",
    }
    (skill_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    return skill_dir
