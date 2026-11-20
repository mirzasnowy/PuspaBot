"""Tests for the retrieval benchmark (BM25 vs vectors vs HippoRAG)."""

from puspa.eval.retrieval_sets import (
    PASSAGES,
    compare_retrievers,
    internal_questions,
    public_question_slots,
)


def test_internal_questions_have_verified_gold_answers():
    questions = internal_questions()
    assert len(questions) == 50
    for question in questions:
        assert question.gold_answer
        assert question.gold_passages
        for doc_id in question.gold_passages:
            assert doc_id in PASSAGES
        gold_texts = " ".join(PASSAGES[doc_id] for doc_id in question.gold_passages)
        assert question.gold_answer.lower() in gold_texts.lower(), question.id


def test_public_questions_are_empty_slots():
    slots = public_question_slots()
    assert len(slots) == 50
    assert all(s.status == "empty_slot" for s in slots)
    assert all(s.question == "" for s in slots)
    assert all(s.origin == "public_2wikimultihopqa" for s in slots)


def test_comparison_reports_all_three_retrievers():
    comparison = compare_retrievers(top_k=5)
    assert set(comparison) == {"bm25", "vectors", "hipporag"}
    for _name, scores in comparison.items():
        assert scores["questions"] == 50
        assert 0.0 <= scores["recall_at_5"] <= 1.0
        assert 0.0 <= scores["answer_accuracy"] <= 1.0
        assert scores["hits"] == int(scores["recall_at_5"] * 50)
        assert scores["answered"] == int(scores["answer_accuracy"] * 50)


def test_comparison_is_deterministic():
    first = compare_retrievers(top_k=5)
    second = compare_retrievers(top_k=5)
    assert first == second


def test_hipporag_does_not_beat_plain_vectors_on_this_set():
    # Measured 2026-11-20 on the 50 internal questions, re-measured
    # after the E-16 follow-up (2026-11-20): BM25 0.84, vectors
    # 0.86, HippoRAG 0.84. The follow-up fixed one real ranking
    # defect (several sentence chunks of the same two or three
    # documents filled the top-5 shortlist; the retriever now
    # returns one result per document), which lifted HippoRAG
    # recall from 0.74 to 0.84. Answer accuracy stays 0.74: the
    # answer sentence of a trust report carries no entity and the
    # stub hash embedding cannot match "trustworthy" to "trusted"
    # or "never shipped", so that gap needs a real embedding
    # model, not a ranking tweak. Pin the numbers: if the
    # retriever changes, re-measure instead of assuming.
    comparison = compare_retrievers(top_k=5)
    assert comparison["vectors"]["recall_at_5"] == 0.86
    assert comparison["bm25"]["recall_at_5"] == 0.84
    assert comparison["hipporag"]["recall_at_5"] == 0.84
    assert comparison["hipporag"]["recall_at_5"] < comparison["vectors"]["recall_at_5"]
