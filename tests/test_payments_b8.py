"""Agent payments, phase 1: prepare only (B8).

Tests run against the Postgres test database named by the
``test_dsn`` fixture (env PUSPA_TEST_DSN). The payments schema
is applied by the autouse fixture below (the repo conftest
does not apply it).

A prepared payment always shows a verified recipient + amount
+ evidence; nothing executes; every preparation is written to
the ledger as a ``payment_prepared`` outcome record plus a G4
approval record; the Rp0 execution limit is architectural —
there is no executor to call, and any execution attempt is
refused and recorded.
"""

from datetime import UTC, datetime, timedelta

import pytest

from puspa.evidence.outcomes import list_outcomes_for_user
from puspa.local_data.rails import PaymentRail
from puspa.payments import (
    DEFAULT_PREPARATION_TTL,
    EXECUTION_LIMIT_RUPIAH,
    ExecutionRefused,
    PaymentPreparationError,
    PreparationService,
    format_rupiah_amount,
    render_prepared_payment,
    resolve_rail,
)
from puspa.payments.schema import create_schema, reset_tables


@pytest.fixture(autouse=True)
def _payments_schema(test_dsn):
    """Create the payments schema, then empty it before and
    after each test."""
    create_schema(test_dsn)
    reset_tables(test_dsn)
    yield
    reset_tables(test_dsn)


@pytest.fixture
def service(test_dsn):
    return PreparationService(test_dsn)


def _recipient(**overrides):
    recipient = {
        "name": "Toko Kopi Senja",
        "account_ref": "1234567890",
        "verified": True,
        "verified_by": "seller page on Tokopedia",
    }
    recipient.update(overrides)
    return recipient


def _evidence(**overrides):
    entry = {
        "kind": "seller_page",
        "source_url": "https://tokopedia.com/toko-kopi-senja",
        "recipient_name": "Toko Kopi Senja",
        "account_ref": "1234567890",
        "excerpt": "Toko Kopi Senja, BCA 1234567890, Rp150.000",
    }
    entry.update(overrides)
    return [entry]


def _prepare(service, user, **overrides):
    kwargs = {
        "user_id": user["user_id"],
        "recipient": _recipient(),
        "amount_rupiah": 150_000,
        "rail": "bayar pake qris",
        "evidence": _evidence(),
        "agent_id": user["agent_id"],
    }
    kwargs.update(overrides)
    return service.prepare_payment(**kwargs)


# --- rail resolution (B5) -------------------------------------------------


def test_resolve_rail_names_rails_by_id_and_mention():
    assert resolve_rail("qris").rail_id == "qris"
    assert resolve_rail("dana").rail_id == "dana"
    assert resolve_rail("gopay").rail_id == "gopay"
    assert resolve_rail("bayar pake qris").rail_id == "qris"
    assert resolve_rail("va bca").rail_id == "virtual_account"
    assert resolve_rail("va bca").bank == "BCA"
    assert resolve_rail("transfer mandiri").rail_id == "bank_transfer"
    rail = PaymentRail(rail_id="ovo", display_name="OVO", kind="e_wallet")
    assert resolve_rail(rail) is rail
    with pytest.raises(PaymentPreparationError):
        resolve_rail("bayar pake tunai")
    with pytest.raises(PaymentPreparationError):
        resolve_rail("")
    with pytest.raises(PaymentPreparationError):
        resolve_rail("cod")


def test_format_rupiah_amount_is_integer_rupiah_only():
    assert format_rupiah_amount(150_000) == "Rp150.000"
    assert format_rupiah_amount(0) == "Rp0"
    assert format_rupiah_amount(1_000_000_000) == "Rp1.000.000.000"
    for bad in (150_000.5, "150000", None, True):
        with pytest.raises(PaymentPreparationError):
            format_rupiah_amount(bad)


# --- preparation: happy path ----------------------------------------------


def test_prepare_payment_records_verified_recipient_amount_rail(service, make_user):
    user = make_user()
    preparation = _prepare(service, user)
    assert preparation.status == "awaiting_approval"
    assert preparation.recipient_name == "Toko Kopi Senja"
    assert preparation.recipient_account == "1234567890"
    assert preparation.amount_rupiah == 150_000
    assert preparation.rail_id == "qris"
    assert preparation.rail_display == "QRIS"
    assert preparation.evidence == _evidence()
    assert preparation.is_payable() is False
    assert preparation.is_expired() is False
    # Default expiry is the 24-hour approval window (the DB clock
    # and the Python clock differ by milliseconds, so check a band).
    assert (
        timedelta(hours=23)
        < preparation.expires_at - preparation.prepared_at
        <= (DEFAULT_PREPARATION_TTL + timedelta(seconds=1))
    )
    summary = preparation.to_dict()
    assert summary["amount_display"] == "Rp150.000"
    assert summary["pay_notice"].startswith("Puspa never pays")
    assert summary["recipient"]["verified"] is True
    rendered = render_prepared_payment(preparation)
    assert "Toko Kopi Senja" in rendered
    assert "Rp150.000" in rendered
    assert "QRIS" in rendered
    assert "not payable yet" in rendered
    assert "Puspa never pays" in rendered


def test_prepare_without_ledger_still_records_row(service, make_user):
    user = make_user()
    preparation = service.prepare_payment(
        user_id=user["user_id"],
        recipient=_recipient(),
        amount_rupiah=150_000,
        rail="qris",
        evidence=_evidence(),
    )
    assert preparation.outcome_id is None
    assert preparation.approval_request_id is None
    assert service.get_prepared_payment(preparation.id, user_id=user["user_id"]) is not None


# --- preparation: refusals --------------------------------------------------


def test_unverified_recipient_is_refused(service, make_user):
    user = make_user()
    for bad in (
        _recipient(verified=False),
        _recipient(verified="yes"),
        _recipient(name=""),
        _recipient(account_ref=""),
        _recipient(verified_by=""),
        {"name": "Toko Kopi Senja"},
    ):
        with pytest.raises(PaymentPreparationError):
            _prepare(service, user, recipient=bad)
    assert service.list_prepared_payments(user["user_id"]) == []


def test_recipient_not_named_by_evidence_is_refused(service, make_user):
    user = make_user()
    evidence = [{"kind": "seller_page", "excerpt": "some other shop, different account"}]
    with pytest.raises(PaymentPreparationError):
        _prepare(service, user, evidence=evidence)
    # Evidence that names the account reference (not the name) counts.
    by_account = [{"kind": "bill", "account_ref": "1234567890"}]
    preparation = _prepare(service, user, evidence=by_account)
    assert preparation.recipient_account == "1234567890"


def test_amount_must_be_positive_integer_rupiah(service, make_user):
    user = make_user()
    for bad in (0, -1, -150_000, 150_000.5, "150000", None, True, 150_000.0):
        with pytest.raises(PaymentPreparationError):
            _prepare(service, user, amount_rupiah=bad)
    assert service.list_prepared_payments(user["user_id"]) == []


def test_rail_must_be_named_and_cannot_be_cod(service, make_user):
    user = make_user()
    for bad in ("bayar pake tunai", "nanti aja", ""):
        with pytest.raises(PaymentPreparationError):
            _prepare(service, user, rail=bad)
    with pytest.raises(PaymentPreparationError):
        _prepare(service, user, rail="cod")
    assert service.list_prepared_payments(user["user_id"]) == []


def test_evidence_is_required(service, make_user):
    user = make_user()
    for bad in ([], None, [{"excerpt": "no kind here"}], [{}], ["a string"]):
        with pytest.raises(PaymentPreparationError):
            _prepare(service, user, evidence=bad)
    assert service.list_prepared_payments(user["user_id"]) == []


def test_expiry_must_be_in_the_future(service, make_user):
    user = make_user()
    past = datetime.now(UTC) - timedelta(minutes=1)
    with pytest.raises(PaymentPreparationError):
        _prepare(service, user, expires_at=past)
    naive = datetime.now() + timedelta(hours=1)
    with pytest.raises(PaymentPreparationError):
        _prepare(service, user, expires_at=naive)
    assert service.list_prepared_payments(user["user_id"]) == []


def test_ledger_without_agent_id_is_refused(service, make_user, ledger):
    user = make_user()
    with pytest.raises(PaymentPreparationError):
        service.prepare_payment(
            user_id=user["user_id"],
            recipient=_recipient(),
            amount_rupiah=150_000,
            rail="qris",
            evidence=_evidence(),
            ledger=ledger,
        )
    assert service.list_prepared_payments(user["user_id"]) == []


# --- idempotency ------------------------------------------------------------


def test_idempotency_same_key_returns_same_preparation(service, make_user, ledger, test_dsn):
    user = make_user()
    first = _prepare(service, user, ledger=ledger, idempotency_key="order-123")
    second = _prepare(service, user, ledger=ledger, idempotency_key="order-123")
    assert second.id == first.id
    assert len(service.list_prepared_payments(user["user_id"])) == 1
    outcomes = list_outcomes_for_user(test_dsn, user["user_id"], outcome_type="payment_prepared")
    assert len(outcomes) == 1


def test_idempotency_same_key_different_content_is_refused(service, make_user):
    user = make_user()
    _prepare(service, user, idempotency_key="order-123")
    with pytest.raises(PaymentPreparationError):
        _prepare(service, user, amount_rupiah=200_000, idempotency_key="order-123")
    with pytest.raises(PaymentPreparationError):
        _prepare(
            service,
            user,
            recipient=_recipient(name="Warung Sebelah"),
            idempotency_key="order-123",
        )
    assert len(service.list_prepared_payments(user["user_id"])) == 1


# --- ledger + approval record ----------------------------------------------


def test_preparation_is_ledger_recorded_with_approval(service, make_user, ledger, test_dsn):
    user = make_user()
    preparation = _prepare(service, user, ledger=ledger)
    assert preparation.outcome_id is not None
    assert preparation.approval_request_id is not None
    outcomes = list_outcomes_for_user(test_dsn, user["user_id"], outcome_type="payment_prepared")
    assert len(outcomes) == 1
    outcome = outcomes[0]
    assert outcome.outcome_id == preparation.outcome_id
    assert outcome.subject_id == preparation.id
    assert outcome.details["kind"] == "payment_prepared"
    assert outcome.details["executed"] is False
    assert outcome.details["amount_rupiah"] == 150_000
    assert outcome.details["recipient_name"] == "Toko Kopi Senja"
    assert outcome.details["rail_id"] == "qris"
    assert outcome.receipts[0]["kind"] == "prepared_payment"
    assert outcome.receipts[0]["status"] == "awaiting_approval"
    # The G4 approval record: an approval_requested event on the
    # same chain, classified as a pay action with an amount.
    events = ledger.events(preparation.task_id, event_type="approval_requested")
    assert len(events) == 1
    approval = events[0]["content"]
    assert approval["kind"] == "payment_approval"
    assert approval["preparation_id"] == preparation.id
    assert approval["classification"] == "pay"
    assert approval["auto_review_findings"] == []
    assert approval["amount_rupiah"] == 150_000


def test_preparation_is_not_payable_before_approval(service, make_user):
    user = make_user()
    preparation = _prepare(service, user)
    assert service.list_payable_payments(user["user_id"]) == []
    approved = service.approve_preparation(preparation.id, user_id=user["user_id"])
    assert approved.status == "approved"
    assert approved.approved_by == user["user_id"]
    assert approved.approved_at is not None
    assert service.list_payable_payments(user["user_id"]) == [approved]
    # Approving again is a no-op, not a second approval.
    assert service.approve_preparation(preparation.id, user_id=user["user_id"]).id == approved.id


def test_approval_is_ledger_recorded(service, make_user, ledger):
    user = make_user()
    preparation = _prepare(service, user, ledger=ledger, agent_id=user["agent_id"])
    service.approve_preparation(
        preparation.id, user_id=user["user_id"], ledger=ledger, agent_id=user["agent_id"]
    )
    events = ledger.events(preparation.task_id, event_type="approval_granted")
    assert len(events) == 1
    assert events[0]["content"]["kind"] == "payment_approval"
    assert events[0]["content"]["preparation_id"] == preparation.id


def test_only_the_owner_can_approve_or_reject(service, make_user):
    user = make_user()
    other = make_user()
    preparation = _prepare(service, user)
    with pytest.raises(PaymentPreparationError):
        service.approve_preparation(preparation.id, user_id=other["user_id"])
    with pytest.raises(PaymentPreparationError):
        service.reject_preparation(preparation.id, user_id=other["user_id"])
    assert service.get_prepared_payment(preparation.id, user_id=user["user_id"]).status == (
        "awaiting_approval"
    )


def test_reject_is_terminal_and_ledger_recorded(service, make_user, ledger):
    user = make_user()
    preparation = _prepare(service, user, ledger=ledger, agent_id=user["agent_id"])
    rejected = service.reject_preparation(
        preparation.id, user_id=user["user_id"], ledger=ledger, agent_id=user["agent_id"]
    )
    assert rejected.status == "rejected"
    assert service.list_payable_payments(user["user_id"]) == []
    with pytest.raises(PaymentPreparationError):
        service.approve_preparation(preparation.id, user_id=user["user_id"])
    with pytest.raises(PaymentPreparationError):
        service.reject_preparation(preparation.id, user_id=user["user_id"])
    events = ledger.events(preparation.task_id, event_type="approval_rejected")
    assert len(events) == 1
    assert events[0]["content"]["kind"] == "payment_approval"


# --- expiry -----------------------------------------------------------------


def test_expiry_is_enforced(service, make_user):
    user = make_user()
    now = datetime.now(UTC)
    preparation = _prepare(service, user, expires_at=now + timedelta(minutes=5), now=now)
    assert preparation.is_payable(now=now) is False  # not approved yet
    approved = service.approve_preparation(preparation.id, user_id=user["user_id"], now=now)
    assert approved.is_payable(now=now) is True
    later = now + timedelta(minutes=6)
    assert approved.is_payable(now=later) is False
    with pytest.raises(PaymentPreparationError):
        service.approve_preparation(preparation.id, user_id=user["user_id"], now=later)
    # Reading it back after expiry shows it expired.
    assert (
        service.get_prepared_payment(preparation.id, user_id=user["user_id"], now=later).status
        == "expired"
    )
    assert service.list_payable_payments(user["user_id"], now=later) == []


def test_expired_preparation_cannot_be_approved_or_marked_paid(service, make_user):
    user = make_user()
    now = datetime.now(UTC)
    preparation = _prepare(service, user, expires_at=now + timedelta(minutes=5), now=now)
    later = now + timedelta(minutes=6)
    with pytest.raises(PaymentPreparationError):
        service.approve_preparation(preparation.id, user_id=user["user_id"], now=later)
    with pytest.raises(PaymentPreparationError):
        service.mark_paid_by_user(preparation.id, user_id=user["user_id"], now=later)


# --- mark paid by user (assertion, never execution) --------------------------


def test_mark_paid_by_user_needs_approval_first(service, make_user):
    user = make_user()
    preparation = _prepare(service, user)
    with pytest.raises(PaymentPreparationError):
        service.mark_paid_by_user(preparation.id, user_id=user["user_id"])
    service.approve_preparation(preparation.id, user_id=user["user_id"])
    paid = service.mark_paid_by_user(
        preparation.id, user_id=user["user_id"], assertion="paid via BCA mobile"
    )
    assert paid.status == "paid_by_user"
    assert paid.paid_asserted_by == user["user_id"]
    assert paid.paid_assertion == "paid via BCA mobile"
    assert paid.paid_asserted_at is not None
    assert service.list_payable_payments(user["user_id"]) == []
    with pytest.raises(PaymentPreparationError):
        service.mark_paid_by_user(preparation.id, user_id=user["user_id"])


def test_mark_paid_by_user_is_ledger_recorded_as_assertion(service, make_user, ledger, test_dsn):
    user = make_user()
    preparation = _prepare(service, user, ledger=ledger, agent_id=user["agent_id"])
    service.approve_preparation(
        preparation.id, user_id=user["user_id"], ledger=ledger, agent_id=user["agent_id"]
    )
    service.mark_paid_by_user(
        preparation.id,
        user_id=user["user_id"],
        assertion="paid via BCA mobile",
        ledger=ledger,
        agent_id=user["agent_id"],
    )
    outcomes = list_outcomes_for_user(
        test_dsn, user["user_id"], outcome_type="payment_paid_by_user"
    )
    assert len(outcomes) == 1
    outcome = outcomes[0]
    assert outcome.details["executed"] is False
    assert outcome.details["paid_by_user"] is True
    assert outcome.evidence[0]["kind"] == "user_assertion"
    assert outcome.evidence[0]["assertion"] == "paid via BCA mobile"


# --- execution guard ----------------------------------------------------------


def test_execution_limit_is_zero_and_architectural():
    assert EXECUTION_LIMIT_RUPIAH == 0


def test_execute_payment_always_refuses_and_records(service, make_user, ledger, test_dsn):
    user = make_user()
    preparation = _prepare(service, user, ledger=ledger, agent_id=user["agent_id"])
    service.approve_preparation(
        preparation.id, user_id=user["user_id"], ledger=ledger, agent_id=user["agent_id"]
    )
    # Even a fully approved preparation cannot be executed.
    with pytest.raises(ExecutionRefused):
        service.execute_payment(
            preparation.id,
            user_id=user["user_id"],
            ledger=ledger,
            agent_id=user["agent_id"],
        )
    events = ledger.events(preparation.task_id, event_type="approval_rejected")
    refusals = [e for e in events if e["content"].get("kind") == "execution_refused"]
    assert len(refusals) == 1
    assert refusals[0]["content"]["execution_limit_rupiah"] == 0
    assert refusals[0]["content"]["amount_rupiah"] == 150_000
    assert service.get_prepared_payment(preparation.id, user_id=user["user_id"]).status == (
        "approved"
    )


def test_execute_payment_refuses_without_a_ledger_too(service, make_user):
    user = make_user()
    preparation = _prepare(service, user)
    with pytest.raises(ExecutionRefused):
        service.execute_payment(preparation.id, user_id=user["user_id"])
    # And for an unknown preparation id: still refused, never executed.
    with pytest.raises(ExecutionRefused):
        service.execute_payment("00000000-0000-0000-0000-000000000000", user_id=user["user_id"])


def test_there_is_no_executor_to_call(service):
    assert not hasattr(service, "executor")
    assert not hasattr(service, "gateway")
    assert not hasattr(PreparationService, "execute_settlement")
    # The only execution-named entry point is the guard, and it raises.
    from puspa.payments import execute_payment

    with pytest.raises(ExecutionRefused):
        execute_payment("00000000-0000-0000-0000-000000000000", user_id="x")


# --- privacy ------------------------------------------------------------------


def test_preparations_never_leak_across_users(service, make_user):
    user = make_user()
    other = make_user()
    preparation = _prepare(service, user)
    assert service.get_prepared_payment(preparation.id, user_id=other["user_id"]) is None
    assert service.list_prepared_payments(other["user_id"]) == []
    assert service.list_payable_payments(other["user_id"]) == []
