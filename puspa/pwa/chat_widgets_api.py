"""HTTP layer for the P-14 interactive widgets in Chat (plan section 12.10).

Plain handler functions over :mod:`puspa.pwa.chat_widgets`, plus
:func:`register_routes`, which mounts them on a FastAPI app.
This module deliberately does not import ``puspa.pwa.app``: the
coordinator wires it into ``create_app`` later with a few lines::

    from puspa.pwa import chat_widgets_api
    chat_widgets_api.register_routes(app, need_user)

``need_user`` is the app's own login resolver (``Request`` ->
user id or None); it is passed in rather than reimplemented
here, so login behaviour stays in exactly one place.

Widgets ride on the in-memory chat store (``app.state.chat``):
posting a widget appends a ``"puspa"`` message carrying the
widget, so the Chat screen shows it in the thread like any
other message. Interaction endpoints are stateless: the client
sends the widget it shows plus the user's input, the server
validates and computes, and the new state comes back as a
fresh chat message (or, for the calculator, as a bare result).
No new table, no migration.

Contract (all responses are JSON; all routes require login):

- ``POST /api/chat/widgets`` (JSON: ``{"text": ..., "widget":
  {...}}``) -> the appended message, widget validated first.
  400 for a bad widget (unknown kind, bad formula, a cycle in
  a diagram, simulation prices that do not match the
  simulator).
- ``POST /api/chat/widgets/compute`` (JSON: ``{"widget": ...,
  "values": {...}}``) -> ``{"result": n, "formatted": "..."}``,
  the calculator result for the given values. 400 for a bad
  widget, a missing value, or an out-of-range value.
- ``POST /api/chat/widgets/submit`` (JSON: ``{"widget": ...,
  "answers": {...}}``) -> the appended message carrying the
  validated form answers as its text. 400 for a missing
  required field or an answer outside the field's options.
- ``POST /api/chat/widgets/simulate`` (JSON: ``{"widget": ...,
  "action": {...}}``) -> the appended message carrying the
  widget after the step (``advance_day`` / ``pay_bill`` /
  ``buy_from_store``). 400 for a bad action (paying an already
  paid bill, advancing past day 30, an unknown store).
- ``GET /api/chat/widgets/price-compare`` -> the simulated
  phone prices as a comparison widget in a new chat message.
- ``GET /api/chat/widgets/simulation`` -> a fresh day-15
  simulation widget in a new chat message.
- ``401 {"error": "belum masuk"}`` when not logged in.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ..i18n.server_lines import render as render_server_line
from .chat_widgets import (
    append_widget_message,
    compute_calculator,
    format_widget_value,
    price_compare_widget,
    simulate_step,
    simulation_widget,
    validate_form_submission,
    validate_widget,
)

__all__ = [
    "compute_widget",
    "get_price_compare_widget",
    "get_simulation_widget",
    "post_widget",
    "register_routes",
    "simulate_widget",
    "submit_widget_form",
]


def _not_logged_in():
    return JSONResponse({"error": render_server_line("error.not_logged_in")}, status_code=401)


def post_widget(user_id: str, *, body: dict, chat_state=None) -> dict:
    """Validate a widget and append it as a ``"puspa"`` chat message.

    ``chat_state`` is the app's in-memory chat store
    (``app.state.chat``); a fresh dict when none is passed, for
    tests. Raises ``ValueError`` (400) for a bad body or widget.
    """
    if not isinstance(body, dict):
        raise ValueError("widget body must be a JSON object")
    text = body.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("text must be a non-empty string")
    widget = validate_widget(body.get("widget"))
    store = chat_state if chat_state is not None else {}
    return append_widget_message(store, user_id, text, widget)


def compute_widget(body: dict) -> dict:
    """Compute a calculator widget's result for the given values.

    Raises ``ValueError`` (400) for a bad widget, a missing
    value, or an out-of-range value. The client evaluates the
    same formula locally for the live result as the user types;
    this endpoint is the authoritative check the bot uses when
    it generates the widget and the tests use to pin the maths.
    """
    if not isinstance(body, dict):
        raise ValueError("compute body must be a JSON object")
    widget = validate_widget(body.get("widget"))
    if widget.get("kind") != "calculator":
        raise ValueError("compute needs a calculator widget")
    result = compute_calculator(widget, body.get("values"))
    return {"result": result, "formatted": format_widget_value(result, widget["value_format"])}


def submit_widget_form(user_id: str, *, body: dict, chat_state=None) -> dict:
    """Validate a form submission and post the answers as a chat message.

    Raises ``ValueError`` (400) for a bad widget, a missing
    required field, or an answer outside the field's options.
    The message text lists the answers (``label: value``), so
    the thread keeps a readable record of what the user sent.
    """
    if not isinstance(body, dict):
        raise ValueError("submit body must be a JSON object")
    widget = validate_widget(body.get("widget"))
    if widget.get("kind") != "form":
        raise ValueError("submit needs a form widget")
    answers = validate_form_submission(widget, body.get("answers"))
    lines = []
    for field in widget["fields"]:
        answer = answers.get(field["id"])
        shown = "—" if answer is None else str(answer)
        lines.append(f"{field['label']}: {shown}")
    text = f"Formulir '{widget['title']}' terkirim: " + "; ".join(lines)
    store = chat_state if chat_state is not None else {}
    return append_widget_message(store, user_id, text, widget)


def _simulate_message_text(widget: dict, action: dict, outcome: dict) -> str:
    """The chat text for one simulation step, from its outcome."""
    action_type = action.get("type")
    if action_type == "advance_day":
        day = widget["day"]
        prices = ", ".join(
            f"{store} Rp{price:,}".replace(",", ".") for store, price in widget["prices"].items()
        )
        return f"Hari simulasi {day}: {prices}."
    if action_type == "pay_bill":
        return f"Tagihan {widget['bill_id']} lunas (Rp{widget['bill']['amount']:,}).".replace(
            ",", "."
        )
    if action_type == "buy_from_store":
        return (
            f"Beli dari {outcome['store']} seharga "
            f"Rp{outcome['price']:,} (harga simulasi, hari {outcome['day']}).".replace(",", ".")
        )
    return "Langkah simulasi selesai."


def simulate_widget(user_id: str, *, body: dict, chat_state=None) -> dict:
    """Apply one simulation action and post the new widget as a chat message.

    The client sends the widget it shows plus the action; the
    server re-derives the world from the widget's day and bill
    state, applies the action, and appends a message carrying
    the rebuilt widget. Raises ``ValueError`` (400) for a bad
    widget or action (paying an already paid bill, advancing
    past day 30, an unknown store).
    """
    if not isinstance(body, dict):
        raise ValueError("simulate body must be a JSON object")
    widget = validate_widget(body.get("widget"))
    if widget.get("kind") != "simulation":
        raise ValueError("simulate needs a simulation widget")
    action = body.get("action")
    if not isinstance(action, dict):
        raise ValueError("action must be an object")
    new_widget, outcome = simulate_step(widget, action)
    text = _simulate_message_text(new_widget, action, outcome)
    store = chat_state if chat_state is not None else {}
    return append_widget_message(store, user_id, text, new_widget)


def get_price_compare_widget(
    user_id: str, *, product_id: str = "phone-x1", chat_state=None
) -> dict:
    """Append the simulator's phone prices as a comparison widget.

    One row per store with the simulated price — the prices are
    computed by the world simulator, not typed in by hand, and
    the widget title says they are simulated, not live.
    """
    widget = price_compare_widget(product_id=product_id)
    store = chat_state if chat_state is not None else {}
    return append_widget_message(store, user_id, widget["title"], widget)


def get_simulation_widget(user_id: str, *, chat_state=None) -> dict:
    """Append a fresh day-15 simulation widget as a chat message."""
    widget = simulation_widget()
    store = chat_state if chat_state is not None else {}
    return append_widget_message(store, user_id, widget["title"], widget)


def register_routes(app: FastAPI, need_user) -> FastAPI:
    """Mount the P-14 chat-widget routes on ``app`` using its login resolver."""

    @app.post("/api/chat/widgets")
    async def post_widget_endpoint(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        try:
            body = await request.json()
            return post_widget(user_id, body=body, chat_state=request.app.state.chat)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.post("/api/chat/widgets/compute")
    async def compute_widget_endpoint(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        try:
            body = await request.json()
            return compute_widget(body)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.post("/api/chat/widgets/submit")
    async def submit_widget_endpoint(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        try:
            body = await request.json()
            return submit_widget_form(user_id, body=body, chat_state=request.app.state.chat)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.post("/api/chat/widgets/simulate")
    async def simulate_widget_endpoint(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        try:
            body = await request.json()
            return simulate_widget(user_id, body=body, chat_state=request.app.state.chat)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.get("/api/chat/widgets/price-compare")
    def get_price_compare_widget_endpoint(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        try:
            return get_price_compare_widget(user_id, chat_state=request.app.state.chat)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.get("/api/chat/widgets/simulation")
    def get_simulation_widget_endpoint(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        return get_simulation_widget(user_id, chat_state=request.app.state.chat)

    return app
