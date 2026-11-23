"""Local push port: store Web Push subscriptions, send nothing.

This module is the public side of the push boundary. The hosted
service implements VAPID delivery behind the same port name; the
local install only stores the subscription (endpoint + keys) in
its own Postgres database (table ``push_subscriptions``) and sends
nothing — there is no push server in a local install.

The local implementation operates on Postgres through
:func:`puspa.accounts.ports.resolve_dsn`. The SQLite backend is a
separate future workstream: when it lands, this port gains a
SQLite implementation behind the same name; nothing here pretends
SQLite is live today.

Deliberately NOT here: ``list_subscriptions``. No public caller
reads subscriptions back (delivery lives in the hosted service),
so it is not invented. If a local sender ever needs it, it
belongs here.
"""

from __future__ import annotations

import uuid

from puspa.db import connect as _db_connect

from puspa.accounts.ports import resolve_dsn

__all__ = ["save_subscription"]


def save_subscription(user_id: str, *, endpoint: str, p256dh: str, auth: str, dsn=None) -> str:
    """Store a user's Web Push subscription; return its id.

    The subscription is stored locally and nothing is sent. The id
    is ``"push-"`` plus 12 hex chars (not a database key the
    caller must know about).
    """
    subscription_id = f"push-{uuid.uuid4().hex[:12]}"
    with _db_connect(resolve_dsn(dsn)) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO push_subscriptions (id, user_id, endpoint, p256dh, auth)"
                " VALUES (%s, %s, %s, %s, %s)",
                (subscription_id, user_id, endpoint, p256dh, auth),
            )
        conn.commit()
    return subscription_id
