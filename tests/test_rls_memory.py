"""Tes RLS tabel memori Fase 1 sebagai peran puspa_app (C118, C56).

Pola yang sama dengan tests/test_rls.py Fase 0: koneksi sebagai
pemilik, lalu SET ROLE puspa_app. Tanpa current_user -> tidak ada
baris terlihat dan tulis ditolak; dengan current_user -> hanya baris
milik sendiri.

On SQLite there is no RLS and no role system (D-SQLite-5):
ownership is enforced in code, by filtering on user_id in the
read APIs. The read tests below assert that same guarantee
through the read APIs on SQLite; the write-rejection test has no
code-level equivalent (the write APIs trust their caller, and
cross-user writes are refused one layer up, by the PWA API with
403s) and is skipped there.
"""

from puspa.db import connect as _db_connect, Error as _db_Error
from puspa.db import is_sqlite_dsn as _is_sqlite_dsn
import pytest


def _connect_as_app(test_dsn):
    conn = _db_connect(test_dsn)
    conn.execute("SET ROLE puspa_app")
    return conn


def test_rls_without_user_sees_nothing(make_user, test_dsn):
    u = make_user()
    from puspa.memory import profile

    profile.add_fact(u["user_id"], "name", "Nero", "user_stated", dsn=test_dsn)
    if _is_sqlite_dsn(test_dsn):
        from puspa.memory import episodic

        episodic.record_episode(u["user_id"], "rahasia", "user_message", dsn=test_dsn)
        other = make_user()
        assert profile.list_facts(other["user_id"], dsn=test_dsn) == []
        assert episodic.search_episodes(other["user_id"], "rahasia", dsn=test_dsn) == []
        assert [f["value"] for f in profile.list_facts(u["user_id"], dsn=test_dsn)] == [
            "Nero"
        ]
        assert episodic.search_episodes(u["user_id"], "rahasia", dsn=test_dsn)
        return
    with _connect_as_app(test_dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM profile_facts")
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT COUNT(*) FROM episodic")
        assert cur.fetchone()[0] == 0


def test_rls_only_own_rows(make_user, test_dsn):
    from puspa.memory import episodic, profile

    a, b = make_user(), make_user()
    profile.add_fact(a["user_id"], "name", "Nero", "user_stated", dsn=test_dsn)
    profile.add_fact(b["user_id"], "name", "Budi", "user_stated", dsn=test_dsn)
    episodic.record_episode(a["user_id"], "rahasia a", "user_message", dsn=test_dsn)
    if _is_sqlite_dsn(test_dsn):
        assert [f["value"] for f in profile.list_facts(a["user_id"], dsn=test_dsn)] == [
            "Nero"
        ]
        assert [f["value"] for f in profile.list_facts(b["user_id"], dsn=test_dsn)] == [
            "Budi"
        ]
        assert [
            e["content"]
            for e in episodic.search_episodes(a["user_id"], "rahasia a", dsn=test_dsn)
        ] == ["rahasia a"]
        assert episodic.search_episodes(b["user_id"], "rahasia a", dsn=test_dsn) == []
        return
    with _connect_as_app(test_dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT set_config('app.current_user_id', %s, true)", (a["user_id"],))
        cur.execute("SELECT value FROM profile_facts")
        assert [r[0] for r in cur.fetchall()] == ["Nero"]
        cur.execute("SELECT content FROM episodic")
        assert [r[0] for r in cur.fetchall()] == ["rahasia a"]
        cur.execute("SELECT set_config('app.current_user_id', %s, true)", (b["user_id"],))
        cur.execute("SELECT value FROM profile_facts")
        assert [r[0] for r in cur.fetchall()] == ["Budi"]
        cur.execute("SELECT content FROM episodic")
        assert cur.fetchall() == []


def test_rls_write_by_other_person_rejected(make_user, test_dsn):
    if _is_sqlite_dsn(test_dsn):
        pytest.skip(
            "SQLite has no RLS or roles: a cross-user INSERT cannot be "
            "rejected at the SQL layer (D-SQLite-5); the PWA API "
            "refuses cross-user writes with 403 instead"
        )
    a = make_user()
    with _connect_as_app(test_dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('app.current_user_id', %s, true)", (a["user_id"],))
            with pytest.raises(_db_Error):
                cur.execute(
                    "INSERT INTO profile_facts (user_id, key, value, source) "
                    "VALUES ('00000000-0000-0000-0000-000000000000', 'nama', 'X', "
                    "'user_stated')"
                )
        conn.rollback()
