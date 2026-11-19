"""Indonesian templates for the B7 mockup editor (beta scope: 5).

Each template is a function from slot values to a validated design
doc. Slots are plain strings supplied by the prompt parser
(``puspa.mockup.generate``) or by the user; filling is
deterministic string substitution, never model output, so the same
prompt always builds the same doc. A template never embeds free
HTML: it only builds whitelisted nodes through ``new_node``.

The five beta templates (plan 12.2):

- ``hajatan_invitation`` — wedding / celebration invitation card.
- ``promo_banner`` — shop promo banner / poster.
- ``warung_menu`` — small food-stall menu board.
- ``chat_catalog`` — product catalogue meant to be shared in chat.
- ``lebaran_card`` — Lebaran greeting card.

Deferred to a follow-up (same plan section lists it, beta scope is
five): ``cash_recap`` — a cash / sales recap sheet.
"""

from __future__ import annotations

from .doc import new_doc, new_node

__all__ = [
    "TEMPLATES",
    "fill_template",
    "template_fields",
]

TEMPLATES = ("hajatan_invitation", "promo_banner", "warung_menu", "chat_catalog", "lebaran_card")

#: Slot names each template accepts. Unknown slots are ignored.
TEMPLATE_FIELDS: dict[str, tuple[str, ...]] = {
    "hajatan_invitation": ("title", "names", "date", "place", "host"),
    "promo_banner": ("shop", "headline", "price", "detail", "contact"),
    "warung_menu": ("shop", "items", "price_note", "contact"),
    "chat_catalog": ("shop", "products", "contact", "note"),
    "lebaran_card": ("sender", "message", "names"),
}


def template_fields(template: str) -> tuple[str, ...]:
    """Slot names for a template; raises KeyError on an unknown template."""
    return TEMPLATE_FIELDS[template]


def _text(node_id: str, x: float, y: float, w: float, h: float, text: str, **style) -> dict:
    return new_node("text", x, y, w, h, node_id=node_id, text=text, style=style)


def _hajatan(fields: dict) -> dict:
    return new_doc(
        title=fields.get("title") or "Undangan Hajatan",
        template="hajatan_invitation",
        frame={"width": 800, "height": 1100},
        theme={
            "primary": "#7c2d12",
            "background": "#fff7ed",
            "text": "#431407",
            "accent": "#b45309",
        },
        nodes=[
            _text(
                "title",
                60,
                60,
                680,
                90,
                fields.get("title") or "Undangan Hajatan",
                font_size=44,
                bold=True,
                color="#7c2d12",
                align="center",
            ),
            _text(
                "names",
                60,
                180,
                680,
                80,
                fields.get("names") or "Bapak & Ibu",
                font_size=32,
                bold=True,
                align="center",
            ),
            _text(
                "date",
                60,
                300,
                680,
                50,
                fields.get("date") or "Sabtu, 20 Desember 2026",
                font_size=24,
                align="center",
            ),
            _text(
                "place",
                60,
                360,
                680,
                90,
                fields.get("place") or "Gedung Serbaguna, Jakarta",
                font_size=22,
                align="center",
            ),
            _text(
                "host",
                60,
                980,
                680,
                50,
                f"Kami yang berbahagia, {fields.get('host') or 'Keluarga Besar'}",
                font_size=20,
                align="center",
            ),
        ],
    )


def _banner(fields: dict) -> dict:
    return new_doc(
        title=fields.get("headline") or "Promo",
        template="promo_banner",
        frame={"width": 1080, "height": 1080},
        theme={
            "primary": "#1d4ed8",
            "background": "#eff6ff",
            "text": "#111827",
            "accent": "#16a34a",
        },
        nodes=[
            _text(
                "shop",
                80,
                80,
                920,
                60,
                fields.get("shop") or "Toko Saya",
                font_size=36,
                bold=True,
                color="#1d4ed8",
            ),
            _text(
                "headline",
                80,
                180,
                920,
                160,
                fields.get("headline") or "Promo Spesial Hari Ini",
                font_size=64,
                bold=True,
            ),
            _text(
                "price",
                80,
                380,
                920,
                120,
                fields.get("price") or "Rp25.000",
                font_size=72,
                bold=True,
                color="#16a34a",
            ),
            _text(
                "detail",
                80,
                540,
                920,
                120,
                fields.get("detail") or "Stok terbatas, buruan!",
                font_size=30,
            ),
            _text(
                "contact",
                80,
                940,
                920,
                60,
                fields.get("contact") or "Pesan via WhatsApp",
                font_size=26,
                color="#1d4ed8",
            ),
        ],
    )


def _warung_menu(fields: dict) -> dict:
    items = fields.get("items") or "Nasi Goreng - Rp15.000\nMie Ayam - Rp12.000\nEs Teh - Rp5.000"
    rows = [row for row in items.split("\n") if row.strip()]
    nodes = [
        _text(
            "shop",
            60,
            40,
            680,
            60,
            fields.get("shop") or "Warung Makan",
            font_size=40,
            bold=True,
            align="center",
        ),
    ]
    y = 140
    for index, row in enumerate(rows[:12]):
        nodes.append(_text(f"item-{index}", 60, y, 680, 44, row, font_size=26))
        y += 56
    nodes.append(
        _text(
            "price-note",
            60,
            y + 10,
            680,
            50,
            fields.get("price_note") or "Harga sudah termasuk pajak",
            font_size=20,
            align="center",
        )
    )
    nodes.append(
        _text(
            "contact",
            60,
            y + 70,
            680,
            50,
            fields.get("contact") or "Buka 08.00 - 21.00",
            font_size=20,
            align="center",
        )
    )
    return new_doc(
        title=f"Menu {fields.get('shop') or 'Warung'}",
        template="warung_menu",
        frame={"width": 800, "height": 1000},
        theme={
            "primary": "#92400e",
            "background": "#fffbeb",
            "text": "#451a03",
            "accent": "#dc2626",
        },
        nodes=nodes,
    )


def _catalog(fields: dict) -> dict:
    products = fields.get("products") or "Produk 1 - Rp20.000\nProduk 2 - Rp35.000"
    rows = [row for row in products.split("\n") if row.strip()]
    cards = []
    y = 150
    for index, row in enumerate(rows[:8]):
        name, _, price = row.partition(" - ")
        cards.append(
            new_node(
                "card",
                60,
                y,
                680,
                90,
                node_id=f"product-{index}",
                children=[
                    new_node(
                        "text",
                        20,
                        12,
                        420,
                        40,
                        node_id=f"product-{index}-name",
                        text=name.strip() or row,
                        style={"font_size": 26, "bold": True},
                    ),
                    new_node(
                        "text",
                        460,
                        12,
                        200,
                        40,
                        node_id=f"product-{index}-price",
                        text=price.strip() or "",
                        style={"font_size": 26, "color": "#16a34a"},
                    ),
                ],
            )
        )
        y += 104
    nodes = [
        _text(
            "shop",
            60,
            40,
            680,
            60,
            fields.get("shop") or "Katalog Toko",
            font_size=38,
            bold=True,
            align="center",
        ),
        *cards,
        _text(
            "note",
            60,
            y + 10,
            680,
            50,
            fields.get("note") or "Pesan sekarang, stok terbatas",
            font_size=22,
            align="center",
        ),
        _text(
            "contact",
            60,
            y + 70,
            680,
            50,
            fields.get("contact") or "Hubungi kami",
            font_size=22,
            align="center",
        ),
    ]
    return new_doc(
        title=f"Katalog {fields.get('shop') or 'Toko'}",
        template="chat_catalog",
        frame={"width": 800, "height": 1100},
        theme={
            "primary": "#0f766e",
            "background": "#f0fdfa",
            "text": "#134e4a",
            "accent": "#16a34a",
        },
        nodes=nodes,
    )


def _lebaran(fields: dict) -> dict:
    return new_doc(
        title="Kartu Lebaran",
        template="lebaran_card",
        frame={"width": 800, "height": 800},
        theme={
            "primary": "#065f46",
            "background": "#ecfdf5",
            "text": "#064e3b",
            "accent": "#d97706",
        },
        nodes=[
            _text(
                "greeting",
                60,
                120,
                680,
                100,
                "Selamat Hari Raya Idul Fitri",
                font_size=44,
                bold=True,
                color="#065f46",
                align="center",
            ),
            _text(
                "message",
                80,
                280,
                640,
                150,
                fields.get("message") or "Mohon maaf lahir dan batin",
                font_size=28,
                align="center",
            ),
            _text(
                "sender",
                60,
                560,
                680,
                60,
                fields.get("sender") or "Keluarga",
                font_size=26,
                align="center",
            ),
            _text(
                "names", 60, 630, 680, 50, fields.get("names") or "", font_size=22, align="center"
            ),
        ],
    )


_BUILDERS = {
    "hajatan_invitation": _hajatan,
    "promo_banner": _banner,
    "warung_menu": _warung_menu,
    "chat_catalog": _catalog,
    "lebaran_card": _lebaran,
}


def fill_template(template: str, fields: dict | None = None) -> dict:
    """Build a validated design doc from a template id and slot values.

    Unknown slot keys are dropped; missing slots fall back to the
    template's defaults, so a bare template name still renders.
    """
    if template not in _BUILDERS:
        raise KeyError(f"unknown template {template!r}; known: {sorted(_BUILDERS)}")
    allowed = TEMPLATE_FIELDS[template]
    clean = {key: str(value) for key, value in (fields or {}).items() if key in allowed}
    return _BUILDERS[template](clean)
