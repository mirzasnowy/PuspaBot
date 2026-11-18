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

import contextlib
import os
from dataclasses import dataclass, field
from typing import Any

from .context import make_ledger_sink
from .hooks import PuspaHook, make_puspa_hook_factory
from .registry import PuspaRegistry
from .sandbox_tool import SandboxTool

__all__ = [
    "PuspaChassis",
    "assemble",
    "build_hook_factory",
    "build_registry",
    "load_chassis_plugins",
]


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
    rail_set: Any = None  # puspa.guardrails.RailSet or None (None = no rails in bare build_hook; assemble() builds the default set)
    plugin_dirs: tuple = ()  # extra plugin directories (G27); PUSPA_PLUGIN_DIRS (colon-separated) is also read at load time
    plugins: list = field(
        default_factory=list
    )  # loaded PluginRecord list (G27); pre-load it or let assemble() fill it
    plugin_failures: list = field(
        default_factory=list
    )  # (source, exception) pairs for plugins that failed to load

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
            return make_ledger_sink(self.ledger, task_id=self.task_id, agent_id=self.agent_id)
        return None


def build_registry(chassis: PuspaChassis) -> PuspaRegistry:
    """Registry Puspa: SandboxTool terdaftar, siap diserahkan ke AgentLoop.

    Plugin tools from ``chassis.plugins`` (G27) are attached after
    the sandbox tool, through the plugin adapter, so they get the
    same PuspaTool enforcement (token check, audit, gate) as any
    other tool.
    """
    registry = PuspaRegistry(
        gate=chassis.gate,
        log=chassis.sink(),
        public_key=chassis.public_key,
        skill_manifests=chassis.skill_manifests,
    )
    registry.register(SandboxTool(chassis.sandbox))
    if chassis.plugins:
        from puspa.plugins.adapters import attach_plugin_tools

        attach_plugin_tools(registry, chassis.plugins)
    return registry


def load_chassis_plugins(chassis: PuspaChassis):
    """Discover and load plugins for a chassis (G27).

    Directories come from ``chassis.plugin_dirs`` plus the
    ``PUSPA_PLUGIN_DIRS`` environment variable (colon-separated);
    the default user/project directories and pip entry points
    are always discovered too. Loaded records land on
    ``chassis.plugins``; failures land on
    ``chassis.plugin_failures`` and are logged through the
    chassis sink when one exists. A broken plugin never aborts
    startup, but it is never silently skipped either: the
    failure is recorded and reported. Returns the ``LoadResult``.
    """
    from puspa.plugins.loader import load_plugins

    env_dirs = [item for item in os.environ.get("PUSPA_PLUGIN_DIRS", "").split(":") if item]
    extra_dirs = [str(item) for item in chassis.plugin_dirs] + env_dirs
    result = load_plugins(extra_dirs=extra_dirs)
    chassis.plugins = result.records
    chassis.plugin_failures = result.failures
    sink = chassis.sink()
    if sink is not None:
        for source, exc in result.failures:
            with contextlib.suppress(Exception):
                sink(
                    "plugin_load_failed",
                    {
                        "origin": source.origin,
                        "location": source.location,
                        "error": str(exc),
                    },
                )
    return result


def build_hook_factory(chassis: PuspaChassis, *, rail_set=None):
    """Factory per-turn untuk PuspaHook (siklus tugas -> Buku Kerja)."""
    return make_puspa_hook_factory(
        chassis.task_id,
        chassis.agent_id,
        log=chassis.sink(),
        rail_set=rail_set if rail_set is not None else chassis.rail_set,
    )


def build_hook(chassis: PuspaChassis, *, rail_set=None) -> PuspaHook:
    """Satu instance PuspaHook (untuk ``hooks=[...]`` di AgentLoop)."""
    return PuspaHook(
        task_id=chassis.task_id,
        agent_id=chassis.agent_id,
        log=chassis.sink(),
        rail_set=rail_set if rail_set is not None else chassis.rail_set,
    )


def assemble(chassis: PuspaChassis, config, *, bus=None):
    """Bangun ``AgentLoop`` nanobot dengan wiring Puspa terpasang.

    Lazy import: modul ini (dan paket ``puspa.chassis``) tetap bisa
    diimpor tanpa nanobot; hanya ``assemble`` yang butuh nanobot.
    """
    from nanobot.agent.loop import AgentLoop

    from puspa.guardrails import default_rail_set

    rail_set = chassis.rail_set if chassis.rail_set is not None else default_rail_set()
    # G27: load plugins only when the chassis asks for them
    # (plugin_dirs / PUSPA_PLUGIN_DIRS) or already carries loaded
    # records. With no plugins configured, nothing changes.
    if (chassis.plugin_dirs or os.environ.get("PUSPA_PLUGIN_DIRS")) and not chassis.plugins:
        load_chassis_plugins(chassis)
    registry = build_registry(chassis)
    hooks = [build_hook(chassis, rail_set=rail_set)]
    if chassis.plugins:
        from puspa.plugins.adapters import build_plugin_hook

        plugin_hook = build_plugin_hook(chassis.plugins)
        if plugin_hook is not None:
            hooks.append(plugin_hook)
    return AgentLoop.from_config(
        config,
        bus=bus,
        tool_registry=registry,
        hooks=hooks,
        hook_factories=[build_hook_factory(chassis, rail_set=rail_set)],
    )
