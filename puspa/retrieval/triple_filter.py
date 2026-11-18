"""LLM triple filtering for HippoRAG retrieval.

HippoRAG 2 filters the knowledge graph before the PageRank walk:
an LLM judges each (subject, relation, object) triple against the
query and drops the ones that do not help answer it, so the walk
does not spread through unrelated parts of the graph. That is what
this module implements, in Puspa's own code.

The seam follows the one ``puspa.retrieval.knowledge_graph`` already
uses for index-time extraction (``JsonTripleExtractor``): the judge
takes a ``complete(prompt)`` callable returning a string, and makes
no network call and holds no key itself. A completion function from
the provider layer plugs in there when a key exists. Until one is
wired, the deterministic rule stays as the fallback, and every
decision records which source made it, so a retrieval that ran
without an LLM is visible as such instead of silently pretending
the LLM judged it.

The judge sends triples to the LLM in batches: one call judges
many triples and returns one keep/drop decision plus a short reason
per triple. Judging one triple per call would multiply the cost of
every retrieval by the size of the graph, and the filter runs in
the middle of retrieval, where latency matters.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

_TOKEN_RE = re.compile(r"[a-z0-9]+")

#: Triples per LLM call. Fifty short triples fit comfortably in one
#: prompt and one JSON reply; larger graphs are split into several
#: calls by ``LLMTripleJudge``.
DEFAULT_BATCH_SIZE = 50


@dataclass
class TripleDecision:
    """One filter verdict: keep or drop a triple, and why.

    ``source`` is ``"llm"`` when the LLM judge made the call and
    ``"deterministic"`` when the rule-based fallback did. The
    reason is a short human-readable sentence, recorded so a
    filtered walk can be inspected afterwards instead of trusted
    blind.
    """

    subject: str
    relation: str
    object: str
    keep: bool
    reason: str
    source: str

    @property
    def triple(self) -> tuple:
        """The judged triple as (subject, relation, object)."""
        return (self.subject, self.relation, self.object)


class TripleFilter:
    """Interface for query-to-triple filtering.

    ``filter(query, triples, seeds=..., query_concepts=...)``
    returns one ``TripleDecision`` per triple, in the same order.
    Any object with that method works: the deterministic filter,
    the LLM judge, or a future filter.
    """

    def filter(self, query, triples, *, seeds=(),
               query_concepts=()) -> list:
        raise NotImplementedError


def _lexical_overlap(query_concepts, text: str) -> float:
    if not query_concepts:
        return 0.0
    tokens = set(_TOKEN_RE.findall(text.lower()))
    return len(set(query_concepts) & tokens) / len(query_concepts)


class DeterministicTripleFilter(TripleFilter):
    """Rule-based triple filter: no model, no network, no cost.

    A triple survives when it touches a seed node, touches a node
    next to a seed (so the walk can reach two hops out), or shares
    a concept token with the query. This is the filter HippoRAG
    retrieval used before the LLM judge existed, and it is still
    the fallback whenever no LLM is available. It is honest about
    its limits: it only sees shared words, so a triple that is
    relevant by meaning but shares no word with the query is
    dropped, and a triple that shares a word but means nothing for
    the query is kept.
    """

    def filter(self, query, triples, *, seeds=(),
               query_concepts=()) -> list:
        if not isinstance(query, str) or query == "":
            raise ValueError("query must be a non-empty string")
        near_seeds = set(seeds)
        # The near-seed set needs the graph's adjacency, which the
        # filter does not own; the caller passes the seed nodes and
        # the filter treats "touches a seed" strictly. The
        # one-hop-neighbor widening lives in the retriever, which
        # hands the widened set in as ``seeds``.
        decisions = []
        for subject, relation, obj in triples:
            triple_tokens = set(
                _TOKEN_RE.findall(subject + " " + obj))
            touches_seed = subject in near_seeds or obj in near_seeds
            shares_concept = bool(
                triple_tokens & set(query_concepts))
            if touches_seed:
                reason = "touches a seed node"
            elif shares_concept:
                reason = "shares a concept token with the query"
            else:
                reason = ("touches no seed node and shares no "
                          "concept token with the query")
            decisions.append(TripleDecision(
                subject=subject, relation=relation, object=obj,
                keep=touches_seed or shares_concept,
                reason=reason, source="deterministic"))
        return decisions


def build_judge_prompt(query: str, triples) -> str:
    """One prompt judging a batch of triples against a query.

    Triples are numbered so the reply can point at each verdict by
    index. The reply must be JSON only, one decision per triple.
    """
    lines = [
        "You judge which knowledge-graph triples help answer the "
        "question below. A triple helps when it is about the "
        "question's topic or a fact the answer would need; a "
        "triple that only shares a word with the question but says "
        "nothing about it does NOT help. When the question asks "
        "whether a deal, seller, or product is trustworthy, safe, "
        "or reliable, a triple about a report, complaint, or "
        "fraud involving that seller, deal, or product DOES help, "
        "even when it names no product or price.",
        "",
        f"Question: {query}",
        "",
        "Triples:",
    ]
    for index, (subject, relation, obj) in enumerate(triples):
        lines.append(
            f"{index}. ({subject}) -[{relation}]-> ({obj})")
    lines.append("")
    lines.append(
        "Reply with JSON only, one decision per triple, in this "
        "shape: {\"decisions\": [{\"index\": 0, \"keep\": true, "
        "\"reason\": \"...\"}, ...]}. \"keep\" is true only when the "
        "triple helps answer the question. \"reason\" is at most "
        "five words. No extra text.")
    return "\n".join(lines)


def parse_judge_reply(reply: str, triple_count: int) -> dict:
    """Parse an LLM judge reply into index -> (keep, reason).

    Strict: the reply must be a JSON object with a ``decisions``
    list covering every triple index exactly once, each decision
    carrying a boolean ``keep`` and a non-empty string ``reason``.
    Anything else raises ValueError so the caller can fall back
    instead of trusting a half-read verdict.
    """
    if not isinstance(reply, str) or reply == "":
        raise ValueError("judge reply is empty")
    try:
        parsed = json.loads(reply)
    except ValueError as exc:
        raise ValueError("judge reply is not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise ValueError("judge reply must be a JSON object")
    raw = parsed.get("decisions")
    if not isinstance(raw, list):
        raise ValueError("judge reply must carry a decisions list")
    decisions = {}
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("each judge decision must be an object")
        index = item.get("index")
        keep = item.get("keep")
        reason = item.get("reason")
        if (not isinstance(index, int) or isinstance(index, bool)
                or not 0 <= index < triple_count):
            raise ValueError(
                f"judge decision has an invalid index: {index!r}")
        if index in decisions:
            raise ValueError(
                f"judge decided triple {index} twice")
        if not isinstance(keep, bool):
            raise ValueError(
                f"judge decision {index} has a non-boolean keep")
        if not isinstance(reason, str) or reason == "":
            raise ValueError(
                f"judge decision {index} has no reason")
        decisions[index] = (keep, reason)
    if len(decisions) != triple_count:
        raise ValueError(
            f"judge decided {len(decisions)} of {triple_count} triples")
    return decisions


class LLMTripleJudge(TripleFilter):
    """Triple filter driven by an LLM judge, batched per call.

    ``complete(prompt)`` is any function returning a string: the
    reply to one judging prompt. This is the seam for a real LLM:
    pass a completion function from the provider layer when a key
    exists (the router's cheapest reader model fits: judging is
    short-form classification, not generation). This class itself
    makes no network call and holds no key.

    Triples are sent in batches of ``batch_size`` (one LLM call
    per batch). A batch whose reply is unusable, or whose call
    raises, falls back to the deterministic filter for exactly
    those triples, so one bad reply never drops the whole graph;
    the fallback decisions record ``source="deterministic"``.
    """

    def __init__(self, complete, *, batch_size: int = DEFAULT_BATCH_SIZE,
                 fallback: TripleFilter | None = None):
        if not callable(complete):
            raise ValueError("complete must be a callable")
        if batch_size < 1:
            raise ValueError("batch_size must be at least 1")
        self.complete = complete
        self.batch_size = batch_size
        self.fallback = fallback or DeterministicTripleFilter()
        self.calls = 0
        self.fallback_batches = 0

    def filter(self, query, triples, *, seeds=(),
               query_concepts=()) -> list:
        if not isinstance(query, str) or query == "":
            raise ValueError("query must be a non-empty string")
        triples = list(triples)
        if not triples:
            return []
        decisions: list[TripleDecision | None] = [None] * len(triples)
        for start in range(0, len(triples), self.batch_size):
            batch = triples[start:start + self.batch_size]
            judged = self._judge_batch(
                query, batch, seeds=seeds,
                query_concepts=query_concepts)
            decisions[start:start + len(batch)] = judged
        return decisions

    def _judge_batch(self, query, batch, *, seeds=(),
                     query_concepts=()) -> list:
        try:
            self.calls += 1
            reply = self.complete(build_judge_prompt(query, batch))
            parsed = parse_judge_reply(reply, len(batch))
        except (ValueError, TypeError, RuntimeError) as exc:
            self.fallback_batches += 1
            fallback = self.fallback.filter(
                query, batch, seeds=seeds,
                query_concepts=query_concepts)
            for decision in fallback:
                decision.reason = (
                    f"LLM judge failed ({exc}); deterministic "
                    f"fallback: {decision.reason}")
            return fallback
        return [
            TripleDecision(
                subject=subject, relation=relation, object=obj,
                keep=keep, reason=reason, source="llm")
            for (subject, relation, obj), (keep, reason) in zip(
                batch,
                (parsed[index] for index in range(len(batch))))
        ]
