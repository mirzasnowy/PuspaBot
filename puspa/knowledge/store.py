"""Document knowledge base (AST-02).

Let the user upload plain text and Markdown files and ask
questions about them using local vector search.

Design (AstrBot's knowledge base is the pattern source only:
a local store with a local vector index, ideas and patterns,
never copied code, never a dependency):

- Upload: ``.txt`` and ``.md`` / ``.markdown`` files. PDF is
  refused with a plain error: no PDF parser exists in this repo
  and this item adds no heavy new dependency for one.
- Chunking: the text is split into sentences, then packed into
  chunks of at most ``MAX_CHUNK_CHARS`` characters. Each chunk
  after the first re-opens with the previous chunk's last
  sentence, so an answer never loses the sentence a passage
  refers back to.
- Embedding: the repo's existing local embedder
  (``puspa.memory.embeddings``; the default is the free local
  ``HashEmbedding``, the same one the episodic index uses).
  Every row stores ``embedder_id`` + ``dimension`` (E-14), and
  search only ever compares rows from the asking embedder, so
  vectors from different embedders never mix. An upload is
  refused when the user already has chunks from another
  embedder: delete the old documents first, or re-embed them.
- Storage: Postgres + pgvector, migration 0037
  (``knowledge_documents`` + ``knowledge_chunks``), RLS
  per user, same policy shape as the episodic table.
- Asking: vector search over the user's chunks, then an
  extractive answer built from the closest chunks. Every
  answer carries citations (source file + chunk number +
  similarity), so no claim is shown without its source.

No new ledger event type: uploads and deletes are ordinary
user data, not agent actions.
"""

from __future__ import annotations

import re
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from puspa.accounts.ports import resolve_dsn
from puspa.memory.embeddings import EMBEDDING_DIM, HashEmbedding
from puspa.migrations import apply_migrations
from puspa.retrieval.embedding_health import MixedEmbedderError, dimension_of, embedder_id_of

#: Longest a stored chunk may be (characters).
MAX_CHUNK_CHARS = 800

#: Sentences carried over from the previous chunk as overlap.
OVERLAP_SENTENCES = 1

#: Largest upload accepted (bytes). A knowledge base is for
#: documents, not disk images; 5 MB is far above a long manual.
MAX_UPLOAD_BYTES = 5 * 1024 * 1024

#: File suffix -> stored content type.
_SUFFIX_CONTENT_TYPES = {
    ".txt": "text",
    ".md": "markdown",
    ".markdown": "markdown",
}

#: Suffixes refused with a specific error (not a generic one).
_PDF_SUFFIXES = {".pdf"}

_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+|\n+")

__all__ = [
    "MAX_CHUNK_CHARS",
    "MAX_UPLOAD_BYTES",
    "ask",
    "chunk_text",
    "create_schema",
    "delete_document",
    "get_document",
    "list_documents",
    "read_document_file",
    "upload_document",
    "upload_file",
]


def create_schema(dsn=None) -> None:
    """Apply the knowledge-base tables (migration 0037)."""
    apply_migrations(resolve_dsn(dsn), target="0037")


def _last_sentences(text: str, count: int) -> str:
    """The last ``count`` sentences of ``text`` (overlap tail)."""
    parts = [part.strip() for part in _SENTENCE_RE.split(text) if part.strip()]
    return " ".join(parts[-count:]) if parts else ""


def chunk_text(text: str, max_chars: int = MAX_CHUNK_CHARS) -> list[str]:
    """Split ``text`` into chunks of at most ``max_chars`` characters.

    Sentences are the unit: a chunk never cuts mid-sentence
    unless one sentence alone is longer than ``max_chars``,
    in which case it is hard-split. Each chunk after the first
    re-opens with the previous chunk's last sentence.
    """
    if not isinstance(text, str) or text.strip() == "":
        raise ValueError("text must be a non-empty string")
    if max_chars < 1:
        raise ValueError("max_chars must be at least 1")
    units: list[str] = []
    for raw in _SENTENCE_RE.split(text):
        unit = raw.strip()
        if not unit:
            continue
        while len(unit) > max_chars:
            units.append(unit[:max_chars])
            unit = unit[max_chars:]
        if unit:
            units.append(unit)
    chunks: list[str] = []
    current = ""
    for unit in units:
        joined = f"{current} {unit}" if current else unit
        if len(joined) > max_chars:
            if current:
                chunks.append(current)
            tail = _last_sentences(current, OVERLAP_SENTENCES) if current else ""
            current = f"{tail} {unit}" if tail and len(tail) + 1 + len(unit) <= max_chars else unit
        else:
            current = joined
    if current:
        chunks.append(current)
    return chunks


def read_document_file(path: str | Path) -> tuple[str, str, str]:
    """Read an uploadable file: return (text, content type, filename).

    Accepted: ``.txt`` (text) and ``.md`` / ``.markdown``
    (markdown). ``.pdf`` is refused with a plain reason (no PDF
    parser in this repo); anything else is refused as unknown.
    """
    file_path = Path(path)
    suffix = file_path.suffix.lower()
    if suffix in _PDF_SUFFIXES:
        raise ValueError(
            "PDF files are not supported yet: this repo has no PDF parser "
            "and the knowledge base does not add one. Upload the text "
            "or Markdown version instead."
        )
    content_type = _SUFFIX_CONTENT_TYPES.get(suffix)
    if content_type is None:
        raise ValueError(f"unsupported file type {suffix!r}: upload a .txt or .md file")
    try:
        raw = file_path.read_bytes()
    except OSError as exc:
        raise ValueError(f"cannot read {file_path}: {exc}") from exc
    if len(raw) > MAX_UPLOAD_BYTES:
        raise ValueError(f"file is too large ({len(raw)} bytes, limit {MAX_UPLOAD_BYTES})")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{file_path} is not UTF-8 text: {exc}") from exc
    if text.strip() == "":
        raise ValueError(f"{file_path} is empty")
    return text, content_type, file_path.name


def _connect(dsn=None):
    return psycopg.connect(resolve_dsn(dsn), row_factory=dict_row)


def _vector_literal(vector) -> str:
    return "[" + ",".join(f"{v:.6f}" for v in vector) + "]"


def _check_user_embedder(conn, user_id: str, embedder_id: str, dimension: int) -> None:
    """Refuse an upload whose embedder differs from the user's own.

    Vectors from different embedders never share one user's
    index: the sanctioned fix is to delete the old documents
    (or re-upload them) before switching embedders.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT embedder_id, dimension FROM knowledge_chunks WHERE user_id = %s",
            (user_id,),
        )
        for row in cur.fetchall():
            if (row["embedder_id"], int(row["dimension"])) != (embedder_id, dimension):
                raise MixedEmbedderError(
                    f"refusing upload from embedder {embedder_id} "
                    f"(dim {dimension}): this user's documents use "
                    f"{row['embedder_id']} (dim {int(row['dimension'])}); "
                    "delete the old documents before switching embedders"
                )


def upload_document(
    user_id: str,
    filename: str,
    text: str,
    content_type: str,
    *,
    title: str | None = None,
    embedder=None,
    dsn=None,
) -> dict:
    """Store one document: chunk it, embed every chunk, save all rows.

    Returns the stored document (id, filename, title, content
    type, byte size, chunk count, embedder, created time).
    """
    if not isinstance(user_id, str) or user_id == "":
        raise ValueError("user_id must be a non-empty string")
    if not isinstance(filename, str) or filename == "":
        raise ValueError("filename must be a non-empty string")
    if content_type not in ("text", "markdown"):
        raise ValueError("content_type must be 'text' or 'markdown'")
    chunks = chunk_text(text)
    embedder = embedder or HashEmbedding()
    embedder_id = embedder_id_of(embedder)
    dimension = dimension_of(embedder)
    if dimension != EMBEDDING_DIM:
        raise ValueError(
            f"embedder dimension is {dimension}, the knowledge_chunks "
            f"column holds vector({EMBEDDING_DIM})"
        )
    byte_size = len(text.encode("utf-8"))
    with _connect(dsn) as conn:
        _check_user_embedder(conn, user_id, embedder_id, dimension)
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO knowledge_documents "
                "(user_id, filename, title, content_type, byte_size, "
                " chunk_count, embedder_id, dimension) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
                "RETURNING id, user_id, filename, title, content_type, "
                "byte_size, chunk_count, embedder_id, dimension, created_at",
                (
                    user_id,
                    filename,
                    title or filename,
                    content_type,
                    byte_size,
                    len(chunks),
                    embedder_id,
                    dimension,
                ),
            )
            document = cur.fetchone()
            for index, chunk in enumerate(chunks):
                cur.execute(
                    "INSERT INTO knowledge_chunks "
                    "(document_id, user_id, chunk_index, content, "
                    " embedding, embedder_id, dimension) "
                    "VALUES (%s, %s, %s, %s, %s::vector, %s, %s)",
                    (
                        document["id"],
                        user_id,
                        index,
                        chunk,
                        _vector_literal(embedder.embed(chunk)),
                        embedder_id,
                        dimension,
                    ),
                )
        conn.commit()
    return _public_document(document)


def upload_file(
    user_id: str, path: str | Path, *, title: str | None = None, embedder=None, dsn=None
) -> dict:
    """Upload one file from disk (read + type check + store)."""
    text, content_type, filename = read_document_file(path)
    return upload_document(
        user_id,
        filename,
        text,
        content_type,
        title=title,
        embedder=embedder,
        dsn=dsn,
    )


def _public_document(row) -> dict:
    return {
        "id": str(row["id"]),
        "user_id": str(row["user_id"]),
        "filename": row["filename"],
        "title": row["title"],
        "content_type": row["content_type"],
        "byte_size": int(row["byte_size"]),
        "chunk_count": int(row["chunk_count"]),
        "embedder_id": row["embedder_id"],
        "dimension": int(row["dimension"]),
        "created_at": row["created_at"],
    }


def list_documents(user_id: str, *, dsn=None) -> list[dict]:
    """The user's uploaded documents, newest first."""
    if not isinstance(user_id, str) or user_id == "":
        raise ValueError("user_id must be a non-empty string")
    with _connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, user_id, filename, title, content_type, "
            "byte_size, chunk_count, embedder_id, dimension, created_at "
            "FROM knowledge_documents WHERE user_id = %s "
            "ORDER BY created_at DESC",
            (user_id,),
        )
        return [_public_document(row) for row in cur.fetchall()]


def get_document(user_id: str, document_id: str, *, dsn=None) -> dict | None:
    """One document of this user's, or None when it is not theirs."""
    if not isinstance(user_id, str) or user_id == "":
        raise ValueError("user_id must be a non-empty string")
    with _connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, user_id, filename, title, content_type, "
            "byte_size, chunk_count, embedder_id, dimension, created_at "
            "FROM knowledge_documents WHERE id = %s AND user_id = %s",
            (document_id, user_id),
        )
        row = cur.fetchone()
        return _public_document(row) if row is not None else None


def delete_document(user_id: str, document_id: str, *, dsn=None) -> bool:
    """Delete one of the user's documents (its chunks go with it).

    Returns True when a document was deleted, False when the id
    is unknown or belongs to another user.
    """
    if not isinstance(user_id, str) or user_id == "":
        raise ValueError("user_id must be a non-empty string")
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM knowledge_documents WHERE id = %s AND user_id = %s",
                (document_id, user_id),
            )
            deleted = cur.rowcount > 0
        conn.commit()
    return deleted


def ask(
    user_id: str,
    question: str,
    *,
    limit: int = 5,
    embedder=None,
    dsn=None,
) -> dict:
    """Answer ``question`` from the user's uploaded documents.

    Vector search finds the closest chunks, then the answer is
    built from those chunks word for word (extractive: the agent
    never invents a sentence the documents do not contain).
    Every citation names its source file and chunk number, so
    the answer can always be checked against the upload.
    """
    if not isinstance(user_id, str) or user_id == "":
        raise ValueError("user_id must be a non-empty string")
    if not isinstance(question, str) or question.strip() == "":
        raise ValueError("question must be a non-empty string")
    if limit < 1:
        raise ValueError("limit must be at least 1")
    embedder = embedder or HashEmbedding()
    embedder_id = embedder_id_of(embedder)
    dimension = dimension_of(embedder)
    vector = _vector_literal(embedder.embed(question))
    with _connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT c.id, c.document_id, c.chunk_index, c.content, "
            "d.filename, d.title, "
            "c.embedding <=> %s::vector AS distance "
            "FROM knowledge_chunks c "
            "JOIN knowledge_documents d ON d.id = c.document_id "
            "WHERE c.user_id = %s AND c.embedder_id = %s AND c.dimension = %s "
            "ORDER BY distance ASC LIMIT %s",
            (vector, user_id, embedder_id, dimension, limit),
        )
        rows = cur.fetchall()
    citations = [
        {
            "chunk_id": str(row["id"]),
            "document_id": str(row["document_id"]),
            "filename": row["filename"],
            "title": row["title"],
            "chunk_index": int(row["chunk_index"]),
            "content": row["content"],
            "similarity": 1.0 - float(row["distance"]),
        }
        for row in rows
    ]
    if not citations:
        return {
            "question": question,
            "answer": (
                "Your knowledge base has no passage to answer from yet. "
                "Upload a .txt or .md file first, then ask again."
            ),
            "citations": [],
        }
    lines = []
    for citation in citations:
        lines.append(
            f"From {citation['filename']} (chunk {citation['chunk_index']}): {citation['content']}"
        )
    return {
        "question": question,
        "answer": "\n\n".join(lines),
        "citations": citations,
    }
