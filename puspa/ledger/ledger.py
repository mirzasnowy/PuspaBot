"""Buku Kerja (definisi 2.3): log kejadian append-only berantai hash.

Satu-satunya sumber tulisan untuk semua yang terjadi di Puspa; chat, Kantor,
audit, bukti, biaya, dan graf adalah proyeksi dari log ini.

Catatan pemetaan isolasi pengguna (CONTRACTS.md): tabel `kejadian` tidak punya
`user_id`; isolasi pengguna lewat `agen_id`, yang terikat ke `user_id` pemilik
agen (pemetaan agen -> pemilik dicatat di paket `accounts`, fase 0).

Catatan urutan rantai: kontrak menulis "urut `id`", tapi `id` dibuat dengan
`gen_random_uuid()` sehingga tidak terurut menurut waktu sisip — mengurutkan
rantai hanya dengan `id` akan memutus rantai secara acak. Yang dipakai di sini
adalah urutan penyisipan, yaitu `(waktu, id)`: `waktu` dari `now()` di DB
(resolusi mikrodetik, praktis tidak pernah seri antar transaksi) dan `id`
sebagai penentu seri. Ini dicatat sebagai selisih dari kontrak untuk STATUS.md.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .errors import InvalidEventTypeError, LedgerError

#: Nilai `hash_sebelumnya` untuk kejadian pertama dalam satu tugas.
GENESIS = "GENESIS"

#: Peran aplikasi: hanya INSERT + SELECT pada tabel `kejadian`.
APP_ROLE = "puspa_app"

#: DSN default (peer auth sebagai user OS, tanpa password).
DEFAULT_DSN = "dbname=puspa_test user=root host=/var/run/postgresql"

#: Himpunan jenis kejadian tertutup — 31 jenis (definisi 2.3 revisi 7:
#: 22 jenis lama + 9 jenis baru alur/modul/pemicu/laporan/persetujuan).
JENIS_KEJADIAN = frozenset(
    {
        "tugas_dibuat",
        "rencana_dibuat",
        "subagen_dibuat",
        "handoff_diajukan",
        "handoff_diterima",
        "handoff_diserahkan",
        "handoff_ditolak",
        "handoff_kedaluwarsa",
        "detak",
        "tool_dipanggil",
        "tool_selesai",
        "bukti_dicatat",
        "pemeriksaan_selesai",
        "persetujuan_diminta",
        "persetujuan_diberikan",
        "persetujuan_ditolak",
        "pesan_user",
        "pesan_agen",
        "anggaran_habis",
        "tugas_selesai",
        "tugas_gagal",
        "tugas_dihentikan",
        "alur_diajukan",
        "alur_diaktifkan",
        "alur_dijeda",
        "pemicu_terpicu",
        "pemicu_terlewat",
        "modul_diajukan",
        "modul_diaktifkan",
        "laporan_salah",
        "persetujuan_data",
    }
)

#: Alias berbahasa Inggris untuk himpunan jenis tertutup.
EVENT_TYPES = JENIS_KEJADIAN


def canonical_json(obj) -> str:
    """JSON kanonis: kunci terurut, tanpa spasi, untuk hash yang stabil."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _waktu_iso(waktu: datetime) -> str:
    """Stempel waktu sebagai ISO-8601 UTC — kebal terhadap timezone sesi DB."""
    return waktu.astimezone(timezone.utc).isoformat()


def compute_event_hash(
    *,
    tugas_id: str,
    agen_id: str,
    sub_agen_id: str | None,
    jenis: str,
    isi: dict,
    id_induk: str | None,
    hash_sebelumnya: str,
    waktu: datetime,
) -> str:
    """sha256 dari JSON kanonis isi kejadian (tanpa `id`, yang acak)."""
    payload = {
        "tugas_id": tugas_id,
        "agen_id": agen_id,
        "sub_agen_id": sub_agen_id,
        "jenis": jenis,
        "isi": isi,
        "id_induk": id_induk,
        "hash_sebelumnya": hash_sebelumnya,
        "waktu_iso": _waktu_iso(waktu),
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _row_to_event(row: dict) -> dict:
    """Baris DB -> dict kejadian (UUID jadi string, `waktu` tetap datetime)."""
    return {
        "id": str(row["id"]),
        "waktu": row["waktu"],
        "tugas_id": row["tugas_id"],
        "agen_id": row["agen_id"],
        "sub_agen_id": row["sub_agen_id"],
        "jenis": row["jenis"],
        "isi": row["isi"],
        "id_induk": str(row["id_induk"]) if row["id_induk"] is not None else None,
        "hash_sebelumnya": row["hash_sebelumnya"],
        "hash": row["hash"],
    }


class Ledger:
    """Buku Kerja: append-only, rantai hash per tugas."""

    def __init__(self, dsn: str | None = None):
        self.dsn = dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN)

    def _connect(self, **kwargs):
        return psycopg.connect(self.dsn, row_factory=dict_row, **kwargs)

    def create_schema(self) -> None:
        """Menjalankan `schema.sql` (CREATE TABLE IF NOT EXISTS + REVOKE)."""
        sql = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
        with self._connect(autocommit=True) as conn:
            conn.execute(sql)

    def append_event(
        self,
        *,
        tugas_id: str,
        agen_id: str,
        sub_agen_id: str | None = None,
        jenis: str,
        isi: dict,
        id_induk: str | None = None,
    ) -> dict:
        """Menambah satu kejadian; kembalikan baris yang dibaca kembali dari DB.

        `waktu` diambil dari DB (`now()`) di transaksi yang sama dengan INSERT,
        jadi nilai yang masuk hash sama persis dengan yang tersimpan.
        `hash_sebelumnya` = hash kejadian sebelumnya dalam tugas yang sama
        (urut penyisipan), atau "GENESIS" untuk kejadian pertama.
        """
        if not isinstance(tugas_id, str) or not tugas_id:
            raise LedgerError("tugas_id harus string tak kosong")
        if not isinstance(agen_id, str) or not agen_id:
            raise LedgerError("agen_id harus string tak kosong")
        if jenis not in JENIS_KEJADIAN:
            raise InvalidEventTypeError(jenis)
        if not isinstance(isi, dict):
            raise LedgerError("isi harus dict")
        try:
            canonical_json(isi)
        except (TypeError, ValueError) as exc:
            raise LedgerError(f"isi tidak bisa dijadikan JSON: {exc}") from exc
        if id_induk is not None:
            try:
                UUID(str(id_induk))
            except ValueError as exc:
                raise LedgerError(f"id_induk bukan UUID: {id_induk!r}") from exc

        with self._connect() as conn:
            with conn.cursor() as cur:
                # Kunci per tugas: dua append bersamaan tidak boleh bercabang.
                cur.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(%s))", (tugas_id,)
                )
                cur.execute("SELECT now() AS waktu")
                waktu = cur.fetchone()["waktu"]
                cur.execute(
                    "SELECT hash FROM kejadian WHERE tugas_id = %s "
                    "ORDER BY waktu DESC, id DESC LIMIT 1",
                    (tugas_id,),
                )
                prev = cur.fetchone()
                hash_sebelumnya = prev["hash"] if prev else GENESIS
                event_hash = compute_event_hash(
                    tugas_id=tugas_id,
                    agen_id=agen_id,
                    sub_agen_id=sub_agen_id,
                    jenis=jenis,
                    isi=isi,
                    id_induk=str(id_induk) if id_induk is not None else None,
                    hash_sebelumnya=hash_sebelumnya,
                    waktu=waktu,
                )
                cur.execute(
                    "INSERT INTO kejadian "
                    "(waktu, tugas_id, agen_id, sub_agen_id, jenis, isi, "
                    " id_induk, hash_sebelumnya, hash) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
                    "RETURNING id",
                    (
                        waktu,
                        tugas_id,
                        agen_id,
                        sub_agen_id,
                        jenis,
                        Jsonb(isi),
                        id_induk,
                        hash_sebelumnya,
                        event_hash,
                    ),
                )
                event_id = cur.fetchone()["id"]
            conn.commit()

        # Baca kembali dari DB (waktu dari DB, bukan dari memori).
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM kejadian WHERE id = %s", (event_id,))
                row = cur.fetchone()
        if row is None:  # tidak mungkin terjadi; pertahanan terakhir
            raise LedgerError(f"kejadian {event_id} hilang setelah INSERT")
        return _row_to_event(row)

    def events(self, tugas_id: str, jenis: str | None = None) -> list[dict]:
        """Semua kejadian satu tugas, urut penyisipan; opsional saring `jenis`."""
        if jenis is not None and jenis not in JENIS_KEJADIAN:
            raise InvalidEventTypeError(jenis)
        with self._connect() as conn:
            with conn.cursor() as cur:
                if jenis is None:
                    cur.execute(
                        "SELECT * FROM kejadian WHERE tugas_id = %s "
                        "ORDER BY waktu ASC, id ASC",
                        (tugas_id,),
                    )
                else:
                    cur.execute(
                        "SELECT * FROM kejadian WHERE tugas_id = %s AND jenis = %s "
                        "ORDER BY waktu ASC, id ASC",
                        (tugas_id, jenis),
                    )
                rows = cur.fetchall()
        return [_row_to_event(row) for row in rows]

    def verify_chain(self, tugas_id: str) -> tuple[bool, list[str]]:
        """Periksa rantai hash satu tugas.

        Kembalikan (True, []) kalau utuh, atau (False, [id kejadian yang
        putus]) — putus karena `hash_sebelumnya` tidak cocok dengan hash
        kejadian sebelumnya, atau hash yang dihitung ulang tidak cocok dengan
        hash tersimpan (isi/waktu/field lain diubah diam-diam).
        """
        rows = self.events(tugas_id)
        broken: list[str] = []
        expected_prev = GENESIS
        for ev in rows:
            recomputed = compute_event_hash(
                tugas_id=ev["tugas_id"],
                agen_id=ev["agen_id"],
                sub_agen_id=ev["sub_agen_id"],
                jenis=ev["jenis"],
                isi=ev["isi"],
                id_induk=ev["id_induk"],
                hash_sebelumnya=ev["hash_sebelumnya"],
                waktu=ev["waktu"],
            )
            if ev["hash_sebelumnya"] != expected_prev or recomputed != ev["hash"]:
                broken.append(ev["id"])
            # Tautan berikutnya memakai hash TERSIMPAN, supaya satu kejadian
            # yang diubah hanya menandai dirinya sendiri, bukan seluruh sisa
            # rantai.
            expected_prev = ev["hash"]
        return (not broken, broken)
