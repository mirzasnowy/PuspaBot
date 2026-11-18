"""Pembungkus tool nanobot dengan penegakan izin Puspa (definisi 2.6).

``PuspaTool`` membungkus satu ``Tool`` nanobot mengikuti pola resmi
``_LegacyErrorPrefixTool`` (``nanobot/agent/tools/loader.py``): semua
atribut/skema diteruskan apa adanya, hanya ``execute`` yang ditambah
pemeriksaan. Kenapa di sini, bukan di hook: hook nanobot
(``AgentHook.before_execute_tool``) hanya observasi — tidak ada nilai
kembali dan tidak bisa mem-veto. Penegakan harus di jalur eksekusi.

Urutan cek di ``execute`` (gagal di langkah mana pun -> TIDAK ada eksekusi,
dikembalikan ``ToolResult.error`` — pola soft-block seperti penjaga SSRF
nanobot, jadi loop agen bisa pulih secara percakapan):

1. Token izin turn ini (ContextVar Puspa, lalu ``RequestContext``).
   Tidak ada token -> tolak (fail-closed).
2. Nama tool ada di ``payload["tools"]`` token. Di luar token -> tolak.
3. Kalau tool punya manifes skill (``skill_manifest``), cek dengan
   ``check_tool`` + ``public_key`` (signature diverifikasi — celah lama
   sudah ditutup di ``puspa/policy/skills.py``). Gagal -> tolak.
4. Kalau tool memetakan ke aksi keluar (kirim / buat_event / siapkan_bayar /
   hapus, lihat ``KIND_TOOLS`` di ``puspa.policy.gate``), jalankan lewat
   ``Gate``: minta persetujuan dulu. Tanpa gerbang -> tolak. Persetujuan
   dibuat (``request_approval``) tapi aksi TIDAK dieksekusi di sini —
   eksekusi menunggu user menyetujui (layar persetujuan PWA, fase 2);
   wrapper mengembalikan hasil "menunggu persetujuan" beserta
   ``request_id``. Kalau gerbang menolak (token tidak mengizinkan /
   batas belanja) -> tolak.
5. Eksekusi tool yang dibungkus. Hasil string dipindai pola injeksi
   (``puspa.quarantine.scan``); temuan dicatat di ``tool_selesai``,
   BUKAN ditulis ulang — pemblokiran/penulisan ulang hasil adalah
   tugas Pemeriksa + renderer (definisi 2.5, fase 2).

Audit: tiap pemanggilan mencatat ``tool_dipanggil`` dan ``tool_selesai``
lewat ``log`` (callable ``log(jenis, isi)`` seperti di ``Gate``;
``make_ledger_sink`` di ``puspa.chassis.context`` mengadaptasi Buku Kerja).
Isi log TIDAK memuat parameter tool (bisa berisi data sensitif) — hanya
nama tool, status, dan alasan penolakan / temuan injeksi.
"""

from __future__ import annotations

from typing import Any

from nanobot.agent.tools.base import Tool, ToolResult

from puspa.policy.gate import KIND_TOOLS, GateDenied
from puspa.policy.skill_signing import SkillRejected, check_tool
from puspa.quarantine.injection import scan

from .context import resolve_token_payload

__all__ = [
    "McpClientWrapper",
    "McpToolSpec",
    "McpUnavailable",
    "PuspaTool",
    "StdioMcpTransport",
    "StubMcpTransport",
    "kind_for_tool",
    "mcp_sdk_info",
]

#: tool nanobot -> kind aksi keluar (balikan KIND_TOOLS dari gate).
_TOOL_TO_KIND: dict[str, str] = {
    tool: kind for kind, tools in KIND_TOOLS.items() for tool in tools
}


def kind_for_tool(tool_name: str) -> str | None:
    """Kind aksi keluar untuk nama tool, atau None kalau bukan aksi keluar."""
    return _TOOL_TO_KIND.get(tool_name)


class PuspaTool(Tool):
    """Tool nanobot yang dieksekusi hanya bila token izin mengizinkan."""

    _plugin_discoverable = False

    def __init__(
        self,
        wrapped: Tool,
        *,
        gate=None,
        log=None,
        skill_manifest: dict | None = None,
        public_key=None,
    ) -> None:
        self._wrapped = wrapped
        self._gate = gate
        self._log = log
        self._skill_manifest = skill_manifest
        self._public_key = public_key

    # -- penerusan atribut (pola _LegacyErrorPrefixTool) --------------------
    @property
    def name(self) -> str:
        return self._wrapped.name

    @property
    def description(self) -> str:
        return self._wrapped.description

    @property
    def parameters(self) -> dict[str, Any]:
        return self._wrapped.parameters

    def runtime_context_provider(self):
        return self._wrapped.runtime_context_provider()

    @property
    def read_only(self) -> bool:
        return self._wrapped.read_only

    @property
    def exclusive(self) -> bool:
        return self._wrapped.exclusive

    @property
    def concurrency_safe(self) -> bool:
        return self._wrapped.concurrency_safe

    @property
    def config_key(self) -> str:
        return getattr(self._wrapped, "config_key", "")

    def set_context(self, ctx) -> None:
        set_context = getattr(self._wrapped, "set_context", None)
        if callable(set_context):
            set_context(ctx)

    def validate_params(self, params: dict[str, Any]) -> list[str]:
        return self._wrapped.validate_params(params)

    def to_schema(self) -> dict[str, Any]:
        return self._wrapped.to_schema()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._wrapped, name)

    # -- pencatatan ---------------------------------------------------------
    def _log_event(self, event_type: str, content: dict) -> None:
        if self._log is None:
            return
        try:
            self._log(event_type, content)
        except Exception:
            pass

    # -- eksekusi berpengawal ----------------------------------------------
    async def execute(self, **kwargs: Any) -> Any:
        name = self.name
        payload = resolve_token_payload()

        def _reject(reason: str):
            self._log_event(
                "tool_called", {"tool": name, "status": "rejected", "reason": reason}
            )
            self._log_event(
                "tool_completed", {"tool": name, "status": "rejected", "reason": reason}
            )
            return ToolResult.error(f"Ditolak Puspa: {reason}")

        if payload is None:
            return _reject("tidak ada token izin Puspa untuk turn ini")
        if name not in payload.get("tools", []):
            return _reject(f"tool {name!r} di luar token izin")
        if self._skill_manifest is not None:
            try:
                check_tool(self._skill_manifest, name, self._public_key)
            except SkillRejected as exc:
                return _reject(f"skill tidak lolos verifikasi: {exc}")

        kind = kind_for_tool(name)
        if kind is not None:
            if self._gate is None:
                return _reject(f"aksi keluar {kind!r} tanpa gerbang persetujuan")
            try:
                request = self._gate.request_approval(
                    user_id=payload["user_id"],
                    agent_id=payload["agent_id"],
                    token=payload,
                    kind=kind,
                    content=dict(kwargs),
                )
            except GateDenied as exc:
                return _reject(f"gerbang menolak aksi {kind!r}: {exc}")
            self._log_event(
                "tool_called",
                {
                    "tool": name,
                    "status": "pending_approval",
                    "request_id": request.request_id,
                },
            )
            return ToolResult.error(
                f"Menunggu persetujuan user untuk aksi {kind!r} "
                f"(request {request.request_id}); aksi belum dijalankan."
            )

        self._log_event("tool_called", {"tool": name})
        try:
            result = await self._wrapped.execute(**kwargs)
        except Exception:
            self._log_event("tool_completed", {"tool": name, "status": "error"})
            raise
        status = "error" if isinstance(result, ToolResult) and result.is_error else "ok"
        content_completed: dict[str, Any] = {"tool": name, "status": status}
        if isinstance(result, str) and result:
            findings = scan(result)
            if findings:
                content_completed["injection"] = [
                    {"pattern": t.pattern, "level": t.level} for t in findings
                ]
        self._log_event("tool_completed", content_completed)
        return result


# ---------------------------------------------------------------------------
# Typed MCP client wrapper (modelcontextprotocol/python-sdk pattern).
#
# The official MCP Python SDK (MIT, already installed in the venv as a
# nanobot dependency: ``mcp==1.30.0``) is the standard way tools are
# exposed to agents. Puspa does NOT depend on the SDK's server code
# and does NOT let an MCP tool run unguarded. The wrapper adds the
# same two gates PuspaTool already enforces, in the same order:
#
# 1. Signed-skill gate: an MCP tool is registered only if it passes
#    ``check_tool`` against a signed skill manifest (Ed25519, public
#    key required). An unsigned skill, a bad signature, or a tool
#    outside the manifest is rejected before any call is made.
# 2. Single outbound gate: a tool that maps to an outbound kind
#    (``kind_for_tool``) goes through ``Gate.request_approval`` and is
#    NOT executed here; the caller gets a pending-approval result.
#
# The wrapper is typed with pydantic (already a dependency):
# ``McpToolSpec`` validates every tool a server advertises, so a
# malformed tool description fails at registration, not at call time.
#
# Transports: ``StdioMcpTransport`` drives a real MCP server over
# stdio through the official SDK (constructed explicitly by the
# caller; nothing auto-connects). ``StubMcpTransport`` is an
# in-memory fake server implementing the same protocol shape
# (list_tools / call_tool over plain dicts, no network, no process)
# for tests and offline development.
# ---------------------------------------------------------------------------

try:
    from mcp import ClientSession as _McpClientSession
    from mcp.client.stdio import stdio_client as _stdio_client
    from mcp.types import Tool as _McpTool

    _MCP_SDK_AVAILABLE = True
except Exception:  # pragma: no cover - SDK missing in a minimal env
    _McpClientSession = None
    _stdio_client = None
    _McpTool = None
    _MCP_SDK_AVAILABLE = False

try:
    from pydantic import BaseModel as _PydanticBaseModel
    from pydantic import Field as _pydantic_field
except Exception:  # pragma: no cover - pydantic is a pinned dependency
    _PydanticBaseModel = None  # type: ignore
    _pydantic_field = None  # type: ignore


class McpUnavailable(Exception):
    """The MCP SDK (or a transport) is not available for this call."""


def mcp_sdk_info() -> dict:
    """Installed MCP SDK facts (name, version, license, availability).

    Read from the installed distribution metadata; no network call.
    """
    try:
        from importlib.metadata import metadata

        meta = metadata("mcp")
        return {
            "name": meta["Name"],
            "version": meta["Version"],
            "license": meta.get("License", "MIT"),
            "available": _MCP_SDK_AVAILABLE,
        }
    except Exception:
        return {"name": "mcp", "version": None, "license": None,
                "available": _MCP_SDK_AVAILABLE}


if _PydanticBaseModel is not None:
    class McpToolSpec(_PydanticBaseModel):
        """A typed MCP tool, validated at registration time.

        ``input_schema`` is the tool's JSON Schema for its arguments.
        ``skill_name`` records which signed skill advertised the tool,
        so the gate can check it later.
        """

        name: str
        description: str = ""
        input_schema: dict[str, Any] = _pydantic_field(default_factory=dict)
        server_name: str = ""
        skill_name: str = ""

        @classmethod
        def from_sdk_tool(cls, tool: Any, *, server_name: str = "",
                          skill_name: str = "") -> "McpToolSpec":
            """Build a spec from an SDK ``mcp.types.Tool`` (or a dict)."""
            if isinstance(tool, dict):
                data = dict(tool)
                schema = data.get("inputSchema") or data.get("input_schema") or {}
                return cls(
                    name=str(data.get("name", "")),
                    description=str(data.get("description", "") or ""),
                    input_schema=dict(schema),
                    server_name=server_name, skill_name=skill_name,
                )
            return cls(
                name=str(tool.name),
                description=str(tool.description or ""),
                input_schema=dict(tool.inputSchema or {}),
                server_name=server_name, skill_name=skill_name,
            )
else:  # pragma: no cover - pydantic is pinned, this never runs here
    class McpToolSpec:  # type: ignore
        def __init__(self, **kwargs):
            raise McpUnavailable("pydantic is required for McpToolSpec")


class StubMcpTransport:
    """In-memory fake MCP server: same list/call shape, no network.

    ``tools`` maps a tool name to ``(input_schema, handler)`` where
    ``handler(arguments)`` returns the tool result (a dict, str, or
    list of content blocks). ``list_tools`` returns ``McpToolSpec``
    objects; ``call_tool`` validates the argument keys against the
    declared schema's ``properties`` (unknown arguments are rejected)
    and then runs the handler.
    """

    def __init__(self, tools: dict[str, tuple[dict, Any]] | None = None,
                 *, server_name: str = "stub") -> None:
        self._tools: dict[str, tuple[dict, Any]] = dict(tools or {})
        self.server_name = server_name
        self.calls: list[dict] = []

    def add_tool(self, name: str, input_schema: dict, handler) -> None:
        self._tools[name] = (dict(input_schema), handler)

    async def list_tools(self) -> list[McpToolSpec]:
        return [
            McpToolSpec(name=name, description="", input_schema=schema,
                        server_name=self.server_name)
            for name, (schema, _handler) in self._tools.items()
        ]

    async def call_tool(self, name: str, arguments: dict | None = None) -> Any:
        arguments = dict(arguments or {})
        if name not in self._tools:
            raise McpUnavailable(f"stub MCP server has no tool {name!r}")
        schema, handler = self._tools[name]
        allowed = set((schema.get("properties") or {}).keys())
        unknown = set(arguments) - allowed
        if unknown:
            raise ValueError(f"arguments {sorted(unknown)} not in schema for {name!r}")
        self.calls.append({"tool": name, "arguments": arguments})
        result = handler(arguments)
        if hasattr(result, "__await__"):
            result = await result
        return result


class StdioMcpTransport:
    """A real MCP server over stdio, driven by the official SDK.

    Constructed with a command (and args); the server process starts
    only inside ``async with transport.session():``. No network is
    involved: stdio is a local pipe to a local process.
    """

    def __init__(self, command: str, args: list[str] | None = None,
                 *, server_name: str = "") -> None:
        self.command = command
        self.args = list(args or [])
        self.server_name = server_name or command

    def session(self):
        """Async context manager yielding an SDK ``ClientSession``."""
        if not _MCP_SDK_AVAILABLE:
            raise McpUnavailable("MCP SDK is not installed")
        from mcp.client.stdio import StdioServerParameters

        params = StdioServerParameters(command=self.command, args=self.args)

        class _SessionContext:
            async def __aenter__(self):
                self._stdio = _stdio_client(params)
                read, write = await self._stdio.__aenter__()
                self._session = _McpClientSession(read, write)
                await self._session.__aenter__()
                await self._session.initialize()
                return self._session

            async def __aexit__(self, *exc):
                await self._session.__aexit__(*exc)
                await self._stdio.__aexit__(*exc)

        return _SessionContext()


class McpClientWrapper:
    """Typed MCP client behind the signed-skill gate + outbound gate.

    ``transport`` is a ``StubMcpTransport`` or a ``StdioMcpTransport``
    (or any object with async ``list_tools`` / ``call_tool``). The
    wrapper never calls a tool that is not in the signed manifest,
    and never executes an outbound kind without an approval request.
    """

    def __init__(self, transport, *, gate=None, log=None,
                 skill_manifest: dict | None = None, public_key=None,
                 server_name: str = "") -> None:
        self.transport = transport
        self._gate = gate
        self._log = log
        self._skill_manifest = skill_manifest
        self._public_key = public_key
        self.server_name = server_name or getattr(transport, "server_name", "")

    def _log_event(self, event_type: str, content: dict) -> None:
        if self._log is None:
            return
        try:
            self._log(event_type, content)
        except Exception:
            pass

    def _check_skill(self, tool_name: str) -> None:
        """Signed-skill gate: raise SkillRejected for unsigned tools."""
        if self._skill_manifest is None:
            raise SkillRejected("MCP tool needs a signed skill manifest")
        check_tool(self._skill_manifest, tool_name, self._public_key)

    async def list_tools(self) -> list[McpToolSpec]:
        """List tools, keeping only those the signed skill allows."""
        if hasattr(self.transport, "session"):
            async with self.transport.session() as session:
                raw = await session.list_tools()
                tools = [McpToolSpec.from_sdk_tool(
                    tool, server_name=self.server_name) for tool in raw.tools]
        else:
            tools = await self.transport.list_tools()
        allowed = []
        for spec in tools:
            try:
                self._check_skill(spec.name)
            except SkillRejected:
                continue
            allowed.append(spec)
        return allowed

    async def call_tool(self, name: str, arguments: dict | None = None,
                        *, token_payload: dict | None = None) -> dict:
        """Call one MCP tool, gated. Returns a result dict, never raw.

        - Unsigned skill / tool outside the manifest: ``{"status":
          "rejected", "reason": ...}``, no call is made.
        - Outbound kind: an approval request is filed through the
          gate and the result is ``{"status": "pending_approval",
          "request_id": ...}``; the tool is NOT executed.
        - Otherwise the tool runs and the result is ``{"status":
          "ok", "result": ...}``; a transport error becomes
          ``{"status": "error", "reason": ...}``.
        """
        arguments = dict(arguments or {})
        try:
            self._check_skill(name)
        except SkillRejected as exc:
            self._log_event("tool_called", {"tool": name, "status": "rejected",
                                            "reason": str(exc)})
            return {"status": "rejected", "reason": f"unsigned skill: {exc}"}
        kind = kind_for_tool(name)
        if kind is not None:
            if self._gate is None:
                return {"status": "rejected",
                        "reason": f"outbound action {kind!r} without an approval gate"}
            if token_payload is None:
                return {"status": "rejected",
                        "reason": "outbound action needs a permission token payload"}
            try:
                request = self._gate.request_approval(
                    user_id=token_payload["user_id"],
                    agent_id=token_payload["agent_id"],
                    token=token_payload, kind=kind, content=dict(arguments),
                )
            except GateDenied as exc:
                return {"status": "rejected", "reason": f"gate refused: {exc}"}
            self._log_event("tool_called", {"tool": name, "status": "pending_approval",
                                            "request_id": request.request_id})
            return {"status": "pending_approval", "request_id": request.request_id}
        self._log_event("tool_called", {"tool": name})
        try:
            if hasattr(self.transport, "session"):
                async with self.transport.session() as session:
                    raw = await session.call_tool(name, arguments)
                    result = raw.model_dump() if hasattr(raw, "model_dump") else raw
            else:
                result = await self.transport.call_tool(name, arguments)
        except Exception as exc:
            self._log_event("tool_completed", {"tool": name, "status": "error"})
            return {"status": "error", "reason": str(exc)}
        self._log_event("tool_completed", {"tool": name, "status": "ok"})
        return {"status": "ok", "result": result}

    # -- sync convenience (no pytest-asyncio in this repo) ---------------
    def list_tools_sync(self) -> list[McpToolSpec]:
        import anyio
        from functools import partial

        return anyio.run(partial(self.list_tools))

    def call_tool_sync(self, name: str, arguments: dict | None = None,
                       *, token_payload: dict | None = None) -> dict:
        import anyio
        from functools import partial

        return anyio.run(partial(
            self.call_tool, name, arguments, token_payload=token_payload))
