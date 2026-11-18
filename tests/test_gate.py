"""Tes gerbang tunggal + batas belanja — definisi 2.6, CONTRACTS.md
bagian "Gerbang tunggal".

Memetakan ke gerbang: alur persetujuan, persetujuan terikat isi
(hash berubah membatalkan), limit Rp0 memblokir pembayaran,
eksekusi ganda hanya sekali, token dicabut ditolak. Persetujuan DAN
batas adalah dua cek independen.
"""

import time

import pytest

from puspa.policy import Gate, GateDenied, SpendingLimit, issue_token, revoke, token_id
from puspa.policy.tokens import _issued, _revoked


@pytest.fixture(autouse=True)
def _reset_revocation():
    """Isolasi antar tes: pencabutan token bersifat in-memory global."""
    _revoked.clear()
    _issued.clear()
    yield
    _revoked.clear()
    _issued.clear()


@pytest.fixture
def stub_log():
    events = []

    def log(event_type, content):
        events.append((event_type, content))

    log.events = events
    return log


def _payload(**overrides):
    now = int(time.time())
    payload = {
        "user_id": "user-1",
        "agent_id": "agen-puspa",
        "subagent_id": None,
        "tools": ["gmail.kirim", "calendar.create_event", "prepare_payment"],
        "domains": ["tokopedia.com"],
        "connectors": {"gmail": ["read", "send"], "calendar": ["read", "create_event"]},
        "budget": {"step_max": 25, "token_max": 100000, "rupiah_max": 1000000},
        "exp": now + 3600,
        "iat": now,
        "parent": None,
    }
    payload.update(overrides)
    return payload


def _token(ephemeral_keys, **overrides):
    sk, _pk = ephemeral_keys
    return issue_token(_payload(**overrides), sk)


def _gate(ephemeral_keys, stub_log, spending=None):
    _sk, pk = ephemeral_keys
    return Gate(log=stub_log, spending=spending, public_key=pk)


# --- Alur persetujuan -----------------------------------------------------


def test_request_approve_execute_ok(ephemeral_keys, stub_log):
    gate = _gate(ephemeral_keys, stub_log)
    token = _token(ephemeral_keys)
    req = gate.request_approval(
        user_id="user-1",
        agent_id="agen-puspa",
        token=token,
        kind="send",
        content={"recipient": "a@contoh.id", "title": "Halo", "content": "tes"},
    )
    assert req.status == "requested"
    assert ("approval_requested", req.request_id) in [
        (j, i.get("request_id")) for j, i in stub_log.events
    ]
    # Belum disetujui: tidak boleh dieksekusi.
    calls = []
    with pytest.raises(GateDenied):
        gate.execute(req.request_id, executor=calls.append)
    assert calls == []

    gate.approve(req.request_id, "user-1")
    assert req.status == "approved"
    assert any(j == "approval_granted" for j, _ in stub_log.events)

    result = gate.execute(req.request_id, executor=lambda content: {"sent": content["recipient"]})
    assert result == {"sent": "a@contoh.id"}
    assert req.status == "executed"


def test_approve_only_owner(ephemeral_keys, stub_log):
    gate = _gate(ephemeral_keys, stub_log)
    req = gate.request_approval(
        user_id="user-1",
        agent_id="agen-puspa",
        token=_token(ephemeral_keys),
        kind="send",
        content={"recipient": "a@contoh.id"},
    )
    with pytest.raises(GateDenied):
        gate.approve(req.request_id, "user-2")
    # Pemilik tetap bisa menyetujui setelah itu.
    gate.approve(req.request_id, "user-1")
    assert req.status == "approved"


def test_request_kind_not_allowed_by_token(ephemeral_keys, stub_log):
    gate = _gate(ephemeral_keys, stub_log)
    token = _token(ephemeral_keys, tools=["read_price"], connectors={})
    with pytest.raises(GateDenied):
        gate.request_approval(
            user_id="user-1",
            agent_id="agen-puspa",
            token=token,
            kind="send",
            content={"recipient": "a@contoh.id"},
        )


def test_request_expired_token_denied(ephemeral_keys, stub_log):
    gate = _gate(ephemeral_keys, stub_log)
    token = _token(ephemeral_keys, exp=int(time.time()) - 5)
    with pytest.raises(GateDenied):
        gate.request_approval(
            user_id="user-1",
            agent_id="agen-puspa",
            token=token,
            kind="send",
            content={"recipient": "a@contoh.id"},
        )


def test_request_unknown_kind_denied(ephemeral_keys, stub_log):
    gate = _gate(ephemeral_keys, stub_log)
    with pytest.raises(GateDenied):
        gate.request_approval(
            user_id="user-1",
            agent_id="agen-puspa",
            token=_token(ephemeral_keys),
            kind="phone",
            content={},
        )


# --- Persetujuan terikat isi ----------------------------------------------


def test_changed_content_voids_approval(ephemeral_keys, stub_log):
    gate = _gate(ephemeral_keys, stub_log)
    req = gate.request_approval(
        user_id="user-1",
        agent_id="agen-puspa",
        token=_token(ephemeral_keys),
        kind="prepare_payment",
        content={"purpose": "PLN", "nominal": 50000},
    )
    gate.approve(req.request_id, "user-1")
    # Isi diubah setelah disetujui (mis. nominal diganti).
    req.content["nominal"] = 500000
    calls = []
    with pytest.raises(GateDenied):
        gate.execute(req.request_id, executor=calls.append)
    assert calls == []
    assert any(j == "approval_rejected" for j, _ in stub_log.events)


# --- Eksekusi tepat sekali -------------------------------------------------


def test_double_execute_runs_once(ephemeral_keys, stub_log):
    gate = _gate(ephemeral_keys, stub_log)
    req = gate.request_approval(
        user_id="user-1",
        agent_id="agen-puspa",
        token=_token(ephemeral_keys),
        kind="create_event",
        content={"title": "Rapat", "created_at": "2026-11-20T10:00:00+07:00"},
    )
    gate.approve(req.request_id, "user-1")
    calls = []

    def executor(content):
        calls.append(dict(content))
        return {"event_id": "evt-1"}

    assert gate.execute(req.request_id, executor=executor) == {"event_id": "evt-1"}
    assert gate.execute(req.request_id, executor=executor) == {"event_id": "evt-1"}
    assert gate.execute(req.request_id, executor=executor) == {"event_id": "evt-1"}
    assert len(calls) == 1


# --- Batas belanja (cek independen dari persetujuan) ------------------------


def test_spending_limit_default_zero_blocks_payment(ephemeral_keys, stub_log):
    spending = SpendingLimit("user-1")
    assert spending.get_limit() == 0
    assert spending.check("user-1", 1) is False
    assert spending.check("user-1", 0) is True

    gate = _gate(ephemeral_keys, stub_log, spending=spending)
    req = gate.request_approval(
        user_id="user-1",
        agent_id="agen-puspa",
        token=_token(ephemeral_keys),
        kind="prepare_payment",
        content={"purpose": "PLN", "nominal": 50000},
    )
    gate.approve(req.request_id, "user-1")
    calls = []
    with pytest.raises(GateDenied):
        gate.execute(req.request_id, executor=calls.append)
    assert calls == []


def test_spending_limit_raised_allows_payment(ephemeral_keys, stub_log):
    spending = SpendingLimit("user-1")
    spending.set_limit("user-1", 100000)
    assert spending.check("user-1", 50000) is True
    assert spending.check("user-1", 100001) is False

    gate = _gate(ephemeral_keys, stub_log, spending=spending)
    req = gate.request_approval(
        user_id="user-1",
        agent_id="agen-puspa",
        token=_token(ephemeral_keys),
        kind="prepare_payment",
        content={"purpose": "PLN", "nominal": 50000},
    )
    gate.approve(req.request_id, "user-1")
    assert gate.execute(req.request_id, executor=lambda content: "paid_off") == "paid_off"


def test_approval_without_limit_still_blocked_and_vice_versa(ephemeral_keys, stub_log):
    # Limit tinggi tapi tanpa persetujuan: tetap diblokir.
    spending = SpendingLimit("user-1")
    spending.set_limit(100000)
    gate = _gate(ephemeral_keys, stub_log, spending=spending)
    req = gate.request_approval(
        user_id="user-1",
        agent_id="agen-puspa",
        token=_token(ephemeral_keys),
        kind="prepare_payment",
        content={"purpose": "PLN", "nominal": 1000},
    )
    calls = []
    with pytest.raises(GateDenied):
        gate.execute(req.request_id, executor=calls.append)
    assert calls == []


def test_spending_set_limit_forms():
    s = SpendingLimit("user-9")
    assert s.set_limit(5000) == 5000
    assert s.get_limit() == 5000
    assert s.check(5000) is True
    assert s.check(5001) is False
    s.record_spend(2000)
    assert s.spent_in_period() == 2000
    assert s.check(3000) is True
    assert s.check(3001) is False


# --- Token dicabut ----------------------------------------------------------


def test_revoked_token_rejected_at_execute(ephemeral_keys, stub_log):
    gate = _gate(ephemeral_keys, stub_log)
    token = _token(ephemeral_keys)
    req = gate.request_approval(
        user_id="user-1",
        agent_id="agen-puspa",
        token=token,
        kind="send",
        content={"recipient": "a@contoh.id"},
    )
    gate.approve(req.request_id, "user-1")
    revoke(token_id(token))
    calls = []
    with pytest.raises(GateDenied):
        gate.execute(req.request_id, executor=calls.append)
    assert calls == []


def test_revoked_token_rejected_at_request(ephemeral_keys, stub_log):
    gate = _gate(ephemeral_keys, stub_log)
    token = _token(ephemeral_keys)
    revoke(token)
    with pytest.raises(GateDenied):
        gate.request_approval(
            user_id="user-1",
            agent_id="agen-puspa",
            token=token,
            kind="send",
            content={"recipient": "a@contoh.id"},
        )


def test_gate_without_public_key_rejects_string_token(ephemeral_keys, stub_log):
    gate = Gate(log=stub_log, spending=None)  # tanpa public_key
    with pytest.raises(GateDenied):
        gate.request_approval(
            user_id="user-1",
            agent_id="agen-puspa",
            token=_token(ephemeral_keys),
            kind="send",
            content={"recipient": "a@contoh.id"},
        )
