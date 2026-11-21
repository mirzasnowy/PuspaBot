"""Screen-text safety (V2-36): screen text is data, never instructions.

Everything the agent reads off the screen — accessibility-tree
node text, OCR words, clipboard content — is untrusted data. It
reaches the model only through this layer:

- Injection: every piece of screen text is scanned with Puspa's
  existing quarantine scanner
  (:mod:`puspa.quarantine.injection`); text that looks like an
  instruction is wrapped as ``[DATA: ...]`` so it can never sit
  in an instruction position in the model prompt, and the attempt
  is logged (a ledger event when a log callable is wired up, plus
  the in-memory ``events`` list on this object). The text is
  never acted on: reading the screen never clicks anything.
- Secrets: password / PIN / card-number content is masked
  (``[MASKED]``) or skipped before it can reach model context.
  A password field is recognised by its AT-SPI role
  (``"password text"``) or by a sensitive label on an editable
  field; free text is matched by pattern (card numbers checked
  with Luhn, ``label: secret`` pairs). A masked value is never
  logged: the event records the field kind, never the secret.

Wiring: :class:`puspa.computer.hands.DesktopHands` and
:class:`puspa.computer.fallback.VisualFallback` both take an
optional ``safety`` and apply it inside their read paths
(``snapshot``, ``clipboard_read``, ``read_screen``), so the
guard cannot be bypassed by calling the raw backend: the only
screen text the agent ever sees is the sanitised kind. Linux
first: the role names come from AT-SPI, the Linux accessibility
stack; Windows and macOS keep their own role names for later.

No network, no files, no model calls here: pure functions plus
one optional log callable.
"""

from __future__ import annotations

import re
from dataclasses import replace

from puspa.quarantine.injection import mark as _mark_injection
from puspa.quarantine.injection import scan as _scan_injection

from .tree import AccessibleNode, Snapshot
from .vision import OcrWord

__all__ = ["MASKED", "ScreenTextSafety", "ledger_event_log"]

#: What a masked secret looks like in model-facing text.
MASKED = "[MASKED]"

#: Ledger event type for screen-safety events. The ledger keeps
#: a closed set of types, so this reuses the existing
#: ``evidence_recorded`` type with a ``kind`` inside the content
#: instead of adding a new type.
EVENT_TYPE = "evidence_recorded"

#: AT-SPI role names (lower-cased) of password fields. The real
#: Linux backend reads the role straight from
#: ``atspi_accessible_get_role_name``, which reports
#: "password text" for a password entry.
_SENSITIVE_ROLES = frozenset({"password text", "password"})

#: Roles of editable text fields. A sensitive *label* only makes
#: a field secret when the field can hold typed text: a button
#: called "Pin" pins something, it is not a PIN field.
_EDITABLE_ROLES = frozenset({"text", "entry", "editable text", "password text", "password"})

#: Words in a field label that mark an editable field as secret.
#: "pin" is matched as a whole word, so "Pin" on a check box
#: never counts (and a check box is not editable anyway).
_SENSITIVE_LABEL_RE = re.compile(
    r"\b(password|kata\s*sandi|sandi|passcode|pin|otp|cvv|cvc|card\s*number|"
    r"nomor\s*kartu|nomor\s*pin|kartu\s*kredit|credit\s*card)\b",
    re.IGNORECASE,
)

#: ``label: secret`` / ``label = secret`` pairs in free text
#: (clipboard, node values, joined OCR text). The label stays,
#: the secret is masked.
_LABELED_SECRET_RE = re.compile(
    r"(?P<label>\b(?:password|kata\s*sandi|sandi|passcode|pin|otp|cvv|cvc|"
    r"card\s*number|nomor\s*kartu|nomor\s*pin|kartu\s*kredit|credit\s*card)\b"
    r"\s*[:=]\s*)(?P<secret>\S+)",
    re.IGNORECASE,
)

#: A run of digits with optional spaces or dashes, long enough
#: to be a card number. Luhn decides whether it really is one.
_CARD_RUN_RE = re.compile(r"\b(?:\d[ -]?){13,19}\b")

#: How much of an injected span a log event may carry (the span
#: is an instruction attempt, not a secret, but it is still
#: capped so one screen cannot flood the ledger).
_MAX_SPAN_LOGGED = 80


def _luhn_ok(digits: str) -> bool:
    """True when ``digits`` (digits only) passes the Luhn check."""
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = ord(char) - ord("0")
        if index % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def _mask_secrets(text: str) -> tuple[str, list[str]]:
    """Mask card numbers and ``label: secret`` pairs in ``text``.

    Returns the masked text plus the kinds found (``"card"``,
    ``"pin"``, ``"password"``, ...). A labelled secret keeps its
    label; only the secret itself becomes ``[MASKED]``.
    """
    kinds: list[str] = []

    def mask_card(match: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", match.group(0))
        if len(digits) < 13 or not _luhn_ok(digits):
            return match.group(0)
        kinds.append("card")
        return MASKED

    masked = _CARD_RUN_RE.sub(mask_card, text)

    def mask_labeled(match: re.Match[str]) -> str:
        label = match.group("label")
        word = re.sub(r"\s*[:=]\s*$", "", label).strip().lower()
        if "pin" in word or "otp" in word:
            kinds.append("pin")
        elif "card" in word or "kartu" in word:
            kinds.append("card")
        elif "cvv" in word or "cvc" in word:
            kinds.append("cvv")
        else:
            kinds.append("password")
        return label + MASKED

    return _LABELED_SECRET_RE.sub(mask_labeled, masked), kinds


def _is_sensitive_node(node: AccessibleNode) -> bool:
    """True when the node holds a secret by role or by label.

    A password role is always secret. Otherwise the node must be
    an editable text field whose name or description carries a
    sensitive word; a button or check box with such a word in its
    name is not a secret field.
    """
    role = (node.role or "").strip().lower()
    if role in _SENSITIVE_ROLES:
        return True
    if role not in _EDITABLE_ROLES:
        return False
    label = f"{node.name} {node.description}"
    return _SENSITIVE_LABEL_RE.search(label) is not None


def _sensitive_kind(node: AccessibleNode) -> str:
    """The kind of secret a sensitive node holds, for the log."""
    label = f"{node.name} {node.description}".lower()
    if "pin" in label or "otp" in label:
        return "pin"
    if "card" in label or "kartu" in label:
        return "card"
    if "cvv" in label or "cvc" in label:
        return "cvv"
    return "password"


def ledger_event_log(ledger, *, task_id: str, agent_id: str):
    """Build a ``log(event_type, content)`` callable on a Ledger.

    The callable appends each safety event to the real ledger
    (``evidence_recorded`` with a ``kind`` in the content), so a
    :class:`ScreenTextSafety` wired with it logs to the same
    append-only record as everything else. ``ledger`` only needs
    an ``append_event(task_id=..., agent_id=..., event_type=...,
    content=...)`` method, so a fake stands in for tests.
    """

    def log(event_type: str, content: dict) -> None:
        ledger.append_event(
            task_id=task_id,
            agent_id=agent_id,
            event_type=event_type,
            content=content,
        )

    return log


class ScreenTextSafety:
    """The screen-text safety layer.

    ``log`` is an optional ``log(event_type, content)`` callable
    (see :func:`ledger_event_log`); every event is also kept in
    ``events`` on this object, in order, so a caller without a
    ledger can still inspect what was ignored and masked. No
    event content ever carries a secret: masked values are
    recorded by kind only, and injected spans are capped.
    """

    def __init__(self, log=None) -> None:
        self._log = log
        self.events: list[dict] = []

    # -- logging ----------------------------------------------------
    def _record(self, event_type: str, content: dict) -> None:
        self.events.append({"type": event_type, **content})
        if self._log is not None:
            self._log(event_type, content)

    def _record_injection(self, *, source: str, ref: str | None, text: str) -> None:
        findings = _scan_injection(text)
        reportable = [f for f in findings if f.level in ("high", "in_progress")]
        if not reportable:
            return
        self._record(
            EVENT_TYPE,
            {
                "kind": "screen_injection_ignored",
                "source": source,
                "ref": ref,
                "patterns": sorted({f.pattern for f in reportable}),
                "level": max(
                    (f.level for f in reportable), key=lambda lv: {"in_progress": 0, "high": 1}[lv]
                ),
                "span": reportable[0].span[:_MAX_SPAN_LOGGED],
            },
        )

    def _record_masked(self, *, source: str, ref: str | None, kind: str) -> None:
        self._record(
            EVENT_TYPE,
            {
                "kind": "screen_secret_masked",
                "source": source,
                "ref": ref,
                "field": kind,
            },
        )

    # -- text -------------------------------------------------------
    def sanitize_text(self, text: str, *, source: str, ref: str | None = None) -> str:
        """Mask secrets in ``text`` and mark injected instructions
        as data. Clean text comes back unchanged; nothing here
        raises on odd input."""
        if not text:
            return text
        masked, kinds = _mask_secrets(text)
        for kind in kinds:
            self._record_masked(source=source, ref=ref, kind=kind)
        self._record_injection(source=source, ref=ref, text=masked)
        return _mark_injection(masked)

    # -- tree -------------------------------------------------------
    def sanitize_node(self, node: AccessibleNode) -> AccessibleNode:
        """Return a copy of ``node`` safe to hand to the model.

        A sensitive node's value is replaced with ``[MASKED]``
        (its name and role stay, so the agent still knows a
        password field is there); every other name, description
        and value goes through :meth:`sanitize_text`. Children
        are sanitised the same way.
        """
        if _is_sensitive_node(node):
            self._record_masked(source="snapshot", ref=node.ref, kind=_sensitive_kind(node))
            value = MASKED if node.value else ""
            name = _mark_injection(node.name) if node.name else ""
            description = _mark_injection(node.description) if node.description else ""
            if node.name or node.description:
                self._record_injection(
                    source="snapshot", ref=node.ref, text=f"{node.name} {node.description}"
                )
            return replace(
                node,
                name=name,
                description=description,
                value=value,
                children=tuple(self.sanitize_node(child) for child in node.children),
            )
        return replace(
            node,
            name=self.sanitize_text(node.name, source="snapshot", ref=node.ref)
            if node.name
            else "",
            description=self.sanitize_text(node.description, source="snapshot", ref=node.ref)
            if node.description
            else "",
            value=self.sanitize_text(node.value, source="snapshot", ref=node.ref)
            if node.value
            else "",
            children=tuple(self.sanitize_node(child) for child in node.children),
        )

    def sanitize_snapshot(self, snapshot: Snapshot) -> Snapshot:
        """Return a copy of ``snapshot`` safe to hand to the model."""
        return replace(
            snapshot,
            nodes=tuple(self.sanitize_node(node) for node in snapshot.nodes),
        )

    # -- OCR ----------------------------------------------------------
    def sanitize_words(self, words: list[OcrWord]) -> list[OcrWord]:
        """Mask secrets in OCR ``words`` and log injections.

        Words are positional data for grounding, so their text is
        only changed when it is a secret: a card-number word
        becomes ``[MASKED]``. An injected instruction spanning
        the words is logged once (from the joined text) but the
        words themselves stay: the agent's own query drives
        clicks, and the text handed to the model as prose goes
        through :meth:`sanitize_text` instead.
        """
        joined = " ".join(word.text for word in words)
        if joined:
            self._record_injection(source="ocr", ref=None, text=joined)
        out: list[OcrWord] = []
        for word in words:
            masked, kinds = _mask_secrets(word.text)
            for kind in kinds:
                self._record_masked(source="ocr", ref=None, kind=kind)
            out.append(word if masked == word.text else replace(word, text=masked))
        return out

    # -- clipboard ----------------------------------------------------
    def sanitize_clipboard(self, text: str) -> str:
        """Mask secrets and mark injections in clipboard text."""
        return self.sanitize_text(text, source="clipboard")
