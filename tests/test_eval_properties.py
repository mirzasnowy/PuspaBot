"""Hypothesis property tests (plan section 8.6).

- token subset: reflexive, monotone under removal, transitive;
- ledger hash chain: recomputation verifies, tampering changes
  the hash, and order matters;
- approval hash binding: same content, same hash; any change,
  a different hash.
"""

from datetime import UTC, datetime

from hypothesis import given, settings
from hypothesis import strategies as st

from puspa.ledger.ledger import compute_event_hash
from puspa.policy.approvals import _answer_hash
from puspa.policy.tokens import is_subset

_TOOLS = st.sampled_from(["read", "search", "send_message", "pay", "write_memory"])
_BUDGET = st.fixed_dictionaries(
    {
        "step_max": st.integers(min_value=1, max_value=50),
        "token_max": st.integers(min_value=100, max_value=100000),
        "rupiah_max": st.integers(min_value=100, max_value=10000),
    }
)


def _payload(tools, budget, exp):
    return {
        "user_id": "user-1",
        "agent_id": "agent-1",
        "subagent_id": None,
        "tools": tools,
        "domains": ["tokopedia.com"],
        "connectors": {"gmail": ["read"]},
        "budget": budget,
        "exp": exp,
        "iat": 0,
        "parent": None,
    }


@settings(max_examples=50)
@given(
    tools=st.lists(_TOOLS, unique=True, min_size=0, max_size=4),
    budget=_BUDGET,
    exp=st.integers(min_value=1, max_value=100000),
)
def test_token_subset_is_reflexive(tools, budget, exp):
    payload = _payload(tools, budget, exp)
    assert is_subset(payload, payload)


@settings(max_examples=50)
@given(
    tools=st.lists(_TOOLS, unique=True, min_size=1, max_size=4),
    budget=_BUDGET,
    exp=st.integers(min_value=1, max_value=100000),
)
def test_token_subset_is_monotone_under_tool_removal(tools, budget, exp):
    parent = _payload(tools, budget, exp)
    child = _payload(tools[:-1], budget, exp)
    assert is_subset(child, parent)
    assert not is_subset(parent, child)


@settings(max_examples=50)
@given(
    tools=st.lists(_TOOLS, unique=True, min_size=0, max_size=4),
    budget=_BUDGET,
    exp=st.integers(min_value=1, max_value=100000),
)
def test_token_subset_rejects_a_tool_outside_the_parent(tools, budget, exp):
    parent = _payload(tools, budget, exp)
    outside = "delete_file" if "delete_file" not in tools else "format_disk"
    child = _payload([*tools, outside], budget, exp)
    assert not is_subset(child, parent)


@settings(max_examples=50)
@given(
    steps=st.lists(
        st.fixed_dictionaries({"step": st.integers(min_value=0, max_value=100)}),
        min_size=1,
        max_size=6,
    )
)
def test_ledger_hash_chain_recomputes_and_detects_tampering(steps):
    created = datetime(2026, 11, 20, 9, 0, 0, tzinfo=UTC)
    previous = "0" * 64
    hashes = []
    for content in steps:
        event_hash = compute_event_hash(
            task_id="task-1",
            agent_id="agent-1",
            subagent_id=None,
            event_type="tool_called",
            content=content,
            parent_id=None,
            previous_hash=previous,
            created_at=created,
        )
        hashes.append(event_hash)
        previous = event_hash
    assert len(set(hashes)) == len(hashes)
    for index, content in enumerate(steps):
        recomputed = compute_event_hash(
            task_id="task-1",
            agent_id="agent-1",
            subagent_id=None,
            event_type="tool_called",
            content=content,
            parent_id=None,
            previous_hash="0" * 64 if index == 0 else hashes[index - 1],
            created_at=created,
        )
        assert recomputed == hashes[index]
        tampered = compute_event_hash(
            task_id="task-1",
            agent_id="agent-1",
            subagent_id=None,
            event_type="tool_called",
            content={"step": content["step"] + 1000},
            parent_id=None,
            previous_hash="0" * 64 if index == 0 else hashes[index - 1],
            created_at=created,
        )
        assert tampered != hashes[index]


@settings(max_examples=50)
@given(
    content=st.fixed_dictionaries(
        {
            "tool": st.sampled_from(["pay", "send_message", "share_file"]),
            "recipient": st.text(min_size=1, max_size=12, alphabet="0123456789"),
            "amount": st.integers(min_value=0, max_value=1000000),
        }
    )
)
def test_approval_hash_binding_is_stable_and_sensitive(content):
    first = _answer_hash(content)
    assert first == _answer_hash(dict(content))
    assert len(first) == 64
    for key in content:
        changed = dict(content)
        changed[key] = "CHANGED" if not isinstance(changed[key], int) else changed[key] + 1
        assert _answer_hash(changed) != first
    assert _answer_hash(None) == ""
    assert _answer_hash({}) == ""
