"""Community skill scanner for the CI gate (snyk/agent-scan pattern).

Scans a community skill directory BEFORE the signed-skill gate
(``puspa.policy.skill_signing`` / ``PuspaSkillsLoader``). A signature
proves who published a skill; it says nothing about what the skill
does. This scanner checks what the skill does: it reads ``SKILL.md``,
the declared ``manifest.json`` (tools, domains, connectors), and any
other text file in the skill directory, and flags concrete threat
patterns drawn from the 341 malicious ClawHub skills named in
``docs/brain-depth-triage.md`` (Tier 2 item 16):

- exfiltration to an undeclared host (a network call to a host the
  skill did not declare in its manifest)
- exfiltration of secret material (credential or secret sent out)
- credential access (private keys, credential files, secret stores)
- prompt injection (override instructions, hidden instructions,
  role reassignment, base64-hidden instructions)
- gate bypass (skip approval, disable the gate or guardrails)
- permission overreach (tool, domain, or connector used but not
  declared in the manifest)
- rug-pull (remote code fetched and executed, or live instructions
  fetched from a URL at run time)
- toxic flow (destructive commands)

Design notes, stated plainly so the limits are visible:

- Every rule is a concrete pattern with a test fixture that proves
  it fires, and every finding names its rule and its location
  (``file:line``). A rule with no firing fixture does not ship.
- A network call is only flagged when its host is not declared in
  the manifest ``domains`` list and not in ``allowed_hosts``. A plain
  documentation link is not a network call and is not flagged. A
  skill that fetches weather from wttr.in must declare wttr.in.
- Reading an environment variable for a named provider API key is
  NOT flagged on its own; it is flagged when the secret is sent
  out (exfiltration-secret-send) or when the material is a private
  key, a credential file, or a secret store (credential-access).
  The scanner cannot prove intent, only the pattern; that boundary
  is deliberate and is covered by tests.
- The scanner is static text matching, not execution and not an LLM
  judge. It cannot catch obfuscated payloads (split strings,
  encoded URLs, homoglyph hosts), a skill that changes behaviour
  after signing, or a benign-looking skill whose harm needs a live
  service to trigger. Those need the runtime gate, the sandbox,
  and the signed-skill transparency log, not this scanner.
- Fail-closed: any scan failure (missing SKILL.md, missing or
  unparsable manifest, unreadable file) produces a ``scan-failed``
  finding and the skill does not pass. ``scan_skill`` never returns
  a pass for a skill it could not fully read.
- This module does not touch the signed-skill gate, the loader, or
  any signing code. It runs before them in CI; wiring it into the
  loader or a CI workflow is a separate step (see the report that
  ships with this work: the repo has no ``.github/workflows/`` yet).
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

__all__ = [
    "Finding",
    "ScanError",
    "ScanResult",
    "require_clean_scan",
    "scan_skill",
    "scan_text",
]

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class ScanError(Exception):
    """The skill did not pass the scan (findings, or scan failure)."""


@dataclass(frozen=True)
class Finding:
    """One flagged pattern. ``rule`` is a stable id, ``location`` is
    ``file:line`` inside the skill directory, ``severity`` is
    ``critical`` or ``high`` (triage priority only: any finding
    blocks the skill, see ``ScanResult.passed``)."""

    rule: str
    severity: str  # "critical" | "high"
    location: str
    message: str
    evidence: str = ""


@dataclass
class ScanResult:
    """Result of scanning one skill directory.

    ``passed`` is True only when the skill was fully read and no
    finding fired. ``error`` holds the fail-closed reason when the
    scan itself failed (missing files, unparsable manifest,
    unreadable file); in that case ``findings`` holds a single
    ``scan-failed`` finding and ``passed`` is False.
    """

    skill_name: str
    skill_dir: str
    findings: list[Finding] = field(default_factory=list)
    files_scanned: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def passed(self) -> bool:
        return not self.findings and self.error is None

    @property
    def blocked(self) -> bool:
        return not self.passed

    def rules(self) -> set[str]:
        """Rule ids that fired."""
        return {finding.rule for finding in self.findings}

    def counts_by_rule(self) -> dict[str, int]:
        """Finding count per rule id."""
        counts: dict[str, int] = {}
        for finding in self.findings:
            counts[finding.rule] = counts.get(finding.rule, 0) + 1
        return counts

    def format_report(self) -> str:
        """One CI log block: verdict line, then one line per finding."""
        verdict = "PASS" if self.passed else "BLOCKED"
        lines = [f"{verdict} skill {self.skill_name!r} ({self.skill_dir})"]
        if self.error is not None:
            lines.append(f"scan error: {self.error}")
        for finding in self.findings:
            lines.append(
                f"{finding.severity} {finding.rule} at {finding.location}: "
                f"{finding.message}"
            )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Rule ids
# ---------------------------------------------------------------------------

RULE_EXFIL_HOST = "exfiltration-unknown-host"
RULE_EXFIL_SECRET = "exfiltration-secret-send"
RULE_CREDENTIAL = "credential-access"
RULE_INJECTION_OVERRIDE = "injection-override-instructions"
RULE_INJECTION_HIDDEN = "injection-hidden-instruction"
RULE_INJECTION_ROLE = "injection-role-reassignment"
RULE_GATE_BYPASS = "gate-bypass"
RULE_OVERREACH_TOOL = "permission-overreach-tool"
RULE_OVERREACH_DOMAIN = "permission-overreach-domain"
RULE_OVERREACH_CONNECTOR = "permission-overreach-connector"
RULE_RUGPULL_CODE = "rug-pull-remote-code"
RULE_RUGPULL_LIVE = "rug-pull-live-instructions"
RULE_TOXIC_DESTRUCTIVE = "toxic-flow-destructive"
RULE_BINARY_PAYLOAD = "binary-payload"
RULE_SCAN_FAILED = "scan-failed"

# ---------------------------------------------------------------------------
# Vocabularies
# ---------------------------------------------------------------------------

#: Privileged tool names a skill may only use when declared in its
#: manifest. Plain words (``read``) are not here: only tools that can
#: move data, run code, or spend money.
_KNOWN_TOOLS = frozenset(
    {
        "shell",
        "exec",
        "bash",
        "sh",
        "run_command",
        "read_file",
        "write_file",
        "edit_file",
        "delete_file",
        "list_files",
        "send_email",
        "send",
        "gmail.send",
        "gmail.kirim",
        "calendar.create_event",
        "create_event",
        "prepare_payment",
        "pay",
        "payment",
        "deploy",
        "browser",
        "fetch_url",
        "http_request",
        "network",
        "subprocess",
        "eval",
        "curl",
        "wget",
    }
)

#: Connector names a skill may only use when declared in its manifest
#: (``connectors`` or ``konektor`` key). Multi-word entries are matched
#: as phrases.
_KNOWN_CONNECTORS = (
    "gmail",
    "google calendar",
    "google drive",
    "google sheets",
    "google docs",
    "calendar",
    "drive",
    "sheets",
    "slack",
    "discord",
    "telegram",
    "whatsapp",
    "stripe",
    "github",
    "gitlab",
    "notion",
    "dropbox",
)

#: Tokens that look like dotted tool names but are binaries, file
#: names, or domains, never Puspa tools.
_BINARY_NAMES = frozenset(
    {"curl", "wget", "python", "python3", "node", "npm", "pip", "summarize", "git"}
)
_EXEMPT_SUFFIXES = (".exe", ".sh", ".py", ".js", ".ts", ".md", ".txt", ".png", ".jpg")

#: File extensions that are scanned as text (besides SKILL.md).
_TEXT_EXTENSIONS = frozenset(
    {".md", ".txt", ".py", ".sh", ".js", ".ts", ".yaml", ".yml", ".toml", ".cfg", ".ini"}
)

#: Extensions that must never ship inside a community skill: a binary
#: blob the scanner cannot read is a hiding place, not content.
_BINARY_EXTENSIONS = frozenset({".exe", ".dll", ".so", ".dylib", ".bin", ".app"})

#: Manifest files the scanner never scans as skill text.
_MANIFEST_FILES = frozenset({"manifest.json", "envelope.json"})

_MAX_EVIDENCE = 120
_MAX_FILE_BYTES = 1_000_000

# ---------------------------------------------------------------------------
# Pattern tables (concrete regexes, case-insensitive unless noted)
# ---------------------------------------------------------------------------

_SECRET_WORDS = (
    r"(?:os\.environ|getenv|ENV\[|\$HOME/\.ssh|~/\.ssh|\.env\b|credentials?|"
    r"api[_\s-]?key|secret|password|passwd|token|private[_\s-]?key|id_rsa|"
    r"id_ed25519|\.pem\b|~/\.aws|AWS_SECRET|auth)"
)
_SEND_WORDS = (
    r"(?:curl|wget|requests\.post|httpx\.post|fetch\(|upload|POST|send|"
    r"exfiltrat|webhook|scp|rsync|socket\.send)"
)

#: Per-line rules: (rule, severity, regex, message). Each regex must
#: match the concrete threat, not a mere mention of the words.
_LINE_RULES: list[tuple[str, str, str, str]] = [
    # --- exfiltration of secret material: secret + send on one line ---
    (
        RULE_EXFIL_SECRET,
        "critical",
        _SECRET_WORDS + r".{0,100}" + _SEND_WORDS,
        "secret material is sent out on this line",
    ),
    (
        RULE_EXFIL_SECRET,
        "critical",
        _SEND_WORDS + r".{0,100}" + _SECRET_WORDS,
        "secret material is sent out on this line",
    ),
    # --- credential access: private keys, credential files, secret stores ---
    (
        RULE_CREDENTIAL,
        "critical",
        r"(?:~/\.ssh|/\.ssh/|id_rsa|id_ed25519|\.pem\b|~/\.aws|\.aws/credentials"
        r"|AWS_SHARED_CREDENTIALS|keychain|gpg\s+--export-secret)",
        "credential material is accessed (private key, credential file, keychain)",
    ),
    (
        RULE_CREDENTIAL,
        "critical",
        r"\bprintenv\b|(?:^|\s)\benv\s*\|",
        "environment is dumped (credential material leaks with it)",
    ),
    (
        RULE_CREDENTIAL,
        "critical",
        r"cat\s+\S*\.env\b|open\(\s*[\"'][^\"']*\.env[\"']",
        "a .env credential file is read",
    ),
    (
        RULE_CREDENTIAL,
        "critical",
        r"(?:os\.environ\[|getenv\(\s*[\"'])[^\"']*(?:AWS_SECRET_ACCESS_KEY|"
        r"(?:SECRET|PASSWORD|PRIVATE_KEY))",
        "a secret environment variable is read",
    ),
    # --- injection: override instructions ---
    (
        RULE_INJECTION_OVERRIDE,
        "critical",
        r"(?:ignore|disregard)\s+(?:all\s+)?(?:previous|prior|above|earlier|"
        r"system|these|your)\s+instructions?",
        "instruction to ignore or disregard previous instructions",
    ),
    (
        RULE_INJECTION_OVERRIDE,
        "critical",
        r"override\s+(?:your|the|system)\s+instructions?",
        "instruction to override the agent instructions",
    ),
    (
        RULE_INJECTION_OVERRIDE,
        "critical",
        r"forget\s+(?:all\s+)?(?:previous|prior|above)\s+instructions?",
        "instruction to forget previous instructions",
    ),
    (
        RULE_INJECTION_OVERRIDE,
        "critical",
        r"(?:reveal|show|print|repeat)\s+(?:your|the)\s+(?:system\s+)?prompt",
        "instruction to reveal the system prompt",
    ),
    (
        RULE_INJECTION_OVERRIDE,
        "critical",
        r"ignore\s+(?:all\s+)?(?:safety|security|policy|rules)",
        "instruction to ignore safety or policy rules",
    ),
    (
        RULE_INJECTION_OVERRIDE,
        "critical",
        r"abaikan\s+(?:semua\s+)?(?:instruksi|aturan)|lupakan\s+(?:semua\s+)?instruksi",
        "instruction to ignore instructions (Indonesian)",
    ),
    # --- injection: role reassignment ---
    (
        RULE_INJECTION_ROLE,
        "high",
        r"you\s+are\s+now\b|from\s+now\s+on[,.]?\s+you\b|pretend\s+you\s+are\b|"
        r"you\s+are\s+no\s+longer\b",
        "role reassignment (you are now / from now on you)",
    ),
    (
        RULE_INJECTION_ROLE,
        "high",
        r"mulai\s+sekarang[,.]?\s+kamu|kamu\s+adalah\s+sekarang",
        "role reassignment (Indonesian)",
    ),
    # --- gate bypass ---
    (
        RULE_GATE_BYPASS,
        "critical",
        r"bypass\s+(?:the\s+)?(?:gate|approval|guardrails?|verification|signing|"
        r"signature|policy)",
        "instruction to bypass the gate, approval, or verification",
    ),
    (
        RULE_GATE_BYPASS,
        "critical",
        r"skip\s+(?:the\s+)?approval|do\s+not\s+(?:ask|request|wait)\s+(?:for\s+)?"
        r"approval|proceed\s+without\s+(?:asking|approval|confirmation)",
        "instruction to skip approval",
    ),
    (
        RULE_GATE_BYPASS,
        "critical",
        r"without\s+approval\b|no\s+approval\s+(?:needed|required)|approval\s+is\s+"
        r"not\s+(?:needed|required)",
        "claim that approval is not needed",
    ),
    (
        RULE_GATE_BYPASS,
        "critical",
        r"disable\s+(?:the\s+)?(?:gate|guardrails?|approval|verification|safety|"
        r"checks?)|ignore\s+(?:the\s+)?gate",
        "instruction to disable the gate or guardrails",
    ),
    (
        RULE_GATE_BYPASS,
        "critical",
        r"circumvent\s+(?:the\s+)?(?:gate|approval|guardrails?)|evade\s+(?:the\s+)?"
        r"(?:gate|approval|guardrails?)",
        "instruction to circumvent the gate or guardrails",
    ),
    # --- rug-pull: remote code fetched and executed ---
    (
        RULE_RUGPULL_CODE,
        "critical",
        r"(?:curl|wget)\b[^|\n]*\|\s*(?:sh|bash|zsh|powershell|pwsh|python3?|perl)\b",
        "remote content is piped into a shell or interpreter",
    ),
    (
        RULE_RUGPULL_CODE,
        "critical",
        r"\b(?:bash|sh)\s+<\s*\(\s*(?:curl|wget)\b",
        "shell runs a process substitution fed by curl or wget",
    ),
    (
        RULE_RUGPULL_CODE,
        "critical",
        r"eval\s*\([^)]*(?:fetch|requests\.get|urllib|urlopen|curl)",
        "fetched remote content is evaluated",
    ),
    (
        RULE_RUGPULL_CODE,
        "critical",
        r"exec\s*\([^)]*(?:requests\.get|urllib|fetch|urlopen)",
        "fetched remote content is executed",
    ),
    (
        RULE_RUGPULL_CODE,
        "critical",
        r"(?:os\.system|subprocess\.(?:call|run|Popen))\s*\([^)]*(?:curl|wget|fetch)",
        "a shell command fetches and runs remote content",
    ),
    (
        RULE_RUGPULL_CODE,
        "critical",
        r"pip\s+install\s+(?:https?://\S+|git\+https?://\S+)",
        "a package is installed straight from a URL",
    ),
    (
        RULE_RUGPULL_CODE,
        "critical",
        r"Invoke-Expression\b[^.\n]*(?:Invoke-WebRequest|Invoke-RestMethod|https?://)",
        "remote content is executed with Invoke-Expression",
    ),
    (
        RULE_RUGPULL_CODE,
        "critical",
        r"execute\s+(?:the\s+)?(?:downloaded|fetched|remote)\s+(?:script|code)|"
        r"run\s+(?:the\s+)?(?:downloaded|remote)\s+script|"
        r"load\s+(?:a\s+)?(?:remote|external)\s+script",
        "a downloaded or remote script is executed",
    ),
    # --- rug-pull: live instructions fetched at run time ---
    (
        RULE_RUGPULL_LIVE,
        "high",
        r"(?:fetch|download|load|check)\b[^.\n]{0,80}\b(?:latest|updated|current|"
        r"newer)\s+(?:instructions?|prompts?|rules)\b",
        "live instructions are fetched from outside the skill",
    ),
    (
        RULE_RUGPULL_LIVE,
        "high",
        r"(?:latest|updated|current)\s+instructions?\s+(?:at|from)\b|"
        r"instructions?\s+(?:at|from)\s+https?://|"
        r"follow\s+(?:the\s+)?(?:latest|remote|updated)\s+instructions?\b",
        "instructions are loaded from a URL at run time",
    ),
    (
        RULE_RUGPULL_LIVE,
        "high",
        r"before\s+(?:starting|acting|doing|running)[^.\n]{0,60}\b(?:fetch|download|"
        r"check)\b[^.\n]{0,40}https?://",
        "a URL is fetched for instructions before acting",
    ),
    (
        RULE_RUGPULL_LIVE,
        "high",
        r"\bself-?update\b|\bauto-?update\b[^.\n]{0,60}https?://|"
        r"update\s+(?:yourself|this\s+skill)\s+from\b",
        "the skill updates itself from outside",
    ),
    # --- toxic flow: destructive commands ---
    (
        RULE_TOXIC_DESTRUCTIVE,
        "critical",
        r"\brm\s+(?:-[a-z]*r[a-z]*f|-[a-z]*f[a-z]*r)\b",
        "recursive force delete (rm -rf)",
    ),
    (
        RULE_TOXIC_DESTRUCTIVE,
        "critical",
        r"\bmkfs\b|\bformat\s+(?:the\s+)?(?:disk|drive|volume)\b|"
        r"\bdd\b[^.\n]*\bof=/dev/|"
        r"\bdrop\s+(?:table|database)\b|"
        r"\bwipe\s+(?:the\s+)?(?:disk|drive|database)\b|"
        r"\bchmod\s+-R\s+777\s+/|>\s*/dev/sd[a-z]\b",
        "destructive command (format, wipe, drop, or raw disk write)",
    ),
    # --- hidden instruction: zero-width characters ---
    (
        RULE_INJECTION_HIDDEN,
        "high",
        "[\u200b-\u200f\ufeff\u2060]",
        "zero-width character hides text from the reader",
    ),
]

_COMPILED_LINE_RULES: list[tuple[str, str, re.Pattern[str], str]] = [
    (rule, severity, re.compile(pattern, re.IGNORECASE), message)
    for rule, severity, pattern, message in _LINE_RULES
]

#: Safe phrasings that must NOT count as exfiltration or credential
#: access: the skill telling the agent never to read, send, or share
#: secret material is the safe direction. Checked before those two
#: rules fire on a line.
_SAFE_SECRET_PATTERNS: list[re.Pattern[str]] = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(?:never|do not|don't|dont|must not|must never)\s+"
        r"(?:read|access|open|cat|use|send|share|upload|expose|post|paste)\b",
    )
]

#: Safe phrasings that must NOT count as gate bypass: the skill
#: telling the agent to require approval is the safe direction.
#: Checked before the gate-bypass rules fire on a line.
_SAFE_APPROVAL_PATTERNS: list[re.Pattern[str]] = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(?:never|do not|don't|dont)\b[^.\n]{0,40}\bwithout approval\b",
        r"\bwithout approval\b[^.\n]{0,40}\b(?:never|do not|don't|dont)\b",
        r"\b(?:requires?|needs?)\s+approval\b",
        r"\bmust\s+(?:ask|request|obtain|get)\s+approval\b",
        r"\b(?:only|always)\b[^.\n]{0,30}\b(?:with|after)\s+approval\b",
        r"\bask\s+for\s+approval\s+before\b",
    )
]

#: Network-call contexts that carry a URL or host. A plain markdown
#: link or a homepage line is not a network call and is not checked.
_NETWORK_URL_PATTERNS: list[re.Pattern[str]] = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"(?:curl|wget)\b[^\n]*?(https?://[^\s\"')\]]+)",
        r"(?:requests|httpx|urllib\.request|fetch|axios|http)\s*(?:\.\w+)?\s*\(\s*[\"'](https?://[^\"']+)",
        r"(?:Invoke-WebRequest|Invoke-RestMethod)\b[^\n]*?(https?://[^\s\"')]+)",
    )
]
_NETWORK_HOST_PATTERN = re.compile(
    r"(?:nc|netcat|ncat)\s+([A-Za-z0-9.-]+)\s+\d+", re.IGNORECASE
)

#: Send verbs carrying a URL: the domain-overreach check.
_SEND_URL_PATTERN = re.compile(
    r"\b(?:send|post|upload|submit|share|forward)\b[^\n]{0,80}(https?://[^\s\"')\]]+)",
    re.IGNORECASE,
)

#: Tool-call contexts: backticked dotted names, JSON tool calls, and
#: ``tool: name`` mentions, plus known privileged tool names used with
#: a call verb.
_DOTTED_TOOL_PATTERN = re.compile(r"`([a-z][a-z0-9_-]*\.[a-z0-9_.-]+)`")
_JSON_TOOL_PATTERN = re.compile(r'"name"\s*:\s*"([a-z][a-z0-9_.-]+)"')
_TOOL_MENTION_PATTERN = re.compile(r"\btool\s*:\s*([a-z][a-z0-9_.-]+)", re.IGNORECASE)
_CALL_CONTEXT_PATTERN = re.compile(
    r"\b(?:tool|call|invoke|using|use|run|execute)\b", re.IGNORECASE
)
_CONNECTOR_CONTEXT_PATTERN = re.compile(
    r"\b(?:use|call|via|through|with|send)\b", re.IGNORECASE
)

#: Instruction verbs that make an HTML comment (or a hidden span) a
#: hidden instruction rather than a harmless note.
_HIDDEN_VERB_PATTERN = re.compile(
    r"\b(?:ignore|disregard|send|run|execute|must|bypass|override|reveal|"
    r"do not tell|you are now)\b",
    re.IGNORECASE,
)
_HTML_COMMENT_PATTERN = re.compile(r"<!--(.*?)-->", re.IGNORECASE | re.DOTALL)
_HIDDEN_STYLE_PATTERN = re.compile(
    r"(?:display\s*:\s*none|visibility\s*:\s*hidden|font-size\s*:\s*0)"
    r"[^>]{0,200}>([^<]{0,500})",
    re.IGNORECASE | re.DOTALL,
)

#: Base64 tokens worth decoding: long enough that a decoded payload
#: is a real instruction, not a short id or hash.
_BASE64_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9+/]{40,}={0,2}")
_INJECTION_DECODE_CHECK = re.compile(
    r"(?:ignore|disregard|override|bypass|you are now|send .*password|"
    r"reveal .*prompt|skip approval)",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _shorten(text: str) -> str:
    """Evidence snippet: one line, capped, whitespace collapsed."""
    collapsed = " ".join(text.split())
    if len(collapsed) > _MAX_EVIDENCE:
        return collapsed[:_MAX_EVIDENCE] + "..."
    return collapsed


def _line_number(text: str, offset: int) -> int:
    """1-based line number for a character offset in ``text``."""
    return text.count("\n", 0, offset) + 1


def _host_of(url: str) -> str | None:
    """Lowercase host of a URL, or None when there is no host."""
    try:
        host = urlparse(url).hostname
    except ValueError:
        return None
    return host.lower() if host else None


def _host_is_declared(host: str, declared_domains, allowed_hosts) -> bool:
    """True when ``host`` equals, or is a subdomain of, a declared
    domain or an allowed host. ``api.example.com`` is covered by a
    declaration of ``example.com``."""
    for declared in list(declared_domains) + list(allowed_hosts):
        declared = declared.strip().lower()
        if not declared:
            continue
        declared = re.sub(r"^https?://", "", declared).split("/")[0]
        if host == declared or host.endswith("." + declared):
            return True
    return False


def _normalize_set(values) -> set[str]:
    """Lowercase string set from a manifest list, dict keys, or None."""
    if values is None:
        return set()
    if isinstance(values, dict):
        values = values.keys()
    if isinstance(values, str):
        values = [values]
    return {str(value).strip().lower() for value in values if str(value).strip()}


def _is_exempt_tool_name(name: str) -> bool:
    """True for binaries, file names, and domains, never Puspa tools."""
    lowered = name.lower()
    if lowered in _BINARY_NAMES:
        return True
    if lowered.endswith(_EXEMPT_SUFFIXES):
        return True
    if lowered.split(".")[0] in _BINARY_NAMES:
        return True
    return False


def _is_safe_approval_line(line: str) -> bool:
    """True when the line tells the agent to REQUIRE approval: the
    safe direction, never a bypass."""
    return any(pattern.search(line) for pattern in _SAFE_APPROVAL_PATTERNS)


# ---------------------------------------------------------------------------
# Text scanning
# ---------------------------------------------------------------------------


def _scan_line(line: str, lineno: int, source: str, ctx: dict) -> list[Finding]:
    """Findings for one line. ``ctx`` holds the declared sets."""
    location = f"{source}:{lineno}"
    findings: list[Finding] = []
    seen: set[tuple[str, str]] = set()

    def add(rule: str, severity: str, message: str, evidence: str) -> None:
        key = (rule, location)
        if key in seen:
            return
        seen.add(key)
        findings.append(
            Finding(
                rule=rule,
                severity=severity,
                location=location,
                message=message,
                evidence=_shorten(evidence),
            )
        )

    for rule, severity, pattern, message in _COMPILED_LINE_RULES:
        if rule == RULE_GATE_BYPASS and _is_safe_approval_line(line):
            continue
        if rule in (RULE_EXFIL_SECRET, RULE_CREDENTIAL) and any(
            guard.search(line) for guard in _SAFE_SECRET_PATTERNS
        ):
            continue
        match = pattern.search(line)
        if match:
            add(rule, severity, message, match.group(0))

    _scan_network_calls(line, location, ctx, add)
    _scan_tool_calls(line, location, ctx, add)
    _scan_connector_calls(line, location, ctx, add)
    return findings


def _scan_network_calls(line, location, ctx, add) -> None:
    """Network calls to undeclared hosts, and send-to-URL domains."""
    declared_domains = ctx["declared_domains"]
    allowed_hosts = ctx["allowed_hosts"]
    for pattern in _NETWORK_URL_PATTERNS:
        for match in pattern.finditer(line):
            host = _host_of(match.group(1))
            if host is None or _host_is_declared(host, declared_domains, allowed_hosts):
                continue
            add(
                RULE_EXFIL_HOST,
                "high",
                f"network call to undeclared host {host!r}",
                match.group(0),
            )
    for match in _NETWORK_HOST_PATTERN.finditer(line):
        host = match.group(1).lower()
        if _host_is_declared(host, declared_domains, allowed_hosts):
            continue
        add(
            RULE_EXFIL_HOST,
            "high",
            f"network call to undeclared host {host!r}",
            match.group(0),
        )
    for match in _SEND_URL_PATTERN.finditer(line):
        host = _host_of(match.group(1))
        if host is None or _host_is_declared(host, declared_domains, allowed_hosts):
            continue
        add(
            RULE_OVERREACH_DOMAIN,
            "high",
            f"data is sent to undeclared domain {host!r}",
            match.group(0),
        )


def _tool_candidates(line: str) -> set[str]:
    """Tool names mentioned in a tool-call context on this line."""
    candidates: set[str] = set()
    has_call_context = bool(_CALL_CONTEXT_PATTERN.search(line))
    for pattern in (
        _DOTTED_TOOL_PATTERN,
        _JSON_TOOL_PATTERN,
        _TOOL_MENTION_PATTERN,
    ):
        for match in pattern.finditer(line):
            name = match.group(1).lower()
            if pattern is _DOTTED_TOOL_PATTERN and not has_call_context:
                continue
            if not _is_exempt_tool_name(name):
                candidates.add(name)
    if has_call_context:
        for name in _KNOWN_TOOLS:
            # No dot before the name: "summarize.sh" is a domain-like
            # name, not the "sh" tool.
            if re.search(
                r"(?<![A-Za-z0-9_.])" + re.escape(name) + r"(?![A-Za-z0-9_])",
                line,
                re.IGNORECASE,
            ):
                candidates.add(name)
    return candidates


def _scan_tool_calls(line, location, ctx, add) -> None:
    declared_tools = ctx["declared_tools"]
    for name in sorted(_tool_candidates(line)):
        if name in declared_tools:
            continue
        add(
            RULE_OVERREACH_TOOL,
            "high",
            f"tool {name!r} is used but not declared in the manifest",
            line,
        )


def _scan_connector_calls(line, location, ctx, add) -> None:
    declared_connectors = ctx["declared_connectors"]
    if not _CONNECTOR_CONTEXT_PATTERN.search(line):
        return
    lowered = line.lower()
    for name in _KNOWN_CONNECTORS:
        if name in declared_connectors:
            continue
        # No dot before the name: "github.io" is a domain, not the
        # github connector.
        if re.search(
            r"(?<![A-Za-z0-9_.])" + re.escape(name) + r"(?![A-Za-z0-9_])", lowered
        ):
            add(
                RULE_OVERREACH_CONNECTOR,
                "high",
                f"connector {name!r} is used but not declared in the manifest",
                line,
            )


def _scan_whole_text(text: str, source: str, ctx: dict) -> list[Finding]:
    """Findings that need more than one line: hidden HTML comments,
    hidden styled spans, and base64 blobs decoding to instructions."""
    findings: list[Finding] = []

    def add(rule, severity, location, message, evidence):
        findings.append(
            Finding(
                rule=rule,
                severity=severity,
                location=location,
                message=message,
                evidence=_shorten(evidence),
            )
        )

    for match in _HTML_COMMENT_PATTERN.finditer(text):
        body = match.group(1)
        if _HIDDEN_VERB_PATTERN.search(body):
            add(
                RULE_INJECTION_HIDDEN,
                "high",
                f"{source}:{_line_number(text, match.start())}",
                "instruction hidden inside an HTML comment",
                body,
            )
    for match in _HIDDEN_STYLE_PATTERN.finditer(text):
        body = match.group(1)
        if _HIDDEN_VERB_PATTERN.search(body):
            add(
                RULE_INJECTION_HIDDEN,
                "high",
                f"{source}:{_line_number(text, match.start())}",
                "instruction hidden with display:none or invisible styling",
                body,
            )
    for match in _BASE64_TOKEN_PATTERN.finditer(text):
        token = match.group(0)
        try:
            padded = token + "=" * (-len(token) % 4)
            decoded = base64.b64decode(padded, validate=True).decode("utf-8")
        except (binascii.Error, ValueError, UnicodeDecodeError):
            continue
        if _INJECTION_DECODE_CHECK.search(decoded):
            add(
                RULE_INJECTION_HIDDEN,
                "high",
                f"{source}:{_line_number(text, match.start())}",
                "base64 blob decodes to an instruction",
                decoded,
            )
    return findings


def scan_text(
    text: str,
    *,
    source: str = "SKILL.md",
    declared_tools=None,
    declared_domains=None,
    declared_connectors=None,
    allowed_hosts=None,
) -> list[Finding]:
    """Scan skill text (usually one SKILL.md) for threat patterns.

    ``declared_*`` are the manifest declarations: a tool, domain, or
    connector used here but missing there is an overreach finding.
    ``allowed_hosts`` adds hosts the caller trusts besides the
    manifest domains. Returns findings sorted by location, deduped
    by (rule, location). Never raises for skill content: an empty
    text returns no findings (the directory-level ``scan_skill``
    treats a missing or empty SKILL.md as a scan failure instead).
    """
    ctx = {
        "declared_tools": _normalize_set(declared_tools),
        "declared_domains": _normalize_set(declared_domains),
        "declared_connectors": _normalize_set(declared_connectors),
        "allowed_hosts": _normalize_set(allowed_hosts),
    }
    if not text:
        return []
    findings: list[Finding] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        findings.extend(_scan_line(line, lineno, source, ctx))
    findings.extend(_scan_whole_text(text, source, ctx))
    deduped: dict[tuple[str, str], Finding] = {}
    for finding in findings:
        deduped.setdefault((finding.rule, finding.location), finding)
    ordered = sorted(
        deduped.values(),
        key=lambda f: (int(f.location.rsplit(":", 1)[1]), f.rule),
    )
    return ordered


# ---------------------------------------------------------------------------
# Directory scanning (the CI gate entry point)
# ---------------------------------------------------------------------------


def _read_text_file(path: Path) -> str:
    """UTF-8 text of ``path``. Raises on missing, oversized, or
    undecodable files: the caller turns that into scan-failed."""
    if path.stat().st_size > _MAX_FILE_BYTES:
        raise ValueError(f"file too large to scan: {path}")
    return path.read_text(encoding="utf-8")


def _scan_failed(name: str, skill_dir: str, reason: str, location: str) -> ScanResult:
    """Fail-closed result: one scan-failed finding, never a pass."""
    return ScanResult(
        skill_name=name,
        skill_dir=skill_dir,
        findings=[
            Finding(
                rule=RULE_SCAN_FAILED,
                severity="critical",
                location=location,
                message=f"scan failed, skill rejected: {reason}",
                evidence="",
            )
        ],
        files_scanned=[],
        error=reason,
    )


def scan_skill(skill_dir, *, allowed_hosts=None) -> ScanResult:
    """Scan one skill directory. Returns a ``ScanResult``; never
    raises for bad skill content and never returns a pass for a
    skill it could not fully read.

    Layout: ``SKILL.md`` + ``manifest.json`` (``{name, version,
    tools[], domains[], connectors{} or konektor{}, author,
    signature}``) + optional extra text files. Extra files with
    binary extensions (``.exe``, ``.dll``, ``.so``, ...) are flagged
    as ``binary-payload``; other non-text files are ignored.
    """
    path = Path(skill_dir)
    name = path.name
    if not path.is_dir():
        return _scan_failed(name, str(path), "skill directory does not exist", str(path))
    manifest_path = path / "manifest.json"
    if not manifest_path.is_file():
        return _scan_failed(
            name, str(path), "manifest.json is missing", "manifest.json:1"
        )
    try:
        manifest = json.loads(_read_text_file(manifest_path))
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        return _scan_failed(
            name, str(path), f"manifest.json cannot be read: {exc}", "manifest.json:1"
        )
    if not isinstance(manifest, dict):
        return _scan_failed(
            name, str(path), "manifest.json must be a JSON object", "manifest.json:1"
        )
    name = str(manifest.get("name") or name)
    declared_tools = _normalize_set(manifest.get("tools"))
    declared_domains = _normalize_set(manifest.get("domains"))
    connectors = manifest.get("connectors")
    if connectors is None:
        connectors = manifest.get("konektor")
    declared_connectors = _normalize_set(connectors)

    skill_path = path / "SKILL.md"
    if not skill_path.is_file():
        return _scan_failed(name, str(path), "SKILL.md is missing", "SKILL.md:1")
    try:
        skill_text = _read_text_file(skill_path)
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        return _scan_failed(
            name, str(path), f"SKILL.md cannot be read: {exc}", "SKILL.md:1"
        )
    if not skill_text.strip():
        return _scan_failed(name, str(path), "SKILL.md is empty", "SKILL.md:1")

    findings = scan_text(
        skill_text,
        source="SKILL.md",
        declared_tools=declared_tools,
        declared_domains=declared_domains,
        declared_connectors=declared_connectors,
        allowed_hosts=allowed_hosts,
    )
    files_scanned = ["SKILL.md"]

    extra_files = sorted(
        p
        for p in path.rglob("*")
        if p.is_file() and p.name not in _MANIFEST_FILES and p.name != "SKILL.md"
    )
    for extra in extra_files:
        rel = extra.relative_to(path).as_posix()
        if extra.suffix.lower() in _BINARY_EXTENSIONS:
            findings.append(
                Finding(
                    rule=RULE_BINARY_PAYLOAD,
                    severity="high",
                    location=f"{rel}:1",
                    message="binary payload shipped inside the skill",
                    evidence=rel,
                )
            )
            files_scanned.append(rel)
            continue
        if extra.suffix.lower() not in _TEXT_EXTENSIONS:
            continue
        try:
            text = _read_text_file(extra)
        except (OSError, ValueError, UnicodeDecodeError) as exc:
            return _scan_failed(
                name,
                str(path),
                f"file {rel} cannot be read: {exc}",
                f"{rel}:1",
            )
        findings.extend(
            scan_text(
                text,
                source=rel,
                declared_tools=declared_tools,
                declared_domains=declared_domains,
                declared_connectors=declared_connectors,
                allowed_hosts=allowed_hosts,
            )
        )
        files_scanned.append(rel)

    findings.sort(key=lambda f: (f.location.rsplit(":", 1)[0], int(f.location.rsplit(":", 1)[1]), f.rule))
    return ScanResult(
        skill_name=name,
        skill_dir=str(path),
        findings=findings,
        files_scanned=files_scanned,
        error=None,
    )


def require_clean_scan(skill_dir, *, allowed_hosts=None) -> ScanResult:
    """Like ``scan_skill``, but raises ``ScanError`` when the skill
    does not pass. Use this in CI: a blocked skill stops the run
    instead of flowing on to the signed-skill gate."""
    result = scan_skill(skill_dir, allowed_hosts=allowed_hosts)
    if not result.passed:
        raise ScanError(result.format_report())
    return result
