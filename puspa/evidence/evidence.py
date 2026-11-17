"""Buku Bukti + Pemeriksa + renderer angka yatim (§2.5 revisi 7).

- Satu rekaman bukti per angka: bukti_id, klaim, nilai, satuan,
  sumber_url, waktu_ambil, potongan_teks (maks 200 karakter),
  hash_halaman, tangkapan_layar (TTL 30 hari), id_pembaca,
  id_pemeriksa, vonis.
- Vonis tiga tingkat: Terverifikasi / Belum pasti / Tidak bisa
  dipastikan — ditampilkan apa adanya.
- Skor Keandalan per sumber: % vonis Terverifikasi dari 50 pembacaan
  terakhir + umur pembacaan terakhir. Sumber di bawah 80% menurunkan
  semua hasilnya maksimal Belum pasti (§3.4).
"""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from puspa.sandbox.allowlist import is_allowed

VONIS = ("Terverifikasi", "Belum pasti", "Tidak bisa dipastikan")
AMBANG_KEANDALAN = 0.80
POTONGAN_MAX = 200


class EvidenceError(Exception):
    """Rekaman bukti tidak valid."""


def _sekarang() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class EvidenceRecord:
    """Satu rekaman bukti (satu angka)."""

    bukti_id: str
    klaim: str
    nilai: float
    satuan: str
    sumber_url: str
    waktu_ambil: datetime
    potongan_teks: str
    hash_halaman: str
    id_pembaca: str
    id_pemeriksa: str | None = None
    vonis: str = "Belum pasti"
    tangkapan_layar: str | None = None  # path blob, TTL 30 hari

    def ringkas(self) -> dict:
        return {"bukti_id": self.bukti_id, "klaim": self.klaim, "nilai": self.nilai,
                "satuan": self.satuan, "sumber_url": self.sumber_url,
                "waktu_ambil": self.waktu_ambil.isoformat(),
                "potongan_teks": self.potongan_teks, "vonis": self.vonis}


def buat_bukti_id() -> str:
    return f"bukti-{uuid.uuid4().hex[:8]}"


def catat_bukti(*, tugas_id: str, klaim: str, nilai, satuan: str,
                sumber_url: str, potongan_teks: str, halaman: str = "",
                id_pembaca: str, tangkapan_layar: str | None = None,
                waktu_ambil: datetime | None = None) -> EvidenceRecord:
    """Buat rekaman bukti (belum vonis — vonis diisi Pemeriksa).

    Validasi deterministik lapis 1: nilai bisa di-parse, satuan
    dikenal, domain sumber di allowlist, potongan dipangkas 200.
    """
    try:
        nilai_f = float(nilai)
    except (TypeError, ValueError) as exc:
        raise EvidenceError(f"nilai tidak bisa di-parse: {nilai!r}") from exc
    if satuan not in ("Rp", "%", "kWh", "GB", "hari", "buah", ""):
        raise EvidenceError(f"satuan tidak dikenal: {satuan!r}")
    if not is_allowed(sumber_url):
        raise EvidenceError(f"domain sumber tidak di allowlist: {sumber_url!r}")
    potongan = " ".join(str(potongan_teks).split())[:POTONGAN_MAX]
    return EvidenceRecord(
        bukti_id=buat_bukti_id(), klaim=klaim, nilai=nilai_f, satuan=satuan,
        sumber_url=sumber_url, waktu_ambil=waktu_ambil or _sekarang(),
        potongan_teks=potongan,
        hash_halaman=hashlib.sha256(halaman.encode("utf-8")).hexdigest() if halaman else "",
        id_pembaca=id_pembaca, tangkapan_layar=tangkapan_layar,
    )


def simpan_bukti(dsn: str, bukti: EvidenceRecord, user_id: str, tugas_id: str) -> None:
    """Simpan rekaman bukti ke Postgres (tabel `bukti` Fase 2)."""
    import os

    import psycopg

    dsn = dsn or os.environ.get("PUSPA_TEST_DSN", "dbname=puspa_test user=root host=/var/run/postgresql")
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO bukti (bukti_id, tugas_id, user_id, klaim, nilai, satuan,"
                " sumber_url, waktu_ambil, potongan_teks, hash_halaman, tangkapan_layar,"
                " id_pembaca, id_pemeriksa, vonis)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (bukti.bukti_id, tugas_id, user_id, bukti.klaim, bukti.nilai,
                 bukti.satuan, bukti.sumber_url, bukti.waktu_ambil,
                 bukti.potongan_teks, bukti.hash_halaman, bukti.tangkapan_layar,
                 bukti.id_pembaca, bukti.id_pemeriksa, bukti.vonis),
            )
        conn.commit()


def skor_keandalan(vonis_terakhir: list[str]) -> dict:
    """Skor Keandalan sumber dari N pembacaan terakhir (maks 50).

    Kembalikan {"persen_terverifikasi": x, "jumlah": n,
    "turunkan_vonis": bool}. Sumber di bawah 80% -> semua hasil
    maksimal Belum pasti.
    """
    potong = vonis_terakhir[-50:]
    if not potong:
        return {"persen_terverifikasi": None, "jumlah": 0, "turunkan_vonis": False}
    persen = sum(1 for v in potong if v == "Terverifikasi") / len(potong)
    return {"persen_terverifikasi": round(persen * 100, 1), "jumlah": len(potong),
            "turunkan_vonis": persen < AMBANG_KEANDALAN}


def ringkasan_vonis(bukti_list: list[EvidenceRecord]) -> str:
    """Ringkasan lencana bukti Kantor, mis. "5 terverifikasi, 1 belum pasti"."""
    hitung: dict[str, int] = {}
    for b in bukti_list:
        hitung[b.vonis] = hitung.get(b.vonis, 0) + 1
    bagian = []
    for vonis in ("Terverifikasi", "Belum pasti", "Tidak bisa dipastikan"):
        if hitung.get(vonis):
            bagian.append(f"{hitung[vonis]} {vonis.lower()}")
    return ", ".join(bagian) if bagian else "belum ada bukti"
