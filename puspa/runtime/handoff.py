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
DEFAULT_TIMEOUT_SECONDS = 45
HEARTBEAT_SECONDS = 15
STALLED_SECONDS = 30
MAX_TRIAL = 2  # 1 percobaan awal + 1 percobaan ulang
MAX_HANDOFF_BETWEEN_AGENTS = 3


class HandoffError(Exception):
    """Kontrak handoff tidak valid / transisi status tidak sah."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class HandoffContract:
    """Satu kontrak handoff; divalidasi penuh saat dibuat."""

    from_id: str
    to_id: str
    purpose: str
    inputs: dict
    expected_output: dict
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    budget: dict = field(default_factory=lambda: {"step_max": 15, "token_max": 20000, "rupiah_max": 2000})
    permission_class: str = "Pembaca"
    completion_definition: str = ""
    idempotency_key: str = ""

    def __post_init__(self) -> None:
        if not self.from_id or not self.to_id:
            raise HandoffError("dari dan ke wajib diisi")
        if self.from_id == self.to_id:
            raise HandoffError("handoff ke diri sendiri ditolak")
        if not self.purpose.strip():
            raise HandoffError("tujuan tidak boleh kosong")
        if not isinstance(self.inputs, dict) or "type" not in self.inputs:
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
        budget = self.budget
        for key in ("step_max", "token_max", "rupiah_max"):
            if key not in budget or not isinstance(budget[key], (int, float)):
                raise HandoffError(f"anggaran.{key} wajib angka")


@dataclass
class Handoff:
    """Satu handoff yang sedang berjalan (mesin status + detak)."""

    contract: HandoffContract
    id: str = field(default_factory=lambda: f"handoff-{uuid.uuid4().hex[:12]}")
    status: str = STATUS_START
    trial: int = 1
    last_heartbeat: datetime | None = None
    created_at: datetime = field(default_factory=_now)
    result: dict | None = None
    reason: str = ""

    # -- transisi -----------------------------------------------------
    def accept(self) -> None:
        self._must_status("proposed")
        self.status = "accepted"

    def start(self) -> None:
        self._must_status("accepted")
        self.status = "running"
        self.last_heartbeat = _now()

    def heartbeat(self, created_at: datetime | None = None) -> None:
        if self.status != "running":
            raise HandoffError(f"detak hanya saat berjalan (status: {self.status})")
        self.last_heartbeat = created_at or _now()

    def hand_over(self, result: dict) -> str:
        """Serahkan hasil; kembalikan 'lolos' atau 'ditolak'.

        Hasil harus dict ber-skema dengan kunci 'jenis' yang sama
        dengan `keluaran_diharapkan['jenis']`; kalau tidak, ditolak.
        """
        self._must_status("running")
        self.result = result
        if not isinstance(result, dict) or result.get("type") != self.contract.expected_output["type"]:
            self.status = "rejected"
            self.reason = "keluaran tidak lolos skema"
            return "rejected"
        self.status = "handed_over_at"
        return "handed_over_at"

    def mark_passed(self) -> None:
        self._must_status("handed_over_at")
        self.status = "passed"

    def reject(self, reason: str) -> None:
        if self.status in STATUS_END:
            raise HandoffError(f"handoff sudah berakhir ({self.status})")
        self.status = "rejected"
        self.reason = reason

    def retry(self) -> bool:
        """Satu percobaan ulang. False bila sudah habis (lapor user)."""
        if self.trial >= MAX_TRIAL:
            return False
        self.trial += 1
        self.status = "proposed"
        self.result = None
        self.reason = ""
        return True

    # -- macet ---------------------------------------------------------
    def is_stalled(self, created_at: datetime | None = None) -> bool:
        """True bila tanpa detak 30 detik atau lewat batas waktu."""
        now = created_at or _now()
        if self.status not in ("accepted", "running"):
            return False
        if self.last_heartbeat is None:
            return (now - self.created_at).total_seconds() > STALLED_SECONDS
        if (now - self.last_heartbeat).total_seconds() > STALLED_SECONDS:
            return True
        return (now - self.created_at).total_seconds() > self.contract.timeout_seconds

    def mark_expired(self) -> None:
        if self.status in STATUS_END:
            raise HandoffError(f"handoff sudah berakhir ({self.status})")
        self.status = "expired"
        self.reason = "lewat batas waktu atau tanpa detak"

    def _must_status(self, expected: str) -> None:
        if self.status != expected:
            raise HandoffError(
                f"transisi tidak sah: status {self.status!r}, diharapkan {expected!r}"
            )


def reject_cycle(path: list[str], from_id: str, to_id: str) -> None:
    """Tolak handoff antar-agen yang membentuk lingkaran (A ke B ke A).

    `jalur` = urutan agen yang sudah dilewati tugas ini. Kalau `ke_id`
    sudah ada di jalur, handoff itu kembali ke agen sebelumnya.
    """
    if to_id in path:
        raise HandoffError(f"handoff melingkar ditolak: {to_id!r} sudah di jalur {path}")


def check_handoff_limit_between_agents(amount_so_far: int) -> None:
    """Maks 3 handoff antar-agen per tugas."""
    if amount_so_far >= MAX_HANDOFF_BETWEEN_AGENTS:
        raise HandoffError(f"maks {MAX_HANDOFF_BETWEEN_AGENTS} handoff antar-agen per tugas")
