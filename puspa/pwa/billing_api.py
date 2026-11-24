"""HTTP layer for the billing plan (Space port: Paket & Harga).

One read-only endpoint: ``GET /api/billing/plan`` returns the
real tiers from :mod:`puspa.billing.config` (the same config the
trial and entitlement code reads), so the Paket screen never
shows invented prices or credit allowances. No purchase happens
here: paid checkout is not built, so the screen offers the real
trial instead.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ..billing.config import default_config
from ..i18n.server_lines import render as render_server_line

__all__ = ["register_routes"]


def _unauthorized():
    return JSONResponse({"error": render_server_line("error.not_logged_in")}, status_code=401)


def register_routes(app: FastAPI, need_user) -> FastAPI:
    """Mount the billing-plan route on ``app`` using its login resolver."""

    @app.get("/api/billing/plan")
    def billing_plan_endpoint(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return _unauthorized()
        config = default_config()
        return {
            "billing_model": config.billing_model,
            "tiers": [
                {
                    "id": tier.tier_id,
                    "price_idr_per_month": tier.price_idr_per_month,
                    "monthly_credits": tier.monthly_credits,
                }
                for tier in config.tiers
            ],
        }

    return app
