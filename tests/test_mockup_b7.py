"""Tests for the B7 mockup editor core (``puspa/mockup/``).

Covers the design-doc engine (validation, numbered ops, undo/redo,
round-trip through JSON), the five templates, deterministic
rendering (SVG + safe HTML, no script tags ever), prompt-to-mockup
generation with variants and self-critique, word-change patches,
the LLM patch seam with a fake model, and PNG/PDF export.
No live model is called anywhere here.
"""

import json

import pytest

from puspa.mockup.doc import (
    DesignError,
    DesignSession,
    apply_op,
    find_node,
    new_doc,
    new_node,
    validate_doc,
)
from puspa.mockup.export import export_pdf, export_png
from puspa.mockup.generate import (
    critique_doc,
    detect_template,
    extract_fields,
    generate_mockup,
    parse_word_patch,
    patch_from_llm,
)
from puspa.mockup.render import csp_header, render_html, render_svg
from puspa.mockup.templates import TEMPLATES, fill_template

TINY_PNG = (
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
    "AAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def _banner_doc():
    return fill_template(
        "promo_banner",
        {"shop": "Warung Sari", "headline": "Diskon 20%", "price": "Rp25.000"},
    )


# -- doc engine ----------------------------------------------------------


def test_new_doc_defaults_and_validation():
    doc = _banner_doc()
    assert doc["template"] == "promo_banner"
    assert doc["version"] == 0
    assert doc["ops"] == []
    assert validate_doc(doc) is doc


def test_validation_rejects_unknown_component_and_scripts():
    doc = _banner_doc()
    doc["nodes"].append(new_node("video", 0, 0, 10, 10))
    with pytest.raises(DesignError):
        validate_doc(doc)
    doc = _banner_doc()
    doc["nodes"][0]["text"] = "<script>alert(1)</script>"
    with pytest.raises(DesignError):
        validate_doc(doc)
    doc = _banner_doc()
    doc["nodes"][0]["text"] = "see javascript:alert(1)"
    with pytest.raises(DesignError):
        validate_doc(doc)


def test_validation_rejects_bad_color_font_and_duplicate_id():
    doc = _banner_doc()
    doc["theme"]["primary"] = "red"
    with pytest.raises(DesignError):
        validate_doc(doc)
    doc = _banner_doc()
    doc["theme"]["font"] = "Comic Sans"
    with pytest.raises(DesignError):
        validate_doc(doc)
    doc = _banner_doc()
    doc["nodes"].append(new_node("text", 0, 0, 10, 10, node_id="shop", text="dup"))
    with pytest.raises(DesignError):
        validate_doc(doc)


def test_ops_are_numbered_and_round_trip_through_json():
    doc = _banner_doc()
    apply_op(doc, {"type": "move_node", "node_id": "price", "payload": {"x": 100, "y": 400}})
    apply_op(doc, {"type": "set_text", "node_id": "price", "payload": {"text": "Rp20.000"}})
    assert [op["seq"] for op in doc["ops"]] == [1, 2]
    assert doc["version"] == 2
    assert find_node(doc["nodes"], "price")["text"] == "Rp20.000"
    assert find_node(doc["nodes"], "price")["x"] == 100
    reloaded = validate_doc(json.loads(json.dumps(doc)))
    assert reloaded["ops"][1]["type"] == "set_text"


def test_op_with_note_records_a_design_decision():
    doc = _banner_doc()
    apply_op(
        doc,
        {
            "type": "set_theme",
            "payload": {"theme": {"accent": "#16a34a"}},
            "note": "Green fits the Lebaran theme.",
        },
    )
    assert doc["decisions"] == [{"seq": 1, "note": "Green fits the Lebaran theme."}]


def test_invalid_ops_rejected():
    doc = _banner_doc()
    with pytest.raises(DesignError):
        apply_op(doc, {"type": "move_node", "node_id": "nope", "payload": {"x": 1, "y": 1}})
    with pytest.raises(DesignError):
        apply_op(doc, {"type": "explode", "node_id": "price"})
    with pytest.raises(DesignError):
        apply_op(
            doc, {"type": "set_style", "node_id": "price", "payload": {"style": {"color": "red"}}}
        )
    with pytest.raises(DesignError):
        apply_op(
            doc,
            {"type": "set_image", "node_id": "price", "payload": {"asset_id": "missing"}},
        )


def test_reorder_add_remove_round_trip():
    doc = _banner_doc()
    first = doc["nodes"][0]["id"]
    apply_op(doc, {"type": "reorder", "node_id": first, "payload": {"index": 2}})
    assert doc["nodes"][2]["id"] == first
    apply_op(
        doc,
        {
            "type": "add_node",
            "payload": {"node": new_node("text", 0, 0, 100, 30, node_id="extra", text="Hi")},
        },
    )
    assert find_node(doc["nodes"], "extra") is not None
    apply_op(doc, {"type": "remove_node", "node_id": "extra"})
    assert find_node(doc["nodes"], "extra") is None


def test_session_undo_redo():
    doc = _banner_doc()
    session = DesignSession(doc)
    assert not session.can_undo
    session.apply({"type": "set_text", "node_id": "price", "payload": {"text": "Rp10.000"}})
    assert session.can_undo
    session.undo()
    assert find_node(session.doc["nodes"], "price")["text"] == "Rp25.000"
    assert session.can_redo
    session.redo()
    assert find_node(session.doc["nodes"], "price")["text"] == "Rp10.000"
    with pytest.raises(DesignError):
        DesignSession(_banner_doc()).undo()
    with pytest.raises(DesignError):
        DesignSession(_banner_doc()).redo()


def test_undo_does_not_rewrite_the_op_log():
    doc = _banner_doc()
    session = DesignSession(doc)
    session.apply({"type": "set_text", "node_id": "price", "payload": {"text": "Rp10.000"}})
    session.undo()
    assert len(session.doc["ops"]) == 0
    assert session.doc["version"] == 0
    assert any("Undid" in d["note"] for d in session.doc["decisions"])


# -- templates -----------------------------------------------------------


def test_five_templates_all_render():
    assert TEMPLATES == (
        "hajatan_invitation",
        "promo_banner",
        "warung_menu",
        "chat_catalog",
        "lebaran_card",
    )
    for template in TEMPLATES:
        doc = fill_template(template, {})
        assert doc["template"] == template
        assert render_svg(doc).startswith("<svg")
    with pytest.raises(KeyError):
        fill_template("cash_recap", {})


def test_template_filling_uses_slots():
    doc = fill_template("warung_menu", {"shop": "Warung Bu Tini", "items": "Soto - Rp10.000"})
    assert find_node(doc["nodes"], "shop")["text"] == "Warung Bu Tini"
    assert find_node(doc["nodes"], "item-0")["text"] == "Soto - Rp10.000"
    doc = fill_template("chat_catalog", {"shop": "Toko A", "products": "Batik - Rp50.000"})
    assert find_node(doc["nodes"], "product-0-name")["text"] == "Batik"


def test_new_doc_with_asset_validates_data_uri_only():
    doc = new_doc(title="T", template="promo_banner")
    doc["assets"]["logo"] = {"kind": "image", "data_uri": TINY_PNG, "name": "logo.png"}
    assert validate_doc(doc) is doc
    doc["assets"]["logo"]["data_uri"] = "https://example.com/logo.png"
    with pytest.raises(DesignError):
        validate_doc(doc)


# -- rendering -----------------------------------------------------------


def test_render_is_deterministic_and_script_free():
    doc = _banner_doc()
    assert render_svg(doc) == render_svg(doc)
    assert render_html(doc) == render_html(doc)
    hostile = _banner_doc()
    apply_op(
        hostile,
        {"type": "set_text", "node_id": "headline", "payload": {"text": "<b>Diskon</b> & hemat"}},
    )
    svg = render_svg(hostile)
    html = render_html(hostile)
    assert "<script" not in svg and "<script" not in html
    assert "&lt;b&gt;Diskon&lt;/b&gt; &amp; hemat" in svg
    assert "&lt;b&gt;Diskon&lt;/b&gt; &amp; hemat" in html
    assert "<b>Diskon</b>" not in svg and "<b>Diskon</b>" not in html


def test_render_html_carries_strict_csp():
    assert "sandbox" in csp_header()
    assert "default-src 'none'" in csp_header()
    html = render_html(_banner_doc())
    assert "Content-Security-Policy" in html
    assert "sandbox" in html


def test_render_svg_escapes_title_and_uses_theme():
    doc = _banner_doc()
    svg = render_svg(doc)
    assert "#eff6ff" in svg
    assert "Warung Sari" in svg


# -- generation ----------------------------------------------------------


def test_detect_template_and_extract_fields():
    assert detect_template("buatkan undangan hajatan untuk pernikahan") == "hajatan_invitation"
    assert detect_template("poster promo diskon toko Kopi Senja Rp25.000") == "promo_banner"
    assert detect_template("menu warung makan siang") == "warung_menu"
    assert detect_template("katalog produk batik untuk dibagikan di chat") == "chat_catalog"
    assert detect_template("kartu ucapan lebaran") == "lebaran_card"
    assert detect_template("sesuatu yang tidak dikenal") == "promo_banner"
    fields = extract_fields('promo "Diskon Besar" toko Kopi Senja Rp25.000', "promo_banner")
    assert fields["headline"] == "Diskon Besar"
    assert fields["price"] == "Rp25.000"
    assert fields["shop"] == "Kopi Senja"


def test_generate_mockup_returns_variants_with_issues_checked():
    result = generate_mockup("poster promo diskon toko Kopi Senja Rp25.000")
    assert result["template"] == "promo_banner"
    assert len(result["variants"]) == 3
    for variant in result["variants"]:
        assert validate_doc(variant["doc"]) is variant["doc"]
        assert isinstance(variant["issues"], list)
    assert result["variants"][0]["doc"]["theme"] != result["variants"][1]["doc"]["theme"]
    with pytest.raises(DesignError):
        generate_mockup("   ")


def test_critique_finds_clipped_text_low_contrast_and_collision():
    doc = _banner_doc()
    apply_op(
        doc,
        {
            "type": "set_style",
            "node_id": "headline",
            "payload": {"style": {"color": "#eff6ff"}},
        },
    )
    kinds = {issue["kind"] for issue in critique_doc(doc)}
    assert "low_contrast" in kinds
    doc = _banner_doc()
    apply_op(
        doc,
        {
            "type": "set_text",
            "node_id": "headline",
            "payload": {"text": "Diskon besar-besaran untuk semua produk toko hari ini saja"},
        },
    )
    apply_op(
        doc,
        {"type": "set_style", "node_id": "headline", "payload": {"style": {"font_size": 64}}},
    )
    kinds = {issue["kind"] for issue in critique_doc(doc)}
    assert "clipped_text" in kinds
    doc = _banner_doc()
    apply_op(doc, {"type": "move_node", "node_id": "price", "payload": {"x": 80, "y": 180}})
    kinds = {issue["kind"] for issue in critique_doc(doc)}
    assert "collision" in kinds


def test_word_patch_besarkan_harga_and_ganti_warna():
    doc = _banner_doc()
    before = find_node(doc["nodes"], "price")["style"].get("font_size", 20)
    ops = parse_word_patch(doc, "besarkan harga")
    assert ops[0]["type"] == "set_style"
    for op in ops:
        apply_op(doc, op)
    assert find_node(doc["nodes"], "price")["style"]["font_size"] > before
    ops = parse_word_patch(doc, "ganti warna jadi hijau")
    for op in ops:
        apply_op(doc, op)
    assert doc["theme"]["accent"] == "#16a34a"
    ops = parse_word_patch(doc, 'ganti teks diskon jadi "Promo Akhir Tahun"')
    for op in ops:
        apply_op(doc, op)
    assert "Promo Akhir Tahun" in find_node(doc["nodes"], "headline")["text"]
    with pytest.raises(DesignError):
        parse_word_patch(doc, "lakukan sesuatu yang ajaib")


def test_llm_patch_seam_validates_replies():
    doc = _banner_doc()

    def fake_complete(prompt: str) -> str:
        assert "headline" in prompt
        return json.dumps(
            [{"type": "set_text", "node_id": "headline", "payload": {"text": "Promo Akhir Tahun"}}]
        )

    ops = patch_from_llm(doc, "ganti headline", fake_complete)
    for op in ops:
        apply_op(doc, op)
    assert find_node(doc["nodes"], "headline")["text"] == "Promo Akhir Tahun"

    def bad_complete(prompt: str) -> str:
        return "bukan json"

    with pytest.raises(DesignError):
        patch_from_llm(doc, "ganti headline", bad_complete)

    def hostile_complete(prompt: str) -> str:
        return json.dumps(
            [
                {
                    "type": "set_text",
                    "node_id": "headline",
                    "payload": {"text": "<script>alert(1)</script>"},
                }
            ]
        )

    with pytest.raises(DesignError):
        patch_from_llm(doc, "ganti headline", hostile_complete)
    ops = patch_from_llm(doc, "besarkan harga", None)
    assert ops[0]["type"] == "set_style"


# -- export --------------------------------------------------------------


def test_export_png_is_a_real_png():
    data = export_png(_banner_doc())
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(data) > 100


def test_export_pdf_is_a_real_pdf():
    data = export_pdf(_banner_doc())
    assert data.startswith(b"%PDF-1.4")
    assert data.rstrip().endswith(b"%%EOF")
    assert b"Warung Sari" in data
    assert b"<script" not in data
