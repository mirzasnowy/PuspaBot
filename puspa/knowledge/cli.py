"""``puspa knowledge`` command group (AST-02).

Upload a document, list what is stored, ask a question about
the stored documents, or delete one. All four commands run
against the same service the PWA screen uses
(``puspa.knowledge.store``), so the CLI and the screen never
disagree about what is in the knowledge base.
"""

from __future__ import annotations

import argparse
import sys

__all__ = ["add_knowledge_parser", "run_knowledge"]


def add_knowledge_parser(sub) -> None:
    """Add the ``knowledge`` subparsers to the top-level parser."""
    knowledge = sub.add_parser(
        "knowledge",
        help="Document knowledge base: upload files and ask about them.",
        description=(
            "Upload plain text or Markdown files and ask questions "
            "about them. Answers come from the uploaded documents "
            "only, with a citation per passage (source file + chunk "
            "number). PDF files are not supported yet."
        ),
    )
    knowledge_sub = knowledge.add_subparsers(dest="knowledge_command", metavar="KNOWLEDGE_COMMAND")
    upload = knowledge_sub.add_parser(
        "upload",
        help="Upload one .txt or .md file.",
        description="Upload one plain text or Markdown file to the knowledge base.",
    )
    upload.add_argument("path", help="File to upload (.txt or .md).")
    upload.add_argument("--user", required=True, help="User id the document belongs to.")
    upload.add_argument("--title", default=None, help="Display title (defaults to the filename).")
    listing = knowledge_sub.add_parser(
        "list",
        help="List the uploaded documents.",
        description="List the user's uploaded documents, newest first.",
    )
    listing.add_argument("--user", required=True, help="User id whose documents to list.")
    ask_parser = knowledge_sub.add_parser(
        "ask",
        help="Ask a question about the uploaded documents.",
        description=(
            "Answer a question from the uploaded documents only. "
            "The answer quotes the closest passages and cites each "
            "one with its source file and chunk number."
        ),
    )
    ask_parser.add_argument("question", help="Question to answer from the documents.")
    ask_parser.add_argument("--user", required=True, help="User id whose documents to ask.")
    ask_parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="How many passages to cite at most (default: 5).",
    )
    delete = knowledge_sub.add_parser(
        "delete",
        help="Delete one uploaded document.",
        description="Delete one uploaded document; its chunks go with it.",
    )
    delete.add_argument("document_id", help="Document id to delete (see `puspa knowledge list`).")
    delete.add_argument("--user", required=True, help="User id the document belongs to.")


def _run_upload(args: argparse.Namespace) -> int:
    from puspa.knowledge.store import upload_file

    try:
        document = upload_file(args.user, args.path, title=args.title)
    except Exception as exc:
        print(f"puspa knowledge upload: failed: {exc}", file=sys.stderr)
        return 1
    print(
        f"Uploaded {document['filename']} "
        f"({document['chunk_count']} chunks, {document['byte_size']} bytes)"
    )
    print(f"Document id: {document['id']}")
    return 0


def _run_list(args: argparse.Namespace) -> int:
    from puspa.knowledge.store import list_documents

    try:
        documents = list_documents(args.user)
    except Exception as exc:
        print(f"puspa knowledge list: failed: {exc}", file=sys.stderr)
        return 1
    if not documents:
        print("No documents uploaded yet. Upload a .txt or .md file first.")
        return 0
    for document in documents:
        print(
            f"{document['id']}  {document['filename']}  "
            f"({document['chunk_count']} chunks, {document['byte_size']} bytes)"
        )
    return 0


def _run_ask(args: argparse.Namespace) -> int:
    from puspa.knowledge.store import ask

    try:
        result = ask(args.user, args.question, limit=args.limit)
    except Exception as exc:
        print(f"puspa knowledge ask: failed: {exc}", file=sys.stderr)
        return 1
    print(result["answer"])
    print()
    for citation in result["citations"]:
        print(
            f"[{citation['filename']} chunk {citation['chunk_index']}] "
            f"similarity {citation['similarity']:.3f}"
        )
    return 0


def _run_delete(args: argparse.Namespace) -> int:
    from puspa.knowledge.store import delete_document

    try:
        deleted = delete_document(args.user, args.document_id)
    except Exception as exc:
        print(f"puspa knowledge delete: failed: {exc}", file=sys.stderr)
        return 1
    if not deleted:
        print("No such document for this user; nothing deleted.")
        return 1
    print(f"Deleted document {args.document_id}.")
    return 0


def run_knowledge(args: argparse.Namespace) -> int:
    """Dispatch ``puspa knowledge <command>`` (returns an exit code)."""
    if args.knowledge_command == "upload":
        return _run_upload(args)
    if args.knowledge_command == "list":
        return _run_list(args)
    if args.knowledge_command == "ask":
        return _run_ask(args)
    if args.knowledge_command == "delete":
        return _run_delete(args)
    raise ValueError(f"unknown knowledge command {args.knowledge_command!r}")
