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

VONIS = ("Verified", "Uncertain", "Cannot be determined")
AMBANG_KEANDALAN = 0.80
EXCERPT_MAX = 200


class EvidenceError(Exception):
    """Rekaman bukti tidak valid."""


def _sekarang() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class EvidenceRecord:
    """Satu rekaman bukti (satu angka)."""

    evidence_id: str
    klaim: str
    value: float
    unit: str
    sumber_url: str
    time_take: datetime
    text_excerpt: str
    page_hash: str
    reader_id: str
    verifier_id: str | None = None
    vonis: str = "Uncertain"
    screenshot: str | None = None  # path blob, TTL 30 hari

    def summarize(self) -> dict:
        return {"evidence_id": self.evidence_id, "klaim": self.klaim, "value": self.value,
                "unit": self.unit, "sumber_url": self.sumber_url,
                "time_take": self.time_take.isoformat(),
                "text_excerpt": self.text_excerpt, "vonis": self.vonis}


def create_evidence_id() -> str:
    return f"bukti-{uuid.uuid4().hex[:8]}"


def record_evidence(*, task_id: str, klaim: str, value, unit: str,
                sumber_url: str, text_excerpt: str, halaman: str = "",
                reader_id: str, screenshot: str | None = None,
                time_take: datetime | None = None) -> EvidenceRecord:
    """Buat rekaman bukti (belum vonis — vonis diisi Pemeriksa).

    Validasi deterministik lapis 1: nilai bisa di-parse, satuan
    dikenal, domain sumber di allowlist, potongan dipangkas 200.
    """
    try:
        value_f = float(value)
    except (TypeError, ValueError) as exc:
        raise EvidenceError(f"nilai tidak bisa di-parse: {value!r}") from exc
    if unit not in ("Rp", "%", "kWh", "GB", "day", "buah", ""):
        raise EvidenceError(f"satuan tidak dikenal: {unit!r}")
    if not is_allowed(sumber_url):
        raise EvidenceError(f"domain sumber tidak di allowlist: {sumber_url!r}")
    excerpt = " ".join(str(text_excerpt).split())[:EXCERPT_MAX]
    return EvidenceRecord(
        evidence_id=create_evidence_id(), klaim=klaim, value=value_f, unit=unit,
        sumber_url=sumber_url, time_take=time_take or _sekarang(),
        text_excerpt=excerpt,
        page_hash=hashlib.sha256(halaman.encode("utf-8")).hexdigest() if halaman else "",
        reader_id=reader_id, screenshot=screenshot,
    )


def save_evidence(dsn: str, evidence: EvidenceRecord, user_id: str, task_id: str) -> None:
    """Simpan rekaman bukti ke Postgres (tabel `bukti` Fase 2)."""
    import os

    import psycopg

    dsn = dsn or os.environ.get("PUSPA_TEST_DSN", "dbname=puspa_test user=root host=/var/run/postgresql")
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO evidence (evidence_id, task_id, user_id, klaim, value, unit,"
                " sumber_url, time_take, text_excerpt, page_hash, screenshot,"
                " reader_id, verifier_id, vonis)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (evidence.evidence_id, task_id, user_id, evidence.klaim, evidence.value,
                 evidence.unit, evidence.sumber_url, evidence.time_take,
                 evidence.text_excerpt, evidence.page_hash, evidence.screenshot,
                 evidence.reader_id, evidence.verifier_id, evidence.vonis),
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
        return {"persen_verified": None, "amount": 0, "turunkan_vonis": False}
    persen = sum(1 for v in potong if v == "Verified") / len(potong)
    return {"persen_verified": round(persen * 100, 1), "amount": len(potong),
            "turunkan_vonis": persen < AMBANG_KEANDALAN}


def summary_vonis(evidence_list: list[EvidenceRecord]) -> str:
    """Ringkasan lencana bukti Kantor, mis. "5 terverifikasi, 1 belum pasti"."""
    hitung: dict[str, int] = {}
    for b in evidence_list:
        hitung[b.vonis] = hitung.get(b.vonis, 0) + 1
    bagian = []
    for vonis in ("Verified", "Uncertain", "Cannot be determined"):
        if hitung.get(vonis):
            bagian.append(f"{hitung[vonis]} {vonis.lower()}")
    return ", ".join(bagian) if bagian else "no evidence yet"
