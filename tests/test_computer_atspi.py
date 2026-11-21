"""Tests for the real Linux AT-SPI backend (V2-31).

This VM is headless: no accessibility bus, no display, no
pyatspi, no dbus-python. So these tests cover the fail-closed
half: the backend reports itself unavailable, and every call
refuses with BackendUnavailableError instead of pretending an
action worked. The live half (a real tree read + click on a
desktop session) is unverified here and waits for a machine
with a desktop; see docs/computer-hands.md.
"""

import pytest

from puspa.computer.backend import (
    AtspiBackend,
    BackendUnavailableError,
    _load_atspi_libs,
    make_backend,
)

pytestmark = pytest.mark.unit

_NO_BUS_ENV = {
    "AT_SPI_BUS_ADDRESS": "",
    "DBUS_SESSION_BUS_ADDRESS": "",
    "DISPLAY": "",
    "WAYLAND_DISPLAY": "",
}


@pytest.fixture
def no_bus(monkeypatch):
    for key in _NO_BUS_ENV:
        monkeypatch.setenv(key, "")


def test_no_bus_means_unavailable(no_bus):
    assert AtspiBackend().is_available() is False


def test_snapshot_without_bus_fails_closed(no_bus):
    with pytest.raises(BackendUnavailableError, match="no accessibility bus"):
        AtspiBackend().snapshot()


def test_click_without_bus_fails_closed(no_bus):
    from puspa.computer.tree import AccessibleNode

    node = AccessibleNode(ref="n1", path=(0,), role="push button", name="X")
    with pytest.raises(BackendUnavailableError, match="no accessibility bus"):
        AtspiBackend().click(node)


def test_libatspi_loads_and_binds_its_symbols():
    # The library IS installed on this VM; only the bus is missing.
    # Binding the symbols proves the ctypes declarations match the
    # installed libatspi, even though no desktop is reachable.
    libs = _load_atspi_libs()
    assert libs.atspi is not None
    assert libs.gobject is not None
    assert libs.glib is not None


def test_make_backend_fake_is_the_in_memory_tree():
    backend = make_backend("fake")
    assert backend.is_available() is True
    assert backend.snapshot().backend == "fake"


def test_make_backend_auto_is_atspi_on_linux():
    assert isinstance(make_backend("auto"), AtspiBackend)


def test_make_backend_unknown_kind_fails_closed():
    with pytest.raises(BackendUnavailableError, match="unknown desktop backend"):
        make_backend("wayland-direct")


def test_make_backend_unknown_os_fails_closed(monkeypatch):
    import puspa.computer.backend as backend_module

    monkeypatch.setattr(backend_module.sys, "platform", "darwin")
    with pytest.raises(BackendUnavailableError, match="Linux-only"):
        make_backend("auto")
