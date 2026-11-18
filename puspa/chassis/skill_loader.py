"""Pemuat skill nanobot dengan verifikasi tanda tangan Puspa (definisi 2.6).

Nanobot memuat skill apa adanya (SKILL.md + frontmatter YAML); tidak ada
pemeriksaan signature di mana pun di jalur skill — workspace dan ClawHub
sama-sama tidak tepercaya. ``PuspaSkillsLoader`` menutupnya dari luar
dengan subclassing ``SkillsLoader`` dan meng-override ``_load_skill``
(satu-satunya pintu isi skill ke prompt — dipakai ``load_skill``,
``load_skills_for_context``, dan ``get_skill_metadata``):

- Skill dari sumber ``workspace`` atau ``plugin`` WAJIB punya
  ``manifest.json`` Puspa yang lolos ``verify_skill`` (Ed25519, kunci
  publik Puspa). Gagal verifikasi -> kembalikan ``None`` (skill tidak
  ada, mengikuti semantik nanobot "skill gagal dimuat dilewati diam-diam").
  Tanpa ``public_key`` yang dipasang, semua skill workspace/plugin
  ditolak (fail-closed).
- Skill dari sumber ``builtin`` (yang ikut paket nanobot) dipercaya
  sebagai bagian chassis — tidak perlu tanda tangan Puspa. Kepercayaan
  ini dicatat eksplisit: kalau satu nama skill ada di workspace dan di
  builtin, workspace menang (aturan nanobot) — jadi skill workspace
  yang menyamar sebagai builtin tetap harus lolos verifikasi.

Skill Puspa = direktori dengan ``SKILL.md`` (frontmatter agentskills
untuk nanobot) + ``manifest.json`` Puspa (``{name, version, tools[],
domains[], konektor{}, author, signature}``, ditandatangani dengan
``puspa.policy.skill_signing.sign_skill``). Isi yang lolos verifikasi lalu
mengalir ke prompt seperti skill nanobot biasa.
"""

from __future__ import annotations

from pathlib import Path

from nanobot.agent.skills import SkillsLoader

from puspa.policy.skill_signing import verify_skill

__all__ = ["PuspaSkillsLoader"]

#: Sumber skill yang wajib terverifikasi tanda tangan Puspa.
_SIGNED_SOURCES = frozenset({"workspace", "plugin"})


class PuspaSkillsLoader(SkillsLoader):
    """SkillsLoader nanobot + verifikasi signature untuk skill luar."""

    def __init__(
        self,
        workspace,
        *,
        public_key=None,
        builtin_skills_dir=None,
        disabled_skills=None,
        transparency_log=None,
        expected_identity=None,
    ) -> None:
        super().__init__(
            workspace,
            builtin_skills_dir=builtin_skills_dir,
            disabled_skills=disabled_skills,
        )
        self._puspa_public_key = public_key
        self._transparency_log = transparency_log
        self._expected_identity = expected_identity

    def _resolve_entry(self, name: str) -> dict | None:
        """Entri skill (``{name, path, source}``) untuk nama/alias ini."""
        skills = self.list_skills(filter_unavailable=False)
        available = {entry["name"] for entry in skills}
        resolved = name if name in available else self._skill_aliases().get(name, name)
        return next((s for s in skills if s["name"] == resolved), None)

    def _load_skill(self, name: str, *, for_context: bool = False):
        entry = self._resolve_entry(name)
        if entry is None:
            return None
        if entry.get("source") in _SIGNED_SOURCES:
            if self._puspa_public_key is None:
                return None
            skill_dir = Path(entry["path"]).parent
            if not verify_skill(
                skill_dir,
                self._puspa_public_key,
                self._transparency_log,
                self._expected_identity,
            ):
                return None
        return super()._load_skill(name, for_context=for_context)
