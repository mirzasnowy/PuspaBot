"""Hook run nanobot untuk Buku Kerja Puspa (definisi 2.3 dan 2.6).

``PuspaHook`` adalah subclass ``AgentHook`` nanobot — titik kait resmi
untuk kustomisasi runner ("Minimal lifecycle surface for shared runner
customization"). Hook di sini HANYA observasi: ia tidak bisa mem-veto
tool (``before_execute_tool`` tidak punya nilai kembali), jadi penegakan
izin tetap di ``PuspaTool``/``PuspaRegistry``. Yang dicatat hook:

- ``before_run`` -> ``tugas_dibuat`` (satu turn = satu tugas di fase 0;
  kalau satu tugas memicu beberapa run, kejadian ganda tidak berbahaya
  di log append-only — proyeksi memakai yang pertama).
- ``after_run`` -> ``tugas_selesai`` + ``pesan_agen`` (konten akhir,
  dipangkas 2000 karakter).
- ``on_error`` -> ``tugas_gagal`` (alasan dari konteks).

Audit tool call (``tool_dipanggil``/``tool_selesai`` + temuan injeksi)
TIDAK dicatat di sini — itu tugas ``PuspaTool`` (satu tempat, supaya
tidak ada pencatatan ganda). Aturan angka yatim (definisi 2.5,
``finalize_content``) belum ditegakkan di fase 0; itu fase 2 bersama
Pemeriksa + renderer.

Penulisan ledger bersifat best-effort (lihat ``make_ledger_sink``):
kegagalan tulis tidak boleh menjatuhkan turn. ``CompositeHook`` nanobot
juga mengisolasi eksepsi hook per-hook, jadi hook yang rusak tidak
merusak loop.

Dipasang lewat konstruktor ``AgentLoop`` (``hooks=[...]``) atau lewat
factory per-turn (``hook_factories=[...]``) — keduanya parameter resmi.
``make_puspa_hook_factory`` mengembalikan ``AgentTurnHookFactory`` yang
dipanggil ``build_agent_turn_hook`` tiap turn.

Rails (NeMo Guardrails pattern, ``puspa/guardrails``): ``PuspaHook``
takes an optional ``rail_set``. The hook stays observation-only,
nanobot gives no veto in ``before_run``, so hard enforcement lives
in two places: ``check_input``/``check_action``, which callers run
BEFORE acting (a violation blocks the action and is recorded
through an existing ledger type), and ``finalize_content``, which
replaces a blocked turn answer with the refusal text (the nanobot
runner calls ``finalize_content`` on every answer, so blocked text
never reaches the user).
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass

from nanobot.agent.hook import AgentHook, AgentTurnHookContext

from puspa.guardrails import REFUSAL_TEXT, RailSet, RailVerdict

__all__ = [
    "HOOK_SKILL_APPROVED",
    "HOOK_SKILL_BLOCKED",
    "HOOK_SKILL_INSTALLED",
    "HOOK_SKILL_REJECTED",
    "HOOK_SKILL_USED",
    "LearnedSkill",
    "LearnedSkillStore",
    "PuspaHook",
    "SkillHookRegistry",
    "check_skill_use",
    "default_skill_registry",
    "load_learned_skills_from_procedures",
    "make_puspa_hook_factory",
]

_MAX_MESSAGE_CHARS = 2000


class PuspaHook(AgentHook):
    """Hook observasi: siklus tugas + pesan akhir ke Buku Kerja.

    With an optional ``rail_set``, this hook also enforces the
    guardrails: input rails run in ``before_run``, output rails run
    in ``finalize_content`` and ``after_run``, and ``check_action``
    is the execution-rail check callers run before acting. A blocked
    turn sets ``rail_blocked`` and every later model answer in that
    turn is replaced by the refusal text.
    """

    def __init__(
        self, *, task_id: str, agent_id: str, log=None, rail_set: RailSet | None = None
    ) -> None:
        super().__init__()
        self.task_id = task_id
        self.agent_id = agent_id
        self._log = log
        self.rail_set = rail_set
        self.rail_blocked = False
        self.rail_block_reason: str | None = None
        self._output_blocked = False

    def _log_event(self, event_type: str, content: dict) -> None:
        if self._log is None:
            return
        with contextlib.suppress(Exception):
            self._log(event_type, content)

    @property
    def violations(self) -> list[dict]:
        """Rail violations recorded in memory for this hook's rail set."""
        if self.rail_set is None:
            return []
        return self.rail_set.list_violations()

    def check_input(self, text: str, *, source: str = "user") -> RailVerdict:
        """Run the input rails. A block marks the turn blocked and is
        recorded as ``task_failed`` (existing ledger type)."""
        if self.rail_set is None:
            return RailVerdict.allow("", "input")
        verdict = self.rail_set.check_input(text, source=source)
        if not verdict.allowed:
            self.rail_blocked = True
            self.rail_block_reason = verdict.reason
            self._log_event(
                "task_failed",
                {
                    "task_id": self.task_id,
                    "agent_id": self.agent_id,
                    "reason": f"rail_blocked: {verdict.reason}",
                },
            )
        return verdict

    def check_output(self, text: str, *, evidence_ids=None) -> RailVerdict:
        """Run the output rails. A block is recorded as a failed
        verification (existing ledger type)."""
        if self.rail_set is None:
            return RailVerdict.allow("", "output")
        verdict = self.rail_set.check_output(text, evidence_ids=evidence_ids)
        if not verdict.allowed:
            self._log_event(
                "verification_completed",
                {
                    "task_id": self.task_id,
                    "passed": False,
                    "reason": f"rail_blocked: {verdict.reason}",
                },
            )
        return verdict

    def check_action(self, action: dict, **extra) -> RailVerdict:
        """Run the execution rails on a proposed action. Callers must
        check the verdict BEFORE executing: a block means no action
        happens. A block is recorded as ``approval_rejected``
        (existing ledger type)."""
        if self.rail_set is None:
            return RailVerdict.allow("", "action")
        verdict = self.rail_set.check_action(action, **extra)
        if not verdict.allowed:
            self._log_event(
                "approval_rejected",
                {
                    "task_id": self.task_id,
                    "agent_id": self.agent_id,
                    "kind": action.get("kind", ""),
                    "reason": f"rail_blocked: {verdict.reason}",
                },
            )
        return verdict

    def finalize_content(self, context, content: str | None) -> str | None:
        """Replace a rail-blocked answer with the refusal text.

        The nanobot runner calls this on every model answer, so it
        is the hard enforcement point for output rails and for
        turns whose input was already blocked. A block is logged
        once per turn through ``check_output``.
        """
        if content is None:
            return None
        if self.rail_blocked:
            return REFUSAL_TEXT
        if self.rail_set is None:
            return content
        if self._output_blocked:
            return REFUSAL_TEXT
        verdict = self.check_output(content)
        if not verdict.allowed:
            self._output_blocked = True
            return REFUSAL_TEXT
        return content

    @staticmethod
    def _last_user_text(messages) -> str:
        """Text of the last user-role message, or empty string."""
        for message in reversed(messages or []):
            if isinstance(message, dict) and message.get("role") == "user":
                content = message.get("content")
                if isinstance(content, str):
                    return content
        return ""

    async def before_run(self, context) -> None:
        if self.rail_set is not None:
            # Input rails on the turn's user message. This cannot
            # veto the nanobot run itself (no veto exists in the
            # hook interface), but it marks the turn blocked, so
            # finalize_content refuses every answer of this turn.
            self.check_input(
                self._last_user_text(getattr(context, "messages", [])),
                source="user",
            )
        self._log_event(
            "task_created",
            {"task_id": self.task_id, "agent_id": self.agent_id},
        )

    async def after_run(self, context) -> None:
        content = {"task_id": self.task_id, "agent_id": self.agent_id}
        final = (context.final_content or "")[:_MAX_MESSAGE_CHARS]
        if final and self.rail_set is not None:
            verdict = self.check_output(final)
            if not verdict.allowed:
                # Blocked draft: no task_completed, only the
                # refusal as the agent message.
                self._log_event("agent_message", {"content": REFUSAL_TEXT})
                return
        if final:
            content["summaries"] = final
        self._log_event("task_completed", content)
        if final:
            self._log_event("agent_message", {"content": final})

    async def on_error(self, context) -> None:
        self._log_event(
            "task_failed",
            {
                "task_id": self.task_id,
                "agent_id": self.agent_id,
                "reason": str(context.error or context.exception or ""),
            },
        )


def make_puspa_hook_factory(task_id: str, agent_id: str, log=None, rail_set: RailSet | None = None):
    """Factory per-turn (``AgentTurnHookFactory``) untuk ``PuspaHook``."""

    def factory(turn_ctx: AgentTurnHookContext) -> AgentHook | None:
        return PuspaHook(task_id=task_id, agent_id=agent_id, log=log, rail_set=rail_set)

    return factory


# ---------------------------------------------------------------------------
# Skill hooks (ECC pattern, rewritten for Puspa).
#
# ECC (affaan-m/ECC, MIT) is a harness with a skill collection and
# hooks: a hook point fires when a skill is installed or used. Puspa
# keeps the hook-point idea and rewrites it around its own rules:
#
# - A hook point fires on skill install and on skill use; listeners
#   are observers only (a broken listener never breaks the run, the
#   same isolation rule as nanobot's CompositeHook).
# - Enforcement lives elsewhere: the signed-skill gate in
#   ``puspa/policy/skill_signing.py`` and the outbound gate in
#   ``puspa/policy/gate.py``. Hooks never approve anything by
#   themselves.
# - Hook events are delivered to a caller-supplied sink, NOT written
#   to the ledger here: the ledger event set is closed in revision 7
#   and has no skill event type.
# ---------------------------------------------------------------------------

#: Hook points for the skill lifecycle.
HOOK_SKILL_INSTALLED = "skill_installed"
HOOK_SKILL_USED = "skill_used"
HOOK_SKILL_APPROVED = "skill_approved"
HOOK_SKILL_REJECTED = "skill_rejected"
HOOK_SKILL_BLOCKED = "skill_blocked"

SKILL_HOOK_POINTS = frozenset(
    {
        HOOK_SKILL_INSTALLED,
        HOOK_SKILL_USED,
        HOOK_SKILL_APPROVED,
        HOOK_SKILL_REJECTED,
        HOOK_SKILL_BLOCKED,
    }
)


class SkillHookRegistry:
    """Registry of listeners per skill hook point.

    ``register(point, callback)`` adds a listener; ``emit(point,
    payload)`` calls every listener for the point and returns their
    results. A listener that raises is isolated: the exception is
    recorded in the returned results and the other listeners still
    run. Unregistering removes one listener.
    """

    def __init__(self) -> None:
        self._listeners: dict[str, list] = {point: [] for point in SKILL_HOOK_POINTS}

    def register(self, point: str, callback) -> None:
        if point not in SKILL_HOOK_POINTS:
            raise ValueError(f"unknown skill hook point: {point!r}")
        if not callable(callback):
            raise ValueError("skill hook callback must be callable")
        self._listeners[point].append(callback)

    def unregister(self, point: str, callback) -> bool:
        if point not in SKILL_HOOK_POINTS:
            raise ValueError(f"unknown skill hook point: {point!r}")
        try:
            self._listeners[point].remove(callback)
            return True
        except ValueError:
            return False

    def listeners(self, point: str) -> list:
        if point not in SKILL_HOOK_POINTS:
            raise ValueError(f"unknown skill hook point: {point!r}")
        return list(self._listeners[point])

    def emit(self, point: str, payload: dict | None = None) -> list[dict]:
        if point not in SKILL_HOOK_POINTS:
            raise ValueError(f"unknown skill hook point: {point!r}")
        payload = dict(payload or {})
        results = []
        for callback in list(self._listeners[point]):
            try:
                results.append({"ok": True, "result": callback(point, payload)})
            except Exception as exc:  # isolate a broken listener
                results.append({"ok": False, "error": str(exc)})
        return results


#: Process-wide default registry (callers may use their own instead).
default_skill_registry = SkillHookRegistry()


# ---------------------------------------------------------------------------
# Learned skills store (hermes-agent / Voyager pattern, read-only here).
#
# The learning loop (planned ``puspa/memory/learning_loop.py``) drafts
# skills from successful tasks and stores them as procedures in
# ``puspa/memory/procedural.py``. This store is a READ-ONLY view over
# that draft store: it never writes, never approves, and never
# activates anything on its own. The hard rule, from the brain repo
# list: a learned skill stays INACTIVE until the user approves it.
# Approval itself happens through the gate in ``puspa/policy/gate.py``
# (outside this module); this store only reports the current state.
# ---------------------------------------------------------------------------

#: Statuses a learned skill can be in. Only "approved" is active.
LEARNED_SKILL_STATUSES = ("pending_approval", "approved", "rejected")


@dataclass(frozen=True)
class LearnedSkill:
    """One learned skill, as the learning loop drafted it."""

    name: str
    source: str = "learning_loop"
    status: str = "pending_approval"
    tools: tuple[str, ...] = ()
    created_from_task_id: str = ""
    approved_by: str = ""

    def __post_init__(self) -> None:
        if self.status not in LEARNED_SKILL_STATUSES:
            raise ValueError(f"unknown learned skill status: {self.status!r}")

    @property
    def is_active(self) -> bool:
        """Active only after user approval. Never active by default."""
        return self.status == "approved"


class LearnedSkillStore:
    """Read-only view over learned skills. No write methods exist.

    Built from plain records (dicts or ``LearnedSkill`` objects), or
    from procedure rows via ``from_procedures``: a procedure in
    ``trial`` status is a learned skill awaiting approval; a
    ``verified`` procedure is an approved one; ``broken`` maps to
    rejected. The store never changes the underlying procedures.
    """

    def __init__(self, skills: list | None = None) -> None:
        self._skills: dict[str, LearnedSkill] = {}
        for record in skills or []:
            skill = (
                record
                if isinstance(record, LearnedSkill)
                else LearnedSkill(
                    name=record["name"],
                    source=record.get("source", "learning_loop"),
                    status=record.get("status", "pending_approval"),
                    tools=tuple(record.get("tools", ())),
                    created_from_task_id=record.get("created_from_task_id", ""),
                    approved_by=record.get("approved_by", ""),
                )
            )
            self._skills[skill.name] = skill

    @classmethod
    def from_procedures(cls, rows: list[dict]) -> LearnedSkillStore:
        """Build a read-only view from ``procedures`` rows (no writes)."""
        status_map = {"trial": "pending_approval", "verified": "approved", "broken": "rejected"}
        skills = []
        for row in rows:
            step = row.get("step") or []
            tools = tuple(
                item.get("tool", "") for item in step if isinstance(item, dict) and item.get("tool")
            )
            skills.append(
                LearnedSkill(
                    name=str(row.get("service", "")),
                    source="procedural",
                    status=status_map.get(str(row.get("status", "trial")), "pending_approval"),
                    tools=tools,
                    created_from_task_id=str(row.get("source_event_id") or ""),
                )
            )
        return cls(skills)

    def get(self, name: str) -> LearnedSkill | None:
        return self._skills.get(name)

    def list_all(self) -> list[LearnedSkill]:
        return list(self._skills.values())

    def list_pending(self) -> list[LearnedSkill]:
        return [s for s in self._skills.values() if s.status == "pending_approval"]

    def list_approved(self) -> list[LearnedSkill]:
        return [s for s in self._skills.values() if s.status == "approved"]

    def is_active(self, name: str) -> bool:
        """False for unknown skills and for unapproved learned skills."""
        skill = self._skills.get(name)
        return bool(skill and skill.is_active)


def load_learned_skills_from_procedures(dsn, user_id: str) -> LearnedSkillStore:
    """Read learned skills from the procedures table (SELECT only).

    Read-only: no INSERT, UPDATE or DELETE is issued here. The
    learning loop owns writes; this function only reads its drafts
    so the hook layer can check them.
    """
    from puspa.db import connect as _db_connect
    from puspa.db import dict_row
    from puspa.memory.schema import resolve_dsn

    with _db_connect(resolve_dsn(dsn), row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT service, step, status, source_event_id FROM procedures "
            "WHERE user_id = %s ORDER BY created_at DESC",
            (user_id,),
        )
        rows = [dict(row) for row in cur.fetchall()]
    return LearnedSkillStore.from_procedures(rows)


def check_skill_use(
    store: LearnedSkillStore, skill_name: str, *, registry: SkillHookRegistry | None = None
) -> bool:
    """Allow a learned skill to be used only if the user approved it.

    Emits ``skill_used`` when allowed and ``skill_blocked`` when the
    skill is unknown or still pending approval. Returns True only for
    an approved skill. This is the hook-layer check; the signed-skill
    gate still applies separately at execution time.
    """
    active = store.is_active(skill_name)
    hook_registry = registry or default_skill_registry
    point = HOOK_SKILL_USED if active else HOOK_SKILL_BLOCKED
    hook_registry.emit(point, {"skill": skill_name, "active": active})
    return active
