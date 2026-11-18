"""Tests for the real cross-encoder rerankers.

Two models, each loaded once per module by a module-scoped
fixture, so the suite stays fast after the first load:

- sentence-transformers ``cross-encoder/ms-marco-MiniLM-L-6-v2``
  (about 90 MB, English MS MARCO), the light backend;
- FlagEmbedding ``BAAI/bge-reranker-v2-m3`` (about 2.3 GB,
  multilingual), the default backend since the 18 Nov 2026
  swap: it loads and scores on this dev VM (load about
  23-28 s, peak RSS 2,148 MB, total RAM 7,934 MB).

The tests prove each reranker is a real model, not a
deterministic scorer in disguise:

- the model promotes the passage that actually answers the
  query over a keyword-stuffed passage that the deterministic
  reranker ranks first;
- the bge model ranks Indonesian queries sensibly, where the
  English MS MARCO model is weak;
- the same (query, passage) pair always gets the same score;
- the batched rerank path agrees with single-pair scoring;
- the factory reuses the loaded model instead of reloading it;
- a missing model or a missing library fails loudly while the
  deterministic fallback keeps working.

One honest limit is locked in as a test, not hidden: for a
trust question ("Is the cheap Galaxy S24 deal trustworthy?")
both models rank the fraud report last, because a reranker
judges "does this passage answer the question", not "is this
passage evidence about trust". The swap does not close the
Wave 1 multi-hop recall gap.

All tests here are in-memory: no test body in this file touches
the shared Postgres database. (The shared tests/conftest.py
autouse fixture still creates schemas and truncates tables on
collection; that is unavoidable for any file under tests/.)
"""

import inspect
import sys

import pytest

from puspa.memory.embeddings import HashEmbedding
from puspa.retrieval.reranker import (
    DEFAULT_BGE_RERANKER_MODEL,
    CrossEncoderReranker,
    DeterministicReranker,
    RerankerUnavailable,
    make_reranker,
)

MODEL_NAME = "cross-encoder/ms-marco-MiniLM-L-6-v2"
BGE_MODEL_NAME = "BAAI/bge-reranker-v2-m3"

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
    {"passage_id": "p-answer", "text": "Samsung Galaxy S24 costs Rp12.999.000 at Tokopedia."},
    {
        "passage_id": "p-stuffed",
        "text": "Galaxy S24 price price price price price cheap deal discount promo sale.",
    },
    {
        "passage_id": "p-generic",
        "text": "The Galaxy S24 is a Samsung phone with a great camera and long battery life.",
    },
    {
        "passage_id": "p-offtopic",
        "text": "The seller never shipped the phone and buyers lost their money.",
    },
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
    assert [r["rerank_score"] for r in ranked_once] == [r["rerank_score"] for r in ranked_twice]


def test_cross_encoder_batch_scores_match_single_scores(cross_encoder):
    ranked = cross_encoder.rerank(QUERY, CANDIDATES)
    by_id = {r["passage_id"]: r["rerank_score"] for r in ranked}
    # The batched rerank path must agree with scoring each
    # pair on its own through the same model.
    for candidate in CANDIDATES:
        single = cross_encoder.score(QUERY, candidate["text"])["score"]
        assert by_id[candidate["passage_id"]] == pytest.approx(single, abs=1e-6)
    # And with a direct call to the underlying model.
    direct = cross_encoder.model.predict([(QUERY, c["text"]) for c in CANDIDATES])
    for candidate, value in zip(CANDIDATES, direct):
        assert by_id[candidate["passage_id"]] == pytest.approx(float(value), abs=1e-6)


def test_factory_reuses_the_loaded_model(cross_encoder):
    again = CrossEncoderReranker.from_sentence_transformers(MODEL_NAME)
    assert again.model is cross_encoder.model
    assert again.model_name == MODEL_NAME
    # The factory entry point shares the same loaded model too
    # (explicit light backend: the default backend is bge now).
    built = make_reranker("cross_encoder", backend="sentence_transformers")
    assert built.model is cross_encoder.model
    assert built.rerank(QUERY, CANDIDATES)[0]["passage_id"] == "p-answer"


def test_missing_model_fails_loudly_and_fallback_works():
    with pytest.raises(RerankerUnavailable) as excinfo:
        CrossEncoderReranker.from_sentence_transformers("cross-encoder/does-not-exist-xyz")
    assert "does-not-exist-xyz" in str(excinfo.value)
    # The failure is loud, not silent: the deterministic
    # fallback is a separate, explicit choice that still works.
    fallback = make_reranker("deterministic")
    assert isinstance(fallback, DeterministicReranker)
    ranked = fallback.rerank(QUERY, CANDIDATES)
    assert next(r["passage_id"] for r in ranked) == "p-stuffed"


# ---------------------------------------------------------------------------
# FlagEmbedding backend: BAAI/bge-reranker-v2-m3 (the default backend)
# ---------------------------------------------------------------------------

#: The Indonesian mirror of the English candidate set above:
#: one passage answers the query, one is keyword stuffing
#: ("harga" repeated, no answer), one is on-topic but has no
#: price, one is off-topic. Scores measured 18 Nov 2026 on
#: this set with the real bge model: answer 4.30, stuffed
#: 0.75, generic -1.43, off-topic -11.00.
ID_QUERY = "Berapa harga Galaxy S24?"
ID_CANDIDATES = [
    {"passage_id": "p-answer", "text": "Harga Samsung Galaxy S24 Rp12.999.000 di Tokopedia."},
    {
        "passage_id": "p-stuffed",
        "text": "harga harga harga harga harga Galaxy S24 murah diskon promo sale.",
    },
    {
        "passage_id": "p-generic",
        "text": "Galaxy S24 adalah ponsel Samsung dengan kamera bagus dan baterai tahan lama.",
    },
    {
        "passage_id": "p-offtopic",
        "text": "Penjual tidak pernah mengirim ponsel dan pembeli kehilangan uang mereka.",
    },
]

#: Trust questions, English and Indonesian. The passage that
#: matters is the fraud report, not the price answer. Scores
#: measured 18 Nov 2026: English stuffed -0.27, answer -4.18,
#: fraud -9.40; Indonesian stuffed -1.28, answer -4.47,
#: fraud -8.73. The fraud report is last in both languages:
#: the known Wave 1 gap, locked in below, not fixed by the
#: swap (see test_bge_trust_query_still_misses_the_fraud_report).
TRUST_QUERY = "Is the cheap Galaxy S24 deal trustworthy?"
TRUST_CANDIDATES = [
    {
        "passage_id": "p-fraud",
        "text": "The seller never shipped the phone and buyers lost their money.",
    },
    {
        "passage_id": "p-stuffed",
        "text": "Galaxy S24 price price price price price cheap deal discount promo sale.",
    },
    {"passage_id": "p-answer", "text": "Samsung Galaxy S24 costs Rp12.999.000 at Tokopedia."},
]
ID_TRUST_QUERY = "Apakah penawaran Galaxy S24 murah ini bisa dipercaya?"
ID_TRUST_CANDIDATES = [
    {
        "passage_id": "p-fraud",
        "text": "Penjual tidak pernah mengirim ponsel dan pembeli kehilangan uang mereka.",
    },
    {
        "passage_id": "p-stuffed",
        "text": "harga harga harga harga harga Galaxy S24 murah diskon promo sale.",
    },
    {"passage_id": "p-answer", "text": "Harga Samsung Galaxy S24 Rp12.999.000 di Tokopedia."},
]


@pytest.fixture(scope="module")
def bge_reranker():
    return CrossEncoderReranker.from_flagembedding(BGE_MODEL_NAME)


def test_bge_promotes_the_real_answer(bge_reranker):
    ranked = bge_reranker.rerank(QUERY, CANDIDATES)
    order = [r["passage_id"] for r in ranked]
    # Same shape as the MS MARCO test: the passage that
    # answers the query wins, the keyword stuffing does not,
    # and the off-topic passage is last.
    assert order[0] == "p-answer"
    assert order[-1] == "p-offtopic"
    assert order.index("p-answer") < order.index("p-stuffed")
    scores = [r["rerank_score"] for r in ranked]
    assert scores == sorted(scores, reverse=True)
    # Measured 18 Nov 2026: answer 4.63, stuffed 1.62.
    by_id = {r["passage_id"]: r["rerank_score"] for r in ranked}
    assert by_id["p-answer"] - by_id["p-stuffed"] > 1.0


def test_bge_indonesian_query_ranks_sensibly(bge_reranker):
    ranked = bge_reranker.rerank(ID_QUERY, ID_CANDIDATES)
    order = [r["passage_id"] for r in ranked]
    # The Indonesian answer wins, the Indonesian keyword
    # stuffing does not, and the off-topic passage is last.
    # This is the case the English MS MARCO model is weak on.
    assert order[0] == "p-answer"
    assert order[-1] == "p-offtopic"
    assert order.index("p-answer") < order.index("p-stuffed")
    scores = [r["rerank_score"] for r in ranked]
    assert scores == sorted(scores, reverse=True)
    # Measured 18 Nov 2026: answer 4.30, stuffed 0.75.
    by_id = {r["passage_id"]: r["rerank_score"] for r in ranked}
    assert by_id["p-answer"] - by_id["p-stuffed"] > 1.0


def test_bge_trust_query_still_misses_the_fraud_report(bge_reranker):
    # KNOWN LIMIT, locked in on purpose: for a trust question
    # the reranker judges "does this passage answer the
    # question", not "is this passage evidence about trust",
    # so the fraud report ranks last in both languages. The
    # bge swap does not close the Wave 1 multi-hop recall
    # gap; this test exists so nobody claims it does. If a
    # later reranker fixes it, update this test with the new
    # measured order instead of deleting it silently.
    for query, candidates in [
        (TRUST_QUERY, TRUST_CANDIDATES),
        (ID_TRUST_QUERY, ID_TRUST_CANDIDATES),
    ]:
        ranked = bge_reranker.rerank(query, candidates)
        order = [r["passage_id"] for r in ranked]
        assert order[-1] == "p-fraud"
        assert order[0] == "p-stuffed"


def test_bge_scores_are_deterministic(bge_reranker):
    answer = CANDIDATES[0]["text"]
    first = bge_reranker.score(QUERY, answer)["score"]
    second = bge_reranker.score(QUERY, answer)["score"]
    assert first == second
    assert first > 0.0
    ranked_once = bge_reranker.rerank(QUERY, CANDIDATES)
    ranked_twice = bge_reranker.rerank(QUERY, CANDIDATES)
    assert [r["rerank_score"] for r in ranked_once] == [r["rerank_score"] for r in ranked_twice]
    # And in Indonesian too.
    id_answer = ID_CANDIDATES[0]["text"]
    assert (
        bge_reranker.score(ID_QUERY, id_answer)["score"]
        == (bge_reranker.score(ID_QUERY, id_answer)["score"])
    )


def test_bge_batch_scores_match_single_scores(bge_reranker):
    ranked = bge_reranker.rerank(QUERY, CANDIDATES)
    by_id = {r["passage_id"]: r["rerank_score"] for r in ranked}
    # The batched rerank path must agree with scoring each
    # pair on its own through the same model. The tolerance is
    # looser than the MS MARCO test (1e-4 vs 1e-6) because
    # FlagEmbedding sorts pairs by length before batching, so
    # padding differs between the batch and the single-pair
    # call and the floats differ in the last bits.
    for candidate in CANDIDATES:
        single = bge_reranker.score(QUERY, candidate["text"])["score"]
        assert by_id[candidate["passage_id"]] == pytest.approx(single, abs=1e-4)
    # And with a direct call to the underlying model.
    direct = bge_reranker.model.compute_score([(QUERY, c["text"]) for c in CANDIDATES])
    for candidate, value in zip(CANDIDATES, direct):
        assert by_id[candidate["passage_id"]] == pytest.approx(float(value), abs=1e-4)


def test_bge_factory_reuses_the_loaded_model(bge_reranker):
    again = CrossEncoderReranker.from_flagembedding(BGE_MODEL_NAME)
    assert again.model is bge_reranker.model
    assert again.model_name == BGE_MODEL_NAME
    # The factory entry point with the explicit bge backend
    # shares the same loaded model too.
    built = make_reranker("cross_encoder", backend="bge")
    assert built.model is bge_reranker.model
    assert built.rerank(QUERY, CANDIDATES)[0]["passage_id"] == "p-answer"
    # The "flagembedding" spelling is the same backend.
    spelled = make_reranker("cross_encoder", backend="flagembedding")
    assert spelled.model is bge_reranker.model


def test_bge_is_the_default_cross_encoder_backend(bge_reranker):
    # No model load needed: the signature default says so.
    assert inspect.signature(make_reranker).parameters["backend"].default == "bge"
    assert (
        inspect.signature(CrossEncoderReranker.from_flagembedding).parameters["model_name"].default
        == BGE_MODEL_NAME
    )
    assert DEFAULT_BGE_RERANKER_MODEL == BGE_MODEL_NAME
    # And the default factory call really builds the bge
    # backend (reusing the already loaded model).
    built = make_reranker("cross_encoder")
    assert built.model is bge_reranker.model
    assert built.model_name == BGE_MODEL_NAME
    assert built.rerank(ID_QUERY, ID_CANDIDATES)[0]["passage_id"] == "p-answer"


def test_bge_missing_model_fails_loudly_and_fallback_works(bge_reranker):
    with pytest.raises(RerankerUnavailable) as excinfo:
        CrossEncoderReranker.from_flagembedding("BAAI/does-not-exist-xyz")
    assert "does-not-exist-xyz" in str(excinfo.value)
    # The deterministic fallback is a separate, explicit
    # choice that still works.
    fallback = make_reranker("deterministic")
    assert isinstance(fallback, DeterministicReranker)
    ranked = fallback.rerank(QUERY, CANDIDATES)
    assert next(r["passage_id"] for r in ranked) == "p-stuffed"


def test_bge_missing_library_fails_loudly(monkeypatch):
    # Hide the library from the import system: the factory
    # must raise RerankerUnavailable, never silently fall back.
    # A fresh model name forces the factory past the cache
    # lookup and into the import.
    monkeypatch.setitem(sys.modules, "FlagEmbedding", None)
    with pytest.raises(RerankerUnavailable) as excinfo:
        CrossEncoderReranker.from_flagembedding("BAAI/bge-reranker-v2-m3-missing-lib")
    assert "FlagEmbedding" in str(excinfo.value)
