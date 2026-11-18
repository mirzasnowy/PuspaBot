---
name: "Find Skills"
description: "Helps users discover and install agent skills when they ask how to do something, look for a skill for a task, or want to extend what Puspa can do. Searches the skills.sh registry, checks the install leaderboard first, verifies quality before recommending, presents options, and installs only with the user's explicit approval through the marketplace."
author: "Puspa Team"
---

# Find Skills

This skill helps you discover and install skills from the open
agent skills ecosystem, through Puspa's own marketplace — never
through an external package manager.

## When to Use This Skill

Use this skill when the user:

- Asks "how do I do X" where X might be a common task with an
  existing skill
- Says "find a skill for X" or "is there a skill for X"
- Asks "can you do X" where X is a specialized capability
- Expresses interest in extending Puspa's capabilities
- Wants to search for tools, templates, or workflows
- Mentions they wish they had help with a specific domain
  (design, testing, deployment, and so on)

## How to Help Users Find Skills

All search and install work goes through
`puspa.marketplace.discovery` — never shell out to a package
manager, and never install anything without the user approving
that exact skill first.

### Step 1: Understand What They Need

When a user asks for help with something, identify:

1. The domain (for example React, testing, design, deployment)
2. The specific task (for example writing tests, creating
   animations, reviewing PRs)
3. Whether this is a common enough task that a skill likely
   exists

### Step 2: Check the Leaderboard First

Before searching, check whether a well-known skill already
exists for the domain. The registry ranks skills by total
installs, surfacing the most popular and battle-tested options.
Use `rank_results` on the search results: a skill with 1K+
installs from a trusted official source
(`vercel-labs`, `anthropics`, `microsoft`) is the default
recommendation.

For example, top skills for web development include:

- `vercel-labs/agent-skills` — React, Next.js, web design
  (100K+ installs each)
- `anthropics/skills` — frontend design, document processing
  (100K+ installs)

### Step 3: Search for Skills

If the leaderboard does not cover the user's need, search the
registry with `search_with_cache`:

```python
from puspa.marketplace.discovery import (
    DiscoveryCache,
    SkillsShClient,
    search_with_cache,
)

client = SkillsShClient()
cache = DiscoveryCache()
outcome = search_with_cache(client, cache, "react performance")
```

The search uses the skills.sh API
(`GET https://skills.sh/api/search?q=...`). When the exact
search returns fewer than 10 results and the query has 2-6
words, it retries with each word dropped once and merges the
partial matches after the exact ones (at most 25 total).
Results are cached locally, so a search still works when the
registry is unreachable.

For example:

- User asks "how do I make my React app faster?" → search
  `react performance`
- User asks "can you help me with PR reviews?" → search
  `pr review`
- User asks "I need to create a changelog" → search `changelog`

### Step 4: Verify Quality Before Recommending

**Do not recommend a skill based solely on search results.**
`rank_results` applies the quality rubric; read its verdict
and reasons before presenting anything:

1. **Install count** — Prefer skills with 1K+ installs. Be
   cautious with anything under 100.
2. **Source reputation** — Official sources (`vercel-labs`,
   `anthropics`, `microsoft`) are more trustworthy than unknown
   authors.
3. **GitHub stars** — Check the source repository. A skill
   from a repo with under 100 stars should be treated with
   skepticism. When the star count is unknown, say so — never
   assume it is good.

### Step 5: Present Options to the User

When you find relevant skills, present them with
`format_options`, showing:

1. The skill name and what it does
2. The install count and source
3. The quality verdict and why
4. A link to learn more at skills.sh

Example response:

```
I found a skill that might help! The "react-best-practices" skill provides
React and Next.js performance optimization guidelines from Vercel Engineering.
(185K installs, recommended: official source, 185K installs)

To install it, approve this exact skill and I will install it through
the marketplace (licence checked, content hash checked, installed disabled
first, enabled only after your approval).

Learn more: https://skills.sh/vercel-labs/agent-skills/react-best-practices
```

### Step 6: Offer to Install — Only With Approval

If the user wants to proceed, install through the marketplace
with `install_discovered`. The install is refused unless the
user approved this exact skill (and content hash), the
downloaded SKILL.md matches the approved hash, and the skill's
licence is MIT, Apache-2.0, or BSD:

```python
from puspa.marketplace import MarketplaceRegistry, install_discovered
from puspa.marketplace.discovery import InstallApproval

registry = MarketplaceRegistry()
# approved_sha256 is the hash of the SKILL.md shown to the user
# when they approved this exact skill.
approval = InstallApproval.for_skill(chosen.slug, approved_sha256)
installed = install_discovered(
    registry,
    chosen,
    user_id=user_id,
    approval=approval,
    skills_root=skills_root,
)
```

The skill is imported DISABLED first and only enabled after
the approval and licence checks pass. Installing also records
the skill with the G24 curator (`puspa.memory.curator`), so
its usage is tracked and it goes stale, then archived, if
nobody uses it — never deleted.

## Common Skill Categories

When searching, consider these common categories:

| Category        | Example Queries                          |
| --------------- | ---------------------------------------- |
| Web Development | react, nextjs, typescript, css, tailwind |
| Testing         | testing, jest, playwright, e2e           |
| DevOps          | deploy, docker, kubernetes, ci-cd        |
| Documentation   | docs, readme, changelog, api-docs        |
| Code Quality    | review, lint, refactor, best-practices   |
| Design          | ui, ux, design-system, accessibility     |
| Productivity    | workflow, automation, git                |

## Tips for Effective Searches

1. **Use specific keywords**: "react testing" is better than
   just "testing"
2. **Try alternative terms**: If "deploy" doesn't work, try
   "deployment" or "ci-cd"
3. **Check popular sources**: Many skills come from
   `vercel-labs/agent-skills` or other official collections

## When No Skills Are Found

If no relevant skills exist:

1. Acknowledge that no existing skill was found
2. Offer to help with the task directly using general
   capabilities
3. Suggest the user could create their own skill

Example:

```
I searched for skills related to "xyz" but didn't find any matches.
I can still help you with this task directly! Would you like me to proceed?
If this is something you do often, you could create your own skill.
```
