"""Per-user connector accounts and per-module account choice (G10).

Account sync: one connector account (for example one Tokopedia
account, or one PLN customer number) belongs to a user and can be
used by several of that user's bots or modules. The account record
holds no secret: credentials live sealed in the vault and only a
``credentials_ref`` (the vault entry name) is stored here.

Per-module account choice (flagship, with the allowlist): a module
chooses *which* account of a connector it uses, instead of a single
global default account. Resolution order is explicit:

1. the module's own choice for that connector, when set;
2. the caller's explicit default account, when given;
3. the user's first linked account for that connector, when any;
4. ``None`` (no account linked yet).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

__all__ = [
    "ConnectorAccount",
    "ConnectorAccountStore",
    "ModuleAccountSelection",
    "AccountError",
]


class AccountError(ValueError):
    """An account or account choice is invalid or unknown."""


@dataclass(frozen=True)
class ConnectorAccount:
    """One linked connector account. No secret is stored here."""

    account_id: str
    user_id: str
    connector_id: str
    label: str
    credentials_ref: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class ConnectorAccountStore:
    """In-memory store of linked connector accounts, per user.

    This store is the sync layer: linking once makes the account
    available to every bot and module of that user. Persistence is a
    follow-up (a table mirroring ``agent_connectors``); the store is
    deliberately in-memory so the account-choice logic is testable
    without a database.
    """

    def __init__(self):
        self._accounts: dict[str, ConnectorAccount] = {}

    def link(self, user_id, connector_id, label, *, credentials_ref=None):
        """Link a new account; return the created record."""
        if not isinstance(label, str) or not label.strip():
            raise AccountError("account label must be a non-empty string")
        if not isinstance(connector_id, str) or not connector_id:
            raise AccountError("connector_id must be a non-empty string")
        account = ConnectorAccount(
            account_id=uuid.uuid4().hex[:16],
            user_id=str(user_id),
            connector_id=connector_id,
            label=label,
            credentials_ref=credentials_ref,
        )
        self._accounts[account.account_id] = account
        return account

    def get(self, account_id):
        """Return the account (KeyError if absent)."""
        return self._accounts[account_id]

    def list_accounts(self, user_id, connector_id=None):
        """Accounts of one user, optionally for one connector."""
        return [
            account
            for account in self._accounts.values()
            if account.user_id == str(user_id)
            and (connector_id is None or account.connector_id == connector_id)
        ]

    def unlink(self, user_id, account_id):
        """Remove one account. Idempotent; also drops module choices
        pointing at it (the selection object must be told via
        ``drop_account`` on :class:`ModuleAccountSelection`)."""
        account = self._accounts.get(account_id)
        if account is None or account.user_id != str(user_id):
            return False
        del self._accounts[account_id]
        return True


class ModuleAccountSelection:
    """Which connector account each module uses (per-module choice).

    A module is identified by its module id (for example a Puspa
    module or bot id). Choices are validated against the account
    store at set time: a module can only choose an account that
    exists and belongs to the same user.
    """

    def __init__(self):
        self._choices: dict[tuple[str, str, str], str] = {}

    def set_choice(self, module_id, user_id, connector_id, account_id, store):
        """Set a module's account for one connector.

        Raises :class:`AccountError` when the account does not
        exist or belongs to another user.
        """
        try:
            account = store.get(account_id)
        except KeyError:
            raise AccountError(f"unknown account {account_id!r}") from None
        if account.user_id != str(user_id):
            raise AccountError("account belongs to another user")
        if account.connector_id != connector_id:
            raise AccountError(
                f"account {account_id!r} is for connector "
                f"{account.connector_id!r}, not {connector_id!r}"
            )
        key = (str(module_id), str(user_id), connector_id)
        self._choices[key] = account_id
        return account_id

    def clear_choice(self, module_id, user_id, connector_id):
        """Remove a module's choice (falls back to the default)."""
        self._choices.pop((str(module_id), str(user_id), connector_id), None)

    def drop_account(self, account_id):
        """Drop every choice pointing at an unlinked account."""
        doomed = [
            key for key, value in self._choices.items() if value == account_id
        ]
        for key in doomed:
            del self._choices[key]

    def choice(self, module_id, user_id, connector_id):
        """A module's explicit choice, or None when unset."""
        return self._choices.get((str(module_id), str(user_id), connector_id))

    def resolve(self, module_id, user_id, connector_id, store, *, default_account_id=None):
        """Resolve the account a module uses for a connector.

        Order: module choice, explicit default, first linked
        account of the user for that connector, else None. The
        explicit default is validated the same way as a choice.
        """
        chosen = self.choice(module_id, user_id, connector_id)
        if chosen is not None:
            return chosen
        if default_account_id is not None:
            try:
                account = store.get(default_account_id)
            except KeyError:
                raise AccountError(
                    f"unknown default account {default_account_id!r}"
                ) from None
            if account.user_id != str(user_id):
                raise AccountError("default account belongs to another user")
            if account.connector_id != connector_id:
                raise AccountError(
                    f"default account is for connector "
                    f"{account.connector_id!r}, not {connector_id!r}"
                )
            return default_account_id
        accounts = store.list_accounts(user_id, connector_id)
        if accounts:
            return accounts[0].account_id
        return None

    def choices_for_module(self, module_id, user_id):
        """All of one module's choices as {connector_id: account_id}."""
        return {
            connector_id: account_id
            for (mid, uid, connector_id), account_id in self._choices.items()
            if mid == str(module_id) and uid == str(user_id)
        }
