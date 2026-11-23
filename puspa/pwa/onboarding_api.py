"""HTTP layer for onboarding + hints (G28).

Plain handler functions wrapping :mod:`puspa.onboarding.onboarding`
(the 4-step first-run flow) and :mod:`puspa.onboarding.hints`
(contextual one-time hints), plus :func:`register_routes`, which
mounts them on a FastAPI app. This module deliberately does not
import ``puspa.pwa.app``: the coordinator wires it into
``create_app`` later with a few lines::

    from puspa.pwa import onboarding_api
    onboarding_api.register_routes(app, need_user)

``need_user`` is the app's own login resolver (``Request`` ->
user id or None); it is passed in rather than reimplemented here,
so login behaviour stays in exactly one place. The existing
``POST /api/onboarding/waktu`` timing stub in ``app.py`` is
untouched; these routes are the real onboarding flow.

Contract (all responses are JSON; all routes require login):

- ``GET /api/onboarding`` -> the user's onboarding status, or
  ``{"started": false, "current_step": 0}`` when never started.
- ``POST /api/onboarding/start`` -> starts onboarding, returns the
  status (``{"started": true, ...}``). Idempotent: starting twice
  returns the existing status.
- ``POST /api/onboarding/step1`` ``{"name", "style",
  "example_message"?}`` -> step 1 (name + speaking style; the
  style must be one of ``santai`` / ``rapi`` / ``campur``).
- ``POST /api/onboarding/step2`` -> step 2 (notification test).
  Honest limit, inherited from the flow: Web Push is not built
  yet, so the test is recorded as NOT sent
  (``notification_sent`` is false) -- never faked as delivered.
- ``POST /api/onboarding/step3`` ``{"connector"?}`` -> step 3
  (connector choice; ``"nanti"`` by default).
- ``POST /api/onboarding/first-task`` ``{"event_type", "target"}``
  -> the guided first task. Honest limit, inherited from the
  flow: the price-compare / bill wedges are not built yet, so the
  task is a measured stub (``verified`` is false, with a reason);
  no price or bill result is ever invented.
- ``GET /api/onboarding/hints`` -> hints due right now:
  ``{"hints": [...]}``. The client reports the branch it just
  reached as query params -- ``screen``, ``first_visit``,
  ``approval_requested``, ``memory_used``, ``task_cost_idr``,
  ``billing_rows`` -- and the server returns only the unseen
  hints that branch makes due. Only branch facts are accepted;
  no other client data is stored.
- ``POST /api/onboarding/hints/{flag}/seen`` -> marks one hint
  seen (dismissed or shown); unknown flags are a 400.

Validation failures are 400 with ``{"error": ...}``; a step
called before onboarding started is also a 400. ``401
{"error": "belum masuk"}`` when not logged in.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ..i18n import DEFAULT_LOCALE
from ..i18n.server_lines import render as render_server_line
from ..onboarding import hints as hints_engine
from ..onboarding import onboarding as onboarding_core
from ..persona.switcher import (
    create_persona,
    persona_dir,
    set_active_persona,
    slugify_name,
    update_persona,
)

__all__ = [
    "dismiss_hint",
    "due_hints",
    "first_task",
    "get_status",
    "parse_hint_context",
    "register_routes",
    "start",
    "step1",
    "step2",
    "step3",
]


def _status_dict(status: dict | None) -> dict:
    if status is None:
        return {"started": False, "current_step": 0}
    return {"started": True, **status}


def get_status(user_id, dsn=None) -> dict:
    """Onboarding status, or ``{"started": false, ...}`` if never started."""
    return _status_dict(onboarding_core.get_status(user_id, dsn=dsn))


def start(user_id, dsn=None) -> dict:
    """Start onboarding (idempotent) and return the status."""
    return _status_dict(onboarding_core.start_onboarding(user_id, dsn=dsn))


def step1(user_id, name, style, example_message=None, dsn=None) -> dict:
    """Complete step 1: name + speaking style (+ optional examples)."""
    return _status_dict(
        onboarding_core.complete_step1(
            user_id, name, style, example_message=example_message, dsn=dsn
        )
    )


def step2(user_id, dsn=None) -> dict:
    """Complete step 2: notification test (honestly reported, see module docstring)."""
    return _status_dict(onboarding_core.complete_step2(user_id, dsn=dsn))


def step3(user_id, connector="nanti", dsn=None) -> dict:
    """Complete step 3: connector choice (``"nanti"`` by default)."""
    return _status_dict(onboarding_core.complete_step3(user_id, connector=connector, dsn=dsn))


def first_task(user_id, event_type, target, dsn=None) -> dict:
    """Run the guided first task (measured stub, see module docstring)."""
    return onboarding_core.run_first_task(user_id, event_type, target, dsn=dsn)


def due_hints(user_id, context=None, dsn=None) -> dict:
    """Hints due right now, as ``{"hints": [...]}``.

    Rendered in the user's stored language (COW-18): the switch in
    Settings flips these sentences. A locale that cannot be read
    falls back to Indonesian instead of failing the request.
    """
    try:
        from puspa.pwa.language import get_language

        locale = get_language(user_id, dsn=dsn)
    except Exception:
        locale = DEFAULT_LOCALE
    return {"hints": hints_engine.due_hints(user_id, context=context, dsn=dsn, locale=locale)}


def dismiss_hint(user_id, flag, dsn=None) -> dict:
    """Mark one hint seen. Raises ValueError for an unknown flag."""
    return hints_engine.mark_seen(user_id, flag, dsn=dsn)


def parse_hint_context(params: dict) -> dict:
    """Parse hint query params into a typed branch context.

    Accepts the flat string values a query string carries:
    ``"true"`` / ``"1"`` / ``"yes"`` (case-insensitive) for flags,
    integers for ``task_cost_idr`` / ``billing_rows``. Unknown keys
    are ignored; unparseable integers raise ValueError.
    """
    context: dict = {}
    if params.get("screen"):
        context["screen"] = str(params["screen"])
    for key in ("first_visit", "approval_requested", "memory_used"):
        if key in params:
            context[key] = str(params[key]).strip().lower() in ("true", "1", "yes")
    for key in ("task_cost_idr", "billing_rows"):
        if key in params and params[key] != "":
            try:
                context[key] = int(params[key])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{key} must be an integer, got {params[key]!r}") from exc
    return context


def _json_body(request_body) -> dict:
    if not isinstance(request_body, dict):
        raise ValueError("request body must be a JSON object")
    return request_body


#: What Puspa is used for (onboarding step 1): fixed choices, the
#: user picks one or more. The ids are stable; the labels are what
#: the screen shows and what the generated agent file records.
PURPOSES = (
    ("price_compare", "Bandingkan harga", "Cari harga termurah di Tokopedia, Shopee, dan Blibli."),
    ("pay_bills", "Bayar tagihan", "Bayar PLN, PDAM, pulsa, dan paket data tepat waktu."),
    ("research", "Riset & belajar", "Kumpulkan sumber dan susun ringkasan untuk skripsi atau kerja."),
    ("organize", "Atur jadwal & tugas", "Ingatkan deadline dan rapikan daftar tugas harian."),
    ("create", "Bikin konten & desain", "Susun draf tulisan dan mockup desain bersama tim agen."),
)

#: What the agent uses and accesses (onboarding step 3). This is
#: the transparency list: each source is named in plain words,
#: and the generated USER.md records exactly the sources the user
#: confirmed. Passwords and OTPs are never on this list: the agent
#: never reads sensitive fields.
DATA_SOURCES = (
    ("chat", "Pesan chat", "Apa yang kamu tulis ke Puspa di chat."),
    ("memory", "Memori & fakta", "Fakta yang kamu simpan atau Puspa pelajari; bisa diedit di Edit Pengetahuan."),
    ("connectors", "Konektor yang kamu sambungkan", "Gmail, Drive, atau Kalender — hanya yang kamu pilih di onboarding atau di layar Konektor."),
    ("receipts", "Struk & tagihan", "Foto struk dan tagihan yang kamu minta dicek atau dibayar."),
)

_STYLE_LINES = {
    "santai": "Bahasa sehari-hari, slang, singkat, hangat.",
    "rapi": "Bahasa baku yang sopan, kalimat lengkap.",
    "campur": "Campur bahasa Indonesia dan Inggris, informal.",
}


def build_agent_files(
    *,
    agent_name: str,
    user_name: str,
    style: str,
    purposes: list[str],
    needs: str,
    data_sources: list[str],
) -> dict[str, str]:
    """Render the agent's three Markdown files from onboarding answers.

    Pure function, no disk writes: the caller saves the result with
    ``create_persona`` / ``update_persona`` (the V2-25 persona
    files, ``SOUL.md`` / ``IDENTITY.md`` / ``USER.md``). Every
    answer the user gave appears in the files verbatim, so the
    generated file can be checked against the answers.
    """
    name = agent_name.strip() or "Puspa"
    user = user_name.strip()
    style_line = _STYLE_LINES.get(style, _STYLE_LINES["santai"])
    purpose_labels = [
        label for pid, label, _ in PURPOSES if pid in purposes
    ]
    source_labels = [
        label for sid, label, _ in DATA_SOURCES if sid in data_sources
    ]
    purposes_md = (
        "\n".join(f"- {label}" for label in purpose_labels)
        if purpose_labels
        else "- (belum dipilih)"
    )
    sources_md = (
        "\n".join(f"- {label}" for label in source_labels)
        if source_labels
        else "- (belum ada)"
    )
    needs_md = needs.strip() or "(belum ditulis)"
    soul = f"""# Soul

You are {name}, a personal AI agent. Be warm, direct, and plain:
short sentences, everyday words, no jargon unless the user asks
for it. Say what you did and what is left, with real counts,
never a guessed percentage. If you do not know something, say so
instead of guessing.

Gaya bicara: {style_line}

Kegunaan utama (dipilih saat onboarding):
{purposes_md}

Kebutuhan pengguna:
{needs_md}

Selalu minta persetujuan sebelum membayar apa pun.
"""
    identity = f"""# Identity

Name: {name}

You are {name}, a personal AI agent the user runs themselves.
"""
    user_md = f"""# About the user

- Name: {user or "(fill in what to call you)"}
- Language: Bahasa Indonesia
- Notes: {needs_md}

Data yang dipakai agen (dikonfirmasi saat onboarding):
{sources_md}
"""
    return {"soul": soul, "identity": identity, "user": user_md}


def register_routes(app: FastAPI, need_user) -> FastAPI:
    """Mount the onboarding + hints routes on ``app``."""

    def _require_user(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return None, JSONResponse(
                {"error": render_server_line("error.not_logged_in")}, status_code=401
            )
        return user_id, None

    def _bad_request(exc: Exception):
        return JSONResponse({"error": str(exc)}, status_code=400)

    @app.get("/api/onboarding")
    def onboarding_status_endpoint(request: Request):
        user_id, error = _require_user(request)
        if error is not None:
            return error
        return get_status(user_id, dsn=app.state.dsn)

    @app.post("/api/onboarding/start")
    def onboarding_start_endpoint(request: Request):
        user_id, error = _require_user(request)
        if error is not None:
            return error
        try:
            return start(user_id, dsn=app.state.dsn)
        except Exception as exc:
            return _bad_request(exc)

    @app.post("/api/onboarding/step1")
    async def onboarding_step1_endpoint(request: Request):
        user_id, error = _require_user(request)
        if error is not None:
            return error
        try:
            data = _json_body(await request.json())
            return step1(
                user_id,
                data.get("name"),
                data.get("style"),
                example_message=data.get("example_message"),
                dsn=app.state.dsn,
            )
        except (ValueError, KeyError) as exc:
            return _bad_request(exc)

    @app.post("/api/onboarding/step2")
    def onboarding_step2_endpoint(request: Request):
        user_id, error = _require_user(request)
        if error is not None:
            return error
        try:
            return step2(user_id, dsn=app.state.dsn)
        except (ValueError, KeyError) as exc:
            return _bad_request(exc)

    @app.post("/api/onboarding/step3")
    async def onboarding_step3_endpoint(request: Request):
        user_id, error = _require_user(request)
        if error is not None:
            return error
        try:
            data = _json_body(await request.json())
            return step3(user_id, connector=data.get("connector", "nanti"), dsn=app.state.dsn)
        except (ValueError, KeyError) as exc:
            return _bad_request(exc)

    @app.post("/api/onboarding/first-task")
    async def onboarding_first_task_endpoint(request: Request):
        user_id, error = _require_user(request)
        if error is not None:
            return error
        try:
            data = _json_body(await request.json())
            return first_task(
                user_id, data.get("event_type"), data.get("target"), dsn=app.state.dsn
            )
        except (ValueError, KeyError) as exc:
            return _bad_request(exc)

    @app.post("/api/onboarding/agent-file")
    async def onboarding_agent_file_endpoint(request: Request):
        """Auto-write the agent Markdown files from onboarding answers.

        Body: ``{"agent_name", "user_name", "style", "purposes"[],
        "needs", "data_sources"[]}``. The three files (SOUL.md,
        IDENTITY.md, USER.md) are written with the V2-25 persona
        mechanism and the new persona is activated, so the agent
        reads them from the next turn. Idempotent: re-running with
        the same agent name updates the same persona instead of
        failing. Returns the file texts, so the screen can show
        exactly what was written.
        """
        user_id, error = _require_user(request)
        if error is not None:
            return error
        try:
            data = _json_body(await request.json())
            agent_name = data.get("agent_name")
            if not isinstance(agent_name, str) or not agent_name.strip():
                raise ValueError("agent_name harus string tak kosong")
            style = data.get("style") or "santai"
            if style not in _STYLE_LINES:
                raise ValueError("style harus santai, rapi, atau campur")
            purposes = data.get("purposes") or []
            needs = data.get("needs") or ""
            sources = data.get("data_sources") or []
            if not isinstance(purposes, list) or not isinstance(sources, list):
                raise ValueError("purposes dan data_sources harus list")
            if not isinstance(needs, str):
                raise ValueError("needs harus string")
            files = build_agent_files(
                agent_name=agent_name,
                user_name=data.get("user_name") or "",
                style=style,
                purposes=[str(p) for p in purposes],
                needs=needs,
                data_sources=[str(s) for s in sources],
            )
            home = getattr(request.app.state, "persona_home", None)
            slug = slugify_name(agent_name)
            if persona_dir(home, slug).exists():
                record = update_persona(
                    home,
                    slug,
                    name=agent_name.strip(),
                    soul=files["soul"],
                    identity=files["identity"],
                    user=files["user"],
                    style=style,
                )
            else:
                record = create_persona(
                    home,
                    agent_name.strip(),
                    soul=files["soul"],
                    identity=files["identity"],
                    user=files["user"],
                    style=style,
                )
            set_active_persona(home, slug)
            return {**record, **files}
        except (ValueError, KeyError) as exc:
            return _bad_request(exc)

    @app.get("/api/onboarding/hints")
    def onboarding_hints_endpoint(request: Request):
        user_id, error = _require_user(request)
        if error is not None:
            return error
        try:
            context = parse_hint_context(dict(request.query_params))
        except ValueError as exc:
            return _bad_request(exc)
        return due_hints(user_id, context=context, dsn=app.state.dsn)

    @app.post("/api/onboarding/hints/{flag}/seen")
    def onboarding_hint_seen_endpoint(request: Request, flag: str):
        user_id, error = _require_user(request)
        if error is not None:
            return error
        try:
            return dismiss_hint(user_id, flag, dsn=app.state.dsn)
        except ValueError as exc:
            return _bad_request(exc)

    return app
