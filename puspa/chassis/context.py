"""Konteks turn Puspa: token izin yang sedang berlaku + sink ledger.

Token izin dipegang sebagai payload dict YANG SUDAH DIVERIFIKASI
(``verify_token`` di turn setup), bukan string mentah. Dua jalur baca:

1. ContextVar Puspa (``bind_token``) — di-set saat turn disiapkan, dan
   terlihat juga di dalam sub-agen karena ``asyncio.create_task`` menyalin
   konteks (mekanisme yang dipakai ``SubagentManager._create_task``).
2. ``RequestContext.attributes["puspa_token"]`` nanobot — slot resmi
   "caller-owned request data" (``Nanobot.run(..., attributes=...)``);
   turn setup menaruh payload dict terverifikasi di sini.

Jalur 1 diutamakan (eksplisit milik Puspa); jalur 2 sebagai cadangan
untuk tool yang hanya melihat ``RequestContext`` nanobot. Kalau tidak ada
keduanya, tidak ada izin — fail-closed di pemanggil (``PuspaTool`` dan
``PuspaRegistry`` menolak eksekusi).

``make_ledger_sink`` mengadaptasi ``Ledger.append_event`` menjadi callable
``log(jenis, isi)`` seperti yang dipakai ``Gate``. Penulisan ledger di
lapisan integrasi bersifat best-effort: kalau Postgres mati, turn tetap
jalan dan kegagalan tidak dilempar (kebijakan saat ledger mati diputuskan
di fase 2; lihat STATUS.md).
"""

from __future__ import annotations

from contextlib import contextmanager, suppress
from contextvars import ContextVar, Token

__all__ = [
    "bind_token",
    "current_token_payload",
    "make_ledger_sink",
    "reset_token",
    "resolve_token_payload",
    "token_context",
    "token_payload_from_request_context",
]

_CURRENT_TOKEN: ContextVar[dict | None] = ContextVar("puspa_token", default=None)


def bind_token(payload: dict | None) -> Token:
    """Set payload token terverifikasi untuk konteks ini; kembalikan token
    untuk ``reset_token``. ``None`` berarti tidak ada token."""
    return _CURRENT_TOKEN.set(payload)


def reset_token(token: Token) -> None:
    """Kembalikan ContextVar ke nilai sebelum ``bind_token``."""
    _CURRENT_TOKEN.reset(token)


@contextmanager
def token_context(payload: dict | None):
    """Context manager: bind payload selama blok ``with``."""
    token = bind_token(payload)
    try:
        yield payload
    finally:
        reset_token(token)


def current_token_payload() -> dict | None:
    """Payload token dari ContextVar Puspa, atau None."""
    payload = _CURRENT_TOKEN.get()
    return payload if isinstance(payload, dict) else None


def token_payload_from_request_context() -> dict | None:
    """Payload token dari ``RequestContext.attributes["puspa_token"]``.

    Hanya dict yang diterima; string token mentah diabaikan (verifikasi
    adalah tugas turn setup, bukan tool).
    """
    try:
        from nanobot.agent.tools.context import current_request_context
    except Exception:
        return None
    ctx = current_request_context()
    if ctx is None:
        return None
    attributes = getattr(ctx, "attributes", None) or {}
    payload = attributes.get("puspa_token")
    return payload if isinstance(payload, dict) else None


def resolve_token_payload() -> dict | None:
    """Payload token turn ini: ContextVar Puspa dulu, lalu RequestContext."""
    payload = current_token_payload()
    if payload is not None:
        return payload
    return token_payload_from_request_context()


def make_ledger_sink(ledger, *, task_id: str, agent_id: str):
    """Adaptasi ``Ledger`` menjadi ``log(jenis, isi)``.

    Best-effort: kegagalan tulis (mis. Postgres mati) ditelan supaya turn
    tidak jatuh karena audit; lihat catatan modul.
    """

    def log(event_type, content=None):
        with suppress(Exception):
            ledger.append_event(
                task_id=task_id,
                agent_id=agent_id,
                event_type=event_type,
                content=dict(content or {}),
            )

    return log
