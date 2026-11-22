"""PWA Puspa — 18 layar (7 dari revisi 7, ditambah Insights dan
Onboarding oleh kerja kematangan G28, ditambah layar Persona untuk
beberapa persona yang bisa diganti, dan layar Artefak penampil artefak
+ log run dari riset CowAgent).

Stack (keputusan 1, diperbarui G1): FastAPI + TypeScript SPA
(React 19 + Vite, `apps/pwa/`, dibangun ke `apps/pwa/dist`) + service
worker. Kalau `dist` belum dibangun, layar jatuh kembali ke template
Jinja lama. Kontrak API: `docs/openapi-pwa.yaml`.
Update live lewat SSE dari proyeksi Buku Kerja, tanpa WebSocket
di MVP. Layar: Chat, Tugas, Modul, Konektor, Memori, Pengaturan,
Kantor, Insights, Onboarding, Ruang. Bukti, Rencana, Rekor Otonomi, alur,
dan modul kustom adalah panel di dalam layar yang sudah ada —
bukan layar baru.

Catatan jujur soal jumlah layar: revisi 7 membatasi PWA pada 7 layar, tetapi perintah
kematangan Nero (kerjakan semua 28 celah G1-G28) mewajibkan halaman Insights dan
Onboarding pertama kali (G28), jadi batas 7 layar itu sudah tidak berlaku; total
sekarang 18 (termasuk Ruang, Artefak, Persona, dan Hub skill COW-03).

Login: Google (keputusan 16, fondasi Fase 1 `puspa.auth`); sesi
cookie httpOnly via `puspa.accounts.ports`. Di tes, `create_app`
menerima `test_user_id` (seam tes saja; di produksi None dan cookie wajib).
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from collections import defaultdict
from pathlib import Path

import psycopg
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader

from puspa.i18n.server_lines import render as render_server_line

BASE_DIR = Path(__file__).resolve().parent.parent.parent
TEMPLATE_DIR = BASE_DIR / "apps" / "pwa" / "templates"
STATIC_DIR = BASE_DIR / "apps" / "pwa" / "static"
DIST_DIR = BASE_DIR / "apps" / "pwa" / "dist"
BOOTSTRAP_MARKER = "<!--PUSPA_BOOTSTRAP-->"

SCREENS = (
    "chat",
    "tasks",
    "modules",
    "connectors",
    "memory",
    "settings",
    # Multiple switchable personas: one persona per role (work vs
    # personal), one active at a time (see puspa.persona.switcher).
    "personas",
    "office",
    "live",
    # P-10: replay timeline of a bot's steps (plan section 12.10).
    "replay",
    # Artifacts + run-logs browser (CowAgent research): the
    # artifacts a run produced plus its ledger log, in one
    # screen with two tabs.
    "artifacts",
    "insights",
    "onboarding",
    "mockup",
    # Broken-rooms fix: the SPA lists Rooms, but the server had no
    # "rooms" screen, so /rooms and /api/rooms both 404'd.
    "rooms",
    # P-07: morning summary + notes-to-actions (plan section 12.8).
    "morning",
    # Redacted diagnostics export for bug reports: one-click
    # download of logs + message trace + ledger events, secrets
    # scrubbed before they leave the server.
    "diagnostics",
    # Skill marketplace hub (COW-03): browse, search, one-click install.
    "hub",
    # AST-02: document knowledge base (upload files, ask about them).
    "knowledge",
)


def _jinja(template_dir=None):
    return Environment(loader=FileSystemLoader(str(template_dir or TEMPLATE_DIR)), autoescape=True)


def create_app(
    *,
    dsn=None,
    test_user_id=None,
    template_dir=None,
    draft_sender=None,
    embedder=None,
    embedding_environment=None,
    transcriber=None,
    ocr_engine=None,
    share_inbox_dir=None,
    # P-07: morning summary test seams (same pattern as the
    # transcriber above): a ``user_id -> GoogleConnector`` factory
    # and an httpx client for the public prayer/holiday sources.
    morning_connector_factory=None,
    morning_http_client=None,
    # V2-30: voice seams (same pattern as the transcriber above):
    # an httpx client for BYOK ASR/TTS endpoints and a connect
    # callable for the Edge TTS WebSocket, so tests can inject
    # fakes and no real network is needed.
    voice_http_client=None,
    voice_edge_connect=None,
    # Switchable personas: the Puspa home holding the persona
    # files (same test seam pattern as the transcriber above).
    # None means the switcher resolves the user's real home.
    persona_home=None,
) -> FastAPI:
    """Bangun aplikasi FastAPI PWA."""
    app = FastAPI(title="Puspa")
    jinja = _jinja(template_dir)
    app.state.dsn = dsn or os.environ.get(
        "PUSPA_TEST_DSN", "dbname=puspa_test user=root host=/var/run/postgresql"
    )
    app.state.test_user_id = test_user_id
    app.state.persona_home = persona_home
    # Anotasi tipe tidak bisa ditulis pada atribut non-self
    # (app.state.*), jadi tipenya dicatat di variabel lokal dulu.
    chat_state: dict[str, list[dict]] = defaultdict(list)
    app.state.chat = chat_state
    app.state.event = []
    app.state.approval = {}
    # E-10: approval-fatigue signal (plan 6.3). The tracker records
    # when a card is first shown (tasks screen) and when the user
    # decides (api_approval below); the unread rate is served at
    # GET /api/approvals/fatigue. It only measures, never blocks.
    from puspa.policy.approval_cards import ApprovalFatigueTracker

    app.state.fatigue_tracker = ApprovalFatigueTracker()
    app.state.time_onboarding = []
    # B13: delivery channel for approved draft sends. Unset by
    # default: until a channel (Telegram / WhatsApp / email) is
    # wired in, approved sends are refused with 501 and nothing
    # is claimed as sent (see puspa/pwa/drafts_api.py).
    app.state.draft_sender = draft_sender
    # P-03: voice messages. The transcriber seam (puspa.voice) is
    # injected here so tests can pass a test double; the default is
    # the stub transcriber — no real speech engine is chosen until
    # the 50-recording WER measurement exists (plan 12.4). V2-30:
    # when no transcriber is injected, each upload resolves the
    # user's own ASR choice from Settings instead
    # (puspa.pwa.voice_api); ``transcriber_explicit`` records
    # which case this app is in.
    from puspa.voice.transcriber import resolve_transcriber

    app.state.transcriber_explicit = transcriber is not None
    app.state.transcriber = transcriber or resolve_transcriber("stub")
    app.state.voice_http_client = voice_http_client
    app.state.voice_edge_connect = voice_edge_connect
    # Pending voice transcripts awaiting user confirmation (keyed by
    # pending id; unconfirmed transcripts are not messages).
    app.state.voice_pending = {}
    # Receipt photos (plan section 12.5). The OCR seam
    # (puspa.receipts) is injected here so tests can pass a test
    # double; the default is the stub engine — no real OCR engine
    # is chosen until the 20-photo accuracy measurement exists.
    from puspa.receipts.ocr import resolve_ocr_engine

    app.state.ocr_engine = ocr_engine or resolve_ocr_engine("stub")
    # P-07: morning summary test seams (see the create_app
    # signature): injected Google connector factory + HTTP client
    # for the public prayer/holiday sources; None means "build the
    # real ones per request".
    app.state.morning_connector_factory = morning_connector_factory
    app.state.morning_http_client = morning_http_client
    # Pending receipt drafts awaiting user confirmation (keyed by
    # pending id; unconfirmed drafts are not expenses).
    app.state.receipt_pending = {}
    # Confirmed expenses per user (in-memory; no expense table
    # yet — see puspa.pwa.receipt_api).
    app.state.receipt_expenses = {}

    # -- G31: serve modes (local operator vs hosted) ---------------------
    # One web codebase, two serve modes (decision locked by Nero, see
    # docs/modes-decision.md). The mode is resolved once at startup
    # from PUSPA_SERVE_MODE / PUSPA_SERVE_MODE_FILE; an explicitly
    # invalid value raises ServeModeError here, so the server refuses
    # to start rather than serve the wrong surface. Unset means
    # "local": the operator owns the machine, so the operator surface
    # is the safe default.
    from puspa.hosting.pairing import PairingStore
    from puspa.policy.gateway import DEFAULT_POLICY_DATA, Gateway
    from puspa.pwa.serve_modes import GatewayDecisionLog, resolve_serve_mode

    serve_mode = resolve_serve_mode()
    app.state.serve_mode = serve_mode.value
    app.state.pairing_store = PairingStore()
    app.state.gateway_log = GatewayDecisionLog()

    # Gateway decisions are audited into the decision log (the
    # panel's "last decision") and mirrored into the app event log
    # (the panel's live log lines), so both panels read live state.
    def _gateway_audit(record):
        app.state.gateway_log.record(record)
        app.state.event.append(
            {
                "type": "gateway_decision",
                "task_id": "",
                "agent_id": "puspa",
                "content": app.state.gateway_log.last() or {},
                "created_at": time.time(),
            }
        )

    app.state.gateway = Gateway.start(DEFAULT_POLICY_DATA, audit=_gateway_audit)
    # -- end G31 ---------------------------------------------------------

    # -- E-14: embedding health at startup (plan 7.4) ------------------
    # The startup embedder is decided once, without loading a model:
    # the cheap probe says whether the real model cache is present,
    # and only then is the real embedder constructed (its model
    # still loads lazily on first use). In production with only the
    # hash stand-in, the strict policy refuses to start here
    # (EmbeddingStartupRefused propagates); the default degraded
    # policy starts degraded instead, and the state is recorded in
    # the ledger once so it is visible there too. A ledger failure
    # never blocks serving the app (same pattern as the Puspa-agent
    # ensure step in need_user below).
    from puspa.memory.embeddings import FastEmbedEmbedding, HashEmbedding
    from puspa.retrieval.embedding_health import (
        detect_available_embedder_kind,
        evaluate_startup,
        record_degraded_to_ledger,
    )

    embedding_env = embedding_environment or os.environ.get("PUSPA_ENV", "development")
    embedding_policy = os.environ.get("PUSPA_EMBEDDING_PRODUCTION_POLICY", "degraded")
    fell_back = False
    if embedder is None:
        if detect_available_embedder_kind() == "fastembed":
            embedder = FastEmbedEmbedding()
        else:
            embedder = HashEmbedding()
            fell_back = True
    embedding_health = evaluate_startup(
        embedder,
        environment=embedding_env,
        production_policy=embedding_policy,
        fell_back=fell_back,
    )
    app.state.embedding_health = embedding_health.to_dict()
    app.state.embedding_status = embedding_health.status
    if embedding_health.is_degraded:
        try:
            from puspa.ledger import Ledger

            record_degraded_to_ledger(Ledger(app.state.dsn), embedding_health)
        except Exception:
            pass
    # -- end E-14 --------------------------------------------------------

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    if (DIST_DIR / "assets").exists():
        app.mount("/assets", StaticFiles(directory=str(DIST_DIR / "assets")), name="assets")

    def user(request: Request):
        if app.state.test_user_id:
            return app.state.test_user_id
        token = request.cookies.get("puspa_sesi")
        if not token:
            return None
        try:
            from puspa.accounts.ports import validate_session

            return validate_session(token, dsn=app.state.dsn)
        except Exception:
            return None

    def need_user(request: Request):
        user_id = user(request)
        if user_id is None:
            return None
        # Keputusan Fase 2 (a): agen Puspa WAJIB — pastikan terdaftar
        # saat pengguna masuk (idempotent; gagal DB tidak memblokir
        # tampilan demo lokal).
        try:
            from puspa.runtime.agents import ensure_puspa_agent

            ensure_puspa_agent(user_id, dsn=app.state.dsn)
        except Exception:
            pass
        return user_id

    def render(name: str, request: Request, context: dict | None = None):
        ctx = {"request": request, "screen": name.replace(".html", "")}
        ctx.update(context or {})
        return HTMLResponse(jinja.get_template(name).render(ctx))

    def spa_or_template(screen: str, request: Request, context: dict | None = None):
        """Serve the built SPA shell with server bootstrap, or the Jinja
        template when `dist` has not been built yet.

        The bootstrap embeds the screen's real data as
        `window.__PUSPA_BOOTSTRAP__` (schema `Bootstrap` in
        `docs/openapi-pwa.yaml`), so the SPA hydrates without an extra
        fetch and the first paint already carries live data. The JSON
        is escaped so it can never break out of its <script> tag.
        """
        index = DIST_DIR / "index.html"
        ctx = dict(context or {})
        if index.exists():
            bootstrap = {
                "screen": screen,
                "user_id": ctx.get("user_id"),
                "messages": ctx.get("message", []),
                "approvals": ctx.get("approval", []),
                "agents": ctx.get("agents", []),
                "profile": ctx.get("profile", []),
                "office": ctx.get("office"),
                "handoffs": ctx.get("handoffs", []),
                # P-03: text shared into the PWA from another app
                # (GET /share); the Chat screen prefills its draft
                # with it. Absent on every other screen.
                "shared_text": ctx.get("shared_text"),
            }
            payload = (
                # default=str: bootstrap values carry datetimes (the
                # memory screen's profile facts), which JSON needs as
                # ISO strings; without it /memory crashes in the SPA
                # path whenever a fact exists.
                json.dumps(bootstrap, ensure_ascii=False, default=str)
                .replace("<", "\\u003c")
                .replace(">", "\\u003e")
                .replace("&", "\\u0026")
            )
            html = index.read_text(encoding="utf-8")
            script = f"<script>window.__PUSPA_BOOTSTRAP__={payload};</script>"
            html = html.replace(BOOTSTRAP_MARKER, script, 1)
            return HTMLResponse(html)
        return render(f"{screen}.html", request, ctx)

    # -- halaman -------------------------------------------------------
    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        return RedirectResponse("/chat")

    @app.get("/app", response_class=HTMLResponse)
    def app_entry(request: Request):
        # E-05: the SPA entry point. Unauthenticated on purpose, like
        # /masuk: it serves only the built SPA shell (no user data —
        # the bootstrap carries an empty context), and the SPA itself
        # routes an anonymous visitor to login. The image smoke test
        # requests this path and checks the React bundle is served;
        # without the built dist it falls back to the Jinja template,
        # which the smoke check rejects as "not the SPA".
        return spa_or_template("chat", request, {})

    @app.get("/masuk", response_class=HTMLResponse)
    def login(request: Request):
        # G1: the login screen is part of the SPA; "masuk.html" never
        # existed (the old template is "login.html"), so the fallback
        # renders that name.
        return spa_or_template(
            "login",
            request,
            {"client_id_ada": bool(os.environ.get("PUSPA_GOOGLE_CLIENT_ID", ""))},
        )

    @app.get("/auth/google")
    def auth_google(request: Request):
        from puspa.auth.google_oauth import build_auth_url

        client_id = os.environ.get("PUSPA_GOOGLE_CLIENT_ID", "")
        redirect = os.environ.get(
            "PUSPA_GOOGLE_REDIRECT_URI", "http://localhost:8000/auth/google/callback"
        )
        if not client_id:
            return JSONResponse(
                {"error": render_server_line("error.google_login_not_configured")},
                status_code=503,
            )
        url, _state, _verifier = build_auth_url(client_id, redirect)
        return RedirectResponse(url)

    @app.get("/auth/google/callback")
    def auth_callback(request: Request, code: str = "", state: str = ""):
        return JSONResponse(
            {
                "notes": "callback login Google ditangani lengkap di Fase 1"
                " (complete_google_login); pasang client_id + client_secret"
                " lalu lanjutkan di sini.",
                "code_accepted": bool(code),
            }
        )

    for screen in SCREENS:

        def _create_page(screen=screen):
            def page(request: Request):
                user_id = need_user(request)
                if user_id is None:
                    return RedirectResponse("/masuk")
                return spa_or_template(screen, request, _context_screen(screen, user_id))

            return page

        app.get(f"/{screen}", response_class=HTMLResponse)(_create_page())

    def _context_screen(screen: str, user_id: str) -> dict:
        from puspa.evidence.projections import office as office_projection

        ctx: dict = {"user_id": user_id}
        if screen == "chat":
            ctx["message"] = app.state.chat[user_id]
        elif screen == "office":
            ctx["office"] = office_projection(app.state.event)
            ctx["handoffs"] = [
                e for e in app.state.event if e.get("type", "").startswith("handoff_")
            ]
        elif screen == "tasks":
            ctx["approval"] = list(app.state.approval.values())
            # E-10: first showing of each card starts the fatigue
            # clock. The timestamp is stamped on the item (so a
            # decision without a live tracker still has it) and
            # recorded in the tracker.
            for item in ctx["approval"]:
                if isinstance(item, dict) and item.get("request_id"):
                    shown_at = item.setdefault("presented_at", time.time())
                    app.state.fatigue_tracker.record_presented(item["request_id"], at=shown_at)
        elif screen == "memory":
            try:
                from puspa.pwa.memory_api import MemoryReadService

                service = getattr(app.state, "memory_service", None) or MemoryReadService(
                    dsn=app.state.dsn
                )
                ctx["profile"] = service.list_facts(user_id)["facts"]
                ctx["memory_consent"] = service.consent_status(user_id)
                ctx["guest_mode"] = service.is_guest(user_id)
            except Exception:
                ctx["profile"] = []
                ctx["memory_consent"] = {"status": "none"}
                ctx["guest_mode"] = False
        elif screen == "connectors":
            try:
                from puspa.runtime.agents import list_agents

                ctx["agents"] = list_agents(user_id, dsn=app.state.dsn)
            except Exception:
                ctx["agents"] = []
        elif screen == "live":
            # P-09: the live screen needs the user's agents to
            # pick which bot to watch (same source as connectors).
            # Narrowed failure modes, like the room-activity SSE
            # frame: the agent list read itself (a database error
            # or an invalid user id), never a bare except.
            try:
                from puspa.runtime.agents import list_agents

                ctx["agents"] = list_agents(user_id, dsn=app.state.dsn)
            except (psycopg.Error, ValueError, TypeError, AttributeError):
                ctx["agents"] = []
        elif screen in ("replay", "artifacts"):
            # P-10: the replay screen needs the same agent list
            # to pick which bot's task to replay. The artifacts
            # screen needs it too: one picker serves both its
            # tabs (artifacts list + run-log list).
            try:
                from puspa.runtime.agents import list_agents

                ctx["agents"] = list_agents(user_id, dsn=app.state.dsn)
            except (psycopg.Error, ValueError, TypeError, AttributeError):
                ctx["agents"] = []
        elif screen == "hub":
            from puspa.pwa import hub_api

            ctx["skills"] = hub_api.hub_screen_context(app)
        return ctx

    # -- G32 WebUI lite + trial (BEGIN: lite-mode wiring, do not move) --
    # The lite mode serves the Jinja templates (apps/pwa/templates/)
    # with no React build, and the trial entry page (/trial) against
    # the G2 billing trial entitlements (puspa.pwa.lite). The trial
    # page is Jinja-only on purpose: it must work even when dist
    # exists, because it is the zero-install first touch.
    from puspa.pwa import lite as lite_mode

    app.state.trial_secret = lite_mode.trial_secret_from_env()

    def _trial_state_for(request: Request, user_id: str):
        token = lite_mode.decode_trial_cookie(request.cookies.get(lite_mode.TRIAL_COOKIE))
        return lite_mode.check_trial(user_id, secret=app.state.trial_secret, token=token)

    @app.get("/trial", response_class=HTMLResponse)
    def trial_page(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return RedirectResponse("/masuk")
        state = _trial_state_for(request, user_id)
        return render(
            "trial.html",
            request,
            {
                "user_id": user_id,
                "trial": state.to_dict(),
                "message": app.state.chat[user_id],
                "upgrade_url": lite_mode.upgrade_url(),
                "error": request.query_params.get("error", ""),
            },
        )

    @app.post("/trial/start")
    def trial_start(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return RedirectResponse("/masuk")
        secret = app.state.trial_secret
        existing = _trial_state_for(request, user_id)
        if existing.status in ("active", "grace") and existing.entitlement is not None:
            # Already trialling: reuse the presented entitlement,
            # never mint a second trial.
            entitlement = existing.entitlement
        elif (
            lite_mode.prior_trial_entitlement(
                user_id,
                secret=secret,
                token=lite_mode.decode_trial_cookie(request.cookies.get(lite_mode.TRIAL_COOKIE)),
            )
            is not None
        ):
            # A previous trial token is still presented (expired):
            # one trial per user, send them to the upgrade offer.
            return RedirectResponse("/trial?error=trial_already_used", status_code=303)
        else:
            try:
                entitlement = lite_mode.start_trial(
                    user_id,
                    machine_id=lite_mode.machine_id_for_user(user_id),
                    secret=secret,
                )
            except ValueError:
                return RedirectResponse("/trial?error=trial_unavailable", status_code=303)
        response = RedirectResponse("/trial", status_code=303)
        response.set_cookie(
            lite_mode.TRIAL_COOKIE,
            lite_mode.encode_trial_cookie(entitlement),
            max_age=lite_mode.trial_cookie_max_age(),
            httponly=True,
            samesite="lax",
        )
        return response

    @app.post("/trial/chat")
    async def trial_chat(request: Request, text: str = Form(...)):
        # Plain-HTML chat post for the lite trial page (no htmx, no
        # JS needed): same message + event shape as /api/chat, then
        # back to the trial page.
        user_id = need_user(request)
        if user_id is None:
            return RedirectResponse("/masuk")
        state = _trial_state_for(request, user_id)
        if state.status not in ("active", "grace"):
            return RedirectResponse("/trial", status_code=303)
        message = {"sender": "users", "text": text, "created_at": time.time()}
        app.state.chat[user_id].append(message)
        app.state.event.append(
            {
                "type": "user_message",
                "task_id": "",
                "agent_id": "puspa",
                "content": {"sender": user_id, "text": text},
                "created_at": time.time(),
            }
        )
        return RedirectResponse("/trial", status_code=303)

    # -- G32 WebUI lite + trial (END) -----------------------------------

    # -- SSE: update live dari proyeksi Buku Kerja ----------------------
    @app.get("/sse")
    def sse(request: Request):
        user_id = user(request)
        if user_id is None:
            return JSONResponse(
                {"error": render_server_line("error.not_logged_in")}, status_code=401
            )

        def aliran():
            from puspa.evidence.projections import office as office_projection

            yield f"data: {json.dumps({'type': 'office', 'content': office_projection(app.state.event)}, ensure_ascii=False)}\n\n"
            # P-05: the same stream carries room activity, filtered
            # to the rooms this user belongs to — the Rooms screen
            # refreshes when one arrives. Membership is checked
            # here, so a removed member stops seeing a room's
            # activity the moment their membership is gone.
            try:
                from puspa.rooms import RoomStore

                mine = {room.id for room in RoomStore(app.state.dsn).list_rooms_for_user(user_id)}
                for event in app.state.event:
                    if event.get("type") == "room_activity" and event.get("room_id") in mine:
                        yield (
                            "data: "
                            + json.dumps(
                                {
                                    "type": "room_activity",
                                    "room_activity": {
                                        "room_id": event.get("room_id"),
                                        "kind": event.get("kind"),
                                        "actor": event.get("actor"),
                                        "summary": event.get("summary"),
                                        "created_at": event.get("created_at"),
                                    },
                                    "created_at": time.time(),
                                },
                                ensure_ascii=False,
                            )
                            + "\n\n"
                        )
            except (psycopg.Error, ValueError, TypeError, KeyError, AttributeError):
                # Room activity is best effort: the office frame
                # above already went out, and the stream must not
                # die because the room lookup failed. The narrowed
                # failure modes are the room lookup itself (a
                # database error, or an invalid user id) and the
                # frame encoding (a malformed event or row, or a
                # value json cannot encode).
                pass
            yield f"data: {json.dumps({'type': 'heartbeat', 'created_at': time.time()}, ensure_ascii=False)}\n\n"

        return StreamingResponse(
            aliran(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
        )

    # -- G31: serve modes — operator status (local mode only) -----------
    # The Office screen polls this endpoint for its operator panels.
    # In local mode the payload is pairing + gateway + live log, all
    # read from the live objects above; in hosted mode it carries
    # operator_panels=false and null panels, and the client hides the
    # panels. Login is required either way: the operator surface is
    # for the logged-in machine owner.
    @app.get("/api/operator/status")
    def operator_status_endpoint(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return JSONResponse(
                {"error": render_server_line("error.not_logged_in")}, status_code=401
            )
        from puspa.observability.health import observability_block
        from puspa.pwa.serve_modes import operator_status

        # E-18: the same observability block /health serves.
        block = None
        with contextlib.suppress(Exception):
            block = observability_block()
        return operator_status(
            app.state.serve_mode,
            pairing_store=app.state.pairing_store,
            gateway=app.state.gateway,
            decision_log=app.state.gateway_log,
            recent_events=app.state.event,
            embedding=getattr(app.state, "embedding_health", None),
            observability=block,
        )

    # -- end G31 ---------------------------------------------------------

    # -- E-14: health endpoint (plan 7.4) --------------------------------
    # Public and login-free (see puspa/hosting/service_allow.py):
    # it reports whether the server is up and whether its embedding
    # subsystem runs degraded. Always 200 while the server is up;
    # the status field carries ok / degraded.
    @app.get("/health")
    def health_endpoint():
        from puspa.observability.health import observability_block
        from puspa.pwa.health import build_health_payload

        # E-18: the same observability block the operator surface
        # serves, so /health and the operator status never disagree
        # about the alarms.
        block = None
        with contextlib.suppress(Exception):
            block = observability_block()
        return build_health_payload(
            app.state.embedding_health,
            serve_mode=app.state.serve_mode,
            observability=block,
        )

    # -- end E-14 --------------------------------------------------------

    # -- panel Bukti -----------------------------------------------------
    @app.get("/bukti/{evidence_id}", response_class=HTMLResponse)
    def panel_evidence(request: Request, evidence_id: str):
        user_id = need_user(request)
        if user_id is None:
            return RedirectResponse("/masuk")
        evidence = next(
            (
                b
                for b in app.state.event
                if b.get("type") == "evidence_recorded"
                and (b.get("content") or {}).get("evidence_id") == evidence_id
            ),
            None,
        )
        return render(
            "panel_evidence.html",
            request,
            {
                "evidence": (evidence or {}).get("content") if evidence else None,
                "evidence_id": evidence_id,
            },
        )

    # -- API -------------------------------------------------------------
    @app.post("/api/chat")
    async def send_chat(request: Request, text: str = Form(...)):
        user_id = need_user(request)
        if user_id is None:
            return JSONResponse(
                {"error": render_server_line("error.not_logged_in")}, status_code=401
            )
        message = {"sender": "users", "text": text, "created_at": time.time()}
        app.state.chat[user_id].append(message)
        app.state.event.append(
            {
                "type": "user_message",
                "task_id": "",
                "agent_id": "puspa",
                "content": {"sender": user_id, "text": text},
                "created_at": time.time(),
            }
        )
        return {"ok": True, "message": message}

    @app.post("/api/agen")
    async def api_create_agent(
        request: Request, name: str = Form(...), role: str = Form(""), purpose: str = Form("")
    ):
        user_id = need_user(request)
        if user_id is None:
            return JSONResponse(
                {"error": render_server_line("error.not_logged_in")}, status_code=401
            )
        from puspa.runtime.agents import create_agent

        try:
            return create_agent(user_id, name, role, purpose, dsn=app.state.dsn)
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.delete("/api/agen/{agent_id}")
    async def api_delete_agent(request: Request, agent_id: str):
        user_id = need_user(request)
        if user_id is None:
            return JSONResponse(
                {"error": render_server_line("error.not_logged_in")}, status_code=401
            )
        from puspa.runtime.agents import delete_agent

        try:
            delete_agent(user_id, agent_id, dsn=app.state.dsn)
            return {"ok": True}
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.post("/api/tugas")
    async def api_create_task(request: Request, purpose: str = Form(...)):
        user_id = need_user(request)
        if user_id is None:
            return JSONResponse(
                {"error": render_server_line("error.not_logged_in")}, status_code=401
            )
        from puspa.runtime.agents import ensure_puspa_agent
        from puspa.runtime.tasks import Task, TaskRunner, create_task_id

        agent_id = ensure_puspa_agent(user_id, dsn=app.state.dsn)
        runner = TaskRunner(
            sink=lambda event_type, content: app.state.event.append(
                {
                    "type": event_type,
                    "task_id": content.get("task_id", ""),
                    "agent_id": agent_id,
                    "content": content,
                    "created_at": time.time(),
                }
            )
        )
        task = runner.run(
            Task(task_id=create_task_id(), user_id=user_id, agent_id=agent_id, purpose=purpose)
        )
        return {
            "task_id": task.task_id,
            "status": task.status,
            "stage": task.stage,
            "question": task.question,
        }

    @app.post("/api/tugas/{task_id}/hentikan")
    async def api_stop(request: Request, task_id: str):
        user_id = need_user(request)
        if user_id is None:
            return JSONResponse(
                {"error": render_server_line("error.not_logged_in")}, status_code=401
            )
        from puspa.runtime.subagents import SubagentPool

        pool = app.state.__dict__.setdefault("pool", SubagentPool())
        result = pool.stop_task(task_id)
        app.state.event.append(
            {
                "type": "task_stopped",
                "task_id": task_id,
                "agent_id": "puspa",
                "content": {"reason": "dihentikan user"},
                "created_at": time.time(),
            }
        )
        return result

    @app.post("/api/persetujuan/{request_id}")
    async def api_approval(request: Request, request_id: str, approve: str = Form(...)):
        user_id = need_user(request)
        if user_id is None:
            return JSONResponse(
                {"error": render_server_line("error.not_logged_in")}, status_code=401
            )
        item = app.state.approval.get(request_id)
        if item is None:
            return JSONResponse(
                {"error": render_server_line("error.unknown_request")}, status_code=404
            )
        item["status"] = "approved" if approve == "ya" else "rejected"
        # E-10: record the decision latency for the fatigue signal.
        # The presentation time comes from the item when the card
        # was shown (tasks screen); without it the decision is
        # counted but cannot be judged unread.
        app.state.fatigue_tracker.record_decision(
            request_id,
            approved=(approve == "ya"),
            presented_at=item.get("presented_at"),
        )
        # E-18: approval latency, from the card being shown to the decision.
        presented_at = item.get("presented_at")
        if presented_at:
            with contextlib.suppress(Exception):
                from puspa.observability import metrics as observability_metrics

                observability_metrics.get_metrics().record_approval_latency(
                    max(0.0, time.time() - float(presented_at)),
                    request_id=request_id,
                    approved=(approve == "ya"),
                )
        app.state.event.append(
            {
                "type": "approval_granted" if approve == "ya" else "approval_rejected",
                "task_id": item.get("task_id", ""),
                "agent_id": "puspa",
                "content": {"request_id": request_id},
                "created_at": time.time(),
            }
        )
        return {"ok": True, "status": item["status"]}

    @app.get("/api/approvals/fatigue")
    async def api_approval_fatigue(request: Request):
        """E-10: approval-fatigue numbers (plan 6.3).

        Share of decisions made faster than a human could read
        the card (under one second). Measurement only.
        """
        user_id = need_user(request)
        if user_id is None:
            return JSONResponse(
                {"error": render_server_line("error.not_logged_in")}, status_code=401
            )
        return app.state.fatigue_tracker.stats()

    @app.post("/api/konektor/{konektor}")
    async def api_connector(
        request: Request, connector: str, agent_id: str = Form(...), action: str = Form(...)
    ):
        user_id = need_user(request)
        if user_id is None:
            return JSONResponse(
                {"error": render_server_line("error.not_logged_in")}, status_code=401
            )
        from puspa.connectors.grants import give_connector, revoke_connector

        try:
            if action == "cabut":
                revoke_connector(user_id, agent_id, connector, dsn=app.state.dsn)
                return {"ok": True, "connected": False}
            return give_connector(user_id, agent_id, connector, dsn=app.state.dsn)
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.post("/api/push/langganan")
    async def api_push(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return JSONResponse(
                {"error": render_server_line("error.not_logged_in")}, status_code=401
            )
        data = await request.json()
        from puspa.push.ports import save_subscription

        subscription_id = save_subscription(
            user_id,
            endpoint=data["endpoint"],
            p256dh=data["keys"]["p256dh"],
            auth=data["keys"]["auth"],
            dsn=app.state.dsn,
        )
        return {"ok": True, "subscription_id": subscription_id}

    # -- G33: own-model selection in Settings -------------------------
    # Own block: GET/POST /api/model-settings + BYOK key endpoints.
    # The selection (hosted / BYOK / local) is persisted by
    # puspa.pwa.model_settings; a BYOK key goes to the vault only
    # and is never accepted, stored, or returned here.
    @app.get("/api/model-settings")
    async def api_get_model_settings(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return JSONResponse(
                {"error": render_server_line("error.not_logged_in")}, status_code=401
            )
        from puspa.pwa.model_settings import get_selection

        try:
            return get_selection(user_id, dsn=app.state.dsn).to_public_dict()
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.post("/api/model-settings")
    async def api_save_model_settings(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return JSONResponse(
                {"error": render_server_line("error.not_logged_in")}, status_code=401
            )
        from puspa.pwa.model_settings import save_selection

        try:
            data = await request.json()
            if not isinstance(data, dict):
                raise ValueError("model settings body must be a JSON object")
            saved = save_selection(
                user_id,
                {
                    "source": data.get("source", "hosted"),
                    "base_url": data.get("base_url"),
                    "model": data.get("model"),
                    "provider": data.get("provider"),
                    # Any key field the client sends is passed
                    # through so save_selection refuses it loudly
                    # instead of silently dropping it.
                    **{
                        k: v
                        for k, v in data.items()
                        if k not in ("source", "base_url", "model", "provider")
                    },
                },
                dsn=app.state.dsn,
            )
            return saved.to_public_dict()
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.post("/api/model-settings/byok-key")
    async def api_save_byok_key(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return JSONResponse(
                {"error": render_server_line("error.not_logged_in")}, status_code=401
            )
        from puspa.pwa.model_settings import save_byok_key

        try:
            data = await request.json()
            if not isinstance(data, dict):
                raise ValueError("BYOK key body must be a JSON object")
            save_byok_key(
                user_id,
                str(data.get("provider", "")),
                str(data.get("key", "")),
                dsn=app.state.dsn,
            )
            return {"ok": True}
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.delete("/api/model-settings/byok-key")
    async def api_delete_byok_key(request: Request, provider: str = ""):
        user_id = need_user(request)
        if user_id is None:
            return JSONResponse(
                {"error": render_server_line("error.not_logged_in")}, status_code=401
            )
        from puspa.pwa.model_settings import delete_byok_key

        try:
            delete_byok_key(user_id, provider, dsn=app.state.dsn)
            return {"ok": True}
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    # -- end G33 ---------------------------------------------------------

    # -- P-02: stop-all + autonomy dial + weekly report -------------
    # One dial per process, owned by the app: the turn loop consults
    # this same instance when it runs in-process, and the PWA
    # controls (ceilings, suggestions) read and write it. The
    # controls set every shown area's ceiling to Ask first at
    # startup; Money is locked there by the dial itself.
    from puspa.autonomy.controls import AutonomyControls
    from puspa.autonomy.dial import AutonomyDial

    app.state.autonomy_dial = AutonomyDial()
    app.state.autonomy_controls = AutonomyControls(app.state.autonomy_dial)
    # -- end P-02 ------------------------------------------------------

    # -- G28: insights + onboarding (wiring) ---------------------------
    # The two G28 API modules mount their own routes on this app
    # with the app's login resolver; the pages themselves already
    # exist through the SCREENS loop above. Insights and Onboarding
    # fetch all their data through these APIs, so _context_screen
    # needs no new bootstrap data for them.
    from puspa.pwa import (
        approval_overlay_api,
        artifacts_api,
        chat_cards_api,
        chat_widgets_api,
        computer_api,
        controls_api,
        diagnostics_api,
        drafts_api,
        graph_api,
        hub_api,
        insights_api,
        knowledge_api,
        live_view_api,
        memory_api,
        mockup_api,
        names_api,
        onboarding_api,
        personas_api,
        proof_api,
        replay_api,
        telemetry_api,
    )
    # The hosted-only APIs (room sharing, room membership, payments)
    # live in the hosted service and mount their routes there; the
    # local app does not import them.

    app.state.graph_views = graph_api.GraphViewService(dsn=app.state.dsn)
    app.state.memory_service = memory_api.MemoryReadService(dsn=app.state.dsn)
    graph_api.register_routes(app, need_user)
    memory_api.register_routes(app, need_user)
    # V2-27: the caller's own display name (read + edit). The room
    # routes themselves live in rooms_api (local rooms, backed by
    # the public rooms stores); only delegation and money splits
    # stay in the hosted service.
    names_api.register_routes(app, need_user)
    # Multiple switchable personas (one active at a time; the
    # turn loop reads the active one fresh every turn).
    personas_api.register_routes(app, need_user)
    from puspa.pwa import rooms_api

    rooms_api.register_routes(app, need_user)
    controls_api.register_routes(app, need_user)
    # V2-35: computer tiers (allowlist, emergency stop, audit).
    # The allowlist and the stop flag are the same files the
    # `puspa computer` CLI reads and writes, so the panel and the
    # CLI always agree; the audit sink reads the app's ledger.
    from puspa.computer.allowlist import Allowlist
    from puspa.computer.audit import LedgerComputerAudit
    from puspa.computer.stop import ComputerStop
    from puspa.ledger import Ledger

    app.state.computer_allowlist = Allowlist()
    app.state.computer_stop = ComputerStop()
    app.state.computer_audit = LedgerComputerAudit(Ledger(dsn=app.state.dsn))
    computer_api.register_routes(app, need_user)

    # P-09: live view + takeover. The store is one per process;
    # the turn loop consults the same table through its own
    # TakeoverStore, so the hold the PWA sets is the hold the
    # loop enforces.
    from puspa.pwa.live_view import TakeoverStore

    app.state.takeover_store = TakeoverStore(dsn=app.state.dsn)
    live_view_api.register_routes(app, need_user)
    # P-11: visual approval overlay over the G4 gate. The store
    # is one per process, like the gate's requests; the ledger is
    # the durable record of every proposal and decision.
    from puspa.pwa.approval_overlay import OverlayStore

    app.state.overlay_store = OverlayStore(dsn=app.state.dsn)
    approval_overlay_api.register_routes(app, need_user)
    # P-12: rich cards in Chat (draft / options / chart / map).
    # The cards ride on the in-memory chat store, so no new
    # table and no new screen: the Chat screen renders them.
    chat_cards_api.register_routes(app, need_user)
    # P-14: interactive widgets in Chat (calculator / form /
    # comparison / diagram / simulation). Same seam as the
    # cards: declarative JSON on a chat message, in-memory
    # chat store, no new table and no new screen.
    chat_widgets_api.register_routes(app, need_user)
    # P-10: replay timeline + GIF export, same login resolver.
    replay_api.register_routes(app, need_user)
    # Redacted diagnostics export for bug reports (one-click
    # download); the screen itself needs no bootstrap data.
    diagnostics_api.register_routes(app, need_user)
    # Artifacts + run-logs browser (list + download; run-log half reuses replay).
    artifacts_api.register_routes(app, need_user)
    # Skill marketplace hub (COW-03): browse, search, one-click install.
    hub_api.register_routes(app, need_user)
    # AST-02: document knowledge base (upload / list / ask / delete).
    # The screen itself needs no bootstrap data: it fetches
    # everything through the knowledge APIs.
    knowledge_api.register_routes(app, need_user)
    mockup_api.register_routes(app, need_user)
    insights_api.register_routes(app, need_user)
    onboarding_api.register_routes(app, need_user)
    drafts_api.register_routes(app, need_user)
    proof_api.register_routes(app, need_user)
    # P-03: Web Share Target (GET/POST /share) and voice messages
    # (POST /api/voice/*). The share handler renders the Chat screen
    # with the shared text prefilled as a draft; nothing is sent
    # until the user taps Send.
    from puspa.pwa import share_api, voice_api

    def _render_shared_chat(request, user_id, shared_text):
        return spa_or_template(
            "chat",
            request,
            {**_context_screen("chat", user_id), "shared_text": shared_text},
        )

    share_api.register_routes(app, need_user, _render_shared_chat, share_inbox_dir=share_inbox_dir)
    voice_api.register_routes(app, need_user)
    # Receipt photos (plan section 12.5): upload -> draft ->
    # confirm-or-discard. Nothing is recorded from a photo alone.
    from puspa.pwa import receipt_api

    receipt_api.register_routes(app, need_user)
    # V2-28: Puspa-native subscription login (terminal + PWA share
    # the same ProviderLoginService; see puspa/pwa/provider_login_api.py).
    from puspa.pwa import provider_login_api

    provider_login_api.register_routes(app, need_user)
    # V2-26: agent avatar photo (upload / get / serve / delete).
    # Registered after share_api so app.state.share_inbox_dir is
    # already set; the avatar lives under the same per-user base.
    from puspa.pwa import avatar_api

    avatar_api.register_routes(app, need_user)
    # P-07: morning summary + notes-to-actions (plan section 12.8).
    # The screen itself needs no bootstrap data: it fetches
    # everything through the morning APIs.
    from puspa.pwa import morning_api

    morning_api.register_routes(app, need_user)
    # AST-09: the telemetry toggle in Settings (GET/POST
    # /api/telemetry/settings). Off by default; the sender in
    # puspa.telemetry only posts when the effective switch is on.
    telemetry_api.register_routes(app, need_user)
    # -- end G28 ---------------------------------------------------------

    @app.post("/api/onboarding/waktu")
    async def api_onboarding_time(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return JSONResponse(
                {"error": render_server_line("error.not_logged_in")}, status_code=401
            )
        data = await request.json()
        app.state.time_onboarding.append(
            {"user_id": user_id, "seconds": data.get("seconds"), "created_at": time.time()}
        )
        return {"ok": True}

    @app.get("/manifest.json")
    def manifest():
        return {
            "name": "Puspa",
            "short_name": "Puspa",
            "start_url": "/chat",
            "display": "standalone",
            "background_color": "#ffffff",
            "theme_color": "#0f766e",
            "icons": [{"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png"}],
            # P-03: Web Share Target. The installed PWA appears in
            # Android's share menu; shared text/URL/files land on
            # POST /share (multipart, so files arrive too). iOS
            # Safari does not support share targets — see
            # docs/pwa-share-and-voice.md for per-platform support.
            "share_target": {
                "action": "/share",
                "method": "POST",
                "enctype": "multipart/form-data",
                "params": {
                    "title": "title",
                    "text": "text",
                    "url": "url",
                    "files": [
                        {
                            "name": "files",
                            "accept": ["image/*", "application/pdf", "text/plain"],
                        }
                    ],
                },
            },
        }

    @app.get("/sw.js")
    def service_worker():
        sw = (
            (STATIC_DIR / "sw.js").read_text(encoding="utf-8")
            if (STATIC_DIR / "sw.js").exists()
            else ""
        )
        return HTMLResponse(sw, media_type="application/javascript")

    return app


def run(*, host: str = "127.0.0.1", port: int = 8000) -> None:
    """Jalankan PWA lokal (uvicorn)."""
    import uvicorn

    uvicorn.run(create_app(), host=host, port=port)
