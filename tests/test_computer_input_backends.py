"""Tests for the real Linux input + window backends (V2-33).

This VM is headless: no display, no accessibility bus, no
libXtst, and no clipboard tool (no xclip, xsel, or wl-copy).
So these tests cover the fail-closed half: every real backend
reports itself unavailable and refuses instead of pretending
an action worked. What IS present is checked directly: libX11
loads, libatspi binds the editable-text symbols the typing
path needs, and the backend seam rejects unknown kinds and
non-Linux platforms. The live half (typing into a real app,
XTest keys, a real window switch) is unverified here and waits
for a Linux desktop session; see docs/computer-hands.md.
"""

import ctypes

import pytest

from puspa.computer.backend import BackendUnavailableError
from puspa.computer.input import (
    XTestInputBackend,
    _bind_editable_text,
    _load_atspi_libs,
    _load_xtest_libs,
    make_input_backend,
)
from puspa.computer.tree import AccessibleNode
from puspa.computer.windows import AtspiWindowBackend, make_window_backend

pytestmark = pytest.mark.unit

_NO_SESSION_ENV = {
    "AT_SPI_BUS_ADDRESS": "",
    "DBUS_SESSION_BUS_ADDRESS": "",
    "DISPLAY": "",
    "WAYLAND_DISPLAY": "",
}


@pytest.fixture
def no_session(monkeypatch):
    for key in _NO_SESSION_ENV:
        monkeypatch.setenv(key, "")


def _node():
    return AccessibleNode(ref="n1", path=(0,), role="text", name="Body", value="")


# -- XTest input backend --------------------------------------------


def test_xtest_backend_unavailable_without_a_display(no_session):
    assert XTestInputBackend().is_available() is False


def test_xtest_type_without_bus_fails_closed(no_session):
    with pytest.raises(BackendUnavailableError, match="no accessibility bus"):
        XTestInputBackend().type_text(_node(), "hello")


def test_xtest_key_without_display_fails_closed(no_session):
    with pytest.raises(BackendUnavailableError, match="no display"):
        XTestInputBackend().key(("ctrl", "a"))


def test_xtest_scroll_without_display_fails_closed(no_session):
    with pytest.raises(BackendUnavailableError, match="no display"):
        XTestInputBackend().scroll(_node(), "down", 1)


def test_xtest_drag_without_display_fails_closed(no_session):
    with pytest.raises(BackendUnavailableError, match="no display"):
        XTestInputBackend().drag(_node(), _node())


def test_libxtst_is_missing_on_this_vm():
    # libX11 is installed, libXtst is not: the loader must say
    # so plainly instead of crashing on a missing symbol.
    assert ctypes.CDLL("libX11.so.6") is not None
    with pytest.raises(BackendUnavailableError, match="XTest libraries not found"):
        _load_xtest_libs()


def test_libatspi_binds_the_editable_text_symbols():
    # The typing path needs these; binding them proves the
    # ctypes declarations match the installed libatspi, even
    # though no desktop is reachable to type into.
    libs = _load_atspi_libs()
    _bind_editable_text(libs)
    assert libs.atspi.atspi_accessible_get_editable_text is not None
    assert libs.atspi.atspi_editable_text_insert_text is not None
    assert libs.atspi.atspi_accessible_get_text is not None
    assert libs.atspi.atspi_text_get_character_count is not None
    assert libs.atspi.atspi_component_grab_focus is not None


def test_xtest_clipboard_without_any_tool_fails_closed(no_session, monkeypatch):
    monkeypatch.setattr("puspa.computer.input.shutil.which", lambda name: None)
    with pytest.raises(BackendUnavailableError, match="no X11 clipboard tool"):
        XTestInputBackend().clipboard_read()
    with pytest.raises(BackendUnavailableError, match="no X11 clipboard tool"):
        XTestInputBackend().clipboard_write("hello")


def test_xtest_clipboard_prefers_wayland_tools(monkeypatch):
    # With a Wayland session but no wl-clipboard installed, the
    # error must name the Wayland tools, not the X11 ones.
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-1")
    monkeypatch.setenv("DISPLAY", "")
    monkeypatch.setattr("puspa.computer.input.shutil.which", lambda name: None)
    with pytest.raises(BackendUnavailableError, match="no Wayland clipboard tool"):
        XTestInputBackend().clipboard_read()


# -- AT-SPI window backend ------------------------------------------


def test_window_backend_unavailable_without_a_bus(no_session):
    assert AtspiWindowBackend().is_available() is False


def test_window_list_without_bus_fails_closed(no_session):
    with pytest.raises(BackendUnavailableError, match="no accessibility bus"):
        AtspiWindowBackend().list_windows()


def test_window_open_without_session_fails_closed(no_session):
    with pytest.raises(BackendUnavailableError, match="no desktop session"):
        AtspiWindowBackend().open_app("gedit")


def test_window_focus_without_bus_fails_closed(no_session):
    with pytest.raises(BackendUnavailableError, match="no accessibility bus"):
        AtspiWindowBackend().focus_window("0.0")


# -- seams ----------------------------------------------------------


def test_make_input_backend_fake_needs_a_fake_backend():
    with pytest.raises(BackendUnavailableError, match="needs a FakeBackend"):
        make_input_backend("fake")


def test_make_input_backend_auto_is_xtest_on_linux():
    assert isinstance(make_input_backend("auto"), XTestInputBackend)


def test_make_input_backend_unknown_kind_fails_closed():
    with pytest.raises(BackendUnavailableError, match="unknown input backend"):
        make_input_backend("uinput")


def test_make_input_backend_unknown_os_fails_closed(monkeypatch):
    import puspa.computer.input as input_module

    monkeypatch.setattr(input_module.sys, "platform", "darwin")
    with pytest.raises(BackendUnavailableError, match="Linux-only"):
        make_input_backend("auto")


def test_make_window_backend_fake_needs_a_fake_backend():
    with pytest.raises(BackendUnavailableError, match="needs a FakeBackend"):
        make_window_backend("fake")


def test_make_window_backend_auto_is_atspi_on_linux():
    assert isinstance(make_window_backend("auto"), AtspiWindowBackend)


def test_make_window_backend_unknown_kind_fails_closed():
    with pytest.raises(BackendUnavailableError, match="unknown window backend"):
        make_window_backend("win32")


def test_make_window_backend_unknown_os_fails_closed(monkeypatch):
    import puspa.computer.windows as windows_module

    monkeypatch.setattr(windows_module.sys, "platform", "win32")
    with pytest.raises(BackendUnavailableError, match="Linux-only"):
        make_window_backend("auto")
