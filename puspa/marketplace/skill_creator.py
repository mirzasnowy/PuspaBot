"""Create skills by talking: describe a workflow and the agent authors it.

COW-04. The user describes a workflow or a third-party API in
conversation and the agent authors it as a reusable skill — the
skill-creator pattern CowAgent ships as a skill
(zhayujie/CowAgent, MIT; the pattern is adapted here and the code
below is written as PuspaBot code, never copied verbatim).

The flow has four stages, and no stage is silent:

1. **Interview** (:class:`SkillCreatorSession`): the session asks
   for what is missing — the skill name, the workflow steps (one
   per line), which tool runs each step, or the API base URL, its
   endpoints and its auth — instead of guessing. An incomplete
   description always produces a follow-up question, never a
   half-invented skill.
2. **Draft** (:func:`render_skill_md`): the answers become a
   ``SKILL.md`` in the repo's own format (frontmatter + canonical
   sections), with the workflow steps or API endpoints as the
   skill's tool wiring.
3. **Validate** (:func:`validate_draft`): the draft must parse as
   a ``SKILL.md``, pass the authoring standards
   (:mod:`puspa.marketplace.authoring`), and every tool it names
   must resolve against the known tools. An invalid draft is
   rejected with the reasons listed, never registered.
4. **Register** (:func:`register_trial_skill`): the valid draft is
   compiled through the existing skill compiler
   (:class:`puspa.governor.compiler.SkillCompiler`) and stored as
   a **trial** skill — versioned and replayable, never silently
   active. The trial replay runs the steps through a validating
   runner that only confirms each tool resolves; real execution
   is the skill's own runtime, not the authoring flow.

The CLI entry (``puspa skill create``) is the same session over
stdin/stdout: it shows the draft and, without ``--approve``,
registers nothing.
"""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from puspa.governor.compiler import CompileResult, SkillCompiler, SkillRun
from puspa.governor.skills import InMemorySkillStore, ProceduralSkillStore
from puspa.marketplace.authoring import check_authoring
from puspa.marketplace.errors import ManifestError
from puspa.marketplace.manifest import SKILL_ID_PATTERN, parse_frontmatter

__all__ = [
    "ApiEndpoint",
    "SkillCreatorSession",
    "SkillDraft",
    "add_skill_parser",
    "known_tool_names",
    "parse_api_description",
    "parse_workflow_steps",
    "register_trial_skill",
    "render_skill_md",
    "run_skill",
    "slugify",
    "validate_draft",
]

#: Skill kinds the interview can produce.
KIND_WORKFLOW = "workflow"
KIND_API = "api"

#: Auth kinds an API skill can declare.
AUTH_NONE = "none"
AUTH_API_KEY = "api_key"
AUTH_BEARER = "bearer"
AUTH_OAUTH = "oauth"

#: HTTP methods an API endpoint may declare.
HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})

#: Native tools the authoring standards name (no shell utilities).
NATIVE_TOOLS = frozenset({"search_files", "read_file", "list_files", "edit_file", "fetch_url"})

#: Frontmatter description limit, from the authoring standards.
MAX_DESCRIPTION_CHARS = 60

#: A workflow step line: ``1. do this``, ``2) do that``, ``- do this``.
_STEP_LINE = re.compile(r"^\s*(?:\d+[.)]|[-*\u2022])\s+(.+?)\s*$")
#: An API endpoint line: ``GET /path - what it does``.
_ENDPOINT_LINE = re.compile(
    r"^\s*(GET|POST|PUT|PATCH|DELETE)\s+(/\S*)(?:\s+[-:]\s*(.+?))?\s*$",
    re.IGNORECASE,
)
_URL = re.compile(r"https?://[^\s)\"'<>]+")


@dataclass(frozen=True)
class ApiEndpoint:
    """One endpoint of an API skill: method, path, what it does."""

    method: str
    path: str
    purpose: str = ""

    def __post_init__(self) -> None:
        if self.method.upper() not in HTTP_METHODS:
            raise ValueError(f"API endpoint method must be one of {sorted(HTTP_METHODS)}")
        if not self.path.startswith("/"):
            raise ValueError(f"API endpoint path must start with '/', got {self.path!r}")


@dataclass(frozen=True)
class SkillDraft:
    """A skill as the interview produced it, before validation.

    ``kind`` is ``"workflow"`` (``steps`` carry the procedure) or
    ``"api"`` (``endpoints`` + ``base_url`` + ``auth`` carry it).
    ``steps`` are ``{"tool": ..., "arguments": {...}}`` dicts in the
    shape the skill compiler replays; a workflow step's arguments
    hold the step text under ``"step"``.
    """

    skill_id: str
    name: str
    description: str
    kind: str
    steps: list[dict] = field(default_factory=list)
    endpoints: list[ApiEndpoint] = field(default_factory=list)
    auth: str = AUTH_NONE
    base_url: str = ""
    author: str = "user"

    def to_steps(self) -> list[dict]:
        """The replayable steps for this draft.

        Workflow steps as described; API skills become one
        ``fetch_url`` step per endpoint, in the order listed.
        """
        if self.kind == KIND_API:
            base = self.base_url.rstrip("/")
            return [
                {
                    "tool": "fetch_url",
                    "arguments": {
                        "method": endpoint.method,
                        "url": f"{base}{endpoint.path}",
                        "purpose": endpoint.purpose or f"Call {endpoint.method} {endpoint.path}",
                    },
                }
                for endpoint in self.endpoints
            ]
        return [dict(step) for step in self.steps]


def slugify(name: str) -> str:
    """A skill id for ``name``: lowercase, words joined by hyphens."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return re.sub(r"-+", "-", slug)


def known_tool_names() -> set[str]:
    """Every tool a drafted skill may name.

    The tools Puspa's own server exposes, plus the native tools
    the authoring standards name. When the server definitions
    cannot be imported here, the core tools alone are the known
    set — a draft naming anything else still fails validation
    instead of slipping through.
    """
    names = set(NATIVE_TOOLS)
    try:
        from puspa.mcp_server.tools import all_tool_definitions

        names.update(tool["name"] for tool in all_tool_definitions())
    except Exception:
        names.update(
            {
                "memory_search",
                "ledger_recent",
                "price_check",
                "bill_status",
                "schedule_list",
                "schedule_add",
            }
        )
    return names


def _tool_named_in(text: str, known_tools: set[str]) -> str | None:
    """The known tool named in ``text``, if any (longest name wins)."""
    for name in sorted(known_tools, key=len, reverse=True):
        if re.search(rf"\b{re.escape(name)}\b", text, re.IGNORECASE):
            return name
    return None


def parse_workflow_steps(text: str, known_tools: set[str] | None = None) -> list[dict]:
    """Workflow steps from ``text``: one step per numbered/bullet line.

    A step that names a known tool carries it; any other step has
    ``"tool": None`` — the interview must ask which tool runs it
    instead of guessing. Text with no step lines yields no steps.
    """
    known = known_tools if known_tools is not None else known_tool_names()
    steps: list[dict] = []
    for line in text.splitlines():
        match = _STEP_LINE.match(line)
        if not match:
            continue
        step_text = match.group(1)
        steps.append({"tool": _tool_named_in(step_text, known), "arguments": {"step": step_text}})
    return steps


def parse_api_description(text: str) -> dict:
    """The API facts stated in ``text``: base URL, endpoints, auth.

    Returns ``{"base_url": str, "endpoints": [...], "auth": str}``
    with empty values for anything the text does not state — the
    caller asks for the missing pieces, it never invents them.
    """
    url_match = _URL.search(text)
    base_url = url_match.group(0).rstrip(".,;:") if url_match else ""
    endpoints: list[ApiEndpoint] = []
    for line in text.splitlines():
        match = _ENDPOINT_LINE.match(line)
        if not match:
            continue
        method, path, purpose = match.groups()
        endpoints.append(
            ApiEndpoint(
                method=method.upper(),
                path=path,
                purpose=(purpose or "").strip() or f"Call {method.upper()} {path}",
            )
        )
    lowered = text.lower()
    if "oauth" in lowered:
        auth = AUTH_OAUTH
    elif "bearer" in lowered:
        auth = AUTH_BEARER
    elif "api key" in lowered or "apikey" in lowered or "api-key" in lowered:
        auth = AUTH_API_KEY
    elif any(
        phrase in lowered
        for phrase in ("no auth", "without auth", "no authentication", "unauthenticated")
    ):
        auth = AUTH_NONE
    else:
        auth = ""
    return {"base_url": base_url, "endpoints": endpoints, "auth": auth}


def _short_description(description: str) -> str:
    """``description`` cut to the frontmatter limit, at a word boundary."""
    text = " ".join(description.split())
    if len(text) <= MAX_DESCRIPTION_CHARS:
        return text
    cut = text[:MAX_DESCRIPTION_CHARS].rsplit(" ", 1)[0]
    return cut or text[:MAX_DESCRIPTION_CHARS]


def _secret_config_name(auth: str) -> str | None:
    """The frontmatter config entry holding the API secret, if any."""
    if auth == AUTH_API_KEY:
        return "api_key"
    if auth == AUTH_BEARER:
        return "bearer_token"
    return None


def render_skill_md(draft: SkillDraft) -> str:
    """The draft as a ``SKILL.md`` in the repo's own format.

    Frontmatter (name, short description, author, plus the API
    config when the skill declares one) and the canonical
    sections in order — Description, When to Use This Skill,
    Tools, Configuration, Examples — so the result passes the
    authoring standards check.
    """
    lines = ["---"]
    lines.append(f"name: {json.dumps(draft.name)}")
    lines.append(f"description: {json.dumps(_short_description(draft.description))}")
    lines.append(f"author: {json.dumps(draft.author)}")
    if draft.kind == KIND_API:
        lines.append("config:")
        lines.append("  - name: base_url")
        lines.append('    prompt: "Base URL of the API"')
        lines.append("    required: true")
        lines.append(f"    default: {json.dumps(draft.base_url)}")
        secret = _secret_config_name(draft.auth)
        if secret is not None:
            lines.append(f"  - name: {secret}")
            lines.append(f'    prompt: "Secret for the {draft.auth} auth of this API"')
            lines.append("    required: true")
    lines.append("---")
    lines.append("")
    lines.append(f"# {draft.name}")
    lines.append("")
    lines.append(f"Author: {draft.author}")
    lines.append("")
    lines.append("## Description")
    lines.append("")
    lines.append(draft.description)
    lines.append("")
    lines.append("## When to Use This Skill")
    lines.append("")
    if draft.kind == KIND_API:
        lines.append(f"Use this skill when the user asks about {draft.name.lower()}.")
        lines.append("")
        lines.append("It calls the API below through `fetch_url`; the base URL")
        lines.append("and the secret come from the skill config, never from chat.")
    else:
        lines.append(f"Use this skill when the user asks to {draft.description.lower()}.")
        lines.append("")
        lines.append("Run the steps in order; stop and report when a step fails")
        lines.append("instead of skipping ahead.")
    lines.append("")
    lines.append("## Tools")
    lines.append("")
    if draft.kind == KIND_API:
        for endpoint in draft.endpoints:
            lines.append(f"- `{endpoint.method} {endpoint.path}` — {endpoint.purpose}")
    else:
        for index, step in enumerate(draft.steps, start=1):
            tool = step.get("tool") or "?"
            text = step.get("arguments", {}).get("step", "")
            lines.append(f"{index}. `{tool}` — {text}")
    lines.append("")
    if draft.kind == KIND_API:
        lines.append("## Configuration")
        lines.append("")
        lines.append("Set at setup time, asked once and stored with the skill:")
        lines.append("")
        lines.append("- `base_url` — the API base URL.")
        secret = _secret_config_name(draft.auth)
        if secret is not None:
            lines.append(f"- `{secret}` — the secret for {draft.auth} auth.")
        elif draft.auth == AUTH_OAUTH:
            lines.append("- OAuth login — sign in once, the token lives in the vault.")
        else:
            lines.append("- No secret — this API needs no auth.")
        lines.append("")
    lines.append("## Examples")
    lines.append("")
    if draft.kind == KIND_API:
        lines.append("Call the first endpoint with the configured base URL:")
        lines.append("")
        lines.append("```python")
        lines.append("fetch_url(")
        first = (
            draft.endpoints[0] if draft.endpoints else ApiEndpoint("GET", "/", "No endpoint yet")
        )
        lines.append(f'    method="{first.method}",')
        lines.append(f'    url="{{{{config.base_url}}}}{first.path}",')
        secret = _secret_config_name(draft.auth)
        if secret is not None:
            lines.append(f'    secret="{{{{config.{secret}}}}}",')
        lines.append(")")
        lines.append("```")
    else:
        lines.append("Register the drafted skill as a trial skill:")
        lines.append("")
        lines.append("```python")
        lines.append("from puspa.governor import InMemorySkillStore, SkillCompiler")
        lines.append("from puspa.marketplace.skill_creator import register_trial_skill")
        lines.append("")
        lines.append("compiler = SkillCompiler(InMemorySkillStore())")
        lines.append("result = register_trial_skill(draft, compiler)")
        lines.append("```")
    lines.append("")
    return "\n".join(lines)


def validate_draft(draft: SkillDraft, *, known_tools: set[str] | None = None) -> list[str]:
    """Every problem with ``draft`` (empty = valid, registerable).

    Checks the draft itself (name, kind, steps or endpoints,
    auth, base URL), that every tool it names resolves, and that
    the rendered ``SKILL.md`` parses and passes the authoring
    standards. Problems are plain sentences, so the authoring
    flow can show them and ask for the missing piece.
    """
    known = known_tools if known_tools is not None else known_tool_names()
    problems: list[str] = []
    if not draft.name.strip():
        problems.append("a skill needs a name")
    if not SKILL_ID_PATTERN.match(draft.skill_id):
        problems.append(f"skill id {draft.skill_id!r} must match ^[a-z0-9]+(-[a-z0-9]+)*$")
    if not draft.description.strip():
        problems.append("a skill needs a description of what it does")
    if draft.kind == KIND_WORKFLOW:
        if not draft.steps:
            problems.append("a workflow skill needs at least one step")
        for index, step in enumerate(draft.steps, start=1):
            tool = step.get("tool")
            if not isinstance(tool, str) or not tool:
                problems.append(f"step {index} has no tool; say which tool runs it")
            elif tool not in known:
                problems.append(f"step {index} uses unknown tool {tool!r}")
            if not isinstance(step.get("arguments"), dict):
                problems.append(f"step {index} arguments must be a mapping")
    elif draft.kind == KIND_API:
        if not draft.base_url.startswith(("http://", "https://")):
            problems.append(f"base URL {draft.base_url!r} must start with http:// or https://")
        if not draft.endpoints:
            problems.append("an API skill needs at least one endpoint")
        for endpoint in draft.endpoints:
            if endpoint.method not in HTTP_METHODS:
                problems.append(
                    f"endpoint {endpoint.path!r} has unknown method {endpoint.method!r}"
                )
            if not endpoint.path.startswith("/"):
                problems.append(f"endpoint path {endpoint.path!r} must start with '/'")
        if "fetch_url" not in known:
            problems.append("API skills call fetch_url, which is not a known tool here")
    else:
        problems.append(f"unknown skill kind {draft.kind!r}")
    try:
        text = render_skill_md(draft)
        frontmatter, _body = parse_frontmatter(text)
        for violation in check_authoring(text, frontmatter):
            problems.append(f"authoring: {violation.code}: {violation.message}")
    except ManifestError as exc:
        problems.append(f"the drafted SKILL.md does not parse: {exc}")
    return problems


def _trial_step_runner(known_tools: set[str]) -> Callable[[dict], dict]:
    """A step runner for trial replay: a tool that resolves passes.

    The trial replay only proves the steps are well-formed and
    every tool resolves; it runs nothing for real. A step naming
    an unknown tool raises — validation already rejects those,
    so a raise here is a caller bug, not a failed replay.
    """

    def run(step: dict) -> dict:
        tool = step.get("tool")
        if tool not in known_tools:
            raise ValueError(f"trial replay: unknown tool {tool!r}")
        return {"ok": True, "tool": tool}

    return run


def register_trial_skill(
    draft: SkillDraft,
    compiler: SkillCompiler,
    *,
    step_runner: Callable[[dict], dict] | None = None,
    agent_id: str | None = None,
) -> CompileResult:
    """Register ``draft`` as a trial skill through the compiler.

    An invalid draft returns a failed result with the reasons —
    nothing is stored. A valid draft compiles from its own steps
    (one run: the described procedure) and is stored as a trial
    skill under ``authored:<skill id>``: versioned, replayable,
    never silently active.
    """
    problems = validate_draft(draft)
    if problems:
        return CompileResult(skill=None, detail="the draft is not valid: " + "; ".join(problems))
    run = SkillRun(steps=draft.to_steps(), last_result={"ok": True})
    runner = step_runner or _trial_step_runner(known_tool_names())
    return compiler.compile(
        f"authored:{draft.skill_id}", [run], step_runner=runner, agent_id=agent_id
    )


#: Questions the interview asks, by pending key.
_QUESTIONS = {
    "description": (
        "Describe the workflow or the API you want as a skill. "
        "For a workflow, list the steps one per line. For an API, "
        "paste its base URL and its endpoints as METHOD /path lines."
    ),
    "kind": ("Is this a workflow with steps, or an API? Answer 'workflow' or 'api'."),
    "name": "What should the skill be called?",
    "steps": (
        "List the steps, one per line — numbered or bulleted. "
        "Name the tool in a step when you know it, for example "
        "'1. check the price with price_check'."
    ),
    "base_url": "What is the API base URL (starting with http:// or https://)?",
    "endpoints": (
        "List the endpoints, one per line, as METHOD /path — what it does. "
        "For example: GET /users - list users."
    ),
    "auth": (
        "How does the API authenticate: none, api key, bearer token, "
        "or oauth? Answer one of those four."
    ),
}


class SkillCreatorSession:
    """The skill-authoring interview: questions until a draft exists.

    Feed the user's replies to :meth:`answer`; it returns the next
    question, or None when the draft is complete — then
    :meth:`draft` holds it. A reply that does not answer the
    question (empty, unknown tool, bad URL, unknown auth) re-asks
    the same question with the reason, so the interview never
    guesses past a gap.
    """

    def __init__(
        self,
        *,
        author: str = "user",
        known_tools: set[str] | frozenset[str] | None = None,
    ) -> None:
        if not author or not isinstance(author, str):
            raise ValueError("a skill-creator session needs a non-empty author")
        self._author = author
        self._known_tools = set(known_tools) if known_tools is not None else known_tool_names()
        self._description = ""
        self._name = ""
        self._kind: str | None = None
        self._steps: list[dict] = []
        self._endpoints: list[ApiEndpoint] = []
        self._base_url = ""
        self._auth = ""
        self._pending: list[str] = ["description"]
        self._error = ""

    @property
    def is_complete(self) -> bool:
        """True when the draft is ready (no question left)."""
        return not self._pending

    @property
    def current_key(self) -> str | None:
        """The pending question's key, or None when complete."""
        return self._pending[0] if self._pending else None

    def next_question(self) -> str | None:
        """The question to ask now, or None when the draft is ready."""
        if not self._pending:
            return None
        text = self._question_for(self._pending[0])
        if self._error:
            return f"{self._error}\n{text}"
        return text

    def _question_for(self, key: str) -> str:
        if key.startswith("tool:"):
            index = int(key.split(":", 1)[1])
            step_text = self._steps[index].get("arguments", {}).get("step", "")
            tools = ", ".join(sorted(self._known_tools))
            return (
                f"Step {index + 1} says: {step_text!r}. Which tool runs it? Known tools: {tools}."
            )
        return _QUESTIONS[key]

    def answer(self, text: str) -> str | None:
        """Feed one reply; return the next question or None when done."""
        if self.is_complete:
            return None
        reply = (text or "").strip()
        if not reply:
            self._error = "I need an answer to continue."
            return self.next_question()
        self._error = ""
        key = self._pending[0]
        if key == "description":
            self._take_description(reply)
        elif key == "kind":
            self._take_kind(reply)
        elif key == "name":
            self._take_name(reply)
        elif key == "steps":
            self._take_steps(reply)
        elif key.startswith("tool:"):
            self._take_tool(key, reply)
        elif key == "base_url":
            self._take_base_url(reply)
        elif key == "endpoints":
            self._take_endpoints(reply)
        elif key == "auth":
            self._take_auth(reply)
        return self.next_question()

    def draft(self) -> SkillDraft | None:
        """The authored draft, or None while the interview runs."""
        if self._pending or self._kind is None or not self._name:
            return None
        if self._kind == KIND_WORKFLOW and not self._steps:
            return None
        if self._kind == KIND_API and (not self._base_url or not self._endpoints):
            return None
        return SkillDraft(
            skill_id=slugify(self._name),
            name=self._name,
            description=self._description,
            kind=self._kind,
            steps=[dict(step) for step in self._steps],
            endpoints=list(self._endpoints),
            auth=self._auth or AUTH_NONE,
            base_url=self._base_url,
            author=self._author,
        )

    # -- one reply per question ----------------------------------------

    def _take_description(self, reply: str) -> None:
        self._description = reply
        self._pending.pop(0)
        parsed = parse_api_description(reply)
        steps = parse_workflow_steps(reply, self._known_tools)
        if parsed["base_url"] or parsed["endpoints"] or re.search(r"\bapi\b", reply.lower()):
            self._kind = KIND_API
            self._base_url = parsed["base_url"]
            self._endpoints = parsed["endpoints"]
            self._auth = parsed["auth"]
            self._pending.extend(["name"])
            if not self._base_url:
                self._pending.append("base_url")
            if not self._endpoints:
                self._pending.append("endpoints")
            if not self._auth:
                self._pending.append("auth")
        elif steps:
            self._kind = KIND_WORKFLOW
            self._steps = steps
            self._pending.append("name")
            for index, step in enumerate(steps):
                if not step.get("tool"):
                    self._pending.append(f"tool:{index}")
        else:
            self._pending.append("kind")

    def _take_kind(self, reply: str) -> None:
        lowered = reply.lower()
        if "workflow" in lowered or "step" in lowered:
            self._kind = KIND_WORKFLOW
            self._pending.pop(0)
            self._pending.extend(["name", "steps"])
        elif re.search(r"\bapi\b", lowered):
            self._kind = KIND_API
            self._pending.pop(0)
            self._pending.extend(["name", "base_url", "endpoints", "auth"])
        else:
            self._error = "Answer 'workflow' or 'api'."

    def _take_name(self, reply: str) -> None:
        if not slugify(reply):
            self._error = "That name has no usable words; give the skill a name."
            return
        self._name = reply
        self._pending.pop(0)

    def _take_steps(self, reply: str) -> None:
        steps = parse_workflow_steps(reply, self._known_tools)
        if not steps:
            self._error = "I found no steps; list them one per line."
            return
        self._steps = steps
        self._pending.pop(0)
        for index, step in enumerate(steps):
            if not step.get("tool"):
                self._pending.append(f"tool:{index}")

    def _take_tool(self, key: str, reply: str) -> None:
        index = int(key.split(":", 1)[1])
        wanted = reply.strip().lower()
        canonical = next((name for name in self._known_tools if name.lower() == wanted), None)
        if canonical is None:
            self._error = f"{reply.strip()!r} is not a known tool; name one from the list."
            return
        self._steps[index]["tool"] = canonical
        self._pending.pop(0)

    def _take_base_url(self, reply: str) -> None:
        if not reply.startswith(("http://", "https://")):
            self._error = "The base URL must start with http:// or https://."
            return
        self._base_url = reply.rstrip(".,;:")
        self._pending.pop(0)

    def _take_endpoints(self, reply: str) -> None:
        endpoints = parse_api_description(reply)["endpoints"]
        if not endpoints:
            self._error = "I found no endpoints; list them as METHOD /path lines."
            return
        self._endpoints = endpoints
        self._pending.pop(0)

    def _take_auth(self, reply: str) -> None:
        lowered = reply.lower()
        if "oauth" in lowered:
            self._auth = AUTH_OAUTH
        elif "bearer" in lowered:
            self._auth = AUTH_BEARER
        elif "api key" in lowered or "apikey" in lowered or "api-key" in lowered:
            self._auth = AUTH_API_KEY
        elif "none" in lowered or "no auth" in lowered:
            self._auth = AUTH_NONE
        else:
            self._error = "Answer one of: none, api key, bearer token, oauth."
            return
        self._pending.pop(0)


# -- CLI: `puspa skill create` -------------------------------------------


def add_skill_parser(sub) -> None:
    """Add the ``skill`` command group to the ``puspa`` parser.

    Standalone form (used by tests and direct CLI use): creates the
    ``skill`` parser with only the ``create`` subcommand. The real CLI
    registers hub + authoring together via ``add_create_subcommand``.
    """
    skill = sub.add_parser(
        "skill",
        help="Create skills by talking (COW-04).",
        description=(
            "Describe a workflow or a third-party API in conversation "
            "and author it as a reusable skill. The draft SKILL.md is "
            "shown first; without --approve nothing is registered."
        ),
    )
    add_create_subcommand(skill)


def add_create_subcommand(skill) -> None:
    """Add the ``create`` subcommand to an existing ``skill`` parser.

    Split from ``add_skill_parser`` so the top-level CLI can register the
    hub commands (list/search/install, COW-03) and the authoring command
    (create, COW-04) on ONE ``skill`` parser instead of two parsers
    overwriting each other. It reuses the parser's existing subparsers
    action when there is one (a second ``add_subparsers`` call would
    reject the other group's subcommands), and creates one otherwise.
    """
    skill_sub = None
    for action in skill._actions:
        if isinstance(action, argparse._SubParsersAction):
            skill_sub = action
            break
    if skill_sub is None:
        skill_sub = skill.add_subparsers(dest="skill_command", metavar="SKILL_COMMAND")
    create = skill_sub.add_parser(
        "create",
        help="Describe a workflow or API; author it as a skill.",
        description=(
            "Interview the user for a workflow or API description, "
            "draft a SKILL.md, validate it, and — with --approve — "
            "register it as a trial skill through the skill compiler."
        ),
    )
    create.add_argument(
        "--describe",
        default=None,
        help="The workflow or API description (asked interactively when omitted).",
    )
    create.add_argument(
        "--name", default=None, help="Skill name (asked interactively when omitted)."
    )
    create.add_argument(
        "--user",
        default=None,
        help=(
            "User id: register the trial skill durably under this user "
            "(needs the database). Without it the trial lives only in this run."
        ),
    )
    create.add_argument(
        "--author",
        default="user",
        help="Author credited in the drafted SKILL.md (default: user).",
    )
    create.add_argument(
        "--approve",
        action="store_true",
        help="Register the drafted skill as a trial skill (without it, only show the draft).",
    )


def run_skill(args: argparse.Namespace, *, input_fn=None, print_fn=None) -> int:
    """Dispatch ``puspa skill ...`` (test seam: inject input/print)."""
    if args.skill_command == "create":
        return _run_skill_create(args, input_fn=input_fn, print_fn=print_fn)
    print_fn = print_fn or print
    print_fn("Usage: puspa skill create [--describe ...] [--name ...] [--approve]")
    return 0


def _run_skill_create(args: argparse.Namespace, *, input_fn=None, print_fn=None) -> int:
    """`puspa skill create`: interview, draft, show, register on approval."""
    input_fn = input_fn or input
    print_fn = print_fn or print
    session = SkillCreatorSession(author=args.author)
    try:
        while not session.is_complete:
            key = session.current_key
            if key == "description" and args.describe:
                session.answer(args.describe)
                continue
            if key == "name" and args.name:
                session.answer(args.name)
                continue
            question = session.next_question()
            if question is None:
                break
            print_fn(question)
            session.answer(input_fn("> "))
    except EOFError:
        print_fn("Cancelled; nothing was registered.")
        return 1
    draft = session.draft()
    if draft is None:
        print_fn("The interview did not finish; nothing was registered.")
        return 1
    text = render_skill_md(draft)
    print_fn("Draft SKILL.md:")
    print_fn(text)
    problems = validate_draft(draft)
    if problems:
        print_fn("The draft is not valid, so nothing was registered:")
        for problem in problems:
            print_fn(f"- {problem}")
        return 1
    if not args.approve:
        print_fn("Nothing was registered. Re-run with --approve to register this as a trial skill.")
        return 0
    if args.user:
        store: InMemorySkillStore | ProceduralSkillStore = ProceduralSkillStore(user_id=args.user)
    else:
        store = InMemorySkillStore()
    compiler = SkillCompiler(store)
    result = register_trial_skill(draft, compiler)
    if result.skill is None:
        print_fn(f"Could not register the trial skill: {result.detail}")
        return 1
    print_fn(
        f"Registered trial skill {draft.skill_id!r} v{result.skill.version} ({result.detail})."
    )
    print_fn(
        "Trial skills never run silently: the skill is stored as trial "
        "and must pass its replay before it is used."
    )
    return 0
