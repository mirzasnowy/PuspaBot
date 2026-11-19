"""Export a design doc to PNG and PDF (rendered output, not the doc).

PNG is rasterised from the node tree with Pillow (rectangles and
text with the default bitmap font — deterministic, no system font
dependency). PDF is written by a small stdlib-only writer: one
page, Helvetica, rectangles and text lines, with PDF string
escaping. Both exports draw the same layout the SVG renderer
draws, so what the user exports is what they saw.

Honest limit: these are flat rendered exports. The PNG has no
embedded fonts and the PDF is not a print-shop file (no bleed,
no CMYK); that is enough for a chat-shared poster or catalogue,
which is the beta use case.
"""

from __future__ import annotations

import io

from .render import _iter_nodes

__all__ = ["export_pdf", "export_png"]


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    return (int(hex_color[1:3], 16), int(hex_color[3:5], 16), int(hex_color[5:7], 16))


def export_png(doc: dict, *, scale: int = 1) -> bytes:
    """Render the doc to PNG bytes (Pillow rasterisation)."""
    from PIL import Image, ImageDraw

    frame = doc["frame"]
    width, height = int(frame["width"] * scale), int(frame["height"] * scale)
    theme = doc.get("theme", {})
    image = Image.new("RGB", (width, height), _hex_to_rgb(theme.get("background", "#ffffff")))
    draw = ImageDraw.Draw(image)
    for node, x, y in _iter_nodes(doc.get("nodes", [])):
        nx, ny = int(x * scale), int(y * scale)
        nw, nh = int(node["w"] * scale), int(node["h"] * scale)
        style = node.get("style", {}) or {}
        node_type = node.get("type")
        if node_type in ("card", "form", "table"):
            draw.rectangle(
                [nx, ny, nx + nw, ny + nh],
                fill=_hex_to_rgb(style.get("background") or "#ffffff"),
                outline=(209, 213, 219),
            )
        elif node_type == "button":
            draw.rectangle(
                [nx, ny, nx + nw, ny + nh],
                fill=_hex_to_rgb(style.get("background") or theme.get("primary", "#1d4ed8")),
            )
        elif node_type == "image":
            draw.rectangle([nx, ny, nx + nw, ny + nh], fill=(229, 231, 235))
        text = node.get("text", "")
        if text:
            fill = _hex_to_rgb(
                style.get("color")
                or ("#ffffff" if node_type == "button" else theme.get("text", "#111827"))
            )
            draw.text((nx + 4, ny + 4), text, fill=fill)
        if node_type == "list":
            cy = ny + 4
            for item in (node.get("props") or {}).get("items", [])[:10]:
                draw.text((nx + 4, cy), f"- {item}", fill=_hex_to_rgb(theme.get("text", "#111827")))
                cy += 16
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _pdf_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def export_pdf(doc: dict) -> bytes:
    """Render the doc to a one-page PDF (stdlib writer, Helvetica)."""
    frame = doc["frame"]
    width, height = float(frame["width"]), float(frame["height"])
    theme = doc.get("theme", {})
    lines: list[str] = []
    lines.append("BT /F1 20 Tf 0 0 0 rg ET")
    for node, x, y in _iter_nodes(doc.get("nodes", [])):
        nx, ny = float(x), height - float(y)
        nw, nh = float(node["w"]), float(node["h"])
        style = node.get("style", {}) or {}
        node_type = node.get("type")
        fill = style.get("background")
        if node_type in ("card", "form", "table"):
            fill = fill or "#ffffff"
        elif node_type == "button":
            fill = fill or theme.get("primary", "#1d4ed8")
        elif node_type == "image":
            fill = "#e5e7eb"
        if fill:
            r, g, b = _hex_to_rgb(fill)
            lines.append(
                f"{r / 255:.3f} {g / 255:.3f} {b / 255:.3f} rg "
                f"{nx:.1f} {ny - nh:.1f} {nw:.1f} {nh:.1f} re f"
            )
        text = node.get("text", "")
        if text:
            color = style.get("color") or (
                "#ffffff" if node_type == "button" else theme.get("text", "#111827")
            )
            r, g, b = _hex_to_rgb(color)
            size = float(style.get("font_size", 20))
            lines.append(
                f"BT /F1 {size:.1f} Tf {r / 255:.3f} {g / 255:.3f} {b / 255:.3f} rg "
                f"{nx + 4:.1f} {ny - size - 4:.1f} Td ({_pdf_escape(text)}) Tj ET"
            )
        if node_type == "list":
            cy = ny - 24
            for item in (node.get("props") or {}).get("items", [])[:10]:
                r, g, b = _hex_to_rgb(theme.get("text", "#111827"))
                lines.append(
                    f"BT /F1 12 Tf {r / 255:.3f} {g / 255:.3f} {b / 255:.3f} rg "
                    f"{nx + 4:.1f} {cy:.1f} Td ({_pdf_escape('- ' + str(item))}) Tj ET"
                )
                cy -= 16
    content = "\n".join(lines).encode("latin-1", errors="replace")
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width:.0f} {height:.0f}] "
        f"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>".encode("latin-1"),
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    ).encode()
    return bytes(out)
