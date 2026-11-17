"""Tool nanobot untuk eksekusi kode di sandbox Puspa (definisi 2.6).

``SandboxTool`` adalah ``Tool`` nanobot biasa yang eksekusinya didelegasikan
ke ``puspa.sandbox.Sandbox`` (container Docker sekali pakai, tanpa jaringan,
read-only, cap-drop ALL, user nobody) — BUKAN ke shell host nanobot.
Tool shell bawaan nanobot TIDAK masuk daftar tool token Puspa, jadi satu-
satunya jalan eksekusi kode di turn Puspa adalah tool ini, dan tool ini
sendiri fail-closed: kalau Docker tidak tersedia, kembalikan
``ToolResult.error`` (tidak pernah eksekusi di host).

Nama tool memakai Bahasa Indonesia (``jalankan_kode``) sesuai konvensi
nama tool Puspa. ``concurrency_safe`` True: tiap pemanggilan memakai
container sendiri yang terisolasi. ``Sandbox.run`` bersifat sinkron
(subprocess); pemanggilan di sini memblokir event loop sebentar — itu
diterima di fase 0, dan dicatat untuk dipindah ke executor di fase 2.

Dipakai lewat ``PuspaRegistry`` (otomatis terbungkus ``PuspaTool``:
cek token + audit) — daftarkan di factory sebelum ``AgentLoop`` dibuat,
supaya ``ToolLoader`` nanobot tidak menimpanya (aturan "caller menang").
"""

from __future__ import annotations

from typing import Any

from nanobot.agent.tools.base import Tool, ToolResult, tool_parameters

from puspa.sandbox.runner import Sandbox, SandboxUnavailable

__all__ = ["SandboxTool"]

_MAX_OUTPUT_CHARS = 10_000
_MAX_ERROR_CHARS = 2_000


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "Kode Python yang akan dijalankan di sandbox.",
            },
            "timeout_s": {
                "type": "number",
                "description": "Batas waktu eksekusi dalam detik (default 30).",
            },
        },
        "required": ["code"],
    }
)
class SandboxTool(Tool):
    """Jalankan kode Python di sandbox Docker terisolasi."""

    def __init__(self, sandbox: Sandbox | None = None) -> None:
        self._sandbox = sandbox or Sandbox()

    @property
    def name(self) -> str:
        return "jalankan_kode"

    @property
    def description(self) -> str:
        return (
            "Jalankan kode Python di sandbox terisolasi (container Docker "
            "sekali pakai, tanpa jaringan dan tanpa akses host). Kembalikan "
            "stdout, atau error bila kode gagal/timeout. Sandbox yang tidak "
            "tersedia berarti kode TIDAK dijalankan di mana pun."
        )

    @property
    def read_only(self) -> bool:
        return False

    @property
    def concurrency_safe(self) -> bool:
        return True

    async def execute(self, **kwargs: Any) -> Any:
        code = kwargs.get("code", "")
        try:
            timeout_s = int(kwargs.get("timeout_s", 30))
        except (TypeError, ValueError):
            return ToolResult.error("timeout_s harus angka.")
        try:
            result = self._sandbox.run(code, timeout_s=timeout_s)
        except SandboxUnavailable as exc:
            return ToolResult.error(f"Sandbox tidak tersedia: {exc}")
        if result.get("timed_out"):
            return ToolResult.error("Timeout: eksekusi melewati batas.")
        stdout = (result.get("stdout") or "")[:_MAX_OUTPUT_CHARS]
        stderr = (result.get("stderr") or "")[:_MAX_ERROR_CHARS]
        if not result.get("ok"):
            detail = stderr or stdout or f"exit {result.get('exit_code')}"
            return ToolResult.error(f"Kode gagal (exit {result.get('exit_code')}): {detail}")
        return stdout or "(tidak ada output)"
