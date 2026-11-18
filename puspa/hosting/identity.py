"""Server identity key: the long-term key the app pins at linking.

Pattern (DuskBot ``server/card-seal.ts``, rewritten as Puspa code):
the server holds one long-term Ed25519 identity key. Its public half
is pinned by the app when the app links to the server; the private
half signs every per-start sealing key (see ``seal.py``), so a relay
in the middle cannot swap in its own sealing key — the signature
would not verify against the pinned identity key.

Storage rules, all enforced here:

- The key lives in exactly one file, mode ``0600`` (created with
  ``os.open`` + an explicit ``chmod`` afterwards, because the umask
  would otherwise weaken the mode passed to ``open``). The key is
  created once and reused across restarts; a fresh key on every start
  would invalidate every pinned app.
- An existing key file whose mode is more open than ``0600`` is
  refused (fail-closed) instead of silently used.
- The key file is excluded from backups by policy: see
  ``IDENTITY_KEY_BACKUP_EXCLUDED``. The Puspa backup module
  (``puspa.backup``) only dumps Postgres, it never walks the home
  directory, so there is no backup path that could pick the file up;
  the exclusion is recorded here so any future file-based backup
  honours it. Losing the identity key orphans every pinned app (they
  must re-link); that is the price of the pinning, and it is why the
  file must be backed up separately by the operator, outside Puspa.
"""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from .errors import IdentityKeyError

__all__ = [
    "IDENTITY_KEY_BACKUP_EXCLUDED",
    "IDENTITY_KEY_FILENAME",
    "IdentityKey",
    "default_identity_key_path",
    "load_or_create_identity_key",
]

#: The identity key file must never be included in a backup. The
#: operator keeps an offline copy outside Puspa instead.
IDENTITY_KEY_BACKUP_EXCLUDED = True

IDENTITY_KEY_FILENAME = "identity.key"

_KEY_MODE = 0o600


def default_identity_key_path() -> Path:
    """Default identity key path: ``~/.puspa/identity.key``."""
    return Path.home() / ".puspa" / IDENTITY_KEY_FILENAME


@dataclass
class IdentityKey:
    """A loaded server identity key (private half + public half)."""

    private_key: Ed25519PrivateKey
    path: Path

    @property
    def public_key(self) -> Ed25519PublicKey:
        return self.private_key.public_key()

    def public_bytes(self) -> bytes:
        """Raw 32-byte public key (what the app pins)."""
        return self.public_key.public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )

    def fingerprint(self) -> str:
        """SHA-256 hex of the raw public key; the pin value."""
        return hashlib.sha256(self.public_bytes()).hexdigest()

    def sign(self, data: bytes) -> bytes:
        """Sign bytes with the identity key (Ed25519)."""
        return self.private_key.sign(data)

    def verify(self, data: bytes, signature: bytes) -> bool:
        """True when the signature is valid for these bytes. Never raises."""
        from cryptography.exceptions import InvalidSignature

        try:
            self.public_key.verify(signature, data)
        except (InvalidSignature, ValueError, TypeError):
            return False
        return True


def _load_existing(path: Path) -> IdentityKey:
    mode = stat.S_IMODE(os.stat(path).st_mode)
    if mode & 0o077:
        raise IdentityKeyError(
            f"identity key file {path} has mode {oct(mode)}; refusing to use "
            "a key readable by anyone but its owner (expected 0600)"
        )
    try:
        private_key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    except (ValueError, TypeError) as exc:
        raise IdentityKeyError(f"identity key file {path} is not a valid PEM private key") from exc
    if not isinstance(private_key, Ed25519PrivateKey):
        raise IdentityKeyError(f"identity key file {path} is not an Ed25519 key")
    return IdentityKey(private_key=private_key, path=path)


def load_or_create_identity_key(path: str | Path | None = None) -> IdentityKey:
    """Load the identity key, creating it (mode 0600) when missing.

    The key is generated once with ``secrets``-grade randomness from
    the OS and never leaves this file. Creating the file is atomic
    enough for a single server process (``O_EXCL``); a concurrent
    creator loses the race and the loser loads the winner's file.
    """
    resolved = Path(path) if path is not None else default_identity_key_path()
    if resolved.exists():
        return _load_existing(resolved)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    private_key = Ed25519PrivateKey.generate()
    encoded = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    try:
        fd = os.open(resolved, os.O_WRONLY | os.O_CREAT | os.O_EXCL, _KEY_MODE)
    except FileExistsError:
        return _load_existing(resolved)
    try:
        os.write(fd, encoded)
    finally:
        os.close(fd)
    # The mode passed to os.open is masked by the umask; force 0600.
    os.chmod(resolved, _KEY_MODE)
    return IdentityKey(private_key=private_key, path=resolved)
