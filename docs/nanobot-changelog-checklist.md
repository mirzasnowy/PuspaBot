# Nanobot changelog checklist (nanobot pin process, plan section 9.4)

Fill this in for every nanobot bump PR, for the upstream range from
the old pin to the new pin. The point is not paperwork: every
upstream change to tools, sessions, or hooks can silently break the
wrapper interface in `puspa/chassis/`, and the chassis contract lane
only catches what its tests cover. Read the upstream changelog and
the diff for the range, then answer each section — even when the
answer is "no change". A section left blank is an unreviewed section,
not a clean one.

Upstream range for this bump: old pin `________` to new pin `________`.

## 1. Tools (`PuspaTool`, `PuspaRegistry`, `SandboxTool`)

The wrapper follows the upstream `_LegacyErrorPrefixTool` pattern
(`nanobot/agent/tools/loader.py`): every attribute and schema is
passed through, only `execute` adds PuspaBot checks. Upstream changes
to any of these break that pass-through.

- [ ] `nanobot/agent/tools/base.py` — `Tool` / `ToolResult`: did the
      base class change (`execute` signature, `to_schema`,
      `validate_params`, new required attributes)? `PuspaTool`
      (`puspa/chassis/tools.py`) forwards these by hand, so a new
      attribute upstream is invisible until a test or a call fails.
- [ ] `nanobot/agent/tools/loader.py` — did the loading pattern
      change (how builtin tools register, the "caller wins" rule the
      `SandboxTool` registration order relies on)?
- [ ] `nanobot/agent/tools/registry.py` — did `ToolRegistry`
      change (`register`, `prepare_call`, `get_definitions`,
      `execute_tool_calls`)? `PuspaRegistry.prepare_call` is the
      backstop: if upstream stops calling `prepare_call`, or changes
      what its third return value means, tools can run unwrapped.
- [ ] Builtin tool set — were tools added, removed, or renamed?
      A renamed builtin that PuspaBot excludes by name (for example the
      host shell tool, which never enters a PuspaBot token) can quietly
      re-enter under its new name.
- [ ] MCP — did the MCP provider or its tool registration change?
      MCP tools enter the registry through the same `register` path,
      so a change there affects every wrapped MCP tool at once.

Notes / findings:

## 2. Sessions (`bind_turn`, `AgentBrain`, session storage)

`bind_turn` (`puspa/chassis/agent_session.py`) binds a login session
to a permission token for one turn, and `AgentBrain` keeps one loop
per session. Upstream session changes affect what survives between
turns and what a new turn can see.

- [ ] `nanobot/session/` — did the session store change (schema,
      `sqlite_store.py`, history, summary, recovery)? A store change
      can orphan saved loops or change what `get_session_brain`
      restores.
- [ ] `nanobot/agent/loop.py` / `runner.py` — did the turn loop
      change (how a loop is created, resumed, or discarded)? The
      saved-loop restore in `_load_saved_loop` depends on the loop
      shape.
- [ ] `Nanobot.run` / `AgentLoop.from_config` — did the composition
      parameters change (`tool_registry`, `hooks`, `attributes`)?
      `assemble` (`puspa/chassis/factory.py`) passes the PuspaBot
      registry and hook factory through these; a renamed parameter
      fails loudly, a changed default fails quietly.
- [ ] Request context — did `RequestContext` or the attributes slot
      change? The permission token travels through it
      (`puspa/chassis/context.py`); a change here can drop the token
      and fail every tool call closed.

Notes / findings:

## 3. Hooks (`PuspaHook`, skill hooks)

`PuspaHook` (`puspa/chassis/hooks.py`) subclasses upstream
`AgentHook` and implements the guardrail cycle (`check_input`,
`check_output`, `check_action`, `finalize_content`) plus the ledger
writes. Upstream hook changes are the most common source of silent
breaks, because a hook that upstream stops calling simply never runs.

- [ ] `nanobot/agent/hook.py` — did `AgentHook` change (new hook
      points, renamed methods, changed call order, changed return
      contract)? A new upstream hook point that PuspaBot does not
      implement is a gap until someone adds it; a renamed one breaks
      the subclass silently (the old method just never runs).
- [ ] `nanobot/agent/hooks/` — did any built-in hook behaviour change
      in a way that overlaps `PuspaHook` (double enforcement, or an
      upstream hook now doing what PuspaBot assumed only it did)?
- [ ] Skill hooks — did the skill loading path change
      (`SkillsLoader`, skill manifests)? `PuspaSkillsLoader`
      (`puspa/chassis/skill_loader.py`) requires signed skills; an
      upstream change to how skills are discovered can admit an
      unsigned skill or drop a signed one.
- [ ] `SkillHookRegistry` points (`HOOK_SKILL_INSTALLED`,
      `HOOK_SKILL_USED`, `HOOK_SKILL_BLOCKED`) — did upstream add a
      skill lifecycle event PuspaBot should observe?

Notes / findings:

## 4. Everything else that touches the wrapper

- [ ] `nanobot/config/` — did config schema or defaults change in a
      way that alters what `assemble` builds (providers, workspace
      paths, sandbox settings)?
- [ ] `nanobot/security/` / `nanobot/execution/` — did the workspace
      policy, sandbox launcher, or SSRF guard change? PuspaBot's
      sandbox and gate assume the upstream guards behave as
      inventoried; a weakened upstream guard is a PuspaBot hole.
- [ ] License / notices — did `LICENSE` or `THIRD_PARTY_NOTICES.md`
      change? The MIT basis of the pin must still hold; a license
      change blocks the bump outright.
- [ ] New upstream dependencies — did the bump add a dependency
      (new entry in upstream `pyproject.toml`)? It lands in
      `requirements-hashed.txt` on the next compile; check its
      license before merging.

Notes / findings:

## Sign-off

- Reviewer: ________
- Contract lane green on the new pin: yes / no
- Full suite green on the new pin: yes / no
- Fork rule fired: yes / no (if yes, stop here — see
  `docs/nanobot-pin-process.md` section 5)
