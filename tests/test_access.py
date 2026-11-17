"""Unit tes modul puspa.accounts.access — lapisan pemeriksaan kepemilikan
(definisi 2.6, jalur serangan ke-8).

Pemeriksaan kepemilikan terjadi SEBELUM data dibaca/didekripsi: tes di sini
membuktikan penolakan lintas pengguna terjadi tanpa dekripsi vault, tanpa
isi ledger terbaca, dan bahwa "data ada tapi bukan milikmu"
(AccessDeniedError) bisa dibedakan dari "data tidak ada" (KeyError).
"""

import uuid

import pytest

from puspa.accounts import (
    create_session,
    create_user,
    list_sessions,
    revoke_session,
    validate_session,
)
from puspa.accounts import vault as vault_mod
from puspa.accounts.access import (
    AccessDeniedError,
    create_agent_schema,
    owner_of,
    read_ledger_event,
    read_ledger_events_for_task,
    read_vault_entry,
    register_agent,
    require_owner,
    resolve_session_as,
)
from puspa.accounts.sessions import SessionError
from puspa.ledger import Ledger


@pytest.fixture(autouse=True)
def _agen_schema(test_dsn):
    """Tabel agen ada untuk tiap tes modul ini (conftest sudah membuat
    tabel pengguna/sesi/vault + TRUNCATE pengguna CASCADE, yang ikut
    mengosongkan agen karena FK)."""
    create_agent_schema(test_dsn)
    yield


def _dua_pengguna(test_dsn):
    uid_a = create_user("akses-a@example.com", "kata-sandi-a", dsn=test_dsn)
    uid_b = create_user("akses-b@example.com", "kata-sandi-b", dsn=test_dsn)
    return uid_a, uid_b


# ======================================================================
# require_owner — fail-closed
# ======================================================================


def test_require_owner_pemilik_sendiri_lolos():
    assert require_owner("user-1", "user-1") is None
    assert require_owner("  user-1  ", "user-1") is None  # spasi diabaikan


def test_require_owner_beda_pengguna_ditolak():
    with pytest.raises(AccessDeniedError):
        require_owner("user-A", "user-B")
    with pytest.raises(AccessDeniedError):
        require_owner("user-B", "user-A")


@pytest.mark.parametrize("buruk", [None, "", "   ", 123, ["user-1"]])
def test_require_owner_id_kosong_atau_bukan_string_ditolak(buruk):
    """Fail-closed: salah satu sisi None/kosong/bukan-string -> ditolak,
    bahkan kalau sisi lainnya valid."""
    with pytest.raises(AccessDeniedError):
        require_owner(buruk, "user-1")
    with pytest.raises(AccessDeniedError):
        require_owner("user-1", buruk)
    with pytest.raises(AccessDeniedError):
        require_owner(buruk, buruk)


# ======================================================================
# Registry agen
# ======================================================================


def test_register_agent_dan_owner_of(test_dsn):
    uid_a, uid_b = _dua_pengguna(test_dsn)
    assert owner_of("agen-belum-ada", dsn=test_dsn) is None
    assert owner_of(None, dsn=test_dsn) is None
    assert owner_of("", dsn=test_dsn) is None

    register_agent("agen-A", uid_a, dsn=test_dsn)
    register_agent("agen-B", uid_b, dsn=test_dsn)
    assert owner_of("agen-A", dsn=test_dsn) == uid_a
    assert owner_of("agen-B", dsn=test_dsn) == uid_b

    # Idempotent: daftar ulang ke pemilik yang sama tidak error.
    register_agent("agen-A", uid_a, dsn=test_dsn)
    assert owner_of("agen-A", dsn=test_dsn) == uid_a


def test_register_agent_ke_pemilik_lain_ditolak(test_dsn):
    """Agen tidak bisa dibajak/dipindah pemilik lewat registrasi ulang."""
    uid_a, uid_b = _dua_pengguna(test_dsn)
    register_agent("agen-A", uid_a, dsn=test_dsn)
    with pytest.raises(AccessDeniedError):
        register_agent("agen-A", uid_b, dsn=test_dsn)
    assert owner_of("agen-A", dsn=test_dsn) == uid_a


def test_register_agent_id_kosong_ditolak(test_dsn):
    uid_a, _uid_b = _dua_pengguna(test_dsn)
    with pytest.raises(ValueError):
        register_agent("", uid_a, dsn=test_dsn)
    with pytest.raises(ValueError):
        register_agent("agen-X", "", dsn=test_dsn)


# ======================================================================
# Pembaca vault bermilik
# ======================================================================


def test_read_vault_entry_pemilik_sendiri_lolos(test_dsn, vault_master_key):
    uid_a, _uid_b = _dua_pengguna(test_dsn)
    vault_mod.put(uid_a, "gmail_token", "token-rahasia-A", dsn=test_dsn)
    assert read_vault_entry(uid_a, uid_a, "gmail_token", dsn=test_dsn) == (
        "token-rahasia-A"
    )


def test_read_vault_entry_pengguna_lain_ditolak(test_dsn, vault_master_key):
    """Data B ADA, tapi A ditolak — dan penolakannya AccessDeniedError,
    bukan KeyError (yang berarti 'tidak ada')."""
    uid_a, uid_b = _dua_pengguna(test_dsn)
    vault_mod.put(uid_b, "gmail_token", "token-rahasia-B", dsn=test_dsn)
    # Bukti data B ada: B sendiri bisa membacanya.
    assert read_vault_entry(uid_b, uid_b, "gmail_token", dsn=test_dsn) == (
        "token-rahasia-B"
    )
    # A ditolak dengan error kepemilikan, bukan "tidak ada".
    with pytest.raises(AccessDeniedError):
        read_vault_entry(uid_a, uid_b, "gmail_token", dsn=test_dsn)


def test_read_vault_entry_tidak_ada_bukan_access_denied(
    test_dsn, vault_master_key
):
    """Pemilik membaca nama yang memang tidak ada -> KeyError, supaya
    'tidak ada' bisa dibedakan dari 'ditolak karena kepemilikan'."""
    uid_a, _uid_b = _dua_pengguna(test_dsn)
    with pytest.raises(KeyError):
        read_vault_entry(uid_a, uid_a, "nama-tidak-ada", dsn=test_dsn)


def test_read_vault_entry_lintas_pengguna_tanpa_dekripsi(
    test_dsn, vault_master_key, monkeypatch
):
    """Tes negatif penting: A membaca vault B -> ditolak SEBELUM dekripsi
    apa pun. Buktinya: _fernet (satu-satunya jalan ke dekripsi) tidak
    pernah dipanggil selama penolakan, padahal vault B berisi ciphertext
    valid yang bisa didekripsi B."""
    uid_a, uid_b = _dua_pengguna(test_dsn)
    vault_mod.put(uid_b, "gmail_token", "token-rahasia-B", dsn=test_dsn)

    panggilan = []

    fernet_asli = vault_mod._fernet

    def fernet_perekam(user_id):
        panggilan.append(user_id)
        return fernet_asli(user_id)

    monkeypatch.setattr(vault_mod, "_fernet", fernet_perekam)

    with pytest.raises(AccessDeniedError):
        read_vault_entry(uid_a, uid_b, "gmail_token", dsn=test_dsn)
    assert panggilan == [], "dekripsi (via _fernet) terjadi sebelum penolakan!"

    # Kontrol positif: B membaca miliknya sendiri tetap mendekripsi.
    assert read_vault_entry(uid_b, uid_b, "gmail_token", dsn=test_dsn) == (
        "token-rahasia-B"
    )
    assert panggilan == [uid_b]


# ======================================================================
# Resolusi sesi bermilik
# ======================================================================


def test_resolve_session_as_pemilik_sendiri_lolos(test_dsn):
    uid_a, _uid_b = _dua_pengguna(test_dsn)
    token_a = create_session(uid_a, "laptop-A", dsn=test_dsn)
    assert resolve_session_as(uid_a, token_a, dsn=test_dsn) == uid_a
    # validate_session mentah tetap memetakan token ke pemiliknya.
    assert validate_session(token_a, dsn=test_dsn) == uid_a


def test_resolve_session_as_token_pengguna_lain_ditolak(test_dsn):
    """A memakai token sesi B untuk mengaku sebagai A -> ditolak. Token B
    tetap valid untuk B (bukan token rusak), jadi penolakannya murni
    karena kepemilikan."""
    uid_a, uid_b = _dua_pengguna(test_dsn)
    token_b = create_session(uid_b, "laptop-B", dsn=test_dsn)
    assert validate_session(token_b, dsn=test_dsn) == uid_b
    with pytest.raises(AccessDeniedError):
        resolve_session_as(uid_a, token_b, dsn=test_dsn)
    # B sendiri tetap bisa memakai tokennya.
    assert resolve_session_as(uid_b, token_b, dsn=test_dsn) == uid_b


def test_resolve_session_as_ditolak_tanpa_efek_samping(test_dsn):
    """Resolusi yang ditolak tidak mengubah sesi B sama sekali:
    `terakhir_dilihat` B tetap seperti sebelum penolakan (resolve memakai
    lookup pemilik tanpa efek samping, bukan validate_session)."""
    uid_a, uid_b = _dua_pengguna(test_dsn)
    token_b = create_session(uid_b, "laptop-B", dsn=test_dsn)
    # Satu validasi sah dulu, supaya terakhir_dilihat terisi.
    assert validate_session(token_b, dsn=test_dsn) == uid_b
    sebelum = list_sessions(uid_b, dsn=test_dsn)[0]["terakhir_dilihat"]
    assert sebelum is not None
    with pytest.raises(AccessDeniedError):
        resolve_session_as(uid_a, token_b, dsn=test_dsn)
    sesudah = list_sessions(uid_b, dsn=test_dsn)[0]["terakhir_dilihat"]
    assert sesudah == sebelum
    # Kontrol: resolve yang lolos juga tidak mengubah timestamp —
    # resolusi adalah pemeriksaan kepemilikan, bukan pemakaian sesi.
    assert resolve_session_as(uid_b, token_b, dsn=test_dsn) == uid_b
    assert list_sessions(uid_b, dsn=test_dsn)[0]["terakhir_dilihat"] == sebelum


def test_resolve_session_as_token_tidak_valid_bukan_access_denied(test_dsn):
    """Token tebak-acakan / sudah dicabut -> SessionError, bukan
    AccessDeniedError: 'tidak dikenal' dibedakan dari 'milik orang lain'."""
    uid_a, _uid_b = _dua_pengguna(test_dsn)
    with pytest.raises(SessionError):
        resolve_session_as(uid_a, "token-tebakan-acak", dsn=test_dsn)
    token_a = create_session(uid_a, "laptop-A", dsn=test_dsn)
    revoke_session(token_a, dsn=test_dsn)
    with pytest.raises(SessionError):
        resolve_session_as(uid_a, token_a, dsn=test_dsn)


# ======================================================================
# Pembaca ledger bermilik
# ======================================================================


def _tugas_dengan_agen(test_dsn, agen_id, tugas_id):
    ledger = Ledger(test_dsn)
    ledger.create_schema()
    ev = ledger.append_event(
        tugas_id=tugas_id, agen_id=agen_id, jenis="pesan_user",
        isi={"teks": f"rahasia {agen_id}"},
    )
    return ledger, ev


def test_read_ledger_event_milik_sendiri_lolos(test_dsn):
    uid_a, _uid_b = _dua_pengguna(test_dsn)
    register_agent("agen-A", uid_a, dsn=test_dsn)
    _ledger, ev = _tugas_dengan_agen(test_dsn, "agen-A", f"tugas-{uuid.uuid4().hex[:8]}")
    hasil = read_ledger_event(uid_a, ev["id"], dsn=test_dsn)
    assert hasil["id"] == ev["id"]
    assert hasil["isi"] == {"teks": "rahasia agen-A"}
    assert hasil["agen_id"] == "agen-A"


def test_read_ledger_event_milik_agen_pengguna_lain_ditolak(test_dsn):
    """Kejadian milik agen B ADA, tapi A ditolak — AccessDeniedError,
    bukan KeyError, dan isi kejadian tidak pernah sampai ke A."""
    uid_a, uid_b = _dua_pengguna(test_dsn)
    register_agent("agen-A", uid_a, dsn=test_dsn)
    register_agent("agen-B", uid_b, dsn=test_dsn)
    _ledger, ev_b = _tugas_dengan_agen(test_dsn, "agen-B", f"tugas-{uuid.uuid4().hex[:8]}")
    # Bukti kejadian B ada: B bisa membacanya.
    assert read_ledger_event(uid_b, ev_b["id"], dsn=test_dsn)["id"] == ev_b["id"]
    # A ditolak karena kepemilikan.
    with pytest.raises(AccessDeniedError):
        read_ledger_event(uid_a, ev_b["id"], dsn=test_dsn)


def test_read_ledger_event_tidak_ada_bukan_access_denied(test_dsn):
    uid_a, _uid_b = _dua_pengguna(test_dsn)
    with pytest.raises(KeyError):
        read_ledger_event(uid_a, str(uuid.uuid4()), dsn=test_dsn)


def test_read_ledger_event_agen_tak_terdaftar_ditolak_fail_closed(test_dsn):
    """Kejadian dari agen yang TIDAK ada di registry -> pemilik tidak
    diketahui -> ditolak, bukan dianggap milik siapa pun."""
    uid_a, _uid_b = _dua_pengguna(test_dsn)
    _ledger, ev = _tugas_dengan_agen(
        test_dsn, "agen-tanpa-registry", f"tugas-{uuid.uuid4().hex[:8]}"
    )
    assert owner_of("agen-tanpa-registry", dsn=test_dsn) is None
    with pytest.raises(AccessDeniedError):
        read_ledger_event(uid_a, ev["id"], dsn=test_dsn)


def test_read_ledger_events_for_task_milik_sendiri_lolos(test_dsn):
    uid_a, _uid_b = _dua_pengguna(test_dsn)
    register_agent("agen-A", uid_a, dsn=test_dsn)
    tugas_id = f"tugas-{uuid.uuid4().hex[:8]}"
    ledger = Ledger(test_dsn)
    ledger.create_schema()
    ledger.append_event(
        tugas_id=tugas_id, agen_id="agen-A", jenis="pesan_user",
        isi={"teks": "satu"},
    )
    ledger.append_event(
        tugas_id=tugas_id, agen_id="agen-A", jenis="pesan_agen",
        isi={"teks": "dua"},
    )
    hasil = read_ledger_events_for_task(uid_a, tugas_id, dsn=test_dsn)
    assert len(hasil) == 2
    assert all(e["tugas_id"] == tugas_id for e in hasil)


def test_read_ledger_events_for_task_campuran_ditolak_total(test_dsn):
    """Satu tugas berisi kejadian agen A dan agen B: A meminta seluruh
    tugas -> ditolak TOTAL (daftar kosong, bukan sebagian) karena satu
    kejadian saja milik orang lain."""
    uid_a, uid_b = _dua_pengguna(test_dsn)
    register_agent("agen-A", uid_a, dsn=test_dsn)
    register_agent("agen-B", uid_b, dsn=test_dsn)
    tugas_id = f"tugas-{uuid.uuid4().hex[:8]}"
    ledger = Ledger(test_dsn)
    ledger.create_schema()
    ledger.append_event(
        tugas_id=tugas_id, agen_id="agen-A", jenis="pesan_user",
        isi={"teks": "milik A"},
    )
    ledger.append_event(
        tugas_id=tugas_id, agen_id="agen-B", jenis="pesan_user",
        isi={"teks": "milik B"},
    )
    with pytest.raises(AccessDeniedError):
        read_ledger_events_for_task(uid_a, tugas_id, dsn=test_dsn)
    with pytest.raises(AccessDeniedError):
        read_ledger_events_for_task(uid_b, tugas_id, dsn=test_dsn)
    # Tugas kosong (tidak ada kejadian): tidak ada yang bocor, lolos kosong.
    assert read_ledger_events_for_task(
        uid_a, f"tugas-tidak-ada-{uuid.uuid4().hex}", dsn=test_dsn
    ) == []
