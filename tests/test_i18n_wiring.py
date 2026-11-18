"""Tests: server-lines actually wired into the surfaces users see (G18).

- PWA API errors come from server-lines (anonymous ``401`` and the
  approval ``404`` return the Indonesian sentences).
- Onboarding hints served by the API are the Indonesian server-lines
  copy (the old English copy is gone).
- Gate denials come from server-lines, byte-identical to before
  (except the fixed double-"belanja" typo — see docs/i18n.md).
"""

from __future__ import annotations

import re
import time

import pytest
from fastapi.testclient import TestClient

from puspa.i18n import render
from puspa.onboarding.hints import HINTS, due_hints, hint_by_flag
from puspa.policy import Gate, GateDenied, issue_token
from puspa.policy.tokens import _issued, _revoked
from puspa.pwa.app import create_app


@pytest.fixture(autouse=True)
def _reset_revocation():
    _revoked.clear()
    _issued.clear()
    yield
    _revoked.clear()
    _issued.clear()


# --- PWA API errors -------------------------------------------------------


def test_anonymous_api_returns_server_lines_not_logged_in():
    """Every anonymous API call answers ``belum masuk`` from the catalog."""
    app = create_app(test_user_id=None)
    with TestClient(app, raise_server_exceptions=False) as client:
        for method, url in [
            ("post", "/api/chat"),
            ("get", "/api/insights"),
            ("get", "/api/onboarding"),
            ("get", "/api/onboarding/hints"),
            ("get", "/api/model-settings"),
            ("get", "/sse"),
        ]:
            kwargs = {"data": {"text": "halo"}} if method == "post" else {}
            resp = getattr(client, method)(url, **kwargs)
            assert resp.status_code == 401, url
            assert resp.json()["error"] == "belum masuk", url
            assert resp.json()["error"] == render("error.not_logged_in")


def test_approval_unknown_request_returns_server_lines_404(make_user):
    user = make_user()
    app = create_app(test_user_id=user["user_id"])
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.post("/api/persetujuan/req-tidak-ada", data={"approve": "ya"})
        assert resp.status_code == 404
        assert resp.json()["error"] == "permintaan tidak dikenal"
        assert resp.json()["error"] == render("error.unknown_request")


# --- onboarding hints: Indonesian copy ------------------------------------


def test_hints_are_indonesian_server_lines():
    """No hint the server serves is in English any more."""
    for hint in HINTS:
        assert hint.title == render(f"hint.{hint.flag}.title")
        assert hint.body == render(
            f"hint.{hint.flag}.body",
            **({"ceiling": "Rp2.000"} if hint.flag == "first_costly_task" else {}),
        )
        assert hint.action_label == render(f"hint.{hint.flag}.action_label")
    assert hint_by_flag("first_modules_visit").title == "Modul menambah kemampuan"
    assert hint_by_flag("first_approval").title == "Puspa bertanya dulu sebelum bertindak"
    assert "Puspa" in hint_by_flag("first_memory_use").body
    assert "Rp2.000" in hint_by_flag("first_costly_task").body


def test_due_hints_serve_indonesian_copy(make_user, test_dsn):
    user = make_user()
    hints = due_hints(
        user["user_id"],
        context={"screen": "modules", "first_visit": True},
        dsn=test_dsn,
    )
    assert [hint["flag"] for hint in hints] == ["first_modules_visit"]
    assert hints[0]["title"] == "Modul menambah kemampuan"
    assert hints[0]["action_label"] == "Buka Modul"
    assert "Modul" in hints[0]["body"]


# --- gate denials ---------------------------------------------------------


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


def test_gate_denials_are_server_lines_sentences(ephemeral_keys):
    sk, pk = ephemeral_keys
    gate = Gate(spending=None, public_key=pk)
    token = issue_token(_payload(), sk)
    with pytest.raises(GateDenied, match=re.escape("token tidak mengizinkan tool 'x.tool'")):
        gate.authorize_tool(_payload(), "x.tool")
    with pytest.raises(GateDenied, match="kind aksi tidak dikenal: 'nope'"):
        gate.request_approval(
            user_id="user-1", agent_id="agen-puspa", token=token, kind="nope", content={}
        )
    req = gate.request_approval(
        user_id="user-1",
        agent_id="agen-puspa",
        token=token,
        kind="send",
        content={"recipient": "a@contoh.id"},
    )
    with pytest.raises(GateDenied, match="hanya pemilik user yang boleh menyetujui"):
        gate.approve(req.request_id, "user-2")
    with pytest.raises(GateDenied, match="permintaan tidak dikenal: 'req-tidak-ada'"):
        gate.approve("req-tidak-ada", "user-1")


def test_gate_spending_denial_uses_corrected_sentence(ephemeral_keys):
    """The double-'belanja' typo is gone; the denial is the catalog sentence."""
    sk, pk = ephemeral_keys
    gate = Gate(spending=None, public_key=pk)
    token = issue_token(_payload(), sk)
    req = gate.request_approval(
        user_id="user-1",
        agent_id="agen-puspa",
        token=token,
        kind="prepare_payment",
        content={"nominal": 1000},
    )
    gate.approve(req.request_id, "user-1")
    with pytest.raises(GateDenied) as excinfo:
        gate.execute(req.request_id, executor=lambda content: None)
    assert str(excinfo.value) == "tidak ada penegak batas belanja; pembayaran ditolak"
    assert str(excinfo.value) == render("error.no_spending_enforcer")
    assert "belanja belanja" not in str(excinfo.value)


def test_gate_expired_token_denial_is_server_lines(ephemeral_keys):
    sk, pk = ephemeral_keys
    gate = Gate(spending=None, public_key=pk)
    expired = issue_token(_payload(exp=int(time.time()) - 10), sk)
    with pytest.raises(GateDenied, match="token sudah kedaluwarsa"):
        gate.request_approval(
            user_id="user-1",
            agent_id="agen-puspa",
            token=expired,
            kind="send",
            content={"recipient": "a@contoh.id"},
        )
