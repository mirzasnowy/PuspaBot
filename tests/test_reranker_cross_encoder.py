"""Tests for the real cross-encoder reranker (sentence-transformers).

The model (cross-encoder/ms-marco-MiniLM-L-6-v2, about 90 MB)
is loaded once per module by a module-scoped fixture: every
test in this file shares that one loaded model, so the suite
stays fast after the first load. The tests prove the reranker
is a real model, not a deterministic scorer in disguise:

- the model promotes the passage that actually answers the
  query over a keyword-stuffed passage that the deterministic
  reranker ranks first;
- the same (query, passage) pair always gets the same score;
- the batched rerank path agrees with single-pair scoring;
- the factory reuses the loaded model instead of reloading it;
- a missing model fails loudly while the deterministic
  fallback keeps working.

All tests here are in-memory: no test body in this file touches
the shared Postgres database. (The shared tests/conftest.py
autouse fixture still creates schemas and truncates tables on
collection; that is unavoidable for any file under tests/.)
"""

import pytest

from puspa.memory.embeddings import HashEmbedding
from puspa.retrieval.reranker import (
    CrossEncoderReranker,
    DeterministicReranker,
    RerankerUnavailable,
    make_reranker,
)

MODEL_NAME = "cross-encoder/ms-marco-MiniLM-L-6-v2"

QUERY = "What is the price of Galaxy S24?"

#: One passage answers the query, one is keyword stuffing
#: ("price" repeated, no answer), one is on-topic but has no
#: price, one is off-topic. The deterministic reranker scores
#: lexical overlap, so it ranks the stuffed passage first; the
#: cross-encoder reads the pair, so it must rank the answer
#: first. Scores measured 18 Nov 2026 on this set:
#: cross-encoder answer 9.33, stuffed 5.07, generic 1.74;
#: deterministic stuffed 0.487, generic 0.347, answer 0.301.
CANDIDATES = [
    {"passage_id": "p-answer",
     "text": "Samsung Galaxy S24 costs Rp12.999.000 at Tokopedia."},
    {"passage_id": "p-stuffed",
     "text": "Galaxy S24 price price price price price cheap deal "
             "discount promo sale."},
    {"passage_id": "p-generic",
     "text": "The Galaxy S24 is a Samsung phone with a great camera "
             "and long battery life."},
    {"passage_id": "p-offtopic",
     "text": "The seller never shipped the phone and buyers lost "
             "their money."},
]


@pytest.fixture(scope="module")
def cross_encoder():
    return CrossEncoderReranker.from_sentence_transformers(MODEL_NAME)


def test_cross_encoder_promotes_the_real_answer(cross_encoder):
    ranked = cross_encoder.rerank(QUERY, CANDIDATES)
    order = [r["passage_id"] for r in ranked]
    # The passage that answers the query wins, the keyword
    # stuffing does not, and the off-topic passage is last.
    assert order[0] == "p-answer"
    assert order[-1] == "p-offtopic"
    assert order.index("p-answer") < order.index("p-stuffed")
    scores = [r["rerank_score"] for r in ranked]
    assert scores == sorted(scores, reverse=True)
    # The answer beats the stuffing by a wide margin, not a
    # rounding error: this is the model judging relevance.
    by_id = {r["passage_id"]: r["rerank_score"] for r in ranked}
    assert by_id["p-answer"] - by_id["p-stuffed"] > 1.0


def test_cross_encoder_changes_the_deterministic_order(cross_encoder):
    deterministic = DeterministicReranker(embedder=HashEmbedding())
    det_ranked = deterministic.rerank(QUERY, CANDIDATES)
    det_order = [r["passage_id"] for r in det_ranked]
    # The deterministic reranker trusts word overlap: the
    # stuffed passage wins and the real answer is third.
    assert det_order[0] == "p-stuffed"
    assert det_order.index("p-answer") == 2
    ce_ranked = cross_encoder.rerank(QUERY, CANDIDATES)
    ce_order = [r["passage_id"] for r in ce_ranked]
    # The model flips the order: the answer moves from rank 3
    # to rank 1, the stuffing drops to rank 2.
    assert ce_order[0] == "p-answer"
    assert ce_order != det_order


def test_cross_encoder_scores_are_consistent(cross_encoder):
    answer = CANDIDATES[0]["text"]
    first = cross_encoder.score(QUERY, answer)["score"]
    second = cross_encoder.score(QUERY, answer)["score"]
    assert first == second
    assert first > 0.0
    ranked_once = cross_encoder.rerank(QUERY, CANDIDATES)
    ranked_twice = cross_encoder.rerank(QUERY, CANDIDATES)
    assert [r["rerank_score"] for r in ranked_once] == [
        r["rerank_score"] for r in ranked_twice]


def test_cross_encoder_batch_scores_match_single_scores(cross_encoder):
    ranked = cross_encoder.rerank(QUERY, CANDIDATES)
    by_id = {r["passage_id"]: r["rerank_score"] for r in ranked}
    # The batched rerank path must agree with scoring each
    # pair on its own through the same model.
    for candidate in CANDIDATES:
        single = cross_encoder.score(
            QUERY, candidate["text"])["score"]
        assert by_id[candidate["passage_id"]] == pytest.approx(
            single, abs=1e-6)
    # And with a direct call to the underlying model.
    direct = cross_encoder.model.predict(
        [(QUERY, c["text"]) for c in CANDIDATES])
    for candidate, value in zip(CANDIDATES, direct):
        assert by_id[candidate["passage_id"]] == pytest.approx(
            float(value), abs=1e-6)


def test_factory_reuses_the_loaded_model(cross_encoder):
    again = CrossEncoderReranker.from_sentence_transformers(MODEL_NAME)
    assert again.model is cross_encoder.model
    assert again.model_name == MODEL_NAME
    # The factory entry point shares the same loaded model too.
    built = make_reranker("cross_encoder")
    assert built.model is cross_encoder.model
    assert built.rerank(QUERY, CANDIDATES)[0]["passage_id"] == "p-answer"


def test_missing_model_fails_loudly_and_fallback_works():
    with pytest.raises(RerankerUnavailable) as excinfo:
        CrossEncoderReranker.from_sentence_transformers(
            "cross-encoder/does-not-exist-xyz")
    assert "does-not-exist-xyz" in str(excinfo.value)
    # The failure is loud, not silent: the deterministic
    # fallback is a separate, explicit choice that still works.
    fallback = make_reranker("deterministic")
    assert isinstance(fallback, DeterministicReranker)
    ranked = fallback.rerank(QUERY, CANDIDATES)
    assert [r["passage_id"] for r in ranked][0] == "p-stuffed"
