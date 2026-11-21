"""Conversation export and import (V2-11).

Covers: an export then re-import round trip keeps messages,
order, roles and timestamps; exporting one conversation only;
dry run writes nothing; a duplicate import is a no-op; a
tampered archive is refused with nothing written; a conflicting
conversation refuses the whole import; lineage survives the
round trip; a dropped parent link is reported and still
re-imports as a duplicate; and the CLI round trip.

The session gateway's single-writer lock lives in Postgres:
``puspa.sessions`` reads PUSPA_TEST_DSN when no DSN is passed,
so every test that opens a gateway needs the database. The
integration marker is the declaration of that dependency.
"""

from __future__ import annotations

import json
import zipfile

import pytest

from puspa.cli import main
from puspa.sessions import open_persistence
from puspa.sessions.export import (
    ConversationArchiveError,
    export_conversations,
    import_conversations,
    plan_import_conversations,
)

pytestmark = pytest.mark.integration


def _seed(gateway):
    """Two conversations with distinct roles and timestamps."""
    first = gateway.create_session("price check", model="test-model", metadata={"source": "test"})
    gateway.append_message(first["id"], "system", "You are Puspa.")
    gateway.append_message(first["id"], "user", "Compare the price of a kettle.")
    gateway.append_message(first["id"], "assistant", "The kettle costs Rp150.000 at Toko A.")
    gateway.append_message(first["id"], "tool", "search results: kettle Rp150.000")
    gateway.append_message(first["id"], "assistant", "Cheapest is Toko A at Rp150.000.")
    second = gateway.create_session("morning note")
    gateway.append_message(second["id"], "user", "Good morning.")
    gateway.append_message(second["id"], "assistant", "Good morning! Here is your day.")
    return first["id"], second["id"]


def _public(session):
    """The portable shape of a session record (no local row ids)."""
    return {
        "id": session["id"],
        "title": session["title"],
        "created_at": session["created_at"],
        "updated_at": session["updated_at"],
        "parent_id": session["parent_id"],
        "model": session["model"],
        "metadata": session["metadata"],
        "messages": [
            {key: message[key] for key in ("seq", "role", "content", "created_at")}
            for message in session["messages"]
        ],
    }


def _rewrite_zip(archive, out, *, mutate):
    """Copy a zip to ``out``, applying ``mutate(files)`` to its entries."""
    with zipfile.ZipFile(archive) as source:
        files = {info.filename: source.read(info.filename) for info in source.infolist()}
    mutate(files)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
        for name in sorted(files):
            target.writestr(name, files[name])
    return out


def test_export_then_reimport_is_intact(tmp_path):
    """Export all, wipe, import: messages, order, roles, timestamps equal."""
    source_home = tmp_path / "source"
    target_home = tmp_path / "target"
    archive = tmp_path / "all.zip"
    with open_persistence(home=source_home) as gateway:
        first_id, second_id = _seed(gateway)
        before = {
            session_id: _public(gateway.get_session(session_id))
            for session_id in (first_id, second_id)
        }
        result = export_conversations(gateway, out=archive)
    assert result["status"] == "exported"
    assert result["counts"] == {"conversations": 2, "messages": 7, "lineage": 0}
    assert set(result["conversations"]) == {first_id, second_id}
    with open_persistence(home=target_home) as gateway:
        done = import_conversations(archive, writer=gateway, approve=True)
    assert done["status"] == "imported"
    assert set(done["created"]) == {first_id, second_id}
    with open_persistence(home=target_home) as gateway:
        for session_id, original in before.items():
            assert _public(gateway.get_session(session_id)) == original


def test_export_single_conversation(tmp_path):
    """Exporting one id leaves the other conversation out of the archive."""
    home = tmp_path / "home"
    archive = tmp_path / "one.zip"
    with open_persistence(home=home) as gateway:
        first_id, second_id = _seed(gateway)
        result = export_conversations(gateway, conversation_ids=[first_id], out=archive)
    assert result["counts"] == {"conversations": 1, "messages": 5, "lineage": 0}
    assert result["conversations"] == [first_id]
    target_home = tmp_path / "target"
    with open_persistence(home=target_home) as gateway:
        done = import_conversations(archive, writer=gateway, approve=True)
    assert done["created"] == [first_id]
    with open_persistence(home=target_home) as gateway:
        assert [session["id"] for session in gateway.list_sessions()] == [first_id]
        assert second_id not in {session["id"] for session in gateway.list_sessions()}


def test_dry_run_writes_nothing(tmp_path):
    """Import without --approve only plans and leaves the store empty."""
    home = tmp_path / "home"
    target_home = tmp_path / "target"
    archive = tmp_path / "all.zip"
    with open_persistence(home=home) as gateway:
        _seed(gateway)
        export_conversations(gateway, out=archive)
    with open_persistence(home=target_home) as gateway:
        plan = plan_import_conversations(archive, reader=gateway)
        assert plan["counts"]["create"] == 2
        assert plan["would_change"] is True
        planned = import_conversations(archive, writer=gateway)
        assert planned["status"] == "awaiting_approval"
        assert gateway.list_sessions() == []
        dried = import_conversations(archive, writer=gateway, dry_run=True)
        assert dried["status"] == "dry_run"
        assert gateway.list_sessions() == []


def test_duplicate_import_is_noop(tmp_path):
    """Importing the same archive twice creates nothing the second time."""
    home = tmp_path / "home"
    target_home = tmp_path / "target"
    archive = tmp_path / "all.zip"
    with open_persistence(home=home) as gateway:
        _seed(gateway)
        export_conversations(gateway, out=archive)
    with open_persistence(home=target_home) as gateway:
        first = import_conversations(archive, writer=gateway, approve=True)
        assert first["status"] == "imported"
        assert len(first["created"]) == 2
        second = import_conversations(archive, writer=gateway, approve=True)
        assert second["status"] == "imported"
        assert second["created"] == []
        assert set(second["duplicates"]) == set(first["created"])
        assert len(gateway.list_sessions()) == 2
        total = sum(len(gateway.get_session(s["id"])["messages"]) for s in gateway.list_sessions())
        assert total == 7


def test_tampered_archive_rejected_with_nothing_written(tmp_path):
    """A changed message breaks the manifest hash: refused, store stays empty."""
    home = tmp_path / "home"
    target_home = tmp_path / "target"
    archive = tmp_path / "all.zip"
    tampered = tmp_path / "tampered.zip"
    with open_persistence(home=home) as gateway:
        _seed(gateway)
        export_conversations(gateway, out=archive)

    def _tamper(files):
        rows = files["conversations.jsonl"].decode("utf-8").splitlines()
        row = json.loads(rows[0])
        row["messages"][0]["content"] = "You are someone else."
        rows[0] = json.dumps(row)
        files["conversations.jsonl"] = ("\n".join(rows) + "\n").encode("utf-8")

    _rewrite_zip(archive, tampered, mutate=_tamper)
    with open_persistence(home=target_home) as gateway:
        with pytest.raises(ConversationArchiveError, match="hash mismatch"):
            import_conversations(tampered, writer=gateway, approve=True)
        assert gateway.list_sessions() == []


def test_manifest_count_mismatch_rejected(tmp_path):
    """A manifest whose counts disagree with the files is refused."""
    home = tmp_path / "home"
    archive = tmp_path / "all.zip"
    wrong = tmp_path / "wrong.zip"
    with open_persistence(home=home) as gateway:
        _seed(gateway)
        export_conversations(gateway, out=archive)

    def _miscount(files):
        manifest = json.loads(files["manifest.json"])
        manifest["counts"]["conversations"] = 99
        files["manifest.json"] = json.dumps(manifest).encode("utf-8")

    _rewrite_zip(archive, wrong, mutate=_miscount)
    with (
        open_persistence(home=home) as gateway,
        pytest.raises(ConversationArchiveError, match="count mismatch"),
    ):
        import_conversations(wrong, writer=gateway, approve=True)


def test_wrong_schema_version_rejected(tmp_path):
    """An archive from a newer schema version is refused."""
    home = tmp_path / "home"
    archive = tmp_path / "all.zip"
    newer = tmp_path / "newer.zip"
    with open_persistence(home=home) as gateway:
        _seed(gateway)
        export_conversations(gateway, out=archive)

    def _bump(files):
        manifest = json.loads(files["manifest.json"])
        manifest["schema_version"] = 99
        files["manifest.json"] = json.dumps(manifest).encode("utf-8")

    _rewrite_zip(archive, newer, mutate=_bump)
    with (
        open_persistence(home=home) as gateway,
        pytest.raises(ConversationArchiveError, match="schema version"),
    ):
        import_conversations(newer, writer=gateway, approve=True)


def test_conflict_refuses_whole_import(tmp_path):
    """A stored conversation with different content refuses everything."""
    home = tmp_path / "home"
    target_home = tmp_path / "target"
    archive = tmp_path / "all.zip"
    with open_persistence(home=home) as gateway:
        first_id, _ = _seed(gateway)
        export_conversations(gateway, out=archive)
    with open_persistence(home=target_home) as gateway:
        done = import_conversations(archive, writer=gateway, approve=True)
        assert done["status"] == "imported"
        gateway.append_message(first_id, "user", "One more question.")
        refused = import_conversations(archive, writer=gateway, approve=True)
        assert refused["status"] == "refused"
        assert refused["counts"]["conflict"] == 1
        stored = gateway.get_session(first_id)
        assert [m["content"] for m in stored["messages"]][-1] == "One more question."
        assert len(stored["messages"]) == 6
        assert len(gateway.list_sessions()) == 2


def test_export_missing_conversation_fails(tmp_path):
    """Exporting an unknown id fails with a clear error."""
    home = tmp_path / "home"
    with open_persistence(home=home) as gateway:
        _seed(gateway)
        with pytest.raises(ConversationArchiveError, match="no conversation"):
            export_conversations(gateway, conversation_ids=["no-such-id"], out=tmp_path / "x.zip")


def test_lineage_round_trip(tmp_path):
    """A compression child keeps its parent link and lineage edge."""
    home = tmp_path / "home"
    target_home = tmp_path / "target"
    archive = tmp_path / "all.zip"
    with open_persistence(home=home) as gateway:
        parent = gateway.create_session("parent chat")
        gateway.append_message(parent["id"], "user", "hello")
        child = gateway.create_child_session(
            parent["id"], summary="Compressed summary", reason="test"
        )
        export_conversations(gateway, out=archive)
    with open_persistence(home=target_home) as gateway:
        done = import_conversations(archive, writer=gateway, approve=True)
    assert done["status"] == "imported"
    assert done["counts"]["lineage_restore"] == 1
    assert done["counts"]["lineage_drop"] == 0
    with open_persistence(home=target_home) as gateway:
        stored_child = gateway.get_session(child["id"])
        assert stored_child["parent_id"] == parent["id"]
        assert stored_child["messages"][0]["content"] == "Compressed summary"
        edges = gateway.children(parent["id"])
        assert len(edges) == 1
        assert edges[0]["child_session_id"] == child["id"]
        assert edges[0]["kind"] == "compression"


def test_dropped_parent_reported_and_still_duplicate(tmp_path):
    """Exporting a child alone drops its parent link, and re-import still matches."""
    home = tmp_path / "home"
    target_home = tmp_path / "target"
    archive = tmp_path / "child.zip"
    with open_persistence(home=home) as gateway:
        parent = gateway.create_session("parent chat")
        gateway.append_message(parent["id"], "user", "hello")
        child = gateway.create_child_session(
            parent["id"], summary="Compressed summary", reason="test"
        )
        export_conversations(gateway, conversation_ids=[child["id"]], out=archive)
    with open_persistence(home=target_home) as gateway:
        done = import_conversations(archive, writer=gateway, approve=True)
        assert done["status"] == "imported"
        assert done["created"] == [child["id"]]
        assert done["dropped_parents"] == [{"id": child["id"], "parent_id": parent["id"]}]
        assert gateway.get_session(child["id"])["parent_id"] is None
        again = import_conversations(archive, writer=gateway, approve=True)
        assert again["status"] == "imported"
        assert again["created"] == []
        assert again["duplicates"] == [child["id"]]


def test_empty_profile_exports_and_imports(tmp_path):
    """An empty profile exports a valid empty archive; importing it changes nothing."""
    home = tmp_path / "home"
    target_home = tmp_path / "target"
    archive = tmp_path / "empty.zip"
    with open_persistence(home=home) as gateway:
        result = export_conversations(gateway, out=archive)
    assert result["counts"] == {"conversations": 0, "messages": 0, "lineage": 0}
    with open_persistence(home=target_home) as gateway:
        done = import_conversations(archive, writer=gateway, approve=True)
        assert done["status"] == "imported"
        assert done["created"] == []
        assert gateway.list_sessions() == []


def test_cli_export_import_round_trip(tmp_path):
    """The CLI exports, dry-runs, then imports with --approve."""
    source_home = tmp_path / "source"
    target_home = tmp_path / "target"
    archive = tmp_path / "cli.zip"
    with open_persistence(home=source_home) as gateway:
        first_id, second_id = _seed(gateway)
    assert (
        main(
            [
                "conversation",
                "export",
                "--home",
                str(source_home),
                "--out",
                str(archive),
            ]
        )
        == 0
    )
    assert archive.exists()
    assert main(["conversation", "import", "--file", str(archive), "--home", str(target_home)]) == 0
    with open_persistence(home=target_home) as gateway:
        assert gateway.list_sessions() == []
    assert (
        main(
            [
                "conversation",
                "import",
                "--file",
                str(archive),
                "--home",
                str(target_home),
                "--approve",
            ]
        )
        == 0
    )
    with open_persistence(home=target_home) as gateway:
        assert {s["id"] for s in gateway.list_sessions()} == {first_id, second_id}
        assert len(gateway.get_session(first_id)["messages"]) == 5
