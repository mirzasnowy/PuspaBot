"""Tests for LLM triple filtering in HippoRAG retrieval.

All tests here are in-memory: no test body in this file touches
the shared Postgres database. (The shared tests/conftest.py
autouse fixture still creates schemas and truncates tables on
collection; that is unavoidable for any file under tests/.)

The graph below is built by hand so every edge is under the
test's control. The query is "Is the cheap Galaxy S24 deal
trustworthy?":

- triples 1-2 touch the seed and are kept by both filters;
- triples 3-4 (promo is a fake promo, reported in fraud report
  FR-88) are two and three hops out and share no word with the
  query, so the deterministic filter drops them while an LLM
  judge that understands the question keeps them;
- triples 5-6 (Galaxy Tab A9, Toko Abal) share the word "galaxy"
  or touch a seed, so the deterministic filter keeps them, while
  an LLM judge that understands the question drops them as a
  different product from a different seller.

A stub LLM stands in for a real model: it reads the numbered
triples out of the judge's prompt and answers from a fixed
decision table, so the verdicts are fully controlled while the
prompt, the parsing, and the batching are exercised for real.
"""

import json
import re
from typing import Any

import pytest

from puspa.memory.embeddings import HashEmbedding
from puspa.retrieval.hipporag import HippoRAGRetriever
from puspa.retrieval.knowledge_graph import Entity, KnowledgeGraph, Triple
from puspa.retrieval.triple_filter import (
    DEFAULT_BATCH_SIZE,
    DeterministicTripleFilter,
    LLMTripleJudge,
    build_judge_prompt,
    parse_judge_reply,
)

QUERY = "Is the cheap Galaxy S24 deal trustworthy?"

#: The graph's edges, in sorted order (the order the judge sees).
TRIPLES = [
    ("fake promo", "reported_in", "fraud report fr-88"),
    ("galaxy tab a9", "sells", "toko abal"),
    ("promo", "is_a", "fake promo"),
    ("samsung galaxy s24", "mentioned_with", "galaxy tab a9"),
    ("samsung galaxy s24", "sells", "toko gadget murah"),
    ("toko gadget murah", "offers", "promo"),
]

#: What the stub LLM decides per triple: keep the chain that
#: answers the trustworthiness question, drop the other product.
STUB_DECISIONS = {
    ("fake promo", "reported_in", "fraud report fr-88"): (
        True,
        ("the fraud report answers whether the deal is trustworthy"),
    ),
    ("galaxy tab a9", "sells", "toko abal"): (
        False,
        ("Toko Abal sells a different product, irrelevant to the S24 deal"),
    ),
    ("promo", "is_a", "fake promo"): (
        True,
        ("a fake promo is exactly what makes the deal untrustworthy"),
    ),
    ("samsung galaxy s24", "mentioned_with", "galaxy tab a9"): (
        False,
        ("Galaxy Tab A9 is a different product, nothing to do with the S24 deal"),
    ),
    ("samsung galaxy s24", "sells", "toko gadget murah"): (
        True,
        "the seller of the phone the question asks about",
    ),
    ("toko gadget murah", "offers", "promo"): (True, "the deal the question asks about"),
}

_TRIPLE_LINE_RE = re.compile(r"^(\d+)\. \((.*?)\) -\[(.*?)\]-> \((.*?)\)$", re.MULTILINE)


class StubJudge:
    """A controllable stand-in for an LLM completion function.

    Reads the numbered triples out of each prompt it receives and
    answers from ``decisions``. Records every prompt and counts
    calls, so tests can prove batching happened.
    """

    def __init__(self, decisions):
        self.decisions = decisions
        self.prompts = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        found = {}
        for match in _TRIPLE_LINE_RE.finditer(prompt):
            index = int(match.group(1))
            found[index] = (match.group(2), match.group(3), match.group(4))
        reply: dict[str, list[dict[str, Any]]] = {"decisions": []}
        for index in sorted(found):
            keep, reason = self.decisions[found[index]]
            reply["decisions"].append({"index": index, "keep": keep, "reason": reason})
        return json.dumps(reply)

    @property
    def calls(self) -> int:
        return len(self.prompts)


def build_chain_graph() -> KnowledgeGraph:
    graph = KnowledgeGraph()
    nodes = (
        "samsung galaxy s24",
        "toko gadget murah",
        "promo",
        "fake promo",
        "fraud report fr-88",
        "galaxy tab a9",
        "toko abal",
    )
    for name in nodes:
        graph.nodes[name] = Entity(name=name, source_ids={"hand"})
    for subject, relation, obj in TRIPLES:
        graph.edges[(subject, relation, obj)] = Triple(
            subject=subject, relation=relation, object=obj, source_ids={"hand"}
        )
    graph.passages = {
        "p1": "Samsung Galaxy S24 costs Rp12.999.000 at Tokopedia.",
        "p2": "Toko Gadget Murah sells Samsung Galaxy S24.",
        "p3": "Toko Gadget Murah offers a promo for Samsung Galaxy S24.",
        "p4": "The promo is a fake promo.",
        "p5": "Fraud report FR-88 confirms the promo was fake.",
        "p6": "Galaxy Tab A9 is a tablet.",
        "p7": "Toko Abal is an unregistered seller.",
    }
    graph.passage_entities = {
        "p1": {"samsung galaxy s24"},
        "p2": {"toko gadget murah", "samsung galaxy s24"},
        "p3": {"toko gadget murah", "promo", "samsung galaxy s24"},
        "p4": {"promo", "fake promo"},
        "p5": {"fraud report fr-88"},
        "p6": {"galaxy tab a9"},
        "p7": {"toko abal"},
    }
    graph.doc_passages = {"hand": sorted(graph.passages)}
    return graph


def graph_score(results, passage_id):
    """Graph score of one passage, 0.0 when it is not returned."""
    for result in results:
        if result["passage_id"] == passage_id:
            return result["graph_score"]
    return 0.0


def passage_ids(results):
    return [r["passage_id"] for r in results]


# ---------------------------------------------------------------------------
# The prompt and the reply parser
# ---------------------------------------------------------------------------


def test_judge_prompt_lists_every_triple_numbered():
    prompt = build_judge_prompt(QUERY, TRIPLES)
    assert QUERY in prompt
    for index, (subject, relation, obj) in enumerate(TRIPLES):
        assert f"{index}. ({subject}) -[{relation}]-> ({obj})" in prompt
    assert "JSON only" in prompt


def test_parse_judge_reply_accepts_a_full_verdict():
    reply = json.dumps(
        {
            "decisions": [
                {"index": 0, "keep": True, "reason": "helps"},
                {"index": 1, "keep": False, "reason": "off topic"},
            ]
        }
    )
    assert parse_judge_reply(reply, 2) == {0: (True, "helps"), 1: (False, "off topic")}


def test_parse_judge_reply_rejects_bad_replies():
    with pytest.raises(ValueError):
        parse_judge_reply("not json", 1)
    with pytest.raises(ValueError):
        parse_judge_reply(json.dumps({"decisions": []}), 1)
    with pytest.raises(ValueError):
        parse_judge_reply(
            json.dumps(
                {
                    "decisions": [
                        {"index": 0, "keep": True, "reason": "helps"},
                        {"index": 0, "keep": False, "reason": "twice"},
                    ]
                }
            ),
            2,
        )
    with pytest.raises(ValueError):
        parse_judge_reply(
            json.dumps(
                {
                    "decisions": [
                        {"index": 5, "keep": True, "reason": "out of range"},
                    ]
                }
            ),
            1,
        )
    with pytest.raises(ValueError):
        parse_judge_reply(
            json.dumps(
                {
                    "decisions": [
                        {"index": 0, "keep": "yes", "reason": "not a bool"},
                    ]
                }
            ),
            1,
        )
    with pytest.raises(ValueError):
        parse_judge_reply(
            json.dumps(
                {
                    "decisions": [
                        {"index": 0, "keep": True, "reason": ""},
                    ]
                }
            ),
            1,
        )


# ---------------------------------------------------------------------------
# The LLM judge itself: batching, verdicts, fallback
# ---------------------------------------------------------------------------


def test_judge_calls_the_llm_once_for_a_batch():
    stub = StubJudge(STUB_DECISIONS)
    judge = LLMTripleJudge(stub.complete)
    assert judge.batch_size == DEFAULT_BATCH_SIZE
    decisions = judge.filter(
        QUERY, TRIPLES, seeds={"samsung galaxy s24"}, query_concepts=["cheap", "galaxy", "s24"]
    )
    assert stub.calls == 1
    assert len(decisions) == len(TRIPLES)
    for decision, triple in zip(decisions, TRIPLES):
        assert decision.triple == triple
        keep, reason = STUB_DECISIONS[triple]
        assert decision.keep is keep
        assert decision.reason == reason
        assert decision.source == "llm"


def test_judge_splits_large_batches_and_counts_calls():
    stub = StubJudge(STUB_DECISIONS)
    judge = LLMTripleJudge(stub.complete, batch_size=2)
    judge.filter(QUERY, TRIPLES)
    assert stub.calls == 3
    assert judge.calls == 3


def test_judge_makes_no_call_for_no_triples():
    stub = StubJudge(STUB_DECISIONS)
    judge = LLMTripleJudge(stub.complete)
    assert judge.filter(QUERY, []) == []
    assert stub.calls == 0


def test_judge_rejects_a_non_callable_and_a_bad_batch_size():
    with pytest.raises(ValueError):
        LLMTripleJudge("not a callable")
    with pytest.raises(ValueError):
        LLMTripleJudge(lambda prompt: "{}", batch_size=0)


def test_judge_falls_back_to_deterministic_on_a_bad_reply():
    judge = LLMTripleJudge(lambda prompt: "this is not json")
    decisions = judge.filter(
        QUERY,
        TRIPLES,
        seeds={"samsung galaxy s24"},
        query_concepts=["cheap", "galaxy", "s24", "deal", "trustworthy"],
    )
    # The bad reply must not drop the graph: the deterministic
    # rule decides instead, and says so.
    assert judge.fallback_batches == 1
    assert all(d.source == "deterministic" for d in decisions)
    assert all("LLM judge failed" in d.reason for d in decisions)
    kept = {d.triple for d in decisions if d.keep}
    assert ("samsung galaxy s24", "sells", "toko gadget murah") in kept
    assert ("promo", "is_a", "fake promo") not in kept


def test_judge_falls_back_to_deterministic_when_the_call_raises():
    def boom(prompt):
        raise RuntimeError("model unavailable")

    judge = LLMTripleJudge(boom)
    decisions = judge.filter(QUERY, TRIPLES, seeds=set(), query_concepts=[])
    assert judge.fallback_batches == 1
    assert all(d.source == "deterministic" for d in decisions)
    # With no seeds and no concepts nothing survives the rule.
    assert not any(d.keep for d in decisions)


# ---------------------------------------------------------------------------
# Judge versus deterministic rule: the verdicts genuinely differ
# ---------------------------------------------------------------------------


def test_llm_keeps_what_the_rule_drops_and_drops_what_it_keeps():
    seeds = {"samsung galaxy s24", "galaxy tab a9", "toko gadget murah", "toko abal"}
    concepts = ["cheap", "deal", "galaxy", "s24", "trustworthy"]
    rule = DeterministicTripleFilter()
    rule_kept = {
        d.triple
        for d in rule.filter(QUERY, TRIPLES, seeds=seeds, query_concepts=concepts)
        if d.keep
    }
    stub = StubJudge(STUB_DECISIONS)
    judge = LLMTripleJudge(stub.complete)
    llm_kept = {
        d.triple
        for d in judge.filter(QUERY, TRIPLES, seeds=seeds, query_concepts=concepts)
        if d.keep
    }
    # Semantically relevant but sharing no word with the query:
    # kept by the LLM, dropped by the rule.
    assert ("promo", "is_a", "fake promo") in llm_kept
    assert ("promo", "is_a", "fake promo") not in rule_kept
    assert ("fake promo", "reported_in", "fraud report fr-88") in llm_kept
    assert ("fake promo", "reported_in", "fraud report fr-88") not in rule_kept
    # Sharing a word but saying nothing about the question: kept
    # by the rule, dropped by the LLM.
    assert ("samsung galaxy s24", "mentioned_with", "galaxy tab a9") in rule_kept
    assert ("samsung galaxy s24", "mentioned_with", "galaxy tab a9") not in llm_kept
    assert ("galaxy tab a9", "sells", "toko abal") in rule_kept
    assert ("galaxy tab a9", "sells", "toko abal") not in llm_kept


# ---------------------------------------------------------------------------
# The retriever with the judge: the filtered walk really changes
# ---------------------------------------------------------------------------


def test_retriever_without_llm_uses_the_deterministic_filter():
    graph = build_chain_graph()
    retriever = HippoRAGRetriever(graph, embedder=HashEmbedding())
    results = retriever.retrieve(QUERY, top_k=10)
    assert all(d.source == "deterministic" for d in retriever.last_filter_decisions)
    assert len(retriever.last_filter_decisions) == len(TRIPLES)
    kept = {d.triple for d in retriever.last_filter_decisions if d.keep}
    assert ("promo", "is_a", "fake promo") not in kept
    # The fraud report is cut off from the walk: the node is
    # unreachable from every seed, so its passage is not returned
    # at all.
    assert "p5" not in passage_ids(results)
    assert graph_score(results, "p5") == 0.0


def test_retriever_with_llm_judge_reaches_the_fraud_report():
    graph = build_chain_graph()
    stub = StubJudge(STUB_DECISIONS)
    retriever = HippoRAGRetriever(graph, embedder=HashEmbedding(), llm_complete=stub.complete)
    results = retriever.retrieve(QUERY, top_k=10)
    assert stub.calls == 1
    assert all(d.source == "llm" for d in retriever.last_filter_decisions)
    assert len(retriever.last_filter_decisions) == len(TRIPLES)
    kept = {d.triple for d in retriever.last_filter_decisions if d.keep}
    assert ("promo", "is_a", "fake promo") in kept
    assert ("fake promo", "reported_in", "fraud report fr-88") in kept
    assert ("samsung galaxy s24", "mentioned_with", "galaxy tab a9") not in kept
    # The walk now reaches the fraud report through the kept
    # chain, and the unrelated seller's passage loses the walk:
    # compare against the deterministic retriever on the same graph.
    plain = HippoRAGRetriever(graph, embedder=HashEmbedding()).retrieve(QUERY, top_k=10)
    assert "p5" not in passage_ids(plain)
    assert "p5" in passage_ids(results)
    assert graph_score(results, "p5") > 0.05
    assert graph_score(results, "p7") < 0.5 * graph_score(plain, "p7")
    assert graph_score(plain, "p7") > 0.05
    # The fraud report now outranks the unrelated seller passage
    # on graph score.
    assert graph_score(results, "p5") > graph_score(results, "p7")


def test_retriever_accepts_a_judge_object_and_rejects_both_seams():
    graph = build_chain_graph()
    stub = StubJudge(STUB_DECISIONS)
    judge = LLMTripleJudge(stub.complete)
    retriever = HippoRAGRetriever(graph, embedder=HashEmbedding(), triple_filter=judge)
    retriever.retrieve(QUERY, top_k=5)
    assert stub.calls == 1
    assert all(d.source == "llm" for d in retriever.last_filter_decisions)
    with pytest.raises(ValueError):
        HippoRAGRetriever(graph, triple_filter=judge, llm_complete=stub.complete)


def test_retriever_records_every_decision_with_a_reason():
    graph = build_chain_graph()
    stub = StubJudge(STUB_DECISIONS)
    retriever = HippoRAGRetriever(graph, embedder=HashEmbedding(), llm_complete=stub.complete)
    retriever.retrieve(QUERY, top_k=5)
    by_triple = {d.triple: d for d in retriever.last_filter_decisions}
    assert set(by_triple) == set(TRIPLES)
    for triple, decision in by_triple.items():
        keep, reason = STUB_DECISIONS[triple]
        assert decision.keep is keep
        assert decision.reason == reason
        assert isinstance(decision.keep, bool)
