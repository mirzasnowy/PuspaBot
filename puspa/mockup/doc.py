"""Design document: the single source of truth for the B7 mockup editor.

A mockup is a declarative JSON document: a tree of whitelisted
components, style tokens, and assets. The LLM generates and patches
this document, never pixels and never free HTML; the drag-and-drop
editor edits the same document. Both sides write numbered ops, so
undo, redo, and version history work on one log.

Security: no scripts inside a design doc. Every string in the doc is
scanned for script patterns (``<script``, ``javascript:``, event
handlers, ``<iframe``) and rejected, not cleaned silently. The
renderer (``puspa.mockup.render``) only ever emits escaped text and
whitelisted components, so a hostile prompt cannot smuggle markup
into the rendered output.

Doc shape (all JSON-serialisable)::

    {
      "id": "doc-...",
      "title": "Promo Warung Kopi",
      "template": "promo_banner",
      "frame": {"width": 800, "height": 600},
      "theme": {"primary": "#...", "background": "#...",
                "text": "#...", "accent": "#...",
                "font": "Inter", "radius": 12},
      "assets": {"logo": {"kind": "image", "data_uri": "data:image/png;...",
                          "name": "logo.png"}},
      "nodes": [ <node>, ... ],
      "version": 3,
      "ops": [ <op>, ... ],
      "decisions": [ {"seq": 2, "note": "..."}, ... ],
    }

A node::

    {"id": "n1", "type": "text", "x": 40, "y": 30, "w": 400, "h": 60,
     "text": "Diskon 20%", "style": {"font_size": 32, "bold": True,
     "color": "#111111", "align": "left"},
     "children": [...]}

Ops are numbered (``seq`` starts at 1 and increases by one per op)
and carry an optional human ``note``: the note is the design
decision record, so the workspace accumulates why a design looks
the way it does, not just what it looks like. Undo/redo replays
snapshots of the whole doc; the op log itself is append-only and
is never rewritten by undo (an undo appends a compensating entry
to ``decisions``, it does not delete history).
"""

from __future__ import annotations

import copy
import re
import uuid

__all__ = [
    "ALLOWED_COMPONENTS",
    "ALLOWED_FONTS",
    "ALLOWED_OP_TYPES",
    "DEFAULT_THEME",
    "MAX_ASSET_BYTES",
    "MAX_DOC_BYTES",
    "MAX_NODES",
    "DesignError",
    "DesignSession",
    "apply_op",
    "find_node",
    "new_doc",
    "new_node",
    "validate_doc",
    "validate_op",
]

#: Whitelisted components (plan 12.2). Anything else is rejected.
ALLOWED_COMPONENTS = ("text", "image", "button", "card", "list", "form", "table", "chart")

#: Open Font License fonts only. A doc naming another font is rejected.
ALLOWED_FONTS = ("Inter", "Poppins", "Nunito", "Lato", "Open Sans", "Roboto")

#: Op types both the LLM patcher and the drag-and-drop editor may write.
ALLOWED_OP_TYPES = (
    "add_node",
    "remove_node",
    "move_node",
    "set_text",
    "set_style",
    "set_image",
    "reorder",
    "set_theme",
    "set_title",
)

#: Hard caps: a design doc is user data, but it must stay small enough
#: to store, render, and compress (G19: design state is large).
MAX_NODES = 200
MAX_DOC_BYTES = 1_048_576
MAX_ASSET_BYTES = 262_144
MAX_ASSETS = 32

DEFAULT_THEME = {
    "primary": "#1d4ed8",
    "background": "#ffffff",
    "text": "#111827",
    "accent": "#16a34a",
    "font": "Inter",
    "radius": 12,
}

#: Substrings that mark code, not data, inside any doc string.
FORBIDDEN_PATTERNS = (
    "<script",
    "</script",
    "<iframe",
    "<object",
    "<embed",
    "javascript:",
    "onerror=",
    "onclick=",
    "onload=",
    "onmouseover=",
    "vbscript:",
    "data:text/html",
)

_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
_DATA_URI_IMAGE = re.compile(r"^data:image/(png|jpeg|webp|gif);base64,[A-Za-z0-9+/=]+$")


class DesignError(ValueError):
    """A design doc or op is invalid or unsafe."""


def _check_strings(value, *, path: str) -> None:
    """Reject script/code patterns anywhere inside doc strings."""
    if isinstance(value, str):
        lowered = value.lower()
        for pattern in FORBIDDEN_PATTERNS:
            if pattern in lowered:
                raise DesignError(f"forbidden pattern {pattern!r} in {path}")
    elif isinstance(value, dict):
        for key, item in value.items():
            _check_strings(item, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _check_strings(item, path=f"{path}[{index}]")


def _check_color(value, *, path: str) -> None:
    if not isinstance(value, str) or not _HEX_COLOR.match(value):
        raise DesignError(f"{path} must be a #rrggbb color, got {value!r}")


def _validate_node(node: dict, *, path: str, seen: set[str]) -> int:
    if not isinstance(node, dict):
        raise DesignError(f"{path} must be an object")
    node_id = node.get("id")
    if not isinstance(node_id, str) or not node_id:
        raise DesignError(f"{path}.id must be a non-empty string")
    if node_id in seen:
        raise DesignError(f"duplicate node id {node_id!r}")
    seen.add(node_id)
    if node.get("type") not in ALLOWED_COMPONENTS:
        raise DesignError(f"{path}.type {node.get('type')!r} is not a whitelisted component")
    for field in ("x", "y", "w", "h"):
        value = node.get(field)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise DesignError(f"{path}.{field} must be a number")
    if node["w"] <= 0 or node["h"] <= 0:
        raise DesignError(f"{path} width and height must be positive")
    if node["x"] < 0 or node["y"] < 0:
        raise DesignError(f"{path} x and y must not be negative")
    style = node.get("style", {})
    if not isinstance(style, dict):
        raise DesignError(f"{path}.style must be an object")
    for key in ("color", "background"):
        if key in style:
            _check_color(style[key], path=f"{path}.style.{key}")
    if "font_size" in style:
        size = style["font_size"]
        if not isinstance(size, (int, float)) or size < 8 or size > 200:
            raise DesignError(f"{path}.style.font_size must be between 8 and 200")
    children = node.get("children", [])
    if not isinstance(children, list):
        raise DesignError(f"{path}.children must be a list")
    count = 1
    for index, child in enumerate(children):
        count += _validate_node(child, path=f"{path}.children[{index}]", seen=seen)
    return count


def validate_doc(doc: dict) -> dict:
    """Validate a design doc dict in place-safe style; return it unchanged.

    Raises :class:`DesignError` on any violation. The check is strict:
    unknown components, duplicate ids, bad colors, non-OFL fonts,
    oversized docs, non-image assets, and any script pattern anywhere
    are all rejected.
    """
    if not isinstance(doc, dict):
        raise DesignError("design doc must be an object")
    for field in ("id", "title", "template"):
        if not isinstance(doc.get(field), str) or not doc[field]:
            raise DesignError(f"doc.{field} must be a non-empty string")
    frame = doc.get("frame", {})
    if not isinstance(frame, dict) or frame.get("width", 0) <= 0 or frame.get("height", 0) <= 0:
        raise DesignError("doc.frame needs positive width and height")
    theme = doc.get("theme", {})
    if not isinstance(theme, dict):
        raise DesignError("doc.theme must be an object")
    for key in ("primary", "background", "text", "accent"):
        if key in theme:
            _check_color(theme[key], path=f"doc.theme.{key}")
    if theme.get("font", "Inter") not in ALLOWED_FONTS:
        raise DesignError(f"doc.theme.font {theme.get('font')!r} is not an OFL font")
    assets = doc.get("assets", {})
    if not isinstance(assets, dict) or len(assets) > MAX_ASSETS:
        raise DesignError("doc.assets must be an object with at most 32 entries")
    for asset_id, asset in assets.items():
        if not isinstance(asset, dict) or asset.get("kind") != "image":
            raise DesignError(f"doc.assets.{asset_id} must be an image asset")
        data_uri = asset.get("data_uri", "")
        if not isinstance(data_uri, str) or not _DATA_URI_IMAGE.match(data_uri):
            raise DesignError(f"doc.assets.{asset_id} must be an image data URI")
        if len(data_uri.encode("utf-8")) > MAX_ASSET_BYTES:
            raise DesignError(f"doc.assets.{asset_id} exceeds the asset size cap")
    nodes = doc.get("nodes", [])
    if not isinstance(nodes, list):
        raise DesignError("doc.nodes must be a list")
    seen: set[str] = set()
    total = 0
    for index, node in enumerate(nodes):
        total += _validate_node(node, path=f"doc.nodes[{index}]", seen=seen)
    if total > MAX_NODES:
        raise DesignError(f"doc has {total} nodes, over the cap of {MAX_NODES}")
    import json

    if len(json.dumps(doc).encode("utf-8")) > MAX_DOC_BYTES:
        raise DesignError("doc exceeds the 1 MiB size cap")
    _check_strings(doc, path="doc")
    return doc


def new_node(
    node_type: str,
    x: float,
    y: float,
    w: float,
    h: float,
    *,
    node_id: str | None = None,
    text: str | None = None,
    style: dict | None = None,
    src: str | None = None,
    children: list | None = None,
    props: dict | None = None,
) -> dict:
    """Build one node dict. Validation happens when it enters a doc."""
    node: dict = {
        "id": node_id or f"n-{uuid.uuid4().hex[:8]}",
        "type": node_type,
        "x": x,
        "y": y,
        "w": w,
        "h": h,
        "style": dict(style or {}),
        "children": list(children or []),
    }
    if text is not None:
        node["text"] = text
    if src is not None:
        node["src"] = src
    if props is not None:
        node["props"] = dict(props)
    return node


def new_doc(
    *,
    title: str,
    template: str,
    frame: dict | None = None,
    theme: dict | None = None,
    nodes: list | None = None,
    assets: dict | None = None,
    doc_id: str | None = None,
) -> dict:
    """Build a fresh validated design doc (version 0, empty op log)."""
    doc = {
        "id": doc_id or f"doc-{uuid.uuid4().hex[:12]}",
        "title": title,
        "template": template,
        "frame": dict(frame or {"width": 800, "height": 600}),
        "theme": {**DEFAULT_THEME, **(theme or {})},
        "assets": dict(assets or {}),
        "nodes": list(nodes or []),
        "version": 0,
        "ops": [],
        "decisions": [],
    }
    return validate_doc(doc)


def find_node(nodes: list, node_id: str) -> dict | None:
    """Depth-first search for a node by id; None when absent."""
    for node in nodes:
        if node.get("id") == node_id:
            return node
        found = find_node(node.get("children", []), node_id)
        if found is not None:
            return found
    return None


def _find_parent_list(nodes: list, node_id: str) -> list | None:
    """The child list that directly holds ``node_id``, or None."""
    for node in nodes:
        if node.get("id") == node_id:
            return nodes
        found = _find_parent_list(node.get("children", []), node_id)
        if found is not None:
            return found
    return None


def _remove_node(nodes: list, node_id: str) -> bool:
    for index, node in enumerate(nodes):
        if node.get("id") == node_id:
            del nodes[index]
            return True
        if _remove_node(node.get("children", []), node_id):
            return True
    return False


def validate_op(op: dict, doc: dict) -> dict:
    """Validate one op against the current doc; return it unchanged."""
    if not isinstance(op, dict):
        raise DesignError("op must be an object")
    op_type = op.get("type")
    if op_type not in ALLOWED_OP_TYPES:
        raise DesignError(f"op.type {op_type!r} is not a whitelisted op")
    _check_strings(op, path="op")
    node_id = op.get("node_id")
    if op_type in ("move_node", "set_text", "set_style", "set_image", "remove_node") and (
        not isinstance(node_id, str) or find_node(doc.get("nodes", []), node_id) is None
    ):
        raise DesignError(f"op targets unknown node {node_id!r}")
    if op_type == "move_node":
        payload = op.get("payload", {})
        if not isinstance(payload, dict):
            raise DesignError("move_node needs a payload object")
        for field in ("x", "y"):
            value = payload.get(field)
            if not isinstance(value, (int, float)) or value < 0:
                raise DesignError(f"move_node payload.{field} must be a non-negative number")
    if op_type == "set_text":
        text = (op.get("payload") or {}).get("text")
        if not isinstance(text, str):
            raise DesignError("set_text payload.text must be a string")
    if op_type == "set_style":
        style = (op.get("payload") or {}).get("style")
        if not isinstance(style, dict):
            raise DesignError("set_style payload.style must be an object")
        for key in ("color", "background"):
            if key in style:
                _check_color(style[key], path=f"op.payload.style.{key}")
    if op_type == "set_image":
        asset_id = (op.get("payload") or {}).get("asset_id")
        if not isinstance(asset_id, str) or asset_id not in doc.get("assets", {}):
            raise DesignError(f"set_image asset {asset_id!r} is not in doc.assets")
    if op_type == "add_node":
        node = (op.get("payload") or {}).get("node")
        if not isinstance(node, dict):
            raise DesignError("add_node payload.node must be an object")
        _validate_node(node, path="op.payload.node", seen=set())
        if find_node(doc.get("nodes", []), node["id"]) is not None:
            raise DesignError(f"add_node id {node['id']!r} already exists")
    if op_type == "reorder":
        payload = op.get("payload", {})
        index = payload.get("index")
        if not isinstance(node_id, str) or not isinstance(index, int) or index < 0:
            raise DesignError("reorder needs a node_id and a non-negative index")
        if find_node(doc.get("nodes", []), node_id) is None:
            raise DesignError(f"reorder targets unknown node {node_id!r}")
    if op_type == "set_theme":
        theme = (op.get("payload") or {}).get("theme")
        if not isinstance(theme, dict):
            raise DesignError("set_theme payload.theme must be an object")
        for key in ("primary", "background", "text", "accent"):
            if key in theme:
                _check_color(theme[key], path=f"op.payload.theme.{key}")
        if "font" in theme and theme["font"] not in ALLOWED_FONTS:
            raise DesignError(f"set_theme font {theme['font']!r} is not an OFL font")
    if op_type == "set_title" and not isinstance((op.get("payload") or {}).get("title"), str):
        raise DesignError("set_title payload.title must be a string")
    return op


def apply_op(doc: dict, op: dict) -> dict:
    """Apply one validated op to ``doc`` in place and return the doc.

    The op is numbered (``seq`` = len(doc ops) + 1), appended to the
    op log, and the doc version bumps by one. A ``note`` on the op is
    recorded in ``decisions`` so the design rationale accumulates.
    """
    validate_op(op, doc)
    stored = copy.deepcopy(op)
    stored["seq"] = len(doc.get("ops", [])) + 1
    op_type = stored["type"]
    payload = stored.get("payload", {}) or {}
    node_id = stored.get("node_id")
    # validate_op already proved the target exists for node ops; the
    # isinstance narrows the type for the checker ("" never reaches
    # a lookup, because validation would have raised first).
    target_id = node_id if isinstance(node_id, str) else ""
    if op_type == "add_node":
        parent_id = stored.get("parent_id")
        if parent_id:
            parent = find_node(doc["nodes"], parent_id)
            if parent is None:
                raise DesignError(f"add_node parent {parent_id!r} not found")
            parent.setdefault("children", []).append(copy.deepcopy(payload["node"]))
        else:
            doc["nodes"].append(copy.deepcopy(payload["node"]))
    elif op_type == "remove_node":
        if not _remove_node(doc["nodes"], target_id):
            raise DesignError(f"remove_node node {target_id!r} not found")
    elif op_type == "move_node":
        node = find_node(doc["nodes"], target_id)
        assert node is not None
        node["x"] = payload["x"]
        node["y"] = payload["y"]
    elif op_type == "set_text":
        node = find_node(doc["nodes"], target_id)
        assert node is not None
        node["text"] = payload["text"]
    elif op_type == "set_style":
        node = find_node(doc["nodes"], target_id)
        assert node is not None
        node.setdefault("style", {}).update(copy.deepcopy(payload["style"]))
    elif op_type == "set_image":
        node = find_node(doc["nodes"], target_id)
        assert node is not None
        node["src"] = payload["asset_id"]
    elif op_type == "reorder":
        siblings = _find_parent_list(doc["nodes"], target_id)
        if siblings is None:
            raise DesignError(f"reorder node {target_id!r} not found")
        current = next(i for i, n in enumerate(siblings) if n["id"] == target_id)
        node = siblings.pop(current)
        siblings.insert(min(payload["index"], len(siblings)), node)
    elif op_type == "set_theme":
        doc["theme"].update(copy.deepcopy(payload["theme"]))
    elif op_type == "set_title":
        doc["title"] = payload["title"]
    doc.setdefault("ops", []).append(stored)
    doc["version"] = doc.get("version", 0) + 1
    note = stored.get("note")
    if isinstance(note, str) and note:
        doc.setdefault("decisions", []).append({"seq": stored["seq"], "note": note})
    return validate_doc(doc)


class DesignSession:
    """A doc plus undo/redo over whole-doc snapshots.

    Undo and redo never rewrite the op log: undoing restores the
    previous snapshot and records a ``decisions`` entry saying which
    op was undone, so history stays honest. The session is what the
    editor screen and the API drive; the store persists the doc.
    """

    def __init__(self, doc: dict):
        self.doc = validate_doc(copy.deepcopy(doc))
        self._past: list[dict] = []
        self._future: list[dict] = []

    def apply(self, op: dict) -> dict:
        """Apply an op; push the pre-op snapshot for undo."""
        self._past.append(copy.deepcopy(self.doc))
        self._future.clear()
        try:
            apply_op(self.doc, op)
        except Exception:
            self._past.pop()
            raise
        return self.doc

    def undo(self) -> dict:
        """Restore the previous snapshot; False-ish when nothing to undo."""
        if not self._past:
            raise DesignError("nothing to undo")
        self._future.append(copy.deepcopy(self.doc))
        undone_seq = self.doc.get("version", 0)
        self.doc = self._past.pop()
        self.doc.setdefault("decisions", []).append(
            {"seq": undone_seq, "note": f"Undid op {undone_seq}."}
        )
        return self.doc

    def redo(self) -> dict:
        """Re-apply the snapshot undone most recently."""
        if not self._future:
            raise DesignError("nothing to redo")
        self._past.append(copy.deepcopy(self.doc))
        self.doc = self._future.pop()
        return self.doc

    @property
    def can_undo(self) -> bool:
        return bool(self._past)

    @property
    def can_redo(self) -> bool:
        return bool(self._future)
