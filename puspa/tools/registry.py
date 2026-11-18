"""Dependency-free tool registry for Puspa (G23).

This module has no Puspa or nanobot imports on purpose: it is the
bottom layer every other tool layer builds on. A tool file registers
itself at import time by calling :func:`register` (or using it as a
decorator); :func:`discover` imports the modules of a tool package so
those import-time registrations happen without a hand-maintained
import list.

The registry owns four jobs:

1. Schema collection (``schemas()``) — the function schemas the
   model sees, in OpenAI function-calling shape.
2. Dispatch (``handle_function_call()`` / ``handle_function_call_async()``)
   — argument validation, availability check, handler call, and
   error wrapping. Dispatch never raises for a bad call: unknown
   tools, missing arguments, unavailable tools, and handler
   exceptions all come back as a JSON string with
   ``{"status": "error" | "unavailable", ...}``.
3. Availability (``is_available()``) — a tool may carry a ``check``
   callable ``check(profile_id) -> bool``; a tool without one is
   always available. Cached TTL checks live in
   :mod:`puspa.tools.check`, which can be plugged in here as the
   ``check`` callable.
4. Multimodal envelope — every handler returns a JSON string,
   except a tool flagged ``multimodal=True`` whose handler returns
   pixel data; that comes back as a ``{"kind": "multimodal", ...}``
   envelope dict instead of a string.

Registration vs exposure are separate concerns: registering a tool
here only makes it *known*. Whether the model ever sees it is
decided by toolsets (:mod:`puspa.tools.toolsets`) and the connector
allowlist (:mod:`puspa.connectors.allowlist`).
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import pkgutil
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "MultimodalContent",
    "ToolDefinition",
    "ToolRegistry",
    "default_registry",
    "discover",
    "handle_function_call",
    "handle_function_call_async",
    "is_available",
    "register",
    "schemas",
]

#: A handler returns one of these: a JSON string, a plain dict (JSON
#: encoded by the registry), or -- for multimodal tools -- a
#: :class:`MultimodalContent` (or a dict with ``mime_type`` + ``data``).
HandlerResult = Any

#: The status strings dispatch returns inside its JSON envelope.
STATUS_OK = "ok"
STATUS_ERROR = "error"
STATUS_UNAVAILABLE = "unavailable"


@dataclass
class MultimodalContent:
    """Pixel (or other non-text) content a tool hands to the model.

    ``data`` is base64-encoded bytes; ``mime_type`` says what they
    are (``"image/png"``, ...). ``text`` is an optional caption the
    model reads alongside the pixels.
    """

    mime_type: str
    data: str
    text: str = ""


@dataclass
class ToolDefinition:
    """One registered tool: schema, handler, and availability."""

    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=dict)
    handler: Callable[..., Any] | None = None
    read_only: bool = False
    #: Optional availability predicate ``check(profile_id) -> bool``.
    #: None means the tool is always available. Use
    #: :mod:`puspa.tools.check` for a TTL-cached credential check.
    check: Callable[[str | None], bool] | None = None
    #: True when the handler returns pixel data instead of JSON text.
    multimodal: bool = False
    #: Credential provider this tool needs (``"openai"``, ...), if any.
    #: Used by the check layer to pick the right credential scope.
    provider: str | None = None
    #: Connector service slug, for allowlist filtering in toolsets.
    service: str | None = None
    #: Module that registered the tool (filled in by ``register``).
    source: str = ""

    def to_schema(self) -> dict[str, Any]:
        """OpenAI-style function schema for this tool."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters
                or {
                    "type": "object",
                    "properties": {},
                },
            },
        }

    def required_params(self) -> list[str]:
        """Parameter names the schema marks as required."""
        if not isinstance(self.parameters, dict):
            return []
        required = self.parameters.get("required") or []
        return [str(item) for item in required]


def _error_payload(tool: str, message: str, *, status: str = STATUS_ERROR) -> str:
    return json.dumps({"status": status, "tool": tool, "error": message})


def _ok_payload(tool: str, result: Any) -> str:
    if isinstance(result, str):
        try:
            json.loads(result)
        except (ValueError, TypeError):
            return json.dumps({"status": STATUS_OK, "tool": tool, "result": result})
        # A handler returns a JSON string by contract: pass it
        # through unchanged -- the handler owns its envelope.
        return result
    if isinstance(result, dict):
        payload = {"status": STATUS_OK, "tool": tool}
        payload.update(result)
        return json.dumps(payload)
    return json.dumps({"status": STATUS_OK, "tool": tool, "result": result})


def _multimodal_envelope(tool: str, content: MultimodalContent) -> dict[str, Any]:
    return {
        "kind": "multimodal",
        "tool": tool,
        "content": [
            {
                "type": "image",
                "mime_type": content.mime_type,
                "data": content.data,
            }
        ],
        "text": content.text,
    }


def _coerce_multimodal(value: Any) -> MultimodalContent | None:
    """Turn a handler return value into multimodal content, or None."""
    if isinstance(value, MultimodalContent):
        return value
    if isinstance(value, dict) and "mime_type" in value and "data" in value:
        return MultimodalContent(
            mime_type=str(value["mime_type"]),
            data=str(value["data"]),
            text=str(value.get("text", "") or ""),
        )
    return None


class ToolRegistry:
    """A dependency-free collection of tool definitions + dispatch."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    # -- registration ---------------------------------------------------

    def register(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any] | None = None,
        handler: Callable[..., Any] | None = None,
        *,
        read_only: bool = False,
        check: Callable[[str | None], bool] | None = None,
        multimodal: bool = False,
        provider: str | None = None,
        service: str | None = None,
        source: str = "",
    ) -> ToolDefinition:
        """Register one tool. Raises ``ValueError`` on a duplicate name."""
        if not name or not isinstance(name, str):
            raise ValueError("tool name must be a non-empty string")
        if name in self._tools:
            raise ValueError(
                f"tool {name!r} is already registered "
                f"(first registered by {self._tools[name].source!r})"
            )
        if handler is not None and not callable(handler):
            raise ValueError(f"tool {name!r} handler is not callable")
        definition = ToolDefinition(
            name=name,
            description=description,
            parameters=dict(parameters or {}),
            handler=handler,
            read_only=read_only,
            check=check,
            multimodal=multimodal,
            provider=provider,
            service=service,
            source=source,
        )
        self._tools[name] = definition
        return definition

    def register_definition(self, definition: ToolDefinition) -> ToolDefinition:
        """Register an already-built :class:`ToolDefinition`."""
        if definition.name in self._tools:
            raise ValueError(f"tool {definition.name!r} is already registered")
        self._tools[definition.name] = definition
        return definition

    def unregister(self, name: str) -> bool:
        """Remove a tool. Returns True when something was removed."""
        return self._tools.pop(name, None) is not None

    def get(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)

    # -- schemas --------------------------------------------------------

    def schemas(self) -> list[dict[str, Any]]:
        """Function schemas for every registered tool, by name."""
        return [self._tools[name].to_schema() for name in self.names()]

    # -- availability ---------------------------------------------------

    def is_available(self, name: str, profile_id: str | None = None) -> bool:
        """True when the tool exists and its check (if any) passes."""
        definition = self._tools.get(name)
        if definition is None:
            return False
        if definition.check is None:
            return True
        try:
            return bool(definition.check(profile_id))
        except Exception:
            return False

    # -- dispatch -------------------------------------------------------

    def _prepare(
        self, name: str, arguments: Any, profile_id: str | None
    ) -> tuple[ToolDefinition | None, dict[str, Any], str | None]:
        """Resolve + validate a call. Returns (definition, args, error)."""
        definition = self._tools.get(name)
        if definition is None:
            return None, {}, _error_payload(name, f"unknown tool {name!r}")
        if definition.handler is None:
            return None, {}, _error_payload(name, f"tool {name!r} has no handler")
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments) if arguments.strip() else {}
            except ValueError:
                return None, {}, _error_payload(name, "arguments are not valid JSON")
        if not isinstance(arguments, dict):
            return None, {}, _error_payload(name, "arguments must be a JSON object")
        if not self.is_available(name, profile_id):
            return (
                None,
                {},
                _error_payload(
                    name,
                    f"tool {name!r} is not available for this profile",
                    status=STATUS_UNAVAILABLE,
                ),
            )
        missing = [param for param in definition.required_params() if param not in arguments]
        if missing:
            return (
                None,
                {},
                _error_payload(name, f"missing required arguments: {', '.join(missing)}"),
            )
        return definition, dict(arguments), None

    def handle_function_call(
        self,
        name: str,
        arguments: Any = None,
        *,
        profile_id: str | None = None,
    ) -> str | dict[str, Any]:
        """Dispatch one tool call synchronously. Returns a JSON string.

        Async handlers are driven with ``asyncio.run`` when no event
        loop is running; inside a running loop the caller must use
        :meth:`handle_function_call_async` instead (the error payload
        says so). Multimodal tools return an envelope dict, not a
        string.
        """
        definition, args, error = self._prepare(name, arguments or {}, profile_id)
        if error is not None:
            return error
        assert definition is not None and definition.handler is not None
        try:
            result = definition.handler(**args)
        except Exception as exc:
            return _error_payload(name, f"handler raised {type(exc).__name__}: {exc}")
        if inspect.isawaitable(result):
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                pass
            else:
                result.close() if hasattr(result, "close") else None
                return _error_payload(
                    name,
                    "async handler called from a running event loop; "
                    "use handle_function_call_async instead",
                )
            try:
                # asyncio.run hanya menerima coroutine, sementara
                # handler boleh mengembalikan awaitable apa pun;
                # loop baru + run_until_complete menerima keduanya.
                loop = asyncio.new_event_loop()
                try:
                    result = loop.run_until_complete(result)
                finally:
                    loop.close()
            except Exception as exc:
                return _error_payload(name, f"handler raised {type(exc).__name__}: {exc}")
        return self._finish(name, definition, result)

    async def handle_function_call_async(
        self,
        name: str,
        arguments: Any = None,
        *,
        profile_id: str | None = None,
    ) -> str | dict[str, Any]:
        """Async version of :meth:`handle_function_call`."""
        definition, args, error = self._prepare(name, arguments or {}, profile_id)
        if error is not None:
            return error
        assert definition is not None and definition.handler is not None
        try:
            result = definition.handler(**args)
            if inspect.isawaitable(result):
                result = await result
        except Exception as exc:
            return _error_payload(name, f"handler raised {type(exc).__name__}: {exc}")
        return self._finish(name, definition, result)

    def _finish(self, name: str, definition: ToolDefinition, result: Any) -> str | dict[str, Any]:
        if definition.multimodal:
            content = _coerce_multimodal(result)
            if content is not None:
                return _multimodal_envelope(name, content)
        return _ok_payload(name, result)


#: The process-wide default registry. Tool modules register here.
_DEFAULT_REGISTRY = ToolRegistry()


def default_registry() -> ToolRegistry:
    """The process-wide default registry."""
    return _DEFAULT_REGISTRY


def register(
    name: str | None = None,
    description: str = "",
    parameters: dict[str, Any] | None = None,
    handler: Callable[..., Any] | None = None,
    *,
    read_only: bool = False,
    check: Callable[[str | None], bool] | None = None,
    multimodal: bool = False,
    provider: str | None = None,
    service: str | None = None,
    registry: ToolRegistry | None = None,
):
    """Register a tool on the default registry (or the given one).

    Two forms::

        register("echo", "Echo text.", {...}, echo_handler)

        @register("echo", "Echo text.", {...})
        def echo_handler(text): ...

    The decorator form records the caller's module as ``source`` so
    a duplicate registration error can say where the first one came
    from.
    """
    target = registry if registry is not None else _DEFAULT_REGISTRY
    source = ""
    if handler is None:
        # Decorator form: capture the caller's module for the source.
        frame = inspect.currentframe()
        try:
            caller = frame.f_back if frame is not None else None
            if caller is not None:
                source = caller.f_globals.get("__name__", "")
        finally:
            del frame

    def _do_register(fn: Callable[..., Any]) -> Callable[..., Any]:
        target.register(
            str(name),
            description,
            parameters,
            fn,
            read_only=read_only,
            check=check,
            multimodal=multimodal,
            provider=provider,
            service=service,
            source=source or fn.__module__,
        )
        return fn

    if handler is not None:
        if name is None:
            raise ValueError("register() needs a tool name")
        target.register(
            name,
            description,
            parameters,
            handler,
            read_only=read_only,
            check=check,
            multimodal=multimodal,
            provider=provider,
            service=service,
            source=source or handler.__module__,
        )
        return target.get(name)
    if name is None:
        raise ValueError("register() needs a tool name")
    return _do_register


def discover(*packages: str, registry: ToolRegistry | None = None) -> ToolRegistry:
    """Import every module of the given tool packages.

    Importing a tool module runs its import-time ``register()``
    calls, so after this returns the registry holds every tool those
    packages define -- with no hand-maintained import list. A plain
    module name (no ``__path__``) is imported as-is. Private modules
    (leading ``_``) are skipped.

    Import-time registration targets the default registry; when
    ``registry`` is a different registry, the definitions collected
    on the default are copied over, so discovery works the same for
    any registry instance.
    """
    target = registry if registry is not None else _DEFAULT_REGISTRY
    for package_name in packages:
        package = importlib.import_module(package_name)
        path = getattr(package, "__path__", None)
        if path is None:
            continue
        for module in pkgutil.iter_modules(path):
            if module.name.startswith("_"):
                continue
            importlib.import_module(f"{package_name}.{module.name}")
    if target is not _DEFAULT_REGISTRY:
        for name in _DEFAULT_REGISTRY.names():
            if name not in target:
                definition = _DEFAULT_REGISTRY.get(name)
                if definition is not None:
                    target.register_definition(definition)
    return target


def schemas(registry: ToolRegistry | None = None) -> list[dict[str, Any]]:
    """Function schemas for every tool on the registry."""
    return (registry if registry is not None else _DEFAULT_REGISTRY).schemas()


def is_available(
    name: str, profile_id: str | None = None, registry: ToolRegistry | None = None
) -> bool:
    """True when the tool exists and its check (if any) passes."""
    return (registry if registry is not None else _DEFAULT_REGISTRY).is_available(name, profile_id)


def handle_function_call(
    name: str,
    arguments: Any = None,
    *,
    profile_id: str | None = None,
    registry: ToolRegistry | None = None,
) -> str | dict[str, Any]:
    """Dispatch one tool call on the default registry (sync)."""
    return (registry if registry is not None else _DEFAULT_REGISTRY).handle_function_call(
        name, arguments, profile_id=profile_id
    )


async def handle_function_call_async(
    name: str,
    arguments: Any = None,
    *,
    profile_id: str | None = None,
    registry: ToolRegistry | None = None,
) -> str | dict[str, Any]:
    """Dispatch one tool call on the default registry (async)."""
    return await (
        registry if registry is not None else _DEFAULT_REGISTRY
    ).handle_function_call_async(name, arguments, profile_id=profile_id)
