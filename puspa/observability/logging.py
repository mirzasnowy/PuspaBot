"""Redacted logging with bounded retention (E-18, plan section 9.2).

Every log line goes through the credential redaction in
:mod:`puspa.credentials.redact` before it is written, so a
secret that is logged by accident (an API key, a token, a
database URL, a vault value) never reaches the log file. The
current trace id (the task id) is prefixed to each line, so
logs for one task are greppable with one id.

Retention is bounded: a file handler rotates at 1 MB and keeps
5 backups (about 6 MB total by default), so logs cannot grow
without bound. Pass ``log_path=None`` for a stderr handler
(no file retention to bound).
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from puspa.credentials.redact import redact_text
from puspa.observability.tracing import current_trace_id

__all__ = [
    "RedactingFormatter",
    "get_observability_logger",
    "install_redacting_handler",
]

#: Default per-file cap for the rotating handler (1 MB).
DEFAULT_MAX_BYTES = 1_048_576
#: Default number of rotated backups kept (about 6 MB total).
DEFAULT_BACKUP_COUNT = 5


class RedactingFormatter(logging.Formatter):
    """A formatter that redacts secrets and prefixes the trace id.

    The formatted line (message + exception text) is redacted,
    so secrets inside arguments, tracebacks, or record fields
    are scrubbed too. When a trace id is known — from the
    record's ``trace_id`` attribute or the current context — it
    is prefixed as ``[trace_id=...]``.
    """

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        trace_id = getattr(record, "trace_id", None) or current_trace_id()
        redacted = redact_text(text)
        if trace_id:
            return f"[trace_id={trace_id}] {redacted}"
        return redacted


def install_redacting_handler(
    logger: logging.Logger | None = None,
    *,
    level: int = logging.INFO,
    log_path: str | Path | None = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
    backup_count: int = DEFAULT_BACKUP_COUNT,
) -> logging.Handler | None:
    """Install one redacting handler on ``logger`` (default: ``puspa``).

    With ``log_path`` the handler is a rotating file handler
    (bounded retention: ``max_bytes`` per file, ``backup_count``
    backups); without it the handler writes to stderr. Installing
    twice on the same logger is a no-op returning None, so
    repeated startup calls cannot duplicate log lines.
    """
    target = logger if logger is not None else logging.getLogger("puspa")
    if getattr(target, "_puspa_redacting_installed", False):
        return None
    if log_path is not None:
        handler: logging.Handler = RotatingFileHandler(
            log_path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
        )
    else:
        handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(RedactingFormatter())
    handler.setLevel(level)
    target.addHandler(handler)
    target.setLevel(min(target.level, level) if target.level else level)
    # setattr, not attribute assignment: Logger has no such field,
    # so mypy rejects the assignment, and the marker must live on
    # the logger itself (tests read it back with getattr).
    setattr(target, "_puspa_redacting_installed", True)  # noqa: B010
    return handler


def get_observability_logger(name: str = "puspa.observability") -> logging.Logger:
    """A logger whose output is redacted and trace-tagged.

    Installs the redacting handler on the ``puspa`` parent once,
    then returns the child logger (propagation carries its
    records to the handler).
    """
    install_redacting_handler()
    return logging.getLogger(name)


def log_record_fields(record: logging.LogRecord) -> dict[str, Any]:
    """The observability fields of a log record (trace id, level, name)."""
    return {
        "trace_id": getattr(record, "trace_id", None) or current_trace_id(),
        "level": record.levelname,
        "logger": record.name,
    }
