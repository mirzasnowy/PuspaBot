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

KLASS_PERMISSION = ("Pembaca", "Pelaksana", "Pemeriksa")
STATUS_START = "proposed"
STATUS_END = ("passed", "rejected", "expired")
BATAS_TIME_DEFAULT_DETIK = 45
HEARTBEAT_DETIK = 15
MACET_DETIK = 30
MAX_TRIAL = 2  # 1 percobaan awal + 1 percobaan ulang
MAX_HANDOFF_ANTAR_AGENT = 3


class HandoffError(Exception):
    """Kontrak handoff tidak valid / transisi status tidak sah."""


def _sekarang() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class HandoffContract:
    """Satu kontrak handoff; divalidasi penuh saat dibuat."""

    dari_id: str
    to_id: str
    purpose: str
    masukan: dict
    expected_output: dict
    timeout_seconds: int = BATAS_TIME_DEFAULT_DETIK
    anggaran: dict = field(default_factory=lambda: {"step_max": 15, "token_max": 20000, "rupiah_max": 2000})
    permission_class: str = "Pembaca"
    completion_definition: str = ""
    idempotency_key: str = ""

    def __post_init__(self) -> None:
        if not self.dari_id or not self.to_id:
            raise HandoffError("dari dan ke wajib diisi")
        if self.dari_id == self.to_id:
            raise HandoffError("handoff ke diri sendiri ditolak")
        if not self.purpose.strip():
            raise HandoffError("tujuan tidak boleh kosong")
        if not isinstance(self.masukan, dict) or "type" not in self.masukan:
            raise HandoffError("masukan harus dict ber-skema dengan kunci 'jenis'")
        if not isinstance(self.expected_output, dict) or "type" not in self.expected_output:
            raise HandoffError("expected_output harus dict ber-skema dengan kunci 'jenis'")
        if not isinstance(self.timeout_seconds, int) or self.timeout_seconds <= 0:
            raise HandoffError("batas_time harus detik positif")
        if self.permission_class not in KLASS_PERMISSION:
            raise HandoffError(f"kelas_izin harus salah satu dari {KLASS_PERMISSION}")
        if not self.completion_definition.strip():
            raise HandoffError("completion_definition tidak boleh kosong")
        if not self.idempotency_key:
            self.idempotency_key = f"handoff-{uuid.uuid4().hex}"
        anggaran = self.anggaran
        for key in ("step_max", "token_max", "rupiah_max"):
            if key not in anggaran or not isinstance(anggaran[key], (int, float)):
                raise HandoffError(f"anggaran.{key} wajib angka")


@dataclass
class Handoff:
    """Satu handoff yang sedang berjalan (mesin status + detak)."""

    kontrak: HandoffContract
    id: str = field(default_factory=lambda: f"handoff-{uuid.uuid4().hex[:12]}")
    status: str = STATUS_START
    trial: int = 1
    last_heartbeat: datetime | None = None
    created_at: datetime = field(default_factory=_sekarang)
    result: dict | None = None
    reason: str = ""

    # -- transisi -----------------------------------------------------
    def terima(self) -> None:
        self._must_status("proposed")
        self.status = "accepted"

    def mulai(self) -> None:
        self._must_status("accepted")
        self.status = "berjalan"
        self.last_heartbeat = _sekarang()

    def heartbeat(self, created_at: datetime | None = None) -> None:
        if self.status != "berjalan":
            raise HandoffError(f"detak hanya saat berjalan (status: {self.status})")
        self.last_heartbeat = created_at or _sekarang()

    def serahkan(self, result: dict) -> str:
        """Serahkan hasil; kembalikan 'lolos' atau 'ditolak'.

        Hasil harus dict ber-skema dengan kunci 'jenis' yang sama
        dengan `keluaran_diharapkan['jenis']`; kalau tidak, ditolak.
        """
        self._must_status("berjalan")
        self.result = result
        if not isinstance(result, dict) or result.get("type") != self.kontrak.expected_output["type"]:
            self.status = "rejected"
            self.reason = "keluaran tidak lolos skema"
            return "rejected"
        self.status = "handed_over_at"
        return "handed_over_at"

    def nyatakan_passed(self) -> None:
        self._must_status("handed_over_at")
        self.status = "passed"

    def tolak(self, reason: str) -> None:
        if self.status in STATUS_END:
            raise HandoffError(f"handoff sudah berakhir ({self.status})")
        self.status = "rejected"
        self.reason = reason

    def coba_ulang(self) -> bool:
        """Satu percobaan ulang. False bila sudah habis (lapor user)."""
        if self.trial >= MAX_TRIAL:
            return False
        self.trial += 1
        self.status = "proposed"
        self.result = None
        self.reason = ""
        return True

    # -- macet ---------------------------------------------------------
    def macet(self, created_at: datetime | None = None) -> bool:
        """True bila tanpa detak 30 detik atau lewat batas waktu."""
        sekarang = created_at or _sekarang()
        if self.status not in ("accepted", "berjalan"):
            return False
        if self.last_heartbeat is None:
            return (sekarang - self.created_at).total_seconds() > MACET_DETIK
        if (sekarang - self.last_heartbeat).total_seconds() > MACET_DETIK:
            return True
        return (sekarang - self.created_at).total_seconds() > self.kontrak.timeout_seconds

    def tandai_kedaluwarsa(self) -> None:
        if self.status in STATUS_END:
            raise HandoffError(f"handoff sudah berakhir ({self.status})")
        self.status = "expired"
        self.reason = "lewat batas waktu atau tanpa detak"

    def _must_status(self, diharapkan: str) -> None:
        if self.status != diharapkan:
            raise HandoffError(
                f"transisi tidak sah: status {self.status!r}, diharapkan {diharapkan!r}"
            )


def tolak_lingkaran(jalur: list[str], dari_id: str, to_id: str) -> None:
    """Tolak handoff antar-agen yang membentuk lingkaran (A ke B ke A).

    `jalur` = urutan agen yang sudah dilewati tugas ini. Kalau `ke_id`
    sudah ada di jalur, handoff itu kembali ke agen sebelumnya.
    """
    if to_id in jalur:
        raise HandoffError(f"handoff melingkar ditolak: {to_id!r} sudah di jalur {jalur}")


def check_batas_handoff_antar_agent(amount_so_far: int) -> None:
    """Maks 3 handoff antar-agen per tugas."""
    if amount_so_far >= MAX_HANDOFF_ANTAR_AGENT:
        raise HandoffError(f"maks {MAX_HANDOFF_ANTAR_AGENT} handoff antar-agen per tugas")
