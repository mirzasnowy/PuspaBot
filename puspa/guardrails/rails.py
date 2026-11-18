"""Programmable rails for Puspa, following the NVIDIA NeMo Guardrails pattern.

NeMo Guardrails (Apache License 2.0, NVIDIA) puts programmable rails
between the application and the model: input rails check the incoming
message, dialog rails steer the conversation flow, retrieval rails
check retrieved chunks, execution rails check tool calls, and output
rails check the draft response before the user sees it. Rules live in
configuration, not in model weights, so the same checks apply no
matter which model answers. The NeMo README states that an input rail
can reject the input and stop further processing, that dialog rails
operate on canonical-form messages and decide whether an action runs
or a predefined response is used instead, and that output rails can
reject a response so it is never returned to the user. Colang is the
NeMo modeling language for dialog flows; Puspa does not embed Colang,
it reimplements the same idea as a small Python state machine
(``DialogFlow`` / ``FlowController``).

This module is a from-scratch rewrite of that pattern for Puspa. No
NeMo Guardrails code is copied, the library is not a dependency, and
no model is called: every rail here is deterministic code, so a rail
verdict is reproducible and testable.

Design rules:

- A rail is one condition plus one action (allow or block). Rails run
  in order inside a ``RailSet``; the first blocking rail wins.
- Fail-closed for actions: a rail that raises blocks instead of
  passing, a payment without a nominal blocks, and a payment with no
  spending limit configured blocks. A violation never silently
  passes.
- Input rails distinguish the source. Text from the user is an
  instruction from a principal, so the imperative-plus-target rule
  does not apply to it; text from a tool or a retrieved chunk is
  untrusted data, so an imperative plus a money or secret target
  inside it blocks. Role takeover and other high-severity injection
  patterns block for every source, including the user.
- Output rails enforce the orphan-number rule from revision 7
  section 2.5: a meaningful number in a draft must come from an
  evidence marker, never typed bare by the model.
- Violations are recorded in memory on the ``RailSet``
  (``list_violations``). No new ledger event type is added: the
  ledger keeps a closed set of 31 types, so the hook layer records
  a violation through an existing type (see ``puspa/chassis/hooks.py``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from puspa.evidence.renderer import MARKER_RE, find_orphan_numbers
from puspa.quarantine import scan

__all__ = [
    "DialogFlow",
    "FlowController",
    "FlowTransition",
    "Rail",
    "RailSet",
    "RailVerdict",
    "action_content_rail",
    "default_dialog_flow",
    "default_rail_set",
    "deployment_rail",
    "evidence_number_rail",
    "injection_rail",
    "integrity_rail",
    "ownership_rail",
    "secret_output_rail",
    "spending_rail",
    "tool_allowlist_rail",
    "topic_rail",
]

_EXCERPT_MAX = 200

#: Default per-task spending cap in rupiah (revision 7 task budget).
DEFAULT_MAX_RUPIAH = 2000

#: Text returned to the user when a turn is refused by a rail.
REFUSAL_TEXT = "Sorry, this request cannot be processed because it breaks a rule."


# ---------------------------------------------------------------------------
# Verdict and rail
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RailVerdict:
    """One rail decision."""

    allowed: bool
    rail: str
    kind: str
    reason: str = ""
    action: str = "allow"  # "allow" or "block"

    @classmethod
    def allow(cls, rail: str, kind: str) -> "RailVerdict":
        return cls(allowed=True, rail=rail, kind=kind, action="allow")

    @classmethod
    def block(cls, rail: str, kind: str, reason: str) -> "RailVerdict":
        return cls(allowed=False, rail=rail, kind=kind, reason=reason, action="block")


@dataclass
class Rail:
    """One programmable rail: a condition plus an allow/block action.

    ``check`` takes a context dict and returns a ``RailVerdict`` when
    the rail fires, or None when the text or action passes this rail.
    A check that raises is treated as a block (fail-closed): a broken
    rail must never silently pass a dangerous input.
    """

    name: str
    kind: str  # "input", "output", or "action"
    check: object = None
    description: str = ""

    def __post_init__(self) -> None:
        if self.kind not in ("input", "output", "action"):
            raise ValueError(f"unknown rail kind: {self.kind!r}")
        if not callable(self.check):
            raise ValueError(f"rail {self.name!r} needs a callable check")

    def evaluate(self, context: dict) -> RailVerdict | None:
        try:
            return self.check(context)
        except Exception as exc:  # fail-closed: a broken rail blocks
            return RailVerdict.block(
                self.name, self.kind, f"rail error: {exc}"
            )


# ---------------------------------------------------------------------------
# Rail set
# ---------------------------------------------------------------------------


class RailSet:
    """An ordered set of rails, run in NeMo order: input, then action,
    then output (each check only runs the rails of its own kind).

    The first blocking rail wins. Every block is recorded in memory
    in ``violations`` with the rail name, the kind, the reason, and a
    short excerpt of the blocked text or action.
    """

    def __init__(self, rails: list[Rail] | None = None) -> None:
        self._rails: list[Rail] = list(rails or [])
        self._violations: list[dict] = []

    @property
    def rails(self) -> list[Rail]:
        return list(self._rails)

    def add(self, rail: Rail) -> None:
        self._rails.append(rail)

    def list_violations(self) -> list[dict]:
        return [dict(v) for v in self._violations]

    def clear_violations(self) -> None:
        self._violations.clear()

    @property
    def violation_count(self) -> int:
        return len(self._violations)

    def _excerpt(self, context: dict) -> str:
        text = context.get("text")
        if isinstance(text, str):
            return text[:_EXCERPT_MAX]
        action = context.get("action")
        if isinstance(action, dict):
            return str(action)[:_EXCERPT_MAX]
        return ""

    def _record(self, verdict: RailVerdict, context: dict) -> None:
        self._violations.append({
            "rail": verdict.rail,
            "kind": verdict.kind,
            "reason": verdict.reason,
            "excerpt": self._excerpt(context),
        })

    def _run(self, kind: str, context: dict) -> RailVerdict:
        for rail in self._rails:
            if rail.kind != kind:
                continue
            verdict = rail.evaluate(context)
            if verdict is None:
                continue
            if not verdict.allowed:
                self._record(verdict, context)
                return verdict
        return RailVerdict.allow("", kind)

    def check_input(self, text: str, *, source: str = "user") -> RailVerdict:
        """Run the input rails on incoming text.

        ``source`` is "user" for a message from the user, "tool" for
        tool output, and "retrieved" for a retrieved chunk. Untrusted
        sources get the full injection rules; the user source only
        gets the high-severity rules.
        """
        return self._run("input", {"text": text or "", "source": source, "kind": "input"})

    def check_output(self, text: str, *, evidence_ids=None) -> RailVerdict:
        """Run the output rails on a draft response.

        ``evidence_ids`` is the set of evidence ids the draft may
        cite; a marker citing an unknown id blocks. When None, the
        unknown-marker check is skipped.
        """
        ids = None if evidence_ids is None else set(evidence_ids)
        return self._run("output", {"text": text or "", "evidence_ids": ids, "kind": "output"})

    def check_action(self, action: dict, **extra) -> RailVerdict:
        """Run the execution rails on a proposed action.

        Extra context is passed through to the rails, for example
        ``allowed_tools=[...]`` or
        ``expected_content_hash="..."``.
        """
        context = {"action": dict(action or {}), "kind": "action"}
        context.update(extra)
        return self._run("action", context)


# ---------------------------------------------------------------------------
# Topic control (input and output)
# ---------------------------------------------------------------------------

#: Denied topics and their keywords (Indonesian and English).
DENIED_TOPICS: dict[str, tuple[str, ...]] = {
    "politics": ("politik", "pilpres", "pemilu", "partai politik", "capres",
                 "cawapres", "politics", "election", "president"),
    "gambling": ("judi", "judol", "slot online", "casino", "taruhan",
                 "gambling", "betting"),
    "adult": ("porno", "porn", "xxx"),
}

#: Known on-topic areas for Puspa: prices, bills, shopping.
KNOWN_TOPICS: dict[str, tuple[str, ...]] = {
    "price": ("harga", "price", "promo", "diskon", "murah", "mahal",
              "bandingkan harga", "compare price"),
    "bill": ("tagihan", "pln", "pdam", "pulsa", "listrik", "bill",
             "bayar tagihan"),
    "shopping": ("beli", "toko", "belanja", "shop", "store", "produk",
                 "product"),
}


def _keyword_regex(keyword: str) -> "re.Pattern[str]":
    return re.compile(r"\b" + re.escape(keyword) + r"\b", re.IGNORECASE)


def _matched_topic(text: str, topics: dict[str, tuple[str, ...]]) -> str | None:
    for topic, keywords in topics.items():
        for keyword in keywords:
            if _keyword_regex(keyword).search(text):
                return topic
    return None


def topic_rail(*, kind: str, allowed_topics=None, denied_topics=None,
               known_topics=None, name: str = "topic") -> Rail:
    """Topic control rail (NeMo topical rails).

    A denied topic always blocks. When ``allowed_topics`` is given,
    a known topic outside the allowed set also blocks. Text that
    matches no known topic passes: a greeting is not off-topic, it
    is just unclassified.
    """
    allowed = None if allowed_topics is None else set(allowed_topics)
    denied = DENIED_TOPICS if denied_topics is None else denied_topics
    known = KNOWN_TOPICS if known_topics is None else known_topics
    all_known = dict(known)
    all_known.update(denied)

    def _check(context: dict) -> RailVerdict | None:
        text = context.get("text", "")
        denied_hit = _matched_topic(text, denied)
        if denied_hit is not None:
            return RailVerdict.block(
                name, kind, f"denied topic: {denied_hit}")
        if allowed is not None:
            hit = _matched_topic(text, all_known)
            if hit is not None and hit not in allowed:
                return RailVerdict.block(name, kind, f"off-topic: {hit}")
        return None

    return Rail(name=name, kind=kind, check=_check,
                description="Topic control: denied topics block, known off-topic blocks when an allowlist is set.")


# ---------------------------------------------------------------------------
# Injection detection beyond regex (input, and action content)
# ---------------------------------------------------------------------------

#: Verbs that address the agent as an instruction.
_IMPERATIVE_RE = re.compile(
    r"(?:^|[.!?]\s+)(kirim|kirimkan|bayar|transfer|ungkapkan|tampilkan|berikan"
    r"|hapus|send|pay|transfer|reveal|show|delete|print)\b"
    r"|\b(kamu|anda|you)\s+(harus|wajib|must)\b",
    re.IGNORECASE,
)

#: A money or secret target: account number, URL, email, secret word.
_TARGET_RE = re.compile(
    r"\b\d{9,}\b|https?://\S+|www\.\S+|[\w.+-]+@[\w-]+\.[\w.]+"
    r"|\b(password|kata\s?sandi|otp|token|api[_\s-]?key|uang|dana|rekening"
    r"|bank\s+account|secret)\b",
    re.IGNORECASE,
)

_SECRECY_RE = re.compile(
    r"jangan\s+(beri\s?tahu|bilang|ceritakan|sampaikan)\b[^.]{0,40}"
    r"(user|pengguna|siapa\s*pun|siapapun|orang\s*lain)"
    r"|do\s+not\s+tell\s+the\s+user\b",
    re.IGNORECASE,
)

_URGENCY_RE = re.compile(
    r"\b(segera|sekarang\s+juga|immediately|urgent|right\s+now)\b",
    re.IGNORECASE,
)

_DATA_SPAN_RE = re.compile(r"\[DATA:\s*(.*?)\]", re.IGNORECASE | re.DOTALL)


def _injection_signals(text: str) -> dict:
    """Deterministic injection signals for one text.

    Goes beyond a single regex list: it combines the existing
    quarantine scan (high-severity findings) with structural
    signals (agent-directed imperative, money or secret target,
    secrecy instruction, urgency) and with a check for
    instructions hidden inside a ``[DATA: ...]`` marked span.
    """
    findings = scan(text)
    high = [f.pattern for f in findings if f.level == "high"]
    imperative = bool(_IMPERATIVE_RE.search(text))
    target = bool(_TARGET_RE.search(text))
    secrecy = bool(_SECRECY_RE.search(text))
    urgency = bool(_URGENCY_RE.search(text))
    data_abuse = False
    for span in _DATA_SPAN_RE.findall(text):
        if (_IMPERATIVE_RE.search(span) or _TARGET_RE.search(span)
                or scan(span) and any(f.level == "high" for f in scan(span))):
            data_abuse = True
            break
    return {
        "high_patterns": high,
        "imperative": imperative,
        "target": target,
        "secrecy": secrecy,
        "urgency": urgency,
        "data_abuse": data_abuse,
    }


def _injection_verdict(text: str, source: str, rail_name: str, kind: str) -> RailVerdict | None:
    signals = _injection_signals(text)
    if signals["high_patterns"]:
        return RailVerdict.block(
            rail_name, kind,
            "injection pattern: " + ", ".join(signals["high_patterns"]))
    if signals["data_abuse"]:
        return RailVerdict.block(
            rail_name, kind, "instruction hidden inside marked data")
    if source == "user":
        # The user is a principal: their own imperative is an
        # instruction, not an injection. Only high-severity
        # patterns (handled above) block user text.
        return None
    if signals["imperative"] and signals["target"]:
        return RailVerdict.block(
            rail_name, kind,
            "imperative with money or secret target in untrusted text")
    if signals["secrecy"] and (signals["imperative"] or signals["target"]):
        return RailVerdict.block(
            rail_name, kind, "secrecy instruction in untrusted text")
    strong = sum([
        signals["imperative"], signals["target"],
        signals["secrecy"], signals["urgency"],
    ])
    if strong >= 3:
        return RailVerdict.block(
            rail_name, kind, "multiple injection signals in untrusted text")
    return None


def injection_rail(name: str = "injection") -> Rail:
    """Input rail: prompt injection beyond single-regex matching."""

    def _check(context: dict) -> RailVerdict | None:
        return _injection_verdict(
            context.get("text", ""), context.get("source", "user"), name, "input")

    return Rail(name=name, kind="input", check=_check,
                description="Injection detection: quarantine scan plus structural signals, stricter for untrusted sources.")


def action_content_rail(name: str = "action_content") -> Rail:
    """Execution rail: an action whose content carries injected text.

    A send action whose message body is itself an injection attempt
    (for example copied from a poisoned listing) blocks before the
    action reaches the gate.
    """

    def _check(context: dict) -> RailVerdict | None:
        action = context.get("action", {})
        if action.get("kind") != "send":
            return None
        content = action.get("content")
        if not isinstance(content, str) or not content:
            return None
        return _injection_verdict(content, "tool", name, "action")

    return Rail(name=name, kind="action", check=_check,
                description="Send actions carrying injected text block before the gate.")


# ---------------------------------------------------------------------------
# Output rails
# ---------------------------------------------------------------------------


def evidence_number_rail(name: str = "evidence_numbers") -> Rail:
    """Output rail: numbers must come from evidence (revision 7 section 2.5).

    A bare meaningful number in the draft (a rupiah amount, a date,
    a percent) is an orphan number and blocks. A marker citing an
    evidence id outside the supplied book also blocks. When no
    evidence book is supplied, only the orphan check runs.
    """

    def _check(context: dict) -> RailVerdict | None:
        text = context.get("text", "")
        orphans = find_orphan_numbers(text)
        if orphans:
            return RailVerdict.block(
                name, "output",
                "number without evidence: " + ", ".join(orphans))
        evidence_ids = context.get("evidence_ids")
        if evidence_ids is not None:
            unknown = [
                m.group("evidence_id")
                for m in MARKER_RE.finditer(text)
                if m.group("evidence_id") not in evidence_ids
            ]
            if unknown:
                return RailVerdict.block(
                    name, "output",
                    "unknown evidence: " + ", ".join(unknown))
        return None

    return Rail(name=name, kind="output", check=_check,
                description="Orphan numbers block; markers must cite known evidence.")


def secret_output_rail(name: str = "secret_output") -> Rail:
    """Output rail: the draft must not leak credentials or secrets."""

    _SECRET_VALUE_RE = re.compile(
        r"(api[_\s-]?key|password|kata\s?sandi|otp)\s*[:=]\s*\S{6,}",
        re.IGNORECASE,
    )

    def _check(context: dict) -> RailVerdict | None:
        text = context.get("text", "")
        findings = scan(text)
        leaking = [f.pattern for f in findings
                   if f.level == "high" and f.pattern in (
                       "minta kredensial", "send/transfer secret",
                       "perintah kirim/bayar rahasia")]
        if leaking:
            return RailVerdict.block(
                name, "output", "secret leak pattern: " + ", ".join(leaking))
        if _SECRET_VALUE_RE.search(text):
            return RailVerdict.block(name, "output", "secret value in output")
        return None

    return Rail(name=name, kind="output", check=_check,
                description="Credential or secret values in a draft response block.")


# ---------------------------------------------------------------------------
# Execution rails (tool and action checks, NeMo execution rails)
# ---------------------------------------------------------------------------


def spending_rail(max_rupiah: int | None, name: str = "spending") -> Rail:
    """Execution rail: spending limit for payment actions.

    A payment without a nominal blocks (the amount cannot be
    checked), and a payment with no limit configured blocks
    (fail-closed, like the gate without a spending enforcer).
    """

    def _check(context: dict) -> RailVerdict | None:
        action = context.get("action", {})
        if action.get("kind") not in ("prepare_payment", "pay", "payment"):
            return None
        nominal = action.get("nominal")
        if nominal is None:
            return RailVerdict.block(
                name, "action", "payment without a nominal amount")
        if max_rupiah is None:
            return RailVerdict.block(
                name, "action", "no spending limit configured")
        if nominal > max_rupiah:
            return RailVerdict.block(
                name, "action",
                f"nominal Rp{nominal} exceeds limit Rp{max_rupiah}")
        return None

    return Rail(name=name, kind="action", check=_check,
                description="Payment actions must carry a nominal within the limit.")


def tool_allowlist_rail(name: str = "tool_allowlist") -> Rail:
    """Execution rail: a tool call must be in the allowed tool set.

    The allowed set comes from the check context
    (``allowed_tools``); when the caller supplies none, this rail
    passes and the token check remains the enforcer.
    """

    def _check(context: dict) -> RailVerdict | None:
        action = context.get("action", {})
        allowed = context.get("allowed_tools")
        tool = action.get("tool")
        if tool is None or allowed is None:
            return None
        if tool not in set(allowed):
            return RailVerdict.block(
                name, "action", f"tool not allowed: {tool}")
        return None

    return Rail(name=name, kind="action", check=_check,
                description="Tool calls outside the allowed set block.")


def ownership_rail(name: str = "ownership") -> Rail:
    """Execution rail: an action may only touch its own owner's data.

    When an action carries both ``owner`` and ``requester`` and they
    differ, the action is a cross-user read or write and blocks.
    """

    def _check(context: dict) -> RailVerdict | None:
        action = context.get("action", {})
        owner = action.get("owner")
        requester = action.get("requester")
        if owner is None or requester is None:
            return None
        if owner != requester:
            return RailVerdict.block(
                name, "action", "cross-user access denied")
        return None

    return Rail(name=name, kind="action", check=_check,
                description="Cross-user reads and writes block.")


def deployment_rail(name: str = "deployment") -> Rail:
    """Execution rail: only signed artifacts may be deployed."""

    def _check(context: dict) -> RailVerdict | None:
        action = context.get("action", {})
        if action.get("kind") != "deploy":
            return None
        if action.get("signed") is not True:
            return RailVerdict.block(
                name, "action", "deploy of an unsigned artifact")
        return None

    return Rail(name=name, kind="action", check=_check,
                description="Unsigned deploys block.")


def integrity_rail(name: str = "integrity") -> Rail:
    """Execution rail: action content must match its approved hash.

    The caller passes ``expected_content_hash`` in the check
    context; a ``content_hash`` on the action that differs blocks,
    which is the rail-level form of the gate's hash binding.
    """

    def _check(context: dict) -> RailVerdict | None:
        action = context.get("action", {})
        expected = context.get("expected_content_hash")
        actual = action.get("content_hash")
        if expected is None or actual is None:
            return None
        if actual != expected:
            return RailVerdict.block(
                name, "action", "action content changed after approval")
        return None

    return Rail(name=name, kind="action", check=_check,
                description="Tampered action content blocks.")


# ---------------------------------------------------------------------------
# Default rail set (Puspa policy)
# ---------------------------------------------------------------------------


def default_rail_set(*, max_rupiah: int | None = DEFAULT_MAX_RUPIAH,
                     allowed_topics=None) -> RailSet:
    """The Puspa default rail set, in NeMo order.

    Input rails (topic control, injection), output rails (evidence
    numbers, topic control, secret leak), execution rails
    (spending, tool allowlist, ownership, deployment, integrity,
    action content). Allowed topics default to price, bill, and
    shopping; the spending limit defaults to the per-task cap.
    """
    allowed = {"price", "bill", "shopping"} if allowed_topics is None else set(allowed_topics)
    return RailSet([
        topic_rail(kind="input", allowed_topics=allowed),
        injection_rail(),
        evidence_number_rail(),
        topic_rail(kind="output", allowed_topics=allowed),
        secret_output_rail(),
        spending_rail(max_rupiah),
        tool_allowlist_rail(),
        ownership_rail(),
        deployment_rail(),
        integrity_rail(),
        action_content_rail(),
    ])


# ---------------------------------------------------------------------------
# Dialog flow (Colang-style flow as a small state machine)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FlowTransition:
    """One allowed step of a dialog flow.

    ``guard`` takes the trigger context dict and returns
    (allowed, reason); a failing guard blocks the transition
    without changing the state.
    """

    from_state: str
    event: str
    to_state: str
    guard: object = None
    description: str = ""


@dataclass
class DialogFlow:
    """A dialog flow: states plus the transitions between them.

    This is the Colang ``define flow`` idea in miniature: a user
    intent (event) moves the conversation from one state to the
    next, and a guard can refuse the move. No Colang syntax is
    parsed; flows are plain Python data, so they are testable
    without a model.
    """

    states: frozenset = frozenset({"idle", "done"})
    transitions: tuple = ()
    initial: str = "idle"

    def transition_for(self, state: str, event: str) -> FlowTransition | None:
        for transition in self.transitions:
            if transition.from_state == state and transition.event == event:
                return transition
        return None


class FlowController:
    """Runs a ``DialogFlow`` across turns.

    ``trigger`` returns (allowed, reason). A blocked trigger leaves
    the state unchanged. Every trigger, allowed or blocked, is
    recorded in ``history``.
    """

    def __init__(self, flow: DialogFlow) -> None:
        self.flow = flow
        self.state = flow.initial
        self.history: list[dict] = []

    def trigger(self, event: str, context: dict | None = None) -> tuple[bool, str]:
        context = dict(context or {})
        transition = self.flow.transition_for(self.state, event)
        if transition is None:
            reason = f"no transition: {self.state} --{event}--> ?"
            self.history.append({"event": event, "from": self.state,
                                 "to": self.state, "allowed": False, "reason": reason})
            return False, reason
        if transition.guard is not None:
            allowed, reason = transition.guard(context)
            if not allowed:
                self.history.append({"event": event, "from": self.state,
                                     "to": self.state, "allowed": False, "reason": reason})
                return False, reason
        self.history.append({"event": event, "from": self.state,
                             "to": transition.to_state, "allowed": True, "reason": ""})
        self.state = transition.to_state
        return True, ""


def _not_outbound_pending(context: dict) -> tuple[bool, str]:
    if context.get("outbound_pending"):
        return False, "outbound action needs approval first"
    return True, ""


def _approved(context: dict) -> tuple[bool, str]:
    if context.get("approved") is True:
        return True, ""
    return False, "approval not granted"


def _outbound_present(context: dict) -> tuple[bool, str]:
    if context.get("outbound"):
        return True, ""
    return False, "no outbound step, use present instead"


def default_dialog_flow() -> DialogFlow:
    """The Puspa turn flow: idle, collecting, awaiting approval,
    presenting, done.

    A plan with an outbound step must pass through approval before
    presenting; presenting while approval is pending blocks, and
    approval only counts when the context says it was granted.
    """
    states = frozenset({"idle", "collecting", "awaiting_approval", "presenting", "done"})
    transitions = (
        FlowTransition("idle", "start", "collecting"),
        FlowTransition("collecting", "plan_ready", "awaiting_approval",
                       guard=_outbound_present,
                       description="Outbound plan waits for approval."),
        FlowTransition("collecting", "present", "presenting",
                       guard=_not_outbound_pending,
                       description="No outbound step, present directly."),
        FlowTransition("awaiting_approval", "approved", "presenting",
                       guard=_approved,
                       description="Approved plan may present."),
        FlowTransition("awaiting_approval", "rejected", "done"),
        FlowTransition("awaiting_approval", "cancel", "done"),
        FlowTransition("collecting", "cancel", "done"),
        FlowTransition("idle", "cancel", "done"),
        FlowTransition("presenting", "done", "done"),
    )
    return DialogFlow(states=states, transitions=transitions, initial="idle")
