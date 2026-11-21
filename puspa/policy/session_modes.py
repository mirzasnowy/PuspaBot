"""Per-session permission modes (COW-13).

One switch per session — read-only, ask-first, full-access — laid
over the permission layers that already exist, not a second
permission system. The mode only changes what the existing gates
do with a write action; it never widens a token, never touches
the K2 gate, the K11 autonomy dial, or the V2-35 tiers, and it
never creates a new audit log (mode switches are kept in the
state's own short history, the same pattern as the K11 dial's
history; action audits keep going to the V2-35 audit sink).

The decision table, for one action in one session:

- Read (changes nothing): allowed in every mode, never asks.
- Write, read-only mode: refused outright, with a clear reason —
  never silently skipped, never run.
- Write, ask-first mode: always goes through the existing
  approval prompt (the localexec prompt for desktop hands, the
  gate's approval request elsewhere). This is the default for a
  new session: safe first, never full-access.
- Write, full-access mode: runs without a per-action prompt —
  EXCEPT a destructive write, which still asks. Full-access is
  not unlimited: the K2/K13 guardrails and the DESTRUCTIVE tier
  still apply, so a destructive action asking in full-access is
  the design, not a bug.

A mode switch applies to the next action at once: no session
restart, no rebuild. The state is in-memory and per session
(one :class:`SessionModeState` per session, owned by whoever
owns the session — the turn loop, the desktop hands, a test);
a restart starts a new session in ask-first, which can only
make the agent more cautious, never bolder.

How an action is recognised as a write (in order — the first
match wins, and the caller should pass what it knows):

1. ``tier`` (a :class:`puspa.computer.tiers.Tier`): READ is a
   read; NORMAL is a write; DESTRUCTIVE is a write that always
   asks. This is the precise path — desktop hands pass it.
2. ``gate_kind`` (a :class:`puspa.policy.gate.Gate` kind such as
   ``"send"`` or ``"delete"``): every gate kind is a write;
   ``"delete"``, ``"prepare_payment"`` and ``"deploy"`` are
   destructive (money and deletes always ask, deploy always
   asks at the gate already).
3. The tool name: the G4 taxonomy (``classify_gate_kind``)
   recognises outbound verbs (send / share / post / pay /
   delete / install); anything else falls back to a small
   read-verb list (``read`` / ``get`` / ``list`` / ``search`` /
   ...), and a tool neither the taxonomy nor the read verbs
   recognise is treated as a write — fail-closed to asking,
   never run free on a guess.

Pattern studied, then rewritten as Puspa's own code (CowAgent,
MIT — idea and pattern only, never copied, never a
dependency): CowAgent switches a session between permission
levels; Puspa's version is this thin layer over its own gate,
dial, and tiers. Pure Python: no database, no network, no LLM
call.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from puspa.policy.approvals import classify_gate_kind
from puspa.policy.gate import KINDS as GATE_KINDS

if TYPE_CHECKING:
    from puspa.computer.tiers import Tier

__all__ = [
    "DEFAULT_MODE",
    "DESTRUCTIVE_GATE_KINDS",
    "MAX_HISTORY",
    "ModeDecision",
    "ModeSwitch",
    "SessionMode",
    "SessionModeDenied",
    "SessionModeState",
]

#: The mode a new session starts in: safe first, never full-access.
DEFAULT_MODE = "ask_first"

#: Gate kinds that are destructive: they always ask, even in
#: full-access (money and deletes always ask, deploy already
#: asks at the gate).
DESTRUCTIVE_GATE_KINDS = frozenset({"delete", "prepare_payment", "deploy"})

#: Mode switches kept in the state's history; older ones drop.
MAX_HISTORY = 100

#: First verbs that mark a tool as a read when neither the tier
#: nor the gate kind is known. Deliberately small: "open" is not
#: here (opening an app writes state, opening a page does not),
#: and an unlisted verb is a write, not a read.
READ_VERBS = frozenset(
    {
        "read",
        "get",
        "list",
        "search",
        "fetch",
        "show",
        "view",
        "snapshot",
        "verify",
        "inspect",
        "describe",
        "lookup",
        "find",
        "query",
        "load",
        "check",
    }
)


class SessionMode(StrEnum):
    """One per-session permission mode."""

    READ_ONLY = "read_only"
    ASK_FIRST = "ask_first"
    FULL_ACCESS = "full_access"


#: Verdicts :meth:`SessionModeState.decide` can return.
VERDICTS = frozenset({"allow", "ask", "deny"})


class SessionModeDenied(Exception):
    """The session mode refuses an action outright.

    Raised by :meth:`SessionModeState.check` when the verdict is
    ``"deny"`` (a write in read-only mode). The action did not
    run, and the message says which mode refused it and what
    would allow it — a refusal is never silent.
    """


def _coerce_mode(mode: SessionMode | str) -> SessionMode:
    """A mode value as a :class:`SessionMode`, or ValueError."""
    try:
        return SessionMode(mode)
    except ValueError:
        raise ValueError(
            f"unknown session permission mode {mode!r}; "
            f"known modes: {[item.value for item in SessionMode]}"
        ) from None


def _tool_verb(tool_name: str) -> str:
    """First verb of a tool name: last dotted segment, first word."""
    name = str(tool_name or "").strip().lower().replace("-", "_").replace("/", ".")
    last = name.split(".")[-1] if name else ""
    return last.split("_")[0] if last else ""


def _classify(
    tool: str, *, tier: Tier | str | None, gate_kind: str | None
) -> tuple[bool, bool, bool]:
    """(is_write, is_destructive, is_recognised) for one action.

    Tier wins when given, then the gate kind, then the tool name
    (G4 taxonomy, then the read-verb list). A tool none of those
    recognise comes back as an unrecognised write: it asks in
    every mode that would otherwise run it, so an unknown action
    never runs free on a guess.
    """
    if tier is not None:
        # Imported here, not at module level: importing
        # ``puspa.computer.tiers`` while ``puspa.policy`` is still
        # initialising runs ``puspa.computer.__init__`` (audit ->
        # ledger -> policy) and fails as a circular import.
        # By call time both packages are loaded.
        from puspa.computer.tiers import Tier

        tier_value = tier if isinstance(tier, Tier) else Tier(tier)
        if tier_value is Tier.READ:
            return False, False, True
        if tier_value is Tier.DESTRUCTIVE:
            return True, True, True
        return True, False, True
    if gate_kind is not None:
        if gate_kind not in GATE_KINDS:
            raise ValueError(f"unknown gate kind {gate_kind!r}; known kinds: {sorted(GATE_KINDS)}")
        return True, gate_kind in DESTRUCTIVE_GATE_KINDS, True
    kind = classify_gate_kind(tool)
    if kind is not None:
        return True, kind in DESTRUCTIVE_GATE_KINDS, True
    if _tool_verb(tool) in READ_VERBS:
        return False, False, True
    # Unrecognised: a write, fail-closed to asking.
    return True, False, False


@dataclass(frozen=True)
class ModeDecision:
    """The verdict for one action under one session mode.

    ``verdict`` is ``"allow"`` (run it), ``"ask"`` (go through
    the existing approval prompt first), or ``"deny"`` (refused
    outright: nothing runs). ``destructive`` marks a write that
    always asks, even in full-access. ``reason`` is one plain
    sentence naming the deciding factor.
    """

    mode: str
    tool: str
    verdict: str
    destructive: bool
    reason: str

    def to_dict(self) -> dict:
        """JSON-safe dict of this decision."""
        return {
            "mode": self.mode,
            "tool": self.tool,
            "verdict": self.verdict,
            "destructive": self.destructive,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ModeSwitch:
    """One recorded mode switch: what the session moved from and to."""

    mode_before: str
    mode_after: str

    def to_dict(self) -> dict:
        """JSON-safe dict of this switch."""
        return {"mode_before": self.mode_before, "mode_after": self.mode_after}


class SessionModeState:
    """One session's permission mode.

    One instance serves one session for its lifetime. The mode
    starts at :data:`DEFAULT_MODE` (ask-first) and the user may
    switch it at any time with :meth:`set_mode`; the switch
    applies to the next action at once. All state is in-memory;
    see the module docstring for the honest restart limit.
    """

    def __init__(self, mode: SessionMode | str = DEFAULT_MODE) -> None:
        """Create a session's mode state, default ask-first.

        Raises ``ValueError`` for an unknown mode.
        """
        self._mode = _coerce_mode(mode)
        self._history: list[ModeSwitch] = []

    # -- reads ---------------------------------------------------------
    @property
    def mode(self) -> SessionMode:
        """The session's mode right now."""
        return self._mode

    def history(self) -> list[ModeSwitch]:
        """The session's mode switches, oldest first. A copy."""
        return list(self._history)

    def to_dict(self) -> dict:
        """JSON-safe dict of this session's mode and its switches."""
        return {
            "mode": self._mode.value,
            "history": [switch.to_dict() for switch in self._history],
        }

    # -- writes --------------------------------------------------------
    def set_mode(self, mode: SessionMode | str) -> SessionMode:
        """Switch the session to ``mode``, effective at once.

        Raises ``ValueError`` for an unknown mode. Switching to
        the mode the session is already in is a no-op: it returns
        the current mode without recording a switch. Returns the
        mode the session was in before the switch.
        """
        new_mode = _coerce_mode(mode)
        previous = self._mode
        if new_mode is previous:
            return previous
        self._mode = new_mode
        self._history.append(ModeSwitch(mode_before=previous.value, mode_after=new_mode.value))
        if len(self._history) > MAX_HISTORY:
            del self._history[: len(self._history) - MAX_HISTORY]
        return previous

    # -- decisions -----------------------------------------------------
    def decide(
        self, tool: str, *, tier: Tier | str | None = None, gate_kind: str | None = None
    ) -> ModeDecision:
        """Decide what one action may do in this session right now.

        Pass the tier when the caller knows it (desktop hands
        do) or the gate kind (outbound actions do); otherwise
        the tool name is classified by the G4 taxonomy and the
        read-verb list. A read is always allowed. A write is
        denied in read-only, asks in ask-first, and runs in
        full-access — unless it is destructive, which asks in
        full-access too.
        """
        is_write, destructive, recognised = _classify(tool, tier=tier, gate_kind=gate_kind)
        mode = self._mode.value
        if not is_write:
            return ModeDecision(
                mode=mode,
                tool=tool,
                verdict="allow",
                destructive=False,
                reason=f"{tool} only reads, so it runs in {mode} mode without asking",
            )
        if self._mode is SessionMode.READ_ONLY:
            return ModeDecision(
                mode=mode,
                tool=tool,
                verdict="deny",
                destructive=destructive,
                reason=(
                    f"session is in read-only mode: {tool} changes something, "
                    "so it is refused; switch the session to ask-first or "
                    "full-access to allow it"
                ),
            )
        if self._mode is SessionMode.ASK_FIRST:
            return ModeDecision(
                mode=mode,
                tool=tool,
                verdict="ask",
                destructive=destructive,
                reason=(
                    f"session is in ask-first mode: {tool} changes something, "
                    "so the user is asked first"
                ),
            )
        if destructive:
            return ModeDecision(
                mode=mode,
                tool=tool,
                verdict="ask",
                destructive=True,
                reason=(
                    f"session is in full-access mode, but {tool} is destructive: "
                    "destructive actions always ask first, even in full-access"
                ),
            )
        if not recognised:
            return ModeDecision(
                mode=mode,
                tool=tool,
                verdict="ask",
                destructive=False,
                reason=(
                    f"session is in full-access mode, but {tool} is not recognised "
                    "as a safe write: an unknown action always asks first, "
                    "never runs free on a guess"
                ),
            )
        return ModeDecision(
            mode=mode,
            tool=tool,
            verdict="allow",
            destructive=False,
            reason=f"session is in full-access mode: {tool} runs without asking",
        )

    def check(
        self, tool: str, *, tier: Tier | str | None = None, gate_kind: str | None = None
    ) -> ModeDecision:
        """Like :meth:`decide`, but a ``"deny"`` verdict raises.

        Returns the decision when the action may proceed (an
        ``"allow"`` or an ``"ask"`` — the caller still runs the
        prompt for an ``"ask"``). Raises
        :class:`SessionModeDenied` when the mode refuses the
        action outright, so the refusal cannot be missed by a
        caller that forgets to read the verdict.
        """
        decision = self.decide(tool, tier=tier, gate_kind=gate_kind)
        if decision.verdict == "deny":
            raise SessionModeDenied(decision.reason)
        return decision
