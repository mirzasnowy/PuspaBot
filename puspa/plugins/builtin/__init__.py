"""Builtin plugins shipped with Puspa (G27).

Builtin plugins are ordinary plugins: they use only the
published ``ctx`` API, they are loaded through the same loader
as third-party plugins, and core never imports them directly
(the factory loads them by module path through the loader, and
the boundary lint forbids any other core import of this
package).
"""

from __future__ import annotations

__all__ = ["BUILTIN_PLUGINS"]

#: Module paths of the bundled plugins, in load order.
BUILTIN_PLUGINS = (
    "puspa.plugins.builtin.echo",
    "puspa.plugins.builtin.simple_context",
    "puspa.plugins.builtin.memory_inmemory",
)
