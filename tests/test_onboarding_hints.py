"""Tests: contextual onboarding hints (G28).

Pure trigger tests need no database; persistence tests use a fresh
user per test (via ``make_user``) so seen-flags never leak between
tests. The ``onboarding_hint_seen`` table is created by the tests
themselves (``hints.create_schema``), like the G33 model-settings
tests do -- conftest does not create it.
"""

import pytest

from puspa.onboarding.hints import (
    HINT_FLAGS,
    HINTS,
    create_schema,
    due_hints,
    hint_by_flag,
    hint_to_dict,
    hint_triggered,
    mark_seen,
    seen_flags,
)


def test_registry_has_unique_flags_with_copy_and_target():
    assert len(HINT_FLAGS) == len(set(HINT_FLAGS)) == len(HINTS)
    assert len(HINTS) == 5
    for hint in HINTS:
        assert hint.flag and hint.title and hint.body
        assert hint.screen and hint.action_label
        # Copy is plain text: no secrets, no URLs with credentials.
        assert "http" not in hint.body and "sk-" not in hint.body
        assert hint_to_dict(hint)["flag"] == hint.flag


def test_hint_by_flag_unknown_is_none():
    assert hint_by_flag("first_modules_visit") is not None
    assert hint_by_flag("no_such_hint") is None


@pytest.mark.parametrize(
    "flag,context",
    [
        ("first_modules_visit", {"screen": "modul", "first_visit": True}),
        ("first_approval", {"approval_requested": True}),
        ("first_memory_use", {"memory_used": True}),
        ("first_costly_task", {"task_cost_idr": 2000}),
        ("first_costly_task", {"task_cost_idr": 5000}),
        ("first_billing_row", {"billing_rows": 1}),
        ("first_billing_row", {"billing_rows": 12}),
    ],
)
def test_each_hint_triggers_only_on_its_own_branch(flag, context):
    hint = hint_by_flag(flag)
    assert hint_triggered(hint, context) is True
    # No other hint fires on this branch's context.
    for other in HINTS:
        if other.flag != flag:
            assert hint_triggered(other, context) is False


@pytest.mark.parametrize(
    "flag,context",
    [
        ("first_modules_visit", {"screen": "modul", "first_visit": False}),
        ("first_modules_visit", {"screen": "chat", "first_visit": True}),
        ("first_modules_visit", {}),
        ("first_approval", {"approval_requested": False}),
        ("first_approval", {}),
        ("first_memory_use", {"memory_used": False}),
        ("first_costly_task", {"task_cost_idr": 1999}),
        ("first_costly_task", {"task_cost_idr": 0}),
        ("first_costly_task", {}),
        ("first_billing_row", {"billing_rows": 0}),
        ("first_billing_row", {}),
    ],
)
def test_hint_does_not_trigger_off_branch(flag, context):
    assert hint_triggered(hint_by_flag(flag), context) is False


def test_empty_context_triggers_nothing():
    assert all(hint_triggered(hint, {}) is False for hint in HINTS)
    assert all(hint_triggered(hint, None) is False for hint in HINTS)


def test_due_hints_only_unseen_triggered(make_user, test_dsn):
    u = make_user()
    create_schema(test_dsn)
    context = {"screen": "modul", "first_visit": True, "approval_requested": True}
    due = due_hints(u["user_id"], context, dsn=test_dsn)
    assert [hint["flag"] for hint in due] == ["first_modules_visit", "first_approval"]
    mark_seen(u["user_id"], "first_modules_visit", dsn=test_dsn)
    due = due_hints(u["user_id"], context, dsn=test_dsn)
    assert [hint["flag"] for hint in due] == ["first_approval"]
    # Seen flags never come back, even when the branch is reached again.
    mark_seen(u["user_id"], "first_approval", dsn=test_dsn)
    assert due_hints(u["user_id"], context, dsn=test_dsn) == []


def test_mark_seen_is_idempotent_and_per_user(make_user, test_dsn):
    first = make_user()
    second = make_user()
    create_schema(test_dsn)
    assert seen_flags(first["user_id"], dsn=test_dsn) == set()
    result = mark_seen(first["user_id"], "first_memory_use", dsn=test_dsn)
    assert result == {
        "user_id": first["user_id"],
        "flag": "first_memory_use",
        "seen": True,
    }
    # Marking twice is a no-op, not an error.
    assert mark_seen(first["user_id"], "first_memory_use", dsn=test_dsn)["seen"] is True
    assert seen_flags(first["user_id"], dsn=test_dsn) == {"first_memory_use"}
    # Another user is unaffected.
    assert seen_flags(second["user_id"], dsn=test_dsn) == set()
    assert (
        due_hints(second["user_id"], {"memory_used": True}, dsn=test_dsn)[0]["flag"]
        == "first_memory_use"
    )


def test_mark_seen_unknown_flag_raises(make_user, test_dsn):
    u = make_user()
    create_schema(test_dsn)
    with pytest.raises(ValueError, match="unknown hint flag"):
        mark_seen(u["user_id"], "not_a_hint", dsn=test_dsn)
    assert seen_flags(u["user_id"], dsn=test_dsn) == set()
