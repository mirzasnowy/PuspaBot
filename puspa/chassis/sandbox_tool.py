"""Tool nanobot untuk eksekusi kode lewat local runner Puspa (definisi 2.6).

``SandboxTool`` adalah ``Tool`` nanobot biasa yang eksekusinya
didelegasikan ke :class:`puspa.localexec.Executor` (port eksekusi):
di instalasi lokal itu :class:`puspa.localexec.LocalExecutor`
(izin dulu — setuju sekali, setuju untuk sesi ini, atau tolak,
model Claude Code — lalu subprocess langsung di mesin pengguna
sendiri, dibatasi ke workspace, dengan timeout dan cap output).
Di server hosted, port yang sama diikat ke sandbox Docker. Tool
ini TIDAK PERNAH mengeksekusi kode di luar executor yang diikat:
izin ditolak berarti kode TIDAK dijalankan di mana pun, dan
hasilnya ``ToolResult.error`` (bukan eksekusi diam-diam di host).

Nama tool memakai Bahasa Indonesia (``jalankan_kode``) sesuai
konvensi nama tool Puspa. ``concurrency_safe`` True: tiap
pemanggilan memakai direktori run sendiri yang dihapus setelah
selesai. ``execute`` bersifat async; eksekusi lewat executor
bersifat sinkron (subprocess) dan memblokir event loop sebentar —
itu diterima di fase 0, dan dicatat untuk dipindah ke executor
di fase 2.

Dipakai lewat ``PuspaRegistry`` (otomatis terbungkus ``PuspaTool``:
cek token + audit) — daftarkan di factory sebelum ``AgentLoop``
dibuat, supaya ``ToolLoader`` nanobot tidak menimpanya (aturan
"caller menang").
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

from nanobot.agent.tools.base import Tool, ToolResult, tool_parameters

from puspa.localexec import (
    Executor,
    LocalExecutor,
    OutsideWorkspaceError,
    PermissionDenied,
)

__all__ = ["SandboxTool"]

_MAX_OUTPUT_CHARS = 10_000
_MAX_ERROR_CHARS = 2_000


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "Kode Python yang akan dijalankan di local runner.",
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
    """Jalankan kode Python lewat local runner (izin dulu)."""

    def __init__(
        self,
        executor: Executor | None = None,
        *,
        workspace: str | Path | None = None,
    ) -> None:
        if executor is None:
            self._workspace = (
                Path(workspace)
                if workspace is not None
                else Path(tempfile.mkdtemp(prefix="puspa-code-"))
            )
            self._workspace.mkdir(parents=True, exist_ok=True)
            self._executor: Executor = LocalExecutor(self._workspace)
        else:
            self._executor = executor
            if workspace is not None:
                self._workspace = Path(workspace)
            else:
                bound = getattr(executor, "workspace", None)
                self._workspace = Path(bound) if bound is not None else Path.cwd()
            self._workspace.mkdir(parents=True, exist_ok=True)

    @property
    def name(self) -> str:
        return "run_code"

    @property
    def description(self) -> str:
        return (
            "Jalankan kode Python di mesin sendiri setelah pengguna "
            "menyetujui (local runner: izin dulu, lalu subprocess, "
            "dibatasi ke workspace). Kembalikan stdout, atau error "
            "bila kode gagal/timeout/ditolak. Izin ditolak berarti "
            "kode TIDAK dijalankan di mana pun."
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
        run_dir = Path(tempfile.mkdtemp(prefix="puspa-code-", dir=str(self._workspace)))
        try:
            program = run_dir / "code.py"
            program.write_text(code, encoding="utf-8")
            try:
                result = self._executor.execute(
                    [sys.executable, str(program)],
                    cwd=run_dir,
                    timeout=float(timeout_s),
                )
            except PermissionDenied as exc:
                return ToolResult.error(f"Eksekusi ditolak pengguna: {exc}")
            except OutsideWorkspaceError as exc:
                return ToolResult.error(f"Eksekusi tidak bisa jalan: {exc}")
        finally:
            shutil.rmtree(run_dir, ignore_errors=True)
        if result.timed_out:
            return ToolResult.error("Timeout: eksekusi melewati batas.")
        stdout = result.stdout[:_MAX_OUTPUT_CHARS]
        stderr = result.stderr[:_MAX_ERROR_CHARS]
        if result.returncode != 0:
            detail = stderr or stdout or f"exit {result.returncode}"
            return ToolResult.error(f"Kode gagal (exit {result.returncode}): {detail}")
        return stdout or "(tidak ada output)"
