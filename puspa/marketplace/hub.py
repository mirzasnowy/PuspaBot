"""Skill marketplace hub: browse, search and one-click install (COW-03).

The browsable hub surface over the LOCAL skill registry. The hub
server (hosted listings, ratings, payments) lives in the private
repo by the D-08 split; everything here runs on the user's own
machine and only ever touches local state plus a plain HTTPS fetch
of the skill the user asked for.

What the hub does (mechanism adapted from zhayujie/CowAgent and
zhayujie/cow-skill-hub, both MIT — rewritten as PuspaBot code,
never copied verbatim; CowAgent's own rule, kept here, is that
every skill is previewed first and only installed after the user
confirms):

- **Browse / search.** :func:`list_hub_skills` merges the skills
  shipped with Puspa (the ``skills/`` folders on disk) with the
  skills already in the local :class:`MarketplaceRegistry`
  (installed library skills). A query matches id, name,
  description and trigger terms; an empty query lists all.
- **Install from GitHub, ClawHub, or a URL.**
  :func:`parse_skill_source` turns what the user pasted into a
  :class:`SkillSource`; :func:`preview_source` fetches the
  skill's ``SKILL.md`` (+ licence) without installing anything,
  so the screen can show name, licence and sha256 first;
  :func:`install_from_source` installs only after the user
  confirms that exact preview (the confirmed sha256 is bound
  into the install, exactly like the skills.sh path in
  :mod:`puspa.marketplace.discovery`).

Install gates (fail-closed, in order — same order as
``install_discovered``):

0. The ``marketplace`` feature flag must be on.
1. The skill-install flag (``PUSPA_SKILL_INSTALL_ENABLED``)
   must be on: third-party skills are off by default for beta.
2. The skill id must be a valid marketplace id.
3. ``SKILL.md`` is fetched and its sha256 computed; when the
   caller passes ``expected_sha256`` (the preview the user saw)
   the fetched hash must match it.
4. ``approval`` must be explicit user approval for this slug
   (and hash, when the approval carries one). No approval, no
   install. The PWA and the CLI build that approval from the
   user's own confirm action, for the exact content they saw.
5. The licence must be detected from the source repo's LICENSE
   file and be in the allowlist (MIT / Apache-2.0 / BSD).
   Undetectable licence means refused.
6. The skill is imported DISABLED through the registry
   (manifest validated, sha256 + source + licence recorded),
   then enabled by ``registry.install`` with
   ``explicit_approval=True``.

A failed install says so: every refusal raises a
:class:`MarketplaceError` (or subclass) with the reason, and
nothing is left half-installed — the registry import happens
only after every check above has passed.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

from puspa.feature_flags import (
    FLAG_MARKETPLACE,
    FLAG_SKILL_INSTALL,
    is_enabled,
    require_enabled,
)

from .discovery import (
    INSTALL_LICENSE_ALLOWLIST,
    SKILL_INSTALL_ENV_VAR,
    InstallApproval,
    detect_license,
)
from .errors import (
    ApprovalRequiredError,
    DiscoveryError,
    HashMismatchError,
    LicenseNotAllowedError,
    MarketplaceError,
)
from .manifest import (
    SKILL_ID_PATTERN,
    parse_frontmatter,
    sha256_bytes,
    validate_manifest,
)

if TYPE_CHECKING:
    from .registry import MarketplaceRegistry

__all__ = [
    "BUILTIN_SKILLS_DIR",
    "CLAW_HUB_HOSTS",
    "FetchedSkill",
    "HubSkill",
    "SkillSource",
    "install_from_source",
    "list_builtin_skills",
    "list_hub_skills",
    "parse_skill_source",
    "preview_source",
    "search_local",
    "slugify_skill_name",
]

#: Default folder holding the skills shipped with Puspa.
BUILTIN_SKILLS_DIR = Path(__file__).resolve().parent.parent.parent / "skills"

#: Hosts that serve ClawHub skill pages.
CLAW_HUB_HOSTS = frozenset({"clawhub.ai", "clawhub.com", "www.clawhub.ai"})

#: Raw GitHub paths tried for a skill's SKILL.md, in order, when
#: the source names a skill folder by slug alone. The explicit
#: folder path (when the user gave one) is tried first.
_SKILL_TEXT_PATHS = (
    "skills/{slug}/SKILL.md",
    "{slug}/SKILL.md",
    "SKILL.md",
)
_BRANCHES = ("main", "master")

#: Licence file names tried on the source repo, in order.
_LICENSE_FILES = ("LICENSE", "LICENSE.md", "LICENSE.txt")

#: A github.com link inside a ClawHub skill page (the page is the
#: registry listing; the repo is where the skill text lives).
_GITHUB_LINK_RE = re.compile(r"github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)")

#: A direct SKILL.md URL (the URL source kind).
_SKILL_MD_URL_RE = re.compile(r"^https?://.+/SKILL\.md$", re.IGNORECASE)


def slugify_skill_name(name: str) -> str:
    """Turn a skill name into a marketplace id (``My Skill`` ->
    ``my-skill``). Returns ``""`` when nothing usable remains."""
    slug = re.sub(r"[^a-z0-9]+", "-", str(name or "").lower()).strip("-")
    return re.sub(r"-+", "-", slug)


@dataclass
class SkillSource:
    """One installable skill source, parsed from user input.

    ``kind`` is ``github`` (``owner/repo`` plus an optional folder
    path inside the repo), ``clawhub`` (``owner/slug`` on ClawHub)
    or ``url`` (a direct ``SKILL.md`` URL). ``slug_hint`` is the
    skill id to look for when the source does not name a folder
    (the repo name for a one-skill repo, the ClawHub slug).
    """

    kind: str
    owner: str = ""
    repo: str = ""
    path: str = ""
    slug_hint: str = ""
    url: str = ""

    @property
    def label(self) -> str:
        """Short source label stored on the installed manifest."""
        if self.kind == "github":
            label = f"github:{self.owner}/{self.repo}"
            return f"{label}/{self.path}" if self.path else label
        if self.kind == "clawhub":
            return f"clawhub:{self.owner}/{self.slug_hint}"
        return f"url:{self.url}"

    @property
    def github_slug(self) -> str:
        """The repo itself is one skill (``owner/repo`` with no
        folder path): the skill id is the repo name."""
        return slugify_skill_name(self.repo)


def parse_skill_source(spec: str) -> SkillSource:
    """Parse what the user pasted into a :class:`SkillSource`.

    Accepted shapes (anything else raises :class:`DiscoveryError`
    with the reason, never a guessed source):

    - GitHub: ``owner/repo``, ``owner/repo/path/to/skill``,
      ``github:owner/repo[/path]``, a ``github.com`` repo URL, a
      ``github.com/.../tree/<branch>/<path>`` URL, or a
      ``raw.githubusercontent.com`` ``SKILL.md`` URL.
    - ClawHub: ``clawhub:owner/slug`` or a ``clawhub.ai`` /
      ``clawhub.com`` skill-page URL.
    - URL: a direct ``https://.../SKILL.md`` URL.
    """
    raw = str(spec or "").strip()
    if not raw:
        raise DiscoveryError("give a skill source: owner/repo, a ClawHub skill, or a SKILL.md URL")
    lowered = raw.lower()

    # -- direct SKILL.md URL (raw.githubusercontent.com included) --
    if _SKILL_MD_URL_RE.match(raw):
        if "raw.githubusercontent.com" in lowered:
            parts = raw.split("/")
            # https://raw.githubusercontent.com/<owner>/<repo>/<branch>/<path>/SKILL.md
            if len(parts) >= 7:
                owner, repo = parts[3], parts[4]
                path = "/".join(parts[6:-1])
                return SkillSource(
                    kind="github",
                    owner=owner,
                    repo=repo,
                    path=path,
                    slug_hint=path.rsplit("/", 1)[-1] if path else slugify_skill_name(repo),
                    url=raw,
                )
        return SkillSource(kind="url", url=raw)

    # -- github.com / raw.githubusercontent.com page URLs --
    if "github.com" in lowered or "raw.githubusercontent.com" in lowered:
        return _parse_github_url(raw)

    # -- ClawHub page URLs --
    for host in CLAW_HUB_HOSTS:
        if host in lowered:
            return _parse_clawhub_url(raw, host)

    # -- clawhub:owner/slug / github:owner/repo prefixes --
    if lowered.startswith("clawhub:"):
        rest = raw.split(":", 1)[1].strip().strip("/")
        owner, _, slug = rest.partition("/")
        if not owner or not slug or "/" in slug:
            raise DiscoveryError(f"ClawHub source {raw!r} must look like clawhub:owner/skill-name")
        return SkillSource(kind="clawhub", owner=owner, slug_hint=slugify_skill_name(slug))
    if lowered.startswith("github:"):
        raw = raw.split(":", 1)[1].strip()

    # -- bare owner/repo[/path] --
    if "/" in raw and "://" not in raw and not raw.startswith("/"):
        owner, _, rest = raw.partition("/")
        repo, _, path = rest.partition("/")
        owner = owner.strip()
        repo = repo.removesuffix(".git").strip()
        path = path.strip().strip("/")
        if (
            owner
            and repo
            and re.fullmatch(r"[A-Za-z0-9_.-]+", owner)
            and re.fullmatch(r"[A-Za-z0-9_.-]+", repo)
        ):
            return SkillSource(
                kind="github",
                owner=owner,
                repo=repo,
                path=path,
                slug_hint=path.rsplit("/", 1)[-1] if path else slugify_skill_name(repo),
            )
    raise DiscoveryError(
        f"cannot understand skill source {raw!r}: use owner/repo, "
        "a ClawHub skill (clawhub:owner/skill), or a SKILL.md URL"
    )


def _parse_github_url(raw: str) -> SkillSource:
    """Parse a github.com or raw.githubusercontent.com URL."""
    from urllib.parse import unquote, urlparse

    parsed = urlparse(raw)
    segments = [unquote(s) for s in parsed.path.strip("/").split("/") if s]
    if "raw.githubusercontent.com" in parsed.netloc.lower():
        if len(segments) < 4:
            raise DiscoveryError(f"GitHub raw URL {raw!r} has no owner/repo/branch/path")
        owner, repo = segments[0], segments[1]
        path = (
            "/".join(segments[3:-1])
            if segments[-1].lower() == "skill.md"
            else "/".join(segments[3:])
        )
        return SkillSource(
            kind="github",
            owner=owner,
            repo=repo,
            path=path,
            slug_hint=path.rsplit("/", 1)[-1] if path else slugify_skill_name(repo),
            url=raw,
        )
    if len(segments) < 2:
        raise DiscoveryError(f"GitHub URL {raw!r} has no owner/repo")
    owner, repo = segments[0], segments[1].removesuffix(".git")
    path = ""
    if len(segments) > 2 and segments[2] in ("tree", "blob"):
        # /owner/repo/tree/<branch>/<path...> (branch is one segment)
        path = "/".join(segments[4:])
    elif len(segments) > 2:
        path = "/".join(segments[2:])
    return SkillSource(
        kind="github",
        owner=owner,
        repo=repo,
        path=path,
        slug_hint=path.rsplit("/", 1)[-1] if path else slugify_skill_name(repo),
        url=raw,
    )


def _parse_clawhub_url(raw: str, host: str) -> SkillSource:
    """Parse a ClawHub skill-page URL (``clawhub.ai/owner/slug``)."""
    from urllib.parse import unquote, urlparse

    parsed = urlparse(raw)
    segments = [unquote(s) for s in parsed.path.strip("/").split("/") if s]
    if len(segments) < 2:
        raise DiscoveryError(
            f"ClawHub URL {raw!r} has no owner/skill — use {host}/owner/skill-name"
        )
    owner, slug = segments[0], segments[1]
    return SkillSource(kind="clawhub", owner=owner, slug_hint=slugify_skill_name(slug))


def _http_get_text(url: str, *, client: httpx.Client | None = None) -> str | None:
    """GET a URL as text; ``None`` unless the response is 200."""
    try:
        if client is not None:
            response = client.get(url, timeout=10.0)
        else:
            with httpx.Client(timeout=10.0) as fresh:
                response = fresh.get(url)
    except httpx.HTTPError:
        return None
    if response.status_code != 200:
        return None
    return response.text


def _raw_github_url(owner: str, repo: str, branch: str, path: str) -> str:
    return f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}"


def _fetch_github_skill_text(
    owner: str, repo: str, slug: str, path: str, *, client: httpx.Client | None = None
) -> str:
    """Fetch SKILL.md from a GitHub repo (conventional layouts).

    When the user gave an explicit folder path it is tried first;
    otherwise ``skills/<slug>/SKILL.md``, ``<slug>/SKILL.md`` and
    the repo-root ``SKILL.md`` are tried, on ``main`` then
    ``master``.
    """
    candidates: list[str] = []
    if path:
        candidates.append(f"{path}/SKILL.md")
    candidates.extend(pattern.format(slug=slug) for pattern in _SKILL_TEXT_PATHS)
    seen: set[str] = set()
    for branch in _BRANCHES:
        for candidate in candidates:
            if candidate in seen:
                continue
            seen.add(candidate)
            text = _http_get_text(_raw_github_url(owner, repo, branch, candidate), client=client)
            if text:
                return text
    raise DiscoveryError(
        f"SKILL.md not found in {owner}/{repo} "
        "(tried the given folder, skills/<skill>/SKILL.md, <skill>/SKILL.md "
        "and the repo-root SKILL.md on main/master)"
    )


def _fetch_github_license_text(
    owner: str, repo: str, *, client: httpx.Client | None = None
) -> str | None:
    """Fetch the source repo's LICENSE file, if any."""
    for branch in _BRANCHES:
        for name in _LICENSE_FILES:
            text = _http_get_text(_raw_github_url(owner, repo, branch, name), client=client)
            if text:
                return text
    return None


def _resolve_clawhub_repo(
    source: SkillSource, *, client: httpx.Client | None = None
) -> tuple[str, str]:
    """Resolve a ClawHub skill to its GitHub repo.

    The ClawHub page is the registry listing; the skill text
    lives in the GitHub repo the page links to. The first
    github.com link on the page wins; a page with no GitHub
    link cannot be installed — the hub refuses rather than
    guessing where the skill lives.
    """
    page_url = f"https://clawhub.ai/{source.owner}/{source.slug_hint}"
    page = _http_get_text(page_url, client=client)
    if page is None:
        raise DiscoveryError(
            f"ClawHub skill {source.owner}/{source.slug_hint} could not be loaded "
            f"({page_url} did not answer)"
        )
    match = _GITHUB_LINK_RE.search(page)
    if match is None:
        raise DiscoveryError(
            f"ClawHub skill {source.owner}/{source.slug_hint} links to no GitHub "
            "repository, so its SKILL.md cannot be fetched"
        )
    return match.group(1), match.group(2).removesuffix(".git")


@dataclass
class FetchedSkill:
    """A skill fetched from its source, not yet installed."""

    slug: str
    name: str
    description: str
    skill_text: str
    license_text: str | None
    license: str | None
    sha256: str
    source_label: str

    def to_dict(self) -> dict:
        return {
            "slug": self.slug,
            "name": self.name,
            "description": self.description,
            "license": self.license,
            "sha256": self.sha256,
            "source": self.source_label,
        }


def _skill_from_text(text: str, *, slug_hint: str, source_label: str) -> FetchedSkill:
    """Build a preview from fetched SKILL.md text."""
    try:
        frontmatter, _body = parse_frontmatter(text)
    except MarketplaceError as exc:
        raise DiscoveryError(f"SKILL.md has no usable frontmatter: {exc}") from exc
    slug = slugify_skill_name(frontmatter.get("name", "")) or slug_hint
    if not SKILL_ID_PATTERN.match(slug):
        raise DiscoveryError(f"skill id {slug!r} (from {source_label}) is not a valid skill id")
    name = str(frontmatter.get("name") or slug)
    description = str(frontmatter.get("description") or "")
    return FetchedSkill(
        slug=slug,
        name=name,
        description=description,
        skill_text=text,
        license_text=None,
        license=None,
        sha256=sha256_bytes(text.encode("utf-8")),
        source_label=source_label,
    )


def fetch_skill_source(
    source: SkillSource,
    *,
    http_client: httpx.Client | None = None,
    fetch_skill_text=None,
    fetch_license_text=None,
) -> FetchedSkill:
    """Fetch a skill's SKILL.md (+ licence) from its source.

    ``fetch_skill_text`` / ``fetch_license_text`` are injectable
    ``(owner, repo, slug, path) -> str`` / ``(owner, repo) -> str``
    callables (tests pass fakes; the defaults hit
    raw.githubusercontent.com, with ``http_client`` injected for
    tests that exercise the default fetchers).
    """
    if source.kind == "url":
        text = _http_get_text(source.url, client=http_client)
        if not text:
            raise DiscoveryError(f"SKILL.md URL {source.url} did not answer")
        fetched = _skill_from_text(text, slug_hint="skill", source_label=source.label)
        directory = source.url.rsplit("/", 1)[0]
        license_text = None
        for name in _LICENSE_FILES:
            license_text = _http_get_text(f"{directory}/{name}", client=http_client)
            if license_text:
                break
        fetched.license_text = license_text
        fetched.license = detect_license(license_text)
        return fetched

    owner, repo = source.owner, source.repo
    slug_hint = source.slug_hint or source.github_slug
    if source.kind == "clawhub":
        owner, repo = _resolve_clawhub_repo(source, client=http_client)
    if fetch_skill_text is not None:
        text = fetch_skill_text(owner, repo, slug_hint, source.path)
        if not text:
            raise DiscoveryError(f"empty SKILL.md for skill {slug_hint!r}")
    else:
        text = _fetch_github_skill_text(owner, repo, slug_hint, source.path, client=http_client)
    fetched = _skill_from_text(text, slug_hint=slug_hint, source_label=source.label)
    if fetch_license_text is not None:
        license_text = fetch_license_text(owner, repo)
    else:
        license_text = _fetch_github_license_text(owner, repo, client=http_client)
    fetched.license_text = license_text
    fetched.license = detect_license(license_text)
    return fetched


def preview_source(
    spec: str,
    *,
    http_client: httpx.Client | None = None,
    fetch_skill_text=None,
    fetch_license_text=None,
) -> FetchedSkill:
    """Preview a skill source: fetch it, install nothing.

    Returns the slug, name, description, licence and sha256 the
    install confirmation binds to. Raises :class:`DiscoveryError`
    when the source cannot be understood or fetched.
    """
    return fetch_skill_source(
        parse_skill_source(spec),
        http_client=http_client,
        fetch_skill_text=fetch_skill_text,
        fetch_license_text=fetch_license_text,
    )


def install_from_source(
    registry: MarketplaceRegistry,
    spec: str,
    *,
    user_id: str,
    approval: InstallApproval | None,
    expected_sha256: str | None = None,
    skills_root=None,
    http_client: httpx.Client | None = None,
    fetch_skill_text=None,
    fetch_license_text=None,
) -> dict:
    """Install a skill from GitHub, ClawHub, or a URL.

    Gates (see the module docstring): marketplace flag, install
    flag, valid slug, fetched hash (bound to ``expected_sha256``
    when given — content that changed between preview and
    install is refused), explicit approval for this slug and
    hash, allowlisted licence, then import DISABLED + enable
    through the registry. Returns the registry's install record.
    """
    require_enabled(FLAG_MARKETPLACE)
    if not is_enabled(FLAG_SKILL_INSTALL):
        raise DiscoveryError(
            "installing third-party skills is disabled by the feature flag "
            f"({SKILL_INSTALL_ENV_VAR} is not set); for beta only built-in "
            "skills may install"
        )
    source = parse_skill_source(spec)
    fetched = fetch_skill_source(
        source,
        http_client=http_client,
        fetch_skill_text=fetch_skill_text,
        fetch_license_text=fetch_license_text,
    )
    slug = fetched.slug
    if not SKILL_ID_PATTERN.match(slug):
        raise MarketplaceError(f"skill id {slug!r} is not a valid skill id")
    if expected_sha256 is not None and fetched.sha256 != expected_sha256:
        raise HashMismatchError(
            f"skill {slug!r} SKILL.md sha256 {fetched.sha256[:12]}... does not match "
            f"the previewed hash {expected_sha256[:12]}..."
        )
    if approval is None or not approval.matches(slug, fetched.sha256):
        raise ApprovalRequiredError(
            f"installing skill {slug!r} requires explicit user approval "
            "for this skill (and content hash) — preview it first, then confirm"
        )
    if fetched.license is None:
        raise LicenseNotAllowedError(
            f"skill {slug!r} has no detectable licence in its source repo; "
            "refusing install (licence must be MIT, Apache-2.0, or BSD)"
        )
    if fetched.license not in INSTALL_LICENSE_ALLOWLIST:
        raise LicenseNotAllowedError(
            f"skill {slug!r} licence {fetched.license!r} is outside the install allowlist"
        )

    terms = [word for word in slug.split("-") if word]
    description = f"{fetched.name} (installed from {source.label})."
    if fetched.description:
        description = f"{fetched.description} (installed from {source.label})."
    manifest = {
        "id": slug,
        "version": "1.0.0",
        "name": fetched.name,
        "description": description,
        "triggerTerms": terms,
        "requiredCapabilities": [],
        "tools": [],
        "license": fetched.license,
        "source": source.label,
        "priceCredits": 0,
        "origin": "library",
    }
    staging = None
    try:
        if skills_root is not None:
            folder = Path(skills_root) / slug
        else:
            import tempfile

            staging = tempfile.TemporaryDirectory(prefix="puspa-hub-")
            folder = Path(staging.name) / slug
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "SKILL.md").write_text(fetched.skill_text, encoding="utf-8")
        (folder / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        registry.import_skill(folder, origin="library", source=source.label)
        installed = registry.install(slug, user_id=user_id, explicit_approval=True)
        if skills_root is not None:
            from ..memory.curator import record_use

            record_use(folder)
        return installed
    finally:
        if staging is not None:
            staging.cleanup()


# -- browse / search ------------------------------------------------------


@dataclass
class HubSkill:
    """One skill as the hub lists it."""

    skill_id: str
    name: str
    description: str
    version: str
    origin: str
    status: str
    source: str
    license: str
    price_credits: int = 0
    installed: bool = False

    def to_dict(self) -> dict:
        return {
            "id": self.skill_id,
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "origin": self.origin,
            "status": self.status,
            "source": self.source,
            "license": self.license,
            "price_credits": self.price_credits,
            "installed": self.installed,
        }


def list_builtin_skills(skills_dir=None) -> list[HubSkill]:
    """Skills shipped with Puspa (the ``skills/`` folders).

    Reads each folder's ``manifest.json`` (validated, never
    trusted blindly); a folder that fails validation is skipped
    — a broken shipped skill hides itself instead of breaking
    the whole hub listing.
    """
    root = Path(skills_dir) if skills_dir is not None else BUILTIN_SKILLS_DIR
    found: list[HubSkill] = []
    if not root.is_dir():
        return found
    for folder in sorted(root.iterdir()):
        if not folder.is_dir():
            continue
        manifest_path = folder / "manifest.json"
        if not manifest_path.is_file():
            continue
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        manifest, problems, _warnings = validate_manifest(data, folder_name=folder.name)
        if problems or manifest is None:
            continue
        found.append(
            HubSkill(
                skill_id=manifest.skill_id,
                name=str(manifest.data.get("name", manifest.skill_id)),
                description=str(manifest.data.get("description", "")),
                version=manifest.version,
                origin="builtin",
                status="builtin",
                source=str(manifest.data.get("source", "puspa-builtin")),
                license=manifest.license,
                price_credits=manifest.price_credits,
                installed=True,
            )
        )
    return found


def list_hub_skills(
    registry: MarketplaceRegistry,
    *,
    skills_dir=None,
    query: str | None = None,
) -> list[HubSkill]:
    """All locally browsable skills: shipped + registry.

    Registry entries override a shipped skill of the same id
    (an installed library skill replaces its builtin twin in
    the listing). ``query`` filters by id, name, description
    and trigger terms; empty query lists everything, builtin
    first, then library, each by id.
    """
    by_id: dict[str, HubSkill] = {s.skill_id: s for s in list_builtin_skills(skills_dir)}
    for entry in registry.list_entries():
        manifest = entry.manifest
        data = manifest.to_dict()
        by_id[entry.skill_id] = HubSkill(
            skill_id=entry.skill_id,
            name=str(data.get("name", entry.skill_id)),
            description=str(data.get("description", "")),
            version=manifest.version,
            origin=entry.origin,
            status=entry.status,
            source=str(data.get("source", "")),
            license=manifest.license,
            price_credits=manifest.price_credits,
            installed=entry.status == "enabled",
        )
    skills = sorted(by_id.values(), key=lambda s: (s.origin != "builtin", s.skill_id))
    return search_local(skills, query)


def search_local(skills: list[HubSkill], query: str | None) -> list[HubSkill]:
    """Filter hub skills by query (all terms must match)."""
    terms = [term.lower() for term in str(query or "").split() if term]
    if not terms:
        return list(skills)
    kept: list[HubSkill] = []
    for skill in skills:
        haystack = " ".join([skill.skill_id, skill.name, skill.description, skill.source]).lower()
        if all(term in haystack for term in terms):
            kept.append(skill)
    return kept
