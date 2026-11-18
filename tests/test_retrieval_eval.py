"""Multi-hop evaluation: pipeline vs the old keyword+vector baseline.

Hand-written, deterministic set on the price graph: product,
promo, seller, fraud report. The six multi-hop queries name a
product and ask about its seller, so the decisive passage (the
fraud report) shares no word with the query and only the graph
walk can reach it. The two control queries name the answer
directly, so the baseline should match the pipeline there.

Both systems rank the same units (one episode per passage) with
the same embedder (HashEmbedding), so the only difference is
the retrieval mechanism. The pipeline is the HippoRAG
retriever plus the deterministic reranker, the same shape the
live loop uses through `search_episodes_pipeline`.

All tests here are in-memory: no test body in this file touches
the shared Postgres database. (The shared tests/conftest.py
autouse fixture still creates schemas and truncates tables on
collection; that is unavoidable for any file under tests/.)
"""

from puspa.memory.embeddings import HashEmbedding
from puspa.memory.episodic import dual_level_search
from puspa.retrieval.hipporag import HippoRAGRetriever
from puspa.retrieval.reranker import DeterministicReranker
from tests.test_retrieval_hipporag import PRICE_DOCS, build_price_graph

EMBEDDER = HashEmbedding()

#: (query, target passage ids). A query hits when any target is
#: in the top-k.
QUERIES = [
    ("Is the cheap Galaxy S24 deal trustworthy?", {"d4#p0"}),
    ("Who should I avoid when buying Asus Vivobook 14?", {"d9#p0"}),
    ("What happened to buyers of the half-price Galaxy S24?",
     {"d3#p1", "d4#p0"}),
    ("What went wrong with the Galaxy S24 discount?", {"d4#p0"}),
    ("Is the S24 seller safe?", {"d3#p1", "d4#p0"}),
    ("Which sellers have a bad track record?", {"d4#p0", "d9#p0"}),
    ("What is the real price of Galaxy S24?", {"d1#p1"}),
    ("Which laptop seller offers free shipping?", {"d8#p1"}),
]


def _recall_at_k(rank_fn, k):
    hits = 0
    for question, targets in QUERIES:
        ids = {r.get("passage_id") or r.get("id")
               for r in rank_fn(question)[:k]}
        if ids & targets:
            hits += 1
    return hits / len(QUERIES)


def _baseline(question):
    graph = build_price_graph()
    episodes = [{"id": pid, "content": text}
                for pid, text in sorted(graph.passages.items())]
    return dual_level_search(episodes, question, embedder=EMBEDDER, limit=10)


def _pipeline(question):
    graph = build_price_graph()
    retriever = HippoRAGRetriever(graph, embedder=EMBEDDER)
    reranker = DeterministicReranker(embedder=EMBEDDER)
    return retriever.retrieve(question, top_k=10, reranker=reranker)


def test_eval_set_targets_exist_in_the_graph():
    graph = build_price_graph()
    for _, targets in QUERIES:
        for target in targets:
            assert target in graph.passages


def test_pipeline_beats_baseline_on_multi_hop_recall():
    # Measured 18 Nov 2026 on this set (8 queries):
    # recall@3:  baseline 0.250, pipeline 0.375
    # recall@5:  baseline 0.375, pipeline 0.375
    # recall@10: baseline 0.500, pipeline 1.000
    # The graph wins on reach (recall@10): the fraud reports sit
    # at ranks 6-9 for the pipeline and are mostly unreachable
    # for the baseline. At recall@5 the two tie, because the
    # decisive passage still ranks behind the seed-adjacent
    # passages; promoting it to the top 3 needs a real
    # cross-encoder judging relevance, which is the documented
    # follow-up, not something the deterministic reranker does.
    assert _recall_at_k(_pipeline, 3) >= _recall_at_k(_baseline, 3)
    assert _recall_at_k(_pipeline, 5) >= _recall_at_k(_baseline, 5)
    assert _recall_at_k(_pipeline, 10) > _recall_at_k(_baseline, 10)
    assert _recall_at_k(_pipeline, 10) >= 0.8
    assert _recall_at_k(_baseline, 10) < 1.0


def test_pipeline_keeps_the_control_queries():
    # The two control queries have keyword answers; the pipeline
    # must not lose them while chasing the graph.
    for question, targets in QUERIES[-2:]:
        pipe_ids = {r["passage_id"] for r in _pipeline(question)[:3]}
        base_ids = {r["id"] for r in _baseline(question)[:5]}
        assert pipe_ids & targets
        assert base_ids & targets
