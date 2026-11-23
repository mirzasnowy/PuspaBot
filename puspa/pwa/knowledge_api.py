"""HTTP layer for the document knowledge base (AST-02).

Plain handler functions over :mod:`puspa.knowledge.store`, plus
:func:`register_routes`, which mounts them on a FastAPI app.
This module deliberately does not import ``puspa.pwa.app``:
the coordinator wires it into ``create_app`` with a few lines::

    from puspa.pwa import knowledge_api
    knowledge_api.register_routes(app, need_user)

``need_user`` is the app's own login resolver (``Request`` ->
user id or None); it is passed in rather than reimplemented
here, so login behaviour stays in exactly one place.

Contract (all responses are JSON unless noted; all routes
require login):

- ``GET /api/knowledge/documents`` -> the user's uploaded
  documents, newest first.
- ``POST /api/knowledge/upload`` (multipart ``file`` + optional
  ``title``) -> the stored document. 400 when the file is not
  a .txt or .md file, is empty, or is too large.
- ``GET /api/knowledge/ask?q=<question>&limit=5`` -> the
  extractive answer plus citations (source file + chunk
  number + similarity per passage). 400 when ``q`` is empty.
- ``DELETE /api/knowledge/documents/{document_id}`` -> deletes
  one of the user's documents (its chunks go with it). 404
  when the id is unknown or belongs to another user.
- ``401 {"error": "belum masuk"}`` when not logged in.
"""

from __future__ import annotations

from typing import Annotated

from puspa.db import Error as _db_Error
from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import JSONResponse

from ..i18n.server_lines import render as render_server_line
from ..knowledge.store import (
    MAX_UPLOAD_BYTES,
    ask,
    delete_document,
    list_documents,
    upload_document,
)
from ..memory.embeddings import HashEmbedding

__all__ = [
    "ask_documents",
    "delete_one_document",
    "list_user_documents",
    "register_routes",
    "upload_one_document",
]

#: Suffixes the upload endpoint accepts (same set as the store).
_ACCEPTED_SUFFIXES = (".txt", ".md", ".markdown")


def _not_logged_in():
    return JSONResponse({"error": render_server_line("error.not_logged_in")}, status_code=401)


def _bad_request(message: str):
    return JSONResponse({"error": message}, status_code=400)


def list_user_documents(user_id: str, *, dsn=None) -> list[dict]:
    """The user's uploaded documents, newest first."""
    return list_documents(user_id, dsn=dsn)


def upload_one_document(
    user_id: str, filename: str, raw: bytes, *, title: str | None = None, dsn=None
) -> dict:
    """Store one uploaded file's bytes as a document.

    Raises :class:`ValueError` when the file type, size, or
    content is not acceptable (the endpoint turns it into a
    400); raises :class:`_db_Error` on a database failure.
    """
    suffix = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if suffix == ".pdf":
        raise ValueError(
            "PDF files are not supported yet: this repo has no PDF parser "
            "and the knowledge base does not add one. Upload the text "
            "or Markdown version instead."
        )
    if suffix not in _ACCEPTED_SUFFIXES:
        raise ValueError(f"unsupported file type {suffix!r}: upload a .txt or .md file")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise ValueError(f"file is too large ({len(raw)} bytes, limit {MAX_UPLOAD_BYTES})")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{filename} is not UTF-8 text: {exc}") from exc
    if text.strip() == "":
        raise ValueError(f"{filename} is empty")
    content_type = "markdown" if suffix in (".md", ".markdown") else "text"
    return upload_document(
        user_id,
        filename,
        text,
        content_type,
        title=title or None,
        embedder=HashEmbedding(),
        dsn=dsn,
    )


def ask_documents(user_id: str, question: str, *, limit: int = 5, dsn=None) -> dict:
    """Answer ``question`` from the user's uploaded documents."""
    return ask(user_id, question, limit=limit, embedder=HashEmbedding(), dsn=dsn)


def delete_one_document(user_id: str, document_id: str, *, dsn=None) -> bool:
    """Delete one of the user's documents; False when it is not theirs."""
    return delete_document(user_id, document_id, dsn=dsn)


def register_routes(app: FastAPI, need_user) -> FastAPI:
    """Mount the knowledge-base routes on ``app`` using its login resolver."""

    @app.get("/api/knowledge/documents")
    def documents_endpoint(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        try:
            return {"documents": list_user_documents(user_id, dsn=request.app.state.dsn)}
        except _db_Error as exc:
            return JSONResponse({"error": str(exc)}, status_code=500)

    @app.post("/api/knowledge/upload")
    async def upload_endpoint(
        request: Request,
        file: Annotated[UploadFile, File()],
        title: str | None = None,
    ):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        raw = await file.read()
        try:
            document = upload_one_document(
                user_id,
                file.filename or "upload.txt",
                raw,
                title=title,
                dsn=request.app.state.dsn,
            )
        except ValueError as exc:
            return _bad_request(str(exc))
        except _db_Error as exc:
            return JSONResponse({"error": str(exc)}, status_code=500)
        return {"ok": True, "document": document}

    @app.get("/api/knowledge/ask")
    def ask_endpoint(request: Request, q: str = "", limit: int = 5):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        if q.strip() == "":
            return _bad_request("q must be a non-empty question")
        try:
            return ask_documents(user_id, q, limit=limit, dsn=request.app.state.dsn)
        except ValueError as exc:
            return _bad_request(str(exc))
        except _db_Error as exc:
            return JSONResponse({"error": str(exc)}, status_code=500)

    @app.delete("/api/knowledge/documents/{document_id}")
    def delete_endpoint(request: Request, document_id: str):
        user_id = need_user(request)
        if user_id is None:
            return _not_logged_in()
        try:
            deleted = delete_one_document(user_id, document_id, dsn=request.app.state.dsn)
        except _db_Error as exc:
            return JSONResponse({"error": str(exc)}, status_code=500)
        if not deleted:
            return JSONResponse({"error": "document not found"}, status_code=404)
        return {"ok": True, "deleted": document_id}

    return app
