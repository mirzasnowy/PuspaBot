"""Rich cards for the Chat screen (P-12, plan section 12.10).

Chat can show four kinds of rich card, all as declarative JSON
payloads attached to a chat message under its ``card`` key —
data describes the card, the renderer decides how to show it
(the same split as the B7 mockup doc: the payload is data, not
code). The four kinds:

- ``draft`` — a draft email or message (B13 draft text): copy
  the body, or open it in Mail (``mailto:``) when it is an email.
- ``options`` — a question with 2..8 tappable options; tapping
  one sends its label as a chat message. A choice, not a form:
  the interactive components (calculator, slider, form) are
  P-14, not this.
- ``chart`` — a small data chart built from the G28 Insights
  report (``activity_per_day``): one metric per day, rendered
  as bars. No new dependency; the PWA draws plain SVG.
- ``map`` — a map with markers and a route, drawn client-side
  from free OpenStreetMap tiles (``tile.openstreetmap.org``,
  no API key). The tile grid maths live here (``map_tiles``)
  and are mirrored in ``apps/pwa/src/state/chatCards.ts`` so
  the server and the client agree on which tiles to fetch.

Cards ride on the in-memory chat store (``app.state.chat``):
:func:`append_card_message` appends a ``"puspa"`` message with
its card, the same list ``POST /api/chat`` appends to. No new
database table: drafts already live in the B13 drafting tables
and Insights data already lives in the usage ledger. No
migration is needed for P-12.

Honest limit, stated here and in ``docs/chat-cards-p12.md``:
the map tiles load client-side from OpenStreetMap, and this
VM has no live browser to prove a tile really renders — the
verification here is the tile URL maths plus the rendered
template, not a loaded tile in a real browser. No map
screenshot is claimed.
"""

from __future__ import annotations

import math
import time
from urllib.parse import quote

__all__ = [
    "CHART_METRICS",
    "OSM_TILE_URL",
    "append_card_message",
    "build_card",
    "chart_card",
    "chart_card_from_insights",
    "draft_card",
    "draft_card_from_draft",
    "map_card",
    "map_tiles",
    "options_card",
    "project_point",
    "tile_url",
    "validate_card",
]

# Free OpenStreetMap tile server (no API key). The client fetches
# these <img> tags directly; the server never proxies tile bytes.
OSM_TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
OSM_MAX_ZOOM = 19
TILE_PX = 256

CHART_METRICS = ("turns", "tokens", "cost_idr")

_DRAFT_CHANNELS = ("email", "message")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _require_string(value, field: str) -> str:
    _require(isinstance(value, str) and value.strip(), f"{field} must be a non-empty string")
    return value


def _optional_string(value, field: str) -> str | None:
    if value is None:
        return None
    _require(isinstance(value, str), f"{field} must be a string")
    return value


def _require_number(value, field: str) -> float:
    _require(
        isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value),
        f"{field} must be a finite number",
    )
    return float(value)


def _validate_lat_lon(lat, lon, field: str) -> tuple[float, float]:
    lat = _require_number(lat, f"{field}.lat")
    lon = _require_number(lon, f"{field}.lon")
    _require(-90 <= lat <= 90, f"{field}.lat must be between -90 and 90")
    _require(-180 <= lon <= 180, f"{field}.lon must be between -180 and 180")
    return lat, lon


# -- draft card ----------------------------------------------------------


def draft_card(
    *,
    body: str,
    title: str | None = None,
    recipient: str | None = None,
    subject: str | None = None,
    channel: str = "message",
    draft_id: str | None = None,
) -> dict:
    """One draft card: the draft body, copyable, openable in Mail.

    ``channel`` is ``"email"`` (``mailto:`` link when a recipient
    is set) or ``"message"`` (copy only). The mailto link carries
    the subject and body only when they are non-empty, so a draft
    without a subject does not get a guessed one.
    """
    body = _require_string(body, "draft body")
    title = _optional_string(title, "draft title")
    recipient = _optional_string(recipient, "draft recipient")
    subject = _optional_string(subject, "draft subject")
    draft_id = _optional_string(draft_id, "draft_id")
    _require(channel in _DRAFT_CHANNELS, "draft channel must be 'email' or 'message'")
    card: dict = {
        "kind": "draft",
        "title": title or "Draft",
        "body": body,
        "channel": channel,
    }
    if draft_id is not None:
        card["draft_id"] = draft_id
    if recipient is not None:
        card["recipient"] = recipient
    if subject is not None:
        card["subject"] = subject
    if channel == "email" and recipient and "@" in recipient:
        query = []
        if subject:
            query.append(f"subject={quote(subject)}")
        query.append(f"body={quote(body)}")
        card["mailto"] = f"mailto:{recipient}?{'&'.join(query)}"
    return card


def draft_card_from_draft(draft: dict) -> dict:
    """Build a draft card from one B13 draft dict (``Draft.to_dict``).

    The B13 draft has no subject field, so the card carries none:
    the mailto link opens Mail with the body filled in and the
    user writes the subject. ``kind == "email"`` maps to the
    email channel; ``"message"`` and ``"document"`` map to the
    message channel (copy only, no mailto).
    """
    _require(isinstance(draft, dict), "draft must be a dict")
    text = draft.get("text")
    _require(isinstance(text, str) and text.strip(), "draft text must be a non-empty string")
    kind = draft.get("kind")
    channel = "email" if kind == "email" else "message"
    return draft_card(
        body=text,
        title=draft.get("brief") or "Draft",
        recipient=draft.get("recipient"),
        channel=channel,
        draft_id=draft.get("id"),
    )


# -- options card --------------------------------------------------------


def options_card(*, question: str, options: list[dict]) -> dict:
    """One options card: a question with 2..8 tappable options.

    Each option is ``{"id": ..., "label": ...}`` with an optional
    ``"detail"`` line. Option ids must be unique: the client
    sends the tapped option's label back as a chat message, and
    a duplicate id would make two options indistinguishable.
    """
    question = _require_string(question, "options question")
    _require(isinstance(options, list), "options must be a list")
    _require(2 <= len(options) <= 8, "options must hold 2 to 8 options")
    seen_ids: set[str] = set()
    cleaned = []
    for option in options:
        _require(isinstance(option, dict), "each option must be an object")
        option_id = _require_string(option.get("id"), "option id")
        label = _require_string(option.get("label"), "option label")
        _require(option_id not in seen_ids, f"duplicate option id {option_id!r}")
        seen_ids.add(option_id)
        entry: dict = {"id": option_id, "label": label}
        detail = _optional_string(option.get("detail"), "option detail")
        if detail is not None:
            entry["detail"] = detail
        cleaned.append(entry)
    return {"kind": "options", "question": question, "options": cleaned}


# -- chart card ----------------------------------------------------------


def chart_card(*, title: str, metric: str, points: list[dict]) -> dict:
    """One chart card: one metric per labelled point, drawn as bars.

    ``metric`` is one of the G28 ``activity_per_day`` fields
    (``turns``, ``tokens``, ``cost_idr``); the points are
    ``{"label": ..., "value": ...}`` with finite, non-negative
    values — a chart cannot draw a negative bar, so negatives
    are refused instead of clamped silently.
    """
    title = _require_string(title, "chart title")
    _require(metric in CHART_METRICS, f"chart metric must be one of {CHART_METRICS}")
    _require(isinstance(points, list) and points, "chart points must be a non-empty list")
    _require(len(points) <= 90, "chart points must hold at most 90 points")
    cleaned = []
    for point in points:
        _require(isinstance(point, dict), "each chart point must be an object")
        label = _require_string(point.get("label"), "chart point label")
        value = _require_number(point.get("value"), "chart point value")
        _require(value >= 0, "chart point value must not be negative")
        cleaned.append({"label": label, "value": value})
    return {"kind": "chart", "title": title, "metric": metric, "points": cleaned}


def chart_card_from_insights(report: dict, *, metric: str = "cost_idr") -> dict:
    """Build a chart card from one G28 Insights report.

    Reads ``activity_per_day`` (every day in the window,
    including zero days, so the chart has no gaps) and the
    report's ``window_days`` for the title. The metric defaults
    to cost, the number the Insights screen leads with.
    """
    _require(isinstance(report, dict), "insights report must be a dict")
    _require(metric in CHART_METRICS, f"chart metric must be one of {CHART_METRICS}")
    days = report.get("activity_per_day")
    _require(isinstance(days, list) and days, "insights report has no activity_per_day")
    metric_titles = {
        "turns": "Turns per day",
        "tokens": "Tokens per day",
        "cost_idr": "Cost per day (Rp)",
    }
    window = report.get("window_days")
    title = metric_titles[metric]
    if isinstance(window, int):
        title = f"{title} — last {window} days"
    return chart_card(
        title=title,
        metric=metric,
        points=[{"label": day.get("date"), "value": day.get(metric)} for day in days],
    )


# -- map card ------------------------------------------------------------


def _lon_to_tile_x(lon: float, zoom: int) -> float:
    return (lon + 180.0) / 360.0 * (1 << zoom)


def _lat_to_tile_y(lat: float, zoom: int) -> float:
    # Standard slippy-map formula (Mercator); the latitude is
    # clamped to the Mercator range so the poles cannot produce
    # an infinite tile row.
    clamped = max(min(lat, 85.05112878), -85.05112878)
    radians = math.radians(clamped)
    return (
        (1.0 - math.log(math.tan(radians) + 1.0 / math.cos(radians)) / math.pi) / 2.0 * (1 << zoom)
    )


def tile_url(zoom: int, x: int, y: int) -> str:
    """One OSM tile URL. ``x`` wraps around the world; ``y`` is
    clamped to the valid row range, so a bad row can never
    request a tile the server does not have."""
    _require(isinstance(zoom, int) and not isinstance(zoom, bool), "zoom must be an integer")
    _require(0 <= zoom <= OSM_MAX_ZOOM, f"zoom must be between 0 and {OSM_MAX_ZOOM}")
    tiles = 1 << zoom
    return OSM_TILE_URL.format(z=zoom, x=x % tiles, y=max(0, min(y, tiles - 1)))


def map_card(
    *,
    center: dict,
    zoom: int,
    title: str | None = None,
    markers: list[dict] | None = None,
    route: list[dict] | None = None,
) -> dict:
    """One map card: OSM tiles with markers and a route.

    ``center`` is ``{"lat": ..., "lon": ...}``; ``zoom`` is an
    OSM zoom level (0..19). At least one marker or a route of
    two or more points is required — a map with nothing on it
    is a blank tile grid, so it is refused instead of shown.
    Markers carry an optional ``label``; route points are plain
    ``{"lat": ..., "lon": ...}`` positions in order.
    """
    lat, lon = (
        _validate_lat_lon(center.get("lat"), center.get("lon"), "map center")
        if isinstance(center, dict)
        else (None, None)
    )
    _require(lat is not None, "map center must be an object with lat and lon")
    _require(isinstance(zoom, int) and not isinstance(zoom, bool), "zoom must be an integer")
    _require(0 <= zoom <= OSM_MAX_ZOOM, f"zoom must be between 0 and {OSM_MAX_ZOOM}")
    title = _optional_string(title, "map title")
    cleaned_markers = []
    for marker in markers or []:
        _require(isinstance(marker, dict), "each marker must be an object")
        mlat, mlon = _validate_lat_lon(marker.get("lat"), marker.get("lon"), "marker")
        entry: dict = {"lat": mlat, "lon": mlon}
        label = _optional_string(marker.get("label"), "marker label")
        if label is not None:
            entry["label"] = label
        cleaned_markers.append(entry)
    cleaned_route = []
    for point in route or []:
        _require(isinstance(point, dict), "each route point must be an object")
        plat, plon = _validate_lat_lon(point.get("lat"), point.get("lon"), "route point")
        cleaned_route.append({"lat": plat, "lon": plon})
    _require(
        len(cleaned_route) != 1,
        "a route of exactly one point draws nothing; use a marker instead",
    )
    _require(
        cleaned_markers or len(cleaned_route) >= 2,
        "map needs at least one marker or a route of 2+ points",
    )
    card: dict = {
        "kind": "map",
        "title": title or "Map",
        "center": {"lat": lat, "lon": lon},
        "zoom": zoom,
        "markers": cleaned_markers,
        "route": cleaned_route,
    }
    return card


def map_tiles(card: dict, *, width_px: int, height_px: int) -> list[dict]:
    """The OSM tiles covering a ``width_px`` x ``height_px`` view
    centred on the card's centre, each with its pixel offset
    from the view's top-left corner and its tile URL.

    Pure slippy-map maths: the centre in tile units times 256
    is the centre pixel; the tiles whose 256px squares intersect
    the view are the ones to fetch. The client renders exactly
    these tiles at exactly these offsets.
    """
    validate_card(card)
    _require(
        isinstance(width_px, int) and not isinstance(width_px, bool) and 1 <= width_px <= 4096,
        "width_px must be an integer between 1 and 4096",
    )
    _require(
        isinstance(height_px, int) and not isinstance(height_px, bool) and 1 <= height_px <= 4096,
        "height_px must be an integer between 1 and 4096",
    )
    zoom = card["zoom"]
    centre_x = _lon_to_tile_x(card["center"]["lon"], zoom) * TILE_PX
    centre_y = _lat_to_tile_y(card["center"]["lat"], zoom) * TILE_PX
    left = centre_x - width_px / 2
    top = centre_y - height_px / 2
    first_x = math.floor(left / TILE_PX)
    first_y = math.floor(top / TILE_PX)
    last_x = math.floor((left + width_px - 1) / TILE_PX)
    last_y = math.floor((top + height_px - 1) / TILE_PX)
    tiles = []
    for x in range(first_x, last_x + 1):
        for y in range(first_y, last_y + 1):
            tiles.append(
                {
                    "z": zoom,
                    "x": x,
                    "y": y,
                    "url": tile_url(zoom, x, y),
                    "left_px": x * TILE_PX - left,
                    "top_px": y * TILE_PX - top,
                }
            )
    return tiles


def project_point(card: dict, *, lat: float, lon: float, width_px: int, height_px: int) -> dict:
    """Pixel position of one lat/lon inside the map view.

    The same centre-pixel maths as :func:`map_tiles`: the
    client pins markers and draws the route polyline at these
    coordinates over the tile grid.
    """
    validate_card(card)
    plat, plon = _validate_lat_lon(lat, lon, "point")
    zoom = card["zoom"]
    centre_x = _lon_to_tile_x(card["center"]["lon"], zoom) * TILE_PX
    centre_y = _lat_to_tile_y(card["center"]["lat"], zoom) * TILE_PX
    return {
        "x": _lon_to_tile_x(plon, zoom) * TILE_PX - (centre_x - width_px / 2),
        "y": _lat_to_tile_y(plat, zoom) * TILE_PX - (centre_y - height_px / 2),
    }


# -- validation + chat seam ----------------------------------------------


def validate_card(card: dict) -> dict:
    """Validate one card payload and return its cleaned copy.

    The ``kind`` field discriminates: an unknown kind is refused
    (400 at the API), and each kind is rebuilt through its own
    builder, so a card that passes here is exactly the shape the
    renderer expects — no extra keys survive.
    """
    _require(isinstance(card, dict), "card must be a JSON object")
    kind = card.get("kind")
    if kind == "draft":
        return draft_card(
            body=card.get("body"),
            title=card.get("title"),
            recipient=card.get("recipient"),
            subject=card.get("subject"),
            channel=card.get("channel") or "message",
            draft_id=card.get("draft_id"),
        )
    if kind == "options":
        return options_card(question=card.get("question"), options=card.get("options"))
    if kind == "chart":
        return chart_card(
            title=card.get("title"),
            metric=card.get("metric"),
            points=card.get("points"),
        )
    if kind == "map":
        return map_card(
            center=card.get("center"),
            zoom=card.get("zoom"),
            title=card.get("title"),
            markers=card.get("markers"),
            route=card.get("route"),
        )
    raise ValueError(f"unknown card kind {kind!r}")


def build_card(kind: str, **fields) -> dict:
    """Build one card by kind (``draft`` / ``options`` / ``chart`` / ``map``)."""
    return validate_card({"kind": kind, **fields})


def append_card_message(
    chat_state: dict,
    user_id: str,
    text: str,
    card: dict,
    *,
    sender: str = "puspa",
    created_at: float | None = None,
) -> dict:
    """Append a message carrying a card to the in-memory chat store.

    The message is the same shape ``POST /api/chat`` appends
    (``sender`` / ``text`` / ``created_at``) plus the validated
    ``card`` payload. The agent's turn loop calls this to put a
    card in front of the user; the HTTP layer in
    ``chat_cards_api`` is the same seam over HTTP.
    """
    _require(isinstance(chat_state, dict), "chat_state must be a dict")
    _require_string(user_id, "user_id")
    _require_string(text, "message text")
    cleaned = validate_card(card)
    message = {
        "sender": sender,
        "text": text,
        "created_at": created_at if created_at is not None else time.time(),
        "card": cleaned,
    }
    chat_state.setdefault(user_id, []).append(message)
    return message
