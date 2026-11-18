"""Marketplace registry: import, install, rollback, uninstall (G3).

The registry is the marketplace's index of installable modules and
skills. It draws a hard line between the three origins:

- ``builtin``: skills shipped with Puspa (trusted, signed by Puspa).
- ``user``: skills the user authored themselves.
- ``library``: skills from the marketplace (signed by their author,
  trust-scored before install).

Lifecycle (DuskBot parity, rewritten):

- **Import** takes only ``SKILL.md`` from a folder and lands
  **disabled**; the manifest is validated strictly and the install
  is recorded with sha256 + source + licence so it can be audited
  and rolled back. At most 30 skills in the index (4,000 bytes of
  index JSON), at most 20 staged.
- **Install** enables a staged skill. It checks the trust floor
  (see :mod:`puspa.marketplace.trust`) and the entitlement seam
  (see below) before anything is enabled.
- **Rollback** restores the previously installed version, using
  the recorded sha256 + source of that version — never a fresh
  download.
- **Uninstall** removes the install but keeps the audit trail.

Price / entitlement seam (G2 owns real charging): the manifest
carries ``priceCredits`` + ``license`` and every install records
both. The entitlement check is an injected callable
``entitlement(user_id, manifest) -> bool``. The default allows
free modules (price 0) and refuses paid modules without an
entitlement record. Real charging and entitlement records are
G2 billing work, not faked here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from .errors import EntitlementError, MarketplaceError, TrustError
from .manifest import (
    MAX_INDEX_BYTES,
    MAX_SKILLS,
    MAX_STAGED,
    SkillManifest,
    validate_skill_folder,
)
from .store import MemoryStore
from .trust import MIN_TRUST_SCORE, TrustScore, require_trust, score_module

__all__ = [
    "MarketplaceRegistry",
    "RegistryEntry",
    "default_entitlement",
]

#: Install statuses.
STATUS_DISABLED = "disabled"
STATUS_ENABLED = "enabled"
STATUS_REMOVED = "removed"


def default_entitlement(user_id: str, manifest: SkillManifest) -> bool:
    """Default entitlement check: free modules (price 0) may install;
    paid modules without an entitlement record are refused.

    This is a seam, not charging: G2 billing owns real entitlement
    records. Pass a real ``entitlement`` callable to install paid
    modules.
    """
    return manifest.price_credits == 0


@dataclass
class RegistryEntry:
    """One skill in the registry index."""

    manifest: SkillManifest
    origin: str
    status: str = STATUS_DISABLED
    staged: bool = True
    skill_text: str = ""
    frontmatter: dict = field(default_factory=dict)

    @property
    def skill_id(self) -> str:
        return self.manifest.skill_id

    def index_entry(self) -> dict:
        entry = self.manifest.index_entry()
        entry["status"] = self.status
        return entry


class MarketplaceRegistry:
    """In-memory registry index backed by a marketplace store.

    ``store`` persists installs, versions and audit records
    (``MemoryStore`` by default; pass ``PostgresStore`` for durable
    use). ``trust_scorer`` maps ``(manifest_dict, skill_id)`` to a
    :class:`TrustScore`; the default scores from the manifest alone
    (signature not checked, so library skills score below the floor
    until a real scorer is wired in — fail-closed by design).
    ``entitlement`` is the paid-module check (see
    :func:`default_entitlement`).
    """

    def __init__(self, *, store=None, trust_scorer=None, entitlement=None):
        self.store = store if store is not None else MemoryStore()
        self._trust_scorer = trust_scorer or self._default_trust_scorer
        self._entitlement = entitlement or default_entitlement
        self._entries: dict[str, RegistryEntry] = {}

    # -- index ---------------------------------------------------------
    def list_entries(
        self, *, origin: str | None = None, status: str | None = None
    ) -> list[RegistryEntry]:
        entries = list(self._entries.values())
        if origin is not None:
            entries = [e for e in entries if e.origin == origin]
        if status is not None:
            entries = [e for e in entries if e.status == status]
        return sorted(entries, key=lambda e: e.skill_id)

    def get_entry(self, skill_id: str) -> RegistryEntry | None:
        return self._entries.get(skill_id)

    def index_bytes(self) -> int:
        """Serialised size of the index (enforces the 4,000-byte cap)."""
        index = [e.index_entry() for e in self.list_entries()]
        return len(json.dumps(index, ensure_ascii=False).encode("utf-8"))

    def _check_limits(self, new_entry: RegistryEntry) -> None:
        if new_entry.skill_id not in self._entries and len(self._entries) >= MAX_SKILLS:
            raise MarketplaceError(f"marketplace index is full ({MAX_SKILLS} skills)")
        staged = sum(1 for e in self._entries.values() if e.staged)
        if new_entry.staged and new_entry.skill_id not in self._entries and staged >= MAX_STAGED:
            raise MarketplaceError(f"at most {MAX_STAGED} staged skills")
        probe = [e.index_entry() for e in self.list_entries()]
        if new_entry.skill_id not in self._entries:
            probe.append(new_entry.index_entry())
        size = len(json.dumps(probe, ensure_ascii=False).encode("utf-8"))
        if size > MAX_INDEX_BYTES:
            raise MarketplaceError(f"marketplace index would exceed {MAX_INDEX_BYTES} bytes")

    # -- import --------------------------------------------------------
    def import_skill(
        self, folder_path, *, origin: str | None = None, source: str | None = None
    ) -> RegistryEntry:
        """Import one skill folder. Lands DISABLED; only SKILL.md is
        taken in (other files are hashed, never executed).

        ``origin`` overrides the manifest's origin when given;
        otherwise the manifest's own origin (``builtin`` / ``user`` /
        ``library``) is kept. Raises :class:`ManifestError` when the
        folder or manifest is invalid, :class:`MarketplaceError`
        when a limit is hit.
        """
        manifest, problems, frontmatter, _body = validate_skill_folder(folder_path, origin=origin)
        if problems:
            raise MarketplaceError("skill folder failed validation: " + "; ".join(problems))
        if source is not None:
            data = manifest.to_dict()
            data["source"] = source
            manifest = SkillManifest(data)
        skill_md = open_text(folder_path)
        entry = RegistryEntry(
            manifest=manifest,
            origin=manifest.origin,
            status=STATUS_DISABLED,
            staged=True,
            skill_text=skill_md,
            frontmatter=frontmatter,
        )
        self._check_limits(entry)
        self._entries[entry.skill_id] = entry
        self.store.save_skill_text(entry.skill_id, skill_md)
        return entry

    def trust_score(self, skill_id: str) -> TrustScore:
        """Trust score for a registry entry (via the injected scorer)."""
        entry = self._entries.get(skill_id)
        if entry is None:
            raise MarketplaceError(f"skill {skill_id!r} is not in the registry")
        return self._trust_scorer(entry.manifest.to_dict(), skill_id)

    @staticmethod
    def _default_trust_scorer(manifest: dict, skill_id: str) -> TrustScore:
        # No signature check wired in: score from the manifest alone.
        # Library skills from unknown sources land below the floor,
        # so they cannot install without explicit approval.
        return score_module(manifest=manifest, signature_ok=None)

    # -- install / rollback / uninstall --------------------------------
    def install(self, skill_id: str, *, user_id: str, explicit_approval: bool = False) -> dict:
        """Install (enable) a skill.

        Checks, in order: the skill is registered, the trust floor
        (or explicit user approval), the entitlement seam. The
        install — version, sha256, source, licence, price — is
        recorded in the store and in the audit trail.
        """
        entry = self._entries.get(skill_id)
        if entry is None:
            raise MarketplaceError(f"skill {skill_id!r} is not in the registry")
        score = self.trust_score(skill_id)
        try:
            require_trust(score, explicit_approval=explicit_approval, skill_id=skill_id)
        except TrustError:
            self._audit(
                "install_refused_trust",
                entry,
                user_id,
                {"trust_score": score.total, "minimum": MIN_TRUST_SCORE},
            )
            raise
        if not self._entitlement(user_id, entry.manifest):
            self._audit(
                "install_refused_entitlement",
                entry,
                user_id,
                {"price_credits": entry.manifest.price_credits},
            )
            raise EntitlementError(
                f"skill {skill_id!r} costs {entry.manifest.price_credits} "
                "credits and no entitlement record exists for this user"
            )
        record = {
            "skill_id": skill_id,
            "user_id": user_id,
            "version": entry.manifest.version,
            "sha256": entry.manifest.get("sha256", ""),
            "source": entry.manifest.get("source", ""),
            "license": entry.manifest.license,
            "price_credits": entry.manifest.price_credits,
            "status": STATUS_ENABLED,
        }
        self.store.record_version({**record, "manifest": entry.manifest.to_dict()})
        installed = self.store.record_install(record)
        entry.status = STATUS_ENABLED
        entry.staged = False
        self._audit(
            "installed",
            entry,
            user_id,
            {"version": entry.manifest.version, "trust_score": score.total},
        )
        return installed

    def rollback(self, skill_id: str, *, user_id: str, to_version: str | None = None) -> dict:
        """Restore the previously installed version.

        ``to_version`` names the version to restore; when omitted,
        the newest version older than the current install is used.
        The restored version is recorded as a NEW install row (the
        old version rows are never rewritten), with the restored
        version's own sha256 + source, so the audit trail shows
        exactly what was restored.
        """
        entry = self._entries.get(skill_id)
        if entry is None:
            raise MarketplaceError(f"skill {skill_id!r} is not in the registry")
        current = self.store.get_install(skill_id, user_id)
        if current is None:
            raise MarketplaceError(f"skill {skill_id!r} is not installed for this user")
        versions = self.store.list_versions(skill_id, user_id)
        candidates = [v for v in versions if v["version"] != current["version"]]
        if to_version is not None:
            target = next((v for v in candidates if v["version"] == to_version), None)
            if target is None:
                raise MarketplaceError(
                    f"version {to_version!r} of skill {skill_id!r} "
                    "was never installed for this user"
                )
        elif not candidates:
            raise MarketplaceError(f"skill {skill_id!r} has no earlier version to roll back to")
        else:
            target = candidates[-1]
        record = {
            "skill_id": skill_id,
            "user_id": user_id,
            "version": target["version"],
            "sha256": target["sha256"],
            "source": target["source"],
            "license": target.get("license", ""),
            "price_credits": target.get("price_credits", 0),
            "status": STATUS_ENABLED,
        }
        self.store.record_install(record)
        entry.status = STATUS_ENABLED
        self._audit(
            "rolled_back",
            entry,
            user_id,
            {
                "from_version": current["version"],
                "to_version": target["version"],
                "sha256": target["sha256"],
            },
        )
        return record

    def uninstall(self, skill_id: str, *, user_id: str) -> dict:
        """Remove an install. The install row is marked removed and
        the audit trail keeps the full history."""
        entry = self._entries.get(skill_id)
        if entry is None:
            raise MarketplaceError(f"skill {skill_id!r} is not in the registry")
        current = self.store.get_install(skill_id, user_id)
        if current is None:
            raise MarketplaceError(f"skill {skill_id!r} is not installed for this user")
        record = dict(current)
        record["status"] = STATUS_REMOVED
        self.store.record_install(record)
        entry.status = STATUS_DISABLED
        self._audit("uninstalled", entry, user_id, {"version": current["version"]})
        return record

    # -- audit ----------------------------------------------------------
    def audit_log(self, *, skill_id: str | None = None, user_id: str | None = None) -> list[dict]:
        return self.store.list_audit(skill_id=skill_id, user_id=user_id)

    def _audit(
        self, event: str, entry: RegistryEntry, user_id: str, detail: dict | None = None
    ) -> dict:
        return self.store.record_audit(
            {
                "event": event,
                "skill_id": entry.skill_id,
                "user_id": user_id,
                "sha256": entry.manifest.get("sha256", ""),
                "source": entry.manifest.get("source", ""),
                "license": entry.manifest.license,
                "price_credits": entry.manifest.price_credits,
                "detail": detail or {},
            }
        )


def open_text(folder_path) -> str:
    """Read a skill folder's SKILL.md text (import takes this file)."""
    from pathlib import Path

    return (Path(folder_path) / "SKILL.md").read_text(encoding="utf-8")
