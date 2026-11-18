"""Trust gate for memory writes (G7, DuskBot memory-trust pattern).

The pattern is adapted from DuskBot ``memory-trust.ts`` (which cites
SpAIware 2024 and MINJA, arXiv:2503.03704) and rewritten for Puspa;
no code is copied. Rules enforced here:

1. Text that reads like an instruction is never stored. A list of
   regex patterns (:data:`INSTRUCTION_PATTERNS`) catches imperative /
   role-hijack phrasing. A match blocks the write.
2. Content from outside sources (web, tool output, email, webhook,
   another bot) never becomes always-loaded memory on its own. It is
   quarantined and needs user approval / reinforcement before it can
   become a permanent fact.
3. Blocked lines are still kept, marked ``[blocked: ...]``, so the
   user can review them instead of the write vanishing silently.
4. Secrets are replaced with ``«redacted»`` before anything is
   written. There was no redaction helper in the repo before this
   module (checked: only ``<redacted>`` log placeholders in
   ``puspa/router/``), so :func:`redact_secrets` is the first one.

Verdicts: ``"allow"`` (store as a permanent fact), ``"quarantine"``
(store only in the journal quarantine queue for user review) and
``"blocked"`` (never a fact; kept as a marked journal line).
"""

from __future__ import annotations

import re

#: Marker substituted for any secret found in text before storage.
REDACTED = "«redacted»"

#: Origins whose content is external and must never become permanent
#: memory on its own.
EXTERNAL_ORIGINS = frozenset(
    {
        "web",
        "tool",
        "email",
        "webhook",
        "other_bot",
        "browser",
        "file",
        "search",
    }
)

#: Origins treated as the user's own words.
USER_ORIGINS = frozenset({"user", "user_confirmed", "user_stated"})

#: Tools that belong to Puspa itself (their output may still be
#: external content when it relays a third party; the origin, not
#: the tool name, decides quarantine).
OWN_TOOLS = frozenset(
    {
        "puspa_search",
        "puspa_billing",
        "puspa_calendar",
        "puspa_tasks",
        "puspa_memory",
        "puspa_router",
    }
)

#: Known external tools. Any tool not in :data:`OWN_TOOLS` is treated
#: as external by :func:`is_external_tool`.
EXTERNAL_TOOLS = frozenset(
    {
        "web_fetch",
        "web_search",
        "email_reader",
        "webhook_receiver",
        "slack_reader",
        "telegram_reader",
        "rss_reader",
        "file_reader",
    }
)

#: (name, regex) pairs for text that reads like an instruction.
#: Written fresh for Puspa from the described attack shapes
#: (instruction override, role hijack, prompt extraction, covert
#: orders, jailbreak framing); none of the patterns is copied.
INSTRUCTION_PATTERNS = (
    (
        "ignore_previous_instructions",
        r"ignore\s+(all\s+)?(previous|prior|above|earlier)\s+instructions?",
    ),
    (
        "disregard_instructions",
        r"disregard\s+(all\s+)?(previous|prior|above|earlier)\s+instructions?",
    ),
    ("forget_instructions", r"forget\s+(your|all|the|these)\s+instructions?"),
    ("you_are_now", r"you\s+are\s+now\s+(a|an|the)\b"),
    ("act_as", r"\bact\s+as\s+(a|an|the)\b"),
    ("pretend_to_be", r"\bpretend\s+(you\s+are|to\s+be)\b"),
    ("reveal_system_prompt", r"reveal\s+(your\s+)?(system\s+prompt|instructions?|prompt|rules)"),
    (
        "show_system_prompt",
        r"(show|print|output|dump)\s+(me\s+)?(your\s+)?(system\s+prompt|instructions?|prompt)",
    ),
    ("do_not_tell_user", r"do\s+not\s+(tell|reveal|mention|show)\s+(the\s+)?user\b"),
    ("new_instructions", r"\bnew\s+instructions?\s*:"),
    ("override_instructions", r"\boverride\s+(your\s+)?(instructions?|rules|safety|guardrails?)\b"),
    ("execute_following", r"\bexecute\s+(the\s+following|this)\b"),
    ("jailbreak", r"\b(jailbreak|jailbroken|dan\s+mode|developer\s+mode|do\s+anything\s+now)\b"),
    ("remember_you_must", r"\bremember\s+(that\s+)?you\s+must\b"),
    ("system_role_line", r"^\s*system\s*:\s*\S"),
    ("instruction_header", r"^\s*(instruction|directive|command)\s*:\s*\S"),
)

_COMPILED_PATTERNS = tuple(
    (name, re.compile(pattern, re.IGNORECASE | re.MULTILINE))
    for name, pattern in INSTRUCTION_PATTERNS
)

#: (name, regex) pairs for secrets, replaced with :data:`REDACTED`.
SECRET_PATTERNS = (
    ("openai_key", r"sk-[A-Za-z0-9]{20,}"),
    ("anthropic_key", r"sk-ant-[A-Za-z0-9\-_]{20,}"),
    ("github_token", r"(?:ghp_|gho_|ghu_|ghs_|ghr_|github_pat_)[A-Za-z0-9_]{20,}"),
    ("aws_access_key", r"AKIA[0-9A-Z]{16}"),
    ("bearer_token", r"Bearer\s+[A-Za-z0-9\-_.~+/=]{20,}"),
    ("jwt", r"eyJ[A-Za-z0-9\-_]+\.eyJ[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+"),
    (
        "private_key_block",
        r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----",
    ),
    ("password_assignment", r"(?i)(password|passwd|pwd)\s*[:=]\s*\S+"),
    ("api_key_assignment", r"(?i)(api[_-]?key|secret[_-]?key|access[_-]?token)\s*[:=]\s*\S+"),
)

_COMPILED_SECRETS = tuple((name, re.compile(pattern)) for name, pattern in SECRET_PATTERNS)


def is_external_tool(tool_name: str) -> bool:
    """True when ``tool_name`` is not one of Puspa's own tools."""
    return tool_name not in OWN_TOOLS


def find_instruction_matches(text: str) -> list[str]:
    """Names of the instruction patterns that match ``text``."""
    if not isinstance(text, str):
        raise ValueError("text must be a string")
    return [name for name, pattern in _COMPILED_PATTERNS if pattern.search(text)]


def redact_secrets(text: str) -> str:
    """Replace secrets in ``text`` with ``«redacted»``.

    Returns the redacted text. The replacement keeps the shape of
    an assignment (``password=«redacted»``) so a reviewer can see a
    secret was there without seeing it.
    """
    if not isinstance(text, str):
        raise ValueError("text must be a string")
    out = text
    for _name, pattern in _COMPILED_SECRETS:
        out = pattern.sub(REDACTED, out)
    return out


def mark_blocked(text: str, reasons) -> str:
    """Keep a blocked line for user review with a ``[blocked: ...]`` tag."""
    reasons_text = ", ".join(reasons) if reasons else "trust check"
    return f"[blocked: {reasons_text}] {text}"


def check_text(text: str, origin: str = "user") -> dict:
    """Trust-check ``text`` from ``origin`` before it becomes memory.

    Returns ``{"verdict", "reasons", "redacted_text",
    "marked_text"}``:

    - ``"blocked"``: an instruction pattern matched. ``marked_text``
      holds the redacted text with a ``[blocked: ...]`` tag.
    - ``"quarantine"``: clean text from an external origin. It may
      not become an always-loaded fact on its own; ``marked_text``
      holds it with a ``[quarantined: ...]`` tag for user review.
    - ``"allow"``: the user's own clean text; safe to store.

    Secrets are redacted in every verdict: ``redacted_text`` never
    contains a matched secret.
    """
    if not isinstance(text, str):
        raise ValueError("text must be a string")
    if not text:
        raise ValueError("text must not be empty")
    redacted = redact_secrets(text)
    matches = find_instruction_matches(redacted)
    if matches:
        reasons = [f"instruction pattern: {name}" for name in matches]
        return {
            "verdict": "blocked",
            "reasons": reasons,
            "redacted_text": redacted,
            "marked_text": mark_blocked(redacted, reasons),
        }
    if origin not in USER_ORIGINS:
        reason = f"external origin: {origin}"
        return {
            "verdict": "quarantine",
            "reasons": [reason],
            "redacted_text": redacted,
            "marked_text": f"[quarantined: {reason}] {redacted}",
        }
    return {
        "verdict": "allow",
        "reasons": [],
        "redacted_text": redacted,
        "marked_text": None,
    }
