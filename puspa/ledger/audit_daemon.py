"""Audit writer as a separate process (E-11, plan section 6.6).

K12 put the audit log behind an INSERT-only database role, but the
writer still ran inside the agent process: any code in that process
could open its own connection with the operator DSN from the
environment and bypass the role entirely. This module closes that
gap by moving the writer into its own process:

- The daemon (:class:`AuditWriterServer`) is the ONLY process that
  holds a database connection. It appends through
  :class:`puspa.ledger.audit_writer.AuditWriter` (the ``puspa_app``
  role: INSERT + SELECT on events, nothing else).
- The agent process gets no DSN at all. It talks to the daemon over
  a Unix socket through :class:`RemoteAuditWriter`, which offers
  the same append / read / verify surface but never imports psycopg
  and never touches the database.
- The socket directory must be private to the two processes (the
  daemon sets mode 0700 on a directory it creates; the socket file
  itself is mode 0600). Anyone who can write to the socket can ask
  for appends — that is the point — but nobody outside the two
  processes reaches the database through it, and the daemon never
  serves UPDATE or DELETE: the protocol has no such operation, and
  the database role would refuse it anyway.

Protocol: one JSON object per connection, newline-terminated.
Request: ``{"op": "append" | "events" | "verify_chain" | "ping", ...}``.
Response: ``{"ok": true, ...}`` or ``{"ok": false, "error": "..."}``.

Run as its own process::

    python -m puspa.ledger.audit_daemon --socket /run/puspa/audit.sock
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import signal
import socket
import threading
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from .audit_writer import AuditWriter

__all__ = ["AuditDaemonError", "AuditWriterServer", "RemoteAuditWriter", "main"]

#: Largest single request the daemon accepts (bytes). Audit events
#: are small; anything bigger is a mistake or an abuse attempt.
MAX_REQUEST_BYTES = 4 * 1024 * 1024


class AuditDaemonError(Exception):
    """The audit daemon refused a request or could not be reached."""


def _jsonable(value: Any) -> Any:
    """Recursively convert a value to plain JSON data (datetimes
    become ISO-8601 strings, UUIDs become strings)."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _parse_valid_at(raw: Any) -> datetime | None:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw
    if isinstance(raw, str):
        return datetime.fromisoformat(raw)
    raise AuditDaemonError("valid_at must be an ISO-8601 string or null")


class AuditWriterServer:
    """The audit writer, alone in its process.

    Binds a Unix socket at ``socket_path`` and serves append / read
    / verify requests from the agent process. Holds the only
    database connection, through :class:`AuditWriter` (the
    INSERT-only application role). ``dsn`` defaults to
    ``PUSPA_TEST_DSN`` like the rest of the ledger package.
    """

    def __init__(self, socket_path: str | Path, dsn: str | None = None):
        self.socket_path = Path(socket_path)
        self._writer = AuditWriter(dsn)
        self._stop = threading.Event()
        self._listener: socket.socket | None = None

    # -- lifecycle ------------------------------------------------------

    def start(self) -> None:
        """Bind the socket and listen. Does not serve yet (see
        :meth:`serve_forever`)."""
        parent = self.socket_path.parent
        parent.mkdir(parents=True, exist_ok=True)
        # Best effort on filesystems without chmod.
        with contextlib.suppress(OSError):
            os.chmod(parent, 0o700)
        if self.socket_path.exists():
            self.socket_path.unlink()
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(self.socket_path))
        with contextlib.suppress(OSError):
            os.chmod(self.socket_path, 0o600)
        listener.listen(16)
        listener.settimeout(0.2)
        self._listener = listener

    def serve_forever(self) -> None:
        """Accept connections until :meth:`shutdown` is called."""
        if self._listener is None:
            self.start()
        assert self._listener is not None
        while not self._stop.is_set():
            try:
                conn, _ = self._listener.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            thread = threading.Thread(target=self._handle_connection, args=(conn,), daemon=True)
            thread.start()

    def shutdown(self) -> None:
        """Stop serving and release the socket."""
        self._stop.set()
        if self._listener is not None:
            with contextlib.suppress(OSError):
                self._listener.close()
            self._listener = None
        try:
            self.socket_path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass

    # -- protocol ---------------------------------------------------------

    def _handle_connection(self, conn: socket.socket) -> None:
        with conn:
            try:
                raw = self._read_request(conn)
                request = json.loads(raw.decode("utf-8"))
                response = self._dispatch(request)
            except Exception as exc:
                response = {"ok": False, "error": str(exc)[:500]}
            with contextlib.suppress(OSError):
                conn.sendall((json.dumps(_jsonable(response)) + "\n").encode("utf-8"))

    def _read_request(self, conn: socket.socket) -> bytes:
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = conn.recv(65536)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_REQUEST_BYTES:
                raise AuditDaemonError("request exceeds the size limit")
            chunks.append(chunk)
            if b"\n" in chunk:
                break
        raw = b"".join(chunks)
        line, _, _ = raw.partition(b"\n")
        if not line.strip():
            raise AuditDaemonError("empty request")
        return line

    def _dispatch(self, request: Any) -> dict[str, Any]:
        if not isinstance(request, dict):
            raise AuditDaemonError("request must be a JSON object")
        op = request.get("op")
        if op == "ping":
            return {"ok": True}
        if op == "append":
            event = self._writer.append_event(
                task_id=request["task_id"],
                agent_id=request["agent_id"],
                subagent_id=request.get("subagent_id"),
                event_type=request["event_type"],
                content=request["content"],
                parent_id=request.get("parent_id"),
                initiator=request.get("initiator"),
                valid_at=_parse_valid_at(request.get("valid_at")),
            )
            return {"ok": True, "event": _jsonable(event)}
        if op == "events":
            events = self._writer.events(request["task_id"], event_type=request.get("event_type"))
            return {"ok": True, "events": _jsonable(events)}
        if op == "verify_chain":
            valid, broken = self._writer.verify_chain(request["task_id"])
            return {"ok": True, "valid": valid, "broken": broken}
        raise AuditDaemonError(f"unknown op: {op!r}")


class RemoteAuditWriter:
    """The agent side of the audit daemon: same surface as
    :class:`AuditWriter`, no database anywhere.

    Takes only a socket path — never a DSN — and never imports
    psycopg: the agent process physically cannot open a ledger
    connection of its own. Events read back through this client
    carry ISO-8601 strings for ``created_at`` / ``valid_at``
    (they cross JSON), where the local :class:`AuditWriter`
    returns datetimes.
    """

    def __init__(self, socket_path: str | Path, *, timeout: float = 10.0):
        self.socket_path = Path(socket_path)
        self.timeout = timeout

    def _call(self, payload: dict[str, Any]) -> dict[str, Any]:
        raw = (json.dumps(payload) + "\n").encode("utf-8")
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
                conn.settimeout(self.timeout)
                conn.connect(str(self.socket_path))
                conn.sendall(raw)
                data = b""
                while b"\n" not in data:
                    chunk = conn.recv(65536)
                    if not chunk:
                        break
                    data += chunk
        except (TimeoutError, OSError) as exc:
            raise AuditDaemonError(
                f"cannot reach the audit daemon at {self.socket_path}: {exc}"
            ) from exc
        line, _, _ = data.partition(b"\n")
        if not line.strip():
            raise AuditDaemonError("audit daemon closed the connection without a reply")
        try:
            response = json.loads(line.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise AuditDaemonError(f"audit daemon sent an unreadable reply: {exc}") from exc
        if not response.get("ok"):
            raise AuditDaemonError(str(response.get("error", "audit daemon refused")))
        return response

    def ping(self) -> bool:
        """True when the daemon answers."""
        return bool(self._call({"op": "ping"}).get("ok"))

    def append_event(self, **kwargs: Any) -> dict[str, Any]:
        """Append one event through the daemon; returns the stored event."""
        payload = {"op": "append"}
        valid_at = kwargs.get("valid_at")
        if isinstance(valid_at, datetime):
            kwargs = dict(kwargs, valid_at=valid_at.isoformat())
        payload.update(kwargs)
        return dict(self._call(payload)["event"])

    def events(self, task_id: str, event_type: str | None = None) -> list[dict]:
        """Read one task's events through the daemon."""
        payload: dict[str, Any] = {"op": "events", "task_id": task_id}
        if event_type is not None:
            payload["event_type"] = event_type
        return list(self._call(payload)["events"])

    def verify_chain(self, task_id: str) -> tuple[bool, list[str]]:
        """Verify one task's hash chain through the daemon."""
        response = self._call({"op": "verify_chain", "task_id": task_id})
        return (bool(response["valid"]), list(response["broken"]))


def main(argv: list[str] | None = None) -> int:
    """Run the audit daemon as its own process.

    Example::

        python -m puspa.ledger.audit_daemon --socket /run/puspa/audit.sock
    """
    parser = argparse.ArgumentParser(description="Audit writer daemon (E-11).")
    parser.add_argument("--socket", required=True, help="Unix socket path to listen on")
    parser.add_argument("--dsn", default=None, help="Postgres DSN (default: PUSPA_TEST_DSN)")
    args = parser.parse_args(argv)
    server = AuditWriterServer(args.socket, args.dsn)
    server.start()

    def _stop(_signum: int, _frame: Any) -> None:
        server.shutdown()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    print(f"audit daemon listening on {args.socket}")
    try:
        server.serve_forever()
    finally:
        server.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
