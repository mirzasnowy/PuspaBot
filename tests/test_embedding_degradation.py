"""Embedding degradation is visible (E-14, plan section 7.4).

Covers: embedder_id + dimension stored on every vector row;
mixed-embedder writes refused; search never mixes embedders;
the production startup gate (refuse vs degraded); the fallback
and canary-recall alarms; the degraded state in /health, the
ledger, and the operator banner; tiered re-embed jobs.

DB tests run on PUSPA_TEST_DSN (puspa_test_w2). The embedding_state
singleton is NOT truncated by conftest, so this module cleans it
(plus the job and baseline tables) before and after every test.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from puspa.db import connect as _db_connect
import pytest
from fastapi.testclient import TestClient

from puspa.ledger import Ledger
from puspa.memory import episodic
from puspa.memory.embeddings import FastEmbedEmbedding, HashEmbedding
from puspa.pwa.app import create_app
from puspa.pwa.health import build_health_payload, embedding_banner
from puspa.retrieval.embedding_health import (
    CANARY_QUESTIONS,
    EmbeddingHealth,
    EmbeddingStartupRefused,
    MixedEmbedderError,
    check_fallback_alarm,
    count_mixed_rows,
    create_reembed_job,
    detect_available_embedder_kind,
    evaluate_canary_recall,
    evaluate_startup,
    get_embedding_state,
    get_reembed_job,
    load_canary_baseline,
    load_reference_baseline,
    measure_canary_recall,
    plan_reembed_tiers,
    read_degraded_from_ledger,
    record_canary_baseline,
    run_canary_check,
    run_reembed_tier,
    set_active_embedder,
    validate_rows_match_active,
)


class _HashBackedStubModel:
    """Fake fastembed model: hash vectors (same space as HashEmbedding)."""

    def embed(self, texts):
        base = HashEmbedding()
        return [base.embed(text) for text in texts]


def _stub_fastembed():
    """A FastEmbedEmbedding whose vectors equal the hash vectors.

    Same vector space, different embedder id: the only thing
    separating its rows from hash rows is the stored id, which is
    exactly what the no-mixing rules must enforce.
    """
    return FastEmbedEmbedding(model=_HashBackedStubModel())


class _FirstTokenEmbedding:
    """Deliberately lossy embedder: only the first token counts."""

    embedder_id = "first-token-test"
    dim = 384

    def __init__(self):
        self._base = HashEmbedding()

    def embed(self, text):
        first = text.split()[0] if text.split() else ""
        return self._base.embed(first)


@pytest.fixture(autouse=True)
def _clean_embedding_tables(test_dsn):
    _truncate_embedding_tables(test_dsn)
    yield
    _truncate_embedding_tables(test_dsn)


def _truncate_embedding_tables(dsn):
    with _db_connect(dsn, autocommit=True) as conn:
        conn.execute("TRUNCATE embedding_state, embedding_reembed_jobs, embedding_canary_baselines")


def _embedder_of_row(dsn, row_id):
    with _db_connect(dsn) as conn:
        (embedder_id,) = conn.execute(
            "SELECT embedder_id FROM episodic WHERE id = %s", (row_id,)
        ).fetchone()
    return embedder_id


def _seed_canary(user_id, embedder, dsn):
    """Seed the 30 canary contents; return [{"question", "episode_id"}]."""
    questions = []
    for item in CANARY_QUESTIONS:
        episode = episodic.record_episode(
            user_id, item["content"], "user_message", embedder=embedder, dsn=dsn
        )
        questions.append({"question": item["question"], "episode_id": episode["id"]})
    return questions


# --- pure: identities, startup gate, alarms -----------------------------------


def test_embedder_ids_are_stable():
    assert HashEmbedding().embedder_id == "hash"
    assert HashEmbedding().dim == 384
    stub = _stub_fastembed()
    assert stub.embedder_id == "fastembed:BAAI/bge-small-en-v1.5"
    assert stub.dim == 384
    other = FastEmbedEmbedding(model_name="other/model", model=_HashBackedStubModel())
    assert other.embedder_id == "fastembed:other/model"


def test_canary_set_has_30_questions_with_unique_contents():
    assert len(CANARY_QUESTIONS) == 30
    assert len({item["content"] for item in CANARY_QUESTIONS}) == 30
    assert all(item["question"] and item["content"] for item in CANARY_QUESTIONS)


def test_startup_healthy_with_real_embedder_in_production():
    health = evaluate_startup(_stub_fastembed(), environment="production")
    assert health.status == "healthy"
    assert health.alarm is False
    assert health.to_banner() is None


def test_startup_degraded_with_hash_only_in_production():
    health = evaluate_startup(HashEmbedding(), environment="production")
    assert health.status == "degraded"
    assert health.is_degraded is True
    assert health.reason == "hash_only_fallback"
    assert health.alarm is True
    assert health.alarm_reasons == ["hash_fallback_in_production"]
    banner = health.to_banner()
    assert banner["level"] == "warning"
    assert banner["title"] == "Embedding degraded"
    assert "hash" in banner["detail"]


def test_startup_strict_policy_refuses_hash_only_in_production():
    with pytest.raises(EmbeddingStartupRefused, match="production start refused"):
        evaluate_startup(HashEmbedding(), environment="production", production_policy="strict")


def test_startup_hash_is_healthy_outside_production():
    health = evaluate_startup(HashEmbedding(), environment="development")
    assert health.status == "healthy"
    assert health.alarm is False


def test_startup_unknown_policy_rejected():
    with pytest.raises(ValueError, match="unknown production policy"):
        evaluate_startup(HashEmbedding(), environment="production", production_policy="panic")


def test_fallback_alarm_only_in_production():
    assert check_fallback_alarm(1, environment="production") is True
    assert check_fallback_alarm(0, environment="production") is False
    assert check_fallback_alarm(1, environment="development") is False
    assert check_fallback_alarm(3, environment="Production") is True


def test_canary_alarm_threshold_is_strictly_above_10_percent():
    assert evaluate_canary_recall(0.70, 0.90)["alarm"] is True  # 22% drop
    assert evaluate_canary_recall(0.85, 0.90)["alarm"] is False  # 5.6% drop
    assert evaluate_canary_recall(0.90, 1.00)["alarm"] is False  # exactly 10%: no alarm
    assert evaluate_canary_recall(0.89, 1.00)["alarm"] is True  # 11%: alarm
    assert evaluate_canary_recall(1.00, 1.00)["alarm"] is False


def test_canary_evaluation_carries_the_numbers():
    result = evaluate_canary_recall(0.70, 1.00)
    assert result["measured_recall"] == 0.70
    assert result["baseline_recall"] == 1.00
    assert result["drop_relative"] == pytest.approx(0.30)
    assert result["threshold"] == 0.10


def test_measure_canary_recall_pure():
    def search_fn(question):
        return [{"id": f"ep-{question}"}] if question != "q-miss" else []

    questions = [{"question": f"q-{n}", "episode_id": f"ep-q-{n}"} for n in range(9)]
    questions.append({"question": "q-miss", "episode_id": "ep-q-miss"})
    result = measure_canary_recall(search_fn, questions, top_k=5)
    assert result["recall"] == pytest.approx(0.9)
    assert result["hits"] == 9
    assert result["total"] == 10
    assert result["misses"] == ["q-miss"]


def test_tier_plan_is_newest_first():
    tiers = plan_reembed_tiers()
    assert [tier["tier"] for tier in tiers] == [1, 2, 3]
    assert [tier["max_age_days"] for tier in tiers] == [7, 90, None]
    assert [tier["label"] for tier in tiers] == ["recent_7d", "recent_90d", "older"]


def test_health_payload_and_banner_from_snapshot():
    degraded = EmbeddingHealth(
        embedder_id="hash",
        dimension=384,
        status="degraded",
        reason="hash_only_fallback",
        environment="production",
        alarm=True,
    ).to_dict()
    payload = build_health_payload(degraded, serve_mode="local")
    assert payload["status"] == "degraded"
    assert payload["embedding"]["embedder_id"] == "hash"
    assert embedding_banner(degraded)["title"] == "Embedding degraded"
    healthy = EmbeddingHealth(embedder_id="hash", dimension=384).to_dict()
    assert build_health_payload(healthy)["status"] == "ok"
    assert embedding_banner(healthy) is None
    assert embedding_banner(None) is None


def test_detect_available_embedder_kind_uses_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("FASTEMBED_CACHE_PATH", str(tmp_path))
    assert detect_available_embedder_kind() == "hash"
    (tmp_path / "models--Qdrant--bge-small-en-v1.5-onnx-Q").mkdir()
    assert detect_available_embedder_kind() == "fastembed"


# --- database: rows, mixing, validation ---------------------------------------


def test_rows_store_embedder_id_and_dimension(make_user, test_dsn):
    user = make_user()
    episode = episodic.record_episode(
        user["user_id"], "kopi tubruk pagi", "user_message", dsn=test_dsn
    )
    assert episode["embedder_id"] == "hash"
    assert episode["dimension"] == 384
    with _db_connect(test_dsn) as conn:
        row = conn.execute(
            "SELECT embedder_id, dimension FROM episodic WHERE id = %s",
            (episode["id"],),
        ).fetchone()
    assert row == ("hash", 384)


def test_mixed_embedder_write_is_refused(make_user, test_dsn):
    user = make_user()
    stub = _stub_fastembed()
    episodic.record_episode(
        user["user_id"], "kopi tubruk", "user_message", embedder=stub, dsn=test_dsn
    )
    with pytest.raises(MixedEmbedderError, match="active embedder"):
        episodic.record_episode(
            user["user_id"],
            "kopi tubruk lagi",
            "user_message",
            embedder=HashEmbedding(),
            dsn=test_dsn,
        )
    assert len(episodic.list_episodes(user["user_id"], dsn=test_dsn)) == 1


def test_search_never_mixes_embedders(make_user, test_dsn):
    first, second = make_user(), make_user()
    stub = _stub_fastembed()
    episodic.record_episode(first["user_id"], "kopi tubruk pagi hari", "user_message", dsn=test_dsn)
    set_active_embedder(test_dsn, stub.embedder_id, 384, allow_mixed=True)
    episodic.record_episode(
        second["user_id"], "kopi tubruk pagi hari", "user_message", embedder=stub, dsn=test_dsn
    )
    assert [
        row["content"]
        for row in episodic.search_episodes(
            first["user_id"], "kopi tubruk", embedder=HashEmbedding(), dsn=test_dsn
        )
    ] == ["kopi tubruk pagi hari"]
    assert [
        row["content"]
        for row in episodic.search_episodes(
            second["user_id"], "kopi tubruk", embedder=stub, dsn=test_dsn
        )
    ] == ["kopi tubruk pagi hari"]
    assert (
        episodic.search_episodes(first["user_id"], "kopi tubruk", embedder=stub, dsn=test_dsn) == []
    )


def test_validate_rows_match_active_catches_direct_sql_row(make_user, test_dsn):
    user = make_user()
    stub = _stub_fastembed()
    episodic.record_episode(user["user_id"], "kopi tubruk", "user_message", dsn=test_dsn)
    assert validate_rows_match_active(test_dsn) == 0
    set_active_embedder(test_dsn, stub.embedder_id, 384, allow_mixed=True)
    with pytest.raises(MixedEmbedderError, match="episodic rows use an embedder"):
        validate_rows_match_active(test_dsn)
    assert count_mixed_rows(test_dsn, embedder_id=stub.embedder_id, dimension=384) == 1


def test_active_embedder_switch_refuses_while_rows_remain(make_user, test_dsn):
    user = make_user()
    stub = _stub_fastembed()
    episodic.record_episode(user["user_id"], "kopi tubruk", "user_message", dsn=test_dsn)
    with pytest.raises(MixedEmbedderError, match="finish a re-embed job first"):
        set_active_embedder(test_dsn, stub.embedder_id, 384)
    assert get_embedding_state(test_dsn)["active_embedder_id"] == "hash"


# --- database: canary baseline + live alarm ------------------------------------


def test_canary_baseline_ratchet(test_dsn):
    first = record_canary_baseline(test_dsn, embedder_id="hash", dimension=384, recall=1.0)
    assert first["updated"] is True
    lower = record_canary_baseline(test_dsn, embedder_id="hash", dimension=384, recall=0.5)
    assert lower["updated"] is False
    assert lower["stored_recall"] == 1.0
    assert load_canary_baseline(test_dsn, embedder_id="hash", dimension=384)["recall"] == 1.0
    forced = record_canary_baseline(
        test_dsn, embedder_id="hash", dimension=384, recall=0.5, force=True
    )
    assert forced["updated"] is True
    assert load_reference_baseline(test_dsn)["recall"] == 0.5


def test_canary_baseline_requires_30_questions(test_dsn):
    with pytest.raises(ValueError, match="exactly 30 questions"):
        record_canary_baseline(
            test_dsn, embedder_id="hash", dimension=384, recall=0.9, question_count=10
        )


def test_canary_recall_alarm_fires_on_live_rows(make_user, test_dsn):
    good_user, bad_user = make_user(), make_user()
    hash_emb = HashEmbedding()
    weak = _FirstTokenEmbedding()
    questions = _seed_canary(good_user["user_id"], hash_emb, test_dsn)
    measured = run_canary_check(
        test_dsn, user_id=good_user["user_id"], embedder=hash_emb, questions=questions
    )
    assert measured["measured"]["recall"] == 1.0
    assert measured["alarm"] is None  # no baseline yet: first run
    record_canary_baseline(test_dsn, embedder_id="hash", dimension=384, recall=1.0)
    set_active_embedder(test_dsn, weak.embedder_id, 384, allow_mixed=True)
    weak_questions = _seed_canary(bad_user["user_id"], weak, test_dsn)
    checked = run_canary_check(
        test_dsn, user_id=bad_user["user_id"], embedder=weak, questions=weak_questions
    )
    assert checked["measured"]["recall"] < 0.5
    assert checked["baseline"]["recall"] == 1.0
    assert checked["evaluation"]["alarm"] is True
    assert checked["alarm"] is True


# --- database: tiered re-embed jobs --------------------------------------------


def test_reembed_job_runs_tier_by_tier(make_user, test_dsn):
    user = make_user()
    uid = user["user_id"]
    hash_emb = HashEmbedding()
    stub = _stub_fastembed()
    now = datetime.now(UTC)
    recent = episodic.record_episode(
        uid, "recent laptop asus vivobook", "user_message", embedder=hash_emb, dsn=test_dsn
    )
    month_old = episodic.record_episode(
        uid, "month-old printer epson l3210", "user_message", embedder=hash_emb, dsn=test_dsn
    )
    old = episodic.record_episode(
        uid, "old bicycle polygon heist", "user_message", embedder=hash_emb, dsn=test_dsn
    )
    with _db_connect(test_dsn, autocommit=True) as conn:
        conn.execute(
            "UPDATE episodic SET created_at = %s WHERE id = %s",
            (now - timedelta(days=30), month_old["id"]),
        )
        conn.execute(
            "UPDATE episodic SET created_at = %s WHERE id = %s",
            (now - timedelta(days=200), old["id"]),
        )
    job = create_reembed_job(
        test_dsn,
        from_embedder_id="hash",
        to_embedder_id=stub.embedder_id,
        from_dimension=384,
        to_dimension=384,
    )
    assert job["status"] == "queued"
    assert job["total_rows"] == 3
    assert [tier["tier"] for tier in job["tiers"]] == [1, 2, 3]

    after_tier_1 = run_reembed_tier(test_dsn, job["id"], 1, stub)
    assert after_tier_1["status"] == "running"
    assert after_tier_1["tier_done_rows"] == 1
    assert _embedder_of_row(test_dsn, recent["id"]) == stub.embedder_id
    assert _embedder_of_row(test_dsn, month_old["id"]) == "hash"
    assert _embedder_of_row(test_dsn, old["id"]) == "hash"

    after_tier_2 = run_reembed_tier(test_dsn, job["id"], 2, stub)
    assert after_tier_2["status"] == "running"
    assert after_tier_2["tier_done_rows"] == 1
    assert _embedder_of_row(test_dsn, month_old["id"]) == stub.embedder_id
    assert _embedder_of_row(test_dsn, old["id"]) == "hash"

    after_tier_3 = run_reembed_tier(test_dsn, job["id"], 3, stub)
    assert after_tier_3["status"] == "done"
    assert after_tier_3["tier_done_rows"] == 1
    assert after_tier_3["done_rows"] == 3
    assert _embedder_of_row(test_dsn, old["id"]) == stub.embedder_id
    state = get_embedding_state(test_dsn)
    assert state["active_embedder_id"] == stub.embedder_id
    assert state["status"] == "healthy"

    assert get_reembed_job(test_dsn, job["id"])["status"] == "done"
    assert episodic.search_episodes(uid, "laptop asus", embedder=stub, dsn=test_dsn), (
        "new embedder must find the re-embedded rows"
    )
    assert episodic.search_episodes(uid, "laptop asus", embedder=hash_emb, dsn=test_dsn) == []


def test_reembed_job_refuses_wrong_embedder(make_user, test_dsn):
    user = make_user()
    episodic.record_episode(user["user_id"], "kopi tubruk", "user_message", dsn=test_dsn)
    stub = _stub_fastembed()
    job = create_reembed_job(
        test_dsn,
        from_embedder_id="hash",
        to_embedder_id=stub.embedder_id,
        from_dimension=384,
        to_dimension=384,
    )
    with pytest.raises(MixedEmbedderError, match="needs embedder"):
        run_reembed_tier(test_dsn, job["id"], 1, HashEmbedding())


def test_reembed_job_needs_a_different_target(test_dsn):
    with pytest.raises(ValueError, match="different target embedder"):
        create_reembed_job(
            test_dsn,
            from_embedder_id="hash",
            to_embedder_id="hash",
            from_dimension=384,
            to_dimension=384,
        )


# --- wiring: degraded state in /health, ledger, operator banner ----------------


def test_degraded_state_in_health_ledger_and_banner(make_user, test_dsn):
    user = make_user()
    app = create_app(
        test_user_id=user["user_id"],
        dsn=test_dsn,
        embedder=HashEmbedding(),
        embedding_environment="production",
    )
    with TestClient(app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["status"] == "degraded"
        assert health.json()["embedding"]["embedder_id"] == "hash"
        assert health.json()["embedding"]["reason"] == "hash_only_fallback"

        operator = client.get("/api/operator/status")
        assert operator.status_code == 200
        banner = operator.json()["embedding_banner"]
        assert banner is not None
        assert banner["title"] == "Embedding degraded"
        assert operator.json()["embedding"]["status"] == "degraded"

    degraded_events = read_degraded_from_ledger(Ledger(test_dsn))
    assert len(degraded_events) >= 1
    content = degraded_events[-1]["content"]
    assert content["kind"] == "embedding_degraded"
    assert content["embedder_id"] == "hash"
    assert content["status"] == "degraded"
    assert content["alarm"] is True


def test_healthy_state_shows_ok_everywhere(make_user, test_dsn):
    user = make_user()
    app = create_app(
        test_user_id=user["user_id"],
        dsn=test_dsn,
        embedder=_stub_fastembed(),
        embedding_environment="production",
    )
    with TestClient(app) as client:
        assert client.get("/health").json()["status"] == "ok"
        assert client.get("/api/operator/status").json()["embedding_banner"] is None
    assert read_degraded_from_ledger(Ledger(test_dsn)) == []


def test_strict_policy_refuses_app_start(make_user, test_dsn, monkeypatch):
    user = make_user()
    monkeypatch.setenv("PUSPA_EMBEDDING_PRODUCTION_POLICY", "strict")
    with pytest.raises(EmbeddingStartupRefused):
        create_app(
            test_user_id=user["user_id"],
            dsn=test_dsn,
            embedder=HashEmbedding(),
            embedding_environment="production",
        )
