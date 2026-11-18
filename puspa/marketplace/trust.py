"""Trust scoring for marketplace modules (G3 flagship).

A marketplace without a trust model is a security hole (341
malicious skills on ClawHub are the proof), so no module installs
without a score, and a module below the minimum cannot install
without the user's explicit approval.

Score 0-100 from checkable signals (weights sum to 100):

- Sigstore / signed manifest (30): the skill directory passes
  ``puspa.policy.skill_signing.verify_skill`` — the existing local
  Ed25519 manifest signature, plus a real Sigstore bundle when the
  manifest declares one. ``None`` (not checked) scores 0 and is
  reported, never assumed.
- Manifest completeness (20): strict validation passes with no
  warnings.
- Known licence (15): licence in ``KNOWN_LICENSES``.
- Trusted source (20): source is a known-trusted origin
  (``puspa-builtin`` / ``puspa-official``). An unknown source
  scores 0; a source on the denylist scores 0 and is flagged.
- Install history (15): prior clean installs of this skill
  (15), no history (0 — a first install earns no history points,
  so an unsigned skill from a trusted source still lands below
  the floor), any recorded incident (0).

``MIN_TRUST_SCORE`` (60) is the install floor. ``score_module``
takes the signals as plain data so tests can score without a
signature key; ``score_skill_dir`` is the convenience that runs
the real ``verify_skill`` check for a folder on disk.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .errors import TrustError
from .manifest import KNOWN_LICENSES, validate_manifest

__all__ = [
    "DENIED_SOURCES",
    "MIN_TRUST_SCORE",
    "TRUSTED_SOURCES",
    "TrustScore",
    "can_install_without_approval",
    "score_module",
    "score_skill_dir",
]

#: Minimum trust score for an install without explicit user approval.
MIN_TRUST_SCORE = 60

#: Sources the marketplace trusts outright.
TRUSTED_SOURCES = frozenset({"puspa-builtin", "puspa-official"})

#: Sources that are never trusted (known malicious distributors).
DENIED_SOURCES = frozenset({"clawhub-unverified", "unknown-mirror"})

_WEIGHTS = {
    "signature": 30,
    "manifest": 20,
    "license": 15,
    "source": 20,
    "history": 15,
}


@dataclass
class TrustScore:
    """A trust score with its per-signal breakdown."""

    total: int
    signals: dict = field(default_factory=dict)
    reasons: list = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """True when the score meets the install floor."""
        return self.total >= MIN_TRUST_SCORE

    def to_dict(self) -> dict:
        return {
            "total": self.total,
            "signals": dict(self.signals),
            "reasons": list(self.reasons),
            "passed": self.passed,
            "minimum": MIN_TRUST_SCORE,
        }


def can_install_without_approval(score) -> bool:
    """True when the score meets the floor (no approval needed)."""
    total = score.total if isinstance(score, TrustScore) else int(score)
    return total >= MIN_TRUST_SCORE


def score_module(
    *,
    manifest: dict,
    signature_ok: bool | None = None,
    source: str | None = None,
    clean_installs: int = 0,
    incidents: int = 0,
) -> TrustScore:
    """Score a module from checkable signals.

    ``manifest`` is the raw manifest dict. ``signature_ok`` is the
    result of ``verify_skill`` for the skill folder (``None`` =
    not checked). ``source`` overrides ``manifest["source"]``.
    ``clean_installs`` / ``incidents`` come from the marketplace
    audit history for this skill.
    """
    signals: dict[str, int] = {}
    reasons: list[str] = []

    # Signal 1: signature / Sigstore verification (30).
    if signature_ok is True:
        signals["signature"] = _WEIGHTS["signature"]
        reasons.append("manifest signature verified (Sigstore/local)")
    elif signature_ok is False:
        signals["signature"] = 0
        reasons.append("manifest signature FAILED verification")
    else:
        signals["signature"] = 0
        reasons.append("signature not checked (no public key / no check run)")

    # Signal 2: manifest completeness (20).
    _manifest, problems, warnings = validate_manifest(dict(manifest))
    if not problems and not warnings:
        signals["manifest"] = _WEIGHTS["manifest"]
        reasons.append("manifest complete, no warnings")
    elif not problems:
        signals["manifest"] = _WEIGHTS["manifest"] // 2
        reasons.append(f"manifest valid with warnings: {'; '.join(warnings)}")
    else:
        signals["manifest"] = 0
        reasons.append(f"manifest invalid: {'; '.join(problems)}")

    # Signal 3: known licence (15).
    licence = manifest.get("license", "")
    if licence in KNOWN_LICENSES:
        signals["license"] = _WEIGHTS["license"]
        reasons.append(f"known licence {licence}")
    else:
        signals["license"] = 0
        reasons.append(f"unknown licence {licence!r}")

    # Signal 4: trusted source (20).
    src = source if source is not None else manifest.get("source", "")
    if src in DENIED_SOURCES:
        signals["source"] = 0
        reasons.append(f"source {src!r} is on the denied list")
    elif src in TRUSTED_SOURCES:
        signals["source"] = _WEIGHTS["source"]
        reasons.append(f"trusted source {src}")
    else:
        signals["source"] = 0
        reasons.append(f"untrusted source {src!r}")

    # Signal 5: install history (15).
    if incidents > 0:
        signals["history"] = 0
        reasons.append(f"{incidents} recorded incident(s) for this skill")
    elif clean_installs > 0:
        signals["history"] = _WEIGHTS["history"]
        reasons.append(f"{clean_installs} prior clean install(s)")
    else:
        signals["history"] = 0
        reasons.append("no install history yet")

    total = sum(signals.values())
    return TrustScore(total=total, signals=signals, reasons=reasons)


def score_skill_dir(
    dirpath, public_key, *, clean_installs: int = 0, incidents: int = 0, **verify_kwargs
) -> TrustScore:
    """Score a skill folder on disk, running the real signature check.

    ``verify_skill`` never raises for a damaged skill (it returns
    False), so this function never raises for a bad folder either:
    an unreadable folder scores 0 on the signature signal and the
    manifest signal carries the problem.
    """
    from puspa.policy.skill_signing import verify_skill

    folder = Path(dirpath)
    signature_ok = verify_skill(folder, public_key, **verify_kwargs)
    manifest_path = folder / "manifest.json"
    try:
        import json

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            manifest = {}
    except Exception:
        manifest = {}
    return score_module(
        manifest=manifest,
        signature_ok=signature_ok,
        clean_installs=clean_installs,
        incidents=incidents,
    )


def require_trust(score, *, explicit_approval: bool = False, skill_id: str = "") -> None:
    """Raise :class:`TrustError` unless the score passes or the user
    explicitly approved this install."""
    if can_install_without_approval(score):
        return
    if explicit_approval:
        return
    raise TrustError(
        f"skill {skill_id!r} trust score {score.total} is below the "
        f"minimum {MIN_TRUST_SCORE}; explicit user approval is required"
    )
