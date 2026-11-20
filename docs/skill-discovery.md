# Skill discovery (G30)

Pattern rewritten from vercel-labs/skills (MIT):
`skills/find-skills/SKILL.md` + `src/find.ts` + the skills.sh
registry. Nothing was copied verbatim; the mechanism below is
Puspa's own Python implementation of the same ideas. The Node
CLI (`npx skills`) is out of scope — Puspa installs through its
own marketplace, not through a package manager.

## What it does

When the user asks "how do I do X" (or "find a skill for X",
"is there a skill that can..."), Puspa can discover an
installable skill from the open agent skills ecosystem:

1. **Check the leaderboard first.** The registry ranks by
   installs; a skill with 1K+ installs from a trusted official
   source is the default recommendation.
2. **Search the registry API.** `GET https://skills.sh/api/search?q=...`
   returns `{"skills": [{"id", "name", "installs", "source"}]}`.
   The API requires every query term to match, so when the
   exact search returns fewer than 10 results and the query has
   2-6 terms, the search retries with each term dropped once
   and merges the partial matches after the exact ones (at most
   25 total), ranked by how many subqueries matched, then best
   rank, then installs.
3. **Verify quality before recommending.** Install count first
   (prefer 1K+, cautious under 100), then source reputation
   (official owners `vercel-labs`, `anthropics`, `microsoft`
   are trusted), then repository stars (a repo under 100 stars
   is treated with suspicion). The API returns no star counts,
   so stars are an optional caller-supplied signal; an unknown
   star count is reported as unknown, never assumed good.
4. **Present options.** Ranked candidates are rendered for the
   user with name, source, install count, quality verdict and
   reasons, and a skills.sh link.
5. **Install only with approval.** The chosen skill is fetched
   (SKILL.md + LICENSE from the source repo), hash-checked
   against the hash the user approved, licence-checked against
   the allowlist (MIT / Apache-2.0 / BSD), imported DISABLED
   through the G3 registry, then enabled only when the user
   approved this exact skill. No auto-install, ever.

## Code map

- `puspa/marketplace/discovery.py` — the whole mechanism:
  `SkillsShClient` (search client; any transport failure,
  non-200 status, or malformed payload yields no results),
  `search_skills` (exact + word-drop fallback merge),
  `assess_quality` / `rank_results` (the quality rubric),
  `format_options` (choices for the user), `DiscoveryCache`
  (local JSON cache of results, 24h TTL; a stale entry is
  still served when the registry is unreachable, so a search
  never depends fully on skills.sh at runtime),
  `search_with_cache` (live first, cache fallback),
  `InstallApproval` (per-skill approval evidence, bound to
  the slug and, when known, to the SKILL.md sha256),
  `detect_license` (LICENSE text to SPDX id),
  `install_discovered` (the install flow).
- `skills/find-skills/SKILL.md` + `skills/find-skills/manifest.json`
  — the built-in find-skills skill. It is active when the user
  asks how to do something, and its instructions point at the
  functions above. It lives in the repo-root `skills/` root,
  the same shape the G24 curator scans
  (`puspa.memory.curator.discover_skills`), and it imports
  through the registry with origin `builtin` (disabled by
  default, like every import).
- `puspa/marketplace/errors.py` — `DiscoveryError`,
  `ApprovalRequiredError`, `HashMismatchError`,
  `LicenseNotAllowedError`.

## Install gates (in order, fail-closed)

0. The skills.sh install feature flag must be on
   (`PUSPA_SKILL_INSTALL_ENABLED=1`). Off by default for beta
   (plan 6.7, E-12): only built-in skills and the self-signed
   allowlist may install. Searching the registry is unaffected —
   the flag gates installation only, never discovery.
1. The slug must be a valid marketplace skill id.
2. SKILL.md is fetched (or supplied) and its sha256 computed.
3. When an expected sha256 is given, the computed hash must
   match it — content that changed between approval and
   download is refused.
4. The approval must be explicit user approval for this slug
   (and hash, when the approval carries one). No approval, no
   install.
5. The licence must be detected from the source repo's LICENSE
   file and be in the allowlist (MIT / Apache-2.0 /
   BSD-2-Clause / BSD-3-Clause). An undetectable licence is
   refused.
6. The skill is imported DISABLED through the registry
   (manifest validated, sha256 + source + licence recorded),
   then enabled by `registry.install(..., explicit_approval=True)`.
   A library skill from skills.sh scores below the G3 trust
   floor (unsigned, untrusted source), so the user's explicit
   approval is what lets it through — exactly as G3 designed it.
7. When a `skills_root` is given, the install records the skill
   with the G24 curator (`puspa.memory.curator.record_use`), so
   its usage is tracked and it goes stale, then archived, if
   nobody uses it — never deleted.

## Approval wiring (G4)

A skill install is a local enablement, not an outbound action,
so it never goes through the outbound Gate (whose kinds cover
send / create / pay / delete / deploy). It goes through the
G4 taxonomy instead: `install` is a first-class action kind
(`puspa/policy/approvals.py`) that always asks, with tiered
answers (once / conversation / always) in the `ApprovalStore`
and 24-hour expiry. The caller asks the user through
`ApprovalPolicy.decide` and records the answer with content
`{"skill_slug": ..., "sha256": ...}`; `approval_from_store`
builds the per-skill `InstallApproval` evidence from the
stored answer right before the install, and
`consume_install_approval` spends a "once" answer after a
successful install. `install_discovered` still enforces the
evidence itself (slug + hash binding, no approval no
install), and the registry's own `explicit_approval` flag
stays the last gate. Build the evidence fresh from the
store for every install; never cache an `InstallApproval`
across installs, or a spent "once" answer would still look
valid.

## Use

```python
from puspa.marketplace import MarketplaceRegistry, install_discovered
from puspa.marketplace.discovery import (
    DiscoveryCache,
    SkillsShClient,
    approval_from_store,
    consume_install_approval,
    format_options,
    search_with_cache,
)
from puspa.policy.approvals import ApprovalPolicy, ApprovalStore

client = SkillsShClient()
cache = DiscoveryCache()
outcome = search_with_cache(client, cache, "react performance")
print(format_options(outcome.skills))
# The user picks one; ask through the G4 policy and record the answer:
store = ApprovalStore()
policy = ApprovalPolicy(store=store)
content = {"skill_slug": chosen.slug, "sha256": approved_sha256}
decision = policy.decide(tool_name="marketplace.install", content=content)
# ... the user answers "once" to the approval card ...
policy.record_answer("install", "once", content=content)
approval = approval_from_store(store, chosen.slug, approved_sha256)
installed = install_discovered(
    MarketplaceRegistry(),
    chosen,
    user_id=user_id,
    approval=approval,
    skills_root=skills_root,
)
consume_install_approval(store, chosen.slug, approved_sha256)
```

## Tests

`tests/test_marketplace_discovery.py` (38 tests, in-memory,
`--noconftest`). All HTTP goes through `httpx.MockTransport`;
no test calls the live skills.sh API or GitHub. Covered:
search parsing (coercion, skipped items, control characters,
non-200 / malformed / transport-error yields no results),
fallback merge (dedup, ordering, 25 cap, no fallback at 10+
exact / 1 term / 7 terms), the ranking rubric (recommended /
caution / suspect, ordering), the cache (fresh vs stale,
live-fail fallback), the install gates (wrong hash refused,
no/wrong approval refused, unknown and GPL licences refused,
MIT/Apache install, invalid slug refused, missing SKILL.md
refused, default fetchers through a mock transport), and the
built-in skill (imports disabled, discoverable by the curator).
`tests/test_marketplace_discovery_approval.py` (8 tests) covers
the G4 bridge: evidence built from a stored answer, wrong-hash
/ expired / wrong-conversation answers refused, "once" answers
spent after install while "conversation" answers stay usable,
and the full install-then-consume flow against the real
registry. `tests/test_approvals_taxonomy.py` (8 install tests)
covers the `install` kind itself: recognition from the tool
verb and the button label, no gate kind, ask-by-default,
"once" bound to the exact content hash, and `request` refusing
it at the policy layer.

## Honest limits

- The registry API returns no description, version, licence,
  or star count. Discovered skills import as version `1.0.0`
  with a generated description; the licence is detected from
  the source repo's LICENSE file (MIT / Apache-2.0 / BSD
  only), and stars must be supplied by the caller when known.
- SKILL.md is fetched from conventional paths only
  (`skills/<slug>/SKILL.md` or `<slug>/SKILL.md` on
  `main`/`master`). A skill stored anywhere else is not found
  and the install is refused.
- No live call to skills.sh or GitHub was made while building
  this: the API shape comes from the public `find.ts` source
  and the SKILL.md, and every test uses a fake transport. The
  first live search is still unverified.
- The built-in find-skills skill is not yet wired into the turn
  loop's skill activation; its SKILL.md documents the flow and
  it imports through the registry, but nothing invokes it
  automatically yet.
- `InstallApproval` is evidence, not a capability: it is a
  snapshot of a stored answer. Build it fresh from the store
  for every install; a cached object would still match after
  its "once" answer was spent.
