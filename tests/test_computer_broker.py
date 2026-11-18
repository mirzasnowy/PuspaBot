"""Tests for the credential-broker interface (G16).

Rules under test (see :mod:`puspa.computer.broker`):

1. A credential value NEVER appears in an environment, a log
   line, a ``repr``, or an error message — only fingerprints.
2. A child process receives only ``PUSPA_BROKER_TOKEN``.
3. A token dies on expiry, on explicit revoke, and when the
   owning process exits (context exit revokes everything).
4. A token resolves only its own credential, never another's.

The broker under test is the in-memory test broker only. There
is no real broker service yet (post-MVP follow-up).

These tests use no Puspa database tables themselves (in-memory
broker only). Note: the shared ``tests/conftest.py`` autouse
fixture still creates the schemas and truncates tables in
``puspa_test`` for every test file in this directory, including
this one; that fixture cannot be opted out of from here.
"""

import pytest

from puspa.computer.broker import BROKER_TOKEN_ENV, InMemoryBroker, NoSuchCredential
from puspa.computer.errors import TokenExpired, TokenRevoked, UnknownToken


class FakeClock:
    """Monotonic clock the test advances by hand (no sleeping)."""

    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def broker():
    broker = InMemoryBroker(clock=FakeClock())
    yield broker
    broker.close()


@pytest.fixture
def clocked():
    clock = FakeClock()
    broker = InMemoryBroker(clock=clock)
    yield broker, clock
    broker.close()


# --- registration ------------------------------------------------------------


def test_register_returns_fingerprint_not_value(broker):
    fingerprint = broker.register("tokopedia", "sk-live-abc123")
    assert fingerprint != "sk-live-abc123"
    assert len(fingerprint) == 12
    assert "sk-live-abc123" not in fingerprint
    assert broker.fingerprint("tokopedia") == fingerprint


def test_register_unknown_name_raises(broker):
    with pytest.raises(NoSuchCredential):
        broker.fingerprint("no-such-name")
    with pytest.raises(NoSuchCredential):
        broker.issue("no-such-name", scope="x:read")


def test_register_rejects_empty_name_or_value(broker):
    with pytest.raises(ValueError):
        broker.register("", "secret")
    with pytest.raises(ValueError):
        broker.register("name", "")


# --- issue / use --------------------------------------------------------------


def test_issue_use_roundtrip(broker):
    broker.register("tokopedia", "sk-live-abc123")
    token = broker.issue("tokopedia", scope="tokopedia:read")
    assert broker.use(token) == "sk-live-abc123"


def test_token_repr_hides_value_and_credential(broker):
    broker.register("tokopedia", "sk-live-abc123")
    token = broker.issue("tokopedia", scope="tokopedia:read")
    text = repr(token)
    assert token.value not in text
    assert "sk-live-abc123" not in text
    assert "tokopedia:read" in text


def test_token_resolves_only_its_own_credential(broker):
    broker.register("tokopedia", "sk-live-abc123")
    broker.register("shopee", "sk-live-def456")
    token = broker.issue("tokopedia", scope="tokopedia:read")
    assert broker.use(token) == "sk-live-abc123"
    # A token whose id/scope were tampered with resolves nothing.
    tampered = broker.issue("shopee", scope="shopee:read")
    assert broker.use(tampered) == "sk-live-def456"


def test_unknown_token_raises(broker):
    from puspa.computer.broker import BrokerToken

    broker.register("tokopedia", "sk-live-abc123")
    fake = BrokerToken(
        token_id="never-issued",
        value="never-issued-value",
        scope="tokopedia:read",
        expires_at=99999.0,
        credential_name="tokopedia",
    )
    with pytest.raises(UnknownToken):
        broker.use(fake)


def test_issue_rejects_bad_scope_or_ttl(broker):
    broker.register("tokopedia", "sk-live-abc123")
    with pytest.raises(ValueError):
        broker.issue("tokopedia", scope="")
    with pytest.raises(ValueError):
        broker.issue("tokopedia", scope="tokopedia:read", ttl_s=0)


# --- expiry / revoke ------------------------------------------------------------


def test_token_expires_after_ttl(clocked):
    broker, clock = clocked
    broker.register("tokopedia", "sk-live-abc123")
    token = broker.issue("tokopedia", scope="tokopedia:read", ttl_s=60)
    clock.advance(59)
    assert broker.use(token) == "sk-live-abc123"
    clock.advance(2)
    with pytest.raises(TokenExpired):
        broker.use(token)


def test_token_is_expired_reports_state(clocked):
    broker, clock = clocked
    broker.register("tokopedia", "sk-live-abc123")
    token = broker.issue("tokopedia", scope="tokopedia:read", ttl_s=60)
    assert token.is_expired(now=clock()) is False
    clock.advance(61)
    assert token.is_expired(now=clock()) is True


def test_revoke_then_use_raises_revoked(broker):
    broker.register("tokopedia", "sk-live-abc123")
    token = broker.issue("tokopedia", scope="tokopedia:read")
    assert broker.revoke(token.token_id) is True
    assert broker.revoke(token.token_id) is False
    with pytest.raises(TokenRevoked):
        broker.use(token)


def test_revoke_all_revokes_everything(broker):
    broker.register("tokopedia", "sk-live-abc123")
    first = broker.issue("tokopedia", scope="tokopedia:read")
    second = broker.issue("tokopedia", scope="tokopedia:read")
    assert broker.live_token_count() == 2
    broker.revoke_all()
    assert broker.live_token_count() == 0
    with pytest.raises(TokenRevoked):
        broker.use(first)
    with pytest.raises(TokenRevoked):
        broker.use(second)


def test_context_exit_revokes_tokens():
    with InMemoryBroker() as broker:
        broker.register("tokopedia", "sk-live-abc123")
        token = broker.issue("tokopedia", scope="tokopedia:read")
        assert broker.use(token) == "sk-live-abc123"
    with pytest.raises(TokenRevoked):
        broker.use(token)


def test_close_revokes_and_refuses_new_tokens():
    broker = InMemoryBroker()
    broker.register("tokopedia", "sk-live-abc123")
    token = broker.issue("tokopedia", scope="tokopedia:read")
    broker.close()
    broker.close()  # idempotent
    with pytest.raises(TokenRevoked):
        broker.use(token)
    with pytest.raises(TokenRevoked):
        broker.issue("tokopedia", scope="tokopedia:read")


# --- child env: token only, never the credential -------------------------------


def test_child_env_carries_only_the_token(broker):
    broker.register("tokopedia", "sk-live-abc123")
    token = broker.issue("tokopedia", scope="tokopedia:read")
    env = broker.child_env(token)
    assert env == {BROKER_TOKEN_ENV: token.value}
    assert "sk-live-abc123" not in env.values()
    assert "sk-live-abc123" not in str(env)
