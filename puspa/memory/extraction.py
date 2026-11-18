"""Memory extraction and ranked recall (mem0 pattern, rewritten).

When an observation arrives, the extractor decides what is worth
keeping as a memory, merges duplicates into one memory, and flags
conflicts instead of silently overwriting. When recalling, memories
are ranked and the most relevant come back first.

Pattern adapted from mem0 (Apache-2.0): extraction decides what
becomes a memory, dedup merges the same fact, conflict is detected
at extraction time, and recall is ranked. Everything here is
rewritten as Puspa code: the extraction rules are deterministic
heuristics (no LLM call), dedup and conflict use the same
entity/concept/vector signals as `episodic.dual_level_search`,
and ranking reuses that dual-level score plus small support and
recency bonuses. No mem0 code is copied.

Memory dict shape (all values English):

- ``id``: short uuid hex.
- ``content``: canonical text of the memory.
- ``facts``: extracted fact clauses.
- ``entities`` / ``concepts``: from `episodic`.
- ``support``: how many observations merged into this memory.
- ``observations``: the raw observations merged in.
- ``status``: "active" or "conflicted".
- ``conflicts_with``: ids of memories this one conflicts with.
- ``created_at`` / ``updated_at``: datetimes (UTC).

Honest stand-in note: the extraction rules here are deterministic
heuristics, not an LLM extractor. An LLM extractor (the real mem0
mechanism) would catch paraphrases and implicit facts these rules
miss; that is a follow-up once the brain has a cheap local LLM
call on the observation path. What is real today: dedup, conflict
flagging, and ranked recall are all measured by tests with
recall@k on a small query set.
"""

from __future__ import annotations

import math
import re
import uuid
from datetime import UTC, datetime

from .embeddings import HashEmbedding, cosine_similarity
from .episodic import dual_level_search, extract_concepts, extract_entities

#: Observations that carry no fact: greetings, thanks, bare
#: acknowledgements. Matched on the whole normalized text or as a
#: leading phrase, so "terima kasih banyak" is still noise.
_FILLER_TEXTS = frozenset(
    [
        "ok",
        "oke",
        "okay",
        "ya",
        "iya",
        "betul",
        "sip",
        "thanks",
        "thank you",
        "terima kasih",
        "makasih",
        "maaf",
        "sorry",
        "hai",
        "halo",
        "hello",
        "hi",
        "bye",
        "dadah",
        "selamat pagi",
        "selamat siang",
        "selamat sore",
        "selamat malam",
        "good morning",
        "good evening",
        "good night",
    ]
)

_GREETING_PREFIXES = (
    "selamat ",
    "halo ",
    "hai ",
    "hello ",
    "hi ",
    "good morning",
    "good evening",
)

#: Leading phrases that mark the whole observation as filler even
#: when extra words follow ("terima kasih banyak").
_FILLER_PREFIXES = (
    "terima kasih",
    "makasih",
    "thank you",
    "thanks",
)

#: A question states no fact, so it is not stored as a memory.
_INTERROGATIVES = frozenset(
    [
        "apa",
        "siapa",
        "kapan",
        "mana",
        "berapa",
        "bagaimana",
        "mengapa",
        "which",
        "what",
        "when",
        "where",
        "who",
        "why",
        "how",
    ]
)

#: Polarity markers for conflict detection. Negative markers are
#: checked first because "tidak suka" contains "suka".
_NEGATIVE_MARKERS = (
    "tidak suka",
    "tidak mau",
    "tidak lagi",
    "tidak pernah",
    "tidak senang",
    "benci",
    "dislike",
    "hate",
    "bukan",
)
_POSITIVE_MARKERS = (
    "suka",
    "senang",
    "gemar",
    "cinta",
    "like",
    "love",
    "prefer",
    "favorit",
    "favorite",
)

_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")
_SENTENCE_SPLIT_RE = re.compile(r"[.!?;]+")

#: Dedup thresholds on the smaller concept set and vector similarity.
DEDUP_CONCEPT_OVERLAP = 0.8
DEDUP_VECTOR_SIMILARITY = 0.6
DEDUP_VECTOR_ONLY = 0.9

#: Rank bonuses: small enough that relevance always dominates.
SUPPORT_BONUS = 0.02
RECENCY_BONUS = 0.02
RECENCY_WINDOW_DAYS = 30.0


def _normalized(text):
    return " ".join(text.strip().lower().split()).strip(" .!?,;:")


def _is_filler_or_greeting(normalized):
    if normalized in _FILLER_TEXTS:
        return True
    return any(normalized.startswith(prefix) for prefix in _GREETING_PREFIXES + _FILLER_PREFIXES)


def _is_question(text):
    stripped = text.strip()
    if stripped.endswith("?"):
        return True
    first = _normalized(stripped).split(" ", 1)[0] if stripped else ""
    return first in _INTERROGATIVES


def is_memorable(text):
    """True when an observation is worth storing as a memory.

    Noise is rejected: empty text, filler/greetings, pure
    questions, and anything with fewer than two content concepts.
    A statement of a fact, preference, or number passes.
    """
    if not isinstance(text, str):
        raise ValueError("text must be a string")
    if not text.strip():
        return False
    if _is_filler_or_greeting(_normalized(text)):
        return False
    if _is_question(text):
        return False
    return len(extract_concepts(text)) >= 2


def extract_facts(text):
    """Split an observation into fact clauses.

    Sentences are split on `.`, `!`, `?`, `;`; a clause only
    counts as a fact when it carries at least two content
    concepts. Returns the list of fact strings, in order.
    """
    if not isinstance(text, str):
        raise ValueError("text must be a string")
    facts = []
    for chunk in _SENTENCE_SPLIT_RE.split(text):
        cleaned = " ".join(chunk.split())
        if cleaned and len(extract_concepts(cleaned)) >= 2:
            facts.append(cleaned)
    return facts


def detect_polarity(text):
    """Preference polarity of a text: +1, -1, or 0 (none)."""
    if not isinstance(text, str):
        raise ValueError("text must be a string")
    lowered = text.lower()
    if any(marker in lowered for marker in _NEGATIVE_MARKERS):
        return -1
    if any(marker in lowered for marker in _POSITIVE_MARKERS):
        return 1
    return 0


def _numbers(text):
    return {match.replace(",", ".") for match in _NUMBER_RE.findall(text)}


def _concept_overlap(a_concepts, b_concepts):
    """Concept overlap relative to the smaller concept set."""
    a_set, b_set = set(a_concepts), set(b_concepts)
    if not a_set or not b_set:
        return 0.0
    return len(a_set & b_set) / min(len(a_set), len(b_set))


def _entity_overlap(a_entities, b_entities):
    a_set, b_set = set(a_entities), set(b_entities)
    if not a_set or not b_set:
        return 0.0
    return len(a_set & b_set) / min(len(a_set), len(b_set))


def _vector_similarity(a_content, b_content, embedder):
    if embedder is None:
        return 0.0
    return cosine_similarity(embedder.embed(a_content), embedder.embed(b_content))


def memories_conflict(a, b):
    """True when two memories disagree about the same subject.

    A conflict needs a shared subject (a shared entity, or at
    least two shared concepts) plus a disagreement: opposite
    preference polarity, or a different number for the same
    subject (a changed price, budget, or count). Identical
    memories do not conflict; unrelated ones do not either.
    """
    a_concepts, b_concepts = set(a["concepts"]), set(b["concepts"])
    shared_concepts = a_concepts & b_concepts
    subject_shared = bool(set(a["entities"]) & set(b["entities"])) or len(shared_concepts) >= 2
    if not subject_shared:
        return False
    polarity_a = detect_polarity(a["content"])
    polarity_b = detect_polarity(b["content"])
    if polarity_a != 0 and polarity_b not in (0, polarity_a):
        return True
    numbers_a, numbers_b = _numbers(a["content"]), _numbers(b["content"])
    return bool(numbers_a and numbers_b and numbers_a != numbers_b)


def is_duplicate(a, b, embedder=None):
    """True when two memories state the same fact.

    Same fact means high concept overlap plus decent vector
    similarity, or near-identical vectors on their own. A pair
    that conflicts is never a duplicate; check conflict first.
    """
    if memories_conflict(a, b):
        return False
    overlap = _concept_overlap(a["concepts"], b["concepts"])
    vector_sim = _vector_similarity(a["content"], b["content"], embedder)
    if vector_sim >= DEDUP_VECTOR_ONLY:
        return True
    if overlap >= DEDUP_CONCEPT_OVERLAP and vector_sim >= DEDUP_VECTOR_SIMILARITY:
        return True
    entity_overlap = _entity_overlap(a["entities"], b["entities"])
    return entity_overlap >= 0.5 and overlap >= DEDUP_CONCEPT_OVERLAP


def _new_memory(content):
    now = datetime.now(UTC)
    return {
        "id": uuid.uuid4().hex[:12],
        "content": content,
        "facts": extract_facts(content),
        "entities": extract_entities(content),
        "concepts": extract_concepts(content),
        "support": 1,
        "observations": [content],
        "status": "active",
        "conflicts_with": [],
        "created_at": now,
        "updated_at": now,
    }


def _merge_into(existing, candidate):
    """Merge a duplicate observation into an existing memory.

    Support grows by one, the raw observation is kept, and the
    canonical text becomes the longer of the two so detail is
    never lost by merging. Facts, entities, and concepts follow
    the canonical text.
    """
    existing["support"] += 1
    existing["observations"].append(candidate["content"])
    if len(candidate["content"]) > len(existing["content"]):
        existing["content"] = candidate["content"]
        existing["facts"] = candidate["facts"]
        existing["entities"] = candidate["entities"]
        existing["concepts"] = candidate["concepts"]
    existing["updated_at"] = datetime.now(UTC)
    return existing


class MemoryStore:
    """In-memory extracted-memory store.

    The extraction + dedup + conflict rules live here so they
    are testable without a database. Persistence to the episodic
    table is a follow-up: the store hands back plain memory
    dicts, and a DB writer can persist them later.
    """

    def __init__(self, embedder=None):
        self._embedder = embedder or HashEmbedding()
        self._memories = []

    def __len__(self):
        return len(self._memories)

    def all(self):
        """All memories, in insertion order."""
        return list(self._memories)

    def get(self, memory_id):
        """One memory by id; KeyError when missing."""
        for memory in self._memories:
            if memory["id"] == memory_id:
                return memory
        raise KeyError(f"memory not found: {memory_id}")

    def add(self, observation):
        """Extract one observation into the store.

        Returns an outcome dict with ``action``:

        - "ignored": noise, nothing stored (`reason` says why).
        - "stored": a new memory.
        - "merged": a duplicate folded into an existing memory
          (`merged_into` is the kept id, `support` its new count).
        - "conflict": a disagreeing memory stored separately
          (`conflicts_with` lists the ids it disagrees with);
          the old memory is never overwritten.
        """
        if not isinstance(observation, str) or not observation.strip():
            raise ValueError("observation must be a non-empty string")
        observation = " ".join(observation.split())
        if not is_memorable(observation):
            return {"action": "ignored", "reason": "noise", "memory": None}
        candidate = _new_memory(observation)
        # Dedup first: a repeat of an already-stored statement
        # (including a repeat of a conflicted one) folds into
        # that memory. `is_duplicate` never matches a conflicting
        # pair, so a genuinely new disagreement still reaches
        # the conflict scan below.
        for memory in self._memories:
            if is_duplicate(candidate, memory, embedder=self._embedder):
                merged = _merge_into(memory, candidate)
                return {
                    "action": "merged",
                    "memory": merged,
                    "merged_into": merged["id"],
                }
        conflicts = [memory for memory in self._memories if memories_conflict(candidate, memory)]
        if conflicts:
            candidate["status"] = "conflicted"
            candidate["conflicts_with"] = [m["id"] for m in conflicts]
            for memory in conflicts:
                if candidate["id"] not in memory["conflicts_with"]:
                    memory["conflicts_with"].append(candidate["id"])
                memory["status"] = "conflicted"
                memory["updated_at"] = datetime.now(UTC)
            self._memories.append(candidate)
            return {
                "action": "conflict",
                "memory": candidate,
                "conflicts_with": candidate["conflicts_with"],
            }
        self._memories.append(candidate)
        return {"action": "stored", "memory": candidate}

    def recall(self, query, limit=5, embedder=None):
        """Ranked recall over the stored memories."""
        return rank_memories(
            self._memories,
            query,
            embedder=embedder or self._embedder,
            limit=limit,
        )

    def measure_recall(self, queries, ks=(1, 3, 5), embedder=None):
        """Measured recall@k over a list of (query, expected id).

        Returns ``{k: recall}`` with recall as a fraction of
        queries whose expected memory appears in the top k.
        """
        return measure_recall_at_ks(
            self._memories,
            queries,
            ks=ks,
            embedder=embedder or self._embedder,
        )


def rank_memories(memories, query, embedder=None, limit=5):
    """Rank memories for a query, most relevant first (pure).

    Base score is the episodic dual-level score (entity +
    concept + vector). Memories with a zero base score share no
    signal with the query and are dropped, so they cannot fill
    the top k by tie-break. Two small bonuses break ties toward
    memories seen more often (support) and memories seen more
    recently (recency); each bonus is at most 0.02, so relevance
    always dominates. Every result carries ``score``, ``level``,
    ``evidence_id`` (the memory id), ``support``, and ``status``.
    """
    if not isinstance(query, str) or query == "":
        raise ValueError("query must be a non-empty string")
    if limit < 1:
        raise ValueError("limit must be at least 1")
    memories = list(memories or [])
    if not memories:
        return []
    embedder = embedder or HashEmbedding()
    ranked = dual_level_search(memories, query, embedder=embedder, limit=len(memories))
    now = datetime.now(UTC)
    scored = []
    for result in ranked:
        # A zero base score means no shared entity, concept, or
        # vector signal at all: the memory is irrelevant to the
        # query, so it is dropped instead of filling the top k
        # by id tie-break (which would inflate recall@k).
        if result["score"] <= 0:
            continue
        support_bonus = SUPPORT_BONUS * math.log1p(max(0, result.get("support", 1) - 1))
        created_at = result.get("created_at")
        recency_bonus = 0.0
        if isinstance(created_at, datetime):
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=UTC)
            age_days = max(0.0, (now - created_at).total_seconds() / 86400.0)
            recency_bonus = RECENCY_BONUS * max(0.0, 1.0 - age_days / RECENCY_WINDOW_DAYS)
        entry = dict(result)
        entry["score"] = result["score"] + support_bonus + recency_bonus
        scored.append(entry)
    scored.sort(key=lambda r: (-r["score"], str(r.get("id"))))
    return scored[:limit]


def recall_at_k(memories, queries, k, embedder=None):
    """Measured recall@k: fraction of queries whose expected memory
    is in the top k ranked results. `queries` is a list of
    (query text, expected memory id).
    """
    if k < 1:
        raise ValueError("k must be at least 1")
    if not queries:
        raise ValueError("queries must be non-empty")
    embedder = embedder or HashEmbedding()
    hits = 0
    for query, expected_id in queries:
        ranked = rank_memories(memories, query, embedder=embedder, limit=k)
        if any(str(r.get("id")) == str(expected_id) for r in ranked):
            hits += 1
    return hits / len(queries)


def measure_recall_at_ks(memories, queries, ks=(1, 3, 5), embedder=None):
    """Measured recall for each k in `ks`: returns ``{k: recall}``."""
    embedder = embedder or HashEmbedding()
    return {k: recall_at_k(memories, queries, k, embedder=embedder) for k in ks}
