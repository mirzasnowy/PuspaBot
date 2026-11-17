"""Pembungkus tool nanobot dengan penegakan izin Puspa (definisi 2.6).

``PuspaTool`` membungkus satu ``Tool`` nanobot mengikuti pola resmi
``_LegacyErrorPrefixTool`` (``nanobot/agent/tools/loader.py``): semua
atribut/skema diteruskan apa adanya, hanya ``execute`` yang ditambah
pemeriksaan. Kenapa di sini, bukan di hook: hook nanobot
(``AgentHook.before_execute_tool``) hanya observasi — tidak ada nilai
kembali dan tidak bisa mem-veto. Penegakan harus di jalur eksekusi.

Urutan cek di ``execute`` (gagal di langkah mana pun -> TIDAK ada eksekusi,
dikembalikan ``ToolResult.error`` — pola soft-block seperti penjaga SSRF
nanobot, jadi loop agen bisa pulih secara percakapan):

1. Token izin turn ini (ContextVar Puspa, lalu ``RequestContext``).
   Tidak ada token -> tolak (fail-closed).
2. Nama tool ada di ``payload["tools"]`` token. Di luar token -> tolak.
3. Kalau tool punya manifes skill (``skill_manifest``), cek dengan
   ``check_tool`` + ``public_key`` (signature diverifikasi — celah lama
   sudah ditutup di ``puspa/policy/skills.py``). Gagal -> tolak.
4. Kalau tool memetakan ke aksi keluar (kirim / buat_event / siapkan_bayar /
   hapus, lihat ``KIND_TOOLS`` di ``puspa.policy.gate``), jalankan lewat
   ``Gate``: minta persetujuan dulu. Tanpa gerbang -> tolak. Persetujuan
   dibuat (``request_approval``) tapi aksi TIDAK dieksekusi di sini —
   eksekusi menunggu user menyetujui (layar persetujuan PWA, fase 2);
   wrapper mengembalikan hasil "menunggu persetujuan" beserta
   ``request_id``. Kalau gerbang menolak (token tidak mengizinkan /
   batas belanja) -> tolak.
5. Eksekusi tool yang dibungkus. Hasil string dipindai pola injeksi
   (``puspa.quarantine.scan``); temuan dicatat di ``tool_selesai``,
   BUKAN ditulis ulang — pemblokiran/penulisan ulang hasil adalah
   tugas Pemeriksa + renderer (definisi 2.5, fase 2).

Audit: tiap pemanggilan mencatat ``tool_dipanggil`` dan ``tool_selesai``
lewat ``log`` (callable ``log(jenis, isi)`` seperti di ``Gate``;
``make_ledger_sink`` di ``puspa.chassis.context`` mengadaptasi Buku Kerja).
Isi log TIDAK memuat parameter tool (bisa berisi data sensitif) — hanya
nama tool, status, dan alasan penolakan / temuan injeksi.
"""

from __future__ import annotations

from typing import Any

from nanobot.agent.tools.base import Tool, ToolResult

from puspa.policy.gate import KIND_TOOLS, GateDenied
from puspa.policy.skills import SkillRejected, check_tool
from puspa.quarantine.injection import scan

from .context import resolve_token_payload

__all__ = ["PuspaTool", "kind_for_tool"]

#: tool nanobot -> kind aksi keluar (balikan KIND_TOOLS dari gate).
_TOOL_TO_KIND: dict[str, str] = {
    tool: kind for kind, tools in KIND_TOOLS.items() for tool in tools
}


def kind_for_tool(tool_name: str) -> str | None:
    """Kind aksi keluar untuk nama tool, atau None kalau bukan aksi keluar."""
    return _TOOL_TO_KIND.get(tool_name)


class PuspaTool(Tool):
    """Tool nanobot yang dieksekusi hanya bila token izin mengizinkan."""

    _plugin_discoverable = False

    def __init__(
        self,
        wrapped: Tool,
        *,
        gate=None,
        log=None,
        skill_manifest: dict | None = None,
        public_key=None,
    ) -> None:
        self._wrapped = wrapped
        self._gate = gate
        self._log = log
        self._skill_manifest = skill_manifest
        self._public_key = public_key

    # -- penerusan atribut (pola _LegacyErrorPrefixTool) --------------------
    @property
    def name(self) -> str:
        return self._wrapped.name

    @property
    def description(self) -> str:
        return self._wrapped.description

    @property
    def parameters(self) -> dict[str, Any]:
        return self._wrapped.parameters

    def runtime_context_provider(self):
        return self._wrapped.runtime_context_provider()

    @property
    def read_only(self) -> bool:
        return self._wrapped.read_only

    @property
    def exclusive(self) -> bool:
        return self._wrapped.exclusive

    @property
    def concurrency_safe(self) -> bool:
        return self._wrapped.concurrency_safe

    @property
    def config_key(self) -> str:
        return getattr(self._wrapped, "config_key", "")

    def set_context(self, ctx) -> None:
        set_context = getattr(self._wrapped, "set_context", None)
        if callable(set_context):
            set_context(ctx)

    def validate_params(self, params: dict[str, Any]) -> list[str]:
        return self._wrapped.validate_params(params)

    def to_schema(self) -> dict[str, Any]:
        return self._wrapped.to_schema()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._wrapped, name)

    # -- pencatatan ---------------------------------------------------------
    def _log_event(self, jenis: str, isi: dict) -> None:
        if self._log is None:
            return
        try:
            self._log(jenis, isi)
        except Exception:
            pass

    # -- eksekusi berpengawal ----------------------------------------------
    async def execute(self, **kwargs: Any) -> Any:
        name = self.name
        payload = resolve_token_payload()

        def _tolak(alasan: str):
            self._log_event(
                "tool_dipanggil", {"tool": name, "status": "ditolak", "alasan": alasan}
            )
            self._log_event(
                "tool_selesai", {"tool": name, "status": "ditolak", "alasan": alasan}
            )
            return ToolResult.error(f"Ditolak Puspa: {alasan}")

        if payload is None:
            return _tolak("tidak ada token izin Puspa untuk turn ini")
        if name not in payload.get("tools", []):
            return _tolak(f"tool {name!r} di luar token izin")
        if self._skill_manifest is not None:
            try:
                check_tool(self._skill_manifest, name, self._public_key)
            except SkillRejected as exc:
                return _tolak(f"skill tidak lolos verifikasi: {exc}")

        kind = kind_for_tool(name)
        if kind is not None:
            if self._gate is None:
                return _tolak(f"aksi keluar {kind!r} tanpa gerbang persetujuan")
            try:
                request = self._gate.request_approval(
                    user_id=payload["user_id"],
                    agen_id=payload["agen_id"],
                    token=payload,
                    kind=kind,
                    isi=dict(kwargs),
                )
            except GateDenied as exc:
                return _tolak(f"gerbang menolak aksi {kind!r}: {exc}")
            self._log_event(
                "tool_dipanggil",
                {
                    "tool": name,
                    "status": "menunggu_persetujuan",
                    "request_id": request.request_id,
                },
            )
            return ToolResult.error(
                f"Menunggu persetujuan user untuk aksi {kind!r} "
                f"(request {request.request_id}); aksi belum dijalankan."
            )

        self._log_event("tool_dipanggil", {"tool": name})
        try:
            result = await self._wrapped.execute(**kwargs)
        except Exception:
            self._log_event("tool_selesai", {"tool": name, "status": "error"})
            raise
        status = "error" if isinstance(result, ToolResult) and result.is_error else "ok"
        isi_selesai: dict[str, Any] = {"tool": name, "status": status}
        if isinstance(result, str) and result:
            temuan = scan(result)
            if temuan:
                isi_selesai["injeksi"] = [
                    {"pola": t.pola, "tingkat": t.tingkat} for t in temuan
                ]
        self._log_event("tool_selesai", isi_selesai)
        return result
