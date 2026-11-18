"""Pembaca terkarantina + penjaga prompt injection (definisi 2.6)."""

from .injection import Finding, mark, scan
from .reader import MAX_CHARS, QuarantinedReader

__all__ = ["MAX_CHARS", "Finding", "QuarantinedReader", "mark", "scan"]
