# nanobot inventory (Puspa chassis) — Phase 0

**Inventory date:** 17 November 2026
**Source:** local clone `~/workspace/goals/hard-ai-engineering-portfolio-project/build/fase-0/nanobot`
**Origin repo:** https://github.com/HKUDS/nanobot

## 1. Clone verification

| Item | Result |
|---|---|
| Clone | Succeeded, branch `main`, remote `origin` = https://github.com/HKUDS/nanobot.git |
| HEAD | `e76717445058f4670ac9299cc89f91504da2f5f2` — "fix(webui): open ready apps by default (#6685)" |
| HEAD date | 2026-11-17 11:21:26 +0800 (newest commit at clone time, 17 Nov 2026) |
| License | **MIT verified** — file `LICENSE`, "Copyright (c) 2025-present Xubin Ren and the nanobot contributors". `pyproject.toml` also states `license = MIT`. |
| Main language | Python (1,071 `.py` files; ~193,000 lines in the `nanobot/` package, ~144,000 lines outside the `tests/` folder) |
| Repo size | 124 MB on disk (including `.git`) |
| Package version | `nanobot-ai` 0.4.1 |
| Working status | The cloned repo is **unchanged** (clean git status). No push to GitHub. No dependencies installed; nanobot not run. |

**Correction to the survey:** the repo survey called nanobot's core "about 12 thousand lines that can be read end to end". That number doesn't match the clone: the `nanobot/` package alone is ~144 thousand lines of Python outside tests (468 files), and ~193 thousand lines including channel tests. This chassis is far bigger than the survey estimated. Still Python and still one tidy package, but the plan to read all the code needs adjusting (read per module, not all at once).

## 2. Top-level directory structure

| Path | Contents |
|---|---|
| `nanobot/` | Python application code (one package, see section 3) |
| `packages/client-events/` | Companion package (client events) |
| `webui/` | WebUI: React + Vite + Tailwind app (TypeScript). Its build (`nanobot/web/dist/`) is produced via `bun run build` and is not committed |
| `tui/` | Native desktop TUI (TypeScript/Bun) |
| `tests/` | Repo-level pytest tests (75% coverage gate) |
| `docs/` | Full documentation: `architecture.md`, `memory.md`, `automations.md`, `mcp-presets.md`, `cli-reference.md`, `configuration.md`, `deployment.md`, etc. |
| `scripts/` | Release/CI utilities: `install.sh`, `ci_gate.py`, `verify_release_approval.py`, `install_channel_dependencies.py`, etc. |
| `Dockerfile`, `docker-compose.yml`, `docker-compose.bwrap.yml` | Image + gateway compose (ports 18790 WebUI, 8765); bwrap variant for the bubblewrap sandbox |
| `entrypoint.sh` | Container entrypoint: drop root privileges to the `nanobot` user (fail-closed on failure), prepare `~/.nanobot/config.json` |
| `pyproject.toml` | Package metadata, dependencies, console-script entry point, ruff/pyright/pytest configuration |
| `render.yaml`, `render-config.json` | One-click deploy to Render |
| `README.md` (91 KB) | Main document, very long |
| `SECURITY.md`, `THIRD_PARTY_NOTICES.md`, `CONTRIBUTING.md`, `AGENTS.md` | Security policy, third-party attribution, contributing |
| `conftest.py`, `hatch_build.py` | pytest configuration + hatch build hook |

### Structure of the `nanobot/` package (important modules)

| Module | Role |
|---|---|
| `nanobot/cli/` | CLI entry point (`entry.py`, `commands.py`, `gateway.py`, `webui.py`, `agent.py`, `onboard.py`, `update.py`, etc.) |
| `nanobot/agent/` | Agent core: `loop.py` (turn orchestration), `runner.py` (model/tool loop), `context.py`, `memory.py`, `skills.py`, `subagent.py`, `execution_policy.py`, `goal_permission.py`, `hooks/`, `tools/` |
| `nanobot/agent/tools/` | All built-in tools: `filesystem.py`, `shell.py`, `web.py`, `search.py`, `cron.py`, `mcp.py`, `sessions.py`, `subagent.py`, `message.py`, `apply_patch.py`, `rg.py`, `image_generation.py`, `self.py`, `cli_apps.py`, `registry.py`, `loader.py`, `base.py`, `schema.py` |
| `nanobot/bus/` | MessageBus: `events.py`, `queue.py`, `delivery.py`, `outbound_events.py` — all inbound/outbound messages pass through here |
| `nanobot/session/` | Session storage: `manager.py`, `sqlite_store.py` (SQLite), `history.py`, `summary.py`, `history_graph.py`, `recovery.py`, `state.py` |
| `nanobot/channels/` | 19 channels (see section 7). Each channel: `manifest.py`, `runtime.py`, `validation.py`, `webui/` |
| `nanobot/cron/` | Scheduler: `types.py`, `service.py`, `binding.py`, `bound_runner.py`, `session_delivery.py` |
| `nanobot/triggers/` | Local triggers (`local_store.py`, `local_runner.py`) |
| `nanobot/config/` | Configuration: `schema.py` (pydantic), `loader.py`, `paths.py`, `home.py`, `network.py`, `watcher.py` |
| `nanobot/gateway/` | Gateway daemon: `runtime.py`, `service.py` |
| `nanobot/providers/` | LLM providers: OpenAI-compatible, Anthropic, Azure, Bedrock, GitHub Copilot, Codex, xAI, fallback, routing |
| `nanobot/security/` | `workspace_policy.py`, `workspace_access.py`, `sandbox_launcher.py`, `network.py` (anti-SSRF, pinned DNS) |
| `nanobot/execution/` | Shell/sandbox execution: `sandbox.py` (bwrap + seatbelt backends), `processes.py`, `http.py` |
| `nanobot/skills/` | Built-in skills (see section 4) |
| `nanobot/templates/` | Prompt + memory templates: `agent/` (`dream.md`, `subagent_system.md`, `tool_contract.md`, `skills_section.md`, etc.), `memory/MEMORY.md`, `prompts/` |
| `nanobot/webui/` | MCP presets (`mcp_presets.json`) + packaged WebUI assets |
| `nanobot/sdk/` | Python SDK for integration from code |
| `nanobot/llm_usage/`, `nanobot/goal/`, `nanobot/pairing/` | LLM usage tracking, goals, DM-sender pairing/approval |
| `nanobot/apps/cli/` | CLI apps (`CliAppManager`, skill aliases) |
| `nanobot/command/` | Slash commands (`/dream`, `/compact`, `/sessions`, `/diff`, etc.) |

## 3. Entry point — how nanobot runs

There are three run modes, all from one console script:

- **`nanobot` → `nanobot.cli.entry:main`** (registered under `[project.scripts]` in `pyproject.toml`). `entry.py` forwards to the subcommand.
- **`nanobot webui`** — the main mode for users: starts/joins the local gateway together then opens the WebUI in the browser (localhost, login via a link containing a token after `#`). The WebUI is served from the `nanobot/web/dist/` bundle.
- **`nanobot agent`** — terminal TUI (or `--classic` / `--no-tui` for classic mode, `-m/--message` for a single run). The TUI uses the launch directory as the project workspace and shares sessions + gateway with the WebUI. Slash commands `/`, mentions `@`, image paste, `$` for skill references.
- **`nanobot gateway`** — gateway daemon (used by Docker: `command: ["gateway"]`, `restart: unless-stopped`). The gateway is a managed process (`gateway/runtime.py`, `gateway/service.py`) with a health identity (instance_id, run_id, version) and a lock file. Docker Compose maps `127.0.0.1:18790:18790` (WebUI) and `8765:8765`.
- **`nanobot-desktop-tui` → `nanobot.cli.desktop_tui:main`** — second entry point for the desktop TUI.
- **Cloud deploy:** one-click Render (`render.yaml`) or Docker (`Dockerfile` + compose). Data lives in `~/.nanobot` (config `config.json`, default workspace `~/.nanobot/workspace`).

Runtime flow (from `docs/architecture.md`): **Channel → MessageBus (InboundMessage) → AgentLoop (session, workspace, context) → AgentRunner (provider/tool loop) → Provider → Tools → back to AgentLoop → MessageBus (OutboundMessage) → Channel.** `AgentLoop` belongs to the channel-facing turn; `AgentRunner` belongs to the model/tool loop. MCP is managed outside the loop (the composition root creates the `MCPProvider`; the loop only uses the shared `ToolRegistry`).

Configuration: a single `~/.nanobot/config.json` file, loaded as a pydantic model (`nanobot/config/schema.py`, `Config`), with `pydantic-settings`. There is a config watcher (`config/watcher.py`).

## 4. Skill/tool system

### Skill format
A skill is a folder containing `SKILL.md` with YAML frontmatter, the standard agentskills format (compatible with OpenClaw/ClawHub):

```yaml
---
name: weather
description: Get current weather and forecasts.
homepage: https://wttr.in/:help
metadata: {"nanobot":{"emoji":"🌤️","requires":{"bins":["curl"]}}}
---
```

The SKILL.md body is Markdown instructions for the agent (when to use it, steps, example commands). Progressive disclosure: the frontmatter description enters the context; the full body is read when the skill is used.

### Built-in skills (`nanobot/skills/`)
`weather`, `cron`, `memory`, `github`, `image-generation`, `summarize`, `tmux`, `skill-creator`, `nanobot-document`, `clawhub` (find + install skills from the public ClawHub registry), `my` (runtime introspection: check context capacity, switch models, write to the scratchpad).

### How skills are registered
- **Loader:** `nanobot/agent/skills.py` (`SkillsLoader`) — scans `nanobot/skills/` (built-in) and `<workspace>/skills/` (user-owned). Aliases from `CliAppManager` (installed CLI apps) also count as skills.
- **Metadata:** `nanobot/agent/skill_metadata.py` — parses + validates frontmatter (`parse_skill_metadata`, `valid_skill_metadata`).
- **No signing / verification.** Skills from the user workspace and from ClawHub load as-is; no signature check, no content scan. This is a point Puspa must harden (definition 2.6: signed skills).
- Skills can also be created automatically by Dream: `templates/agent/dream.md` orders the creation of `skills/<name>/SKILL.md` when a workflow has appeared at least 2 times.

### How tools are called
- **A tool = a Python class** in `nanobot/agent/tools/`, using the `@tool_parameters` decorator with a JSON schema (`nanobot/agent/tools/schema.py`: `StringSchema`, `NumberSchema`, `BooleanSchema`, `tool_parameters_schema`). Examples: `SubagentTool`, the `cron`, `message`, `my` tools.
- **Registry:** `nanobot/agent/tools/registry.py` (`ToolRegistry`) — `register()`, `unregister()`, `get()`; tool definitions are cached to send to the provider as function-calling schemas.
- **Loader:** `nanobot/agent/tools/loader.py` (`ToolLoader`) — assembles the registry from config.
- **Execution:** `AgentRunner` (`agent/runner.py`) runs the loop: send a message to the provider → receive a tool call → execute the tool → return the result to the model → repeat until a final answer or the iteration limit. Per-request context travels via `ContextVar` (`agent/tools/context.py`: `RequestContext`, `ToolContext`).
- **Built-in tools:** filesystem (read/write/patch), shell (with exec sessions), web fetch + search (`ddgs`), cron, message (send to a channel), sessions (manage sessions), subagent (spawn subagents), MCP (see section 8), image generation, `my` (self), `cli_apps`, `rg` (ripgrep), `apply_patch`.
- **Tool limits:** `ToolsConfig` (`config/schema.py`) has `enabled_tools` per MCP server, `max_session_messages_per_minute` (default 6), `restrict_to_workspace` (**default `False`** — the policy intent is "keep tool access inside the workspace", but it is off by default), `ssrf_whitelist`, `webui_allow_local_service_access` (default True), `webui_allow_remote_package_install` (default False). There is no per-skill tool allowlist and no approval before execution.

## 5. Memory system

What exists is **Markdown-file memory + JSONL logs**, with no vector database and no structured facts:

- **Canonical files** (`nanobot/agent/memory.py`, class `MemoryStore` — "Pure file I/O for memory files"):
  - `memory/MEMORY.md` — long-term memory (template in `nanobot/templates/memory/MEMORY.md`: User Information, Preferences, Project Context, Important Notes)
  - `memory/history.jsonl` — append-only conversation log (each entry: `cursor`, `timestamp`, `content`; max 1,000 entries by default), plus legacy `HISTORY.md`
  - `SOUL.md` — agent behaviour/guardrails; `USER.md` — user attributes/preferences (both at the workspace root)
  - `memory/.cursor`, `memory/.dream_cursor` — Dream consumption cursors
- **Dream (consolidation):** `templates/agent/dream.md` — a consolidator agent that summarises history into the canonical files (routing per file: SOUL/USER/MEMORY/skill), with retention tags `[skip]`, `[correction]`, `[permanent]`, `[durable]`, `[ephemeral]`. Runs on schedule when enabled, or manually via the `/dream` command; `/dream-log` and `/dream-restore <sha>` view/restore old versions. **Memory is git-backed** (`nanobot/utils/gitstore.py`, `GitStore`) so each Dream change is recorded as a restorable commit.
- **The `memory` skill:** only instructs the agent to search `history.jsonl` with a text-search tool. No semantic search.
- **Context compaction:** `agent/autocompact.py` + `session/summary.py` — session summaries as context grows, or manual `/compact`.
- **Sessions:** `session/sqlite_store.py` (SQLite) + `session/manager.py` — sessions, metadata, history, checkpoints, recovery; `session/history_graph.py` for history size/structure.
- **What does NOT exist:** structured memory (facts + provenance + validity + conflicts), vector search, a knowledge graph, per-agent/subagent memory scoping (all agents share the same workspace files), Proposals/user confirmation of inferred facts. Puspa must build all of that (mem0 + Graphiti per definition 2.7).

## 6. Cron / scheduler

Already present and complete enough for basic needs:

- **Model:** `nanobot/cron/types.py` — `CronSchedule` (kind `at` / `every` / `cron`, with `at_ms`, `every_ms`, cron `expr` + `tz`), `CronPayload` (kind `agent_turn` or `system_event`, `message`, `deliver`, `channel`, `to`, `session_key`, `origin_channel`, `origin_chat_id`), `CronRunRecord` (status `ok`/`error`/`skipped`, duration, error).
- **Service:** `nanobot/cron/service.py` — loads/stores jobs from **`jobs.json`** (with a `.corrupt-<ts>` backup if the file is corrupt), runs jobs, records execution history. `binding.py`, `bound_runner.py`, `session_delivery.py`, `session_turns.py` bind jobs to sessions and deliver results to the originating channel.
- **Tool + skill:** `nanobot/agent/tools/cron.py` (the `cron` tool: `add`/`list`/`remove`/pause/resume) and `nanobot/skills/cron/SKILL.md` — the agent creates schedules from natural language ("every day at 8am, remind me…", `cron_expr="0 9 * * 1-5"`, `at="<ISO datetime>"`, `every_seconds=1200`). Task kinds: Reminder, Task (the agent executes on each run), One-time (auto-deleted after running).
- **HEARTBEAT.md:** a periodic background-check pattern with flexible timing — it only notifies when there is an actionable change; explicitly NOT a replacement for cron for schedules that must be on time.
- **Note for Puspa:** nanobot cron is jobs in a local per-instance `jobs.json`, executed by the running gateway. The Puspa definition (2.12) wants scheduled tasks as Postgres rows + an APScheduler scheduler on the VPS + audit into the ledger — so this cron layer's pattern can be used, but its storage and execution need moving/wrapping to Postgres + the ledger.

## 7. Channels (WA / Telegram / etc.)

19 channel packages in `nanobot/channels/` (plus a `tests/` folder):

| Channel | Package | How it works (from runtime + manifest) |
|---|---|---|
| WhatsApp | `whatsapp/` | **neonize** (a WhatsApp Web library, dependencies `neonize>=0.5.2`, `python-magic`, `segno` for QR). Not the official Cloud API — this is the unofficial WA Web protocol, with a risk of number blocking. Config: `allow_from` (sender allowlist), `group_policy` open/mention, `database_path`, `lid_mappings`. Login via QR/pairing |
| WhatsApp (agent) | `whatsapp_agent/` | Second WA channel variant (agent) |
| Telegram | `telegram/` | `python-telegram-bot` (official Bot API, long polling). Message handlers, callback queries, reactions |
| Discord | `discord/` | Discord bot |
| Slack | `slack/` | Slack bot |
| Signal | `signal/` | Signal |
| Matrix | `matrix/` | Matrix |
| MS Teams | `msteams/` | Microsoft Teams |
| Feishu | `feishu/` | Feishu/Lark (websocket + `lark-oapi`) |
| WeChat | `weixin/` | WeChat |
| DingTalk | `dingtalk/` | DingTalk |
| WeCom | `wecom/` | WeCom |
| QQ | `qq/` | QQ |
| NapCat | `napcat/` | NapCat (QQ bridge) |
| MoChat | `mochat/` | MoChat |
| Email | `email/` | Email channel (read + reply to email as conversation) |
| Linear | `linear/` | Linear (with team-membership checks) |
| Mattermost | `mattermost/` | Mattermost |
| WebSocket | `websocket/` | Generic WebSocket channel (used by the WebUI + outside integrations) |

Common pattern per channel: `manifest.py` (defines `ChannelPlugin`: name, runtime, connector, setup spec, dependencies, webui), `runtime.py` (a channel class descending from `BaseChannel`, receiving messages → `MessageBus` → sending replies), `validation.py`, a `webui/` folder (channel settings UI). Channels are enabled from config; channel dependencies are installed separately (`scripts/install_channel_dependencies.py`).

**Note for Puspa:** the MVP definition (2.8) states **no WhatsApp and no Telegram in the MVP (PWA only)**. So these channels are reserves for post-MVP phases, not what's used first. If WA is used later, a decision is needed: stay on neonize (unofficial, blocking risk) or move to the official Cloud API.

## 8. MCP

Already present as an **MCP client** (nanobot connects to outside MCP servers), not an MCP server:

- **Client:** `nanobot/agent/tools/mcp.py` (`MCPProvider`) — stdio/SSE/streamable HTTP connections, retries for transient stream failures, remote OAuth (`mcp_oauth.py`), 30-second tool timeout.
- **Config:** `MCPServerConfig` (`config/schema.py`): `type` (stdio/sse/streamableHttp, auto-detect), `command`/`args`/`env`/`cwd` (stdio), `url`/`headers` (HTTP), `auth: oauth` (token stored outside config), `tool_timeout`, and **`enabled_tools` (default `["*"]` = all capabilities: tools, resources, prompts; when restricted, only the named tools, no resources/prompts)**. MCP tools are wrapped with the name `mcp_<server>_<tool>`.
- **Presets:** `nanobot/webui/mcp_presets.json` + `docs/mcp-presets.md` — ready-to-use MCP server presets from the WebUI.
- **What does NOT exist:** nanobot doesn't expose itself as an MCP server (no `mcp server` mode in this repo); no security scanning of MCP servers/tools (tool poisoning, rug-pull). Puspa needs to add curation + signing for allowed MCP servers.

## 9. Security that already exists (and what doesn't)

Already present:
- **Workspace path limits:** `security/workspace_policy.py` + `workspace_access.py` — helpers `resolve_path`, `is_path_within`, `require_path_within`, `WorkspaceScope` via `ContextVar` (`bind_workspace_scope`). Its own documentation stresses this is **not a replacement for an OS sandbox**.
- **Shell sandbox:** `execution/sandbox.py` — **bwrap** (bubblewrap, Linux) and **seatbelt** (macOS) backends, with read-only/read-write binds; `security/sandbox_launcher.py`; `docker-compose.bwrap.yml` (needs `SYS_ADMIN`, unconfined AppArmor/seccomp). The sandbox wraps shell execution, not one container per agent.
- **Network:** `security/network.py` — anti-SSRF (URL target validation, pinned DNS transport), `NetworkConfig` + `ssrf_whitelist` (excluded CIDRs, e.g. Tailscale). `config/network.py` for proxies.
- **Docker:** the entrypoint drops root privileges → the `nanobot` user (fail-closed), `cap_drop: ALL`, `no-new-privileges`.
- **Releases:** `release_trust.py` + `sigstore` (dependency `sigstore==4.5.0`) — package release-approval verification. This is for nanobot's own updates, not for skills.
- **DM pairing:** `pairing/store.py` — DM sender approval (allowlist of who may DM the agent). This is about who may talk, not about agent actions.
- **Goal permission:** `agent/goal_permission.py` — per-run goal-mutation permission bound via `ContextVar` (a pattern similar to the permission token, but scoped to goals only, not tools).

What does **NOT** exist (Puspa must build it):
- **No per-action approval.** Grepping `approval` across all of `nanobot/` finds only: DM sender approval (pairing), package release approval (sigstore), and the OAuth device prompt. No approval queue, no content-hash-bound approval, no spending limit as code.
- **`tools.restrictToWorkspace` defaults to off** (`False` in `ToolsConfig`) — per the survey note.
- **No skill signing** (workspace + ClawHub skills load without verification).
- **No permission tokens** per agent/subagent (no list of tools/domains/connectors/budget/expiry checked by code).
- **No single gate for outbound actions** (send/message/execute tools are called directly from the runner loop).
- **No per-agent/bot sandbox** (one gateway process + shared workspace; the sandbox only wraps shell commands).
- **No data separation between agents** (one workspace, one MEMORY.md, one history.jsonl).
- **No Google connectors** (Gmail/Drive/Calendar) — the email channel exists, but it isn't a minimal-scope Google OAuth connector. The `oauth-cli-kit` dependency already exists and the OAuth provider pattern (GitHub Copilot, Codex, xAI) can be copied.
- **No PWA** — the WebUI is a desktop-browser React app; no PWA manifest/service worker.

## 10. Hook points for Puspa's layers (analysis)

The most natural place to attach each Puspa layer, based on the structure read:

| Puspa layer | Hook point in nanobot | Why |
|---|---|---|
| **Single gate for outbound actions** | `nanobot/agent/runner.py` (tool-call loop) + `nanobot/agent/tools/registry.py` (`ToolRegistry.get`/execution) + `nanobot/bus/delivery.py` (outbound delivery) | All tool calls and all outbound messages pass through these points. One wrapper here closes every outbound path without changing each tool |
| **Permission token (narrowing)** | `nanobot/agent/tools/context.py` (`RequestContext`/`ToolContext` via `ContextVar`) + `nanobot/security/workspace_access.py` (`WorkspaceScope`, `bind_workspace_scope`) + `nanobot/agent/goal_permission.py` (existing per-run permission pattern) | The per-request context mechanism already exists; the token can hang off `RequestContext` and be checked in `ToolRegistry` before execution. `WorkspaceScope` is an example of a scope pattern that already works |
| **Ledger (append-only log)** | `nanobot/bus/events.py` + `nanobot/bus/queue.py` (all message events) + `nanobot/session/sqlite_store.py` (existing SQLite storage) + `nanobot/agent/memory.py` (`history.jsonl` append-only) | The bus is already the one place all events pass through; the ledger can be built as an event sink on the bus + a new SQLite/Postgres table, using the storage pattern already proven in `sqlite_store.py` |
| **Per-action approval** | `nanobot/agent/runner.py` (hold the tool call before execution) + WebUI (`webui/`, new approval queue) + `nanobot/bus/` (delivering the approval result back to the loop) | The runner is the only place tools execute; the approval queue just holds there. The "hold then continue" pattern already exists in `session/turn_continuation.py` and `agent/pending_inputs.py` |
| **Structured memory (mem0 + Graphiti)** | `nanobot/agent/memory.py` (`MemoryStore`) + `nanobot/templates/agent/dream.md` | `MemoryStore` is the only door for memory reads/writes; swap its contents for mem0/Graphiti without changing callers. Dream stays as the consolidator, but writes schema'd facts + provenance instead of free Markdown |
| **Per-bot sandbox** | `nanobot/execution/sandbox.py` (bwrap/seatbelt backends) + `nanobot/agent/tools/exec_session.py` + `docker-compose.bwrap.yml` | The sandbox backend already exists; what's missing is orchestrating one sandbox per agent/subagent and forcing its use (currently optional). Fail-closed is already demonstrated in `entrypoint.sh` |
| **Subagent model** | `nanobot/agent/subagent.py` (`SubagentManager`) + `nanobot/agent/tools/subagent.py` (the `subagent` tool: create/send/cancel/check) + `nanobot/templates/agent/subagent_system.md` | Spawning subagents per task **already exists and works** (create → task → wait/check/cancel, a private session per task). What's missing: a permission class per spawn (Reader/Executor/Verifier), a token narrowed from the parent, a 1-level limit, parallel + budget limits, a schema'd handoff contract. This is a layer on top of `SubagentManager`, not a rebuild |
| **Signed skills** | `nanobot/agent/skills.py` (`SkillsLoader`) + `nanobot/agent/skill_metadata.py` + `nanobot/skills/clawhub/` | All skills enter through `SkillsLoader`; one signature check here covers built-in, workspace, and ClawHub skills at once. The release-verification pattern (`release_trust.py` + sigstore) can be copied |
| **Google connectors** | The OAuth pattern in `nanobot/providers/` (`github_copilot_oauth.py`, `openai_codex_oauth.py`, `xai_oauth.py`) + the `oauth-cli-kit` dependency + the `email/` channel | The OAuth pattern + token storage outside config already exist; new Gmail/Drive/Calendar connectors follow this pattern |
| **Scheduler → ledger** | `nanobot/cron/service.py` (`jobs.json`) + `nanobot/triggers/local_store.py` | Cron jobs are already structured (`CronSchedule`/`CronPayload`); what changes is storage (jobs.json → Postgres) and recording executions in the ledger |
| **PWA** | `webui/` (React + Vite) | The WebUI already exists as a frontend; PWA means adding a manifest/service worker/push + Puspa's 7 screens, not writing UI from scratch |

## 11. Main Python dependencies

Python required: **>= 3.11** (developed/tested on 3.11 and 3.12; ruff/pyright target `py311`).

Core dependencies from `pyproject.toml` (package `nanobot-ai` 0.4.1):

| Dependency | Version | For |
|---|---|---|
| `typer` | >=0.20, <1.0 | CLI |
| `anthropic`, `openai` | anthropic >=0.100, <1.0; openai >=2.8 | LLM providers |
| `pydantic`, `pydantic-settings` | >=2.12, <3.0 | Config + schemas |
| `mcp` | >=1.26, <2.0 | MCP client |
| `httpx[socks]`, `aiohttp`, `websockets` | httpx >=0.28; aiohttp >=3.14; websockets 15–16 | HTTP/WebSocket (Feishu needs websockets <16) |
| `croniter` | >=6.0, <7.0 | Cron |
| `loguru`, `rich`, `prompt-toolkit`, `questionary` | — | Logging + TUI/CLI |
| `cryptography`, `sigstore==4.5.0` | — | Security + release verification |
| `ddgs` | >=9.16, <10 | Web search |
| `readability-lxml`, `lxml-html-clean` | — | Web content extraction |
| `qrcode[pil]`, `pillow` | — | WA pairing QR |
| `tiktoken`, `jinja2`, `pyyaml`, `jsonschema`, `json-repair`, `regex`, `rapidfuzz`, `dulwich` | — | Tokens, templates, schemas, git |
| `pypdf`, `python-docx`, `openpyxl`, `python-pptx`, `defusedxml` | — | Document reading (PDF/Word/Excel/PowerPoint) |
| `oauth-cli-kit` | >=0.1.6, <1.0 | CLI OAuth flow |
| `filelock`, `watchfiles`, `tzdata`, `tzlocal` | — | Locks, file watching, time zones |
| `setproctitle` | — | Process name (non-Windows) |

Optional extras: `azure` (azure-identity), `bedrock` (boto3/botocore), `langfuse`, `olostep`, `dev` (pytest, ruff, basedpyright, coverage, etc.). Channel dependencies (e.g. `neonize` for WA, `python-telegram-bot` for Telegram) are **not** in the main `pyproject.toml` — installed separately per channel via `scripts/install_channel_dependencies.py`.

Build: `hatchling` + `hatch_build.py` (custom hook). Packaged: `nanobot/**/*.py`, Markdown templates, built-in skills, MCP presets, `nanobot/web/dist/**/*` (WebUI build output).

## 12. Other notes

- **Internal documentation is very good.** `docs/architecture.md` maps each behaviour to its source file; `docs/memory.md` explains Dream + `/dream-log` + `/dream-restore`. This sped up Phase 0 work.
- **Tests:** `tests/` + tests in each channel; `pytest` with `asyncio_mode = auto`; 75% coverage gate.
- **Self-update:** nanobot has its own update system (`nanobot/update*.py`, `release_feed.py`, `release_trust.py`) with release-approval verification — relevant when Puspa later distributes fork updates.
- **Python SDK:** `nanobot/sdk/` + `docs/python-sdk.md` — integration from Python code is possible without the CLI.
- **Nothing needed installing/running in this phase.** All findings above come from reading the cloned code, as tasked.
