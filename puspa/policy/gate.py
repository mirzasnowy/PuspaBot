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
from dataclasses import dataclass

from puspa.i18n.server_lines import render as render_server_line

from .tokens import (
    TokenError,
    canonical_json,
    is_revoked,
    token_id,
    tool_permission,
    verify_token,
)

__all__ = ["ApprovalRequest", "Gate", "GateDenied"]

# kind aksi keluar yang diakui gerbang.
KINDS = frozenset({"send", "create_event", "prepare_payment", "delete", "deploy"})

# Tool yang mengizinkan tiap kind (nama persis atau nama konektor).
KIND_TOOLS = {
    "send": {"send", "gmail.kirim", "gmail.send", "email.kirim"},
    "create_event": {"create_event", "calendar.create_event", "calendar.create"},
    "prepare_payment": {"prepare_payment", "pay", "payment.bayar"},
    "delete": {"delete"},
    "deploy": {"deploy"},
}

# Scope konektor yang mengizinkan tiap kind.
KIND_SCOPES = {
    "send": {"send", "write"},
    "create_event": {"create_event", "write", "create"},
    "prepare_payment": {"prepare_payment", "pay"},
    "delete": {"delete"},
    "deploy": {"deploy"},
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
    agent_id: str
    kind: str
    content: dict
    content_hash: str
    token_payload: dict
    token_id: str
    status: str = "requested"  # diminta | disetujui | ditolak | dieksekusi
    result: object = None
    executed: bool = False


def _content_hash(content: dict) -> str:
    return hashlib.sha256(canonical_json(content)).hexdigest()


def _token_allows_kind(payload: dict, kind: str) -> bool:
    # A tool at level "allow" or "ask" may request approval for this
    # kind ("ask" means allowed only after the user approves, and
    # requesting approval is that flow). A "deny" tool may not even
    # ask. Connector scopes keep their old meaning.
    for tool in KIND_TOOLS.get(kind, set()):
        if tool_permission(payload, tool) in ("allow", "ask"):
            return True
    scopes = KIND_SCOPES.get(kind, set())
    return any(set(items_scope) & scopes for items_scope in payload.get("connectors", {}).values())


class Gate:
    """Gerbang tunggal aksi keluar."""

    def __init__(self, log=None, spending=None, public_key=None):
        self.log = log
        self.spending = spending
        self.public_key = public_key
        self._requests: dict[str, ApprovalRequest] = {}

    # -- pencatatan ---------------------------------------------------
    def _log(self, event_type: str, content: dict) -> None:
        if self.log is None:
            return
        try:
            self.log(event_type, content)
        except TypeError:
            # Stub pencatat yang menerima satu dict kejadian.
            self.log({"type": event_type, "content": content})

    def _deny(self, request, reason: str, invalidate: bool = False):
        """Catat penolakan lalu lempar GateDenied.

        ``invalidate=True`` hanya untuk penolakan yang membatalkan
        persetujuan itu sendiri (isi berubah, token mati): request
        tidak bisa dipakai lagi dan harus diajukan ulang. Penolakan
        sementara (belum disetujui, batas belanja) membiarkan status
        apa adanya supaya alur bisa dilanjutkan (disetujui nanti /
        limit dinaikkan lalu execute ulang).
        """
        content_log = {"reason": reason}
        if request is not None:
            content_log["request_id"] = request.request_id
            content_log["user_id"] = request.user_id
            content_log["kind"] = request.kind
            if invalidate:
                request.status = "rejected"
        self._log("approval_rejected", content_log)
        raise GateDenied(reason)

    # -- token --------------------------------------------------------
    def authorize_tool(self, token_payload: dict, tool: str) -> str:
        """Per-tool permission check (opencode / claude code model).

        ``token_payload`` is an already verified token payload dict.
        Returns "allow" or "ask". Raises GateDenied for "deny": the
        tool must not run at all. An "ask" tool needs a user approval
        (via ``request_approval``) before it runs; this method only
        reports the level, it never approves anything by itself.
        """
        level = tool_permission(token_payload, tool)
        if level == "deny":
            raise GateDenied(render_server_line("error.token_tool_not_allowed", tool=repr(tool)))
        return level

    def _resolve_token(self, token, user_id, agent_id):
        """Kembalikan (payload, token_id). Token string diverifikasi
        kriptografis; payload dict dianggap sudah diverifikasi
        pemanggil (dicek exp + pencabutan di sini)."""
        if isinstance(token, dict):
            payload = token
            tid = token_id(payload)
        elif isinstance(token, str):
            if self.public_key is None:
                raise GateDenied(render_server_line("error.token_needs_public_key"))
            try:
                payload = verify_token(token, self.public_key)
            except TokenError as exc:
                raise GateDenied(
                    render_server_line("error.token_invalid", detail=str(exc))
                ) from exc
            tid = token_id(token)
        else:
            raise GateDenied(render_server_line("error.token_wrong_type"))
        if payload.get("user_id") != user_id or payload.get("agent_id") != agent_id:
            raise GateDenied(render_server_line("error.token_wrong_owner"))
        if payload.get("exp", 0) <= int(time.time()):
            raise GateDenied(render_server_line("error.token_expired"))
        if is_revoked(tid):
            raise GateDenied(render_server_line("error.token_revoked"))
        return payload, tid

    def _check_token_allows(self, payload, kind, content):
        if not _token_allows_kind(payload, kind):
            raise GateDenied(
                render_server_line("error.token_action_not_allowed", action=repr(kind))
            )
        domain = content.get("domain")
        if domain is not None and domain not in payload.get("domains", []):
            raise GateDenied(
                render_server_line("error.token_domain_not_allowed", domain=repr(domain))
            )
        if kind == "prepare_payment":
            nominal = content.get("nominal")
            if nominal is not None:
                rupiah_max = payload.get("budget", {}).get("rupiah_max")
                if rupiah_max is not None and nominal > rupiah_max:
                    raise GateDenied(
                        render_server_line(
                            "error.token_nominal_over_budget", nominal=nominal, budget=rupiah_max
                        )
                    )

    # -- API kontrak --------------------------------------------------
    def request_approval(self, *, user_id, agent_id, token, kind, content: dict) -> ApprovalRequest:
        """Ajukan aksi keluar untuk dimintai persetujuan user.

        Cek token mengizinkan kind + target. Catat
        ``persetujuan_diminta`` lewat ``log`` bila ada.
        """
        if kind not in KINDS:
            raise GateDenied(render_server_line("error.unknown_action_kind", kind=repr(kind)))
        if not isinstance(content, dict):
            raise GateDenied(render_server_line("error.action_content_must_be_dict"))
        payload, tid = self._resolve_token(token, user_id, agent_id)
        self._check_token_allows(payload, kind, content)
        request = ApprovalRequest(
            request_id=uuid.uuid4().hex,
            user_id=user_id,
            agent_id=agent_id,
            kind=kind,
            content=content,
            content_hash=_content_hash(content),
            token_payload=payload,
            token_id=tid,
        )
        self._requests[request.request_id] = request
        self._log(
            "approval_requested",
            {
                "request_id": request.request_id,
                "user_id": user_id,
                "agent_id": agent_id,
                "kind": kind,
                "content_hash": request.content_hash,
            },
        )
        return request

    def approve(self, request_id, user_id) -> ApprovalRequest:
        """Setujui permintaan. Hanya pemilik user yang boleh."""
        request = self._requests.get(request_id)
        if request is None:
            raise GateDenied(
                render_server_line("error.unknown_request_id", request_id=repr(request_id))
            )
        if request.user_id != user_id:
            raise GateDenied(render_server_line("error.only_owner_may_approve"))
        if request.status != "requested":
            raise GateDenied(
                render_server_line("error.request_already_status", status=repr(request.status))
            )
        request.status = "approved"
        self._log(
            "approval_granted",
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
            raise GateDenied(
                render_server_line("error.unknown_request_id", request_id=repr(request_id))
            )
        if request.executed:
            return request.result
        if request.status != "approved":
            self._deny(
                request,
                render_server_line("error.action_not_approved", status=repr(request.status)),
            )
        if _content_hash(request.content) != request.content_hash:
            self._deny(
                request, "action content changed after approval; approval void", invalidate=True
            )
        # Token masih valid? (cek ulang exp + pencabutan)
        if request.token_payload.get("exp", 0) <= int(time.time()):
            self._deny(request, render_server_line("error.token_expired"), invalidate=True)
        if is_revoked(request.token_id):
            self._deny(request, render_server_line("error.token_revoked"), invalidate=True)
        # Batas belanja (cek independen dari persetujuan).
        if request.kind == "prepare_payment":
            nominal = request.content.get("nominal", 0)
            if self.spending is None:
                self._deny(request, render_server_line("error.no_spending_enforcer"))
            if not self._spending_ok(request.user_id, nominal):
                self._deny(
                    request,
                    render_server_line("error.spending_limit_denied", nominal=nominal),
                )
        result = executor(request.content)
        request.result = result
        request.executed = True
        request.status = "executed"
        return result

    def _spending_ok(self, user_id, nominal) -> bool:
        try:
            return bool(self.spending.check(user_id, nominal))
        except TypeError:
            return bool(self.spending.check(nominal))
