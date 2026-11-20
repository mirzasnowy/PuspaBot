"""Single-writer authority, atomic writes, contention (G26).

The writer token is only minted by the gateway, so a direct
writable ``SessionStore`` open must fail; a second live gateway for
the same profile must fail; readers must be physically read-only.
"""

from __future__ import annotations

import sqlite3
import threading

import pytest

from puspa.sessions import (
    SessionGateway,
    SessionNotFoundError,
    SingleWriterError,
    WriteContentionError,
    open_persistence,
    open_persistence_reader,
)
from puspa.sessions.store import SessionStore

# The session gateway's single-writer lock lives in Postgres:
# puspa.sessions reads PUSPA_TEST_DSN when no DSN is passed,
# so every test that opens a gateway needs the database.
# The integration marker is the declaration of that
# dependency (E-17); without it these tests classify as unit
# and fail when no database exists.
pytestmark = pytest.mark.integration


def test_direct_writable_store_is_refused(tmp_path):
    with pytest.raises(SingleWriterError):
        SessionStore(tmp_path / "state.db")


def test_second_gateway_is_refused_while_first_is_open(tmp_path):
    with open_persistence(home=tmp_path), pytest.raises(SingleWriterError):
        open_persistence(home=tmp_path)


def test_gateway_reopens_after_close(tmp_path):
    with open_persistence(home=tmp_path) as gateway:
        session = gateway.create_session("first")
    with open_persistence(home=tmp_path) as gateway:
        assert gateway.get_session(session["id"])["title"] == "first"


def test_is_writer_active_tracks_the_registry(tmp_path):
    assert not SessionGateway.is_writer_active(home=tmp_path)
    with open_persistence(home=tmp_path):
        assert SessionGateway.is_writer_active(home=tmp_path)
    assert not SessionGateway.is_writer_active(home=tmp_path)


def test_reader_sees_writer_data_but_cannot_write(tmp_path):
    with open_persistence(home=tmp_path) as gateway:
        session = gateway.create_session("shared")
        gateway.append_message(session["id"], "user", "hello from writer")
        reader = open_persistence_reader(home=tmp_path)
        try:
            found = reader.get_session(session["id"])
            assert [m["content"] for m in found["messages"]] == ["hello from writer"]
            with pytest.raises((SingleWriterError, sqlite3.OperationalError)):
                reader.append_message(session["id"], "user", "sneaky write")
            with pytest.raises((SingleWriterError, sqlite3.OperationalError)):
                reader.create_session("sneaky")
        finally:
            reader.close()


def test_two_readers_can_be_open_at_once(tmp_path):
    with open_persistence(home=tmp_path) as gateway:
        gateway.create_session("one")
    first = open_persistence_reader(home=tmp_path)
    second = open_persistence_reader(home=tmp_path)
    try:
        assert len(first.list_sessions()) == 1
        assert len(second.list_sessions()) == 1
    finally:
        first.close()
        second.close()


def test_reader_on_missing_database_fails(tmp_path):
    with pytest.raises(FileNotFoundError):
        open_persistence_reader(home=tmp_path)


def test_append_to_missing_session_fails(tmp_path):
    with open_persistence(home=tmp_path) as gateway, pytest.raises(SessionNotFoundError):
        gateway.append_message("no-such-session", "user", "hi")


def test_batch_append_is_atomic(tmp_path):
    with open_persistence(home=tmp_path) as gateway:
        session = gateway.create_session("atomic")
        gateway.append_message(session["id"], "user", "before")
        with pytest.raises(ValueError):
            gateway.append_messages(
                session["id"],
                [("user", "good"), ("nonsense-role", "bad"), ("user", "never")],
            )
        stored = gateway.get_session(session["id"])
        assert [m["content"] for m in stored["messages"]] == ["before"]


def test_concurrent_appends_do_not_lose_messages(tmp_path):
    with open_persistence(home=tmp_path) as gateway:
        session = gateway.create_session("concurrent")
        errors = []

        def _append(n):
            try:
                for i in range(10):
                    gateway.append_message(session["id"], "user", f"writer-{n}-{i}")
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_append, args=(n,)) for n in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert not errors
        stored = gateway.get_session(session["id"])
        assert len(stored["messages"]) == 40
        seqs = [m["seq"] for m in stored["messages"]]
        assert seqs == sorted(seqs) and len(set(seqs)) == 40


def test_contended_write_raises_instead_of_silently_dropping(tmp_path):
    # A separate connection holds an exclusive lock; with a tiny
    # busy timeout the gateway write must fail loudly.
    with open_persistence(home=tmp_path, busy_timeout_ms=50) as gateway:
        session = gateway.create_session("locked")
        holder = sqlite3.connect(tmp_path / "state.db", timeout=5)
        try:
            holder.execute("BEGIN EXCLUSIVE")
            holder.execute("INSERT INTO state_meta(key, value) VALUES ('hold', '1')")
            with pytest.raises(WriteContentionError):
                gateway.append_message(session["id"], "user", "blocked write")
        finally:
            holder.execute("ROLLBACK")
            holder.close()
        # After the lock is released the write succeeds.
        gateway.append_message(session["id"], "user", "after release")
        assert len(gateway.get_session(session["id"])["messages"]) == 1


def test_model_config_round_trip(tmp_path):
    with open_persistence(home=tmp_path) as gateway:
        assert gateway.get_model_config("model", "fallback") == "fallback"
        gateway.set_model_config("model", "llama-3.2-3b")
        gateway.set_model_config("temperature", 0.7)
        assert gateway.get_model_config("model") == "llama-3.2-3b"
        assert gateway.get_model_config("temperature") == 0.7
