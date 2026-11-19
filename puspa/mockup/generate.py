"""Prompt to mockup: intent parsing, variants, self-critique, patches.

Flow (plan 12.2): parse intent -> pick frame -> fill -> render ->
self-critique -> show 2-3 variants. Everything deterministic
except the optional LLM step, which is a seam: the caller may pass
an ``llm_complete(prompt) -> str`` callable (built from
``puspa.router.complete.default_llm_complete``); when none is
passed, generation is fully deterministic template filling and
word-change patches are parsed by rules. Tests never call a live
model.

Self-critique (deterministic, no vision model yet): checks clipped
text (estimated width vs box), contrast (WCAG ratio vs the theme
background), and colliding sibling elements. Issues are reported
and auto-fixed where safe (shrink the font one step, darken the
text); the remaining issues ride along in the result so the UI can
show them. A real vision model checking the rendered pixels is a
follow-up seam — see ``docs/mockup-editor.md``.

Word-change patches (Indonesian, e.g. "besarkan harga, ganti warna
jadi hijau"): parsed by rules into ops on the doc. The LLM seam
(``patch_from_llm``) accepts the same instruction and asks the
model for a JSON list of ops; the reply is validated as strictly
as a user op (whitelisted op types, no scripts) before anything
is applied, and a malformed reply raises instead of guessing.
"""

from __future__ import annotations

import copy
import json
import re

from puspa.local_data.money import format_rupiah, parse_rupiah_amount

from .doc import ALLOWED_OP_TYPES, DesignError, find_node, validate_doc
from .render import absolute_boxes, contrast_ratio, estimate_text_width
from .templates import fill_template

__all__ = [
    "GREEN",
    "critique_doc",
    "detect_template",
    "extract_fields",
    "generate_mockup",
    "parse_word_patch",
    "patch_from_llm",
]

#: Indonesian colour words the word-change parser understands.
COLOR_WORDS = {
    "hijau": "#16a34a",
    "merah": "#dc2626",
    "biru": "#1d4ed8",
    "kuning": "#eab308",
    "hitam": "#111827",
    "putih": "#ffffff",
    "ungu": "#7c3aed",
    "oranye": "#ea580c",
    "pink": "#db2777",
    "coklat": "#92400e",
}

GREEN = "#16a34a"

_TEMPLATE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "hajatan_invitation": ("undangan", "hajatan", "pernikahan", "nikah", "walimah", "syukuran"),
    "promo_banner": ("banner", "spanduk", "poster", "promo", "diskon", "sale", "iklan"),
    "warung_menu": ("menu", "warung", "makanan", "kuliner", "daftar harga makanan"),
    "chat_catalog": ("katalog", "catalog", "produk", "jualan", "etalase", "daftar produk"),
    "lebaran_card": ("lebaran", "idul fitri", "idulfitri", "ramadan", "ramadhan", "kartu ucapan"),
}

#: Variant themes: same layout, different accent/background pairs.
VARIANT_THEMES = (
    {"primary": "#1d4ed8", "background": "#eff6ff", "text": "#111827", "accent": "#16a34a"},
    {"primary": "#7c2d12", "background": "#fff7ed", "text": "#431407", "accent": "#b45309"},
    {"primary": "#065f46", "background": "#ecfdf5", "text": "#064e3b", "accent": "#d97706"},
)


def detect_template(prompt: str) -> str:
    """Pick the best template for a prompt by keyword hits.

    Ties break by template order (the first listed wins). A prompt
    with no keyword at all falls back to ``promo_banner`` — the
    most common UMKM ask — rather than failing.
    """
    lowered = prompt.lower()
    best, best_hits = "promo_banner", 0
    for template, keywords in _TEMPLATE_KEYWORDS.items():
        hits = sum(1 for keyword in keywords if keyword in lowered)
        if hits > best_hits:
            best, best_hits = template, hits
    return best


_QUOTED_RE = re.compile(r'"([^"]+)"')


def extract_fields(prompt: str, template: str) -> dict:
    """Pull slot values out of a prompt with small deterministic rules.

    Quoted text becomes the headline/title; the first money amount
    (``Rp25.000``, ``50rb``, ``1,5 jt`` — see
    ``puspa.local_data.money``) becomes the price, rendered the
    local way; the first capitalised phrase after "toko" or
    "warung" becomes the shop name. Anything not found stays
    missing and the template default fills it — extraction never
    invents a value.
    """
    fields: dict[str, str] = {}
    quoted = _QUOTED_RE.findall(prompt)
    amount = parse_rupiah_amount(prompt)
    if amount is not None:
        fields["price"] = format_rupiah(amount)
    shop = re.search(r"(?:toko|warung|kedai)\s+([A-Z][\w' ]{1,30})", prompt)
    if shop:
        fields["shop"] = shop.group(1).split(" Rp")[0].strip()
    if quoted:
        if template in ("promo_banner", "hajatan_invitation"):
            fields["headline" if template == "promo_banner" else "title"] = quoted[0]
        elif template == "lebaran_card":
            fields["message"] = quoted[0]
        elif template == "chat_catalog":
            fields["note"] = quoted[0]
    date = re.search(r"(\d{1,2}\s+\w+\s+\d{4})", prompt)
    if date and template == "hajatan_invitation":
        fields["date"] = date.group(1)
    return fields


def critique_doc(doc: dict) -> list[dict]:
    """Self-critique: clipped text, low contrast, colliding elements.

    Returns a list of ``{"kind", "node_id", "detail"}`` issues.
    Empty means the mockup passes its own checks.
    """
    issues: list[dict] = []
    theme = doc.get("theme", {})
    background = theme.get("background", "#ffffff")
    boxes = absolute_boxes(doc.get("nodes", []))
    for box in boxes:
        node = box["node"]
        text = node.get("text", "")
        style = node.get("style", {}) or {}
        if text and node.get("type") in ("text", "button"):
            font_size = style.get("font_size", 20)
            lines = max(1, -(-len(text) // max(1, int(box["w"] // max(1, 0.55 * font_size)))))
            needed = lines * font_size * 1.3
            if estimate_text_width(text, font_size) > box["w"] and needed > box["h"]:
                issues.append(
                    {
                        "kind": "clipped_text",
                        "node_id": node.get("id"),
                        "detail": f"text needs about {needed:.0f}px height in a {box['h']:.0f}px box",
                    }
                )
            color = style.get("color") or theme.get("text", "#111827")
            try:
                if contrast_ratio(color, background) < 3.0:
                    issues.append(
                        {
                            "kind": "low_contrast",
                            "node_id": node.get("id"),
                            "detail": f"contrast {contrast_ratio(color, background):.1f}:1 "
                            "between text and background is below 3:1",
                        }
                    )
            except (ValueError, IndexError):
                pass
    for i, first in enumerate(boxes):
        for second in boxes[i + 1 :]:
            if first["node"] is second["node"]:
                continue
            if _siblings(first, second, doc) and _overlap(first, second):
                issues.append(
                    {
                        "kind": "collision",
                        "node_id": first["id"],
                        "detail": f"overlaps {second['id']}",
                    }
                )
    return issues


def _siblings(first: dict, second: dict, doc: dict) -> bool:
    """True when two boxes share the same parent list (top level here)."""
    top_ids = {node.get("id") for node in doc.get("nodes", [])}
    return first["id"] in top_ids and second["id"] in top_ids


def _overlap(a: dict, b: dict) -> bool:
    return (
        a["x"] < b["x"] + b["w"]
        and b["x"] < a["x"] + a["w"]
        and a["y"] < b["y"] + b["h"]
        and b["y"] < a["y"] + a["h"]
    )


def _autofix(doc: dict, issues: list[dict]) -> dict:
    """Apply the safe automatic fixes for critique issues.

    Clipped text: shrink the font one step (never below 12px).
    Low contrast: darken the text to the theme text colour. Anything
    else is left for the user; the issue stays in the report.
    """
    fixed = copy.deepcopy(doc)
    for issue in issues:
        node = find_node(fixed.get("nodes", []), issue.get("node_id") or "")
        if node is None:
            continue
        if issue["kind"] == "clipped_text":
            style = node.setdefault("style", {})
            size = style.get("font_size", 20)
            if size > 12:
                style["font_size"] = max(12, size - 4)
        elif issue["kind"] == "low_contrast":
            node.setdefault("style", {})["color"] = fixed["theme"].get("text", "#111827")
    return fixed


def generate_mockup(prompt: str, *, variants: int = 3) -> dict:
    """Build 2-3 mockup variants from a text prompt (deterministic).

    Returns ``{"template": ..., "fields": ..., "variants": [...]}``
    where each variant is ``{"doc": ..., "issues": [...]}``. The
    first variant is the base fill; the others re-theme it. Each
    variant is self-critiqued and auto-fixed once; the issues
    reported are the ones remaining after the fix.
    """
    if not isinstance(prompt, str) or not prompt.strip():
        raise DesignError("prompt must be a non-empty string")
    template = detect_template(prompt)
    fields = extract_fields(prompt, template)
    base = fill_template(template, fields)
    count = max(1, min(3, variants))
    out = []
    for index in range(count):
        doc = copy.deepcopy(base)
        if index:
            doc["theme"].update(VARIANT_THEMES[index % len(VARIANT_THEMES)])
            doc["title"] = f"{base['title']} (varian {index + 1})"
        issues = critique_doc(doc)
        doc = _autofix(doc, issues)
        remaining = critique_doc(doc)
        out.append({"doc": validate_doc(doc), "issues": remaining})
    return {"template": template, "fields": fields, "variants": out}


_SIZE_WORDS = {"besarkan": 1.25, "perbesar": 1.25, "kecilkan": 0.8, "perkecil": 0.8}


def parse_word_patch(doc: dict, instruction: str) -> list[dict]:
    """Turn an Indonesian word-change instruction into ops.

    Understands: "besarkan/perbesar <node word>" (grow the font of
    the node whose text contains the word), "kecilkan/perkecil ...",
    "ganti warna jadi <colour>" (theme accent, or a named node's
    colour when "warna <node word>" is given), "ganti teks <node
    word> jadi <new text>". Returns ops in doc-op shape (without
    seq; ``apply_op`` numbers them). Raises :class:`DesignError`
    when nothing in the instruction matches the doc.
    """
    if not isinstance(instruction, str) or not instruction.strip():
        raise DesignError("instruction must be a non-empty string")
    ops: list[dict] = []
    nodes = absolute_boxes(doc.get("nodes", []))

    #: Words users say for a node they mean by role, not by its text.
    ROLE_ALIASES = {
        "harga": ("price",),
        "judul": ("headline", "title"),
        "nama": ("shop", "names", "sender"),
        "toko": ("shop",),
        "kontak": ("contact",),
        "tanggal": ("date",),
        "tempat": ("place",),
    }

    def node_by_word(word: str) -> dict | None:
        for box in nodes:
            node = box["node"]
            text = (node.get("text") or "").lower()
            if word and (word in text or word in (node.get("id") or "").lower()):
                return node
        for alias in ROLE_ALIASES.get(word, ()):
            node = find_node(doc.get("nodes", []), alias)
            if node is not None:
                return node
        return None

    colour_match = re.search(r"ganti warna(?: (\w+))? jadi (\w+)", instruction, re.IGNORECASE)
    if colour_match:
        target_word = (colour_match.group(1) or "").lower()
        colour_word = colour_match.group(2).lower()
        colour = COLOR_WORDS.get(colour_word)
        if colour is None:
            raise DesignError(f"unknown colour word {colour_word!r}")
        if target_word:
            node = node_by_word(target_word)
            if node is None:
                raise DesignError(f"no node mentions {target_word!r}")
            ops.append(
                {
                    "type": "set_style",
                    "node_id": node["id"],
                    "payload": {"style": {"color": colour}},
                    "note": instruction,
                }
            )
        else:
            ops.append(
                {
                    "type": "set_theme",
                    "payload": {"theme": {"accent": colour, "primary": colour}},
                    "note": instruction,
                }
            )
    text_match = re.search(r"ganti teks (\w+) jadi (.+)", instruction, re.IGNORECASE)
    if text_match:
        node = node_by_word(text_match.group(1).lower())
        if node is None:
            raise DesignError(f"no node mentions {text_match.group(1)!r}")
        ops.append(
            {
                "type": "set_text",
                "node_id": node["id"],
                "payload": {"text": text_match.group(2).strip().strip("\"'")},
                "note": instruction,
            }
        )
    for word, factor in _SIZE_WORDS.items():
        size_match = re.search(rf"{word} (\w+)", instruction, re.IGNORECASE)
        if size_match:
            node = node_by_word(size_match.group(1).lower())
            if node is None:
                raise DesignError(f"no node mentions {size_match.group(1)!r}")
            current = (node.get("style") or {}).get("font_size", 20)
            new_size = max(8, min(200, round(current * factor)))
            ops.append(
                {
                    "type": "set_style",
                    "node_id": node["id"],
                    "payload": {"style": {"font_size": new_size}},
                    "note": instruction,
                }
            )
    if not ops:
        raise DesignError(f"could not parse instruction {instruction!r} into ops")
    return ops


def patch_from_llm(doc: dict, instruction: str, llm_complete) -> list[dict]:
    """Ask an LLM for ops implementing ``instruction`` (the LLM seam).

    ``llm_complete`` is a ``complete(prompt) -> str`` callable, e.g.
    ``puspa.router.complete.default_llm_complete(role="editor")``.
    The model must reply with a JSON list of ops; the reply is
    parsed strictly and every op is validated against the doc
    (whitelisted types only, no scripts) before it is returned.
    A non-JSON reply or an invalid op raises :class:`DesignError`
    — never a silent guess. When ``llm_complete`` is None, the
    deterministic word parser is used instead.
    """
    if llm_complete is None:
        return parse_word_patch(doc, instruction)
    prompt = (
        "You edit a declarative design document. Reply with ONLY a JSON list of ops. "
        "Each op is an object with type (one of "
        + ", ".join(ALLOWED_OP_TYPES)
        + "), node_id when it targets a node, and payload. "
        f"Nodes: {json.dumps([{'id': b['id'], 'text': b['node'].get('text', '')} for b in absolute_boxes(doc.get('nodes', []))])}. "
        f"Instruction: {instruction}"
    )
    raw = llm_complete(prompt)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DesignError(f"LLM patch reply is not JSON: {exc}") from exc
    if not isinstance(parsed, list):
        raise DesignError("LLM patch reply must be a JSON list of ops")
    from .doc import validate_op

    for op in parsed:
        validate_op(op, doc)
    return parsed
