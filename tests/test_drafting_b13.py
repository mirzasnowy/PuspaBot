"""Service tests for co-drafting with context memory (B13).

Covers the drafting service only (the HTTP layer lives in
``tests/test_pwa_drafts_api.py``): the voice profile learns
from a recorded preference and the next draft reflects it; a
voice correction persists and changes a later draft; the
draft-then-approve-then-send flow works with an injected
sender; sending without approval is refused; a draft whose
brief contradicts a recorded past decision is flagged; and an
approval is void once the draft text changes.

The in-memory stores are used here so the voice and approval
logic is tested without Postgres; the Postgres store is
covered by ``test_drafting_postgres_roundtrip`` and by the API
tests, which run against the real ``create_app``.
"""

import pytest

from puspa.drafting import DraftingService, PostgresDraftStore
from puspa.drafting.drafting import content_hash
from puspa.drafting.errors import (
    DraftNotApprovedError,
    DraftNotFoundError,
    DraftValidationError,
    NoDeliveryChannelError,
)
from puspa.drafting.schema import create_schema, reset_tables
from puspa.drafting.voice import build_voice_profile, parse_rule
from puspa.memory.facts import MemoryFactStore, record_fact


@pytest.fixture(autouse=True)
def _drafting_schema(test_dsn):
    """Create the drafting schema, then empty it (like the room
    tests: conftest does NOT apply this schema)."""
    create_schema(test_dsn)
    reset_tables(test_dsn)
    yield
    reset_tables(test_dsn)


def _service():
    return DraftingService(fact_store=MemoryFactStore())


def _record(service, user_id, key, content):
    return record_fact(user_id, key, content, source="user_confirmed", store=service.fact_store)


def _sender(captured):
    def send(payload):
        captured.append(payload)
        return {"delivered": True, "channel": payload.get("channel")}

    return send


# -- voice profile ------------------------------------------------------


def test_voice_profile_reads_recorded_preference():
    service = _service()
    user_id = "user-1"
    _record(service, user_id, "preference.greeting", "always start with Halo semuanya,")
    _record(service, user_id, "voice.style", "keep it short")
    profile = service.voice_profile(user_id)
    assert any(rule.kind == "require_start" for rule in profile.rules)
    assert profile.wants_brevity


def test_draft_reflects_recorded_preference():
    service = _service()
    user_id = "user-1"
    _record(service, user_id, "preference.greeting", "always start with Halo semuanya,")
    draft = service.draft(user_id, "message", "mohon maaf, pesanan kakak terlambat sehari")
    assert draft.text.startswith("Halo semuanya")
    assert "Halo semuanya" in draft.text
    assert "keep it short" not in draft.text


def test_default_register_is_everyday_indonesian_not_formal():
    service = _service()
    draft = service.draft("user-1", "message", "tolong kabari saya kalau barang sudah sampai")
    assert draft.text.startswith("Halo,")
    assert "Yth." not in draft.text
    assert "Dear" not in draft.text


def test_english_brief_gets_english_draft():
    service = _service()
    draft = service.draft("user-1", "email", "Please send the invoice for order 42")
    assert draft.text.startswith("Hi,")
    assert draft.text.rstrip().endswith("Thank you,")


def test_parse_rule_shapes():
    assert parse_rule("never use Dear").kind == "ban"
    assert parse_rule("never use Dear").phrase == "Dear"
    assert parse_rule("jangan pakai Yth.").phrase == "Yth"
    assert parse_rule("always start with Halo semuanya,").kind == "require_start"
    assert parse_rule("keep it short").kind == "brevity"
    assert parse_rule("santai aja").kind == "casual"
    assert parse_rule("please write like a poem") is None


# -- voice correction persists ------------------------------------------


def test_voice_correction_persists_and_changes_later_draft():
    service = _service()
    user_id = "user-1"
    before = service.draft(user_id, "message", "Dear customer, pesanan Anda sudah dikirim")
    assert "Dear customer," in before.text
    fact = service.correct_voice(user_id, "never use Dear")
    assert fact["key"].startswith("voice.correction.")
    assert fact["stored"] is True
    after = service.draft(user_id, "message", "Dear customer, pesanan Anda sudah dikirim")
    assert "Dear" not in after.text
    assert "never use 'Dear'" in after.voice_rules_applied
    # The correction is a memory fact: it survives a fresh profile read.
    profile = service.voice_profile(user_id)
    assert any(rule.kind == "ban" and rule.phrase == "Dear" for rule in profile.rules)


def test_voice_correction_rejects_empty():
    service = _service()
    with pytest.raises(DraftValidationError):
        service.correct_voice("user-1", "   ")


def test_unparsed_correction_is_kept_as_style_note():
    service = _service()
    user_id = "user-1"
    service.correct_voice(user_id, "tulis seperti sedang ngobrol di warung")
    profile = service.voice_profile(user_id)
    assert profile.style_notes == ["tulis seperti sedang ngobrol di warung"]


# -- drafting ------------------------------------------------------------


def test_draft_kinds_and_validation():
    service = _service()
    email = service.draft("user-1", "email", "mohon kirim invoice pesanan 42")
    assert email.kind == "email"
    assert email.text.startswith("Halo,")
    assert email.text.rstrip().endswith("Terima kasih,")
    document = service.draft(
        "user-1", "document", "Laporan penjualan\nPenjualan naik 10%\nBiaya ongkir turun"
    )
    assert document.text.startswith("Laporan penjualan")
    assert "Biaya ongkir turun" in document.text
    with pytest.raises(DraftValidationError):
        service.draft("user-1", "letter", "hello")
    with pytest.raises(DraftValidationError):
        service.draft("user-1", "message", "   ")


def test_draft_lists_and_gets_only_own_drafts():
    service = _service()
    first = service.draft("user-1", "message", "halo, barang sudah sampai")
    service.draft("user-2", "message", "halo juga")
    assert [d.id for d in service.list_drafts("user-1")] == [first.id]
    assert service.get_draft(first.id, user_id="user-1").id == first.id
    with pytest.raises(DraftNotFoundError):
        service.get_draft(first.id, user_id="user-2")


# -- past decisions ------------------------------------------------------


def test_draft_contradicting_recorded_decision_is_flagged():
    service = _service()
    user_id = "user-1"
    _record(
        service,
        user_id,
        "decision.discounts",
        "never offer discounts to new customers",
    )
    draft = service.draft(user_id, "message", "tawarkan diskon 20% untuk pelanggan baru hari ini")
    assert draft.decision_flags == ["never offer discounts to new customers"]
    # The flag is a warning, not a silent rewrite: the draft still exists.
    assert draft.status == "draft"


def test_related_decision_is_surfaces_but_not_flagged():
    service = _service()
    user_id = "user-1"
    _record(
        service,
        user_id,
        "decision.shipping",
        "always use same-day shipping for Jakarta orders",
    )
    draft = service.draft(user_id, "message", "pesanan Jakarta ini pakai pengiriman same-day ya")
    assert draft.decision_flags == []
    assert draft.related_decisions == ["always use same-day shipping for Jakarta orders"]


def test_unrelated_decision_is_not_surfaced():
    service = _service()
    user_id = "user-1"
    _record(service, user_id, "decision.refunds", "never refund without a receipt photo")
    draft = service.draft("user-1", "message", "halo, stok batik motif parang masih ada?")
    assert draft.decision_flags == []
    assert draft.related_decisions == []


# -- approval-gated sending ----------------------------------------------


def test_draft_approve_send_flow():
    service = _service()
    user_id = "user-1"
    draft = service.draft(
        user_id, "email", "mohon kirim invoice pesanan 42", recipient="toko@example.id"
    )
    captured: list[dict] = []
    approval = service.approve_draft(draft.id, user_id=user_id)
    assert approval.content_hash == content_hash(draft.text)
    assert service.get_draft(draft.id, user_id=user_id).status == "approved"
    result = service.send_draft(
        draft.id, user_id=user_id, approval_id=approval.id, sender=_sender(captured)
    )
    assert result["draft"]["status"] == "sent"
    assert result["approval"]["status"] == "consumed"
    assert captured[0]["text"] == draft.text
    assert captured[0]["recipient"] == "toko@example.id"
    # The approval is single-use: a second send is refused.
    with pytest.raises(DraftNotApprovedError):
        service.send_draft(
            draft.id, user_id=user_id, approval_id=approval.id, sender=_sender(captured)
        )


def test_send_without_approval_is_refused():
    service = _service()
    draft = service.draft("user-1", "message", "halo, barang sudah sampai")
    captured: list[dict] = []
    with pytest.raises(DraftNotApprovedError):
        service.send_draft(draft.id, user_id="user-1", approval_id="", sender=_sender(captured))
    with pytest.raises(DraftNotApprovedError):
        service.send_draft(
            draft.id,
            user_id="user-1",
            approval_id="no-such-approval",
            sender=_sender(captured),
        )
    assert captured == []
    assert service.get_draft(draft.id, user_id="user-1").status == "draft"


def test_send_with_approval_for_another_draft_is_refused():
    service = _service()
    first = service.draft("user-1", "message", "halo, barang sudah sampai")
    second = service.draft("user-1", "message", "halo, terima kasih sudah order")
    approval = service.approve_draft(first.id, user_id="user-1")
    with pytest.raises(DraftNotApprovedError):
        service.send_draft(second.id, user_id="user-1", approval_id=approval.id, sender=_sender([]))


def test_approval_void_after_edit():
    service = _service()
    draft = service.draft("user-1", "message", "halo, barang sudah sampai")
    approval = service.approve_draft(draft.id, user_id="user-1")
    service.revise_draft(draft.id, user_id="user-1", text="Halo, barang sudah sampai ya")
    assert service.get_draft(draft.id, user_id="user-1").status == "draft"
    with pytest.raises(DraftNotApprovedError):
        service.send_draft(draft.id, user_id="user-1", approval_id=approval.id, sender=_sender([]))
    # A fresh approval for the new text works.
    fresh = service.approve_draft(draft.id, user_id="user-1")
    captured: list[dict] = []
    result = service.send_draft(
        draft.id, user_id="user-1", approval_id=fresh.id, sender=_sender(captured)
    )
    assert result["draft"]["status"] == "sent"


def test_approved_send_without_sender_keeps_approval():
    service = _service()
    draft = service.draft("user-1", "message", "halo, barang sudah sampai")
    approval = service.approve_draft(draft.id, user_id="user-1")
    with pytest.raises(NoDeliveryChannelError):
        service.send_draft(draft.id, user_id="user-1", approval_id=approval.id)
    # The approval was not consumed: it still works once a sender exists.
    captured: list[dict] = []
    result = service.send_draft(
        draft.id, user_id="user-1", approval_id=approval.id, sender=_sender(captured)
    )
    assert result["draft"]["status"] == "sent"


def test_expired_approval_is_refused():
    service = _service()
    draft = service.draft("user-1", "message", "halo, barang sudah sampai")
    approval = service.approve_draft(draft.id, user_id="user-1")
    stored = service.store.get_approval(approval.id, "user-1")
    from datetime import UTC, datetime, timedelta

    stored.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    service.store.update_approval(stored)
    with pytest.raises(DraftNotApprovedError):
        service.send_draft(draft.id, user_id="user-1", approval_id=approval.id, sender=_sender([]))


def test_request_send_approval_without_policy_waits():
    service = _service()
    draft = service.draft("user-1", "message", "halo, barang sudah sampai")
    result = service.request_send_approval(draft.id, user_id="user-1")
    assert result["status"] == "awaiting_approval"
    assert service.get_draft(draft.id, user_id="user-1").status == "draft"


def test_request_send_approval_with_allowing_policy():
    from puspa.policy.approvals import ApprovalPolicy

    policy = ApprovalPolicy()
    policy.record_answer("send_to_other", "always")
    service = DraftingService(fact_store=MemoryFactStore(), policy=policy)
    draft = service.draft(
        "user-1", "message", "halo, barang sudah sampai", recipient="toko@example.id"
    )
    result = service.request_send_approval(draft.id, user_id="user-1")
    assert result["status"] == "approved"
    assert service.get_draft(draft.id, user_id="user-1").status == "approved"


# -- postgres store roundtrip --------------------------------------------


def test_drafting_postgres_roundtrip(test_dsn, make_user):
    user = make_user()
    user_id = user["user_id"]
    service = DraftingService(
        test_dsn,
        draft_store=PostgresDraftStore(test_dsn),
        fact_store=MemoryFactStore(),
    )
    draft = service.draft(user_id, "message", "halo, barang sudah sampai")
    assert service.get_draft(draft.id, user_id=user_id).text == draft.text
    approval = service.approve_draft(draft.id, user_id=user_id)
    captured: list[dict] = []
    result = service.send_draft(
        draft.id, user_id=user_id, approval_id=approval.id, sender=_sender(captured)
    )
    assert result["draft"]["status"] == "sent"
    assert service.list_drafts(user_id)[0].status == "sent"


def test_build_voice_profile_ignores_unknown_keys():
    profile = build_voice_profile(
        "user-1",
        [
            {"key": "voice.greeting", "content": "Halo semuanya,"},
            {"key": "random.thing", "content": "never use Halo"},
            {"key": "voice.style", "content": ""},
        ],
    )
    assert profile.greeting == "Halo semuanya,"
    assert profile.rules == []
