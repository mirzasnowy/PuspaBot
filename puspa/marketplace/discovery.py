"""Skill discovery through the skills.sh registry (G30).

Pattern rewritten from vercel-labs/skills (MIT):
``skills/find-skills/SKILL.md`` + ``src/find.ts``. Nothing was
copied verbatim; the mechanism below is Puspa's own Python
implementation of the same ideas:

- **Search the registry API first.** ``GET {base}/api/search?q=...``
  returns ``{"skills": [{"id", "name", "installs", "source"}]}``.
  A failed request or a malformed response yields no results,
  never an exception: discovery is a best-effort lookup, and
  the local cache covers the registry being down.
- **Check the leaderboard before searching.** The registry
  ranks by installs; a skill with 1K+ installs from a trusted
  source is the default recommendation. The client exposes
  :func:`rank_results` for that; callers that already know the
  domain can pass a known-good source and skip the search.
- **Word-drop fallback.** The API requires every query term to
  match, so one extra word can hide a relevant skill. When the
  exact search returns fewer than 10 results and the query has
  2-6 terms, retry with each term dropped once and merge the
  partial matches after the exact ones (at most 25 total),
  ranked by how many subqueries matched, then best rank, then
  installs.
- **Verify quality before recommending.** Install count first
  (prefer 1K+, cautious under 100), then source reputation
  (official owners are trusted), then repository stars (a repo
  under 100 stars is treated with suspicion). The API returns
  no star counts, so stars are an optional caller-supplied
  signal (``stars_by_slug``); an unknown star count is
  reported as unknown, never assumed good.
- **Present options, install only with approval.** Discovery
  returns ranked candidates; :func:`install_discovered` turns
  a chosen candidate into a G3 marketplace install — SKILL.md
  fetched from the source repo, sha256-checked against the
  hash the user approved, licence checked against the
  allowlist, imported DISABLED through the registry, then
  enabled only when the user approved this exact skill. No
  auto-install, ever.

The Node CLI (``npx skills``) is out of scope: Puspa installs
through its own registry, not through a package manager.

Approval wiring (G4): a skill install is a local enablement,
not an outbound action, so it never goes through the outbound
Gate (whose kinds cover send / create / pay / delete / deploy).
It goes through the G4 taxonomy instead: ``install`` is a
first-class action kind (``puspa.policy.approvals``) that always
asks, with tiered answers (once / conversation / always) in the
``ApprovalStore`` and 24-hour expiry. The caller asks the user
through ``ApprovalPolicy.decide`` and records the answer with
content ``{"skill_slug": ..., "sha256": ...}``;
:func:`approval_from_store` builds the per-skill evidence
(:class:`InstallApproval`) from the stored answer right before
the install, and :func:`consume_install_approval` spends a
"once" answer after a successful install. ``install_discovered``
still enforces the evidence itself (slug + hash binding, no
approval no install), and the registry's own
``explicit_approval`` flag stays the last gate. Build the
evidence fresh from the store for every install; never cache
an ``InstallApproval`` across installs, or a spent "once"
answer would still look valid.
"""

from __future__ import annotations

import json
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

from .errors import (
    ApprovalRequiredError,
    DiscoveryError,
    HashMismatchError,
    LicenseNotAllowedError,
    MarketplaceError,
)
from .manifest import SKILL_ID_PATTERN, sha256_bytes

if TYPE_CHECKING:
    from puspa.policy.approvals import ApprovalStore

__all__ = [
    "DEFAULT_CACHE_TTL_SECONDS",
    "INSTALL_LICENSE_ALLOWLIST",
    "MAX_FALLBACK_TERMS",
    "MAX_MERGED_RESULTS",
    "MIN_EXACT_RESULTS",
    "RECOMMENDED_MIN_INSTALLS",
    "SEARCH_API_BASE",
    "SUSPECT_MAX_INSTALLS",
    "SUSPECT_MAX_STARS",
    "TRUSTED_SOURCE_OWNERS",
    "DiscoveredSkill",
    "DiscoveryCache",
    "InstallApproval",
    "SearchOutcome",
    "SkillsShClient",
    "approval_from_store",
    "assess_quality",
    "consume_install_approval",
    "detect_license",
    "format_installs",
    "format_options",
    "install_discovered",
    "rank_results",
    "search_skills",
    "search_with_cache",
]

#: Default registry base URL (override with ``SKILLS_API_URL``-style
#: injection through the client constructor, never an env read here).
SEARCH_API_BASE = "https://skills.sh"

#: Exact results below this count trigger the word-drop fallback.
MIN_EXACT_RESULTS = 10
#: Fallback only runs for queries with at most this many terms.
MAX_FALLBACK_TERMS = 6
#: Total results after merging exact + fallback matches.
MAX_MERGED_RESULTS = 25

#: Install count that marks a skill as battle-tested.
RECOMMENDED_MIN_INSTALLS = 1000
#: Install count below which a skill is treated with suspicion.
SUSPECT_MAX_INSTALLS = 100
#: Repository stars below which a skill is treated with suspicion.
SUSPECT_MAX_STARS = 100

#: Owners whose skills are trusted outright (official sources).
TRUSTED_SOURCE_OWNERS = frozenset({"vercel-labs", "anthropics", "microsoft"})

#: Licences a discovered skill may install under. Anything else —
#: including an undetectable licence — refuses the install.
INSTALL_LICENSE_ALLOWLIST = frozenset({"MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause"})

#: Default cache lifetime for discovery results (24 hours).
DEFAULT_CACHE_TTL_SECONDS = 24 * 3600

#: Raw GitHub paths tried for a skill's SKILL.md, in order.
#: ``{owner}`` / ``{repo}`` come from the result's ``source``
#: (``owner/repo``); ``{slug}`` is the skill id. The registry
#: does not publish file paths, so both conventional layouts
#: are tried on both default branches.
_SKILL_TEXT_PATHS = (
    "skills/{slug}/SKILL.md",
    "{slug}/SKILL.md",
)
_BRANCHES = ("main", "master")

#: Licence file names tried on the source repo, in order.
_LICENSE_FILES = ("LICENSE", "LICENSE.md", "LICENSE.txt")

#: Quality verdicts, best first.
QUALITY_RECOMMENDED = "recommended"
QUALITY_CAUTION = "caution"
QUALITY_SUSPECT = "suspect"
_QUALITY_ORDER = {
    QUALITY_RECOMMENDED: 0,
    QUALITY_CAUTION: 1,
    QUALITY_SUSPECT: 2,
}


def _clean_text(value, *, limit: int = 200) -> str:
    """Strip control characters and trim to ``limit`` chars.

    Registry metadata is untrusted input: it is rendered to the
    user and stored in the manifest, so control characters are
    dropped and over-long values are cut.
    """
    text = "".join(ch for ch in str(value or "") if ch.isprintable() or ch in (" ", "\t"))
    text = " ".join(text.split())
    return text[:limit]


def _coerce_installs(value) -> int:
    try:
        count = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, count)


def format_installs(count: int) -> str:
    """Human-readable install count (``185K installs``)."""
    if not count or count <= 0:
        return ""
    if count >= 1_000_000:
        shown = f"{count / 1_000_000:.1f}".rstrip("0").rstrip(".")
        return f"{shown}M installs"
    if count >= 1_000:
        shown = f"{count / 1_000:.1f}".rstrip("0").rstrip(".")
        return f"{shown}K installs"
    return f"{count} install{'s' if count != 1 else ''}"


def _owner_of(source: str) -> str:
    """GitHub owner from a ``owner/repo`` source string."""
    return source.split("/", maxsplit=1)[0].strip().lower() if "/" in source else ""


@dataclass
class DiscoveredSkill:
    """One skill found in the registry."""

    slug: str
    name: str
    source: str
    installs: int = 0
    stars: int | None = None
    exact: bool = True
    quality: str = QUALITY_CAUTION
    reasons: list[str] = field(default_factory=list)

    @property
    def detail_url(self) -> str:
        """Registry page for this skill."""
        if self.source and "/" in self.source:
            return f"https://skills.sh/{self.source}/{self.slug}"
        return "https://skills.sh"

    def to_dict(self) -> dict:
        return {
            "slug": self.slug,
            "name": self.name,
            "source": self.source,
            "installs": self.installs,
            "stars": self.stars,
            "exact": self.exact,
            "quality": self.quality,
            "reasons": list(self.reasons),
        }

    @classmethod
    def from_dict(cls, data: dict) -> DiscoveredSkill:
        return cls(
            slug=str(data.get("slug", "")),
            name=str(data.get("name", "")),
            source=str(data.get("source", "")),
            installs=_coerce_installs(data.get("installs")),
            stars=data.get("stars"),
            exact=bool(data.get("exact", True)),
            quality=str(data.get("quality", QUALITY_CAUTION)),
            reasons=list(data.get("reasons") or []),
        )


@dataclass
class SearchOutcome:
    """Result of one discovery search."""

    skills: list[DiscoveredSkill]
    exact_count: int
    query: str = ""
    from_cache: bool = False


def assess_quality(skill: DiscoveredSkill, *, stars: int | None = None) -> DiscoveredSkill:
    """Attach the quality verdict to one result (returns a copy).

    Verdicts:

    - ``suspect``: under 100 installs, or a known repo star
      count under 100. Never recommended on this evidence.
    - ``recommended``: 1K+ installs AND a trusted official
      source, or 1K+ installs AND a known star count of 100+.
    - ``caution``: everything else — usable, but the user
      should look before installing.

    An unknown star count is reported as unknown; it never
    counts for or against the skill.
    """
    reasons: list[str] = []
    known_stars = stars if stars is not None else skill.stars
    trusted = _owner_of(skill.source) in TRUSTED_SOURCE_OWNERS
    if trusted:
        reasons.append(f"official source ({_owner_of(skill.source)})")
    if skill.installs >= RECOMMENDED_MIN_INSTALLS:
        reasons.append(f"{format_installs(skill.installs)}")
    elif skill.installs > 0:
        reasons.append(f"only {format_installs(skill.installs)}")
    else:
        reasons.append("no install count reported")
    if known_stars is None:
        reasons.append("repository stars unknown")
    elif known_stars < SUSPECT_MAX_STARS:
        reasons.append(f"repository has only {known_stars} stars")
    else:
        reasons.append(f"repository has {known_stars} stars")

    if skill.installs < SUSPECT_MAX_INSTALLS or (
        known_stars is not None and known_stars < SUSPECT_MAX_STARS
    ):
        verdict = QUALITY_SUSPECT
    elif skill.installs >= RECOMMENDED_MIN_INSTALLS and (
        trusted or (known_stars is not None and known_stars >= SUSPECT_MAX_STARS)
    ):
        verdict = QUALITY_RECOMMENDED
    else:
        verdict = QUALITY_CAUTION
    return DiscoveredSkill(
        slug=skill.slug,
        name=skill.name,
        source=skill.source,
        installs=skill.installs,
        stars=known_stars,
        exact=skill.exact,
        quality=verdict,
        reasons=reasons,
    )


def rank_results(
    skills: list[DiscoveredSkill],
    *,
    stars_by_slug: dict[str, int] | None = None,
) -> list[DiscoveredSkill]:
    """Attach quality verdicts and order results for display.

    Exact matches come before fallback matches; within each
    group, recommended first, then caution, then suspect; ties
    break by install count, highest first.
    """
    stars_by_slug = stars_by_slug or {}
    assessed = [
        assess_quality(skill, stars=stars_by_slug.get(skill.slug, skill.stars)) for skill in skills
    ]
    return sorted(
        assessed,
        key=lambda s: (
            not s.exact,
            _QUALITY_ORDER[s.quality],
            -s.installs,
            s.slug,
        ),
    )


def format_options(skills: list[DiscoveredSkill]) -> str:
    """Render ranked candidates as numbered choices for the user."""
    if not skills:
        return "No matching skills found."
    lines = []
    for index, skill in enumerate(skills, start=1):
        installs = format_installs(skill.installs)
        installs = f" ({installs})" if installs else ""
        lines.append(
            f"{index}. {skill.name} — {skill.source}{installs} "
            f"[{skill.quality}] — {skill.detail_url}"
        )
        if skill.reasons:
            lines.append(f"   {'; '.join(skill.reasons)}")
    return "\n".join(lines)


class SkillsShClient:
    """Thin client for the skills.sh search API.

    ``client`` is an injected :class:`httpx.Client` (tests pass
    one backed by a ``MockTransport``); otherwise a short-lived
    client is built per search. Any transport failure, non-200
    status, or malformed payload yields an empty list — the
    caller falls back to the local cache.
    """

    def __init__(
        self,
        *,
        base_url: str = SEARCH_API_BASE,
        client: httpx.Client | None = None,
        timeout: float = 10.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = client
        self.timeout = timeout

    def search(self, query: str, *, owner: str | None = None) -> list[DiscoveredSkill]:
        """Search the registry for ``query`` (exact match only).

        Returns raw results in registry order; use
        :func:`search_skills` for the word-drop fallback and
        :func:`rank_results` for the quality ordering.
        """
        params = {"q": query}
        if owner:
            params["owner"] = owner
        try:
            if self._client is not None:
                response = self._client.get(
                    f"{self.base_url}/api/search", params=params, timeout=self.timeout
                )
            else:
                with httpx.Client(timeout=self.timeout) as fresh:
                    response = fresh.get(f"{self.base_url}/api/search", params=params)
        except httpx.HTTPError:
            return []
        if response.status_code != 200:
            return []
        try:
            payload = response.json()
        except ValueError:
            return []
        if not isinstance(payload, dict):
            return []
        raw = payload.get("skills")
        if not isinstance(raw, list):
            return []
        results = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            slug = _clean_text(item.get("id"), limit=120)
            name = _clean_text(item.get("name"), limit=120)
            if not slug or not name:
                continue
            results.append(
                DiscoveredSkill(
                    slug=slug,
                    name=name,
                    source=_clean_text(item.get("source"), limit=120),
                    installs=_coerce_installs(item.get("installs")),
                    exact=True,
                )
            )
        return results


def search_skills(client: SkillsShClient, query: str, *, owner: str | None = None) -> SearchOutcome:
    """Search with the word-drop fallback.

    The registry requires every query term to match, so when an
    exact search returns fewer than 10 results and the query
    has 2-6 terms, each term is dropped once and the partial
    matches are merged after the exact ones (at most 25
    total). Partials rank by how many subqueries matched, then
    best rank in a subquery, then installs. Exact results are
    never displaced by partials.
    """
    exact = client.search(query, owner=owner)
    terms = list(dict.fromkeys(query.split()))
    if len(exact) >= MIN_EXACT_RESULTS or len(terms) < 2 or len(terms) > MAX_FALLBACK_TERMS:
        return SearchOutcome(skills=exact, exact_count=len(exact), query=query)
    subqueries = [
        " ".join(term for j, term in enumerate(terms) if j != i) for i in range(len(terms))
    ]
    partial_lists = [client.search(sub, owner=owner) for sub in subqueries]
    seen = {skill.slug for skill in exact}
    candidates: dict[str, dict] = {}
    for partial in partial_lists:
        for rank, skill in enumerate(partial):
            if skill.slug in seen:
                continue
            entry = candidates.get(skill.slug)
            if entry is None:
                candidates[skill.slug] = {
                    "skill": skill,
                    "hits": 1,
                    "best_rank": rank,
                }
            else:
                entry["hits"] += 1
                entry["best_rank"] = min(entry["best_rank"], rank)
    partials = sorted(
        candidates.values(),
        key=lambda entry: (
            -entry["hits"],
            entry["best_rank"],
            -entry["skill"].installs,
        ),
    )
    room = max(0, MAX_MERGED_RESULTS - len(exact))
    merged = list(exact)
    for entry in partials[:room]:
        skill = entry["skill"]
        merged.append(
            DiscoveredSkill(
                slug=skill.slug,
                name=skill.name,
                source=skill.source,
                installs=skill.installs,
                stars=skill.stars,
                exact=False,
                quality=skill.quality,
                reasons=list(skill.reasons),
            )
        )
    return SearchOutcome(skills=merged, exact_count=len(exact), query=query)


class DiscoveryCache:
    """Local JSON cache of discovery results.

    One file, keyed by normalised query, holding the ranked
    results and the time they were saved. A lookup is fresh
    while it is younger than the TTL; a stale entry is still
    served when the registry cannot be reached, so a search
    never depends fully on skills.sh at runtime. Cache I/O
    failures are ignored (the cache is a convenience, not a
    source of truth).
    """

    def __init__(self, path=None, *, ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS) -> None:
        if path is None:
            path = Path.home() / ".cache" / "puspa" / "skill-discovery.json"
        self.path = Path(path)
        self.ttl_seconds = ttl_seconds

    @staticmethod
    def _normalise(query: str) -> str:
        return " ".join(str(query or "").split()).lower()

    def _read_all(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def lookup(self, query: str, *, allow_stale: bool = False):
        """Cached skills for ``query``, or ``None``.

        Returns ``(skills, age_seconds)``. Fresh entries are
        returned always; stale ones only when ``allow_stale``
        is set (registry unreachable).
        """
        entry = self._read_all().get(self._normalise(query))
        if not isinstance(entry, dict):
            return None
        saved_at = entry.get("saved_at")
        raw_skills = entry.get("skills")
        if not isinstance(saved_at, (int, float)) or not isinstance(raw_skills, list):
            return None
        age = time.time() - float(saved_at)
        if age > self.ttl_seconds and not allow_stale:
            return None
        skills = []
        for item in raw_skills:
            if isinstance(item, dict) and item.get("slug") and item.get("name"):
                skills.append(DiscoveredSkill.from_dict(item))
        return skills, age

    def store(self, query: str, skills: list[DiscoveredSkill]) -> None:
        """Save ranked results for ``query`` (best effort)."""
        try:
            data = self._read_all()
            data[self._normalise(query)] = {
                "saved_at": time.time(),
                "skills": [skill.to_dict() for skill in skills],
            }
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass


def search_with_cache(
    client: SkillsShClient,
    cache: DiscoveryCache,
    query: str,
    *,
    owner: str | None = None,
    stars_by_slug: dict[str, int] | None = None,
) -> SearchOutcome:
    """Search live, falling back to the local cache.

    A live hit is ranked and cached. When the live search
    returns nothing, a fresh cache entry is served; when
    there is no fresh entry, a stale one is served rather
    than an empty result. Only when the cache is empty too
    does the search come back empty.
    """
    outcome = search_skills(client, query, owner=owner)
    if outcome.skills:
        ranked = rank_results(outcome.skills, stars_by_slug=stars_by_slug)
        cache.store(query, ranked)
        return SearchOutcome(
            skills=ranked, exact_count=outcome.exact_count, query=query, from_cache=False
        )
    cached = cache.lookup(query, allow_stale=True)
    if cached is not None:
        skills, _age = cached
        ranked = rank_results(skills, stars_by_slug=stars_by_slug)
        return SearchOutcome(skills=ranked, exact_count=0, query=query, from_cache=True)
    return SearchOutcome(skills=[], exact_count=0, query=query, from_cache=False)


# -- install ------------------------------------------------------------


@dataclass(frozen=True)
class InstallApproval:
    """Explicit user approval for installing one skill.

    The approval binds to the skill slug and, when the hash
    was known at approval time, to the sha256 of the SKILL.md
    the user saw. An approval for another skill — or for
    different content — does not authorise this install.
    """

    approved: bool
    granted_by: str = "user"
    skill_slug: str = ""
    sha256: str = ""
    scope: str = "once"

    @classmethod
    def for_skill(cls, slug: str, sha256: str = "", *, granted_by: str = "user") -> InstallApproval:
        """Approval evidence for one skill (and content hash)."""
        return cls(approved=True, granted_by=granted_by, skill_slug=slug, sha256=sha256)

    def matches(self, slug: str, sha256: str) -> bool:
        """True when this approval covers ``slug`` (and its hash,
        when the approval carries one)."""
        if not self.approved or self.skill_slug != slug:
            return False
        return not self.sha256 or self.sha256 == sha256


def approval_from_store(
    store: ApprovalStore,
    slug: str,
    sha256: str,
    *,
    conversation_id: str | None = None,
) -> InstallApproval | None:
    """Build install approval evidence from a G4 tiered answer.

    Looks up the ``install`` kind in the store for the exact
    content (skill slug + SKILL.md sha256). Returns ``None``
    when no usable answer exists — no answer, a wrong hash, an
    expired answer, or an answer for another conversation —
    so the caller must ask the user first. The evidence is a
    snapshot: build it fresh from the store right before every
    install, never cache it across installs.
    """
    answer = store.find(
        "install",
        conversation_id=conversation_id,
        content={"skill_slug": slug, "sha256": sha256},
    )
    if answer is None:
        return None
    return InstallApproval(
        approved=True,
        granted_by=answer.granted_by,
        skill_slug=slug,
        sha256=sha256,
        scope=answer.scope,
    )


def consume_install_approval(
    store: ApprovalStore,
    slug: str,
    sha256: str,
    *,
    conversation_id: str | None = None,
) -> bool:
    """Spend a "once" install answer after a successful install.

    Mirrors the outbound ``request`` path: only "once" answers
    are consumed (a "conversation" or "always" answer stays
    usable). Returns False when there is no usable "once"
    answer for this exact content.
    """
    answer = store.find(
        "install",
        conversation_id=conversation_id,
        content={"skill_slug": slug, "sha256": sha256},
    )
    if answer is None or answer.scope != "once":
        return False
    return store.consume(answer.answer_id)


def detect_license(license_text: str | None) -> str | None:
    """Identify the licence from a LICENSE file's text.

    Returns one of the allowlisted SPDX ids, or ``None`` when
    the text matches no known licence. Unknown means refused:
    the installer never guesses a licence.
    """
    if not license_text:
        return None
    text = license_text
    if "Apache License" in text and "Version 2.0" in text:
        return "Apache-2.0"
    if "MIT License" in text:
        return "MIT"
    if "Redistribution and use in source and binary forms" in text:
        if "Neither the name" in text:
            return "BSD-3-Clause"
        return "BSD-2-Clause"
    return None


def _raw_github_url(owner: str, repo: str, branch: str, path: str) -> str:
    return f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}"


def _fetch_raw_text(url: str, *, client: httpx.Client | None = None) -> str | None:
    """GET a raw file; ``None`` unless the response is 200."""
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


def _default_fetch_skill_text(source: str, slug: str, *, client: httpx.Client | None = None) -> str:
    """Fetch SKILL.md from the source repo (conventional layouts)."""
    if "/" not in source:
        raise DiscoveryError(f"cannot fetch skill text: source {source!r} is not owner/repo")
    owner, repo = source.split("/", 1)
    for branch in _BRANCHES:
        for pattern in _SKILL_TEXT_PATHS:
            text = _fetch_raw_text(
                _raw_github_url(owner, repo, branch, pattern.format(slug=slug)),
                client=client,
            )
            if text:
                return text
    raise DiscoveryError(
        f"SKILL.md for {slug!r} not found in {source} "
        "(tried skills/<slug>/SKILL.md and <slug>/SKILL.md on main/master)"
    )


def _default_fetch_license_text(
    source: str, slug: str, *, client: httpx.Client | None = None
) -> str | None:
    """Fetch the source repo's LICENSE file, if any."""
    if "/" not in source:
        return None
    owner, repo = source.split("/", 1)
    for branch in _BRANCHES:
        for name in _LICENSE_FILES:
            text = _fetch_raw_text(_raw_github_url(owner, repo, branch, name), client=client)
            if text:
                return text
    return None


def install_discovered(
    registry,
    skill: DiscoveredSkill,
    *,
    user_id: str,
    approval: InstallApproval | None,
    skill_text: str | None = None,
    license_text: str | None = None,
    expected_sha256: str | None = None,
    skills_root=None,
    fetch_skill_text=None,
    fetch_license_text=None,
    http_client: httpx.Client | None = None,
    trigger_terms: list[str] | None = None,
) -> dict:
    """Install a discovered skill through the G3 registry.

    Order of gates (fail-closed, each refusal leaves nothing
    installed):

    1. The slug must be a valid marketplace skill id.
    2. SKILL.md is fetched (or taken from ``skill_text``) and
       its sha256 computed.
    3. When ``expected_sha256`` is given, the computed hash
       must match it — content that changed between approval
       and download is refused.
    4. ``approval`` must be explicit user approval for this
       slug (and hash, when the approval carries one). No
       approval, no install.
    5. The licence must be detected from the source repo's
       LICENSE file and be in the allowlist (MIT / Apache-2.0
       / BSD). Undetectable licence means refused.
    6. The skill is imported DISABLED through the registry
       (manifest validated, sha256 + source + licence
       recorded), then enabled by ``registry.install`` with
       ``explicit_approval=True`` — the registry's own trust
       floor is below the pass mark for an unsigned library
       skill, so the user's explicit approval is what lets it
       through, exactly as G3 designed it.

    ``skills_root`` is where the imported skill folder lives
    (so the G24 curator can see it afterwards); without one,
    a temporary staging folder is used and the registry's
    stored copy is the only record. ``fetch_skill_text`` /
    ``fetch_license_text`` are injectable ``(source, slug) ->
    str`` callables (tests pass fakes; the defaults hit
    raw.githubusercontent.com, with ``http_client`` injected
    for tests that exercise the default fetchers).
    """
    slug = skill.slug
    if not SKILL_ID_PATTERN.match(slug):
        raise MarketplaceError(f"discovered skill id {slug!r} is not a valid skill id")
    if fetch_skill_text is not None:
        fetcher = fetch_skill_text
    else:

        def fetcher(source: str, slug: str) -> str:
            return _default_fetch_skill_text(source, slug, client=http_client)

    text = skill_text if skill_text is not None else fetcher(skill.source, slug)
    if not text:
        raise DiscoveryError(f"empty SKILL.md for skill {slug!r}")
    digest = sha256_bytes(text.encode("utf-8"))
    if expected_sha256 is not None and digest != expected_sha256:
        raise HashMismatchError(
            f"skill {slug!r} SKILL.md sha256 {digest[:12]}... does not match "
            f"the approved hash {expected_sha256[:12]}..."
        )
    if approval is None or not approval.matches(slug, digest):
        raise ApprovalRequiredError(
            f"installing skill {slug!r} requires explicit user approval "
            "for this skill (and content hash)"
        )
    license_fetcher = fetch_license_text
    if license_fetcher is None:

        def license_fetcher(source: str, slug: str) -> str | None:
            return _default_fetch_license_text(source, slug, client=http_client)

    raw_license = license_text if license_text is not None else license_fetcher(skill.source, slug)
    detected = detect_license(raw_license)
    if detected is None:
        raise LicenseNotAllowedError(
            f"skill {slug!r} has no detectable licence in its source repo; "
            "refusing install (licence must be MIT, Apache-2.0, or BSD)"
        )
    if detected not in INSTALL_LICENSE_ALLOWLIST:
        raise LicenseNotAllowedError(
            f"skill {slug!r} licence {detected!r} is outside the install allowlist"
        )

    terms = list(trigger_terms) if trigger_terms else [word for word in slug.split("-") if word]
    description = (
        f"{skill.name} (discovered via skills.sh from {skill.source}). "
        f"{format_installs(skill.installs)}"
    ).strip()
    manifest = {
        "id": slug,
        "version": "1.0.0",
        "name": skill.name,
        "description": description,
        "triggerTerms": terms,
        "requiredCapabilities": [],
        "tools": [],
        "license": detected,
        "source": f"skills.sh:{skill.source}",
        "priceCredits": 0,
        "origin": "library",
    }
    staging = None
    try:
        if skills_root is not None:
            folder = Path(skills_root) / slug
        else:
            staging = tempfile.TemporaryDirectory(prefix="puspa-discovered-")
            folder = Path(staging.name) / slug
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "SKILL.md").write_text(text, encoding="utf-8")
        (folder / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        registry.import_skill(folder, origin="library", source=f"skills.sh:{skill.source}")
        installed = registry.install(slug, user_id=user_id, explicit_approval=True)
        if skills_root is not None:
            # The new skill enters G24 curation telemetry (active,
            # tracked from install; it only goes stale/archived
            # through the curator, never deleted).
            from ..memory.curator import record_use

            record_use(folder)
        return installed
    finally:
        if staging is not None:
            staging.cleanup()
