"""Composition root Puspa di atas nanobot (definisi 2.6, 2.3, 2.13).

Satu tempat yang merakit semua wiring Puspa -> nanobot, tanpa mengubah
satu baris kode nanobot. Semua injeksi memakai parameter konstruktor
resmi nanobot:

- ``AgentLoop.from_config(config, tool_registry=..., hooks=[...],
  hook_factories=[...])`` — registry dan hook adalah parameter yang
  didokumentasikan ("The tool registry is caller-owned so application
  composition can share it").
- ``Nanobot.run(..., hooks=[...], attributes={"puspa_token": payload})``
  — attributes adalah slot resmi "caller-owned request data".

``assemble`` membangun loop lengkap; butuh ``Config`` nanobot + provider
(model) yang valid, jadi uji end-to-end loop penuh ditunda ke fase 2
(lihat STATUS.md). Yang diuji di fase 0: ``build_registry`` dan
``build_hook_factory`` (komponen nyata) + ``assemble`` dengan
``AgentLoop.from_config`` yang di-stub (membuktikan kwarg komposisi
yang benar diteruskan, tanpa membangun loop sungguhan).

Urutan pendaftaran tool: tool Puspa (``SandboxTool``) didaftarkan dulu ke
registry, sebelum ``AgentLoop`` dibuat — karena ``ToolLoader`` nanobot
mempertahankan tool yang sudah ada ("caller menang") dan tidak akan
menimpanya dengan builtin bernama sama.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .context import make_ledger_sink
from .hooks import PuspaHook, make_puspa_hook_factory
from .registry import PuspaRegistry
from .sandbox_tool import SandboxTool

__all__ = ["PuspaChassis", "assemble", "build_hook_factory", "build_registry"]


@dataclass
class PuspaChassis:
    """Semua yang dibutuhkan wiring Puspa (dibuat sekali saat startup)."""

    task_id: str = "tugas-utama"
    agent_id: str = "agen-puspa"
    ledger: Any = None  # puspa.ledger.Ledger atau None (tanpa audit persisten)
    gate: Any = None  # puspa.policy.Gate atau None (tanpa gerbang -> aksi keluar ditolak)
    public_key: Any = None  # verifikasi token + skill (Ed25519 public key)
    sandbox: Any = None  # puspa.sandbox.Sandbox atau None (dibuat default di tool)
    skill_manifests: dict = field(default_factory=dict)  # nama tool -> manifes
    log: Any = None  # callable log(jenis, isi); kalau None dan ledger ada, dibuat dari ledger
    world_model: Any = None  # puspa.world.WorldModel atau None (tanpa otak)
    learning_loop: Any = None  # puspa.memory.learning_loop.LearningLoop atau None

    def brain(self):
        """The session brain for this chassis, or None.

        When both a world model and a learning loop were given,
        they are the same instances the session registered through
        ``bind_turn`` / ``get_session_brain``: one brain shared
        across turns, never a fresh instance per turn. Callers
        that run tasks (``TaskRunner``, ``Planner``,
        ``CurriculumRunner``) take ``world_model`` and
        ``learning_loop`` from here.
        """
        if self.world_model is None and self.learning_loop is None:
            return None
        return (self.world_model, self.learning_loop)

    def sink(self):
        """Callable log(jenis, isi) untuk hook/registry/wrapper."""
        if self.log is not None:
            return self.log
        if self.ledger is not None:
            return make_ledger_sink(
                self.ledger, task_id=self.task_id, agent_id=self.agent_id
            )
        return None


def build_registry(chassis: PuspaChassis) -> PuspaRegistry:
    """Registry Puspa: SandboxTool terdaftar, siap diserahkan ke AgentLoop."""
    registry = PuspaRegistry(
        gate=chassis.gate,
        log=chassis.sink(),
        public_key=chassis.public_key,
        skill_manifests=chassis.skill_manifests,
    )
    registry.register(SandboxTool(chassis.sandbox))
    return registry


def build_hook_factory(chassis: PuspaChassis):
    """Factory per-turn untuk PuspaHook (siklus tugas -> Buku Kerja)."""
    return make_puspa_hook_factory(
        chassis.task_id, chassis.agent_id, log=chassis.sink()
    )


def build_hook(chassis: PuspaChassis) -> PuspaHook:
    """Satu instance PuspaHook (untuk ``hooks=[...]`` di AgentLoop)."""
    return PuspaHook(
        task_id=chassis.task_id, agent_id=chassis.agent_id, log=chassis.sink()
    )


def assemble(chassis: PuspaChassis, config, *, bus=None):
    """Bangun ``AgentLoop`` nanobot dengan wiring Puspa terpasang.

    Lazy import: modul ini (dan paket ``puspa.chassis``) tetap bisa
    diimpor tanpa nanobot; hanya ``assemble`` yang butuh nanobot.
    """
    from nanobot.agent.loop import AgentLoop

    registry = build_registry(chassis)
    loop = AgentLoop.from_config(
        config,
        bus=bus,
        tool_registry=registry,
        hooks=[build_hook(chassis)],
        hook_factories=[build_hook_factory(chassis)],
    )
    return loop
