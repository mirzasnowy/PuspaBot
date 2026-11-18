"""Layered connectors (G10).

Four layers, one allowlist, one account choice:

- :mod:`puspa.connectors.native`: curated native connector
  directory (definitions + operation contracts).
- :mod:`puspa.connectors.wedges`: Indonesian wedge definitions
  (Tokopedia/Shopee/Blibli, PLN/PDAM/pulsa/data) plus fake
  providers. Live execution is OFF by default (ToS decision 15).
- :mod:`puspa.connectors.accounts`: per-user account sync and
  per-module account choice.
- :mod:`puspa.connectors.allowlist`: the advertised-tool allowlist
  (flagship): the engine only sees allowed tools.
- :mod:`puspa.connectors.aggregator`: broker interface + fake
  broker + project sessions (no real broker yet).
- :mod:`puspa.connectors.mcp_registry`: MCP registry + OAuth
  sign-in, reusing the G27 definition format.
- :mod:`puspa.connectors.token_apps`: paste-token apps, checked
  live, stored sealed, served as MCP servers.

Older modules (``google``, ``grants``) keep working unchanged.
"""

from .accounts import ConnectorAccount, ConnectorAccountStore, ModuleAccountSelection
from .aggregator import Broker, FakeBroker, ProjectSession
from .allowlist import ToolAllowlist
from .mcp_registry import McpRegistry, OAuthFlow
from .native import ConnectorDefinition, CuratedDirectory, Operation
from .token_apps import TokenApp
from .wedges import (
    FakeBillProvider,
    FakeMarketplaceProvider,
    LiveExecutionGate,
    build_wedge_definitions,
    curated_wedge_directory,
    live_execution_enabled,
)

__all__ = [
    "Broker",
    "ConnectorAccount",
    "ConnectorAccountStore",
    "ConnectorDefinition",
    "CuratedDirectory",
    "FakeBillProvider",
    "FakeBroker",
    "FakeMarketplaceProvider",
    "LiveExecutionGate",
    "McpRegistry",
    "ModuleAccountSelection",
    "OAuthFlow",
    "Operation",
    "ProjectSession",
    "TokenApp",
    "ToolAllowlist",
    "build_wedge_definitions",
    "curated_wedge_directory",
    "live_execution_enabled",
]
