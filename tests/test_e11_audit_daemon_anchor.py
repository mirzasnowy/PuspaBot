"""Tests for E-11 (a): audit writer as a separate process, and (b): daily anchoring.

(a) The daemon is the only process holding a database connection:
the agent side (RemoteAuditWriter) takes only a socket path, never
a DSN, and the daemon serves append / read / verify only — there is
no operation that rewrites history, and the INSERT-only role would
refuse one anyway. One test runs the daemon as a real subprocess
(``python -m puspa.ledger.audit_daemon``) to prove the split is a
process boundary, not just a class boundary.

(b) Anchors pin each day's chain heads outside the database:
write-once files, digest + per-task head comparison on verify, and
a tampered past shows up as a mismatch. The live external target
(object-lock bucket / second VPS) is E-02 work; the local store
here is the stand-in with the same write-once semantics.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from puspa.ledger import (
    AnchorConflict,
    AnchorRecord,
    AuditDaemonError,
    AuditWriterServer,
    Ledger,
    LocalAnchorStore,
    RemoteAuditWriter,
    anchor_day,
    collect_heads,
    compute_digest,
    verify_anchor,
)

TASK_A = "task-e11-anchor-a"
TASK_B = "task-e11-anchor-b"
AGENT_ID = "agent-e11"


def _append(ledger, task_id, content=None):
    return ledger.append_event(
        task_id=task_id,
        agent_id=AGENT_ID,
        event_type="task_created",
        content=content or {"step": 1},
    )


@pytest.fixture
def ledger(test_dsn):
    return Ledger(test_dsn)


@pytest.fixture
def server(tmp_path, test_dsn):
    """Daemon running in a thread, on a private socket."""
    sock = tmp_path / "audit.sock"
    srv = AuditWriterServer(sock, test_dsn)
    srv.start()
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv, RemoteAuditWriter(sock)
    srv.shutdown()
    thread.join(timeout=5)


# --- (a) separate-process audit writer ---------------------------------------


def test_daemon_ping_append_read_verify(server):
    _srv, client = server
    assert client.ping() is True
    first = client.append_event(
        task_id=TASK_A, agent_id=AGENT_ID, event_type="task_created", content={"step": 1}
    )
    second = client.append_event(
        task_id=TASK_A, agent_id=AGENT_ID, event_type="tool_called", content={"step": 2}
    )
    assert first["previous_hash"] == "GENESIS"
    assert second["previous_hash"] == first["hash"]
    events = client.events(TASK_A)
    assert [e["id"] for e in events] == [first["id"], second["id"]]
    assert [e["type"] for e in events] == ["task_created", "tool_called"]
    valid, broken = client.verify_chain(TASK_A)
    assert valid is True and broken == []


def test_daemon_refuses_bad_event_type_and_unknown_op(server):
    _srv, client = server
    with pytest.raises(AuditDaemonError):
        client.append_event(
            task_id=TASK_A,
            agent_id=AGENT_ID,
            event_type="not_a_real_type",
            content={},
        )
    with pytest.raises(AuditDaemonError):
        client._call({"op": "delete_everything"})


def test_daemon_client_needs_no_database_credential(server, tmp_path):
    _srv, client = server
    # The client carries no DSN attribute at all: the agent process
    # physically cannot open a ledger connection of its own.
    assert not hasattr(client, "dsn")
    assert client.ping() is True
    missing = RemoteAuditWriter(tmp_path / "no-such-socket.sock", timeout=1.0)
    with pytest.raises(AuditDaemonError):
        missing.ping()


def test_daemon_runs_as_its_own_process(tmp_path, test_dsn):
    """The split is a process boundary: spawn the real entry point."""
    sock = tmp_path / "audit.sock"
    env = dict(os.environ, PUSPA_TEST_DSN=test_dsn)
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "puspa.ledger.audit_daemon",
            "--socket",
            str(sock),
        ],
        cwd=str(Path(__file__).resolve().parent.parent),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    client = RemoteAuditWriter(sock, timeout=2.0)
    try:
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                if client.ping():
                    break
            except AuditDaemonError:
                time.sleep(0.1)
        else:
            raise AssertionError("audit daemon subprocess never answered ping")
        event = client.append_event(
            task_id=TASK_A,
            agent_id=AGENT_ID,
            event_type="task_created",
            content={"step": 1},
        )
        assert event["task_id"] == TASK_A
        valid, broken = client.verify_chain(TASK_A)
        assert valid is True and broken == []
        assert proc.poll() is None  # still alive, serving
    finally:
        proc.terminate()
        proc.wait(timeout=10)


# --- (b) daily anchoring -----------------------------------------------------


def test_anchor_and_verify_roundtrip(ledger, test_dsn, tmp_path):
    _append(ledger, TASK_A)
    _append(ledger, TASK_A, {"step": 2})
    _append(ledger, TASK_B)
    store = LocalAnchorStore(tmp_path / "anchors")
    today = datetime.now(UTC).date()
    record = anchor_day(test_dsn, store, day=today)
    assert set(record.heads) == {TASK_A, TASK_B}
    assert record.heads[TASK_A]["event_count"] == 2
    assert record.heads[TASK_B]["event_count"] == 1
    assert record.digest == compute_digest(record.heads)
    ok, problems = verify_anchor(test_dsn, store, day=today)
    assert ok is True and problems == []
    # Re-anchoring the same unchanged day is a no-op, same record.
    again = anchor_day(test_dsn, store, day=today)
    assert again.to_dict() == record.to_dict()
    assert store.dates() == [today.isoformat()]


def test_anchor_cutoff_excludes_later_events(ledger, test_dsn, tmp_path):
    _append(ledger, TASK_A)
    store = LocalAnchorStore(tmp_path / "anchors")
    yesterday = datetime.now(UTC).date() - timedelta(days=1)
    record = anchor_day(test_dsn, store, day=yesterday)
    assert record.heads == {}  # today's events are after yesterday's end
    ok, problems = verify_anchor(test_dsn, store, day=yesterday)
    assert ok is True and problems == []


def test_anchor_detects_rewritten_past(ledger, test_dsn, tmp_path):
    _append(ledger, TASK_A)
    head = _append(ledger, TASK_A, {"step": 2})
    store = LocalAnchorStore(tmp_path / "anchors")
    today = datetime.now(UTC).date()
    anchor_day(test_dsn, store, day=today)
    # An owner rewrites the head row's hash (what the anchor guards).
    with psycopg.connect(test_dsn, autocommit=True) as conn:
        conn.execute("UPDATE events SET hash = 'tampered' WHERE id = %s", (head["id"],))
    ok, problems = verify_anchor(test_dsn, store, day=today)
    assert ok is False
    assert problems, "a rewritten past must be reported"
    assert any(TASK_A in problem for problem in problems)


def test_anchor_and_chain_cover_different_rewrites(ledger, test_dsn, tmp_path):
    """Boundary, stated honestly: the anchor pins chain heads, not
    every row. Rewriting a middle row leaves the head hash intact,
    so the anchor still verifies — but the hash chain itself breaks,
    and the ledger's own verify_chain catches it. The two checks
    together cover a rewrite anywhere in the past."""
    first = _append(ledger, TASK_A)
    _append(ledger, TASK_A, {"step": 2})
    store = LocalAnchorStore(tmp_path / "anchors")
    today = datetime.now(UTC).date()
    anchor_day(test_dsn, store, day=today)
    with psycopg.connect(test_dsn, autocommit=True) as conn:
        conn.execute("UPDATE events SET hash = 'tampered' WHERE id = %s", (first["id"],))
    ok, _problems = verify_anchor(test_dsn, store, day=today)
    assert ok is True  # head unchanged: the anchor cannot see this one
    valid, broken = ledger.verify_chain(TASK_A)
    assert valid is False
    assert broken, "the chain must flag the rewritten row"


def test_anchor_store_refuses_overwrite(tmp_path):
    store = LocalAnchorStore(tmp_path / "anchors")
    today = datetime.now(UTC).date()
    heads = {TASK_A: {"head": "abc", "event_count": 1, "head_at": "2026-11-20T00:00:00+00:00"}}
    first = AnchorRecord(
        date=today.isoformat(),
        heads=heads,
        digest=compute_digest(heads),
        created_at=datetime.now(UTC).isoformat(),
    )
    assert store.put(first).to_dict() == first.to_dict()
    # Identical content: idempotent, same record back.
    assert store.put(first).to_dict() == first.to_dict()
    # Different content for the same day: refused, history stands.
    other_heads = {
        TASK_A: {"head": "zzz", "event_count": 1, "head_at": first.heads[TASK_A]["head_at"]}
    }
    other = AnchorRecord(
        date=today.isoformat(),
        heads=other_heads,
        digest=compute_digest(other_heads),
        created_at=datetime.now(UTC).isoformat(),
    )
    with pytest.raises(AnchorConflict):
        store.put(other)
    assert store.get(today).to_dict() == first.to_dict()


def test_signed_anchor_verifies_and_wrong_key_fails(ledger, test_dsn, tmp_path):
    _append(ledger, TASK_A)
    store = LocalAnchorStore(tmp_path / "anchors")
    today = datetime.now(UTC).date()
    sk = Ed25519PrivateKey.generate()
    record = anchor_day(test_dsn, store, day=today, signing_key=sk, signer="operator")
    assert record.signature and record.signer == "operator"
    ok, problems = verify_anchor(test_dsn, store, day=today, public_key=sk.public_key())
    assert ok is True and problems == []
    other_pk = Ed25519PrivateKey.generate().public_key()
    ok, problems = verify_anchor(test_dsn, store, day=today, public_key=other_pk)
    assert ok is False
    assert any("signature" in problem for problem in problems)


def test_verify_anchor_without_stored_anchor(test_dsn, tmp_path):
    store = LocalAnchorStore(tmp_path / "anchors")
    ok, problems = verify_anchor(test_dsn, store, day=datetime.now(UTC).date() - timedelta(days=3))
    assert ok is False
    assert problems and "no anchor" in problems[0]


def test_collect_heads_is_empty_without_events(test_dsn):
    heads = collect_heads(test_dsn, before=datetime.now(UTC))
    assert heads == {}


def test_anchor_cli_roundtrip(ledger, test_dsn, tmp_path):
    _append(ledger, TASK_A)
    store_dir = tmp_path / "anchors"
    env = dict(os.environ, PUSPA_TEST_DSN=test_dsn)
    root = str(Path(__file__).resolve().parent.parent)
    today = datetime.now(UTC).date().isoformat()
    anchored = subprocess.run(
        [
            sys.executable,
            "-m",
            "puspa.ledger.anchor",
            "--dir",
            str(store_dir),
            "--day",
            today,
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert anchored.returncode == 0, anchored.stdout + anchored.stderr
    assert "anchored" in anchored.stdout
    verified = subprocess.run(
        [
            sys.executable,
            "-m",
            "puspa.ledger.anchor",
            "--dir",
            str(store_dir),
            "--day",
            today,
            "--verify",
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert verified.returncode == 0, verified.stdout + verified.stderr
    assert "verifies" in verified.stdout
    assert json.loads((store_dir / f"anchor-{today}.json").read_text())["date"] == today
