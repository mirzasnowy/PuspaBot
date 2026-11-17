"""Gerbang tunggal aksi keluar Puspa (definisi 2.6, CONTRACTS.md).

Semua aksi keluar (kirim, buat event, siapkan bayar, hapus) lewat
satu gerbang kode: cek token, cek batas belanja, cek aturan
persetujuan, antre persetujuan. Tidak ada jalur lain di kode.

``log`` adalah callable pencatat kejadian dengan signature
``log(jenis, isi)`` — sengaja bukan import ledger, untuk menghindari
circular import; tes memakai stub pencatat. ``jenis`` memakai nama
jenis Buku Kerja: persetujuan_diminta, persetujuan_diberikan,
persetujuan_ditolak.

``spending`` adalah objek batas belanja (``SpendingLimit``) atau
None. Kalau None, pembayaran (siapkan_bayar) selalu ditolak
(fail-closed): tanpa penegak batas belanja, tidak ada uang yang bergerak.

Token bisa diberikan sebagai string (diverifikasi dengan
``public_key`` yang dipasang di Gate) atau sebagai payload dict yang
sudah diverifikasi pemanggil. Token string tanpa ``public_key``
ditolak — signature tidak boleh dilewati diam-diam.

Idempotensi: kunci = ``request_id``. ``execute`` yang kedua (dan
seterusnya) untuk request yang sama mengembalikan hasil pertama
tanpa memanggil ``executor`` lagi.
"""

import hashlib
import time
import uuid
from dataclasses import dataclass, field

from .tokens import TokenError, canonical_json, is_revoked, token_id, verify_token

__all__ = ["ApprovalRequest", "Gate", "GateDenied"]

# kind aksi keluar yang diakui gerbang.
KINDS = frozenset({"kirim", "buat_event", "siapkan_bayar", "hapus"})

# Tool yang mengizinkan tiap kind (nama persis atau nama konektor).
KIND_TOOLS = {
    "kirim": {"kirim", "gmail.kirim", "gmail.send", "email.kirim"},
    "buat_event": {"buat_event", "calendar.buat_event", "calendar.create"},
    "siapkan_bayar": {"siapkan_bayar", "bayar", "payment.bayar"},
    "hapus": {"hapus", "delete"},
}

# Scope konektor yang mengizinkan tiap kind.
KIND_SCOPES = {
    "kirim": {"kirim", "send", "write"},
    "buat_event": {"buat_event", "write", "create"},
    "siapkan_bayar": {"siapkan_bayar", "bayar"},
    "hapus": {"hapus", "delete"},
}


class GateDenied(Exception):
    """Gerbang menolak: persetujuan tidak ada/batal, token tidak
    valid/dicabut, atau batas belanja tidak mengizinkan. TIDAK ada
    eksekusi saat exception ini dilempar."""


@dataclass
class ApprovalRequest:
    """Satu permintaan persetujuan aksi keluar."""

    request_id: str
    user_id: str
    agen_id: str
    kind: str
    isi: dict
    isi_hash: str
    token_payload: dict
    token_id: str
    status: str = "diminta"  # diminta | disetujui | ditolak | dieksekusi
    result: object = None
    executed: bool = False


def _isi_hash(isi: dict) -> str:
    return hashlib.sha256(canonical_json(isi)).hexdigest()


def _token_allows_kind(payload: dict, kind: str) -> bool:
    tools = set(payload.get("tools", []))
    if tools & KIND_TOOLS.get(kind, set()):
        return True
    scopes = KIND_SCOPES.get(kind, set())
    for daftar_scope in payload.get("konektor", {}).values():
        if set(daftar_scope) & scopes:
            return True
    return False


class Gate:
    """Gerbang tunggal aksi keluar."""

    def __init__(self, log=None, spending=None, public_key=None):
        self.log = log
        self.spending = spending
        self.public_key = public_key
        self._requests: dict[str, ApprovalRequest] = {}

    # -- pencatatan ---------------------------------------------------
    def _log(self, jenis: str, isi: dict) -> None:
        if self.log is None:
            return
        try:
            self.log(jenis, isi)
        except TypeError:
            # Stub pencatat yang menerima satu dict kejadian.
            self.log({"jenis": jenis, "isi": isi})

    def _deny(self, request, alasan: str, invalidate: bool = False):
        """Catat penolakan lalu lempar GateDenied.

        ``invalidate=True`` hanya untuk penolakan yang membatalkan
        persetujuan itu sendiri (isi berubah, token mati): request
        tidak bisa dipakai lagi dan harus diajukan ulang. Penolakan
        sementara (belum disetujui, batas belanja) membiarkan status
        apa adanya supaya alur bisa dilanjutkan (disetujui nanti /
        limit dinaikkan lalu execute ulang).
        """
        isi_log = {"alasan": alasan}
        if request is not None:
            isi_log["request_id"] = request.request_id
            isi_log["user_id"] = request.user_id
            isi_log["kind"] = request.kind
            if invalidate:
                request.status = "ditolak"
        self._log("persetujuan_ditolak", isi_log)
        raise GateDenied(alasan)

    # -- token --------------------------------------------------------
    def _resolve_token(self, token, user_id, agen_id):
        """Kembalikan (payload, token_id). Token string diverifikasi
        kriptografis; payload dict dianggap sudah diverifikasi
        pemanggil (dicek exp + pencabutan di sini)."""
        if isinstance(token, dict):
            payload = token
            tid = token_id(payload)
        elif isinstance(token, str):
            if self.public_key is None:
                raise GateDenied(
                    "Gate butuh public_key untuk memverifikasi token string"
                )
            try:
                payload = verify_token(token, self.public_key)
            except TokenError as exc:
                raise GateDenied(f"token tidak valid: {exc}") from exc
            tid = token_id(token)
        else:
            raise GateDenied("token harus string atau payload dict")
        if payload.get("user_id") != user_id or payload.get("agen_id") != agen_id:
            raise GateDenied("token bukan milik user/agen ini")
        if payload.get("exp", 0) <= int(time.time()):
            raise GateDenied("token sudah kedaluwarsa")
        if is_revoked(tid):
            raise GateDenied("token sudah dicabut")
        return payload, tid

    def _check_token_allows(self, payload, kind, isi):
        if not _token_allows_kind(payload, kind):
            raise GateDenied(f"token tidak mengizinkan aksi {kind!r}")
        domain = isi.get("domain")
        if domain is not None and domain not in payload.get("domains", []):
            raise GateDenied(f"token tidak mengizinkan domain {domain!r}")
        if kind == "siapkan_bayar":
            nominal = isi.get("nominal")
            if nominal is not None:
                rupiah_max = payload.get("anggaran", {}).get("rupiah_max")
                if rupiah_max is not None and nominal > rupiah_max:
                    raise GateDenied(
                        f"nominal Rp{nominal} melebihi anggaran token Rp{rupiah_max}"
                    )

    # -- API kontrak --------------------------------------------------
    def request_approval(self, *, user_id, agen_id, token, kind, isi: dict) -> ApprovalRequest:
        """Ajukan aksi keluar untuk dimintai persetujuan user.

        Cek token mengizinkan kind + target. Catat
        ``persetujuan_diminta`` lewat ``log`` bila ada.
        """
        if kind not in KINDS:
            raise GateDenied(f"kind aksi tidak dikenal: {kind!r}")
        if not isinstance(isi, dict):
            raise GateDenied("isi aksi harus dict")
        payload, tid = self._resolve_token(token, user_id, agen_id)
        self._check_token_allows(payload, kind, isi)
        request = ApprovalRequest(
            request_id=uuid.uuid4().hex,
            user_id=user_id,
            agen_id=agen_id,
            kind=kind,
            isi=isi,
            isi_hash=_isi_hash(isi),
            token_payload=payload,
            token_id=tid,
        )
        self._requests[request.request_id] = request
        self._log(
            "persetujuan_diminta",
            {
                "request_id": request.request_id,
                "user_id": user_id,
                "agen_id": agen_id,
                "kind": kind,
                "isi_hash": request.isi_hash,
            },
        )
        return request

    def approve(self, request_id, user_id) -> ApprovalRequest:
        """Setujui permintaan. Hanya pemilik user yang boleh."""
        request = self._requests.get(request_id)
        if request is None:
            raise GateDenied(f"permintaan tidak dikenal: {request_id!r}")
        if request.user_id != user_id:
            raise GateDenied("hanya pemilik user yang boleh menyetujui")
        if request.status != "diminta":
            raise GateDenied(f"permintaan sudah berstatus {request.status!r}")
        request.status = "disetujui"
        self._log(
            "persetujuan_diberikan",
            {"request_id": request.request_id, "user_id": user_id, "kind": request.kind},
        )
        return request

    def execute(self, request_id, *, executor):
        """Jalankan aksi yang sudah disetujui, tepat sekali.

        Cek ulang SEMUA sebelum eksekusi: sudah disetujui, isi_hash
        masih sama dengan isi saat ini, token masih valid (belum
        kedaluwarsa, belum dicabut), batas belanja OK. Gagal cek mana
        pun -> GateDenied, TIDAK ada eksekusi, dicatat sebagai
        persetujuan_ditolak. Sudah dieksekusi -> kembalikan hasil yang
        sama tanpa memanggil executor lagi.
        """
        request = self._requests.get(request_id)
        if request is None:
            raise GateDenied(f"permintaan tidak dikenal: {request_id!r}")
        if request.executed:
            return request.result
        if request.status != "disetujui":
            self._deny(request, f"aksi belum disetujui (status: {request.status!r})")
        if _isi_hash(request.isi) != request.isi_hash:
            self._deny(request, "isi aksi berubah setelah disetujui; persetujuan batal", invalidate=True)
        # Token masih valid? (cek ulang exp + pencabutan)
        if request.token_payload.get("exp", 0) <= int(time.time()):
            self._deny(request, "token sudah kedaluwarsa", invalidate=True)
        if is_revoked(request.token_id):
            self._deny(request, "token sudah dicabut", invalidate=True)
        # Batas belanja (cek independen dari persetujuan).
        if request.kind == "siapkan_bayar":
            nominal = request.isi.get("nominal", 0)
            if self.spending is None:
                self._deny(request, "tidak ada penegak batas belanja belanja; pembayaran ditolak")
            if not self._spending_ok(request.user_id, nominal):
                self._deny(
                    request,
                    f"batas belanja tidak mengizinkan nominal Rp{nominal}",
                )
        result = executor(request.isi)
        request.result = result
        request.executed = True
        request.status = "dieksekusi"
        return result

    def _spending_ok(self, user_id, nominal) -> bool:
        try:
            return bool(self.spending.check(user_id, nominal))
        except TypeError:
            return bool(self.spending.check(nominal))
