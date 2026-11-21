"""Recall-first recall_plan() (design doc docs/kosmos-brain-v2.md).

Every turn starts with recall, before intent inference reads the
message. :func:`recall_plan` loads, in this fixed order:

1. Pinned items: corrections, commitments, and standing goals, in
   that order (see :func:`puspa.memory.durable.pinned_items`).
   These are never dropped from context.
2. Semantic top-k: the best matching active facts for the message,
   from the existing hybrid recall
   (:func:`puspa.memory.recall.hybrid_recall`).
3. Ledger-linked episodes: recent episodes whose ledger event is
   real, ranked for the message by dual-level retrieval
   (:func:`puspa.memory.episodic.dual_level_search`). An episode
   counts as linked only when the ledger holds its event (matched
   by event id within its task, or by task when the episode
   carries no event id). An episode with no ledger event is not
   shown as linked, it is left out.
4. Person models: the K5 other-agent models
   (:class:`puspa.world.agents.AgentGoalModel`) for everyone named
   in the message, plus anyone named by a ``person:`` fact. A
   model is included only when its contact matches a name from
   the message or the facts.

A cold session is the point: a fresh :func:`recall_plan` call
with no in-memory state reads everything from the stores, so
every pinned item is back before intent inference runs. Each
layer is loaded on its own: when a layer is empty (no facts, no
linked episodes, no named person) the plan still builds with
that layer empty, and a layer that fails to load is recorded in
``layer_errors`` instead of killing the turn.

Wiring: :func:`recall_first_infer_intent` runs the plan and then
infers intent from the plan, so the order (recall, then intent)
is observable through the optional ``trace`` list. The turn
advisor (:class:`puspa.intent.turn_wiring.IntentTurnAdvisor`)
takes the same plan through its ``plan_provider`` argument and
runs it inside :meth:`_context`, before inference.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from puspa.intent.distribution import IntentDistribution
from puspa.intent.inference import IntentContext, IntentInferrer, infer_intent

from . import durable, episodic
from . import facts as facts_module
from .recall import hybrid_recall

__all__ = [
    "DEFAULT_EPISODE_LIMIT",
    "DEFAULT_SEMANTIC_TOP_K",
    "EPISODE_CANDIDATE_LIMIT",
    "LAYER_ORDER",
    "RecallPlan",
    "recall_first_infer_intent",
    "recall_plan",
]

#: Layers in load order. The order is part of the contract: pinned
#: items first, person models last.
LAYER_ORDER = ("pinned", "semantic", "episodes", "persons")

#: Semantic facts returned by default.
DEFAULT_SEMANTIC_TOP_K = 5

#: Ledger-linked episodes returned by default.
DEFAULT_EPISODE_LIMIT = 5

#: Recent episodes read as linkage candidates.
EPISODE_CANDIDATE_LIMIT = 20

_CAPITALIZED_RE = re.compile(r"\b[A-Z][a-z]{1,}\b")


@dataclass
class RecallPlan:
    """What one turn recalled, before intent inference ran.

    ``pinned`` holds ``{"section", "key", "text"}`` dicts from
    :func:`puspa.memory.durable.pinned_items`, corrections first.
    ``semantic`` holds :func:`puspa.memory.recall.hybrid_recall`
    results (each with ``"fact"``, ``"score"``, ``"cosine"``).
    ``episodes`` holds episode dicts, each with ``"score"``,
    ``"level"``, and ``"evidence_id"`` (the episode id, the proof
    for the recall). ``persons`` holds ``{"contact_id",
    "active_goals", "model"}`` dicts, one per named contact with
    a stored model. ``load_order`` records the layers in the
    order they were actually loaded. ``layer_errors`` records a
    load failure per layer, so a failed layer is visible instead
    of silently empty.
    """

    user_id: str
    query: str
    pinned: list[dict[str, Any]] = field(default_factory=list)
    semantic: list[dict[str, Any]] = field(default_factory=list)
    episodes: list[dict[str, Any]] = field(default_factory=list)
    persons: list[dict[str, Any]] = field(default_factory=list)
    load_order: tuple[str, ...] = ()
    layer_errors: dict[str, str] = field(default_factory=dict)

    @property
    def counts(self) -> dict[str, int]:
        """How many items each layer contributed."""
        return {
            "pinned": len(self.pinned),
            "semantic": len(self.semantic),
            "episodes": len(self.episodes),
            "persons": len(self.persons),
        }

    @property
    def pinned_texts(self) -> list[str]:
        """Plain texts of the pinned items, in load order."""
        return [str(item.get("text") or "") for item in self.pinned]

    def intent_context(self, *, world: Any = None) -> IntentContext:
        """The intent context this plan supports.

        The profile is built from the pinned items first (key to
        text), then filled in from the semantic facts, so a pinned
        item always wins when a key appears in both layers. The
        world model is passed through untouched: person models do
        not become world beliefs, they stay in ``persons`` for
        the planner to read.
        """
        profile: dict[str, str] = {}
        for item in self.pinned:
            key = str(item.get("key") or "")
            text = str(item.get("text") or "")
            if key and text:
                profile[key] = text
        for hit in self.semantic:
            fact = hit.get("fact") or {}
            key = str(fact.get("key") or "")
            text = str(fact.get("content") or "")
            if key and text and key not in profile:
                profile[key] = text
        return IntentContext(profile=profile, world=world)


def _snapshot_from_facts(facts: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """A durable snapshot (pinned sections only) from fact rows."""
    snapshot: dict[str, list[dict[str, Any]]] = {key: [] for key in durable.SNAPSHOT_KEYS}
    for fact in facts or []:
        if fact.get("status") != "active":
            continue
        text = str(fact.get("content") or fact.get("value") or "")
        if not text:
            continue
        section = durable.classify_fact(fact)
        snapshot[section].append(
            {
                "key": str(fact.get("key") or ""),
                "text": text,
                "source": fact.get("source"),
            }
        )
    return snapshot


def _load_pinned(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pinned items from active facts, via durable.pinned_items."""
    return durable.pinned_items(_snapshot_from_facts(facts))


def _load_semantic(
    query: str,
    facts: list[dict[str, Any]],
    *,
    embedder: Any = None,
    top_k: int = DEFAULT_SEMANTIC_TOP_K,
) -> list[dict[str, Any]]:
    """Semantic top-k facts for ``query`` (empty query: empty)."""
    if not query.strip() or top_k < 1:
        return []
    return hybrid_recall(query, facts, embedder=embedder, limit=top_k)


def _ledger_event_ids(ledger: Any, task_id: str) -> set[str]:
    """Ledger event ids for one task (empty set when none)."""
    return {str(event.get("id")) for event in ledger.events(task_id)}


def _episode_is_linked(episode: dict[str, Any], ledger: Any, task_ids: dict[str, set[str]]) -> bool:
    """True when the ledger holds this episode's event.

    Linked means the episode's event id is in its task's ledger
    events. An episode with no event id is linked when its task
    has any ledger event at all. An episode with no task id, or
    a task the ledger never saw, is not linked.
    """
    task_id = episode.get("task_id")
    if not task_id:
        return False
    known = task_ids.get(str(task_id))
    if known is None:
        return False
    event_id = episode.get("event_id")
    if event_id is None:
        return bool(known)
    return str(event_id) in known


def _load_episodes(
    user_id: str,
    query: str,
    *,
    dsn: Any = None,
    ledger: Any = None,
    episodes: list[dict[str, Any]] | None = None,
    embedder: Any = None,
    limit: int = DEFAULT_EPISODE_LIMIT,
) -> list[dict[str, Any]]:
    """Ledger-linked episodes, ranked for ``query``.

    Without a ledger the link cannot be verified, so the layer
    is honestly empty. With no query the most recent linked
    episodes are returned; with a query they are ranked by
    dual-level retrieval.
    """
    if ledger is None or limit < 1:
        return []
    candidates = (
        list(episodes)
        if episodes is not None
        else episodic.list_episodes(user_id, limit=EPISODE_CANDIDATE_LIMIT, dsn=dsn)
    )
    if not candidates:
        return []
    task_ids: dict[str, set[str]] = {}
    for episode in candidates:
        task_id = episode.get("task_id")
        if task_id and str(task_id) not in task_ids:
            task_ids[str(task_id)] = _ledger_event_ids(ledger, str(task_id))
    linked = [ep for ep in candidates if _episode_is_linked(ep, ledger, task_ids)]
    if not linked:
        return []
    if not query.strip():
        ordered = sorted(linked, key=lambda ep: str(ep.get("created_at") or ""), reverse=True)
        return ordered[:limit]
    return episodic.dual_level_search(linked, query, embedder=embedder, limit=limit)


def _names_in_query(query: str) -> set[str]:
    """Capitalized words in ``query``, lowercased.

    A capitalized word is only a candidate name: the persons
    layer includes a model only when the word matches a stored
    contact, so an ordinary sentence starter never leaks a
    person into the plan on its own.
    """
    words = _CAPITALIZED_RE.findall(query)
    if not words:
        return set()
    return {word.lower() for word in words}


def _load_persons(
    user_id: str,
    query: str,
    facts: list[dict[str, Any]],
    *,
    agent_id: str | None = None,
    dsn: Any = None,
    agent_models: Any = None,
) -> list[dict[str, Any]]:
    """Person models for everyone named in the query or the facts.

    Names come from capitalized words in the query and from
    ``person:`` facts. The store is the injected
    :class:`puspa.world.agents.AgentGoalStore`, or the stored
    models for (user, agent) when an agent id is given. Without
    a store there is nothing to load, so the layer is empty. A
    model is included only when its contact matches a name (by
    exact match or either way as a substring, case-insensitive),
    so an unnamed contact never leaks into the plan.
    """
    store = agent_models
    if store is None:
        if agent_id is None:
            return []
        from puspa.world import store as world_store

        store = world_store.load_agent_models(user_id, agent_id, dsn=dsn)
    if store is None:
        return []
    names = _names_in_query(query)
    for fact in facts or []:
        key = str(fact.get("key") or "").lower()
        if key.startswith("person:"):
            names.add(key[len("person:") :].strip())
    if not names:
        return []
    persons: list[dict[str, Any]] = []
    for model in store.all_models():
        contact = str(model.contact_id)
        lowered = contact.lower()
        if not any(name == lowered or name in lowered or lowered in name for name in names if name):
            continue
        persons.append(
            {
                "contact_id": contact,
                "active_goals": [goal.label for goal in model.active_goals],
                "model": model,
            }
        )
    return persons


def recall_plan(
    user_id: str,
    query: str,
    *,
    agent_id: str | None = None,
    dsn: Any = None,
    fact_store: Any = None,
    facts: list[dict[str, Any]] | None = None,
    ledger: Any = None,
    agent_models: Any = None,
    episodes: list[dict[str, Any]] | None = None,
    embedder: Any = None,
    top_k: int = DEFAULT_SEMANTIC_TOP_K,
    episode_limit: int = DEFAULT_EPISODE_LIMIT,
    trace: list[str] | None = None,
) -> RecallPlan:
    """Build the recall plan for one turn, before intent inference.

    Loads pinned items, semantic top-k, ledger-linked episodes,
    and person models, in that order, each from its store (a cold
    session reads everything fresh: no in-memory state is kept
    between calls). ``facts``, ``episodes``, ``ledger``, and
    ``agent_models`` may be injected for tests; otherwise the
    Postgres stores are read with ``dsn``. Every loaded layer
    name is appended to ``trace`` as it loads, so the load order
    is observable. A layer that fails records its error in
    ``layer_errors`` and loads as empty instead of failing the
    whole plan.
    """
    if not isinstance(user_id, str) or not user_id:
        raise ValueError("user_id must be a non-empty string")
    if not isinstance(query, str):
        raise ValueError("query must be a string")
    plan = RecallPlan(user_id=str(user_id), query=query)
    loaded_facts: list[dict[str, Any]] = []
    if facts is not None:
        loaded_facts = list(facts)
    elif fact_store is not None:
        loaded_facts = fact_store.list_user(user_id, statuses=("active",))
    else:
        loaded_facts = facts_module.PostgresFactStore(dsn=dsn).list_user(
            user_id, statuses=("active",)
        )
    layers: dict[str, Any] = {}
    loaders = (
        ("pinned", lambda: _load_pinned(loaded_facts)),
        ("semantic", lambda: _load_semantic(query, loaded_facts, embedder=embedder, top_k=top_k)),
        (
            "episodes",
            lambda: _load_episodes(
                user_id,
                query,
                dsn=dsn,
                ledger=ledger,
                episodes=episodes,
                embedder=embedder,
                limit=episode_limit,
            ),
        ),
        (
            "persons",
            lambda: _load_persons(
                user_id, query, loaded_facts, agent_id=agent_id, dsn=dsn, agent_models=agent_models
            ),
        ),
    )
    for name, loader in loaders:
        try:
            layers[name] = loader()
        except Exception as exc:
            layers[name] = []
            plan.layer_errors[name] = str(exc)
        plan.load_order += (name,)
        if trace is not None:
            trace.append(name)
    plan.pinned = layers["pinned"]
    plan.semantic = layers["semantic"]
    plan.episodes = layers["episodes"]
    plan.persons = layers["persons"]
    return plan


def recall_first_infer_intent(
    message: str,
    user_id: str,
    *,
    inferrer: IntentInferrer | None = None,
    trace: list[str] | None = None,
    world: Any = None,
    **plan_kwargs: Any,
) -> tuple[IntentDistribution, RecallPlan]:
    """Recall first, then infer intent from what was recalled.

    Runs :func:`recall_plan` (appending each layer to ``trace``),
    builds the intent context from the plan, appends ``"intent"``
    to ``trace``, and infers the intent distribution. The trace
    makes the order observable: every recall layer name comes
    before ``"intent"``. Returns ``(distribution, plan)``.
    """
    plan = recall_plan(user_id, message, trace=trace, **plan_kwargs)
    if trace is not None:
        trace.append("intent")
    distribution = infer_intent(message, plan.intent_context(world=world), inferrer=inferrer)
    return distribution, plan
