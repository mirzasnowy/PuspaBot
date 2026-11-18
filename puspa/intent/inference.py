"""Deterministic intent inference (Kosmos K1).

The default inferrer reads a user message against what Puspa
already knows about the user — the profile (goals and values the
user stated or confirmed) and the world model (bills, prices,
habits, schedules learned from observation) — by keyword and
topic overlap. No LLM call, no cost, fully testable, and the same
input always gives the same distribution.

Candidates come from three sources, in priority order:

1. Profile entries whose key names an intent: ``goal*`` reads at
   the goal level, ``value*`` / ``principle*`` at the values
   level, ``want*`` / ``need*`` / ``desire*`` / ``preference*`` /
   ``like*`` at the desire level. Any other profile key (a name,
   an address) is an identity fact, not an intent, and is ignored.
   Inferred profile facts never reach here in the first place:
   ``puspa.memory.profile`` keeps them as proposals until the user
   confirms them, so only stated or confirmed goals and values
   can become hypotheses.
2. World beliefs: unpaid bills and known prices read at the desire
   level ("pay bill pln", "check price of ..."), repeated habits
   and learned schedules at the goal level ("keep up ...").
   Capability beliefs are not intent (they say what the agent can
   do, not what the user wants) and contact beliefs belong to K5
   (other-agent models); neither becomes a hypothesis here.
3. The message itself, through a small hand-written signal
   lexicon (``_MESSAGE_SIGNALS``): "I'm so tired" reads as a
   desire to rest even when the profile says nothing. Signal
   candidates are a fallback only — they are added at the desire
   level only when no profile or world candidate scored there, so
   grounded evidence always wins over the lexicon.

Scoring is coverage: a candidate's score is the fraction of its
keywords that appear in the message. Message tokens are expanded
through ``_SIGNAL_EXPANSIONS`` first, so "tired" also matches a
"health" value or a "sleep well" goal — that expansion is how one
message is read at all three levels at once. A candidate with no
matching keyword scores 0 and is dropped; a level with no scoring
candidate stays empty, honestly.

LLM seam
--------
:class:`IntentInferrer` is the pluggable interface: any inferrer
takes a message and an :class:`IntentContext` and returns an
:class:`~puspa.intent.distribution.IntentDistribution`. The
deterministic inferrer is the default. A future LLM-backed
inferrer plugs in through the router's ``llm_complete`` seam —
the same ``complete(prompt)`` callable the retrieval modules take
(``puspa.router.complete.default_llm_complete``) — and
:func:`build_llm_prompt` renders the prompt it must answer, as
JSON in the schema documented there. The LLM inferrer itself is
NOT built here: it needs a live model, a key, and a JSON-parsing
contract that belongs to its own item, and building it now would
put a network call and a cost on every turn. Until it exists, the
deterministic inferrer is the whole inference path.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from .distribution import IntentDistribution, IntentLevel

if TYPE_CHECKING:  # pragma: no cover - typing only, no runtime import
    from puspa.world.model import WorldModel

__all__ = [
    "DeterministicInferrer",
    "IntentContext",
    "IntentInferrer",
    "build_llm_prompt",
    "infer_intent",
]

#: Words too common to carry intent (English + Indonesian).
_STOPWORDS = frozenset(
    {
        "i",
        "im",
        "m",
        "me",
        "my",
        "we",
        "you",
        "he",
        "she",
        "it",
        "its",
        "the",
        "a",
        "an",
        "and",
        "or",
        "but",
        "so",
        "to",
        "of",
        "in",
        "on",
        "for",
        "with",
        "at",
        "by",
        "from",
        "is",
        "am",
        "are",
        "was",
        "were",
        "be",
        "been",
        "do",
        "does",
        "did",
        "have",
        "has",
        "had",
        "can",
        "could",
        "will",
        "would",
        "should",
        "please",
        "this",
        "that",
        "these",
        "those",
        "there",
        "here",
        "what",
        "when",
        "where",
        "which",
        "who",
        "how",
        "why",
        "not",
        "no",
        "yes",
        "just",
        "very",
        "really",
        "too",
        "now",
        "then",
        "than",
        "saya",
        "aku",
        "gue",
        "kamu",
        "dia",
        "kita",
        "kami",
        "yang",
        "dan",
        "atau",
        "tapi",
        "di",
        "ke",
        "dari",
        "untuk",
        "dengan",
        "adalah",
        "ini",
        "itu",
        "sudah",
        "belum",
        "mau",
        "bisa",
        "tolong",
        "dong",
        "sih",
        "aja",
        "banget",
        "sangat",
        "lagi",
        "juga",
    }
)

#: Profile key prefixes that name an intent, and the level each reads at.
#: Anything else (a name, an address) is an identity fact, not intent.
_PROFILE_KEY_LEVELS = (
    ("goal", "goal"),
    ("value", "values"),
    ("principle", "values"),
    ("want", "desire"),
    ("need", "desire"),
    ("desire", "desire"),
    ("preference", "desire"),
    ("like", "desire"),
)

#: Message signals that read as an immediate desire on their own.
#: A signal word maps to the desire label it reads as. These are a
#: fallback only: they are used at the desire level only when no
#: profile or world candidate scored there.
_MESSAGE_SIGNALS = {
    "tired": "rest",
    "exhausted": "rest",
    "sleepy": "rest",
    "capek": "rest",
    "lelah": "rest",
    "ngantuk": "rest",
    "hungry": "eat",
    "lapar": "eat",
    "thirsty": "drink",
    "haus": "drink",
    "stressed": "rest and recover",
    "stres": "rest and recover",
    "sick": "recover",
    "sakit": "recover",
    "bored": "do something interesting",
    "bosan": "do something interesting",
    "busy": "get help with the load",
    "sibuk": "get help with the load",
    "broke": "save money",
    "bokek": "save money",
    "buy": "buy",
    "beli": "buy",
}

#: Concept expansion: a message word also counts as these related
#: words when matching candidates. This is what lets one message
#: be read at all three levels at once ("tired" matches a "sleep
#: well" goal and a "health" value). Small and hand-written on
#: purpose: every expansion here is visible in this file, and a
#: word with no entry expands to nothing.
_SIGNAL_EXPANSIONS = {
    "tired": {"rest", "sleep", "health", "energy", "istirahat", "tidur"},
    "exhausted": {"rest", "sleep", "health", "energy"},
    "sleepy": {"rest", "sleep", "health"},
    "capek": {"rest", "sleep", "health", "energy", "istirahat", "tidur"},
    "lelah": {"rest", "sleep", "health", "energy", "istirahat", "tidur"},
    "ngantuk": {"rest", "sleep", "tidur"},
    "hungry": {"eat", "food", "makan", "lapar"},
    "lapar": {"eat", "food", "makan"},
    "thirsty": {"drink", "water", "minum", "haus"},
    "haus": {"drink", "water", "minum"},
    "stressed": {"calm", "rest", "health", "stress"},
    "stres": {"calm", "rest", "health"},
    "sick": {"health", "rest", "doctor"},
    "sakit": {"health", "rest"},
    "bored": {"fun", "learn", "interesting"},
    "bosan": {"fun", "learn", "interesting"},
    "busy": {"time", "focus", "help"},
    "sibuk": {"time", "focus", "help"},
    "broke": {"money", "save", "budget"},
    "bokek": {"money", "save", "budget"},
    "save": {"money", "budget", "frugal", "hemat"},
    "hemat": {"save", "money", "frugal"},
    "bill": {"pay", "money", "tagihan", "bayar"},
    "tagihan": {"pay", "bill", "money", "bayar"},
    "bayar": {"pay", "bill", "money"},
    "price": {"buy", "compare", "cheap", "harga"},
    "harga": {"price", "buy", "compare"},
    "buy": {"price", "compare", "shop", "beli"},
    "beli": {"buy", "price", "shop"},
    "learn": {"study", "skill", "growth", "belajar"},
    "belajar": {"learn", "study", "skill"},
    "health": {"rest", "sleep", "sehat"},
    "sehat": {"health", "rest"},
}


@dataclass
class IntentContext:
    """What the inferrer may read: profile plus world state.

    ``profile`` is the plain ``key -> value`` dict
    ``puspa.memory.profile.get_profile`` returns (stated or
    confirmed facts only). ``world`` is a
    :class:`~puspa.world.model.WorldModel`, its ``state`` dict, or
    None when no world model is wired in. Passing None for either
    is honest: the inferrer simply has less evidence, and levels
    with no evidence stay empty.
    """

    profile: dict[str, str] = field(default_factory=dict)
    world: WorldModel | dict | None = None


class IntentInferrer(Protocol):
    """Pluggable intent inference: message + context -> distribution.

    The deterministic inferrer implements this; a future
    LLM-backed inferrer implements the same method (taking its
    ``llm_complete`` callable at construction, per the seam note
    in the module docstring) so the caller never cares which one
    is plugged in.
    """

    def infer(self, message: str, context: IntentContext) -> IntentDistribution:
        """Read ``message`` in ``context`` as an intent distribution."""
        ...


def _tokenize(text: str) -> set[str]:
    """Lowercase word tokens of ``text``, stopwords dropped."""
    tokens = set(re.findall(r"[a-z0-9]+", text.lower()))
    return {token for token in tokens if token not in _STOPWORDS}


def _expand(tokens: set[str]) -> set[str]:
    """Message tokens plus their concept expansions."""
    expanded = set(tokens)
    for token in tokens:
        expanded |= _SIGNAL_EXPANSIONS.get(token, set())
    return expanded


def _get(obj: object, key: str, default=None):
    """Attribute-or-key read, for belief dataclasses and plain dicts."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


@dataclass
class _Candidate:
    level: str
    label: str
    keywords: frozenset[str]
    source: str


def _profile_candidates(profile: dict[str, str]) -> list[_Candidate]:
    """One candidate per intent-bearing profile entry."""
    candidates: list[_Candidate] = []
    for key, value in profile.items():
        if not isinstance(value, str) or not value.strip():
            continue
        level = None
        lowered = key.lower()
        for prefix, candidate_level in _PROFILE_KEY_LEVELS:
            if lowered.startswith(prefix):
                level = candidate_level
                break
        if level is None:
            continue
        keywords = _tokenize(value)
        if not keywords:
            continue
        candidates.append(
            _Candidate(
                level=level, label=value.strip(), keywords=frozenset(keywords), source="profile"
            )
        )
    return candidates


def _world_state(world: WorldModel | dict | None) -> dict:
    """A world model (or its state dict) as a plain state dict."""
    if world is None:
        return {}
    if isinstance(world, dict):
        return world
    state = getattr(world, "state", None)
    if isinstance(state, dict):
        return state
    if callable(state):
        return state()
    return {}


def _world_candidates(world: WorldModel | dict | None) -> list[_Candidate]:
    """Candidates from world beliefs: bills, prices, habits, schedules."""
    candidates: list[_Candidate] = []
    state = _world_state(world)
    if not state:
        return candidates
    for bill_id, belief in (state.get("bill_beliefs") or {}).items():
        if _get(belief, "paid", False):
            continue
        keywords = {"pay", "bill", "tagihan", "bayar"} | _tokenize(str(bill_id))
        candidates.append(
            _Candidate(
                level="desire",
                label=f"pay bill {bill_id}",
                keywords=frozenset(keywords),
                source="world",
            )
        )
    for price_key, belief in (state.get("price_beliefs") or {}).items():
        if _get(belief, "last_price", None) is None:
            continue
        product_id = str(price_key).split("@")[0]
        store = str(price_key).split("@")[1] if "@" in str(price_key) else ""
        keywords = {"price", "check", "compare", "harga"} | _tokenize(product_id)
        keywords |= _tokenize(store)
        label = (
            f"check price of {product_id} at {store}" if store else (f"check price of {product_id}")
        )
        candidates.append(
            _Candidate(level="desire", label=label, keywords=frozenset(keywords), source="world")
        )
    for task_kind, belief in (state.get("habit_beliefs") or {}).items():
        if not _get(belief, "has_repeated_pattern", False):
            continue
        words = str(task_kind).replace("_", " ")
        keywords = {"habit", "routine", "usually"} | _tokenize(words)
        candidates.append(
            _Candidate(
                level="goal", label=f"keep up {words}", keywords=frozenset(keywords), source="world"
            )
        )
    for task_kind, belief in (state.get("schedule_beliefs") or {}).items():
        if _get(belief, "mean_duration", None) is None:
            continue
        words = str(task_kind).replace("_", " ")
        keywords = {"finish", "done", "complete"} | _tokenize(words)
        candidates.append(
            _Candidate(
                level="goal", label=f"finish {words}", keywords=frozenset(keywords), source="world"
            )
        )
    return candidates


def _signal_candidates(tokens: set[str]) -> list[_Candidate]:
    """Desire candidates from the message's own signal words.

    Fallback only: the caller adds these when no grounded
    candidate scored at the desire level. Each signal word reads
    as one desire; the same desire from several words merges into
    one candidate, scored at full strength.
    """
    by_label: dict[str, set[str]] = {}
    for token in tokens:
        label = _MESSAGE_SIGNALS.get(token)
        if label is None:
            continue
        keywords = by_label.setdefault(label, set())
        keywords.add(token)
        keywords |= _SIGNAL_EXPANSIONS.get(token, set())
    return [
        _Candidate(level="desire", label=label, keywords=frozenset(keywords), source="message")
        for label, keywords in sorted(by_label.items())
    ]


def _score(
    candidates: list[_Candidate], expanded: set[str]
) -> list[tuple[str, float, tuple[str, ...], str]]:
    """Score candidates by keyword coverage against the message.

    Score is the fraction of the candidate's keywords present in
    the expanded message tokens. Zero-score candidates are
    dropped. Evidence is the matched keywords, sorted.
    """
    scored: list[tuple[str, float, tuple[str, ...], str]] = []
    for candidate in candidates:
        matched = sorted(candidate.keywords & expanded)
        if not matched:
            continue
        score = len(matched) / len(candidate.keywords)
        scored.append((candidate.label, score, tuple(matched), candidate.source))
    return scored


class DeterministicInferrer:
    """Keyword-overlap inference: the default, no-LLM inferrer.

    Stateless and deterministic: the same message and context
    always produce the same distribution, and it never calls a
    model, so it costs nothing per turn.
    """

    def infer(self, message: str, context: IntentContext) -> IntentDistribution:
        """Read ``message`` in ``context`` at all three levels."""
        if not isinstance(message, str):
            raise ValueError("message must be a string")
        tokens = _tokenize(message)
        expanded = _expand(tokens)
        candidates = _profile_candidates(context.profile or {})
        candidates.extend(_world_candidates(context.world))
        by_level: dict[str, list[_Candidate]] = {
            "desire": [],
            "goal": [],
            "values": [],
        }
        for candidate in candidates:
            by_level[candidate.level].append(candidate)
        scored: dict[str, list[tuple[str, float, tuple[str, ...], str]]] = {}
        for level in ("desire", "goal", "values"):
            scored[level] = _score(by_level[level], expanded)
        if not scored["desire"]:
            # No grounded reading of the immediate desire: fall back
            # to the message's own signal words (tired -> rest).
            # The goal and values levels never use this fallback —
            # a signal word is about right now, not about a value.
            scored["desire"] = _score(_signal_candidates(tokens), expanded)
        return IntentDistribution(
            desire=IntentLevel.from_scores("desire", scored["desire"]),
            goal=IntentLevel.from_scores("goal", scored["goal"]),
            values=IntentLevel.from_scores("values", scored["values"]),
            message=message,
            source="deterministic",
        )


def infer_intent(
    message: str,
    context: IntentContext | None = None,
    *,
    inferrer: IntentInferrer | None = None,
) -> IntentDistribution:
    """Infer an intent distribution for ``message``.

    ``context`` defaults to an empty context (no profile, no
    world): only the message's own signal words can score, and
    every other level stays honestly empty. ``inferrer`` defaults
    to :class:`DeterministicInferrer`; pass any
    :class:`IntentInferrer` to plug a different one in.
    """
    chosen = inferrer if inferrer is not None else DeterministicInferrer()
    return chosen.infer(message, context if context is not None else IntentContext())


def build_llm_prompt(message: str, context: IntentContext) -> str:
    """Render the prompt a future LLM inferrer must answer.

    This is the seam contract, not a live call: it states exactly
    what the LLM is asked, so a later LLM inferrer can be built
    against it without changing the caller. The model must answer
    with JSON only, in this shape::

        {"desire": [{"label": "...", "weight": 0.7, "evidence": ["..."]}],
         "goal": [{"label": "...", "weight": 0.6, "evidence": ["..."]}],
         "values": []}

    Weights per level must sum to 1 (or the level is ``[]`` when
    nothing was read — never invent a hypothesis for an empty
    level). Labels must come from the profile, the world beliefs,
    or the message itself; evidence lists the words behind each
    label. The caller validates the JSON against that contract and
    falls back to the deterministic inferrer when it does not parse.
    """
    profile_lines = (
        "\n".join(f"- {key}: {value}" for key, value in (context.profile or {}).items())
        or "- (no profile facts)"
    )
    state = _world_state(context.world)
    world_lines: list[str] = []
    for bill_id, belief in (state.get("bill_beliefs") or {}).items():
        if not _get(belief, "paid", False):
            world_lines.append(f"- unpaid bill: {bill_id} (amount {_get(belief, 'amount')})")
    for price_key, belief in (state.get("price_beliefs") or {}).items():
        if _get(belief, "last_price", None) is not None:
            world_lines.append(f"- known price: {price_key} = {_get(belief, 'last_price')}")
    for task_kind, belief in (state.get("habit_beliefs") or {}).items():
        if _get(belief, "has_repeated_pattern", False):
            world_lines.append(f"- repeated habit: {task_kind}")
    world_text = "\n".join(world_lines) or "- (no world beliefs)"
    return (
        "Read the user's intent as a distribution over three levels: "
        "the current desire, the ongoing goal, and long-term values.\n"
        f"User message: {message}\n"
        f"Profile (stated or confirmed facts only):\n{profile_lines}\n"
        f"World beliefs (learned from observation only):\n{world_text}\n"
        "Answer with JSON only, no other text:\n"
        '{"desire": [{"label": "...", "weight": 0.0, "evidence": ["..."]}], '
        '"goal": [], "values": []}\n'
        "Weights in each level must sum to 1. A level with no evidence "
        "is an empty list — never invent a hypothesis. Labels must come "
        "from the profile, the world beliefs, or the message itself."
    )
