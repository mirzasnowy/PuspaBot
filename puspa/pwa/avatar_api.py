"""HTTP layer for the agent avatar photo (V2-26).

The agent gets a profile photo the user can change from the PWA,
and the avatar in Chat animates from the real agent state (see
``apps/pwa/src/state/avatar.ts``). This module owns only the
photo; the animation state comes from the live office projection
and the chat turn, never from a timer.

Contract (all routes require login):

- ``POST /api/agent/avatar/photo`` (multipart ``photo``) ->
  ``{ok, has_photo, photo_url, updated_at, mime_type}``. The
  photo must be a jpeg, png, webp, or gif, non-empty, at most
  2 MiB, and its bytes must decode as an image (Pillow check; a
  renamed text file is refused). 400 otherwise. The photo is
  stored per user under ``avatar/`` in the share inbox dir
  (``app.state.share_inbox_dir``, the same base the receipt
  photos use): one file per user, ``avatar.<ext>``, replaced
  wholesale on every new upload, so an old extension never
  lingers next to the new one.
- ``GET /api/agent/avatar`` -> ``{ok, has_photo, photo_url,
  updated_at, mime_type}``. ``photo_url`` is null and
  ``updated_at`` is null while no photo is set; the PWA then
  shows the default Puspa logo instead.
- ``GET /api/agent/avatar/photo`` -> the stored photo bytes
  with the right media type. 404 (``{ok: false, has_photo:
  false}``) while no photo is set, so the caller can fall back
  to the default logo.
- ``DELETE /api/agent/avatar/photo`` -> removes the stored
  photo, ``{ok: true, has_photo: false}``; deleting again is
  still ok (idempotent).

The photo is a profile picture, not an expense or a message:
it is never OCR-read, never classified, and never posted to
chat. Identity documents are refused by the same shape check
as receipts only in the sense that a non-photo is refused;
no document classification runs here.
"""

from __future__ import annotations

import contextlib
import time
from io import BytesIO
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from ..i18n.server_lines import render as render_server_line
from .share_api import DEFAULT_INBOX_DIR

__all__ = ["MAX_AVATAR_BYTES", "register_routes"]

#: Largest avatar photo the endpoint accepts (2 MiB: a profile
#: photo is small; phone photos fit after the gallery downscales
#: them, and the PWA previews before upload).
MAX_AVATAR_BYTES = 2 * 1024 * 1024

#: Mime types a browser can display as an <img>. HEIC/HEIF are
#: left out on purpose: phone galleries produce them, but the
#: PWA cannot render them, so accepting one would store a photo
#: the avatar could never show.
ALLOWED_AVATAR_MIME_TYPES = frozenset({"image/jpeg", "image/png", "image/webp", "image/gif"})

_MIME_SUFFIX = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}

#: Magic bytes per type, used when Pillow is unavailable.
_MAGIC_BYTES = {
    b"\xff\xd8\xff": "image/jpeg",
    b"\x89PNG\r\n\x1a\n": "image/png",
    b"RIFF": "image/webp",
    b"GIF87a": "image/gif",
    b"GIF89a": "image/gif",
}


class AvatarPhotoError(ValueError):
    """An uploaded avatar photo failed validation (type, size, or content)."""


def validate_avatar_photo(image: bytes, mime_type: str) -> None:
    """Reject avatar photos the PWA cannot display or should not store."""
    if mime_type not in ALLOWED_AVATAR_MIME_TYPES:
        raise AvatarPhotoError(
            f"unsupported photo type {mime_type!r}; "
            f"accepted: {', '.join(sorted(ALLOWED_AVATAR_MIME_TYPES))}"
        )
    if not image:
        raise AvatarPhotoError("empty photo upload")
    if len(image) > MAX_AVATAR_BYTES:
        raise AvatarPhotoError(
            f"photo is {len(image)} bytes, over the {MAX_AVATAR_BYTES} byte limit"
        )
    try:
        from PIL import Image
    except ImportError:
        Image = None  # type: ignore[assignment]
    if Image is not None:
        try:
            with Image.open(BytesIO(image)) as photo:
                photo.verify()
        except Exception as exc:
            raise AvatarPhotoError("photo bytes do not decode as an image") from exc
    elif not any(image.startswith(magic) for magic in _MAGIC_BYTES):
        raise AvatarPhotoError("photo bytes do not look like an image")


def _not_logged_in() -> JSONResponse:
    return JSONResponse({"error": render_server_line("error.not_logged_in")}, status_code=401)


def _avatar_dir(request: Request, user_id: str) -> Path:
    base_dir = getattr(request.app.state, "share_inbox_dir", None) or DEFAULT_INBOX_DIR
    return Path(base_dir) / user_id / "avatar"


def _stored_photo(avatar_dir: Path) -> Path | None:
    if not avatar_dir.is_dir():
        return None
    for suffix in (".jpg", ".png", ".webp", ".gif"):
        candidate = avatar_dir / f"avatar{suffix}"
        if candidate.is_file():
            return candidate
    return None


def _photo_response(avatar_dir: Path) -> dict:
    stored = _stored_photo(avatar_dir)
    if stored is None:
        return {
            "ok": True,
            "has_photo": False,
            "photo_url": None,
            "updated_at": None,
            "mime_type": None,
        }
    mime = next(
        (mime for mime, suffix in _MIME_SUFFIX.items() if suffix == stored.suffix),
        "image/png",
    )
    return {
        "ok": True,
        "has_photo": True,
        "photo_url": "/api/agent/avatar/photo",
        "updated_at": stored.stat().st_mtime,
        "mime_type": mime,
    }


def register_routes(app: FastAPI, need_user) -> FastAPI:
    """Mount the agent avatar routes on ``app`` using its login resolver."""

    @app.post("/api/agent/avatar/photo")
    async def upload_avatar_photo(
        request: Request,
        photo: Annotated[UploadFile, File()],
    ):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        content = await photo.read()
        try:
            validate_avatar_photo(content, photo.content_type or "")
        except AvatarPhotoError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        avatar_dir = _avatar_dir(request, user_id)
        avatar_dir.mkdir(parents=True, exist_ok=True)
        suffix = _MIME_SUFFIX[photo.content_type or "image/png"]
        # One photo per user: drop any previous extension first so
        # a .png upload after a .jpg one leaves no stale file behind.
        for old in avatar_dir.glob("avatar.*"):
            with contextlib.suppress(OSError):
                old.unlink()
        stored_path = avatar_dir / f"avatar{suffix}"
        stored_path.write_bytes(content)
        body = _photo_response(avatar_dir)
        body["updated_at"] = time.time()
        return body

    @app.get("/api/agent/avatar")
    async def get_avatar(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        return _photo_response(_avatar_dir(request, user_id))

    @app.get("/api/agent/avatar/photo")
    async def get_avatar_photo(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        stored = _stored_photo(_avatar_dir(request, user_id))
        if stored is None:
            return JSONResponse(
                {"ok": False, "has_photo": False, "error": "no agent photo set"},
                status_code=404,
            )
        mime = next(
            (mime for mime, suffix in _MIME_SUFFIX.items() if suffix == stored.suffix),
            "image/png",
        )
        return FileResponse(str(stored), media_type=mime)

    @app.delete("/api/agent/avatar/photo")
    async def delete_avatar_photo(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        avatar_dir = _avatar_dir(request, user_id)
        for old in avatar_dir.glob("avatar.*"):
            with contextlib.suppress(OSError):
                old.unlink()
        return {"ok": True, "has_photo": False}

    return app
