"""Tes Row Level Security (RLS) akun Puspa — definisi 2.15.

Semua tes di sini berjalan sebagai peran aplikasi `puspa_app` lewat
``SET ROLE`` — BUKAN sebagai pemilik tabel. Pemilik tabel/superuser
melewati RLS begitu saja (sifat Postgres), jadi tes sebagai root tidak
membuktikan apa-apa; lihat keputusan "tanpa FORCE" di
``puspa/accounts/schema.sql`` dan tes terakhir di file ini yang
mendokumentasikan bypass pemilik secara eksplisit.

Semantik RLS yang diuji: baris pengguna lain TIDAK TERLIHAT (SELECT
mengembalikan kosong, bukan error); tulis ke baris orang lain DITOLAK
(``InsufficientPrivilege``); tanpa ``app.current_user_id`` yang diset,
semua akses fail-closed (kosong/ditolak).

Data uji (dua pengguna A dan B + sesi + vault) dibuat sebagai pemilik
tabel (bypass RLS) supaya setup tidak bergantung pada policy yang
sedang diuji.
"""

import psycopg
import pytest
from psycopg import errors as pg_errors

from puspa.accounts import create_user
from puspa.accounts import rls


@pytest.fixture(scope="session", autouse=True)
def _ensure_app_role(test_dsn):
    """Pastikan peran puspa_app ada SEBELUM skema diterapkan per tes.

    (create_schema di conftest memberi GRANT ke puspa_app hanya kalau
    peran sudah ada — jadi peran harus ada dulu. Sama seperti yang
    dilakukan infra/setup_db.sql.)
    """
    with psycopg.connect(test_dsn) as conn:
        conn.execute(
            "DO $$ BEGIN "
            "IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'puspa_app') THEN "
            "CREATE ROLE puspa_app NOLOGIN; "
            "END IF; END $$"
        )
        conn.commit()


@pytest.fixture
def dua_user(test_dsn):
    """Buat pengguna A dan B (sebagai pemilik), masing-masing dengan
    1 sesi + 1 entri vault. Kembalikan dict."""
    a = create_user("rls-a@example.com", "pw-a", dsn=test_dsn)
    b = create_user("rls-b@example.com", "pw-b", dsn=test_dsn)
    with psycopg.connect(test_dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO sessions (user_id, token_hash, device_label) "
                "VALUES (%s, %s, %s), (%s, %s, %s)",
                (a, "hash-sesi-a", "laptop-a", b, "hash-sesi-b", "laptop-b"),
            )
            cur.execute(
                "INSERT INTO vault (user_id, name, ciphertext) "
                "VALUES (%s, %s, %s), (%s, %s, %s)",
                (a, "gmail_token", "ct-a", b, "gmail_token", "ct-b"),
            )
        conn.commit()
    return {"a": a, "b": b}


@pytest.fixture
def app_conn(test_dsn):
    """Koneksi sebagai peran aplikasi (dikenai RLS) via SET ROLE."""
    conn = psycopg.connect(test_dsn)
    conn.execute("SET ROLE puspa_app")
    conn.commit()
    yield conn
    conn.close()


# --- SELECT: hanya baris sendiri yang terlihat -------------------------------

def test_select_hanya_rows_own_own(app_conn, dua_user):
    a, b = dua_user["a"], dua_user["b"]
    with app_conn.transaction():
        with rls.as_user(app_conn, a):
            vault_rows = app_conn.execute(
                "SELECT user_id, name FROM vault"
            ).fetchall()
            assert [(str(r[0]), r[1]) for r in vault_rows] == [(a, "gmail_token")]
            sesi_rows = app_conn.execute(
                "SELECT user_id, device_label FROM sessions"
            ).fetchall()
            assert [(str(r[0]), r[1]) for r in sesi_rows] == [(a, "laptop-a")]
            user_rows = app_conn.execute(
                "SELECT id, email FROM users"
            ).fetchall()
            assert [(str(r[0]), r[1]) for r in user_rows] == [
                (a, "rls-a@example.com")
            ]
    # Giliran B: cerminnya — hanya baris B yang terlihat.
    with app_conn.transaction():
        with rls.as_user(app_conn, b):
            assert app_conn.execute(
                "SELECT COUNT(*) FROM vault"
            ).fetchone()[0] == 1
            assert app_conn.execute(
                "SELECT COUNT(*) FROM sessions"
            ).fetchone()[0] == 1
            assert app_conn.execute(
                "SELECT COUNT(*) FROM users"
            ).fetchone()[0] == 1


def test_rows_user_lain_not_terlihat_bukan_error(app_conn, dua_user):
    """RLS menyaring, bukan melempar error: query eksplisit ke baris B
    sebagai A mengembalikan kosong."""
    a, b = dua_user["a"], dua_user["b"]
    with app_conn.transaction():
        with rls.as_user(app_conn, a):
            assert app_conn.execute(
                "SELECT * FROM vault WHERE user_id = %s", (b,)
            ).fetchall() == []
            assert app_conn.execute(
                "SELECT * FROM sessions WHERE user_id = %s", (b,)
            ).fetchall() == []
            assert app_conn.execute(
                "SELECT * FROM users WHERE id = %s", (b,)
            ).fetchall() == []


# --- INSERT / UPDATE / DELETE: tulis ke milik orang lain ditolak -------------

def test_insert_user_id_orang_lain_rejected(app_conn, dua_user):
    a, b = dua_user["a"], dua_user["b"]
    # pytest.raises membungkus blok transaksi dari LUAR: error membuat
    # transaksi abort, jadi rollback harus jalan lewat keluarnya blok.
    with pytest.raises(pg_errors.InsufficientPrivilege):
        with app_conn.transaction():
            with rls.as_user(app_conn, a):
                app_conn.execute(
                    "INSERT INTO vault (user_id, name, ciphertext) "
                    "VALUES (%s, %s, %s)",
                    (b, "gmail_token", "ct-palsu"),
                )
    with pytest.raises(pg_errors.InsufficientPrivilege):
        with app_conn.transaction():
            with rls.as_user(app_conn, a):
                app_conn.execute(
                    "INSERT INTO sessions (user_id, token_hash) VALUES (%s, %s)",
                    (b, "hash-palsu"),
                )
    # INSERT pengguna sebagai A juga ditolak: id baris baru dibuat DB
    # (UUID acak) dan tidak sama dengan A.
    with pytest.raises(pg_errors.InsufficientPrivilege):
        with app_conn.transaction():
            with rls.as_user(app_conn, a):
                app_conn.execute(
                    "INSERT INTO users (email, password_hash) "
                    "VALUES (%s, %s)",
                    ("rls-c@example.com", "hash-dummy"),
                )


def test_insert_own_own_accepted(app_conn, dua_user):
    a = dua_user["a"]
    with app_conn.transaction():
        with rls.as_user(app_conn, a):
            app_conn.execute(
                "INSERT INTO vault (user_id, name, ciphertext) "
                "VALUES (%s, %s, %s)",
                (a, "drive_token", "ct-baru"),
            )
            app_conn.execute(
                "INSERT INTO sessions (user_id, token_hash, device_label) "
                "VALUES (%s, %s, %s)",
                (a, "hash-sesi-a2", "hp-a"),
            )
    # Terverifikasi sebagai pemilik: barisnya memang masuk.
    with psycopg.connect(app_conn.info.dsn) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM vault WHERE user_id = %s", (a,)
        ).fetchone()[0] == 2
        assert conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE user_id = %s", (a,)
        ).fetchone()[0] == 2


def test_update_to_own_orang_lain_rejected(app_conn, dua_user):
    a, b = dua_user["a"], dua_user["b"]
    # UPDATE baris B sebagai A: baris tidak terlihat -> 0 baris kena.
    with app_conn.transaction():
        with rls.as_user(app_conn, a):
            cur = app_conn.execute(
                "UPDATE vault SET ciphertext = 'diubah' WHERE user_id = %s",
                (b,),
            )
            assert cur.rowcount == 0
    # UPDATE baris sendiri tapi pindahkan kepemilikannya ke B:
    # WITH CHECK gagal -> ditolak dengan error.
    with pytest.raises(pg_errors.InsufficientPrivilege):
        with app_conn.transaction():
            with rls.as_user(app_conn, a):
                app_conn.execute(
                    "UPDATE vault SET user_id = %s WHERE user_id = %s",
                    (b, a),
                )


def test_delete_hanya_own_own(app_conn, dua_user):
    a, b = dua_user["a"], dua_user["b"]
    # DELETE baris B sebagai A: 0 baris kena, baris B tetap ada.
    with app_conn.transaction():
        with rls.as_user(app_conn, a):
            cur = app_conn.execute(
                "DELETE FROM vault WHERE user_id = %s", (b,)
            )
            assert cur.rowcount == 0
    with psycopg.connect(app_conn.info.dsn) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM vault WHERE user_id = %s", (b,)
        ).fetchone()[0] == 1
    # DELETE baris sendiri: 1 baris kena.
    with app_conn.transaction():
        with rls.as_user(app_conn, a):
            cur = app_conn.execute(
                "DELETE FROM vault WHERE user_id = %s", (a,)
            )
            assert cur.rowcount == 1


# --- Fail-closed: tanpa current_user_id --------------------------------------

def test_without_current_user_fail_closed(app_conn, dua_user):
    """Tanpa app.current_user_id: SELECT kosong, INSERT ditolak,
    UPDATE/DELETE tidak kena baris apa pun."""
    a, b = dua_user["a"], dua_user["b"]
    with app_conn.transaction():
        assert app_conn.execute("SELECT * FROM vault").fetchall() == []
        assert app_conn.execute("SELECT * FROM sessions").fetchall() == []
        assert app_conn.execute("SELECT * FROM users").fetchall() == []
        cur = app_conn.execute(
            "UPDATE vault SET ciphertext = 'x' WHERE user_id = %s", (a,)
        )
        assert cur.rowcount == 0
        cur = app_conn.execute(
            "DELETE FROM vault WHERE user_id = %s", (a,)
        )
        assert cur.rowcount == 0
    # INSERT yang ditolak, masing-masing di transaksi sendiri (transaksi
    # abort lalu rollback lewat keluarnya blok).
    with pytest.raises(pg_errors.InsufficientPrivilege):
        with app_conn.transaction():
            app_conn.execute(
                "INSERT INTO vault (user_id, name, ciphertext) "
                "VALUES (%s, %s, %s)",
                (a, "x", "ct"),
            )
    with pytest.raises(pg_errors.InsufficientPrivilege):
        with app_conn.transaction():
            app_conn.execute(
                "INSERT INTO sessions (user_id, token_hash) VALUES (%s, %s)",
                (a, "hash-x"),
            )


def test_insert_user_without_sesi_adalah_registrasi(app_conn):
    """Pengecualian yang disengaja: INSERT pengguna tanpa pengguna yang
    diset diizinkan — itu jalur registrasi (belum ada sesi). Setelah
    ada pengguna yang diset, INSERT pengguna lain ditolak (lihat
    test_insert_user_id_orang_lain_ditolak)."""
    with app_conn.transaction():
        app_conn.execute(
            "INSERT INTO users (email, password_hash) VALUES (%s, %s)",
            ("rls-registrasi@example.com", "hash-dummy"),
        )
    with psycopg.connect(app_conn.info.dsn) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM users WHERE email = %s",
            ("rls-registrasi@example.com",),
        ).fetchone()[0] == 1


# --- Helper rls.py -----------------------------------------------------------

def test_helper_as_user_set_and_bersihkan(app_conn, dua_user):
    a = dua_user["a"]
    with app_conn.transaction():
        assert rls.current_user_id(app_conn) == ""
        with pytest.raises(rls.RlsError):
            rls.require_current_user(app_conn)
        with rls.as_user(app_conn, a) as uid:
            assert uid == a
            assert rls.current_user_id(app_conn) == a
            assert rls.require_current_user(app_conn) == a
        # Keluar context manager: kembali kosong.
        assert rls.current_user_id(app_conn) == ""


def test_helper_tolak_user_id_bukan_uuid(app_conn):
    with app_conn.transaction():
        with pytest.raises(ValueError):
            rls.set_current_user(app_conn, "bukan-uuid")
        with pytest.raises(ValueError):
            rls.set_current_user(app_conn, None)
        # Nilai yang tidak valid tidak memasang apa-apa.
        assert rls.current_user_id(app_conn) == ""


def test_helper_not_bocor_antar_transaction(app_conn, dua_user):
    """SET LOCAL hilang sendiri saat transaksi berakhir."""
    a = dua_user["a"]
    with app_conn.transaction():
        with rls.as_user(app_conn, a):
            assert rls.current_user_id(app_conn) == a
    with app_conn.transaction():
        assert rls.current_user_id(app_conn) == ""


# --- Keputusan tanpa-FORCE, didokumentasikan sebagai tes ---------------------

def test_pemilik_table_melewati_rls_terdokumentasi(test_dsn, dua_user):
    """Pemilik tabel (root, superuser) melihat SEMUA baris walau RLS
    aktif — itu sifat Postgres tanpa FORCE ROW LEVEL SECURITY, dan
    disengaja: operasi istimewa (registrasi, validate_session,
    delete_user, fixture TRUNCATE) jalan sebagai pemilik. Batas
    penegakan adalah peran puspa_app (tes-tes di atas). Kalau suatu
    hari dipakai FORCE, tes ini harus diubah — lihat schema.sql."""
    a, b = dua_user["a"], dua_user["b"]
    with psycopg.connect(test_dsn) as conn:
        assert conn.execute("SELECT COUNT(*) FROM vault").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 2
        assert {str(r[0]) for r in conn.execute(
            "SELECT user_id FROM vault"
        ).fetchall()} == {a, b}
