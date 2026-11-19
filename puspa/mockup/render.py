"""Deterministic rendering of a design doc to SVG and safe HTML.

Both renderers walk the same node tree in the same order, escape
every user string, and emit no scripts, no event handlers, and no
free HTML. The HTML page is meant to be served inside a sandboxed
iframe with the strict CSP from :func:`csp_header` (``sandbox`` +
``default-src 'none'`` + ``style-src 'unsafe-inline'`` for the
whitelisted inline styles the renderer itself writes — no style
ever comes from doc content unescaped).

Determinism: attribute order is fixed, numbers are formatted the
same way every run, and nodes render in doc order, so rendering
the same doc twice yields byte-identical output. That is what
makes exports reproducible and what the self-critique in
``puspa.mockup.generate`` can check against.
"""

from __future__ import annotations

import html

__all__ = [
    "csp_header",
    "render_html",
    "render_svg",
]

CSP_HEADER = (
    "default-src 'none'; style-src 'unsafe-inline'; img-src data:; "
    "font-src 'none'; sandbox allow-same-origin; base-uri 'none'; form-action 'none'"
)


def csp_header() -> str:
    """Strict CSP for serving a rendered mockup page."""
    return CSP_HEADER


def _esc(text) -> str:
    return html.escape(str(text), quote=True)


def _num(value) -> str:
    if float(value) == int(value):
        return str(int(value))
    return f"{float(value):.1f}"


def _iter_nodes(nodes: list, ox: float = 0.0, oy: float = 0.0):
    """Yield (node, absolute x, absolute y) depth-first, doc order."""
    for node in nodes:
        x = ox + node.get("x", 0)
        y = oy + node.get("y", 0)
        yield node, x, y
        yield from _iter_nodes(node.get("children", []), x, y)


def _style_attr(node: dict, theme: dict) -> str:
    style = node.get("style", {}) or {}
    parts = []
    if style.get("color"):
        parts.append(f"fill:{style['color']}")
    if style.get("background"):
        parts.append(f"background:{style['background']}")
    if style.get("font_size"):
        parts.append(f"font-size:{style['font_size']}px")
    if style.get("bold"):
        parts.append("font-weight:bold")
    align = style.get("align")
    if align in ("left", "center", "right"):
        parts.append(f"text-align:{align}")
    return ";".join(parts)


def render_svg(doc: dict) -> str:
    """Render a doc to a standalone SVG string (no scripts, no HTML)."""
    frame = doc["frame"]
    width, height = frame["width"], frame["height"]
    theme = doc.get("theme", {})
    font = _esc(theme.get("font", "Inter"))
    background = theme.get("background", "#ffffff")
    lines = [
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{_num(width)}" '
            f'height="{_num(height)}" viewBox="0 0 {_num(width)} {_num(height)}" role="img" '
            f'aria-label="{_esc(doc.get("title", "Mockup"))}">'
        ),
        f'<rect x="0" y="0" width="{_num(width)}" height="{_num(height)}" fill="{_esc(background)}"/>',
    ]
    for node, x, y in _iter_nodes(doc.get("nodes", [])):
        lines.append(_svg_node(node, x, y, theme, font))
    lines.append("</svg>")
    return "\n".join(lines)


def _svg_node(node: dict, x: float, y: float, theme: dict, font: str) -> str:
    node_type = node.get("type")
    w, h = node["w"], node["h"]
    style = node.get("style", {}) or {}
    fill = style.get("background")
    radius = theme.get("radius", 12)
    text_color = style.get("color") or theme.get("text", "#111827")
    font_size = style.get("font_size", 20)
    weight = "bold" if style.get("bold") else "normal"
    align = str(style.get("align") or "left")
    anchor = {"center": "middle", "right": "end"}.get(align, "start")
    text_x = {"center": x + w / 2, "right": x + w}.get(align, x)
    if node_type in ("card", "button", "form", "table"):
        bg = fill or (theme.get("primary") if node_type == "button" else "#ffffff")
        stroke = "" if node_type == "button" else ' stroke="#d1d5db"'
        parts = [
            (
                f'<rect x="{_num(x)}" y="{_num(y)}" width="{_num(w)}" height="{_num(h)}" '
                f'rx="{_num(radius)}" fill="{_esc(bg)}"{stroke}/>'
            )
        ]
        text = node.get("text")
        if text:
            parts.append(
                f'<text x="{_num(text_x)}" y="{_num(y + h / 2 + font_size * 0.35)}" '
                f'font-family="{font}" font-size="{_num(font_size)}" font-weight="{weight}" '
                f'fill="{_esc(text_color if node_type != "button" else "#ffffff")}" '
                f'text-anchor="{anchor}">{_esc(text)}</text>'
            )
        return "".join(parts)
    if node_type == "image":
        src = node.get("src", "")
        if src:
            return (
                f'<rect x="{_num(x)}" y="{_num(y)}" width="{_num(w)}" height="{_num(h)}" '
                f'rx="{_num(radius)}" fill="#e5e7eb"/>'
                f'<text x="{_num(x + w / 2)}" y="{_num(y + h / 2)}" font-family="{font}" '
                f'font-size="16" fill="#6b7280" text-anchor="middle">[image]</text>'
            )
        return (
            f'<rect x="{_num(x)}" y="{_num(y)}" width="{_num(w)}" height="{_num(h)}" '
            f'rx="{_num(radius)}" fill="#e5e7eb"/>'
        )
    if node_type == "chart":
        return _svg_chart(node, x, y, theme, font)
    if node_type == "list":
        items = (node.get("props") or {}).get("items", [])
        rows = []
        cy = y + font_size
        for item in items[:10]:
            rows.append(
                f'<text x="{_num(x)}" y="{_num(cy)}" font-family="{font}" '
                f'font-size="{_num(font_size)}" fill="{_esc(text_color)}">'
                f"\u2022 {_esc(item)}</text>"
            )
            cy += font_size * 1.6
        return "".join(rows)
    text = node.get("text", "")
    return (
        f'<text x="{_num(text_x)}" y="{_num(y + font_size)}" font-family="{font}" '
        f'font-size="{_num(font_size)}" font-weight="{weight}" '
        f'fill="{_esc(text_color)}" text-anchor="{anchor}">{_esc(text)}</text>'
    )


def _svg_chart(node: dict, x: float, y: float, theme: dict, font: str) -> str:
    values = (node.get("props") or {}).get("values", [])
    numbers = [float(v) for v in values if isinstance(v, (int, float))]
    w, h = node["w"], node["h"]
    if not numbers:
        return (
            f'<rect x="{_num(x)}" y="{_num(y)}" width="{_num(w)}" height="{_num(h)}" '
            f'fill="#f3f4f6"/>'
        )
    top, bottom = max(numbers), min(numbers)
    span = (top - bottom) or 1.0
    points = []
    for i, value in enumerate(numbers):
        px = x + 10 + (i * (w - 20) / max(len(numbers) - 1, 1))
        py = y + 10 + (1 - (value - bottom) / span) * (h - 20)
        points.append(f"{px:.1f},{py:.1f}")
    color = theme.get("primary", "#1d4ed8")
    return (
        f'<rect x="{_num(x)}" y="{_num(y)}" width="{_num(w)}" height="{_num(h)}" fill="#ffffff"/>'
        f'<polyline points="{" ".join(points)}" fill="none" stroke="{_esc(color)}" stroke-width="2"/>'
    )


def render_html(doc: dict) -> str:
    """Render a doc to a standalone sandboxed HTML page.

    Every string is escaped; styles come only from the theme and
    the node's own whitelisted style keys. The page carries the
    strict CSP as a meta tag as well, so it is safe even when
    served without the header.
    """
    frame = doc["frame"]
    theme = doc.get("theme", {})
    font = _esc(theme.get("font", "Inter"))
    background = theme.get("background", "#ffffff")
    text_color = theme.get("text", "#111827")
    parts = [
        "<!DOCTYPE html>",
        '<html lang="id"><head><meta charset="utf-8">',
        f'<meta http-equiv="Content-Security-Policy" content="{_esc(CSP_HEADER)}">',
        f"<title>{_esc(doc.get('title', 'Mockup'))}</title>",
        "</head>",
        (
            f'<body style="margin:0;background:{_esc(background)};color:{_esc(text_color)};'
            f'font-family:{font},sans-serif">'
        ),
        (
            f'<div style="position:relative;width:{_num(frame["width"])}px;'
            f'height:{_num(frame["height"])}px;overflow:hidden">'
        ),
    ]
    for node, x, y in _iter_nodes(doc.get("nodes", [])):
        parts.append(_html_node(node, x, y, theme))
    parts.append("</div></body></html>")
    return "\n".join(parts)


def _html_node(node: dict, x: float, y: float, theme: dict) -> str:
    node_type = node.get("type")
    w, h = node["w"], node["h"]
    style = node.get("style", {}) or {}
    font_size = style.get("font_size", 20)
    color = style.get("color") or theme.get("text", "#111827")
    background = style.get("background")
    align = (
        style.get("align", "left") if style.get("align") in ("left", "center", "right") else "left"
    )
    weight = "bold" if style.get("bold") else "normal"
    radius = theme.get("radius", 12)
    base = (
        f"position:absolute;left:{_num(x)}px;top:{_num(y)}px;"
        f"width:{_num(w)}px;height:{_num(h)}px;overflow:hidden;"
        f"font-size:{_num(font_size)}px;color:{_esc(color)};font-weight:{weight};"
        f"text-align:{align};box-sizing:border-box"
    )
    if node_type == "image":
        inner = f"[image: {_esc(node.get('src', ''))}]" if node.get("src") else "[image]"
        return (
            f'<div style="{base};background:#e5e7eb;border-radius:{_num(radius)}px;'
            f'display:flex;align-items:center;justify-content:center">{inner}</div>'
        )
    if node_type in ("card", "form", "table"):
        bg = background or "#ffffff"
        return (
            f'<div style="{base};background:{_esc(bg)};border:1px solid #d1d5db;'
            f'border-radius:{_num(radius)}px;padding:12px">{_esc(node.get("text", ""))}</div>'
        )
    if node_type == "button":
        bg = background or theme.get("primary", "#1d4ed8")
        return (
            f'<div style="{base};background:{_esc(bg)};color:#ffffff;'
            f"border-radius:{_num(radius)}px;display:flex;align-items:center;"
            f'justify-content:center">{_esc(node.get("text", ""))}</div>'
        )
    if node_type == "list":
        items = (node.get("props") or {}).get("items", [])
        rows = "".join(f"<li>{_esc(item)}</li>" for item in items[:10])
        return f'<ul style="{base};margin:0;padding-left:20px">{rows}</ul>'
    if node_type == "chart":
        values = (node.get("props") or {}).get("values", [])
        label = ", ".join(str(v) for v in values[:10])
        return f'<div style="{base}">Chart: {_esc(label)}</div>'
    return f'<div style="{base}">{_esc(node.get("text", ""))}</div>'


def estimate_text_width(text: str, font_size: float) -> float:
    """Rough text width in px (0.55 * font size per char, Latin avg)."""
    return len(str(text)) * 0.55 * float(font_size)


def luminance(hex_color: str) -> float:
    """Relative luminance of a #rrggbb color (WCAG formula)."""
    r = int(hex_color[1:3], 16) / 255
    g = int(hex_color[3:5], 16) / 255
    b = int(hex_color[5:7], 16) / 255

    def channel(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast_ratio(color_a: str, color_b: str) -> float:
    """WCAG contrast ratio between two #rrggbb colors."""
    la = luminance(color_a)
    lb = luminance(color_b)
    lighter, darker = (la, lb) if la >= lb else (lb, la)
    return (lighter + 0.05) / (darker + 0.05)


def boxes_overlap(a: dict, b: dict) -> bool:
    """True when two absolute boxes intersect with positive area."""
    return (
        a["x"] < b["x"] + b["w"]
        and b["x"] < a["x"] + a["w"]
        and a["y"] < b["y"] + b["h"]
        and b["y"] < a["y"] + a["h"]
    )


def absolute_boxes(nodes: list, ox: float = 0.0, oy: float = 0.0) -> list[dict]:
    """Flatten the tree to absolute boxes (id, x, y, w, h, node)."""
    out = []
    for node in nodes:
        x, y = ox + node.get("x", 0), oy + node.get("y", 0)
        out.append(
            {"id": node.get("id"), "x": x, "y": y, "w": node["w"], "h": node["h"], "node": node}
        )
        out.extend(absolute_boxes(node.get("children", []), x, y))
    return out
