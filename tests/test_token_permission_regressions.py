"""Regression tests for the surviving mutants in
``puspa/policy/tokens.py`` (E-16 mutation score 0.333, first 10
mutants). Each test FAILS on its mutant and PASSES on the real
code, so the mutation runner can prove the gap is closed:

- M001/M002 (line 88): ``TOOL_PERMISSION_RANK`` values
  (``deny`` 0 -> 1, ``ask`` 1 -> 0) — a child that widens "deny"
  to "ask" must never be a subset;
- M004 (line 127): ``and`` -> ``or`` in ``_normalize_id`` — a
  non-string token id must not raise;
- M005 (line 161): ``not`` removed in ``jti_of`` — a dict payload
  carrying a ``jti`` must report it;
- M008 (line 173): ``or`` -> ``and`` in ``_validate_payload``
  (tools) — a string ``tools`` value must be rejected;
- M009 (line 177): ``or`` -> ``and`` in ``_validate_payload``
  (domains) — a string ``domains`` value must be rejected;
- M010 (line 181): ``or`` -> ``and`` in ``_validate_payload``
  (connectors) — a connector scope given as a bare string
  instead of a list must be rejected.

Note: the E-16 doc described the last three as "the ``or``
guards"; the line numbers above are the measured ones (tools
173, domains 177, connectors 181).
"""

import time

import pytest

from puspa.policy import TokenError, issue_child, issue_token, jti_of
from puspa.policy.tokens import _issued, _revoked, is_revoked, is_subset, revoke

AGENT_ID = "agent-token-regressions"
USER_ID = "user-1"


@pytest.fixture(autouse=True)
def _reset_token_state():
    """Isolation: in-memory token state is process-global."""
    _revoked.clear()
    _issued.clear()
    yield
    _revoked.clear()
    _issued.clear()


def _payload(**overrides):
    now = int(time.time())
    payload = {
        "user_id": USER_ID,
        "agent_id": AGENT_ID,
        "subagent_id": None,
        "tools": ["search_web"],
        "domains": ["tokopedia.com"],
        "connectors": {"search": ["read"]},
        "budget": {"step_max": 25, "token_max": 100000, "rupiah_max": 1000000},
        "exp": now + 3600,
        "iat": now,
        "parent": None,
    }
    payload.update(overrides)
    return payload


def test_child_ask_permission_cannot_widen_deny_parent():
    """Kills M001 (deny 0 -> 1) and M002 (ask 1 -> 0).

    The only input that separates the real ranks from either
    mutant is a child asking where the parent denies: the real
    order (deny 0 < ask 1) refuses it, and both mutants collapse
    deny and ask to the same rank and accept it.
    """
    parent = _payload(tool_permissions={"search_web": "deny"})
    child = _payload(tool_permissions={"search_web": "ask"})
    assert is_subset(child, parent) is False
    # The reverse direction is a genuine narrowing (deny is
    # narrower than ask), so it stays a subset.
    assert is_subset(parent, child) is True


def test_issue_child_refuses_ask_child_of_deny_parent(ephemeral_keys):
    """Kills M001 and M002 through the issuing path as well."""
    signing_key, public_key = ephemeral_keys
    parent_token = issue_token(_payload(tool_permissions={"search_web": "deny"}), signing_key)
    with pytest.raises(TokenError, match="bukan himpunan bagian"):
        issue_child(
            parent_token,
            _payload(tool_permissions={"search_web": "ask"}),
            signing_key,
            public_key,
        )


def test_revoke_accepts_non_string_token_id():
    """Kills M004 (``and`` -> ``or`` in ``_normalize_id``).

    The mutant evaluates ``"." in token_or_id`` for a non-string
    id, which raises TypeError; the real code short-circuits and
    treats the id as opaque.
    """
    assert is_revoked(12345) is False
    revoke(12345)
    assert is_revoked(12345) is True


def test_jti_of_returns_jti_for_dict_payload():
    """Kills M005 (``not`` removed in ``jti_of``).

    The mutant returns None for every dict payload; the real code
    returns the carried ``jti`` and None only for a non-dict.
    """
    assert jti_of(_payload(jti="abc-123")) == "abc-123"
    assert jti_of("not-a-dict") is None
    assert jti_of(_payload()) is None


def test_string_tools_value_is_rejected(ephemeral_keys):
    """Kills M008 (``or`` -> ``and`` in the tools check).

    A string iterates to single characters, all strings, so the
    mutant's ``and`` lets it pass validation; the real code
    rejects any non-list ``tools`` value.
    """
    signing_key, _public_key = ephemeral_keys
    with pytest.raises(TokenError, match="field tools harus list"):
        issue_token(_payload(tools="search_web"), signing_key)


def test_string_domains_value_is_rejected(ephemeral_keys):
    """Kills M009 (``or`` -> ``and`` in the domains check)."""
    signing_key, _public_key = ephemeral_keys
    with pytest.raises(TokenError, match="field domains harus list"):
        issue_token(_payload(domains="tokopedia.com"), signing_key)


def test_string_connector_scope_value_is_rejected(ephemeral_keys):
    """Kills M010 (``or`` -> ``and`` in the connectors check).

    A scope given as a bare string instead of a list: the real
    code rejects it, while the mutant's ``and`` lets it through
    because the value is a dict (so the first half is False).
    """
    signing_key, _public_key = ephemeral_keys
    with pytest.raises(TokenError, match="field konektor harus"):
        issue_token(_payload(connectors={"gmail": "read"}), signing_key)
