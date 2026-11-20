"""Server-rendered approval cards + approval-fatigue signal (E-10, plan 6.2/6.3).

The gate (``puspa.policy.gate``) already binds an approval to the
content hash, and the taxonomy (``puspa.policy.approvals``) already
decides *whether* to ask. What neither did was decide *what the user
sees*: until now the card text came from wherever the caller got it,
which in practice meant the model's narrative. A deceptive narrative
("just a routine confirmation") could sit on top of an action the
hash still bound correctly — the hash protected the integrity of the
action, not the honesty of its display (plan 6.2, attack 6.1.5).

This module closes that gap:

- :func:`render_approval_card` builds the card text server-side,
  from the structured tool arguments that will actually run, and
  nothing else. Every visible line is one field of those arguments
  (who, how much, where to). A model narrative may be passed in —
  so callers do not have to change their call shape — but it is
  never rendered: it is flagged as ignored, and the card carries
  no trace of its wording.
- The card carries the content hash of exactly those arguments,
  computed with the same canonical JSON the gate uses, so the hash
  the user sees is the hash the gate enforces. :func:`verify_card`
  re-renders from fresh arguments and checks both the hash and the
  lines.
- :class:`ApprovalFatigueTracker` measures the signal plan 6.3
  names for approval fatigue: the share of decisions made faster
  than a human could have read the card (under one second). It is
  a measurement, not an enforcement: a fast decision is recorded,
  never blocked.

Fail-closed rules, matching the rest of the policy package:

- A required field that is missing from the arguments renders as
  "Not provided", marks the card incomplete, and adds a warning —
  it is never invented, guessed, or filled in from narrative.
- Fields that look like secrets (password, token, API key, ...) are
  withheld from the card entirely.
- Credential changes never get a card: they are never approvable,
  so there is nothing honest to show.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass

from .tokens import canonical_json

__all__ = [
    "UNREAD_THRESHOLD_SECONDS",
    "ApprovalCard",
    "ApprovalCardLine",
    "ApprovalFatigueTracker",
    "render_approval_card",
    "verify_card",
]

#: A decision faster than this after the card was shown counts as
#: "agreed without reading" (plan 6.3). One second is far below
#: the time needed to read even the shortest card.
UNREAD_THRESHOLD_SECONDS = 1.0

#: How much of a message body a send card shows. The preview comes
#: from the structured arguments, truncated — never from narrative.
MESSAGE_PREVIEW_CHARS = 280

#: Card titles, one per action kind.
_CARD_TITLES = {
    "pay": "Approve payment",
    "send_to_other": "Approve send",
    "delete": "Approve delete",
    "share": "Approve share",
    "post": "Approve post",
    "install": "Approve skill install",
}

#: Argument keys whose values are never shown on a card.
_SENSITIVE_SUBSTRINGS = (
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
    "credential",
    "passkey",
    "private_key",
    "pin",
)

_MISSING = "Not provided"


def _content_hash(content: dict) -> str:
    """Content hash, identical to the gate's (same canonical JSON)."""
    return hashlib.sha256(canonical_json(content)).hexdigest()


def _first_text(content: dict, keys: tuple[str, ...]) -> str | None:
    """First non-empty string value among the keys, or None."""
    for key in keys:
        value = content.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _first_number(content: dict, keys: tuple[str, ...]) -> int | float | None:
    """First real number among the keys (booleans are not numbers)."""
    for key in keys:
        value = content.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            return value
    return None


def _format_amount(value: int | float) -> str:
    """Rupiah amount as shown on a card, e.g. ``Rp150.000``."""
    if isinstance(value, float) and not value.is_integer():
        return f"Rp{value:,.2f}".replace(",", ".")
    return f"Rp{int(value):,}".replace(",", ".")


def _is_sensitive(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in _SENSITIVE_SUBSTRINGS)


def _lines_for_kind(kind: str, content: dict) -> tuple[list, list[str]]:
    """Card lines for one kind, from structured arguments only.

    Returns (lines, warnings). A line is an
    :class:`ApprovalCardLine`; a warning names a field that was
    missing or withheld, so the user sees the gap instead of a
    silently incomplete card.
    """
    lines: list[ApprovalCardLine] = []
    warnings: list[str] = []

    def add(label: str, value: str | None, *, required: bool = True) -> None:
        lines.append(ApprovalCardLine(label=label, value=value or _MISSING))
        if value is None and required:
            warnings.append(f"{label} was not provided in the action arguments")

    if kind == "pay":
        add("Recipient", _first_text(content, ("recipient_name", "recipient", "payee", "to")))
        amount = _first_number(content, ("nominal", "amount", "amount_rupiah"))
        add("Amount", None if amount is None else _format_amount(amount))
        add(
            "Account",
            _first_text(content, ("recipient_account", "account", "account_ref")),
            required=False,
        )
        add(
            "Payment method",
            _first_text(content, ("rail_display", "rail", "rail_id", "method")),
            required=False,
        )
        return lines, warnings
    if kind == "send_to_other":
        add("Recipient", _first_text(content, ("recipient", "recipient_name", "to")))
        add("Channel", _first_text(content, ("channel",)), required=False)
        text = _first_text(content, ("text", "body", "message"))
        if text is None:
            add("Message", None)
        else:
            preview = (
                text if len(text) <= MESSAGE_PREVIEW_CHARS else text[:MESSAGE_PREVIEW_CHARS] + "…"
            )
            lines.append(ApprovalCardLine(label="Message", value=preview))
        return lines, warnings
    if kind == "delete":
        add("Target", _first_text(content, ("target", "path", "name")))
        return lines, warnings
    if kind in ("share", "post"):
        add("Destination", _first_text(content, ("target", "domain", "url")))
        add("Title", _first_text(content, ("title", "subject")), required=False)
        return lines, warnings
    if kind == "install":
        add("Skill", _first_text(content, ("skill", "name")))
        add("Version", _first_text(content, ("version",)), required=False)
        return lines, warnings
    # Unknown kind: show every scalar argument, sorted by key, so
    # the card still shows what will run — and nothing else.
    for key in sorted(content):
        if _is_sensitive(key):
            warnings.append(f"a sensitive field ({key}) was withheld from this card")
            continue
        value = content[key]
        if isinstance(value, bool):
            lines.append(ApprovalCardLine(label=key, value="yes" if value else "no"))
        elif isinstance(value, (str, int, float)):
            text = str(value).strip()
            lines.append(ApprovalCardLine(label=key, value=text or _MISSING))
        # Lists and nested objects are not rendered as flat lines:
        # a card must stay readable, and their content is covered
        # by the hash below.
    if not lines:
        warnings.append("no readable fields in the action arguments")
    return lines, warnings


@dataclass(frozen=True)
class ApprovalCardLine:
    """One visible line of a card: a label and its value."""

    label: str
    value: str

    def to_dict(self) -> dict:
        return {"label": self.label, "value": self.value}


@dataclass(frozen=True)
class ApprovalCard:
    """A server-rendered approval card.

    Every line comes from the structured tool arguments; the card
    carries the hash of exactly those arguments, so what the user
    reads is what the gate enforces. ``narrative_ignored`` is True
    when a model narrative was supplied with the render request and
    discarded — the card never contains its wording.
    """

    request_id: str
    kind: str
    tool_name: str
    title: str
    lines: tuple[ApprovalCardLine, ...]
    content_hash: str
    warnings: tuple[str, ...] = ()
    complete: bool = True
    narrative_ignored: bool = False

    def to_dict(self) -> dict:
        """JSON form, as served to the PWA approval screen."""
        return {
            "request_id": self.request_id,
            "kind": self.kind,
            "tool_name": self.tool_name,
            "title": self.title,
            "lines": [line.to_dict() for line in self.lines],
            "content_hash": self.content_hash,
            "warnings": list(self.warnings),
            "complete": self.complete,
            "narrative_ignored": self.narrative_ignored,
        }

    def verify(self, content: dict) -> bool:
        """True when this card matches fresh structured arguments.

        Re-renders from ``content`` and checks both the content
        hash and every visible line.
        """
        return verify_card(self, content)


def render_approval_card(
    *,
    kind: str,
    tool_name: str,
    content: dict,
    request_id: str = "",
    narrative: str | None = None,
    model_text: str | None = None,
    display_text: str | None = None,
    summary: str | None = None,
) -> ApprovalCard:
    """Render an approval card from structured tool arguments.

    ``content`` is the exact argument dict that will run. The
    narrative parameters (``narrative``, ``model_text``,
    ``display_text``, ``summary``) exist only so callers that
    already carry a model-written description do not have to
    change their call shape: any non-empty one is ignored, never
    rendered, and only recorded as ``narrative_ignored=True``.

    Raises ``ValueError`` when ``content`` is not a dict, or when
    the kind is ``change_credentials`` — credential changes are
    never approvable, so there is no card to show for them.
    """
    if not isinstance(content, dict):
        raise ValueError("approval card content must be a dict of structured arguments")
    if kind == "change_credentials":
        raise ValueError("credential changes are never approvable; no approval card is rendered")
    narrative_supplied = any(
        isinstance(text, str) and text.strip()
        for text in (narrative, model_text, display_text, summary)
    )
    lines, warnings = _lines_for_kind(kind, content)
    if narrative_supplied:
        warnings.append(
            "a model-written description was supplied and ignored: "
            "this card shows only the structured action arguments"
        )
    title = _CARD_TITLES.get(kind, "Approve action")
    return ApprovalCard(
        request_id=request_id,
        kind=kind,
        tool_name=tool_name,
        title=title,
        lines=tuple(lines),
        content_hash=_content_hash(content),
        warnings=tuple(warnings),
        complete=not warnings or all("withheld" in w or "ignored" in w for w in warnings),
        narrative_ignored=narrative_supplied,
    )


def verify_card(card: ApprovalCard, content: dict) -> bool:
    """True when ``card`` is the card for these arguments.

    The hash must match, and re-rendering from ``content`` must
    produce exactly the same visible lines — so a card whose text
    was edited after rendering, or rendered from different
    arguments, fails even if its hash was copied along.
    """
    if not isinstance(card, ApprovalCard) or not isinstance(content, dict):
        return False
    if card.content_hash != _content_hash(content):
        return False
    try:
        rendered = render_approval_card(
            kind=card.kind,
            tool_name=card.tool_name,
            content=content,
            request_id=card.request_id,
        )
    except ValueError:
        return False
    return rendered.lines == card.lines


# -- approval fatigue ---------------------------------------------------


@dataclass
class _FatigueDecision:
    """One recorded decision, with its response latency if known."""

    request_id: str
    latency_seconds: float | None
    approved: bool | None
    unread: bool


class ApprovalFatigueTracker:
    """Measures approval fatigue: decisions made without reading.

    The PWA records when a card is first shown
    (:meth:`record_presented`) and when the user decides
    (:meth:`record_decision`). A decision faster than
    :data:`UNREAD_THRESHOLD_SECONDS` is counted as unread — no
    human reads a card in under a second. The tracker only
    measures: it never blocks a decision, however fast.
    """

    def __init__(self) -> None:
        self._presented: dict[str, float] = {}
        self._decisions: list[_FatigueDecision] = []

    def record_presented(self, request_id: str, *, at: float | None = None) -> None:
        """Record when a card was first shown. First showing wins."""
        if request_id not in self._presented:
            self._presented[request_id] = time.time() if at is None else at

    def record_decision(
        self,
        request_id: str,
        *,
        at: float | None = None,
        approved: bool | None = None,
        presented_at: float | None = None,
    ) -> float | None:
        """Record a decision; return its latency in seconds.

        Returns None when the presentation time is unknown (the
        card was never recorded as shown): the decision is still
        counted, but it cannot be judged unread.
        """
        decided_at = time.time() if at is None else at
        shown_at = (
            presented_at if presented_at is not None else self._presented.pop(request_id, None)
        )
        latency = None if shown_at is None else max(0.0, decided_at - shown_at)
        self._decisions.append(
            _FatigueDecision(
                request_id=request_id,
                latency_seconds=latency,
                approved=approved,
                unread=latency is not None and latency < UNREAD_THRESHOLD_SECONDS,
            )
        )
        return latency

    @property
    def total_decisions(self) -> int:
        """Decisions recorded so far."""
        return len(self._decisions)

    @property
    def timed_decisions(self) -> int:
        """Decisions with a known presentation time."""
        return sum(1 for d in self._decisions if d.latency_seconds is not None)

    @property
    def unread_count(self) -> int:
        """Decisions made faster than a human could read the card."""
        return sum(1 for d in self._decisions if d.unread)

    @property
    def unread_rate(self) -> float:
        """Unread share among timed decisions (0.0 when none)."""
        timed = self.timed_decisions
        return self.unread_count / timed if timed else 0.0

    def stats(self) -> dict:
        """Operator-visible fatigue numbers."""
        return {
            "decisions": self.total_decisions,
            "timed_decisions": self.timed_decisions,
            "unread_decisions": self.unread_count,
            "unread_rate": self.unread_rate,
            "unread_threshold_seconds": UNREAD_THRESHOLD_SECONDS,
        }
