"""G4-backed install approval for discovered skills (G30).

The ``install`` taxonomy kind + the store bridge in
``puspa.marketplace.discovery``: the G4 store is the authority,
the ``InstallApproval`` is evidence built fresh from it for
every install. In-memory; no live HTTP.
"""

from __future__ import annotations

import time

import pytest

from puspa.marketplace import MarketplaceRegistry
from puspa.marketplace.discovery import (
    SKILL_INSTALL_ENV_VAR,
    DiscoveredSkill,
    approval_from_store,
    consume_install_approval,
    install_discovered,
)
from puspa.marketplace.manifest import sha256_bytes
from puspa.policy.approvals import ANSWER_TTL_SECONDS, ApprovalStore

SKILL_TEXT = """---
name: "React Best Practices"
description: "React and Next.js performance optimization guidelines."
author: "Vercel"
---

# React Best Practices

Optimize React rendering and bundle size.
"""

MIT_LICENSE_TEXT = """MIT License

Copyright (c) 2026 Example

Permission is hereby granted, free of charge, to any person obtaining
a copy of this software and associated documentation files.
"""

SLUG = "react-best-practices"
DIGEST = sha256_bytes(SKILL_TEXT.encode("utf-8"))
CONTENT = {"skill_slug": SLUG, "sha256": DIGEST}


@pytest.fixture(autouse=True)
def _skill_install_enabled(monkeypatch):
    """Installs from skills.sh are off by default (plan 6.7); the
    full-flow test below opts in through the feature flag."""
    monkeypatch.setenv(SKILL_INSTALL_ENV_VAR, "1")


def _skill() -> DiscoveredSkill:
    return DiscoveredSkill(
        slug=SLUG,
        name="React Best Practices",
        source="vercel-labs/agent-skills",
        installs=185000,
    )


def test_bridge_returns_none_without_answer():
    store = ApprovalStore()
    assert approval_from_store(store, SLUG, DIGEST) is None


def test_bridge_builds_evidence_from_once_answer():
    store = ApprovalStore()
    store.record("install", "once", content=CONTENT)
    approval = approval_from_store(store, SLUG, DIGEST)
    assert approval is not None
    assert approval.approved is True
    assert approval.skill_slug == SLUG
    assert approval.sha256 == DIGEST
    assert approval.scope == "once"
    assert approval.matches(SLUG, DIGEST) is True


def test_bridge_rejects_wrong_hash():
    store = ApprovalStore()
    store.record("install", "once", content=CONTENT)
    assert approval_from_store(store, SLUG, "0" * 64) is None


def test_bridge_rejects_expired_answer():
    store = ApprovalStore()
    old = time.time() - ANSWER_TTL_SECONDS - 1
    store.record("install", "always", content=CONTENT, granted_at=old)
    assert approval_from_store(store, SLUG, DIGEST) is None


def test_bridge_conversation_answer_needs_same_conversation():
    store = ApprovalStore()
    store.record("install", "conversation", conversation_id="conv-1", content=CONTENT)
    assert approval_from_store(store, SLUG, DIGEST, conversation_id="conv-1") is not None
    assert approval_from_store(store, SLUG, DIGEST, conversation_id="conv-2") is None
    assert approval_from_store(store, SLUG, DIGEST) is None


def test_consume_spends_once_answer_only():
    store = ApprovalStore()
    store.record("install", "once", content=CONTENT)
    assert consume_install_approval(store, SLUG, DIGEST) is True
    # Spent: the bridge no longer finds it, and a second consume fails.
    assert approval_from_store(store, SLUG, DIGEST) is None
    assert consume_install_approval(store, SLUG, DIGEST) is False


def test_consume_leaves_conversation_answer_usable():
    store = ApprovalStore()
    store.record("install", "conversation", conversation_id="conv-1", content=CONTENT)
    assert consume_install_approval(store, SLUG, DIGEST, conversation_id="conv-1") is False
    assert approval_from_store(store, SLUG, DIGEST, conversation_id="conv-1") is not None


def test_full_flow_install_then_consume(tmp_path):
    store = ApprovalStore()
    store.record("install", "once", content=CONTENT)
    registry = MarketplaceRegistry()
    approval = approval_from_store(store, SLUG, DIGEST)
    assert approval is not None
    installed = install_discovered(
        registry,
        _skill(),
        user_id="user-1",
        approval=approval,
        skill_text=SKILL_TEXT,
        license_text=MIT_LICENSE_TEXT,
        skills_root=tmp_path / "skills",
    )
    assert installed["sha256"] == DIGEST
    assert consume_install_approval(store, SLUG, DIGEST) is True
    # The "once" answer is spent: a fresh lookup finds nothing,
    # so a second install would have to ask the user again.
    assert approval_from_store(store, SLUG, DIGEST) is None
