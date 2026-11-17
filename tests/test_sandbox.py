# Tes sandbox fail-closed + allowlist.
# Pemetaan: definisi 2.6 (Sandbox fail-closed) dan CONTRACTS.md bagian
# "Sandbox fail-closed". Prinsip: bila Docker tidak bisa mengeksekusi,
# run() raise SandboxUnavailable dan TIDAK ADA kode yang jalan di host.

import os

import pytest

from puspa.sandbox import Allowlist, Sandbox, SandboxUnavailable, is_allowed
from puspa.sandbox.allowlist import DEFAULT_ALLOWLIST

PROBE_HOST = "/tmp/puspa-host-exec-probe.txt"
KODE_TULIS_HOST = (
    f"open({PROBE_HOST!r}, 'w').write('host-executed')\nprint('tulis-host')"
)


# --- allowlist ---------------------------------------------------------------


def test_allowlist_terima_domain_default():
    assert is_allowed("https://www.tokopedia.com/barang/123")
    assert is_allowed("shopee.co.id")
    assert is_allowed("https://blibli.com/x")
    assert is_allowed("https://www.pln.co.id/tagihan")
    assert is_allowed("https://api.openai.com/v1/chat/completions")
    assert is_allowed("https://gmail.googleapis.com/gmail/v1/users/me/messages")
    assert "tokopedia.com" in DEFAULT_ALLOWLIST
    assert "pln.co.id" in DEFAULT_ALLOWLIST


def test_allowlist_tolak_domain_di_luar_daftar():
    assert not is_allowed("https://evil.com/x")
    assert not is_allowed("https://example.com/")
    assert not is_allowed("https://tokopedia.com.evil.com/x")
    assert not is_allowed("https://notokopedia.com/x")
    assert not is_allowed("https://shopee.co.id.evil.com/")
    assert not is_allowed("")


def test_allowlist_subdomain_diterima():
    assert is_allowed("https://api.tokopedia.com/v1")
    assert is_allowed("https://www.shopee.co.id/cart")


def test_allowlist_pdam_per_daerah():
    assert is_allowed("https://pdam-tirtamarta.co.id/tagihan")
    assert is_allowed("https://www.pdamkotamakassar.go.id/")
    assert not is_allowed("https://pdamphishing.com/")


def test_allowlist_localhost_hanya_untuk_test_double():
    assert not is_allowed("http://localhost:8000/x")
    assert not is_allowed("http://127.0.0.1:9000/x")
    test_only = Allowlist(allow_test_localhost=True)
    assert test_only.is_allowed("http://localhost:8000/x")
    assert test_only.is_allowed("http://127.0.0.1:9000/x")
    assert not test_only.is_allowed("https://evil.com/")


# --- fail-closed: Docker tidak tersedia --------------------------------------


def test_run_tanpa_binary_docker_raise_dan_tidak_mengeksekusi(monkeypatch, tmp_path):
    """PATH tanpa docker -> SandboxUnavailable; kode tulis-file tidak jalan."""
    monkeypatch.setenv("PATH", str(tmp_path))
    if os.path.exists(PROBE_HOST):
        os.remove(PROBE_HOST)
    sandbox = Sandbox()
    assert sandbox.check_available() is False
    with pytest.raises(SandboxUnavailable):
        sandbox.run(KODE_TULIS_HOST)
    assert not os.path.exists(PROBE_HOST)


def test_run_dengan_daemon_mati_raise_dan_tidak_mengeksekusi(monkeypatch):
    """DOCKER_HOST ke endpoint mati -> SandboxUnavailable; tanpa eksekusi."""
    monkeypatch.setenv("DOCKER_HOST", "tcp://127.0.0.1:9")
    if os.path.exists(PROBE_HOST):
        os.remove(PROBE_HOST)
    sandbox = Sandbox()
    assert sandbox.check_available() is False
    with pytest.raises(SandboxUnavailable):
        sandbox.run(KODE_TULIS_HOST)
    assert not os.path.exists(PROBE_HOST)


def test_run_validasi_input_tanpa_menyentuh_docker():
    sandbox = Sandbox()
    with pytest.raises(TypeError):
        sandbox.run(123)
    with pytest.raises(ValueError):
        sandbox.run("x" * 1_000_001)
    with pytest.raises(ValueError):
        sandbox.run("print(1)", timeout_s=0)


# --- Docker tersedia: eksekusi nyata di container ----------------------------


def test_run_print_ok_di_container():
    sandbox = Sandbox()
    if not sandbox.check_available():
        pytest.skip("Docker tidak bisa mengeksekusi di VM ini (fail-closed aktif)")
    hasil = sandbox.run('print("ok")')
    assert hasil["ok"] is True
    assert hasil["stdout"].strip() == "ok"
    assert hasil["exit_code"] == 0
    assert hasil["timed_out"] is False


def test_run_kode_gagal_tetap_hasil_bukan_unavailable():
    sandbox = Sandbox()
    if not sandbox.check_available():
        pytest.skip("Docker tidak bisa mengeksekusi di VM ini (fail-closed aktif)")
    hasil = sandbox.run("raise RuntimeError('boom')")
    assert hasil["ok"] is False
    assert hasil["exit_code"] == 1
    assert "RuntimeError" in hasil["stderr"]


def test_run_jaringan_ke_domain_luar_allowlist_ditolak():
    sandbox = Sandbox()
    if not sandbox.check_available():
        pytest.skip("Docker tidak bisa mengeksekusi di VM ini (fail-closed aktif)")
    kode = (
        "import urllib.request\n"
        "urllib.request.urlopen('https://example.com/', timeout=5)\n"
        "print('jaringan-lolos')"
    )
    hasil = sandbox.run(kode)
    assert hasil["ok"] is False
    assert "jaringan-lolos" not in hasil["stdout"]


def test_run_tidak_bisa_menulis_ke_host():
    sandbox = Sandbox()
    if not sandbox.check_available():
        pytest.skip("Docker tidak bisa mengeksekusi di VM ini (fail-closed aktif)")
    if os.path.exists(PROBE_HOST):
        os.remove(PROBE_HOST)
    hasil = sandbox.run(KODE_TULIS_HOST)
    assert hasil["ok"] is False
    assert not os.path.exists(PROBE_HOST)
