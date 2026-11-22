"""Tests for the document knowledge base (AST-02).

Upload (.txt / .md) -> chunk + embed -> list / ask / delete,
through the store, the ``puspa knowledge`` CLI, and the PWA
API + screen. PDF is refused everywhere: no PDF parser exists
in this repo and this item adds none.
"""

import psycopg
import pytest
from fastapi.testclient import TestClient

from puspa.cli import main as cli_main
from puspa.knowledge.store import (
    chunk_text,
    create_schema,
    read_document_file,
)
from puspa.memory.embeddings import HashEmbedding
from puspa.pwa.app import create_app

SENTENCE = "The quick brown fox jumps over the lazy dog. "
CATS_TEXT = (
    "Cats sleep for most of the day. A cat naps in the sun "
    "for hours. Cats groom themselves after every meal."
)
REFUND_TEXT = (
    "Our refund policy: returns are accepted within 30 days "
    "of purchase. A refund is paid back to the original payment "
    "method. Refunds take five working days to arrive."
)


@pytest.fixture(autouse=True)
def _knowledge_tables(test_dsn):
    create_schema(test_dsn)
    yield
    with psycopg.connect(test_dsn) as conn:
        conn.execute("TRUNCATE knowledge_chunks, knowledge_documents CASCADE")
        conn.commit()


@pytest.fixture
def client(make_user):
    u = make_user()
    app = create_app(test_user_id=u["user_id"])
    with TestClient(app, raise_server_exceptions=False) as c:
        c.user_id = u["user_id"]
        yield c


@pytest.fixture
def other_client(make_user):
    u = make_user()
    app = create_app(test_user_id=u["user_id"])
    with TestClient(app, raise_server_exceptions=False) as c:
        c.user_id = u["user_id"]
        yield c


def _write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


class _FakeEmbedder:
    """A second embedder (different id) for the mixing refusal test."""

    embedder_id = "fake"
    dim = 384

    def embed(self, text: str) -> list[float]:
        return [1.0] + [0.0] * 383


class _NarrowEmbedder:
    """An embedder whose dimension does not fit the column."""

    embedder_id = "narrow"
    dim = 128

    def embed(self, text: str) -> list[float]:
        return [0.0] * 128


# -- chunking (pure, no database) --------------------------------------


def test_short_text_is_one_chunk():
    assert chunk_text("Hello world. Short text.") == ["Hello world. Short text."]


def test_long_text_splits_within_limit_and_keeps_sentences_whole():
    chunks = chunk_text(SENTENCE * 40, max_chars=200)
    assert len(chunks) > 1
    assert all(len(chunk) <= 200 for chunk in chunks)
    assert all(chunk.endswith(".") for chunk in chunks)


def test_next_chunk_reopens_with_previous_last_sentence():
    text = " ".join(f"Sentence number {n} is here." for n in range(10))
    chunks = chunk_text(text, max_chars=120)
    assert len(chunks) > 1
    first_last = chunks[0].rsplit(". ", 1)[-1]
    assert chunks[1].startswith(first_last)


def test_one_overlong_sentence_is_hard_split():
    chunks = chunk_text("x" * 2000, max_chars=800)
    assert len(chunks) == 3
    assert all(len(chunk) <= 800 for chunk in chunks)


def test_chunk_text_refuses_empty_or_non_string():
    for bad in ("", "   ", 42, None):
        with pytest.raises(ValueError):
            chunk_text(bad)


# -- file reading (pure, no database) -----------------------------------


def test_read_txt_and_markdown(tmp_path):
    text, kind, name = read_document_file(_write(tmp_path, "notes.txt", "hello"))
    assert (text, kind, name) == ("hello", "text", "notes.txt")
    text, kind, name = read_document_file(_write(tmp_path, "guide.md", "# hi"))
    assert (text, kind, name) == ("# hi", "markdown", "guide.md")


def test_read_pdf_refused_with_reason(tmp_path):
    path = _write(tmp_path, "doc.pdf", "%PDF-1.4 fake")
    with pytest.raises(ValueError, match="PDF"):
        read_document_file(path)


def test_read_unknown_type_missing_and_empty_refused(tmp_path):
    with pytest.raises(ValueError, match="unsupported"):
        read_document_file(_write(tmp_path, "data.csv", "a,b"))
    with pytest.raises(ValueError, match="cannot read"):
        read_document_file(tmp_path / "gone.txt")
    with pytest.raises(ValueError, match="empty"):
        read_document_file(_write(tmp_path, "blank.txt", "  \n "))


def test_read_non_utf8_and_oversize_refused(tmp_path, monkeypatch):
    binary = tmp_path / "bin.txt"
    binary.write_bytes(b"\xff\xfe not text")
    with pytest.raises(ValueError, match="UTF-8"):
        read_document_file(binary)
    monkeypatch.setattr("puspa.knowledge.store.MAX_UPLOAD_BYTES", 10)
    with pytest.raises(ValueError, match="too large"):
        read_document_file(_write(tmp_path, "big.txt", "this is longer than ten bytes"))


# -- store (database) ----------------------------------------------------


def test_upload_list_ask_delete_round_trip(make_user, test_dsn, tmp_path):
    from puspa.knowledge.store import (
        ask,
        delete_document,
        list_documents,
        upload_file,
    )

    user = make_user()
    path = _write(tmp_path, "refunds.md", REFUND_TEXT)
    document = upload_file(user["user_id"], path, embedder=HashEmbedding(), dsn=test_dsn)
    assert document["filename"] == "refunds.md"
    assert document["content_type"] == "markdown"
    assert document["chunk_count"] >= 1
    assert document["embedder_id"] == "hash"
    assert document["dimension"] == 384

    listed = list_documents(user["user_id"], dsn=test_dsn)
    assert len(listed) == 1
    assert listed[0]["id"] == document["id"]

    result = ask(
        user["user_id"],
        "What is the refund policy?",
        embedder=HashEmbedding(),
        dsn=test_dsn,
    )
    assert len(result["citations"]) >= 1
    top = result["citations"][0]
    assert top["filename"] == "refunds.md"
    assert top["chunk_index"] == 0
    assert "refund policy" in top["content"]
    assert "refunds.md" in result["answer"]
    assert top["similarity"] > 0

    assert delete_document(user["user_id"], document["id"], dsn=test_dsn) is True
    assert list_documents(user["user_id"], dsn=test_dsn) == []
    after = ask(
        user["user_id"],
        "What is the refund policy?",
        embedder=HashEmbedding(),
        dsn=test_dsn,
    )
    assert after["citations"] == []


def test_ask_ranks_the_matching_document_first(make_user, test_dsn, tmp_path):
    from puspa.knowledge.store import ask, upload_file

    user = make_user()
    upload_file(
        user["user_id"],
        _write(tmp_path, "cats.txt", CATS_TEXT),
        embedder=HashEmbedding(),
        dsn=test_dsn,
    )
    upload_file(
        user["user_id"],
        _write(tmp_path, "refunds.txt", REFUND_TEXT),
        embedder=HashEmbedding(),
        dsn=test_dsn,
    )
    result = ask(
        user["user_id"],
        "What is the refund policy?",
        embedder=HashEmbedding(),
        dsn=test_dsn,
    )
    assert result["citations"][0]["filename"] == "refunds.txt"


def test_ask_on_empty_base_says_so(make_user, test_dsn):
    from puspa.knowledge.store import ask

    user = make_user()
    result = ask(user["user_id"], "Anything?", embedder=HashEmbedding(), dsn=test_dsn)
    assert result["citations"] == []
    assert "no passage" in result["answer"]


def test_documents_are_per_user(make_user, test_dsn, tmp_path):
    from puspa.knowledge.store import (
        ask,
        delete_document,
        get_document,
        list_documents,
        upload_file,
    )

    user = make_user()
    other = make_user()
    document = upload_file(
        user["user_id"],
        _write(tmp_path, "mine.txt", CATS_TEXT),
        embedder=HashEmbedding(),
        dsn=test_dsn,
    )
    assert list_documents(other["user_id"], dsn=test_dsn) == []
    assert get_document(other["user_id"], document["id"], dsn=test_dsn) is None
    assert ask(other["user_id"], "cats", embedder=HashEmbedding(), dsn=test_dsn)["citations"] == []
    assert delete_document(other["user_id"], document["id"], dsn=test_dsn) is False
    assert len(list_documents(user["user_id"], dsn=test_dsn)) == 1


def test_upload_refuses_a_second_embedder(make_user, test_dsn, tmp_path):
    from puspa.knowledge.store import upload_file
    from puspa.retrieval.embedding_health import MixedEmbedderError

    user = make_user()
    upload_file(
        user["user_id"],
        _write(tmp_path, "a.txt", CATS_TEXT),
        embedder=HashEmbedding(),
        dsn=test_dsn,
    )
    with pytest.raises(MixedEmbedderError):
        upload_file(
            user["user_id"],
            _write(tmp_path, "b.txt", REFUND_TEXT),
            embedder=_FakeEmbedder(),
            dsn=test_dsn,
        )


def test_upload_refuses_a_wrong_dimension(make_user, test_dsn, tmp_path):
    from puspa.knowledge.store import upload_file

    user = make_user()
    with pytest.raises(ValueError, match="dimension"):
        upload_file(
            user["user_id"],
            _write(tmp_path, "a.txt", CATS_TEXT),
            embedder=_NarrowEmbedder(),
            dsn=test_dsn,
        )


# -- CLI -------------------------------------------------------------------


def test_cli_upload_list_ask_delete(make_user, test_dsn, tmp_path, capsys):
    user = make_user()
    path = _write(tmp_path, "refunds.md", REFUND_TEXT)
    assert cli_main(["knowledge", "upload", str(path), "--user", user["user_id"]]) == 0
    out = capsys.readouterr().out
    assert "refunds.md" in out

    assert cli_main(["knowledge", "list", "--user", user["user_id"]]) == 0
    out = capsys.readouterr().out
    assert "refunds.md" in out
    document_id = out.split()[0]

    assert (
        cli_main(["knowledge", "ask", "What is the refund policy?", "--user", user["user_id"]]) == 0
    )
    out = capsys.readouterr().out
    assert "refund policy" in out
    assert "[refunds.md chunk 0]" in out

    assert cli_main(["knowledge", "delete", document_id, "--user", user["user_id"]]) == 0
    assert cli_main(["knowledge", "list", "--user", user["user_id"]]) == 0
    assert "No documents" in capsys.readouterr().out


def test_cli_upload_refuses_pdf(make_user, tmp_path, capsys):
    user = make_user()
    path = _write(tmp_path, "doc.pdf", "%PDF-1.4 fake")
    assert cli_main(["knowledge", "upload", str(path), "--user", user["user_id"]]) == 1
    assert "PDF" in capsys.readouterr().err


def test_cli_ask_on_empty_base(make_user, capsys):
    user = make_user()
    assert cli_main(["knowledge", "ask", "Anything?", "--user", user["user_id"]]) == 0
    assert "no passage" in capsys.readouterr().out


# -- PWA API + screen -------------------------------------------------------


def _upload(client, name="refunds.md", text=REFUND_TEXT, mime="text/markdown"):
    return client.post("/api/knowledge/upload", files={"file": (name, text.encode("utf-8"), mime)})


def test_pwa_upload_list_ask_delete(client):
    resp = _upload(client)
    assert resp.status_code == 200, resp.text
    document = resp.json()["document"]
    assert document["filename"] == "refunds.md"
    assert document["chunk_count"] >= 1

    listed = client.get("/api/knowledge/documents").json()["documents"]
    assert len(listed) == 1
    assert listed[0]["id"] == document["id"]

    result = client.get("/api/knowledge/ask", params={"q": "What is the refund policy?"}).json()
    assert result["citations"][0]["filename"] == "refunds.md"
    assert "refund policy" in result["answer"]

    deleted = client.delete(f"/api/knowledge/documents/{document['id']}")
    assert deleted.status_code == 200
    assert client.get("/api/knowledge/documents").json()["documents"] == []


def test_pwa_upload_refuses_pdf_and_empty_question(client):
    resp = client.post(
        "/api/knowledge/upload",
        files={"file": ("doc.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )
    assert resp.status_code == 400
    assert "PDF" in resp.json()["error"]
    resp = client.get("/api/knowledge/ask", params={"q": "   "})
    assert resp.status_code == 400


def test_pwa_delete_unknown_is_404_and_other_user_is_isolated(client, other_client):
    resp = _upload(client)
    document_id = resp.json()["document"]["id"]
    assert other_client.get("/api/knowledge/documents").json()["documents"] == []
    assert other_client.delete(f"/api/knowledge/documents/{document_id}").status_code == 404
    assert client.get("/api/knowledge/documents").json()["documents"] != []


def test_pwa_knowledge_routes_need_login():
    app = create_app(test_user_id=None)
    with TestClient(app, raise_server_exceptions=False) as c:
        assert c.get("/api/knowledge/documents").status_code == 401
        assert c.get("/api/knowledge/ask", params={"q": "x"}).status_code == 401
        assert (
            c.post(
                "/api/knowledge/upload",
                files={"file": ("a.txt", b"hello", "text/plain")},
            ).status_code
            == 401
        )
        assert (
            c.delete("/api/knowledge/documents/00000000-0000-0000-0000-000000000000").status_code
            == 401
        )


def test_knowledge_screen_renders(client):
    resp = client.get("/knowledge")
    assert resp.status_code == 200
    assert "Puspa" in resp.text


def test_knowledge_screen_renders_without_spa_dist(client, monkeypatch, tmp_path):
    """The Jinja fallback: with dist/ hidden the template must exist.

    CI runs without the built SPA, so this is the test that
    catches a missing apps/pwa/templates/knowledge.html.
    """
    monkeypatch.setattr("puspa.pwa.app.DIST_DIR", tmp_path / "no-dist")
    resp = client.get("/knowledge")
    assert resp.status_code == 200
    assert "Knowledge Base" in resp.text
