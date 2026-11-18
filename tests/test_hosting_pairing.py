"""Tests: pairing codes, 30-day sessions, address lockout.

All in-memory with a fake clock; no database, no network.
"""

import hashlib

import pytest

from puspa.hosting.errors import AddressLockedError, PairingError
from puspa.hosting.pairing import (
    PAIRING_CODE_TTL_SECONDS,
    PAIRING_LOCKOUT_SECONDS,
    PAIRING_MAX_FAILURES,
    PAIRING_SESSION_TTL_SECONDS,
    PairingStore,
    _sha256_hex,
)


class FakeClock:
    def __init__(self, now=1_700_000_000.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def _store():
    return PairingStore(clock=FakeClock())


def _wrong_code(issued: str) -> str:
    """A 6-digit code guaranteed different from the issued one.

    The issued code is random (``secrets.randbelow(1_000_000)``), so
    a hardcoded "wrong" code can coincide with it once in a million
    runs and make the test flaky; derive the wrong code instead.
    """
    return "000000" if issued != "000000" else "000001"


def test_issue_returns_six_digit_code_with_five_minute_ttl():
    clock = FakeClock()
    store = PairingStore(clock=clock)
    issued = store.issue("user-1", "headless-server")
    assert len(issued["code"]) == 6
    assert issued["code"].isdigit()
    assert issued["expires_at"] == clock.now + PAIRING_CODE_TTL_SECONDS


def test_issue_rejects_empty_user_or_label():
    store = _store()
    with pytest.raises(PairingError):
        store.issue("", "device")
    with pytest.raises(PairingError):
        store.issue("user-1", "  ")


def test_verify_mints_thirty_day_session():
    clock = FakeClock()
    store = PairingStore(clock=clock)
    code = store.issue("user-1", "headless-server")["code"]
    session = store.verify(code, address="10.0.0.1")
    assert session["user_id"] == "user-1"
    assert session["device_label"] == "headless-server"
    assert session["expires_at"] == clock.now + PAIRING_SESSION_TTL_SECONDS
    assert session["session_token"]
    record = store.validate_session(session["session_token"])
    assert record["user_id"] == "user-1"


def test_code_is_single_use():
    store = _store()
    code = store.issue("user-1", "device")["code"]
    store.verify(code, address="10.0.0.1")
    with pytest.raises(PairingError):
        store.verify(code, address="10.0.0.1")


def test_wrong_code_counts_but_does_not_lock_before_five():
    store = _store()
    for _ in range(PAIRING_MAX_FAILURES - 1):
        with pytest.raises(PairingError):
            store.verify("000000", address="10.0.0.2")
    assert store.is_locked("10.0.0.2") is False


def test_five_wrong_codes_lock_the_address_for_ten_minutes():
    clock = FakeClock()
    store = PairingStore(clock=clock)
    for _ in range(PAIRING_MAX_FAILURES):
        with pytest.raises(PairingError):
            store.verify("000000", address="10.0.0.3")
    assert store.is_locked("10.0.0.3") is True
    assert store.locked_until("10.0.0.3") == clock.now + PAIRING_LOCKOUT_SECONDS
    with pytest.raises(AddressLockedError) as exc_info:
        store.verify("000000", address="10.0.0.3")
    assert exc_info.value.locked_until == clock.now + PAIRING_LOCKOUT_SECONDS
    # Another address is unaffected.
    assert store.is_locked("10.0.0.4") is False


def test_correct_code_cannot_be_used_while_address_locked():
    clock = FakeClock()
    store = PairingStore(clock=clock)
    code = store.issue("user-1", "device")["code"]
    for _ in range(PAIRING_MAX_FAILURES):
        with pytest.raises(PairingError):
            store.verify(_wrong_code(code), address="10.0.0.5")
    with pytest.raises(AddressLockedError):
        store.verify(code, address="10.0.0.5")
    # The lockout refuses without touching the code: its hash is still
    # stored (the lockout never burns a code). But the code's own
    # 5-minute TTL still runs, and the 10-minute lockout outlasts it,
    # so by the time the lock lifts the original code has expired and
    # a fresh code is needed.
    assert _sha256_hex(code) in store._codes
    clock.advance(PAIRING_LOCKOUT_SECONDS + 1)
    assert store.is_locked("10.0.0.5") is False
    with pytest.raises(PairingError):
        store.verify(code, address="10.0.0.5")
    fresh = store.issue("user-1", "device")["code"]
    session = store.verify(fresh, address="10.0.0.5")
    assert session["user_id"] == "user-1"


def test_failures_reset_after_a_successful_verify():
    store = _store()
    code = store.issue("user-1", "device")["code"]
    for _ in range(PAIRING_MAX_FAILURES - 1):
        with pytest.raises(PairingError):
            store.verify(_wrong_code(code), address="10.0.0.6")
    store.verify(code, address="10.0.0.6")
    # A fresh run of 4 wrong codes must not lock (counter was cleared).
    for _ in range(PAIRING_MAX_FAILURES - 1):
        with pytest.raises(PairingError):
            store.verify(_wrong_code(code), address="10.0.0.6")
    assert store.is_locked("10.0.0.6") is False


def test_expired_code_rejected():
    clock = FakeClock()
    store = PairingStore(clock=clock)
    code = store.issue("user-1", "device")["code"]
    clock.advance(PAIRING_CODE_TTL_SECONDS + 1)
    with pytest.raises(PairingError):
        store.verify(code, address="10.0.0.7")


def test_session_expires_after_thirty_days():
    clock = FakeClock()
    store = PairingStore(clock=clock)
    code = store.issue("user-1", "device")["code"]
    token = store.verify(code, address="10.0.0.8")["session_token"]
    clock.advance(PAIRING_SESSION_TTL_SECONDS - 1)
    assert store.validate_session(token)["user_id"] == "user-1"
    clock.advance(2)
    with pytest.raises(PairingError):
        store.validate_session(token)


def test_revoke_session_is_idempotent():
    store = _store()
    code = store.issue("user-1", "device")["code"]
    token = store.verify(code, address="10.0.0.9")["session_token"]
    store.revoke_session(token)
    store.revoke_session(token)
    with pytest.raises(PairingError):
        store.validate_session(token)


def test_only_hashes_are_stored_never_raw_codes_or_tokens():
    store = _store()
    code = store.issue("user-1", "device")["code"]
    assert hashlib.sha256(code.encode()).hexdigest() in store._codes
    assert code not in store._codes
    token = store.verify(code, address="10.0.0.10")["session_token"]
    assert hashlib.sha256(token.encode()).hexdigest() in store._sessions
    assert token not in store._sessions


def test_purge_expired_counts_what_it_drops():
    clock = FakeClock()
    store = PairingStore(clock=clock)
    code = store.issue("user-1", "device")["code"]
    token = store.verify(code, address="10.0.0.11")["session_token"]
    live_code = store.issue("user-1", "other")["code"]
    clock.advance(PAIRING_CODE_TTL_SECONDS + 1)
    counts = store.purge_expired()
    assert counts["codes"] == 1  # the live code expired too; the used one is gone
    assert counts["sessions"] == 0
    assert store.validate_session(token)["user_id"] == "user-1"
    with pytest.raises(PairingError):
        store.verify(live_code, address="10.0.0.11")
    clock.advance(PAIRING_SESSION_TTL_SECONDS)
    counts = store.purge_expired()
    assert counts["sessions"] == 1
    with pytest.raises(PairingError):
        store.validate_session(token)
