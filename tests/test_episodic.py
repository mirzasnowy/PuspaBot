"""Tes Episodik — lapis 2 memori + index pgvector (C54)."""

import psycopg
import pytest

from puspa.memory import episodic
from puspa.memory.embeddings import HashEmbedding, cosine_similarity


def test_jalur_tulis_and_read_episodik(make_user, test_dsn):
    u = make_user()
    ep = episodic.record_episode(
        u["user_id"], "user suka kopi tubruk", "user_message", dsn=test_dsn
    )
    assert ep["type"] == "user_message"
    result = episodic.search_episodes(u["user_id"], "kopi tubruk", dsn=test_dsn)
    assert result and result[0]["id"] == ep["id"]
    assert result[0]["kemiripan"] > 0.5


def test_pencarian_mengutamakan_yang_paling_mirip(make_user, test_dsn):
    u = make_user()
    episodic.record_episode(u["user_id"], "harga laptop asus vivobook", "user_message", dsn=test_dsn)
    episodic.record_episode(u["user_id"], "tagihan listrik pln bulan ini", "user_message", dsn=test_dsn)
    episodic.record_episode(u["user_id"], "laptop asus vivobook empat belas inci", "agent_message", dsn=test_dsn)
    result = episodic.search_episodes(u["user_id"], "laptop asus vivobook", limit=3, dsn=test_dsn)
    assert "laptop" in result[0]["content"]
    assert "laptop" in result[1]["content"]
    assert result[2]["content"].startswith("bill")


def test_episodik_terisolasi_antar_user(make_user, test_dsn):
    a, b = make_user(), make_user()
    episodic.record_episode(a["user_id"], "rahasia kopi tubruk", "user_message", dsn=test_dsn)
    assert episodic.search_episodes(b["user_id"], "kopi tubruk", dsn=test_dsn) == []
    assert episodic.list_episodes(b["user_id"], dsn=test_dsn) == []


def test_embedding_deterministik_and_ternormalisasi():
    emb = HashEmbedding()
    v1 = emb.embed("kopi tubruk pagi hari")
    v2 = emb.embed("kopi tubruk pagi hari")
    assert v1 == v2
    assert len(v1) == 384
    assert abs(sum(x * x for x in v1) - 1.0) < 1e-6
    assert cosine_similarity(v1, v1) == pytest.approx(1.0)
    assert cosine_similarity(v1, emb.embed("tagihan listrik pln")) < 0.5


def test_index_pgvector_ada(test_dsn):
    """Index HNSW episodik benar-benar terpasang di Postgres."""
    import psycopg

    with psycopg.connect(test_dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT indexname FROM pg_indexes WHERE tablename = 'episodic' "
                "AND indexname = 'idx_episodik_embedding'"
            )
            assert cur.fetchone() is not None
            cur.execute(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_name = 'episodic' AND column_name = 'embedding'"
            )
            assert cur.fetchone()[0] == "USER-DEFINED"  # tipe vector
