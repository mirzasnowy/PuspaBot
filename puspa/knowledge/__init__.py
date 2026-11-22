"""Document knowledge base: upload files, ask questions about them."""

from .store import (
    MAX_CHUNK_CHARS,
    MAX_UPLOAD_BYTES,
    ask,
    chunk_text,
    create_schema,
    delete_document,
    get_document,
    list_documents,
    read_document_file,
    upload_document,
    upload_file,
)

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
