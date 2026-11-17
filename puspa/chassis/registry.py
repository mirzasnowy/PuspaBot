"""Registry tool nanobot yang ditegakkan Puspa (definisi 2.6).

``PuspaRegistry`` adalah ``ToolRegistry`` nanobot dengan tiga lapis
penegakan, tanpa mengubah satu baris kode nanobot:

1. ``register`` membungkus tiap tool dengan ``PuspaTool`` (cek token,
   cek skill, gerbang aksi keluar, audit). Karena ``ToolLoader.load``
   mendaftarkan tool bawaan lewat ``register`` yang sama, dan
   ``MCPProvider`` juga mendaftarkan tool MCP-nya ke registry ini,
   semua tool yang masuk registry otomatis terbungkus — termasuk yang
   didaftarkan belakangan. Tool yang sudah terbungkus tidak dibungkus
   dua kali.
2. ``prepare_call`` (backstop): ``execute_tool_calls`` nanobot memanggil
   ``prepare_call`` SEBELUM eksekusi, dan jalur cepatnya mem-bypass
   ``registry.execute`` — jadi membungkus ``execute`` saja tidak cukup.
   Override ini memeriksa token mengizinkan nama tool; kalau tidak,
   kembalikan error sebagai elemen ketiga (runner mengubahnya menjadi
   hasil tool error, loop lanjut — pola yang sama dengan pelanggaran
   SSRF/workspace nanobot). Ini menutup tool yang entah bagaimana
   lolos tanpa bungkus.
3. ``get_definitions`` (visibilitas): model hanya melihat skema tool
   yang namanya ada di daftar ``tools`` token turn ini — sumber
   kebenaran yang sama dengan ``prepare_call``. Tanpa token terikat,
   daftar kosong (fail-closed). Lapisan ini menutup celah visibilitas
   (model tidak lagi bisa memilih tool di luar token), sementara
   ``prepare_call`` tetap menjadi penegak eksekusi (defense in depth).

Tanpa token izin yang terikat di turn ini, semua eksekusi ditolak
(fail-closed) dan tidak ada definisi tool yang terlihat. Registry ini
milik Puspa: semua turn yang memakainya (user, cron, dream, sub-agen)
wajib mengikat token saat turn disiapkan (lihat
``puspa.chassis.agent_session.bind_turn`` dan ``token_context``).
"""

from __future__ import annotations

from typing import Any

from nanobot.agent.tools.base import ToolResult
from nanobot.agent.tools.registry import ToolRegistry

from .context import resolve_token_payload
from .tools import PuspaTool

__all__ = ["PuspaRegistry"]


class PuspaRegistry(ToolRegistry):
    """ToolRegistry dengan penegakan token izin Puspa di semua tool."""

    def __init__(
        self,
        *,
        gate=None,
        log=None,
        public_key=None,
        skill_manifests: dict[str, dict] | None = None,
    ) -> None:
        super().__init__()
        self._gate = gate
        self._log = log
        self._public_key = public_key
        self._skill_manifests = dict(skill_manifests or {})

    def register(self, tool) -> PuspaTool:
        """Daftarkan tool setelah dibungkus PuspaTool (sekali saja)."""
        if not isinstance(tool, PuspaTool):
            manifest = self._skill_manifests.get(tool.name)
            tool = PuspaTool(
                tool,
                gate=self._gate,
                log=self._log,
                skill_manifest=manifest,
                public_key=self._public_key,
            )
        super().register(tool)
        return tool

    def get_definitions(self) -> list[dict[str, Any]]:
        """Definisi tool yang BOLEH dilihat model di turn ini saja.

        Hanya skema tool yang namanya ada di daftar ``tools`` token izin
        turn ini (sumber kebenaran yang sama dengan ``prepare_call``);
        tanpa token terikat, kembalikan daftar kosong (fail-closed).
        Nanobot memanggil ini per putaran model (dan saat konsolidasi di
        dalam turn), jadi filter dibaca dari token yang sedang berlaku
        setiap panggilan — cache induk hanya menyimpan daftar lengkap
        yang belum difilter, dan yang dikembalikan selalu daftar baru
        (cache induk tidak pernah diubah).
        """
        payload = resolve_token_payload()
        if payload is None:
            return []
        allowed = set(payload.get("tools", []) or [])
        return [
            schema
            for schema in super().get_definitions()
            if self._schema_name(schema) in allowed
        ]

    def prepare_call(
        self,
        name: str,
        params: Any,
    ) -> tuple[Any, Any, str | None]:
        """Resolve + validasi seperti induk, lalu cek token mengizinkan tool.

        Mengembalikan ``(tool, params, error)``; ``error`` bukan None
        berarti call ditolak sebelum eksekusi.
        """
        tool, params, error = super().prepare_call(name, params)
        if error is not None or tool is None:
            return tool, params, error
        payload = resolve_token_payload()
        if payload is None:
            self._log_denied(name, "tidak ada token izin untuk turn ini")
            return (
                tool,
                params,
                ToolResult.error("Ditolak Puspa: tidak ada token izin untuk turn ini."),
            )
        if name not in payload.get("tools", []):
            self._log_denied(name, f"tool {name!r} di luar token izin")
            return (
                tool,
                params,
                ToolResult.error(f"Ditolak Puspa: tool {name!r} di luar token izin."),
            )
        return tool, params, None

    def _log_denied(self, name: str, reason: str) -> None:
        """Catat penolakan di prepare_call (wrapper tidak sempat jalan)."""
        if self._log is None:
            return
        try:
            self._log(
                "tool_called", {"tool": name, "status": "rejected", "reason": reason}
            )
            self._log(
                "tool_completed", {"tool": name, "status": "rejected", "reason": reason}
            )
        except Exception:
            pass
