"""Cadangan terenkripsi + uji restore (definisi 2.17, C126, gerbang 15).

- Dump Postgres (`pg_dump` format custom) -> enkripsi Fernet ->
  simpan di direktori DI LUAR database (di VM ini:
  `build/fase-1/backups/`; di VPS nanti: penyimpanan di luar VPS).
- Retensi 14 hari (angka awal); cadangan lebih tua dihapus otomatis.
- Uji restore ke DB bersih: `restore_backup` + verifikasi jumlah baris
  + rantai hash Buku Kerja. Uji restore pertama DIJALANKAN Fase 1
  (lihat STATUS_FASE1.md); lalu tiap 2 minggu.
- Blob (tangkapan layar) TIDAK dicadangkan (TTL 30 hari, revisi 7).
- Kunci cadangan hanya dari env `PUSPA_BACKUP_KEY` (base64 urlsafe,
  32 byte) — tidak ada kunci default, tidak disimpan di database.
"""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.fernet import Fernet

from ..memory.schema import resolve_dsn

RETENTION_DAYS = 14
BACKUP_PREFIX = "puspa-"
BACKUP_SUFFIX = ".dump.enc"


class BackupError(Exception):
    """Cadangan/restore gagal."""


def _backup_key():
    raw = os.environ.get("PUSPA_BACKUP_KEY")
    if not raw:
        raise BackupError("PUSPA_BACKUP_KEY tidak diset; cadangan menolak jalan tanpa kunci")
    try:
        key = base64.urlsafe_b64decode(raw.encode())
    except Exception as exc:
        raise BackupError("PUSPA_BACKUP_KEY bukan base64 urlsafe yang valid") from exc
    if len(key) != 32:
        raise BackupError("PUSPA_BACKUP_KEY harus 32 byte")
    return raw.encode()


def _fernet():
    return Fernet(_backup_key())


def _dsn_parts(dsn):
    """Pecah DSN `dbname=X user=Y host=Z` menjadi dict (tanpa password)."""
    parts = {}
    for token in (dsn or "").split():
        if "=" in token:
            key, value = token.split("=", 1)
            parts[key] = value
    return parts


def _pg_env(dsn):
    parts = _dsn_parts(dsn)
    env = dict(os.environ)
    if parts.get("host"):
        env["PGHOST"] = parts["host"]
    if parts.get("users"):
        env["PGUSER"] = parts["users"]
    if parts.get("port"):
        env["PGPORT"] = parts["port"]
    return env


def _db_name(dsn):
    return _dsn_parts(dsn).get("dbname", "puspa_test")


def create_backup(dsn=None, backup_dir=None, pg_dump_cmd="pg_dump", now=None):
    """Buat cadangan terenkripsi. Kembalikan dict {path, manifest, ...}."""
    dsn = resolve_dsn(dsn)
    backup_dir = Path(backup_dir or Path(__file__).resolve().parent.parent.parent / "backups")
    backup_dir.mkdir(parents=True, exist_ok=True)
    now = now or datetime.now(timezone.utc)
    stamp = now.strftime("%Y%m%d-%H%M%S")
    db = _db_name(dsn)
    path = backup_dir / f"{BACKUP_PREFIX}{stamp}{BACKUP_SUFFIX}"
    with tempfile.NamedTemporaryFile(suffix=".dump", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        proc = subprocess.run(
            [pg_dump_cmd, "-Fc", "-d", db, "-f", tmp_path],
            env=_pg_env(dsn), capture_output=True, text=True, timeout=300,
        )
        if proc.returncode != 0:
            raise BackupError(f"pg_dump gagal: {proc.stderr.strip()[:500]}")
        plaintext = Path(tmp_path).read_bytes()
        ciphertext = _fernet().encrypt(plaintext)
        path.write_bytes(ciphertext)
    finally:
        Path(tmp_path).unlink(missing_ok=True)
    manifest = {
        "file": path.name,
        "database": db,
        "created_at": now.isoformat(),
        "ukuran_terenkripsi": path.stat().st_size,
        "ukuran_dump": len(plaintext),
    }
    (backup_dir / (path.name + ".json")).write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    pruned = prune_backups(backup_dir)
    manifest["deleted_retensi"] = pruned
    return {"path": str(path), "manifest": manifest}


def list_backups(backup_dir=None):
    """Daftar cadangan (terbaru dulu) beserta manifestnya."""
    backup_dir = Path(backup_dir or Path(__file__).resolve().parent.parent.parent / "backups")
    out = []
    if not backup_dir.exists():
        return out
    for path in sorted(backup_dir.glob(f"{BACKUP_PREFIX}*{BACKUP_SUFFIX}"), reverse=True):
        manifest_path = Path(str(path) + ".json")
        manifest = (
            json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest_path.exists() else {"file": path.name}
        )
        out.append({"path": str(path), "manifest": manifest})
    return out


def prune_backups(backup_dir=None, retention_days=RETENTION_DAYS, now=None):
    """Hapus cadangan lebih tua dari retensi. Kembalikan nama yang dihapus."""
    backup_dir = Path(backup_dir or Path(__file__).resolve().parent.parent.parent / "backups")
    now = now or datetime.now(timezone.utc)
    batas = now - timedelta(days=retention_days)
    deleted = []
    if not backup_dir.exists():
        return deleted
    for path in backup_dir.glob(f"{BACKUP_PREFIX}*{BACKUP_SUFFIX}"):
        match = re.search(r"(\d{8})-(\d{6})", path.name)
        if not match:
            continue
        stamp = datetime.strptime(
            match.group(1) + match.group(2), "%Y%m%d%H%M%S"
        ).replace(tzinfo=timezone.utc)
        if stamp < batas:
            path.unlink(missing_ok=True)
            Path(str(path) + ".json").unlink(missing_ok=True)
            deleted.append(path.name)
    return deleted


def restore_backup(backup_path, target_dsn, pg_restore_cmd="pg_restore"):
    """Restore cadangan ke database target (harus sudah ada + kosong).

    Kembalikan dict {database, tabel}. Pemanggil bertanggung jawab
    membuat DB target kosong dulu (uji restore memakai DB bersih).
    """
    target_dsn = resolve_dsn(target_dsn)
    plaintext = _fernet().decrypt(Path(backup_path).read_bytes())
    with tempfile.NamedTemporaryFile(suffix=".dump", delete=False) as tmp:
        tmp.write(plaintext)
        tmp_path = tmp.name
    try:
        proc = subprocess.run(
            [pg_restore_cmd, "-d", _db_name(target_dsn), "--clean",
             "--if-exists", tmp_path],
            env=_pg_env(target_dsn), capture_output=True, text=True, timeout=300,
        )
        if proc.returncode != 0:
            raise BackupError(f"pg_restore gagal: {proc.stderr.strip()[:500]}")
    finally:
        Path(tmp_path).unlink(missing_ok=True)
    import psycopg

    with psycopg.connect(target_dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
                "ORDER BY tablename"
            )
            tabels = [r[0] for r in cur.fetchall()]
    return {"database": _db_name(target_dsn), "table": tabels}


def verify_restored_counts(target_dsn, tables):
    """Hitung baris tiap tabel di DB hasil restore (verifikasi isi)."""
    import psycopg

    out = {}
    with psycopg.connect(resolve_dsn(target_dsn)) as conn:
        with conn.cursor() as cur:
            for table in tables:
                cur.execute(f"SELECT COUNT(*) FROM {table}")
                out[table] = cur.fetchone()[0]
    return out
