"""Kontrak handoff + mesin status (§2.4 revisi 7).

Kontrak handoff (skema tetap, 10 field): dari, ke, tujuan, masukan
(ber-skema), keluaran_diharapkan (skema), batas_waktu, anggaran
(langkah + token), kelas_izin, definisi_selesai, kunci_idempotensi.

Mesin status: diajukan -> diterima -> berjalan -> diserahkan ->
lolos / ditolak. Cabang gagal: kedaluwarsa, 1 percobaan ulang, lapor
ke user. Keluaran yang tidak lolos skema = ditolak (tidak ada
penerimaan setengah-setengah).

Detak: sub-agen mengirim detak tiap 15 detik; tanpa detak 30 detik
atau lewat batas waktu = macet. Batas waktu default 45 detik per
percobaan. Handoff antar-agen: maks 3 per tugas, lingkaran (A ke B
ke A) ditolak kode.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

KELAS_IZIN = ("Pembaca", "Pelaksana", "Pemeriksa")
STATUS_AWAL = "diajukan"
STATUS_AKHIR = ("lolos", "ditolak", "kedaluwarsa")
BATAS_WAKTU_DEFAULT_DETIK = 45
DETAK_DETIK = 15
MACET_DETIK = 30
MAX_PERCOBAAN = 2  # 1 percobaan awal + 1 percobaan ulang
MAX_HANDOFF_ANTAR_AGEN = 3


class HandoffError(Exception):
    """Kontrak handoff tidak valid / transisi status tidak sah."""


def _sekarang() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class HandoffContract:
    """Satu kontrak handoff; divalidasi penuh saat dibuat."""

    dari_id: str
    ke_id: str
    tujuan: str
    masukan: dict
    keluaran_diharapkan: dict
    batas_waktu_detik: int = BATAS_WAKTU_DEFAULT_DETIK
    anggaran: dict = field(default_factory=lambda: {"langkah_max": 15, "token_max": 20000, "rupiah_max": 2000})
    kelas_izin: str = "Pembaca"
    definisi_selesai: str = ""
    kunci_idempotensi: str = ""

    def __post_init__(self) -> None:
        if not self.dari_id or not self.ke_id:
            raise HandoffError("dari dan ke wajib diisi")
        if self.dari_id == self.ke_id:
            raise HandoffError("handoff ke diri sendiri ditolak")
        if not self.tujuan.strip():
            raise HandoffError("tujuan tidak boleh kosong")
        if not isinstance(self.masukan, dict) or "jenis" not in self.masukan:
            raise HandoffError("masukan harus dict ber-skema dengan kunci 'jenis'")
        if not isinstance(self.keluaran_diharapkan, dict) or "jenis" not in self.keluaran_diharapkan:
            raise HandoffError("keluaran_diharapkan harus dict ber-skema dengan kunci 'jenis'")
        if not isinstance(self.batas_waktu_detik, int) or self.batas_waktu_detik <= 0:
            raise HandoffError("batas_waktu harus detik positif")
        if self.kelas_izin not in KELAS_IZIN:
            raise HandoffError(f"kelas_izin harus salah satu dari {KELAS_IZIN}")
        if not self.definisi_selesai.strip():
            raise HandoffError("definisi_selesai tidak boleh kosong")
        if not self.kunci_idempotensi:
            self.kunci_idempotensi = f"handoff-{uuid.uuid4().hex}"
        anggaran = self.anggaran
        for kunci in ("langkah_max", "token_max", "rupiah_max"):
            if kunci not in anggaran or not isinstance(anggaran[kunci], (int, float)):
                raise HandoffError(f"anggaran.{kunci} wajib angka")


@dataclass
class Handoff:
    """Satu handoff yang sedang berjalan (mesin status + detak)."""

    kontrak: HandoffContract
    id: str = field(default_factory=lambda: f"handoff-{uuid.uuid4().hex[:12]}")
    status: str = STATUS_AWAL
    percobaan: int = 1
    detak_terakhir: datetime | None = None
    dibuat: datetime = field(default_factory=_sekarang)
    hasil: dict | None = None
    alasan: str = ""

    # -- transisi -----------------------------------------------------
    def terima(self) -> None:
        self._harus_status("diajukan")
        self.status = "diterima"

    def mulai(self) -> None:
        self._harus_status("diterima")
        self.status = "berjalan"
        self.detak_terakhir = _sekarang()

    def detak(self, waktu: datetime | None = None) -> None:
        if self.status != "berjalan":
            raise HandoffError(f"detak hanya saat berjalan (status: {self.status})")
        self.detak_terakhir = waktu or _sekarang()

    def serahkan(self, hasil: dict) -> str:
        """Serahkan hasil; kembalikan 'lolos' atau 'ditolak'.

        Hasil harus dict ber-skema dengan kunci 'jenis' yang sama
        dengan `keluaran_diharapkan['jenis']`; kalau tidak, ditolak.
        """
        self._harus_status("berjalan")
        self.hasil = hasil
        if not isinstance(hasil, dict) or hasil.get("jenis") != self.kontrak.keluaran_diharapkan["jenis"]:
            self.status = "ditolak"
            self.alasan = "keluaran tidak lolos skema"
            return "ditolak"
        self.status = "diserahkan"
        return "diserahkan"

    def nyatakan_lolos(self) -> None:
        self._harus_status("diserahkan")
        self.status = "lolos"

    def tolak(self, alasan: str) -> None:
        if self.status in STATUS_AKHIR:
            raise HandoffError(f"handoff sudah berakhir ({self.status})")
        self.status = "ditolak"
        self.alasan = alasan

    def coba_ulang(self) -> bool:
        """Satu percobaan ulang. False bila sudah habis (lapor user)."""
        if self.percobaan >= MAX_PERCOBAAN:
            return False
        self.percobaan += 1
        self.status = "diajukan"
        self.hasil = None
        self.alasan = ""
        return True

    # -- macet ---------------------------------------------------------
    def macet(self, waktu: datetime | None = None) -> bool:
        """True bila tanpa detak 30 detik atau lewat batas waktu."""
        sekarang = waktu or _sekarang()
        if self.status not in ("diterima", "berjalan"):
            return False
        if self.detak_terakhir is None:
            return (sekarang - self.dibuat).total_seconds() > MACET_DETIK
        if (sekarang - self.detak_terakhir).total_seconds() > MACET_DETIK:
            return True
        return (sekarang - self.dibuat).total_seconds() > self.kontrak.batas_waktu_detik

    def tandai_kedaluwarsa(self) -> None:
        if self.status in STATUS_AKHIR:
            raise HandoffError(f"handoff sudah berakhir ({self.status})")
        self.status = "kedaluwarsa"
        self.alasan = "lewat batas waktu atau tanpa detak"

    def _harus_status(self, diharapkan: str) -> None:
        if self.status != diharapkan:
            raise HandoffError(
                f"transisi tidak sah: status {self.status!r}, diharapkan {diharapkan!r}"
            )


def tolak_lingkaran(jalur: list[str], dari_id: str, ke_id: str) -> None:
    """Tolak handoff antar-agen yang membentuk lingkaran (A ke B ke A).

    `jalur` = urutan agen yang sudah dilewati tugas ini. Kalau `ke_id`
    sudah ada di jalur, handoff itu kembali ke agen sebelumnya.
    """
    if ke_id in jalur:
        raise HandoffError(f"handoff melingkar ditolak: {ke_id!r} sudah di jalur {jalur}")


def cek_batas_handoff_antar_agen(jumlah_sejauh_ini: int) -> None:
    """Maks 3 handoff antar-agen per tugas."""
    if jumlah_sejauh_ini >= MAX_HANDOFF_ANTAR_AGEN:
        raise HandoffError(f"maks {MAX_HANDOFF_ANTAR_AGEN} handoff antar-agen per tugas")
