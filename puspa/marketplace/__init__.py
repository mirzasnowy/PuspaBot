"""Puspa skill/module marketplace (G3).

Registry, trust scoring, install/rollback, sharing, authoring
standards, per-run narrowing, and learn-from-chat review for
installable modules and skills.
"""

from __future__ import annotations

from .authoring import check_authoring, inject_config, resolve_config
from .errors import (
    EntitlementError,
    ManifestError,
    MarketplaceError,
    ShareError,
    ToolLimitError,
    TrustError,
)
from .learning import (
    MAX_CHANGES_PER_REVIEW,
    MAX_CONSECUTIVE_FAILURES,
    REVIEW_TIMEOUT_SECONDS,
    LearningStep,
    ReviewOutcome,
    learn_from_turn,
    review_after_reply,
)
from .manifest import (
    MAX_INDEX_BYTES,
    MAX_SKILLS,
    MAX_STAGED,
    SkillManifest,
    parse_frontmatter,
    validate_manifest,
    validate_skill_folder,
)
from .narrowing import (
    MAX_DIRECT_TOOLS,
    MAX_TOOLS,
    SEARCH_TOOLS_NAME,
    TOOL_LIMIT_REFUSED_EVENT,
    NarrowingResult,
    narrow_tools,
    search_tools,
)
from .registry import MarketplaceRegistry, RegistryEntry, default_entitlement
from .share import DEFAULT_SHARE_MODE, SHARE_MODES, ShareDecision, share_skill
from .store import MemoryStore, PostgresStore, create_schema
from .trust import (
    MIN_TRUST_SCORE,
    TrustScore,
    can_install_without_approval,
    score_module,
    score_skill_dir,
)

__all__ = [
    "MAX_CHANGES_PER_REVIEW",
    "MAX_CONSECUTIVE_FAILURES",
    "MAX_DIRECT_TOOLS",
    "MAX_INDEX_BYTES",
    "MAX_SKILLS",
    "MAX_STAGED",
    "MAX_TOOLS",
    "MIN_TRUST_SCORE",
    "REVIEW_TIMEOUT_SECONDS",
    "SEARCH_TOOLS_NAME",
    "SHARE_MODES",
    "TOOL_LIMIT_REFUSED_EVENT",
    "DEFAULT_SHARE_MODE",
    "EntitlementError",
    "LearningStep",
    "ManifestError",
    "MarketplaceError",
    "MarketplaceRegistry",
    "MemoryStore",
    "NarrowingResult",
    "PostgresStore",
    "RegistryEntry",
    "ReviewOutcome",
    "ShareDecision",
    "ShareError",
    "SkillManifest",
    "ToolLimitError",
    "TrustError",
    "TrustScore",
    "can_install_without_approval",
    "check_authoring",
    "create_schema",
    "default_entitlement",
    "inject_config",
    "learn_from_turn",
    "narrow_tools",
    "parse_frontmatter",
    "resolve_config",
    "review_after_reply",
    "score_module",
    "score_skill_dir",
    "search_tools",
    "share_skill",
    "validate_manifest",
    "validate_skill_folder",
]
