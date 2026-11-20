"""HTTP layer for the Web Share Target (P-03, plan section 12.4).

The installed PWA appears in Android's share menu (see the
``share_target`` entry in ``GET /manifest.json``), so a user can
"Share to Puspa" from WhatsApp, a browser, or the gallery without
any WhatsApp API. The plan's "forward to Puspa" flow stays alive
this way; WhatsApp integration itself is out of scope (Meta's
WhatsApp Business API terms, effective 15 Jan 2026, bar
general-purpose AI assistants there).

Contract (all routes require login; anonymous visitors are sent to
``/masuk``, like every other screen):

- ``GET /share?title&text&url`` -> the Chat screen with the shared
  content prefilled as a draft. Nothing is sent yet: the user
  reviews the draft and taps Send, the same confirmation rule as
  for voice transcripts.
- ``POST /share`` (multipart) -> the same, plus any uploaded files.
  Files are stored in the per-user share inbox and announced in
  chat as a message listing each file, so the agent can read them
  later. The response redirects to ``/chat`` (303).
- A share with no text, no URL, and no files redirects to ``/chat``.

``register_routes`` takes ``render_shared_chat`` — a callable the
host app supplies (``(request, user_id, shared_text) -> Response``)
— because only the host app knows how to render its Chat screen.
``puspa.pwa.app.create_app`` passes a wrapper around its own
``spa_or_template``; the Jinja fallback renders the same draft
through the ``shared_text`` template context.

Platform support is recorded honestly in
``docs/pwa-share-and-voice.md``: Android (Chrome) supports the
share target fully; iOS Safari does not support Web Share Target,
so iPhone users share through the in-app chat instead.
"""

from __future__ import annotations

import os
import re
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.datastructures import UploadFile as StarletteUploadFile

__all__ = ["compose_shared_text", "register_routes", "sanitize_filename"]

#: Most files one share may carry (a gallery multi-select).
MAX_SHARED_FILES = 5

#: Largest single shared file (10 MiB — phone photos fit; videos do not).
MAX_SHARED_FILE_BYTES = 10 * 1024 * 1024

#: What a share may carry: photos from the gallery, PDFs, plain text.
#: Videos are refused: they are large, and nothing in the app reads
#: video yet — refusing loudly beats storing bytes nobody can use.
ALLOWED_SHARED_MIME_TYPES = frozenset({"application/pdf", "text/plain"})

#: Default inbox when no directory is configured (env
#: ``PUSPA_SHARE_INBOX_DIR`` or the ``share_inbox_dir`` argument).
DEFAULT_INBOX_DIR = Path.home() / ".puspa" / "share-inbox"

_FILENAME_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def compose_shared_text(title: str = "", text: str = "", url: str = "") -> str:
    """One chat draft from the three share-target fields.

    Empty parts are dropped; the rest join with a blank line, title
    first, URL last. A URL-only share is just the URL; a WhatsApp
    forward is just its text.
    """
    parts = [part.strip() for part in (title, text, url) if part.strip()]
    return "\n\n".join(parts)


def sanitize_filename(name: str) -> str:
    """A safe storage name: no directories, no odd characters."""
    base = Path(name).name
    clean = _FILENAME_SAFE.sub("_", base).strip("._")[:80]
    return clean or "shared-file"


def _is_allowed_file(mime_type: str) -> bool:
    return mime_type.startswith("image/") or mime_type in ALLOWED_SHARED_MIME_TYPES


def _post_chat_message(app, user_id: str, text: str) -> dict:
    """Append a message to the user's chat, as ``POST /api/chat`` does."""
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
    return message


def _human_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.0f} KB"
    return f"{size_bytes / (1024 * 1024):.1f} MB"


def register_routes(app: FastAPI, need_user, render_shared_chat, share_inbox_dir=None):
    """Mount the share-target routes on ``app`` using its login resolver."""
    inbox_dir = Path(share_inbox_dir or os.environ.get("PUSPA_SHARE_INBOX_DIR", DEFAULT_INBOX_DIR))
    app.state.share_inbox_dir = inbox_dir

    @app.get("/share", response_class=HTMLResponse)
    async def share_get(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return RedirectResponse("/masuk")
        shared = compose_shared_text(
            title=request.query_params.get("title", ""),
            text=request.query_params.get("text", ""),
            url=request.query_params.get("url", ""),
        )
        if not shared:
            return RedirectResponse("/chat")
        return render_shared_chat(request, user_id, shared)

    @app.post("/share")
    async def share_post(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return RedirectResponse("/masuk")
        form = await request.form()
        shared = compose_shared_text(
            title=str(form.get("title", "") or ""),
            text=str(form.get("text", "") or ""),
            url=str(form.get("url", "") or ""),
        )
        files = form.getlist("files")
        if len(files) > MAX_SHARED_FILES:
            return JSONResponse(
                {"error": f"at most {MAX_SHARED_FILES} files per share"},
                status_code=400,
            )
        stored: list[dict] = []
        for upload in files:
            # request.form() yields starlette's UploadFile, not
            # FastAPI's — isinstance against FastAPI's class is
            # always false here and would silently drop every file.
            if not isinstance(upload, StarletteUploadFile):
                continue
            content = await upload.read()
            if not _is_allowed_file(upload.content_type or ""):
                return JSONResponse(
                    {"error": f"unsupported shared file type {upload.content_type!r}"},
                    status_code=400,
                )
            if len(content) > MAX_SHARED_FILE_BYTES:
                return JSONResponse(
                    {"error": f"file {upload.filename!r} is over the 10 MB limit"},
                    status_code=400,
                )
            user_dir = inbox_dir / user_id
            user_dir.mkdir(parents=True, exist_ok=True)
            stored_name = f"{uuid.uuid4().hex}_{sanitize_filename(upload.filename or '')}"
            stored_path = user_dir / stored_name
            stored_path.write_bytes(content)
            stored.append(
                {
                    "filename": upload.filename or stored_name,
                    "stored_as": stored_name,
                    "size_bytes": len(content),
                    "mime_type": upload.content_type or "",
                }
            )
        if stored:
            lines = ["Dibagikan dari luar Puspa:"]
            lines.extend(
                f"- {item['filename']} ({_human_size(item['size_bytes'])})" for item in stored
            )
            if shared:
                lines.append("")
                lines.append(shared)
            _post_chat_message(app, user_id, "\n".join(lines))
            return RedirectResponse("/chat", status_code=303)
        if not shared:
            return RedirectResponse("/chat", status_code=303)
        return render_shared_chat(request, user_id, shared)
