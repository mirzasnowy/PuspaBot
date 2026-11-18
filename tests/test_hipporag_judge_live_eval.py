"""Live evaluation of the R2 LLM triple judge (HippoRAG 2 filter).

The judge was built against a stub LLM (see
``tests/test_hipporag_llm_filter.py``). This file measures it
against the real cheapest router model,
``meta-llama/llama-3.1-8b-instruct`` (the reader role's cheapest,
1 Rp / 1k input tokens, 2 Rp / 1k output tokens), on the same
material: the 8-query price-graph eval set from
``tests/test_retrieval_eval.py`` and the 6-triple chain graph
whose stub verdicts are the reference.

Measured 2026-11-18, temperature 0.0, one batch per query
(22 triples, under the 50-triple batch size), via
``puspa.router.complete.complete_detailed`` with the default
1024-token budget unless noted:

- Deterministic filter (offline baseline, reproducible):
  recall@1 0.125, recall@3 0.375, recall@10 1.000.
- Live judge, shipped prompt (see below): recall@1 0.125,
  recall@3 0.250, recall@10 1.000. The judge does NOT beat the
  deterministic filter on this set: it matches at @10 and loses
  at @3, because the fraud report still ranks behind the
  seed-adjacent passages (promoting it needs the cross-encoder
  reranker, the documented follow-up, not the judge). One query
  ("Which sellers have a bad track record?") has no seed nodes
  and takes the vector fallback, so the eval makes 7 judge calls.
- Live judge, full eval, shipped prompt: 7 calls, 4330 input /
  2920 output tokens, Rp6 total, mean 12.5s, max 16.8s per call.
  132 of 154 decisions came from the LLM, 1 batch fell back to
  the deterministic rule (a malformed reply).
- Live judge, chain graph, shipped prompt: 5/6 verdicts agree
  with the stub in 3 runs, and the fraud report passage (p5) is
  reached in all 3. The one steady miss is
  ("promo", "is_a", "fake promo"), which the model drops.
- Live judge, price-graph query 1, shipped prompt: 22/22
  decisions from the LLM, 643 input / 496 output tokens, Rp1,
  1.3s; it keeps the buyer report and the fraud report triples.

What the shipped prompt fixed (measured, same model):

- Before, the prompt asked for "one short sentence" per reason:
  a 22-triple reply ran past the default 1024-token budget
  (1024 output tokens, truncated mid-JSON), the parse failed,
  and the whole batch silently fell back to the deterministic
  rule. The prompt now asks for at most five words per reason,
  so a 22-triple reply fits in ~500 output tokens.
- Before, the prompt said nothing about reports: the model
  judged relevance as "mentions the product or its price" and
  dropped every fraud-report triple (chain-graph agreement
  3/6, then 2/6 on re-run; p5 never reached). The prompt now
  says that, for a trustworthiness question, a report,
  complaint, or fraud involving the seller, deal, or product
  DOES help. Chain-graph agreement rose to 5/6 with p5 reached.

How to run: the replay and deterministic tests below run in the
normal suite and touch no network. The live tests are skipped
unless ``PUSPA_LIVE_JUDGE_EVAL=1`` is set; they then call the
live model (7 calls for the eval, 1 for query 1, 1 for the chain
graph) and assert the thresholds above, not exact verdicts,
because the model is nondeterministic even at temperature 0.0
(the old prompt scored 3/6 then 2/6 on identical input). A
live test that cannot reach the model (no key, no network)
skips instead of failing, so a missing key is reported as a
skip, never faked as a pass.
"""

import os

import pytest

from puspa.memory.embeddings import HashEmbedding
from puspa.retrieval.hipporag import HippoRAGRetriever
from puspa.retrieval.reranker import DeterministicReranker
from puspa.retrieval.triple_filter import (
    LLMTripleJudge,
    build_judge_prompt,
    parse_judge_reply,
)
from puspa.router.complete import (
    CompletionError,
    complete_detailed,
)
from tests.test_hipporag_llm_filter import (
    QUERY as CHAIN_QUERY,
    STUB_DECISIONS,
    TRIPLES as CHAIN_TRIPLES,
    build_chain_graph,
)
from tests.test_retrieval_eval import QUERIES
from tests.test_retrieval_hipporag import build_price_graph

EMBEDDER = HashEmbedding()

#: The cheapest reader model, from the router catalog.
LIVE_MODEL = "meta-llama/llama-3.1-8b-instruct"

#: Opt-in for the live tests (see the module docstring).
LIVE_ENV = "PUSPA_LIVE_JUDGE_EVAL"

#: The question the recorded reply below judged.
QUERY_1 = "Is the cheap Galaxy S24 deal trustworthy?"

#: Triples the live judge must keep on query 1: the buyer report
#: and the fraud report about the seller. (The model also keeps
#: the FR-91 triple, about the other seller; that over-keep is
#: recorded, not asserted, because it does not hurt this query.)
FRAUD_TRIPLES_QUERY_1 = {
    ("buyer ani", "reports", "toko gadget murah"),
    ("fraud", "reports", "fr-88"),
}

#: Triples the live judge must drop on query 1: other products,
#: the unrelated bill, and the other seller's promo.
IRRELEVANT_TRIPLES_QUERY_1 = {
    ("samsung galaxy a55", "costs", "tokopedia"),
    ("xiaomi redmi note 13", "costs", "shopee"),
    ("asus vivobook 14", "costs", "blibli"),
    ("march", "is_a", "rp250"),
    ("toko ponsel abal", "offers", "asus vivobook 14"),
}

#: The raw reply the live model gave for query 1 on 2026-11-18
#: (probe run, shipped prompt, 643 input / 496 output tokens,
#: Rp1, 1.3s). The replay tests below run this reply through the
#: real prompt builder, parser, and judge, so the verdict
#: quality measured live is checked on every suite run without
#: calling the model again. The triple order is the sorted edge
#: order the retriever hands the judge.
RECORDED_LIVE_REPLY_QUERY_1 = """{
  "decisions": [
    {"index": 0, "keep": false, "reason": "not about Galaxy S24"},
    {"index": 1, "keep": true, "reason": "reports about a deal"},
    {"index": 2, "keep": false, "reason": "not about Galaxy S24"},
    {"index": 3, "keep": false, "reason": "not about Galaxy S24"},
    {"index": 4, "keep": true, "reason": "fraud report about a deal"},
    {"index": 5, "keep": true, "reason": "fraud report about a deal"},
    {"index": 6, "keep": false, "reason": "not about Galaxy S24"},
    {"index": 7, "keep": false, "reason": "not about Galaxy S24"},
    {"index": 8, "keep": false, "reason": "not about Galaxy S24"},
    {"index": 9, "keep": false, "reason": "not about Galaxy S24"},
    {"index": 10, "keep": true, "reason": "costs of Galaxy S24"},
    {"index": 11, "keep": false, "reason": "not about Galaxy S24"},
    {"index": 12, "keep": false, "reason": "not about Galaxy S24"},
    {"index": 13, "keep": true, "reason": "offers Galaxy S24"},
    {"index": 14, "keep": true, "reason": "sells Galaxy S24"},
    {"index": 15, "keep": false, "reason": "not about Galaxy S24"},
    {"index": 16, "keep": false, "reason": "not about Galaxy S24"},
    {"index": 17, "keep": false, "reason": "not about Galaxy S24"},
    {"index": 18, "keep": false, "reason": "not about Galaxy S24"},
    {"index": 19, "keep": false, "reason": "not about Galaxy S24"},
    {"index": 20, "keep": false, "reason": "not about Galaxy S24"},
    {"index": 21, "keep": false, "reason": "not about Galaxy S24"}
  ]
}"""

#: Measured numbers, locked in (see the module docstring).
MEASURED = {
    # Deterministic filter, offline, reproducible.
    "deterministic": {"recall@1": 0.125, "recall@3": 0.375,
                      "recall@10": 1.0},
    # Live judge, shipped prompt, full eval (7 calls).
    "live": {"recall@1": 0.125, "recall@3": 0.25,
             "recall@10": 1.0, "calls": 7,
             "input_tokens": 4330, "output_tokens": 2920,
             "cost_rp": 6, "mean_latency_s": 12.5,
             "max_latency_s": 16.8, "llm_decisions": 132,
             "total_decisions": 154, "fallback_batches": 1},
    # Live judge, chain graph, shipped prompt (3 runs).
    "live_chain_agreement": 5,
    "live_chain_triples": 6,
}

live_only = pytest.mark.skipif(
    os.environ.get(LIVE_ENV) != "1",
    reason=f"live model call; set {LIVE_ENV}=1 to run")


class Meter:
    """A ``complete(prompt)`` callable that meters the live call.

    Same call the default path makes (reader role, cheapest
    model, the default 1024-token budget), plus token, cost, and
    latency accounting. The key comes from the existing BYOK
    path inside ``complete_detailed``; it is never printed here.
    """

    def __init__(self):
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.cost_rp = 0
        self.latencies = []

    def complete(self, prompt: str) -> str:
        import time

        start = time.monotonic()
        result = complete_detailed(
            prompt, role="reader", model=LIVE_MODEL, max_tokens=1024)
        self.latencies.append(time.monotonic() - start)
        self.calls += 1
        self.input_tokens += result.input_tokens
        self.output_tokens += result.output_tokens
        self.cost_rp += result.cost_rp or 0
        assert result.model == LIVE_MODEL
        return result.text

    @property
    def mean_latency(self) -> float:
        if not self.latencies:
            return 0.0
        return sum(self.latencies) / len(self.latencies)


def _recalls(ranked_lists):
    """recall@1/@3/@10 from one ranked list per query."""
    out = {1: 0, 3: 0, 10: 0}
    for (_, targets), ranked in zip(QUERIES, ranked_lists):
        for k in out:
            ids = {r["passage_id"] for r in ranked[:k]}
            if ids & targets:
                out[k] += 1
    return {k: value / len(QUERIES) for k, value in out.items()}


def _pipeline(question, *, llm_complete=None):
    graph = build_price_graph()
    retriever = HippoRAGRetriever(
        graph, embedder=EMBEDDER, llm_complete=llm_complete)
    reranker = DeterministicReranker(embedder=EMBEDDER)
    return retriever.retrieve(question, top_k=10, reranker=reranker)


def _skip_when_live_unavailable(exc):
    """Skip (do not fail, do not fake) when the model is out."""
    pytest.skip(f"live model unavailable: {exc}")


# ---------------------------------------------------------------------------
# The shipped prompt: compact replies, and reports count for trust
# ---------------------------------------------------------------------------

def test_judge_prompt_asks_for_short_reasons():
    # A 22-triple reply with one-sentence reasons ran past the
    # default 1024-token budget and truncated mid-JSON; five-word
    # reasons fit in ~500 output tokens.
    prompt = build_judge_prompt(
        QUERY_1, sorted(build_price_graph().edges))
    assert "at most five words" in prompt
    assert "JSON only" in prompt
    assert QUERY_1 in prompt


def test_judge_prompt_says_reports_count_for_trust():
    # The cheapest model read relevance as "mentions the product
    # or its price" and dropped every fraud-report triple; the
    # prompt now says a report, complaint, or fraud about the
    # seller, deal, or product helps a trustworthiness question.
    prompt = build_judge_prompt(QUERY_1, CHAIN_TRIPLES)
    assert "trustworthy" in prompt
    assert "fraud" in prompt


# ---------------------------------------------------------------------------
# Replay: the recorded live reply, through the real judge (offline)
# ---------------------------------------------------------------------------

def test_recorded_live_reply_parses_and_covers_every_triple():
    parsed = parse_judge_reply(RECORDED_LIVE_REPLY_QUERY_1, 22)
    assert len(parsed) == 22
    assert set(parsed) == set(range(22))
    assert all(isinstance(keep, bool)
               for keep, _ in parsed.values())


def test_recorded_live_reply_keeps_the_fraud_triples():
    triples = sorted(build_price_graph().edges)
    parsed = parse_judge_reply(RECORDED_LIVE_REPLY_QUERY_1, 22)
    kept = {triples[index] for index, (keep, _) in parsed.items()
            if keep}
    assert FRAUD_TRIPLES_QUERY_1 <= kept
    assert not (IRRELEVANT_TRIPLES_QUERY_1 & kept)


def test_recorded_live_reply_replayed_through_the_judge():
    # The replay uses the real prompt builder and judge: the
    # recorded reply is fed back as the model's answer, so the
    # verdicts the model gave live are what the judge applies.
    judge = LLMTripleJudge(lambda prompt: RECORDED_LIVE_REPLY_QUERY_1)
    graph = build_price_graph()
    retriever = HippoRAGRetriever(
        graph, embedder=EMBEDDER, triple_filter=judge)
    results = retriever.retrieve(QUERY_1, top_k=10)
    assert judge.calls == 1
    assert judge.fallback_batches == 0
    assert all(d.source == "llm"
               for d in retriever.last_filter_decisions)
    kept = {d.triple for d in retriever.last_filter_decisions
            if d.keep}
    assert FRAUD_TRIPLES_QUERY_1 <= kept
    assert not (IRRELEVANT_TRIPLES_QUERY_1 & kept)
    # The fraud report passage is reachable through the kept
    # chain, as the stub judge achieves on the chain graph.
    assert "d4#p0" in {r["passage_id"] for r in results}


# ---------------------------------------------------------------------------
# Deterministic baseline recalls (offline, reproducible)
# ---------------------------------------------------------------------------

def test_deterministic_baseline_recalls_are_as_measured():
    ranked = [_pipeline(question) for question, _ in QUERIES]
    recalls = _recalls(ranked)
    assert recalls[1] == MEASURED["deterministic"]["recall@1"]
    assert recalls[3] == MEASURED["deterministic"]["recall@3"]
    assert recalls[10] == MEASURED["deterministic"]["recall@10"]


# ---------------------------------------------------------------------------
# Live: the real model, through the real wiring (opt-in only)
# ---------------------------------------------------------------------------

@live_only
def test_live_judge_keeps_the_fraud_triples_on_query_1():
    meter = Meter()
    try:
        graph = build_price_graph()
        retriever = HippoRAGRetriever(
            graph, embedder=EMBEDDER, llm_complete=meter.complete)
        retriever.retrieve(QUERY_1, top_k=10)
    except (KeyError, CompletionError) as exc:
        _skip_when_live_unavailable(exc)
    decisions = retriever.last_filter_decisions
    assert meter.calls == 1
    # The judge really judged: at least half the decisions carry
    # the LLM as their source, not the deterministic fallback.
    llm_sourced = sum(1 for d in decisions if d.source == "llm")
    assert llm_sourced >= len(decisions) // 2
    kept = {d.triple for d in decisions if d.keep}
    assert FRAUD_TRIPLES_QUERY_1 <= kept
    assert not (IRRELEVANT_TRIPLES_QUERY_1 & kept)
    # Cheap and fast enough to run inside retrieval: the measured
    # query-1 call cost Rp1 and took 1.3s; the bounds below are
    # loose on purpose, they only catch a blow-up.
    assert meter.cost_rp <= 5
    assert meter.mean_latency < 60.0


@live_only
def test_live_judge_chain_graph_agreement_with_the_stub():
    meter = Meter()
    try:
        graph = build_chain_graph()
        retriever = HippoRAGRetriever(
            graph, embedder=EMBEDDER, llm_complete=meter.complete)
        results = retriever.retrieve(CHAIN_QUERY, top_k=10)
    except (KeyError, CompletionError) as exc:
        _skip_when_live_unavailable(exc)
    decisions = {d.triple: d
                 for d in retriever.last_filter_decisions}
    agree = sum(1 for triple in CHAIN_TRIPLES
                if decisions[triple].keep is STUB_DECISIONS[triple][0])
    # Measured 5/6 in 3 runs with the shipped prompt (the old
    # prompt scored 3/6, then 2/6). The threshold is below the
    # measured value because the model varies run to run.
    assert agree >= 4
    assert "p5" in {r["passage_id"] for r in results}


@live_only
def test_live_judge_eval_recalls_cost_and_latency():
    meter = Meter()
    ranked = []
    llm_decisions = 0
    total_decisions = 0
    try:
        for question, _ in QUERIES:
            graph = build_price_graph()
            retriever = HippoRAGRetriever(
                graph, embedder=EMBEDDER,
                llm_complete=meter.complete)
            ranked.append(retriever.retrieve(
                question, top_k=10,
                reranker=DeterministicReranker(embedder=EMBEDDER)))
            for decision in retriever.last_filter_decisions:
                total_decisions += 1
                if decision.source == "llm":
                    llm_decisions += 1
    except (KeyError, CompletionError) as exc:
        _skip_when_live_unavailable(exc)
    recalls = _recalls(ranked)
    # Measured with the shipped prompt: @1 0.125, @3 0.250,
    # @10 1.000. The @3 bar is the measured floor, deliberately
    # below the deterministic 0.375: the live judge does not beat
    # the rule on this set, and this test must not pretend it does.
    assert recalls[1] >= 0.125
    assert recalls[3] >= 0.25
    assert recalls[10] >= 0.875
    # Most decisions really came from the LLM (measured 132/154).
    assert llm_decisions >= total_decisions // 2
    # Cost and latency: measured Rp6 total, mean 12.5s, max 16.8s
    # for the 7 judge calls; the bounds only catch a blow-up.
    assert meter.calls == 7
    assert meter.cost_rp <= 50
    assert meter.mean_latency < 45.0
