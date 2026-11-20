"""BM25 ranking (Robertson et al.) for the retrieval comparison.

A small, dependency-free BM25 used as the lexical baseline in the
retrieval benchmark: BM25 vs plain vectors vs HippoRAG. Standard
parameters (k1=1.5, b=0.75), lowercase word tokens, no stemming.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


@dataclass
class BM25Index:
    """BM25 over a fixed set of documents (passages)."""

    doc_ids: list[str]
    doc_tokens: list[list[str]]
    doc_freq: dict[str, int]
    doc_lengths: list[int]
    avg_length: float
    total_docs: int

    @classmethod
    def build(cls, documents: dict[str, str]) -> BM25Index:
        doc_ids = sorted(documents)
        doc_tokens = [tokenize(documents[doc_id]) for doc_id in doc_ids]
        doc_freq: dict[str, int] = {}
        for tokens in doc_tokens:
            for token in set(tokens):
                doc_freq[token] = doc_freq.get(token, 0) + 1
        lengths = [len(tokens) for tokens in doc_tokens]
        avg = sum(lengths) / len(lengths) if lengths else 0.0
        return cls(
            doc_ids=doc_ids,
            doc_tokens=doc_tokens,
            doc_freq=doc_freq,
            doc_lengths=lengths,
            avg_length=avg,
            total_docs=len(doc_ids),
        )

    def rank(self, query: str, *, top_k: int = 5, k1: float = 1.5, b: float = 0.75) -> list[dict]:
        """Rank documents for a query. Returns ``[{"passage_id", "score"}]``."""
        query_tokens = tokenize(query)
        scored: list[tuple[float, str]] = []
        for index, doc_id in enumerate(self.doc_ids):
            tokens = self.doc_tokens[index]
            length = self.doc_lengths[index]
            score = 0.0
            for token in set(query_tokens):
                if token not in self.doc_freq:
                    continue
                freq = tokens.count(token)
                if freq == 0:
                    continue
                idf = math.log(
                    1.0
                    + (self.total_docs - self.doc_freq[token] + 0.5) / (self.doc_freq[token] + 0.5)
                )
                denom = freq + k1 * (
                    1.0 - b + b * (length / self.avg_length if self.avg_length else 0.0)
                )
                score += idf * (freq * (k1 + 1.0)) / denom
            scored.append((score, doc_id))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [{"passage_id": doc_id, "score": score} for score, doc_id in scored[:top_k]]
