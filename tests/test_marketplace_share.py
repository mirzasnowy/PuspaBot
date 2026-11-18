"""Marketplace sharing modes + invariants (G3).

In-memory: run with ``pytest tests/test_marketplace_share.py
--noconftest -q``.
"""

import copy

import pytest

from puspa.marketplace import DEFAULT_SHARE_MODE, share_skill
from puspa.marketplace.errors import ShareError


def test_default_mode_is_ask():
    assert DEFAULT_SHARE_MODE == "ask"


def test_off_never_shares():
    decision = share_skill("price-compare", mode="off", peers=["p1", "p2"], learned_from_peers=2)
    assert decision.shared is False
    assert decision.granted_peers == []


def test_auto_shares_up_to_learned_peer_count():
    decision = share_skill(
        "price-compare", mode="auto", peers=["p1", "p2", "p3"], learned_from_peers=2
    )
    assert decision.shared is True
    assert decision.granted_peers == ["p1", "p2"]


def test_ask_shares_when_user_approves():
    decision = share_skill(
        "price-compare",
        mode="ask",
        peers=["p1"],
        learned_from_peers=1,
        ask_user=lambda skill_id, peers: True,
    )
    assert decision.shared is True
    assert decision.granted_peers == ["p1"]


def test_ask_declines_without_an_asker():
    decision = share_skill("price-compare", mode="ask", peers=["p1"], learned_from_peers=1)
    assert decision.shared is False


def test_ask_declines_when_user_says_no():
    decision = share_skill(
        "price-compare",
        mode="ask",
        peers=["p1"],
        learned_from_peers=1,
        ask_user=lambda skill_id, peers: False,
    )
    assert decision.shared is False


def test_skill_learned_alone_reaches_no_peers():
    decision = share_skill("price-compare", mode="auto", peers=["p1"], learned_from_peers=0)
    assert decision.shared is False
    assert "alone" in decision.reason


def test_share_never_mutates_the_conversation_index():
    index = {"skills": ["price-compare"], "peers": ["p1"]}
    snapshot = copy.deepcopy(index)
    decision = share_skill(
        "price-compare",
        mode="auto",
        peers=["p1", "p2"],
        learned_from_peers=2,
        conversation_index=index,
    )
    assert decision.shared is True
    assert index == snapshot


def test_unknown_mode_raises():
    with pytest.raises(ShareError):
        share_skill("price-compare", mode="everyone")


def test_no_peers_requested_shares_nothing():
    decision = share_skill("price-compare", mode="auto", peers=[], learned_from_peers=3)
    assert decision.shared is False
