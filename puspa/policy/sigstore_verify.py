"""Real Sigstore bundle verification for Puspa community skills.

This module verifies Sigstore bundles with the public Sigstore
infrastructure's own rules, using the ``sigstore`` library
(Apache-2.0). It is the real thing, not the local stand-in in
``puspa.policy.signing``: a bundle passes only if every check below
holds against a Sigstore trust root.

What one verification checks (all offline, no Fulcio or Rekor call):
1. The bundle parses as a Sigstore bundle (message signature or
   DSSE envelope).
2. The certificate chains to a Fulcio root in the trust root, and the
   certificate was valid at the Rekor integrated time recorded in the
   bundle. Certificate expiry is therefore judged at signing time,
   not at verification time: a Fulcio certificate lives about ten
   minutes, so a bundle whose certificate has since expired still
   verifies when it was valid while it was logged. That is correct
   Sigstore behavior, not a hole.
3. The certificate carries the expected identity (Subject Alternative
   Name) and the expected OIDC issuer (the issuer extension).
4. The artifact digest in the bundle matches the artifact bytes, and
   the signature is valid for those bytes under the certificate key.
5. The transparency log entry is genuine: its signed entry timestamp
   verifies against the Rekor log key in the trust root, and its
   Merkle inclusion proof verifies against the log root in the
   bundle's checkpoint.

Trust root source. The verifier never trusts a root from the bundle
or from caller input. By default it uses the production trust root
shipped inside the installed ``sigstore`` package
(``sigstore._store``). ``trust_root="staging"`` selects the staging
root shipped the same way, for tests. A file path selects a
``trusted_root.json`` on disk. The shipped root is pinned to the
installed ``sigstore`` version: it does not refresh itself over the
network. Upgrading the ``sigstore`` package refreshes it; a live TUF
refresh at verification time is a follow-up, not done here, because
verification must keep working with no network at all.

Scope. Verification only. This module cannot sign: creating a
bundle needs a Fulcio certificate from an OIDC identity and a Rekor
log entry, both live Sigstore services. Community skill authors sign
with the ``sigstore`` CLI; Puspa only verifies what they publish.

References (read 18 Nov 2026):
- https://pypi.org/project/sigstore/ (Apache-2.0, ``sigstore`` 4.5.0)
- https://sigstore.github.io/sigstore-python/ (offline verification:
  a bundle plus a trust root is enough; no service call at verify
  time)
- https://docs.sigstore.dev/ (verification = signature + expected
  identity + proof of inclusion in the transparency log)
"""

from __future__ import annotations

from importlib.resources import as_file, files
from pathlib import Path

__all__ = [
    "MAX_ARTIFACT_BYTES",
    "MAX_BUNDLE_BYTES",
    "verify_sigstore_bundle",
]

#: Largest bundle accepted. Real bundles are a few KiB; anything
#: bigger is not a skill bundle and is rejected before parsing.
MAX_BUNDLE_BYTES = 256 * 1024

#: Largest artifact accepted. A skill artifact is its manifest bytes,
#: far below this; the cap only stops abuse through huge inputs.
MAX_ARTIFACT_BYTES = 16 * 1024 * 1024

#: Trust roots shipped inside the installed ``sigstore`` package.
_BUNDLED_ROOTS = {
    "production": "https%3A%2F%2Ftuf-repo-cdn.sigstore.dev/trusted_root.json",
    "staging": "https%3A%2F%2Ftuf-repo-cdn.sigstage.dev/trusted_root.json",
}


def _load_sigstore():
    """Import the sigstore pieces this module needs.

    Kept behind a helper so a missing library is a clean failure
    instead of an import-time crash, and so tests can simulate the
    missing-library path. Returns ``(Bundle, TrustedRoot, Verifier,
    Identity)``.
    """
    from sigstore.models import Bundle, TrustedRoot
    from sigstore.verify import Verifier
    from sigstore.verify.policy import Identity

    return Bundle, TrustedRoot, Verifier, Identity


def _load_trusted_root(TrustedRoot, trust_root):
    """Load a trust root. ``trust_root`` is ``"production"``,
    ``"staging"``, or a path to a ``trusted_root.json`` file."""
    if trust_root in _BUNDLED_ROOTS:
        resource = files("sigstore._store").joinpath(_BUNDLED_ROOTS[trust_root])
        with as_file(resource) as path:
            return TrustedRoot.from_file(str(path))
    path = Path(trust_root)
    if path.is_file():
        return TrustedRoot.from_file(str(path))
    raise ValueError(f"unknown trust root: {trust_root!r}")


def _fail(reason: str) -> tuple[bool, str]:
    """One-line failure reason: library errors can be multi-line."""
    return False, " ".join(str(reason).split())


def verify_sigstore_bundle(bundle_bytes, artifact_bytes, identity, issuer, *,
                           trust_root="production") -> tuple[bool, str]:
    """Verify a Sigstore bundle for ``artifact_bytes``. Returns
    ``(ok, reason)`` and never raises for bad input.

    ``identity`` is the expected signer identity (for example the
    skill author's email or GitHub identity) and ``issuer`` is the
    expected OIDC issuer URL (for example
    ``https://github.com/login/oauth``). Both must match the
    certificate in the bundle exactly. ``trust_root`` is
    ``"production"`` (default), ``"staging"``, or a path to a
    ``trusted_root.json`` file. A bundle that fails any check is
    rejected; a missing ``sigstore`` library also rejects, never
    falls back to a weaker check.
    """
    if not isinstance(bundle_bytes, (bytes, bytearray)):
        return _fail("bundle must be bytes")
    if not isinstance(artifact_bytes, (bytes, bytearray)):
        return _fail("artifact must be bytes")
    if len(bundle_bytes) == 0:
        return _fail("bundle is empty")
    if len(bundle_bytes) > MAX_BUNDLE_BYTES:
        return _fail("bundle exceeds size limit")
    if len(artifact_bytes) > MAX_ARTIFACT_BYTES:
        return _fail("artifact exceeds size limit")
    if not isinstance(identity, str) or not identity:
        return _fail("expected identity missing")
    if not isinstance(issuer, str) or not issuer:
        return _fail("expected issuer missing")
    try:
        Bundle, TrustedRoot, Verifier, Identity = _load_sigstore()
    except Exception:
        return _fail("sigstore library is not installed")
    try:
        root = _load_trusted_root(TrustedRoot, trust_root)
    except Exception as exc:
        return _fail(f"cannot load trust root: {exc}")
    try:
        bundle = Bundle.from_json(bytes(bundle_bytes))
    except Exception as exc:
        return _fail(f"malformed bundle: {exc}")
    try:
        policy = Identity(identity=identity, issuer=issuer)
        Verifier(trusted_root=root).verify_artifact(
            bytes(artifact_bytes), bundle, policy
        )
    except Exception as exc:
        return _fail(f"sigstore verification failed: {exc}")
    return True, "ok"
