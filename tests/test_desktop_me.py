"""GET /api/me (PWA parity, M8-fix).

The native app shows the logged-in user's real email and credit
allowance in the sidebar and on Saya — no demo data. The email comes
from the session's user row; the allowance is the free tier's monthly
credits from the billing config.
"""

from fastapi.testclient import TestClient

from puspa.billing.config import FREE_CREDITS_PER_MONTH
from puspa.pwa.app import create_app


def test_me_requires_login():
    app = create_app(test_user_id=None)
    with TestClient(app) as client:
        resp = client.get("/api/me")
        assert resp.status_code == 401


def test_me_returns_email_and_credit_allowance(make_user):
    user = make_user()
    app = create_app(test_user_id=user["user_id"])
    with TestClient(app) as client:
        resp = client.get("/api/me")
        assert resp.status_code == 200
        assert resp.json() == {
            "email": user["email"],
            "credits_monthly": FREE_CREDITS_PER_MONTH,
        }
        assert FREE_CREDITS_PER_MONTH == 15


def test_me_without_user_row_returns_null_email():
    app = create_app(test_user_id="00000000-0000-0000-0000-000000000000")
    with TestClient(app) as client:
        resp = client.get("/api/me")
        assert resp.status_code == 200
        assert resp.json()["email"] is None
        assert resp.json()["credits_monthly"] == FREE_CREDITS_PER_MONTH
