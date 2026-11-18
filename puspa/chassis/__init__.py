"""Integrasi paket keamanan Puspa dengan nanobot sebagai chassis.

Tanpa mengubah kode nanobot: semua kait memakai titik ekstensi resmi
nanobot (parameter konstruktor ``AgentLoop`` / ``Nanobot.run``,
``AgentHook``, ``ToolRegistry``, ``SkillsLoader``).

- ``context`` — token izin turn ini (ContextVar + ``RequestContext``) + sink ledger.
- ``tools`` — ``PuspaTool``: pembungkus tool dengan cek token, cek skill, gerbang aksi keluar, audit.
- ``registry`` — ``PuspaRegistry``: semua tool otomatis terbungkus + backstop ``prepare_call``.
- ``hooks`` — ``PuspaHook``: siklus tugas + pesan akhir ke Buku Kerja.
- ``skills`` — ``PuspaSkillsLoader``: skill workspace/plugin wajib bertanda tangan.
- ``session`` — ``bind_turn``: sesi login -> token izin agen.
- ``sandbox_tool`` — ``SandboxTool``: eksekusi kode lewat sandbox Docker Puspa.
- ``factory`` — ``PuspaChassis`` + ``assemble``: composition root.
"""

from .agent_session import (
    AgentBrain,
    bind_turn,
    get_session_brain,
    payload_from_token,
    reset_session_brains,
)
from .context import (
    bind_token,
    current_token_payload,
    make_ledger_sink,
    reset_token,
    resolve_token_payload,
    token_context,
    token_payload_from_request_context,
)
from .factory import (
    PuspaChassis,
    assemble,
    build_hook_factory,
    build_registry,
    load_chassis_plugins,
)
from .hooks import PuspaHook, make_puspa_hook_factory
from .registry import PuspaRegistry
from .sandbox_tool import SandboxTool
from .skill_loader import PuspaSkillsLoader
from .tools import PuspaTool, kind_for_tool

__all__ = [
    "AgentBrain",
    "PuspaChassis",
    "PuspaHook",
    "PuspaRegistry",
    "PuspaSkillsLoader",
    "PuspaTool",
    "SandboxTool",
    "assemble",
    "bind_token",
    "bind_turn",
    "build_hook_factory",
    "build_registry",
    "current_token_payload",
    "get_session_brain",
    "kind_for_tool",
    "load_chassis_plugins",
    "make_ledger_sink",
    "make_puspa_hook_factory",
    "payload_from_token",
    "reset_session_brains",
    "reset_token",
    "resolve_token_payload",
    "token_context",
    "token_payload_from_request_context",
]
