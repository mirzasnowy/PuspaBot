# PuspaBot Plugin SDK

Build a plugin once, and it keeps working when PuspaBot upgrades.
That is the whole point of this SDK: a **versioned plugin API** —
a fixed contract between your plugin and PuspaBot — so a plugin
written against API 1.0 still loads on a PuspaBot shipping API
1.3, and a plugin that needs a newer API is refused with a clear
message instead of breaking halfway through a turn.

The published API is `PluginContext` (the `ctx` object your
`register(ctx)` receives) plus the module contract below. The
machine-readable copy of the API lives in
`puspa/plugins/plugin_api.json`; `tests/test_plugin_api.py`
checks the code and that file stay in sync. A plugin must never
import Puspa core modules directly — the boundary lint
(`python -m puspa.plugins.boundary`) rejects any import outside
the published list.

## The plugin contract

A plugin is a Python module with one entry point, `register(ctx)`:

```python
"""Hello-world plugin for PuspaBot (plugin API 1.0).

Save this file as ``hello_world.py`` in your plugin directory
(``~/.puspa/plugins/`` or ``./plugins/``) and PuspaBot loads
it on the next start.
"""

PLUGIN_ID = "hello-world"
PLUGIN_VERSION = "1.0.0"
PLUGIN_API_VERSION = "1.0"
PLUGIN_PERMISSIONS = ["tools"]


def register(ctx):
    """Register one read-only tool that greets the caller."""

    def hello_world(name="world"):
        return f"Hello, {name}! This plugin runs on PuspaBot plugin API {ctx.api_version}."

    ctx.register_tool(
        "hello_world",
        "Greet someone by name.",
        {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Who to greet."}
            },
        },
        hello_world,
        read_only=True,
    )
```

Module attributes (all optional except `register` itself):

| Attribute | Meaning |
| --- | --- |
| `PLUGIN_ID` | The plugin id. Defaults to the module name when absent. Lowercase slug. |
| `PLUGIN_VERSION` | Your plugin's own version, semver `MAJOR.MINOR.PATCH` (for example `"1.0.0"`). |
| `PLUGIN_API_VERSION` | Which plugin API you wrote against, `"MAJOR.MINOR"` (for example `"1.0"`). When absent, the plugin is treated as written against the current API. |
| `PLUGIN_PERMISSIONS` | What the plugin needs (see below). When absent, no permission check runs. |

What `ctx` lets you register (the full reference is the
`PluginContext` docstring in `puspa/plugins/context.py`):

- `ctx.register_tool(name, description, parameters, handler, read_only=False)` — a named tool the agent can call. The handler receives keyword arguments matching the JSON Schema `parameters` and returns a string (or a dict, which is JSON-encoded). Mark side-effect-free tools `read_only=True`.
- `ctx.register_hook(event, handler)` — a lifecycle hook for one of `before_run`, `after_run`, `on_error`, `before_execute_tool`, `after_execute_tool`, `finalize_content`. Hooks only observe, except `finalize_content`, whose handler may return a replacement string for the turn answer.
- `ctx.register_command(name, description, handler)` — a CLI command. The handler receives the argument list and returns an int exit code.
- `ctx.register_memory_provider(name, provider_cls, default=False)` / `ctx.register_context_engine(name, factory, default=False)` — single-select providers; only one is active at a time per kind.
- `ctx.log(message)` — a best-effort log line, kept in `ctx.shared` and shown on the load report.
- `ctx.shared` — a per-plugin dict for state shared between your own registrations. Never shared with other plugins.

## Permissions

Declare what your plugin needs. The loader checks the
declaration twice: the names must be known, and what you
actually register must be a subset of what you declared.
A plugin that registers a tool without declaring `tools` is
refused at load time.

| Permission | Covers |
| --- | --- |
| `tools` | `register_tool` |
| `hooks` | `register_hook` |
| `commands` | `register_command` |
| `memory_provider` | `register_memory_provider` |
| `context_engine` | `register_context_engine` |
| `network` | The plugin makes network requests. Declared only in API v1 (see below). |
| `filesystem` | The plugin reads or writes files outside its own directory. Declared only in API v1 (see below). |

`network` and `filesystem` are **declarations of intent in
API v1, not enforced limits**: a plugin runs in-process, so
the loader cannot block a socket or a file open yet. They are
recorded on the load record and shown to the operator, so a
plugin's network/file use is visible — but do not mistake the
declaration for a sandbox. Blocking enforcement is planned for
a later API major, and will be announced as a breaking change
under the rules below.

## The catalog manifest

To publish a plugin in the marketplace catalog, ship a
`plugin.yaml` manifest next to the code:

```yaml
id: hello-world
name: Hello World
version: 1.0.0
description: Greets the caller. The smallest possible plugin.
author: Your Name (you@example.com)
license: MIT
repository: https://github.com/you/hello-world-plugin
entry_point: hello_world.plugin
kind: general
api_version: 1
permissions:
  - tools
dependencies: []
price_credits: 0
```

Admission rules (enforced by `puspa.plugins.catalog`):

- Required fields: `id`, `name`, `version`, `description`,
  `author`, `license`, `repository`, `entry_point`, `kind`,
  `api_version`.
- `id` is a lowercase slug (3-64 chars); `version` is semver
  `MAJOR.MINOR.PATCH`; `license` is one of MIT, Apache-2.0,
  BSD-2-Clause, BSD-3-Clause, ISC.
- `repository` is an `https://github.com/...` or
  `https://gitlab.com/...` URL; `kind` is `general`,
  `memory_provider`, or `context_engine`.
- `api_version` must be **compatible** with the host (see
  below), not merely equal: `1` and `"1.0"` both pass on a
  1.0 host; `2` and `"1.3"` on a 1.0 host fail admission.
- `permissions`, when present, must be a list of known
  permission names (see the table above).

## Versioning and the compatibility promise

The plugin API has its own version, separate from the
PuspaBot release version and from your plugin's version.
The current API is **1.0** (`puspa.plugins.api_version.API_VERSION`).

| Change | API version | What happens to your plugin |
| --- | --- | --- |
| Bug fix in the API implementation | Patch (ignored: `1.0.3` is API 1.0) | Nothing. Patches never change behaviour a plugin can see. |
| New `ctx` method, new hook event, new permission | Minor (`1.0` -> `1.1`) | **Your plugin keeps working.** Minor releases only add; they never remove or change what an older plugin uses. |
| A `ctx` method removed or changed, a hook event removed, a permission's meaning changed | Major (`1.0` -> `2.0`) | **Your plugin is refused with a clear error** until you ship a release for the new major. |

The loader applies exactly this rule:

- **Same major, older or equal minor: loads.** A plugin
  written against 1.0 runs on a host shipping 1.3.
- **Different major: refused.** A 1.x plugin on a 2.x host
  (or the reverse) is refused; the error names both versions
  and tells the operator to use a plugin release written for
  the host's major, or to upgrade PuspaBot.
- **Newer minor than the host: refused.** A plugin written
  against 1.3 uses things a 1.0 host does not have yet; the
  error tells the operator to upgrade PuspaBot or use a plugin
  release written for 1.0 or older.

## Upgrade policy

- **As a plugin author:** declare `PLUGIN_API_VERSION` with
  the oldest API your plugin actually needs, and bump it only
  when you start using a newer API feature. Keep
  `PLUGIN_VERSION` (your release) separate from the API
  version. When PuspaBot ships a new API major, test your
  plugin against it and ship a new release; the old release
  keeps working for users on the old major.
- **As a PuspaBot operator:** a plugin that stops loading
  after an upgrade is never silently skipped — the loader
  records the failure per plugin (with the version error) and
  the rest of the system keeps running. Read the error: it
  says whether to upgrade PuspaBot or wait for a plugin
  release.
- **As a PuspaBot maintainer:** the API version only moves in
  the ways the table above allows, and the move is recorded
  in `puspa/plugins/api_version.py` (`API_VERSION`) and
  `puspa/plugins/plugin_api.json` (`api_version` /
  `api_version_string`) together, with the tests in
  `tests/test_plugin_sdk.py` updated to pin the new rule.
