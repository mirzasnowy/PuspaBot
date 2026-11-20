"""Approval taxonomy (G4, maturity phase).

Puspa already had a single outbound gate (``puspa.policy.gate.Gate``:
content-hash binding, 24-hour token expiry). Every approval looked
the same. This module adds the taxonomy on top of it, following the
pattern in OpenBot's ``docs/approvals.md`` + ``approval-rules/``
(rewritten as Puspa code, never copied verbatim; see ``NOTICE.md``
and ``REUSE.md``):

- Action kinds that always ask in every edition: delete, send to
  another person, share, post, pay, change account password or
  settings, install a skill. Tiered answers: allow once / for this conversation /
  always. An admin can close one kind for everyone (ask every time,
  or members do it themselves). Rules come from three owners (own,
  admin, allowed for this conversation) and the strictest wins.
- Auto-review: a deterministic pre-check that flags a request
  before a human sees it (unrecognized action, payment without an
  amount, credential change).
- Extra vigilant: a personal switch, default off, that makes every
  action ask even when an "always" answer exists.
- Tasks from check-in ask before every change, whatever the answers
  say.

Action recognition reads the TOOL NAME (first verb) and the BUTTON
LABEL, never the content. Payment is recognized from the word
``payment`` anywhere in the action or the page, from tool names
(transfer / payout / order / payroll / plan upgrade /
subscription), and from button labels (Transfer / Withdraw / Pay /
Buy / Place your order / Subscribe).

Credential changes can NEVER be approved by any answer: they are
handed to a human (fail-closed to a human). Anti-double-approval:
when the gate is asking, the bot is told through the
``ASKING_GUIDANCE`` marker and must not place its own approval
card; destructive tools are marked in their description.

Known recognition gaps (documented honestly, like OpenBot does):
see ``docs/approvals.md``. The short version: a button without a
label, a keyboard shortcut, or a tool whose name hides its verb
("do_it", "run") is not recognized and therefore asks every time
(fail-closed to asking), and a payment tool named without any of the
listed signals is missed until the auto-review or the human catches
it.
- (E-10, plan 6.3) An "always" answer can now be narrowed to a
  target pattern (recipient, domain, path glob) and a nominal cap,
  so it only ever applies to the same kind of action it was
  granted for. In beta mode, "always" is disabled entirely for
  ``pay`` and ``send_to_other``.
- (E-10, plan 6.2) What the user sees is rendered server-side from
  the structured arguments by ``puspa.policy.approval_cards``;
  this module's :meth:`ApprovalPolicy.render_card` is the seam.
"""

from __future__ import annotations

import fnmatch
import hashlib
import time
import uuid
from dataclasses import dataclass, field

from .approval_cards import render_approval_card
from .gate import Gate, GateDenied

__all__ = [
    "ACTION_KINDS",
    "ANSWER_TTL_SECONDS",
    "ASKING_GUIDANCE_MARKER",
    "BETA_ALWAYS_FORBIDDEN_KINDS",
    "DESTRUCTIVE_MARKER",
    "GATE_KIND_FOR_ACTION",
    "ApprovalAnswer",
    "ApprovalDecision",
    "ApprovalPolicy",
    "ApprovalRule",
    "ApprovalStore",
    "AutoReviewFinding",
    "Classification",
    "GuidanceState",
    "auto_review",
    "classify_action",
    "classify_gate_kind",
    "is_asking_guidance",
    "is_credential_change",
    "is_destructive",
    "mark_asking_guidance",
    "mark_destructive",
]

#: Action kinds that always ask in every edition. ``install`` is
#: a skill install from the marketplace (G30): a local enablement,
#: not an outbound action — it always asks, and its tiered answers
#: live in the store like the other kinds.
ACTION_KINDS = frozenset(
    {
        "delete",
        "send_to_other",
        "share",
        "post",
        "pay",
        "change_credentials",
        "install",
    }
)

#: Taxonomy kind -> Gate kind. ``change_credentials`` has no Gate
#: kind on purpose: it never reaches the gate. ``install`` is the
#: same: a local enablement, never an outbound action, so the
#: policy ``request`` path (which files with the gate) refuses it
#: and the install flow asks through ``decide`` + the store
#: instead (see ``puspa.marketplace.discovery``).
GATE_KIND_FOR_ACTION = {
    "delete": "delete",
    "send_to_other": "send",
    "share": "send",
    "post": "send",
    "pay": "prepare_payment",
    "change_credentials": None,
    "install": None,
}

#: Tiered answers (OpenBot: Allow once / for this conversation /
#: always). Every answer expires after 24 hours, the same window
#: the gate uses for tokens.
ANSWER_SCOPES = frozenset({"once", "conversation", "always"})

#: Answer lifetime in seconds (24 hours).
ANSWER_TTL_SECONDS = 24 * 3600

#: Kinds for which "always" is disabled entirely in beta (plan
#: 6.3): a standing yes to moving money, or to messaging someone
#: else, is exactly the approval fatigue the beta must not
#: create. Enforced by ``ApprovalStore`` and ``ApprovalPolicy``
#: when ``beta_mode`` is on.
BETA_ALWAYS_FORBIDDEN_KINDS = frozenset({"pay", "send_to_other"})

#: Argument keys read for each "always" narrowing axis.
_RECIPIENT_KEYS = ("recipient", "recipient_name", "recipient_account", "payee", "to", "target")
_DOMAIN_KEYS = ("domain", "host", "site")
_PATH_KEYS = ("path", "url", "target")
_NOMINAL_KEYS = ("nominal", "amount", "amount_rupiah")


def _glob_matches(pattern: str, value: str) -> bool:
    """Case-insensitive glob match (``*`` wildcards allowed)."""
    return fnmatch.fnmatchcase(value.lower(), pattern.lower())


def _target_values(content: dict | None) -> dict[str, list[str]]:
    """Candidate values per narrowing axis, from structured args."""
    content = content or {}
    values: dict[str, list[str]] = {"recipient": [], "domain": [], "path": []}
    for keys, axis in (
        (_RECIPIENT_KEYS, "recipient"),
        (_DOMAIN_KEYS, "domain"),
        (_PATH_KEYS, "path"),
    ):
        for key in keys:
            value = content.get(key)
            if isinstance(value, str) and value.strip():
                values[axis].append(value.strip())
    return values


def _nominal_of(content: dict | None) -> int | float | None:
    """Nominal amount from structured args, or None when absent."""
    for key in _NOMINAL_KEYS:
        value = (content or {}).get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            return value
    return None


#: Rule owners. ``conversation`` is a rule allowed for this
#: conversation only; ``admin`` is a rule an admin set for everyone.
RULE_OWNERS = frozenset({"own", "admin", "conversation"})

#: Admin closure modes for one kind. ``ask_every_time`` forces a
#: question even when an "always" answer exists.
#: ``members_self_serve`` means the bot must not perform this kind
#: at all: members do it themselves.
RULE_MODES = frozenset({"ask_every_time", "members_self_serve"})

#: Marker the gate puts on its question so the bot knows the gate
#: is already asking (anti-double-approval).
ASKING_GUIDANCE_MARKER = "ASKING_GUIDANCE"

#: Marker appended to a destructive tool's description.
DESTRUCTIVE_MARKER = "[destructive: requires approval]"

# -- recognition signals (tool name first verb / button label) --------

#: Tool names that move money (matched on the normalized tool
#: name, case-insensitive). "plan upgrade" also matches the
#: underscored / reversed spellings a registry may use.
_PAYMENT_TOOL_WORDS = (
    "transfer",
    "payout",
    "order",
    "payroll",
    "subscription",
    "plan upgrade",
    "upgrade plan",
)

#: Button labels that mean "pay" (normalized, case-insensitive).
_PAYMENT_BUTTON_LABELS = (
    "transfer",
    "withdraw",
    "pay",
    "buy",
    "place your order",
    "subscribe",
)

#: Words that mean a credential change, in a tool name or a button
#: label. Any match makes the action un-approvable.
_CREDENTIAL_WORDS = (
    "change password",
    "update password",
    "reset password",
    "change credential",
    "update credential",
    "rotate secret",
    "change secret",
    "update api key",
    "change api key",
    "rotate api key",
    "change login",
    "update login",
)

#: First-verb mapping: action word -> taxonomy kind. The word is
#: the last dotted segment of the tool name (``gmail.send`` -> the
#: verb is ``send``), lowercased, split on separators.
_VERB_TO_KIND = {
    "delete": "delete",
    "remove": "delete",
    "destroy": "delete",
    "purge": "delete",
    "install": "install",
    "send": "send_to_other",
    "message": "send_to_other",
    "email": "send_to_other",
    "mail": "send_to_other",
    "share": "share",
    "post": "post",
    "publish": "post",
    # Aliases already present in Puspa's own tool registry
    # (``gmail.kirim``); recognition has to read the names that
    # exist, it does not invent new ones.
    "kirim": "send_to_other",
    "hapus": "delete",
    "bayar": "pay",
}

#: Button-label first-word mapping (same idea as the tool verbs).
_LABEL_TO_KIND = {
    "delete": "delete",
    "remove": "delete",
    "send": "send_to_other",
    "share": "share",
    "post": "post",
    "publish": "post",
    "install": "install",
}


def _normalize(text: str | None) -> str:
    """Lowercase, separators folded to spaces, runs collapsed.

    Underscores and hyphens become spaces so "change_password",
    "change-password" and "change password" all match the same
    recognition words.
    """
    raw = str(text or "").strip().lower().replace("_", " ").replace("-", " ")
    return " ".join(raw.split())


def _action_word(tool_name: str) -> str:
    """First verb of a tool name: last dotted segment, first word."""
    name = str(tool_name or "").strip().lower().replace("-", "_").replace("/", ".")
    last = name.split(".")[-1] if name else ""
    return last.split("_")[0] if last else ""


def _label_word(button_label: str | None) -> str:
    return _normalize(button_label).split(" ")[0] if button_label else ""


def is_credential_change(tool_name: str, button_label: str | None = None) -> bool:
    """True when the action changes credentials (password, secret,
    API key, login). Such an action can never be approved."""
    haystacks = [_normalize(tool_name), _normalize(button_label)]
    return any(word in hay for word in _CREDENTIAL_WORDS for hay in haystacks)


def _is_payment(tool_name: str, button_label: str | None, page_text: str | None) -> bool:
    """Payment signals, in order: the word ``payment`` anywhere in
    the action or the page, a payment tool name, a payment button."""
    if "payment" in _normalize(tool_name):
        return True
    if "payment" in _normalize(button_label):
        return True
    if "payment" in _normalize(page_text):
        return True
    word = _action_word(tool_name)
    normalized_name = _normalize(tool_name)
    words = set(normalized_name.split())
    for token in _PAYMENT_TOOL_WORDS:
        # Single words must match whole ("order" must not fire on
        # "recorder"); multi-word names match as a phrase.
        if " " in token:
            if token in normalized_name:
                return True
        elif token in words:
            return True
    if word.startswith("pay"):
        return True
    return _normalize(button_label) in _PAYMENT_BUTTON_LABELS


@dataclass(frozen=True)
class Classification:
    """What an action was recognized as, and from which signals."""

    kind: str | None
    signals: tuple[str, ...] = ()
    recognized: bool = False


def classify_action(
    tool_name: str, *, button_label: str | None = None, page_text: str | None = None
) -> Classification:
    """Recognize an action kind from the tool name and button label.

    The content is never read: recognition uses the first verb of
    the tool name and the button label only. An unrecognized action
    returns ``kind=None`` so the caller asks the user (fail-closed
    to asking).
    """
    if is_credential_change(tool_name, button_label):
        return Classification(
            kind="change_credentials",
            signals=("credential_change",),
            recognized=True,
        )
    if _is_payment(tool_name, button_label, page_text):
        signals = ("payment",)
        if _normalize(button_label) in _PAYMENT_BUTTON_LABELS:
            signals = ("payment_button",)
        return Classification(kind="pay", signals=signals, recognized=True)
    word = _action_word(tool_name)
    if word in _VERB_TO_KIND:
        return Classification(
            kind=_VERB_TO_KIND[word], signals=(f"tool_verb:{word}",), recognized=True
        )
    label_word = _label_word(button_label)
    if label_word in _LABEL_TO_KIND:
        return Classification(
            kind=_LABEL_TO_KIND[label_word],
            signals=(f"button_label:{label_word}",),
            recognized=True,
        )
    return Classification(kind=None, signals=(), recognized=False)


def classify_gate_kind(
    tool_name: str, *, button_label: str | None = None, page_text: str | None = None
) -> str | None:
    """Gate kind for a tool name, or None when it is not an outbound
    action. This is the fallback ``puspa.chassis.tools.kind_for_tool``
    uses for tool names outside the gate's own table."""
    classification = classify_action(tool_name, button_label=button_label, page_text=page_text)
    if classification.kind is None:
        return None
    return GATE_KIND_FOR_ACTION.get(classification.kind)


# -- auto-review --------------------------------------------------------


@dataclass(frozen=True)
class AutoReviewFinding:
    """One deterministic auto-review flag on a request."""

    code: str
    detail: str


def auto_review(classification: Classification, content: dict | None) -> list[AutoReviewFinding]:
    """Deterministic pre-check before a human sees the request.

    Never silently allows: every finding forces the decision to
    "ask" (or worse). The check reads the action shape, not the
    user's words.
    """
    findings: list[AutoReviewFinding] = []
    content = content or {}
    if not classification.recognized or classification.kind is None:
        findings.append(
            AutoReviewFinding(
                "unrecognized_action",
                "action kind could not be recognized from the tool name or button label",
            )
        )
        return findings
    if classification.kind == "change_credentials":
        findings.append(
            AutoReviewFinding(
                "credential_change_never_approvable",
                "credential changes are never approved by an answer; a human must do it",
            )
        )
    if classification.kind == "pay":
        nominal = content.get("nominal", content.get("amount"))
        if nominal is None:
            findings.append(
                AutoReviewFinding("payment_without_amount", "payment without a nominal amount")
            )
    if classification.kind == "delete" and not content.get("target"):
        findings.append(AutoReviewFinding("delete_without_target", "delete without a named target"))
    if classification.kind == "send_to_other" and not content.get("recipient"):
        findings.append(AutoReviewFinding("send_without_recipient", "send without a recipient"))
    return findings


# -- tiered answers -----------------------------------------------------


def _answer_hash(content: dict | None) -> str:
    """Content hash binding an answer to the exact approved action
    (same canonical hash idea as the gate)."""
    if not content:
        return ""
    import json

    canonical = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass
class ApprovalAnswer:
    """One tiered answer from the user.

    An "always" answer may be narrowed (E-10, plan 6.3): the
    ``*_pattern`` fields are globs the action's target must match
    (recipient, domain, path), and ``max_nominal`` is the highest
    amount the answer covers. An answer with no narrowing fields
    is unconstrained, as before. Narrowing is fail-closed: when a
    pattern is set and the action carries no value on that axis,
    the answer does not apply.
    """

    kind: str
    scope: str  # "once" | "conversation" | "always"
    answer_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    conversation_id: str | None = None
    content_hash: str = ""
    granted_at: float = field(default_factory=time.time)
    granted_by: str = "user"
    recipient_pattern: str | None = None
    domain_pattern: str | None = None
    path_pattern: str | None = None
    max_nominal: int | float | None = None

    def expired(self, now: float | None = None) -> bool:
        """All answers expire 24 hours after they were granted."""
        now = time.time() if now is None else now
        return now - self.granted_at >= ANSWER_TTL_SECONDS

    def _narrowing_matches(self, content: dict | None) -> bool:
        """True when the action fits this answer's narrowing.

        Unconstrained answers match everything. A set pattern
        needs a matching value on its axis; a set cap needs a
        nominal at or under it. No value on an axis the answer
        narrows means no match.
        """
        if (
            self.recipient_pattern is None
            and self.domain_pattern is None
            and self.path_pattern is None
            and self.max_nominal is None
        ):
            return True
        if content is None:
            return False
        values = _target_values(content)
        for pattern, axis in (
            (self.recipient_pattern, "recipient"),
            (self.domain_pattern, "domain"),
            (self.path_pattern, "path"),
        ):
            if pattern is not None and not any(
                _glob_matches(pattern, value) for value in values[axis]
            ):
                return False
        if self.max_nominal is not None:
            nominal = _nominal_of(content)
            if nominal is None or nominal > self.max_nominal:
                return False
        return True

    def usable_for(
        self,
        *,
        conversation_id: str | None,
        content_hash: str = "",
        content: dict | None = None,
        beta_mode: bool = False,
        now: float | None = None,
    ) -> bool:
        """True when this answer covers this action right now."""
        if self.expired(now):
            return False
        if self.scope == "once":
            # "Allow once" covers exactly the approved content.
            return bool(content_hash) and content_hash == self.content_hash
        if self.scope == "conversation":
            return (
                conversation_id is not None
                and conversation_id == self.conversation_id
                and self._narrowing_matches(content)
            )
        if self.scope == "always":
            # Beta: "always" never applies to pay / send_to_other,
            # even to an answer recorded before beta mode was on.
            if beta_mode and self.kind in BETA_ALWAYS_FORBIDDEN_KINDS:
                return False
            return self._narrowing_matches(content)
        return False


class ApprovalStore:
    """In-memory store of tiered answers.

    Answers are keyed by kind; lookup prefers the narrowest usable
    answer ("once" for this exact content, then "conversation",
    then "always"). Expired answers never apply. With
    ``beta_mode`` on, recording an "always" answer for ``pay`` or
    ``send_to_other`` is refused, and a stored one never applies.
    """

    def __init__(self, *, beta_mode: bool = False) -> None:
        self.beta_mode = beta_mode
        self._answers: list[ApprovalAnswer] = []
        self._consumed: set[str] = set()

    def record(
        self,
        kind: str,
        scope: str,
        *,
        conversation_id: str | None = None,
        content: dict | None = None,
        granted_by: str = "user",
        granted_at: float | None = None,
        recipient_pattern: str | None = None,
        domain_pattern: str | None = None,
        path_pattern: str | None = None,
        max_nominal: int | float | None = None,
    ) -> ApprovalAnswer:
        if kind not in ACTION_KINDS:
            raise ValueError(f"unknown action kind: {kind!r}")
        if scope not in ANSWER_SCOPES:
            raise ValueError(f"unknown answer scope: {scope!r}")
        if scope == "conversation" and not conversation_id:
            raise ValueError("a conversation answer needs a conversation_id")
        if scope == "always" and self.beta_mode and kind in BETA_ALWAYS_FORBIDDEN_KINDS:
            raise ValueError(
                f"'always' is disabled in beta for {kind!r}: the user is asked every time"
            )
        if max_nominal is not None and (
            isinstance(max_nominal, bool)
            or not isinstance(max_nominal, (int, float))
            or max_nominal < 0
        ):
            raise ValueError("max_nominal must be a non-negative number")
        answer = ApprovalAnswer(
            kind=kind,
            scope=scope,
            conversation_id=conversation_id,
            content_hash=_answer_hash(content),
            granted_at=time.time() if granted_at is None else granted_at,
            granted_by=granted_by,
            recipient_pattern=recipient_pattern,
            domain_pattern=domain_pattern,
            path_pattern=path_pattern,
            max_nominal=max_nominal,
        )
        self._answers.append(answer)
        return answer

    def find(
        self,
        kind: str,
        *,
        conversation_id: str | None = None,
        content: dict | None = None,
        now: float | None = None,
    ) -> ApprovalAnswer | None:
        """Narrowest usable answer for this kind and context."""
        content_hash = _answer_hash(content)
        candidates = [
            a
            for a in self._answers
            if a.kind == kind
            and a.answer_id not in self._consumed
            and a.usable_for(
                conversation_id=conversation_id,
                content_hash=content_hash,
                content=content,
                beta_mode=self.beta_mode,
                now=now,
            )
        ]
        for scope in ("once", "conversation", "always"):
            for answer in candidates:
                if answer.scope == scope:
                    return answer
        return None

    def has_narrowed_always(self, kind: str, *, now: float | None = None) -> bool:
        """True when a live "always" answer exists for the kind.

        Used to explain an "ask": the answer exists, but it does
        not cover this target or amount.
        """
        return any(
            a.kind == kind
            and a.scope == "always"
            and a.answer_id not in self._consumed
            and not a.expired(now)
            for a in self._answers
        )

    def consume(self, answer_id: str) -> bool:
        """Mark a "once" answer used. Returns False when unknown."""
        if any(a.answer_id == answer_id for a in self._answers):
            self._consumed.add(answer_id)
            return True
        return False

    def purge_expired(self, now: float | None = None) -> int:
        """Drop expired answers. Returns how many were dropped."""
        now = time.time() if now is None else now
        before = len(self._answers)
        self._answers = [a for a in self._answers if not a.expired(now)]
        self._consumed = {c for c in self._consumed if any(a.answer_id == c for a in self._answers)}
        return before - len(self._answers)

    def list_answers(self) -> list[ApprovalAnswer]:
        return list(self._answers)


# -- rules (strictest wins) ---------------------------------------------


@dataclass(frozen=True)
class ApprovalRule:
    """One rule for one action kind, from one owner."""

    kind: str
    owner: str  # "own" | "admin" | "conversation"
    mode: str  # "ask_every_time" | "members_self_serve"
    created_by: str = "user"


@dataclass(frozen=True)
class ApprovalDecision:
    """The policy verdict for one action."""

    outcome: str  # "allow" | "ask" | "deny" | "escalate_to_human"
    kind: str | None
    reason: str
    matched_rule: ApprovalRule | None = None
    answer_scope: str | None = None
    findings: tuple[AutoReviewFinding, ...] = ()


class ApprovalPolicy:
    """Taxonomy policy: rules + tiered answers + switches.

    Decision order (strictest wins):

    1. Credential change -> escalate to a human, always.
    2. Any ``members_self_serve`` rule for the kind -> deny (the
       bot must not perform it; members do it themselves).
    3. Any ``ask_every_time`` rule for the kind -> ask.
    4. Extra vigilant switch on -> ask.
    5. Task from check-in -> ask before every change.
    6. Auto-review findings -> ask.
    7. A usable tiered answer -> allow. An "always" answer only
       applies when its narrowing (target pattern, nominal cap)
       covers this action; in beta mode it never applies to
       ``pay`` or ``send_to_other``.
    8. Otherwise -> ask (these kinds always ask by default).
    """

    def __init__(
        self,
        *,
        store: ApprovalStore | None = None,
        rules: list[ApprovalRule] | None = None,
        extra_vigilant: bool = False,
        gate: Gate | None = None,
        log=None,
        beta_mode: bool = False,
    ) -> None:
        self.store = store or ApprovalStore(beta_mode=beta_mode)
        # The store owns the beta flag: a store passed in keeps
        # its own mode, so a beta store stays beta in any policy.
        self.beta_mode = self.store.beta_mode
        self._rules: list[ApprovalRule] = list(rules or [])
        self.extra_vigilant = extra_vigilant
        self.gate = gate
        self.log = log

    # -- rules ----------------------------------------------------------
    @property
    def rules(self) -> list[ApprovalRule]:
        return list(self._rules)

    def add_rule(self, rule: ApprovalRule) -> None:
        if rule.kind not in ACTION_KINDS:
            raise ValueError(f"unknown action kind: {rule.kind!r}")
        if rule.owner not in RULE_OWNERS:
            raise ValueError(f"unknown rule owner: {rule.owner!r}")
        if rule.mode not in RULE_MODES:
            raise ValueError(f"unknown rule mode: {rule.mode!r}")
        self._rules.append(rule)

    def remove_rule(self, rule: ApprovalRule) -> bool:
        try:
            self._rules.remove(rule)
            return True
        except ValueError:
            return False

    def _rules_for(self, kind: str) -> list[ApprovalRule]:
        return [r for r in self._rules if r.kind == kind]

    # -- decision -------------------------------------------------------
    def decide(
        self,
        *,
        tool_name: str,
        button_label: str | None = None,
        page_text: str | None = None,
        content: dict | None = None,
        conversation_id: str | None = None,
        from_checkin: bool = False,
        now: float | None = None,
    ) -> ApprovalDecision:
        classification = classify_action(tool_name, button_label=button_label, page_text=page_text)
        findings = auto_review(classification, content)
        kind = classification.kind
        if kind == "change_credentials":
            return ApprovalDecision(
                outcome="escalate_to_human",
                kind=kind,
                reason="credential changes can never be approved by an answer; a human must do it",
                findings=tuple(findings),
            )
        if kind is None:
            return ApprovalDecision(
                outcome="ask",
                kind=None,
                reason="unrecognized action: ask the user",
                findings=tuple(findings),
            )
        rules = self._rules_for(kind)
        self_serve = next((r for r in rules if r.mode == "members_self_serve"), None)
        if self_serve is not None:
            return ApprovalDecision(
                outcome="deny",
                kind=kind,
                reason=f"admin closed {kind}: members do it themselves, the bot must not",
                matched_rule=self_serve,
                findings=tuple(findings),
            )
        ask_always = next((r for r in rules if r.mode == "ask_every_time"), None)
        if ask_always is not None:
            return ApprovalDecision(
                outcome="ask",
                kind=kind,
                reason=f"rule for {kind}: ask every time",
                matched_rule=ask_always,
                findings=tuple(findings),
            )
        if self.extra_vigilant:
            return ApprovalDecision(
                outcome="ask",
                kind=kind,
                reason="extra vigilant is on: every action asks",
                findings=tuple(findings),
            )
        if from_checkin:
            return ApprovalDecision(
                outcome="ask",
                kind=kind,
                reason="task from check-in: ask before every change",
                findings=tuple(findings),
            )
        if findings:
            return ApprovalDecision(
                outcome="ask",
                kind=kind,
                reason="auto-review: " + "; ".join(f.detail for f in findings),
                findings=tuple(findings),
            )
        answer = self.store.find(kind, conversation_id=conversation_id, content=content, now=now)
        if answer is not None:
            return ApprovalDecision(
                outcome="allow",
                kind=kind,
                reason=f"tiered answer applies: {answer.scope}",
                answer_scope=answer.scope,
            )
        if self.store.has_narrowed_always(kind, now=now):
            return ApprovalDecision(
                outcome="ask",
                kind=kind,
                reason=(
                    f"stored 'always' answer for {kind} does not cover this target or amount; ask"
                ),
                findings=tuple(findings),
            )
        return ApprovalDecision(
            outcome="ask",
            kind=kind,
            reason=f"{kind} always asks",
            findings=tuple(findings),
        )

    # -- answers --------------------------------------------------------
    def record_answer(
        self,
        kind: str,
        scope: str,
        *,
        conversation_id: str | None = None,
        content: dict | None = None,
        recipient_pattern: str | None = None,
        domain_pattern: str | None = None,
        path_pattern: str | None = None,
        max_nominal: int | float | None = None,
    ) -> ApprovalAnswer:
        """Store a tiered answer ("once" / "conversation" /
        "always"). A "once" answer is bound to the exact content;
        an "always" answer may be narrowed to a target pattern
        and a nominal cap. In beta mode, "always" for ``pay`` or
        ``send_to_other`` is refused."""
        return self.store.record(
            kind,
            scope,
            conversation_id=conversation_id,
            content=content,
            recipient_pattern=recipient_pattern,
            domain_pattern=domain_pattern,
            path_pattern=path_pattern,
            max_nominal=max_nominal,
        )

    # -- server-rendered card -------------------------------------------
    def render_card(
        self,
        *,
        tool_name: str,
        content: dict,
        request_id: str = "",
        narrative: str | None = None,
        button_label: str | None = None,
        page_text: str | None = None,
    ):
        """Render the approval card for one action, server-side.

        The kind comes from the tool name (same recognition as
        :meth:`decide`); every visible line comes from the
        structured ``content``. A model narrative, when supplied,
        is ignored and flagged — it never reaches the card. See
        ``puspa.policy.approval_cards``.
        """
        classification = classify_action(tool_name, button_label=button_label, page_text=page_text)
        return render_approval_card(
            kind=classification.kind or "unknown",
            tool_name=tool_name,
            content=content,
            request_id=request_id,
            narrative=narrative,
        )

    # -- gate wiring ----------------------------------------------------
    def request(
        self,
        *,
        user_id: str,
        agent_id: str,
        token,
        tool_name: str,
        content: dict,
        button_label: str | None = None,
        page_text: str | None = None,
        conversation_id: str | None = None,
        from_checkin: bool = False,
    ):
        """Ask for (or reuse) approval for one outbound action,
        through the single gate.

        - "escalate_to_human" / "deny" -> ``GateDenied``; nothing
          reaches the gate.
        - "allow" (a usable tiered answer) -> the request is filed
          with the gate and approved at once from the stored answer;
          a "once" answer is consumed.
        - "ask" -> the request is filed and left pending; the caller
          presents the approval card to the user.

        The gate still binds the approval to the content hash and
        still enforces its 24-hour token expiry; this layer only
        decides *whether* to ask.
        """
        if self.gate is None:
            raise GateDenied("approval policy has no gate wired in")
        decision = self.decide(
            tool_name=tool_name,
            button_label=button_label,
            page_text=page_text,
            content=content,
            conversation_id=conversation_id,
            from_checkin=from_checkin,
        )
        if decision.outcome in ("escalate_to_human", "deny"):
            raise GateDenied(decision.reason)
        gate_kind = GATE_KIND_FOR_ACTION.get(decision.kind or "")
        if gate_kind is None:
            raise GateDenied(f"no gate kind for action kind {decision.kind!r}")
        request = self.gate.request_approval(
            user_id=user_id,
            agent_id=agent_id,
            token=token,
            kind=gate_kind,
            content=dict(content),
        )
        if decision.outcome == "allow":
            kind = decision.kind
            if kind is None:  # pragma: no cover - "allow" always has a kind
                raise GateDenied("approval decision has no action kind")
            answer = self.store.find(kind, conversation_id=conversation_id, content=content)
            if answer is not None and answer.scope == "once":
                self.store.consume(answer.answer_id)
            self.gate.approve(request.request_id, user_id)
        return request

    def request_with_card(
        self,
        *,
        user_id: str,
        agent_id: str,
        token,
        tool_name: str,
        content: dict,
        button_label: str | None = None,
        page_text: str | None = None,
        conversation_id: str | None = None,
        from_checkin: bool = False,
        narrative: str | None = None,
    ):
        """Like :meth:`request`, plus the server-rendered card.

        Returns ``(gate_request, card)``. The card is rendered
        from the same structured ``content`` the gate binds to,
        with the gate request's id, so the screen shows exactly
        what the approval covers. The narrative, when supplied,
        is ignored and flagged on the card.
        """
        request = self.request(
            user_id=user_id,
            agent_id=agent_id,
            token=token,
            tool_name=tool_name,
            content=content,
            button_label=button_label,
            page_text=page_text,
            conversation_id=conversation_id,
            from_checkin=from_checkin,
        )
        card = self.render_card(
            tool_name=tool_name,
            content=content,
            request_id=request.request_id,
            narrative=narrative,
            button_label=button_label,
            page_text=page_text,
        )
        return request, card


# -- anti-double-approval -----------------------------------------------


def mark_asking_guidance(message: str) -> str:
    """Tag a question with the marker that tells the bot the gate
    is already asking, so it does not place its own approval card."""
    if ASKING_GUIDANCE_MARKER in message:
        return message
    return f"[{ASKING_GUIDANCE_MARKER}] {message}"


def is_asking_guidance(message: str) -> bool:
    """True when a message carries the gate-is-asking marker."""
    return ASKING_GUIDANCE_MARKER in (message or "")


def mark_destructive(description: str) -> str:
    """Mark a destructive tool in its description."""
    if DESTRUCTIVE_MARKER in description:
        return description
    return f"{description} {DESTRUCTIVE_MARKER}"


def is_destructive(description: str) -> bool:
    """True when a tool description carries the destructive mark."""
    return DESTRUCTIVE_MARKER in (description or "")


class GuidanceState:
    """Whether the gate is currently asking the user.

    The bot checks :meth:`should_place_approval_card` before
    placing its own approval card: while the gate is asking, the
    answer is always False (anti-double-approval).
    """

    def __init__(self) -> None:
        self.asking = False

    def set_asking(self) -> None:
        self.asking = True

    def clear(self) -> None:
        self.asking = False

    def should_place_approval_card(self) -> bool:
        return not self.asking
