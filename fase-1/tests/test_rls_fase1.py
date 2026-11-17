"""Tes RLS tabel memori Fase 1 sebagai peran puspa_app (C118, C56).

Pola yang sama dengan tests/test_rls.py Fase 0: koneksi sebagai
pemilik, lalu SET ROLE puspa_app. Tanpa current_user -> tidak ada
baris terlihat dan tulis ditolak; dengan current_user -> hanya baris
milik sendiri.
"""

import psycopg
import pytest


def _connect_as_app(test_dsn):
    conn = psycopg.connect(test_dsn)
    conn.execute("SET ROLE puspa_app")
    return conn


def test_rls_tanpa_pengguna_tidak_melihat_apa_pun(make_user, test_dsn):
    u = make_user()
    from puspa.memory import profile

    profile.add_fact(u["user_id"], "nama", "Nero", "diucapkan_user", dsn=test_dsn)
    with _connect_as_app(test_dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM fakta_profil")
            assert cur.fetchone()[0] == 0
            cur.execute("SELECT COUNT(*) FROM episodik")
            assert cur.fetchone()[0] == 0


def test_rls_hanya_baris_milik_sendiri(make_user, test_dsn):
    from puspa.memory import episodic, profile

    a, b = make_user(), make_user()
    profile.add_fact(a["user_id"], "nama", "Nero", "diucapkan_user", dsn=test_dsn)
    profile.add_fact(b["user_id"], "nama", "Budi", "diucapkan_user", dsn=test_dsn)
    episodic.record_episode(a["user_id"], "rahasia a", "pesan_user", dsn=test_dsn)
    with _connect_as_app(test_dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('app.current_user_id', %s, true)", (a["user_id"],))
            cur.execute("SELECT nilai FROM fakta_profil")
            assert [r[0] for r in cur.fetchall()] == ["Nero"]
            cur.execute("SELECT isi FROM episodik")
            assert [r[0] for r in cur.fetchall()] == ["rahasia a"]
            cur.execute("SELECT set_config('app.current_user_id', %s, true)", (b["user_id"],))
            cur.execute("SELECT nilai FROM fakta_profil")
            assert [r[0] for r in cur.fetchall()] == ["Budi"]
            cur.execute("SELECT isi FROM episodik")
            assert cur.fetchall() == []


def test_rls_tulis_orang_lain_ditolak(make_user, test_dsn):
    a = make_user()
    with _connect_as_app(test_dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('app.current_user_id', %s, true)", (a["user_id"],))
            with pytest.raises(psycopg.Error):
                cur.execute(
                    "INSERT INTO fakta_profil (user_id, kunci, nilai, asal) "
                    "VALUES ('00000000-0000-0000-0000-000000000000', 'nama', 'X', "
                    "'diucapkan_user')"
                )
        conn.rollback()
