"""Ikat sesi login ke token izin agen untuk satu turn (definisi 2.13 + 2.6).

Alur turn setup Puspa (dipakai backend/PWA, fase 2; diuji di sini):

1. User login -> ``create_session`` (``puspa.accounts``) memberi token sesi.
2. Saat turn dimulai, ``bind_turn`` memvalidasi token sesi itu
   (``validate_session`` -> ``user_id``), lalu menerbitkan token izin
   agen Ed25519 (``issue_token``) untuk ``agen_id`` ini.
3. Pemanggil memverifikasi token string itu sekali
   (``payload_from_token``) dan menaruh payload dict-nya di
   ``RequestContext.attributes["puspa_token"]`` dan/atau ContextVar
   Puspa (``puspa.chassis.context.token_context``). Tool dan registry
   membaca payload dict itu — TIDAK PERNAH string mentah.

Token agen: ``sub_agen_id`` null (token agen, bukan sub-agen),
``tugas_id`` opsional di payload (dipakai ``revoke_all_for_tugas``
saat tugas selesai/dihentikan). Anggaran default: 25 langkah,
100.000 token, Rp0 (batas belanja default definisi 2.6 — pembayaran
apa pun tetap butuh persetujuan + limit yang dinaikkan user).

Otak sesi (``AgentBrain``): satu WorldModel + satu learning loop
yang SAMA lintas turn untuk satu pasangan (user, agen), supaya otak
yang belajar tidak lupa antar turn. ``bind_turn`` mendaftarkan otak
yang diberikan pemanggil, atau memakai (dan bila belum ada,
membuat) otak yang sudah terdaftar untuk pasangan itu;
``get_session_brain`` mengambilnya kembali tanpa memvalidasi sesi.
Registry ini in-memory dan per proses: keyakinan yang harus selamat
dari restart disimpan lewat ``puspa.world.store`` (Postgres), bukan
di sini. Koreksi loop yang tersimpan dimuat kembali lewat
``puspa.memory.loop_store`` saat otak baru dibuat dengan DSN
(``get_session_brain(..., dsn=...)``); tanpa DSN, atau kalau database
tidak bisa dijangkau, otak baru mulai dengan loop kosong seperti
sebelumnya. ``reset_session_brains`` hanya untuk tes.
"""

from __future__ import annotations

import contextlib
import time
from dataclasses import dataclass, field
from typing import Any

from puspa.accounts.user_sessions import validate_session
from puspa.policy.tokens import issue_token, verify_token

__all__ = [
    "AgentBrain",
    "bind_turn",
    "get_session_brain",
    "payload_from_token",
    "reset_session_brains",
]


@dataclass
class AgentBrain:
    """One agent's brain: the world model plus its learning loop.

    Created once per (user_id, agent_id) and shared across turns.
    Both objects are plain in-memory state; see ``puspa.world.store``
    for the Postgres persistence of the world model.
    """

    user_id: str
    agent_id: str
    world_model: Any = field(default=None)
    learning_loop: Any = field(default=None)

    def __post_init__(self) -> None:
        if self.world_model is None:
            from puspa.world.model import WorldModel

            self.world_model = WorldModel()
        if self.learning_loop is None:
            from puspa.memory.learning_loop import LearningLoop

            self.learning_loop = LearningLoop()
        # Route the model's misprediction reports to this session's
        # loop instead of the module-level default loop.
        with contextlib.suppress(Exception):
            self.world_model.learning_loop = self.learning_loop
        # Share the loop's causal ledger with the model (K4 read
        # path) when the loop has one; never wipe a ledger the
        # model already carries.
        with contextlib.suppress(Exception):
            ledger = getattr(self.learning_loop, "causal_ledger", None)
            if ledger is not None:
                self.world_model.causal_ledger = ledger


#: Session brains, keyed by (user_id, agent_id). In-memory only.
_SESSION_BRAINS: dict[tuple[str, str], AgentBrain] = {}


def _load_saved_loop(brain, dsn):
    """Load saved loop corrections into a fresh brain, fail-open.

    A missing table, an unreachable database, or no saved row all
    leave the fresh empty loop untouched: the session keeps working
    exactly as before, it just starts without past corrections.
    Only runs when the caller passed a DSN.
    """
    if dsn is None:
        return
    try:
        from puspa.memory.loop_store import load_learning_loop

        saved = load_learning_loop(brain.user_id, brain.agent_id, dsn=dsn)
    except Exception:
        return
    if saved is None:
        return
    brain.learning_loop = saved
    with contextlib.suppress(Exception):
        brain.world_model.learning_loop = saved


def get_session_brain(user_id: str, agent_id: str, *, dsn=None) -> AgentBrain:
    """The shared brain for one (user, agent) pair, creating it first.

    The same instances come back on every call for the same pair,
    across turns, until ``reset_session_brains`` clears them.
    When a fresh brain is created and ``dsn`` is given, saved loop
    corrections are loaded from Postgres first, so the loop does
    not start empty after a restart.
    """
    key = (str(user_id), str(agent_id))
    brain = _SESSION_BRAINS.get(key)
    if brain is None:
        brain = AgentBrain(user_id=key[0], agent_id=key[1])
        _SESSION_BRAINS[key] = brain
        _load_saved_loop(brain, dsn)
    return brain


def reset_session_brains() -> None:
    """Drop every registered session brain (tests only)."""
    _SESSION_BRAINS.clear()


def bind_turn(
    *,
    session_token: str,
    signing_key,
    public_key,
    agent_id: str,
    tools,
    domains=None,
    connector=None,
    budget=None,
    exp_seconds: int = 3600,
    task_id: str | None = None,
    dsn=None,
    world_model=None,
    learning_loop=None,
) -> tuple[str, str, dict]:
    """Validasi sesi login lalu terbitkan token izin agen.

    Kembalikan ``(user_id, token_string, payload_dict)``. ``payload_dict``
    adalah payload yang sudah diverifikasi (hasil ``verify_token``) dan
    siap ditaruh di ``RequestContext.attributes`` / ContextVar.
    Raise ``SessionError`` kalau sesi tidak valid, ``TokenError`` kalau
    payload tidak bisa diterbitkan.

    Brain: when ``world_model`` / ``learning_loop`` are given they
    become this (user, agent) pair's session brain (replacing any
    brain registered before); when they are not given, the pair's
    existing brain is reused, or a fresh one is created. Either
    way, ``get_session_brain(user_id, agent_id)`` afterwards
    returns the same WorldModel + LearningLoop instances across
    turns, so the learning brain never forgets between turns.
    A fresh brain created here loads saved loop corrections from
    Postgres when ``dsn`` is given (see ``get_session_brain``).
    """
    user_id = validate_session(session_token, dsn=dsn)
    if world_model is not None or learning_loop is not None:
        brain = _SESSION_BRAINS.get((str(user_id), str(agent_id)))
        if brain is None:
            brain = AgentBrain(
                user_id=str(user_id),
                agent_id=str(agent_id),
                world_model=world_model,
                learning_loop=learning_loop,
            )
            _SESSION_BRAINS[(str(user_id), str(agent_id))] = brain
        else:
            if world_model is not None:
                brain.world_model = world_model
            if learning_loop is not None:
                brain.learning_loop = learning_loop
            with contextlib.suppress(Exception):
                brain.world_model.learning_loop = brain.learning_loop
    else:
        get_session_brain(user_id, agent_id, dsn=dsn)
    now = int(time.time())
    payload = {
        "user_id": user_id,
        "agent_id": agent_id,
        "subagent_id": None,
        "tools": list(tools),
        "domains": list(domains or []),
        "connectors": dict(connector or {}),
        "budget": dict(budget or {"step_max": 25, "token_max": 100_000, "rupiah_max": 0}),
        "exp": now + exp_seconds,
        "iat": now,
        "parent": None,
    }
    if task_id is not None:
        payload["task_id"] = task_id
    token = issue_token(payload, signing_key)
    verified = verify_token(token, public_key)
    return user_id, token, verified


def payload_from_token(token: str, public_key) -> dict:
    """Verifikasi token string; kembalikan payload dict. Gagal -> TokenError."""
    return verify_token(token, public_key)
