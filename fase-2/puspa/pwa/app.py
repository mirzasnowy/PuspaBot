"""PWA Puspa — 7 layar, tidak lebih (§2.1 revisi 7).

Stack (keputusan 1): FastAPI + Jinja + HTMX + service worker.
Update live lewat SSE dari proyeksi Buku Kerja, tanpa WebSocket
di MVP. Layar: Chat, Tugas, Modul, Konektor, Memori, Pengaturan,
Kantor. Bukti, Rencana, Rekor Otonomi, alur, dan modul kustom adalah
panel di dalam layar yang sudah ada — bukan layar baru.

Login: Google (keputusan 16, fondasi Fase 1 `puspa.auth`); sesi
cookie httpOnly via `puspa.accounts.sessions`. Di tes, `create_app`
menerima `test_user_id` (seam tes saja; di produksi None dan cookie
wajib). Pesan chat + kejadian demo disimpan di `app.state` (memori
proses) untuk demo lokal; jalur produksi menulis ke Buku Kerja
(`Ledger`) — lihat `catat_kejadian`.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from collections import defaultdict
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader

BASE_DIR = Path(__file__).resolve().parent.parent.parent
TEMPLATE_DIR = BASE_DIR / "apps" / "pwa" / "templates"
STATIC_DIR = BASE_DIR / "apps" / "pwa" / "static"

LAYAR = ("chat", "tugas", "modul", "konektor", "memori", "pengaturan", "kantor")


def _jinja(template_dir=None):
    return Environment(loader=FileSystemLoader(str(template_dir or TEMPLATE_DIR)),
                       autoescape=True)


def create_app(*, dsn=None, test_user_id=None, template_dir=None) -> FastAPI:
    """Bangun aplikasi FastAPI PWA."""
    app = FastAPI(title="Puspa")
    jinja = _jinja(template_dir)
    app.state.dsn = dsn or os.environ.get("PUSPA_TEST_DSN",
                                          "dbname=puspa_test user=root host=/var/run/postgresql")
    app.state.test_user_id = test_user_id
    app.state.chat: dict[str, list[dict]] = defaultdict(list)
    app.state.kejadian: list[dict] = []
    app.state.persetujuan: dict[str, dict] = {}
    app.state.waktu_onboarding: list[dict] = []

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    def pengguna(request: Request):
        if app.state.test_user_id:
            return app.state.test_user_id
        token = request.cookies.get("puspa_sesi")
        if not token:
            return None
        try:
            from puspa.accounts.sessions import validate_session

            return validate_session(token, dsn=app.state.dsn)
        except Exception:
            return None

    def butuh_pengguna(request: Request):
        user_id = pengguna(request)
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

    def render(nama: str, request: Request, konteks: dict | None = None):
        ctx = {"request": request, "layar": nama.replace(".html", "")}
        ctx.update(konteks or {})
        return HTMLResponse(jinja.get_template(nama).render(ctx))

    # -- halaman -------------------------------------------------------
    @app.get("/", response_class=HTMLResponse)
    def beranda(request: Request):
        return RedirectResponse("/chat")

    @app.get("/masuk", response_class=HTMLResponse)
    def masuk(request: Request):
        client_id = os.environ.get("PUSPA_GOOGLE_CLIENT_ID", "")
        return render("masuk.html", request, {"client_id_ada": bool(client_id)})

    @app.get("/auth/google")
    def auth_google(request: Request):
        from puspa.auth.google_oauth import build_auth_url

        client_id = os.environ.get("PUSPA_GOOGLE_CLIENT_ID", "")
        redirect = os.environ.get("PUSPA_GOOGLE_REDIRECT_URI", "http://localhost:8000/auth/google/callback")
        if not client_id:
            return JSONResponse({"galat": "login Google belum dikonfigurasi (PUSPA_GOOGLE_CLIENT_ID kosong)"},
                                status_code=503)
        url, _state, _verifier = build_auth_url(client_id, redirect)
        return RedirectResponse(url)

    @app.get("/auth/google/callback")
    def auth_callback(request: Request, code: str = "", state: str = ""):
        return JSONResponse({"catatan": "callback login Google ditangani lengkap di Fase 1"
                                       " (complete_google_login); pasang client_id + client_secret"
                                       " lalu lanjutkan di sini.",
                             "code_diterima": bool(code)})

    for layar in LAYAR:
        def _buat_halaman(layar=layar):
            def halaman(request: Request):
                user_id = butuh_pengguna(request)
                if user_id is None:
                    return RedirectResponse("/masuk")
                return render(f"{layar}.html", request, _konteks_layar(layar, user_id))
            return halaman
        app.get(f"/{layar}", response_class=HTMLResponse)(_buat_halaman())

    def _konteks_layar(layar: str, user_id: str) -> dict:
        from puspa.evidence.projections import kantor as proyeksi_kantor

        ctx: dict = {"user_id": user_id}
        if layar == "chat":
            ctx["pesan"] = app.state.chat[user_id]
        elif layar == "kantor":
            ctx["kantor"] = proyeksi_kantor(app.state.kejadian)
            ctx["handoff"] = [e for e in app.state.kejadian if e.get("jenis", "").startswith("handoff_")]
        elif layar == "tugas":
            ctx["persetujuan"] = list(app.state.persetujuan.values())
        elif layar == "memori":
            try:
                from puspa.memory.profile import list_facts
                ctx["profil"] = list_facts(user_id, dsn=app.state.dsn)
            except Exception:
                ctx["profil"] = []
        elif layar == "konektor":
            try:
                from puspa.runtime.agents import daftar_agen
                ctx["agen"] = daftar_agen(user_id, dsn=app.state.dsn)
            except Exception:
                ctx["agen"] = []
        return ctx

    # -- SSE: update live dari proyeksi Buku Kerja ----------------------
    @app.get("/sse")
    def sse(request: Request):
        user_id = pengguna(request)
        if user_id is None:
            return JSONResponse({"galat": "belum masuk"}, status_code=401)

        def aliran():
            from puspa.evidence.projections import kantor as proyeksi_kantor

            yield f"data: {json.dumps({'jenis': 'kantor', 'isi': proyeksi_kantor(app.state.kejadian)}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'jenis': 'detak', 'waktu': time.time()}, ensure_ascii=False)}\n\n"

        return StreamingResponse(aliran(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache"})

    # -- panel Bukti -----------------------------------------------------
    @app.get("/bukti/{bukti_id}", response_class=HTMLResponse)
    def panel_bukti(request: Request, bukti_id: str):
        user_id = butuh_pengguna(request)
        if user_id is None:
            return RedirectResponse("/masuk")
        bukti = next((b for b in app.state.kejadian
                      if b.get("jenis") == "bukti_dicatat"
                      and (b.get("isi") or {}).get("bukti_id") == bukti_id), None)
        return render("panel_bukti.html", request, {"bukti": (bukti or {}).get("isi") if bukti else None,
                                                    "bukti_id": bukti_id})

    # -- API -------------------------------------------------------------
    @app.post("/api/chat")
    async def kirim_chat(request: Request, teks: str = Form(...)):
        user_id = butuh_pengguna(request)
        if user_id is None:
            return JSONResponse({"galat": "belum masuk"}, status_code=401)
        pesan = {"dari": "user", "teks": teks, "waktu": time.time()}
        app.state.chat[user_id].append(pesan)
        app.state.kejadian.append({"jenis": "pesan_user", "tugas_id": "", "agen_id": "puspa",
                                   "isi": {"dari": user_id, "teks": teks}, "waktu": time.time()})
        return {"ok": True, "pesan": pesan}

    @app.post("/api/agen")
    async def api_buat_agen(request: Request, nama: str = Form(...),
                            peran: str = Form(""), tujuan: str = Form("")):
        user_id = butuh_pengguna(request)
        if user_id is None:
            return JSONResponse({"galat": "belum masuk"}, status_code=401)
        from puspa.runtime.agents import buat_agen

        try:
            return buat_agen(user_id, nama, peran, tujuan, dsn=app.state.dsn)
        except Exception as exc:
            return JSONResponse({"galat": str(exc)}, status_code=400)

    @app.delete("/api/agen/{agen_id}")
    async def api_hapus_agen(request: Request, agen_id: str):
        user_id = butuh_pengguna(request)
        if user_id is None:
            return JSONResponse({"galat": "belum masuk"}, status_code=401)
        from puspa.runtime.agents import hapus_agen

        try:
            hapus_agen(user_id, agen_id, dsn=app.state.dsn)
            return {"ok": True}
        except Exception as exc:
            return JSONResponse({"galat": str(exc)}, status_code=400)

    @app.post("/api/tugas")
    async def api_buat_tugas(request: Request, tujuan: str = Form(...)):
        user_id = butuh_pengguna(request)
        if user_id is None:
            return JSONResponse({"galat": "belum masuk"}, status_code=401)
        from puspa.runtime.agents import daftar_agen, ensure_puspa_agent
        from puspa.runtime.tasks import Task, TaskRunner, buat_tugas_id

        agen_id = ensure_puspa_agent(user_id, dsn=app.state.dsn)
        runner = TaskRunner(sink=lambda jenis, isi: app.state.kejadian.append(
            {"jenis": jenis, "tugas_id": isi.get("tugas_id", ""), "agen_id": agen_id,
             "isi": isi, "waktu": time.time()}))
        task = runner.jalankan(Task(tugas_id=buat_tugas_id(), user_id=user_id,
                                    agen_id=agen_id, tujuan=tujuan))
        return {"tugas_id": task.tugas_id, "status": task.status, "tahap": task.tahap,
                "pertanyaan": task.pertanyaan}

    @app.post("/api/tugas/{tugas_id}/hentikan")
    async def api_hentikan(request: Request, tugas_id: str):
        user_id = butuh_pengguna(request)
        if user_id is None:
            return JSONResponse({"galat": "belum masuk"}, status_code=401)
        from puspa.runtime.subagents import SubagentPool

        kolam = app.state.__dict__.setdefault("kolam", SubagentPool())
        hasil = kolam.hentikan_tugas(tugas_id)
        app.state.kejadian.append({"jenis": "tugas_dihentikan", "tugas_id": tugas_id,
                                   "agen_id": "puspa", "isi": {"alasan": "dihentikan user"},
                                   "waktu": time.time()})
        return hasil

    @app.post("/api/persetujuan/{request_id}")
    async def api_persetujuan(request: Request, request_id: str, setuju: str = Form(...)):
        user_id = butuh_pengguna(request)
        if user_id is None:
            return JSONResponse({"galat": "belum masuk"}, status_code=401)
        item = app.state.persetujuan.get(request_id)
        if item is None:
            return JSONResponse({"galat": "permintaan tidak dikenal"}, status_code=404)
        item["status"] = "disetujui" if setuju == "ya" else "ditolak"
        app.state.kejadian.append(
            {"jenis": "persetujuan_diberikan" if setuju == "ya" else "persetujuan_ditolak",
             "tugas_id": item.get("tugas_id", ""), "agen_id": "puspa",
             "isi": {"request_id": request_id}, "waktu": time.time()})
        return {"ok": True, "status": item["status"]}

    @app.post("/api/konektor/{konektor}")
    async def api_konektor(request: Request, konektor: str,
                           agen_id: str = Form(...), aksi: str = Form(...)):
        user_id = butuh_pengguna(request)
        if user_id is None:
            return JSONResponse({"galat": "belum masuk"}, status_code=401)
        from puspa.connectors.grants import beri_konektor, cabut_konektor

        try:
            if aksi == "cabut":
                cabut_konektor(user_id, agen_id, konektor, dsn=app.state.dsn)
                return {"ok": True, "terhubung": False}
            return beri_konektor(user_id, agen_id, konektor, dsn=app.state.dsn)
        except Exception as exc:
            return JSONResponse({"galat": str(exc)}, status_code=400)

    @app.post("/api/push/langganan")
    async def api_push(request: Request):
        user_id = butuh_pengguna(request)
        if user_id is None:
            return JSONResponse({"galat": "belum masuk"}, status_code=401)
        data = await request.json()
        from puspa.push.push import simpan_langganan

        langganan_id = simpan_langganan(
            user_id, endpoint=data["endpoint"], p256dh=data["keys"]["p256dh"],
            auth=data["keys"]["auth"], dsn=app.state.dsn)
        return {"ok": True, "langganan_id": langganan_id}

    @app.post("/api/onboarding/waktu")
    async def api_onboarding_waktu(request: Request):
        user_id = butuh_pengguna(request)
        if user_id is None:
            return JSONResponse({"galat": "belum masuk"}, status_code=401)
        data = await request.json()
        app.state.waktu_onboarding.append({"user_id": user_id, "detik": data.get("detik"),
                                           "waktu": time.time()})
        return {"ok": True}

    @app.get("/manifest.json")
    def manifest():
        return {"name": "Puspa", "short_name": "Puspa", "start_url": "/chat",
                "display": "standalone", "background_color": "#ffffff",
                "theme_color": "#0f766e",
                "icons": [{"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png"}]}

    @app.get("/sw.js")
    def service_worker():
        sw = (STATIC_DIR / "sw.js").read_text(encoding="utf-8") if (STATIC_DIR / "sw.js").exists() else ""
        return HTMLResponse(sw, media_type="application/javascript")

    return app


def jalankan(*, host: str = "127.0.0.1", port: int = 8000) -> None:
    """Jalankan PWA lokal (uvicorn)."""
    import uvicorn

    uvicorn.run(create_app(), host=host, port=port)
