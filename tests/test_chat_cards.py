"""Tests for the P-12 rich cards in Chat (``puspa.pwa.chat_cards``).

Pure unit tests (``unit`` marker: no database, no network):
the four card builders and their validation, the OSM tile
maths, and the in-memory chat seam. The HTTP layer lives in
``tests/test_chat_cards_api.py``.

No live tile is fetched here: the tile URL maths are checked
against the slippy-map formulas, and the honest limit (no
real browser in this VM to prove a tile renders) is recorded
in ``docs/chat-cards-p12.md``.
"""

from __future__ import annotations

import itertools
import math

import pytest

from puspa.pwa.chat_cards import (
    append_card_message,
    build_card,
    chart_card,
    chart_card_from_insights,
    draft_card,
    draft_card_from_draft,
    map_card,
    map_tiles,
    options_card,
    project_point,
    tile_url,
    validate_card,
)

pytestmark = pytest.mark.unit


# -- draft card ----------------------------------------------------------


def test_draft_card_email_builds_mailto():
    card = draft_card(
        body="Halo Sari,\n\nBarang sudah sampai.",
        title="Kabar barang",
        recipient="sari@contoh.id",
        subject="Barang sampai",
        channel="email",
        draft_id="d-1",
    )
    assert card["kind"] == "draft"
    assert card["channel"] == "email"
    assert card["mailto"].startswith("mailto:sari@contoh.id?")
    assert "subject=Barang%20sampai" in card["mailto"]
    assert "body=Halo%20Sari" in card["mailto"]


def test_draft_card_message_has_no_mailto():
    card = draft_card(body="halo", channel="message")
    assert "mailto" not in card
    assert card["title"] == "Draft"


def test_draft_card_refuses_empty_body_and_bad_channel():
    with pytest.raises(ValueError, match="non-empty string"):
        draft_card(body="   ")
    with pytest.raises(ValueError, match="channel"):
        draft_card(body="halo", channel="letter")


def test_draft_card_from_b13_draft():
    draft = {
        "id": "d-9",
        "kind": "email",
        "brief": "kabari Sari barang sampai",
        "text": "Halo Sari, barang sudah sampai.",
        "recipient": "sari@contoh.id",
        "status": "draft",
    }
    card = draft_card_from_draft(draft)
    assert card["kind"] == "draft"
    assert card["channel"] == "email"
    assert card["draft_id"] == "d-9"
    assert card["body"] == "Halo Sari, barang sudah sampai."
    assert card["mailto"].startswith("mailto:sari@contoh.id?")
    # A message draft never gets a mailto link.
    message = draft_card_from_draft({**draft, "kind": "message"})
    assert message["channel"] == "message"
    assert "mailto" not in message


def test_draft_card_from_draft_refuses_empty_text():
    with pytest.raises(ValueError, match="non-empty string"):
        draft_card_from_draft({"kind": "email", "text": "  "})


# -- options card --------------------------------------------------------


def test_options_card_round_trip():
    card = options_card(
        question="Mau lanjut yang mana?",
        options=[
            {"id": "a", "label": "Kirim email", "detail": "ke Sari"},
            {"id": "b", "label": "Tunggu besok"},
        ],
    )
    assert card["kind"] == "options"
    assert [o["id"] for o in card["options"]] == ["a", "b"]
    assert card["options"][0]["detail"] == "ke Sari"


def test_options_card_refuses_too_few_duplicate_or_blank():
    with pytest.raises(ValueError, match="2 to 8"):
        options_card(question="q", options=[{"id": "a", "label": "satu"}])
    with pytest.raises(ValueError, match="duplicate option id"):
        options_card(
            question="q",
            options=[{"id": "a", "label": "satu"}, {"id": "a", "label": "dua"}],
        )
    with pytest.raises(ValueError, match="non-empty string"):
        options_card(
            question="q",
            options=[{"id": "a", "label": "satu"}, {"id": "b", "label": "  "}],
        )


# -- chart card ----------------------------------------------------------


def test_chart_card_from_insights_uses_activity_per_day():
    report = {
        "window_days": 7,
        "activity_per_day": [
            {"date": "2026-11-15", "turns": 2, "tokens": 100, "cost_idr": 500},
            {"date": "2026-11-16", "turns": 0, "tokens": 0, "cost_idr": 0},
            {"date": "2026-11-17", "turns": 3, "tokens": 250, "cost_idr": 1200},
        ],
    }
    card = chart_card_from_insights(report, metric="cost_idr")
    assert card["kind"] == "chart"
    assert card["metric"] == "cost_idr"
    assert card["points"] == [
        {"label": "2026-11-15", "value": 500},
        {"label": "2026-11-16", "value": 0},
        {"label": "2026-11-17", "value": 1200},
    ]
    assert "7 days" in card["title"]
    turns = chart_card_from_insights(report, metric="turns")
    assert turns["points"][0] == {"label": "2026-11-15", "value": 2}


def test_chart_card_refuses_negative_values_and_unknown_metric():
    with pytest.raises(ValueError, match="must not be negative"):
        chart_card(title="t", metric="turns", points=[{"label": "a", "value": -1}])
    with pytest.raises(ValueError, match="metric"):
        chart_card(title="t", metric="rupiah", points=[{"label": "a", "value": 1}])
    with pytest.raises(ValueError, match="non-empty list"):
        chart_card(title="t", metric="turns", points=[])


# -- map card ------------------------------------------------------------


def _jakarta_map(**overrides):
    body = {
        "center": {"lat": -6.2, "lon": 106.8},
        "zoom": 15,
        "markers": [{"lat": -6.2, "lon": 106.8, "label": "Kantor"}],
        "route": [
            {"lat": -6.2, "lon": 106.8},
            {"lat": -6.21, "lon": 106.81},
        ],
    }
    body.update(overrides)
    return body


def test_map_card_keeps_markers_and_route():
    card = map_card(**_jakarta_map())
    assert card["kind"] == "map"
    assert card["center"] == {"lat": -6.2, "lon": 106.8}
    assert card["zoom"] == 15
    assert card["markers"][0]["label"] == "Kantor"
    assert len(card["route"]) == 2


def test_map_card_refuses_bad_coordinates_and_empty_map():
    with pytest.raises(ValueError, match="between -90 and 90"):
        map_card(center={"lat": 91, "lon": 0}, zoom=10, markers=[{"lat": 0, "lon": 0}])
    with pytest.raises(ValueError, match="between 0 and 19"):
        map_card(center={"lat": 0, "lon": 0}, zoom=20, markers=[{"lat": 0, "lon": 0}])
    with pytest.raises(ValueError, match="at least one marker or a route"):
        map_card(center={"lat": 0, "lon": 0}, zoom=10)
    with pytest.raises(ValueError, match="exactly one point"):
        map_card(
            center={"lat": 0, "lon": 0},
            zoom=10,
            route=[{"lat": 0, "lon": 0}],
        )


def test_tile_url_wraps_x_and_clamps_y():
    assert tile_url(1, 0, 0) == "https://tile.openstreetmap.org/1/0/0.png"
    # x wraps around the world; y clamps to the valid row range.
    assert tile_url(1, 2, 0) == "https://tile.openstreetmap.org/1/0/0.png"
    assert tile_url(1, 0, 5) == "https://tile.openstreetmap.org/1/0/1.png"
    with pytest.raises(ValueError, match="zoom"):
        tile_url(20, 0, 0)


def test_map_tiles_cover_the_view_around_the_centre():
    card = map_card(**_jakarta_map())
    tiles = map_tiles(card, width_px=512, height_px=512)
    # A 512x512 view is 2 tiles across when the centre lands on a
    # tile boundary and 3 when it does not — either way the tiles
    # must cover the whole view with no gaps.
    assert len(tiles) in (4, 6, 9)
    assert all(t["z"] == 15 for t in tiles)
    assert all(t["url"].startswith("https://tile.openstreetmap.org/15/") for t in tiles)
    # The tiles tile the view with no gaps: left/top offsets step by 256.
    xs = sorted({t["left_px"] for t in tiles})
    ys = sorted({t["top_px"] for t in tiles})
    assert all(b - a == 256 for a, b in itertools.pairwise(xs))
    assert all(b - a == 256 for a, b in itertools.pairwise(ys))
    # The tile rects together cover the whole 512x512 view.
    assert min(xs) <= 0 and max(xs) + 256 >= 512
    assert min(ys) <= 0 and max(ys) + 256 >= 512
    # The centre pixel sits in the middle of the view.
    centre = project_point(card, lat=-6.2, lon=106.8, width_px=512, height_px=512)
    assert centre == {"x": 256.0, "y": 256.0}


def test_project_point_moves_with_lat_lon():
    card = map_card(**_jakarta_map())
    here = project_point(card, lat=-6.2, lon=106.8, width_px=512, height_px=512)
    east = project_point(card, lat=-6.2, lon=106.81, width_px=512, height_px=512)
    north = project_point(card, lat=-6.19, lon=106.8, width_px=512, height_px=512)
    assert east["x"] > here["x"] and east["y"] == pytest.approx(here["y"])
    assert north["y"] < here["y"] and north["x"] == pytest.approx(here["x"])
    # One degree of longitude at zoom 15 is 2^15 / 360 * 256 px.
    assert east["x"] - here["x"] == pytest.approx((1 << 15) / 360 * 256 * 0.01, rel=1e-6)


def test_map_tiles_refuse_bad_viewport():
    card = map_card(**_jakarta_map())
    with pytest.raises(ValueError, match="width_px"):
        map_tiles(card, width_px=0, height_px=256)


# -- validation + chat seam ----------------------------------------------


def test_validate_card_rejects_unknown_kind_and_extra_keys():
    with pytest.raises(ValueError, match="unknown card kind"):
        validate_card({"kind": "calculator"})
    cleaned = validate_card(
        {"kind": "draft", "body": "halo", "channel": "message", "surprise": True}
    )
    assert "surprise" not in cleaned


def test_build_card_dispatches_by_kind():
    assert (
        build_card(
            "options", question="q", options=[{"id": "a", "label": "x"}, {"id": "b", "label": "y"}]
        )["kind"]
        == "options"
    )


def test_append_card_message_stores_validated_card():
    store: dict = {}
    message = append_card_message(
        store,
        "user-1",
        "Ini draftnya:",
        {"kind": "draft", "body": "halo", "channel": "message"},
        created_at=1700000000.0,
    )
    assert message["sender"] == "puspa"
    assert message["text"] == "Ini draftnya:"
    assert message["created_at"] == 1700000000.0
    assert message["card"]["kind"] == "draft"
    assert store["user-1"] == [message]
    # A bad card appends nothing.
    with pytest.raises(ValueError, match="unknown card kind"):
        append_card_message(store, "user-1", "x", {"kind": "nope"})
    assert len(store["user-1"]) == 1


def test_append_card_message_uses_current_time_by_default():
    before = 1700000000.0
    message = append_card_message({}, "u", "t", {"kind": "draft", "body": "b"})
    assert message["created_at"] >= before
    assert message["created_at"] <= before + 1e9  # wall clock, not a fixed stamp
    assert math.isfinite(message["created_at"])
