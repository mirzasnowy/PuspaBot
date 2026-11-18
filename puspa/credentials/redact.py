"""Secret redaction, per secret type (G22).

Pattern studied, then rewritten as Puspa's own code (see the
G22 section of the repo-comparison report): hermes-agent ships
~25 redaction modules (sensitive text, command segments, DB
connection URLs, headers, phone numbers, vault values, home
paths, partial keys) and applies them to trajectories before
storage. No hermes-agent code is copied.

This module is the single redaction point for Puspa. Use it on
any text, record, or trajectory BEFORE it is stored or logged:

- :func:`redact_text` — one string, every secret type.
- :func:`redact_record` — a string / dict / list / tuple / set,
  cleaned recursively. Dict values under a secret-named key
  (``password``, ``api_key``, ``token``, ...) are replaced
  wholesale, whatever they contain.
- :func:`redact_trajectory` — a list of trajectory step dicts,
  cleaned step by step (the "before storage" call site).

Secret types handled (each has a test in
``tests/test_credentials_redact.py``):

1. API keys and tokens: ``sk-...``, ``sk-ant-...``, GitHub
   tokens, AWS access keys, Bearer tokens, JWTs, private-key
   blocks, and ``api_key`` / ``secret`` / ``password`` /
   ``token`` assignments.
2. Command segments: ``--api-key <value>``, ``--token``,
   ``--password``, ``--secret``, and ``export FOO_KEY=<value>``
   in shell text.
3. DB connection URLs: ``postgres://``, ``mysql://``,
   ``mongodb://``, ``redis://`` (and variants) — the whole
   credential part is replaced, so user, password, and host
   never survive.
4. Headers: ``Authorization: Bearer ...``, ``x-api-key: ...``.
5. Phone numbers: Indonesian ``+62...`` / ``08...`` numbers.
6. Vault values: exact values registered with
   :func:`register_secret` (e.g. a value just read from the
   vault) are replaced wherever they appear.
7. Home paths: ``/home/<user>``, ``/Users/<user>``,
   ``C:\\Users\\<user>`` become ``~``.
8. Partial keys: long high-entropy tokens (40+ chars, mixed
   letters and digits) and ``...<tail>`` key fragments.

The replacement marker is ``«redacted»``, the same marker
``puspa.memory.trust.redact_secrets`` uses, so redacted text
looks the same everywhere in the repo. Registered vault values
are kept in memory only, never logged, and
:func:`clear_registered_secrets` drops them (tests use it).
"""

from __future__ import annotations

import re

__all__ = [
    "REDACTED",
    "clear_registered_secrets",
    "redact_record",
    "redact_text",
    "redact_trajectory",
    "register_secret",
    "register_secrets",
]

#: Marker substituted for any secret found.
REDACTED = "«redacted»"

#: Dict keys whose value is always a secret, whatever it holds.
SECRET_KEYS = frozenset(
    {
        "password",
        "passwd",
        "pwd",
        "api_key",
        "apikey",
        "api-key",
        "secret",
        "secret_key",
        "client_secret",
        "access_token",
        "refresh_token",
        "token",
        "auth_token",
        "private_key",
        "authorization",
        "x-api-key",
        "dsn",
        "database_url",
        "connection_string",
    }
)

#: (name, regex) pairs for secrets in free text. The regexes are
#: written fresh for Puspa from the secret shapes themselves.
_SECRET_PATTERNS = (
    # Private-key blocks first: they span lines and contain
    # other matchable text.
    (
        "private_key_block",
        (
            r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----"
            r"[\s\S]*?-----END (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----"
        ),
    ),
    # DB connection URLs: whole URL incl. user/password/host.
    (
        "db_url",
        (
            r"(?:postgres(?:ql)?|mysql|mariadb|mongodb(?:\+srv)?|redis|rediss)"
            r"://[^\s\"'<>]+"
        ),
    ),
    # Any URL carrying userinfo (user:password@host).
    ("url_userinfo", r"([A-Za-z][A-Za-z0-9+.\-]*://)[^\s\"'/:@]+:[^\s\"'@/]+@"),
    # Bearer / auth headers.
    ("auth_header", r"(?i)(authorization\s*:\s*)(?:bearer\s+)?[^\s\"',;]+"),
    ("api_key_header", r"(?i)((?:x-api-key|api-key)\s*:\s*)[^\s\"',;]+"),
    # API keys and tokens.
    ("openai_key", r"sk-[A-Za-z0-9]{20,}"),
    ("anthropic_key", r"sk-ant-[A-Za-z0-9\-_]{20,}"),
    ("github_token", r"(?:ghp_|gho_|ghu_|ghs_|ghr_|github_pat_)[A-Za-z0-9_]{20,}"),
    ("aws_access_key", r"AKIA[0-9A-Z]{16}"),
    ("jwt", r"eyJ[A-Za-z0-9\-_]+\.eyJ[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+"),
    ("bearer_token", r"Bearer\s+[A-Za-z0-9\-_.~+/=]{20,}"),
    # Command segments: --api-key / --token / --password / --secret.
    ("cli_secret_flag", r"(?i)(--(?:api[-_]?key|token|password|secret)(?:=|\s+))[^\s\"']+"),
    # Shell exports of secret env vars.
    ("env_secret_assignment", r"(?i)\b([A-Z_0-9]*(?:KEY|TOKEN|SECRET|PASSWORD)\s*=\s*)[^\s\"']+"),
    # Assignments in config / log text (keeps the key name).
    (
        "secret_assignment",
        (
            r"(?i)((?:password|passwd|pwd|api[_-]?key|secret[_-]?key|"
            r"access[_-]?token|client[_-]?secret)\s*[:=]\s*)[^\s\"',;]+"
        ),
    ),
    # Phone numbers (Indonesian +62 / 08...).
    ("phone_number", r"(?:\+?62[\s\-]?8\d[\d\s\-]{7,12}|(?<!\d)08\d{8,11}(?!\d))"),
    # Home paths.
    ("home_path", r"(?:/home/|/Users/|C:\\Users\\)[^/\s\\]+"),
    # Partial keys: long high-entropy tokens (40+ chars, letters
    # and digits mixed). Applied last so structured patterns win.
    (
        "long_token",
        (
            r"\b(?=[A-Za-z0-9+/=_-]*[a-z])(?=[A-Za-z0-9+/=_-]*[A-Z])"
            r"(?=[A-Za-z0-9+/=_-]*\d)[A-Za-z0-9+/=_-]{40,}\b"
        ),
    ),
    # A key shown by its tail only, e.g. "...9f3a7c1e".
    ("key_tail", r"\.\.\.[A-Za-z0-9]{6,}"),
)

_COMPILED = tuple((name, re.compile(pattern)) for name, pattern in _SECRET_PATTERNS)

#: A bare secret flag in an argv list (its next element is the value).
_CLI_SECRET_FLAG = re.compile(r"(?i)^--(?:api[-_]?key|token|password|secret)$")

#: Vault values registered for exact-match redaction.
_REGISTERED_SECRETS: list[str] = []


def register_secret(value: str) -> None:
    """Register one vault value for exact-match redaction.

    The value itself is never logged or returned; it is only
    used to scrub text. Values shorter than 8 characters are
    ignored (too short to match safely).
    """
    if isinstance(value, str) and len(value) >= 8 and value not in _REGISTERED_SECRETS:
        _REGISTERED_SECRETS.append(value)


def register_secrets(values) -> None:
    """Register several vault values at once."""
    for value in values or ():
        register_secret(value)


def clear_registered_secrets() -> None:
    """Drop all registered vault values (tests use this)."""
    _REGISTERED_SECRETS.clear()


def _redact_registered(text: str) -> str:
    # Longest first, so a long value is not half-redacted by a
    # shorter value contained in it.
    for value in sorted(_REGISTERED_SECRETS, key=len, reverse=True):
        if value in text:
            text = text.replace(value, REDACTED)
    return text


def redact_text(text: str, *, extra_secrets=None) -> str:
    """Redact every secret type in ``text``.

    ``extra_secrets`` are additional exact values to scrub for
    this call only (they are not registered). The original
    secret values never appear in the result: assignments keep
    their key name (``password=«redacted»``), URLs keep only
    their scheme, headers keep only their name.
    """
    if not isinstance(text, str):
        raise ValueError("text must be a string")
    if extra_secrets:
        for value in sorted(
            (v for v in extra_secrets if isinstance(v, str) and len(v) >= 8), key=len, reverse=True
        ):
            text = text.replace(value, REDACTED)
    text = _redact_registered(text)
    for name, pattern in _COMPILED:
        if name == "url_userinfo":
            text = pattern.sub(r"\1" + REDACTED + "@", text)
        elif name in (
            "auth_header",
            "api_key_header",
            "cli_secret_flag",
            "env_secret_assignment",
            "secret_assignment",
        ):
            text = pattern.sub(r"\1" + REDACTED, text)
        elif name == "home_path":
            text = pattern.sub("~", text)
        elif name == "db_url":
            text = pattern.sub(
                lambda match: match.group(0).split("://", 1)[0] + "://" + REDACTED, text
            )
        else:
            text = pattern.sub(REDACTED, text)
    return text


def redact_record(record, *, extra_secrets=None):
    """Redact a string / dict / list / tuple / set, recursively.

    Dict values under a secret-named key are replaced wholesale
    (the key name stays, so the shape is visible). Every string
    anywhere in the structure goes through :func:`redact_text`.
    Other values pass through unchanged. Returns a new
    structure of the same shape; the input is not mutated.
    """
    if isinstance(record, str):
        return redact_text(record, extra_secrets=extra_secrets)
    if isinstance(record, dict):
        return {
            key: (
                REDACTED
                if _is_secret_key(key)
                else redact_record(value, extra_secrets=extra_secrets)
            )
            for key, value in record.items()
        }
    if isinstance(record, list):
        # A command split into argv: a secret flag element
        # (``--token``, ``--api-key``, ...) means the NEXT
        # element is the secret value itself.
        out = []
        redact_next = False
        for item in record:
            if redact_next and isinstance(item, str):
                out.append(REDACTED)
                redact_next = False
                continue
            cleaned = redact_record(item, extra_secrets=extra_secrets)
            out.append(cleaned)
            redact_next = isinstance(item, str) and _CLI_SECRET_FLAG.match(item.strip())
        return out
    if isinstance(record, tuple):
        return tuple(redact_record(item, extra_secrets=extra_secrets) for item in record)
    if isinstance(record, (set, frozenset)):
        return {redact_record(item, extra_secrets=extra_secrets) for item in record}
    return record


def _is_secret_key(key) -> bool:
    if not isinstance(key, str):
        return False
    lowered = key.lower().replace("-", "_")
    return lowered in SECRET_KEYS


def redact_trajectory(steps, *, extra_secrets=None):
    """Redact a trajectory (list of step dicts) before storage.

    Each step is cleaned with :func:`redact_record`, so tool
    arguments, model outputs, and observations in the trajectory
    never carry a secret into storage. Returns a new list.
    """
    if not isinstance(steps, (list, tuple)):
        raise ValueError("trajectory steps must be a list")
    return [redact_record(step, extra_secrets=extra_secrets) for step in steps]
