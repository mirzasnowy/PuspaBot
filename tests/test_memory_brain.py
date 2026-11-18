"""Tests for the Brain-phase memory hardening.

All tests here are in-memory: no test body in this file touches
the shared Postgres database. Pure helpers (promotion,
decay, contradiction, reflection, dual-level ranking) are tested
directly; the database-backed wrappers with the same logic are
covered against Postgres in tests/test_memory_brain_db.py.
(The shared tests/conftest.py autouse fixture still creates
schemas and truncates tables on collection; that is unavoidable
for any file under tests/.)
"""

from datetime import UTC, datetime, timedelta

import pytest

from puspa.graphs import identity
from puspa.memory import consolidate, episodic, procedural
from puspa.memory.embeddings import (
    EmbeddingUnavailable,
    FastEmbedEmbedding,
    HashEmbedding,
    cosine_similarity,
    get_default_embedder,
    make_embedder,
)

# --- embeddings: real provider, stub interface, safe fallback --------------


class _StubTextEmbedding:
    """Fake fastembed model: fixed 384-dim vectors per text."""

    def __init__(self):
        self.calls = []

    def embed(self, texts):
        self.calls.append(list(texts))
        out = []
        for text in texts:
            vec = [0.0] * 384
            vec[hash(text) % 384] = 1.0
            vec[(hash(text) // 7) % 384] += 0.5
            out.append(vec)
        return out


class _FailingTextEmbedding:
    def embed(self, texts):
        raise RuntimeError("model file missing")


def test_hash_embedding_still_deterministic():
    emb = HashEmbedding()
    vec = emb.embed("kopi tubruk pagi hari")
    assert vec == emb.embed("kopi tubruk pagi hari")
    assert len(vec) == 384
    assert abs(sum(x * x for x in vec) - 1.0) < 1e-6
    assert emb.name == "hash"


def test_fastembed_provider_with_stub_model():
    stub = _StubTextEmbedding()
    emb = FastEmbedEmbedding(model=stub)
    assert emb.dim == 384
    assert emb.name == "fastembed"
    vec = emb.embed("laptop asus vivobook")
    assert len(vec) == 384
    assert abs(sum(x * x for x in vec) - 1.0) < 1e-6
    assert vec == emb.embed("laptop asus vivobook")
    assert stub.calls == [["laptop asus vivobook"]] * 2


def test_fastembed_provider_rejects_wrong_dimension():
    class _WrongDim:
        def embed(self, texts):
            return [[0.1] * 128 for _ in texts]

    with pytest.raises(EmbeddingUnavailable):
        FastEmbedEmbedding(model=_WrongDim()).embed("hello")


def test_make_embedder_auto_uses_stub_when_given():
    emb = make_embedder("auto", model=_StubTextEmbedding())
    assert emb.name == "fastembed"
    assert len(emb.embed("test")) == 384


def test_make_embedder_auto_falls_back_to_hash_when_model_unavailable(monkeypatch):
    def _broken_model(self):
        raise EmbeddingUnavailable("no model")

    monkeypatch.setattr(FastEmbedEmbedding, "model", property(_broken_model))
    emb = make_embedder("auto")
    assert isinstance(emb, HashEmbedding)
    assert emb.name == "hash"


def test_make_embedder_hash_and_unknown_kind():
    assert isinstance(make_embedder("hash"), HashEmbedding)
    with pytest.raises(ValueError):
        make_embedder("sentence-transformers")


def test_default_embedder_returns_384_dim_normalized_vector():
    """Live wiring check: real fastembed model when cached, hash
    fallback otherwise. Either way the seam holds: dim 384,
    L2 normalized, same text gives the same vector."""
    emb = get_default_embedder()
    assert emb.dim == 384
    vec = emb.embed("harga laptop asus vivobook")
    assert len(vec) == 384
    assert abs(sum(x * x for x in vec) - 1.0) < 1e-6
    assert emb.embed("harga laptop asus vivobook") == vec
    similar = cosine_similarity(vec, emb.embed("laptop asus vivobook empat belas inci"))
    different = cosine_similarity(vec, emb.embed("tagihan listrik pln"))
    assert similar > different


# --- procedural: promotion, decay, contradiction ----------------


def _proc(status="trial", successful=1, failed=0, consecutive=0, agents=None):
    return {
        "id": "proc-1",
        "user_id": "user-1",
        "service": "check_pln",
        "version": 1,
        "step": ["open site", "read bill"],
        "output_schema": {},
        "required_checks": [],
        "status": status,
        "successful_runs": successful,
        "failed_runs": failed,
        "consecutive_failed_runs": consecutive,
        "agent_ids": list(agents or ["agent-a"]),
        "source_event_id": None,
        "created_at": None,
        "updated_at": None,
    }


def test_procedure_trial_becomes_verified_then_reused():
    """Behaviour change: three successes by two agents promote a
    trial procedure to verified, and only then is it reusable."""
    proc = _proc(status="trial", successful=1, agents=["agent-a"])
    proc = procedural.compute_run_update(proc, True, agent_id="agent-b")
    assert proc["status"] == "trial"  # only 2 runs so far
    assert proc["demoted"] is False
    proc = procedural.compute_run_update(proc, True, agent_id="agent-a")
    assert proc["status"] == "verified"
    assert proc["successful_runs"] == 3
    assert len(proc["agent_ids"]) == 2


def test_verified_procedure_survives_single_failure():
    """Revision 7 section 3.3: one failure does not unverify a
    procedure. It keeps its verified status and its successful
    runs, with one consecutive failure counted. (An earlier
    Brain-phase draft demoted on one failure; that was removed
    during integration because it broke the canary contract:
    run_canary only runs on verified procedures, so a demoted
    procedure could never record the second failure that makes
    it broken.)"""
    proc = _proc(status="verified", successful=3, agents=["agent-a", "agent-b"])
    updated = procedural.compute_run_update(proc, False, agent_id="agent-a")
    assert updated["status"] == "verified"
    assert updated["demoted"] is False
    assert updated["successful_runs"] == 3
    assert updated["consecutive_failed_runs"] == 1
    assert updated["failed_runs"] == 1


def test_verified_procedure_broken_after_two_consecutive_failures():
    proc = _proc(status="verified", successful=3, agents=["agent-a", "agent-b"])
    proc = procedural.compute_run_update(proc, False, agent_id="agent-a")
    assert proc["status"] == "verified"
    proc = procedural.compute_run_update(proc, False, agent_id="agent-a")
    assert proc["status"] == "broken"
    assert proc["consecutive_failed_runs"] == 2


def test_verified_procedure_recovers_after_failure_then_success():
    proc = _proc(status="verified", successful=3, agents=["agent-a", "agent-b"])
    proc = procedural.compute_run_update(proc, False, agent_id="agent-a")
    assert proc["status"] == "verified"
    proc = procedural.compute_run_update(proc, True, agent_id="agent-a")
    assert proc["status"] == "verified"
    assert proc["consecutive_failed_runs"] == 0
    assert proc["successful_runs"] == 4


def test_procedure_broken_after_two_consecutive_failures():
    proc = _proc(status="trial", successful=1)
    proc = procedural.compute_run_update(proc, False)
    assert proc["status"] == "trial"
    proc = procedural.compute_run_update(proc, False)
    assert proc["status"] == "broken"
    assert proc["consecutive_failed_runs"] == 2


def test_success_resets_consecutive_failures():
    proc = _proc(status="trial", successful=1, consecutive=1)
    updated = procedural.compute_run_update(proc, True)
    assert updated["consecutive_failed_runs"] == 0
    assert updated["status"] == "trial"


def test_stale_verified_procedure_decays_to_trial():
    old = datetime.now(UTC) - timedelta(days=120)
    proc = _proc(status="verified", successful=5)
    proc["updated_at"] = old
    assert procedural.is_stale(proc) is True
    decayed = procedural.apply_decay(proc)
    assert decayed["status"] == "trial"
    assert decayed["successful_runs"] == 0
    assert decayed["decayed"] is True


def test_fresh_or_trial_procedure_does_not_decay():
    recent = datetime.now(UTC) - timedelta(days=10)
    proc = _proc(status="verified", successful=5)
    proc["updated_at"] = recent
    assert procedural.is_stale(proc) is False
    assert procedural.apply_decay(proc)["decayed"] is False
    trial = _proc(status="trial", successful=1)
    trial["updated_at"] = datetime.now(UTC) - timedelta(days=200)
    assert procedural.is_stale(trial) is False


def test_contradiction_detected_but_never_silent():
    old = _proc()
    old["step"] = ["open site", "read bill", "save result"]
    new_step = ["open site", "read bill", "send result by email"]
    assert procedural.steps_conflict(old["step"], new_step) is True
    found = procedural.detect_contradiction([old], "check_pln", new_step)
    assert found is old
    # Identical steps: no conflict. Wholly different steps: no conflict.
    assert procedural.steps_conflict(old["step"], list(old["step"])) is False
    assert procedural.steps_conflict(old["step"], ["cook rice", "serve"]) is False
    assert procedural.detect_contradiction([old], "other_service", new_step) is None
    assert procedural.detect_contradiction([], "check_pln", new_step) is None


# --- consolidate: reflection into Proposals --------------------------------


def _episodes():
    return [
        {
            "type": "task_completed",
            "content": "check_pln: berhasil, tagihan terbaca",
            "event_id": "ev-1",
        },
        {
            "type": "task_completed",
            "content": "check_pln: berhasil, tagihan terbaca",
            "event_id": "ev-2",
        },
        {
            "type": "user_message",
            "content": "I always prefer the cheapest price",
            "event_id": "ev-3",
        },
        {
            "type": "user_message",
            "content": "I always prefer the cheapest price",
            "event_id": "ev-4",
        },
    ]


def test_reflection_turns_repeated_success_into_inference():
    inferences = consolidate.reflect_episodes(_episodes())
    by_key = {i["key"]: i for i in inferences}
    assert by_key["reliable_service"]["value"] == "check_pln"
    assert by_key["reliable_service"]["source"] == "inferred"
    assert set(by_key["reliable_service"]["evidence_event_ids"]) == {"ev-1", "ev-2"}
    assert by_key["user_preference"]["value"] == "I always prefer the cheapest price"
    assert set(by_key["user_preference"]["evidence_event_ids"]) == {"ev-3", "ev-4"}


def test_reflection_needs_two_pieces_of_evidence():
    single = [{"type": "task_completed", "content": "check_pln: berhasil", "event_id": "ev-1"}]
    assert consolidate.reflect_episodes(single) == []
    assert consolidate.reflect_episodes([]) == []


def test_reflection_detects_repeated_failure():
    episodes = [
        {
            "type": "task_completed",
            "content": "check_pln: gagal, situs tidak bisa dibuka",
            "event_id": "ev-1",
        },
        {"type": "task_completed", "content": "check_pln: gagal, timeout", "event_id": "ev-2"},
    ]
    inferences = consolidate.reflect_episodes(episodes)
    assert len(inferences) == 1
    assert inferences[0]["key"] == "repeated_failure"
    assert inferences[0]["value"] == "check_pln"


def test_reflection_proposes_never_writes_active_facts():
    """Inferred facts go to Proposals only: the propose callable
    receives source 'inferred', never an active fact."""
    seen = []

    def fake_propose(user_id, key, value, source, agent_id=None, event_id=None, dsn=None):
        seen.append({"key": key, "value": value, "source": source, "event_id": event_id})
        return {"key": key, "value": value, "source": source, "status": "proposed"}

    proposals = consolidate.reflect_and_propose("user-1", _episodes(), propose=fake_propose)
    assert len(proposals) == 2
    assert all(p["source"] == "inferred" for p in seen)
    assert all(p["status"] == "proposed" for p in proposals)
    assert {p["key"] for p in seen} == {"reliable_service", "user_preference"}


# --- episodic: dual-level retrieval ----------------------------------------


def test_extract_entities_and_concepts():
    assert "sm-a546e" in episodic.extract_entities("Samsung Galaxy SM-A546E price")
    assert "asus vivobook" in episodic.extract_entities("Asus Vivobook 14 price")
    concepts = episodic.extract_concepts("harga laptop asus vivobook")
    assert "laptop" in concepts and "asus" in concepts
    assert "yang" not in episodic.extract_concepts("laptop yang bagus")


def test_dual_level_entity_match_beats_concept_match():
    episodes = [
        {"id": "ep-concept", "content": "laptop asus vivobook is popular"},
        {"id": "ep-entity", "content": "Asus Vivobook 14 X1404 price dropped"},
        {"id": "ep-other", "content": "tagihan listrik pln bulan ini"},
    ]
    results = episodic.dual_level_search(episodes, "Asus Vivobook 14 X1404 price")
    assert results[0]["id"] == "ep-entity"
    assert results[0]["level"] == "entity"
    assert results[1]["id"] == "ep-concept"
    assert results[1]["level"] == "concept"
    assert results[2]["id"] == "ep-other"
    assert all(r["evidence_id"] == r["id"] for r in results)


def test_dual_level_vector_similarity_breaks_ties_with_embedder():
    episodes = [
        {"id": "ep-a", "content": "kopi tubruk pagi hari"},
        {"id": "ep-b", "content": "tagihan listrik pln"},
    ]
    results = episodic.dual_level_search(episodes, "kopi tubruk", embedder=HashEmbedding())
    assert results[0]["id"] == "ep-a"
    assert results[0]["similarity"] > results[1]["similarity"]


def test_dual_level_search_validates_input():
    with pytest.raises(ValueError):
        episodic.dual_level_search([], "")
    with pytest.raises(ValueError):
        episodic.dual_level_search([], "kopi", limit=0)


# --- graphs/identity: dual-level product ranking ----------------------------


def _products():
    return [
        {
            "id": "p-1",
            "brand": "Asus",
            "series": "Vivobook",
            "model_number": "X1404",
            "category": "laptop",
        },
        {
            "id": "p-2",
            "brand": "Asus",
            "series": "Zenbook",
            "model_number": "UX3405",
            "category": "laptop",
        },
        {
            "id": "p-3",
            "brand": "Samsung",
            "series": "Galaxy",
            "model_number": "SM-A546E",
            "category": "phone",
        },
    ]


def test_rank_products_entity_match_first():
    ranked = identity.rank_products(_products(), "Asus Vivobook X1404 price")
    assert ranked[0]["product"]["id"] == "p-1"
    assert ranked[0]["level"] == "entity"
    assert ranked[0]["score"] == 1.0
    assert ranked[1]["product"]["id"] == "p-2"
    assert ranked[1]["level"] == "concept"


def test_rank_products_drops_non_matching_products():
    ranked = identity.rank_products(_products(), "Samsung Galaxy SM-A546E")
    assert [r["product"]["id"] for r in ranked] == ["p-3"]
    assert ranked[0]["level"] == "entity"


def test_rank_products_validates_input():
    with pytest.raises(ValueError):
        identity.rank_products([], "")
