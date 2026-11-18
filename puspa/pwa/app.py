"""PWA Puspa — 9 layar (7 dari revisi 7, ditambah Insights dan
Onboarding oleh kerja kematangan G28).

Stack (keputusan 1, diperbarui G1): FastAPI + TypeScript SPA
(React 19 + Vite, `apps/pwa/`, dibangun ke `apps/pwa/dist`) + service
worker. Kalau `dist` belum dibangun, layar jatuh kembali ke template
Jinja lama. Kontrak API: `docs/openapi-pwa.yaml`.
Update live lewat SSE dari proyeksi Buku Kerja, tanpa WebSocket
di MVP. Layar: Chat, Tugas, Modul, Konektor, Memori, Pengaturan,
Kantor, Insights, Onboarding. Bukti, Rencana, Rekor Otonomi, alur,
dan modul kustom adalah panel di dalam layar yang sudah ada —
bukan layar baru.

Catatan jujur soal jumlah layar: revisi 7 membatasi PWA pada 7
layar, tetapi perintah kematangan Nero (kerjakan semua 28 celah
G1-G28) mewajibkan halaman Insights (celah biaya/token) dan
Onboarding pertama kali (G28). Batas 7 layar itu sudah tidak
berlaku lagi setelah penambahan kematangan; total sekarang 9.

Login: Google (keputusan 16, fondasi Fase 1 `puspa.auth`); sesi
cookie httpOnly via `puspa.accounts.user_sessions`. Di tes, `create_app`
menerima `test_user_id` (seam tes saja; di produksi None dan cookie
wajib). Pesan chat + kejadian demo disimpan di `app.state` (memori
proses) untuk demo lokal; jalur produksi menulis ke Buku Kerja
(`Ledger`) — lihat `catat_kejadian`.
"""

from __future__ import annotations

import json
import os
import time
from collections import defaultdict
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader

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
    "office",
    "insights",
    "onboarding",
)


def _jinja(template_dir=None):
    return Environment(loader=FileSystemLoader(str(template_dir or TEMPLATE_DIR)), autoescape=True)


def create_app(*, dsn=None, test_user_id=None, template_dir=None) -> FastAPI:
    """Bangun aplikasi FastAPI PWA."""
    app = FastAPI(title="Puspa")
    jinja = _jinja(template_dir)
    app.state.dsn = dsn or os.environ.get(
        "PUSPA_TEST_DSN", "dbname=puspa_test user=root host=/var/run/postgresql"
    )
    app.state.test_user_id = test_user_id
    # Anotasi tipe tidak bisa ditulis pada atribut non-self
    # (app.state.*), jadi tipenya dicatat di variabel lokal dulu.
    chat_state: dict[str, list[dict]] = defaultdict(list)
    app.state.chat = chat_state
    app.state.event = []
    app.state.approval = {}
    app.state.time_onboarding = []

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
            from puspa.accounts.user_sessions import validate_session

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
            }
            payload = (
                json.dumps(bootstrap, ensure_ascii=False)
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
                {"error": "login Google belum dikonfigurasi (PUSPA_GOOGLE_CLIENT_ID kosong)"},
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
        elif screen == "memory":
            try:
                from puspa.memory.profile import list_facts

                ctx["profile"] = list_facts(user_id, dsn=app.state.dsn)
            except Exception:
                ctx["profile"] = []
        elif screen == "connectors":
            try:
                from puspa.runtime.agents import list_agents

                ctx["agents"] = list_agents(user_id, dsn=app.state.dsn)
            except Exception:
                ctx["agents"] = []
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
            return JSONResponse({"error": "belum masuk"}, status_code=401)

        def aliran():
            from puspa.evidence.projections import office as office_projection

            yield f"data: {json.dumps({'type': 'office', 'content': office_projection(app.state.event)}, ensure_ascii=False)}\n\n"
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
            return JSONResponse({"error": "belum masuk"}, status_code=401)
        from puspa.pwa.serve_modes import operator_status

        return operator_status(
            app.state.serve_mode,
            pairing_store=app.state.pairing_store,
            gateway=app.state.gateway,
            decision_log=app.state.gateway_log,
            recent_events=app.state.event,
        )

    # -- end G31 ---------------------------------------------------------

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
            return JSONResponse({"error": "belum masuk"}, status_code=401)
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
            return JSONResponse({"error": "belum masuk"}, status_code=401)
        from puspa.runtime.agents import create_agent

        try:
            return create_agent(user_id, name, role, purpose, dsn=app.state.dsn)
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.delete("/api/agen/{agent_id}")
    async def api_delete_agent(request: Request, agent_id: str):
        user_id = need_user(request)
        if user_id is None:
            return JSONResponse({"error": "belum masuk"}, status_code=401)
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
            return JSONResponse({"error": "belum masuk"}, status_code=401)
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
            return JSONResponse({"error": "belum masuk"}, status_code=401)
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
            return JSONResponse({"error": "belum masuk"}, status_code=401)
        item = app.state.approval.get(request_id)
        if item is None:
            return JSONResponse({"error": "permintaan tidak dikenal"}, status_code=404)
        item["status"] = "approved" if approve == "ya" else "rejected"
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

    @app.post("/api/konektor/{konektor}")
    async def api_connector(
        request: Request, connector: str, agent_id: str = Form(...), action: str = Form(...)
    ):
        user_id = need_user(request)
        if user_id is None:
            return JSONResponse({"error": "belum masuk"}, status_code=401)
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
            return JSONResponse({"error": "belum masuk"}, status_code=401)
        data = await request.json()
        from puspa.push.push import save_subscription

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
            return JSONResponse({"error": "belum masuk"}, status_code=401)
        from puspa.pwa.model_settings import get_selection

        try:
            return get_selection(user_id, dsn=app.state.dsn).to_public_dict()
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.post("/api/model-settings")
    async def api_save_model_settings(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return JSONResponse({"error": "belum masuk"}, status_code=401)
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
            return JSONResponse({"error": "belum masuk"}, status_code=401)
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
            return JSONResponse({"error": "belum masuk"}, status_code=401)
        from puspa.pwa.model_settings import delete_byok_key

        try:
            delete_byok_key(user_id, provider, dsn=app.state.dsn)
            return {"ok": True}
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    # -- end G33 ---------------------------------------------------------

    # -- G28: insights + onboarding (wiring) ---------------------------
    # The two G28 API modules mount their own routes on this app
    # with the app's login resolver; the pages themselves already
    # exist through the SCREENS loop above. Insights and Onboarding
    # fetch all their data through these APIs, so _context_screen
    # needs no new bootstrap data for them.
    from puspa.pwa import insights_api, onboarding_api

    insights_api.register_routes(app, need_user)
    onboarding_api.register_routes(app, need_user)
    # -- end G28 ---------------------------------------------------------

    @app.post("/api/onboarding/waktu")
    async def api_onboarding_time(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return JSONResponse({"error": "belum masuk"}, status_code=401)
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
