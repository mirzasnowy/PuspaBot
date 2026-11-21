"""Voice profile for co-drafting (B13).

The profile is built from the user's existing memory facts
(G7/G24 ``puspa.memory.facts``) — this module stores nothing of
its own about the voice. Fact keys it reads:

- ``voice.style`` — free-text description of how the user
  writes (parsed for rules when it uses a recognised shape).
- ``voice.greeting`` / ``voice.closing`` — the exact greeting /
  closing the user wants, when recorded.
- ``voice.correction.<id>`` — one user correction each, recorded
  by :meth:`DraftingService.correct_voice`. Each correction is
  parsed into a rule when it uses a recognised shape; a
  correction that does not parse is still kept as a style note
  and surfaced with the draft, so it is never silently dropped.
- ``preference.*`` — user preferences, surfaced with the draft
  and applied when they use a recognised shape.
- ``decision.*`` — past decisions. A draft whose brief asks for
  something a recorded decision forbids is flagged, never
  silently rewritten (see :meth:`VoiceProfile.flag_decisions`).

Recognised correction shapes (English and everyday Indonesian,
the two registers the user writes in):

- ``never use X`` / ``jangan pakai X`` — the phrase X must not
  appear in a draft.
- ``always start with X`` / ``selalu mulai dengan X`` — the
  draft must open with X.
- ``always use X`` / ``selalu pakai X`` — X must appear.
- ``keep it short`` / ``singkat`` — no filler, brief points only.
- ``casual`` / ``santai`` — everyday register, ``Halo`` greeting.
- ``formal`` / ``formal`` — formal register, ``Yth.`` greeting.

Default register is everyday Indonesian, never formal (B14):
when no greeting is recorded and no rule says otherwise, a
message or email opens with ``Halo,``. When the brief itself
is in English (no Indonesian marker word in it), the draft is
written in English instead, opening with ``Hi,`` — a draft
answers in the language the user briefed it in.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

__all__ = ["VoiceProfile", "VoiceRule", "build_voice_profile"]

# Words that mark a brief as Indonesian. The list is deliberately
# short: one hit is enough to pick the Indonesian register, and a
# brief with none of them is treated as English.
INDONESIAN_MARKERS = (
    "yang",
    "dan",
    "untuk",
    "saya",
    "kami",
    "terima",
    "tolong",
    "dengan",
    "ini",
    "itu",
    "halo",
    "kak",
    "mas",
    "mbak",
    "pak",
    "bu",
    "jangan",
    "bisa",
    "mau",
    "lagi",
    "sudah",
    "belum",
    "juga",
    "atau",
    "kalau",
    "karena",
    "supaya",
    "agar",
    "harga",
    "barang",
    "pesan",
    "kirim",
    "bayar",
)

_STOPWORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "to",
        "of",
        "for",
        "and",
        "or",
        "in",
        "on",
        "with",
        "yang",
        "dan",
        "untuk",
        "dengan",
        "di",
        "ke",
        "dari",
        "atau",
        "kalau",
        "karena",
        "supaya",
        "agar",
        "never",
        "always",
        "jangan",
        "selalu",
        "tidak",
        "not",
        "no",
    }
)


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def _content_words(text: str) -> list[str]:
    return [w for w in _words(text) if w not in _STOPWORDS and len(w) >= 3]


# Small bilingual lexicon for decision matching: the user records
# decisions in English or Indonesian and briefs in either, so a
# "never offer discounts" decision must still catch an Indonesian
# brief about "diskon". This is word matching with a fixed
# dictionary, not meaning — see the limits in docs/drafting.md.
_SYNONYMS = {
    "diskon": "discount",
    "pelanggan": "customer",
    "baru": "new",
    "tawarkan": "offer",
    "tawar": "offer",
    "penawaran": "offer",
    "kirim": "send",
    "bayar": "pay",
    "harga": "price",
    "barang": "product",
    "pesan": "message",
    "pesanan": "order",
    "terima": "receive",
    "pengembalian": "refund",
    "kembalikan": "refund",
    "struk": "receipt",
    "kwitansi": "receipt",
    "pengiriman": "shipping",
    "ongkir": "shipping",
    "jakarta": "jakarta",
}


def _normalise_word(word: str) -> str:
    """One canonical form: synonym mapped, plural ``s`` stripped."""
    word = _SYNONYMS.get(word, word)
    if len(word) > 3 and word.endswith("s"):
        word = word[:-1]
    return word


def _clean_phrase(raw: str) -> str:
    """Strip quotes and trailing punctuation from a rule phrase."""
    phrase = (raw or "").strip().strip("\"'\u201c\u201d\u2018\u2019").strip()
    return re.sub(r"[.,;:!]+$", "", phrase).strip()


@dataclass(frozen=True)
class VoiceRule:
    """One parsed voice rule: what it does and where it came from."""

    kind: str  # "ban" | "require_start" | "require" | "brevity" | "casual" | "formal"
    phrase: str = ""
    source: str = ""  # the fact content the rule was parsed from

    def describe(self) -> str:
        if self.kind == "ban":
            return f"never use {self.phrase!r}"
        if self.kind == "require_start":
            return f"always start with {self.phrase!r}"
        if self.kind == "require":
            return f"always use {self.phrase!r}"
        if self.kind == "brevity":
            return "keep it short"
        if self.kind == "casual":
            return "casual register"
        if self.kind == "formal":
            return "formal register"
        return self.kind


def parse_rule(text: str) -> VoiceRule | None:
    """Parse one correction / style note into a rule, or None.

    Returns None when the text uses no recognised shape — the
    caller keeps it as a style note instead of dropping it.
    """
    raw = (text or "").strip()
    lowered = raw.lower()
    match = re.match(
        r"(?:never use|never start with|jangan pakai|jangan mulai dengan)\s+(.+)$",
        raw,
        re.IGNORECASE,
    )
    if match:
        phrase = _clean_phrase(match.group(1))
        if phrase:
            return VoiceRule(kind="ban", phrase=phrase, source=raw)
    match = re.match(r"(?:always start with|selalu mulai dengan)\s+(.+)$", raw, re.IGNORECASE)
    if match:
        phrase = _clean_phrase(match.group(1))
        if phrase:
            return VoiceRule(kind="require_start", phrase=phrase, source=raw)
    match = re.match(r"(?:always use|selalu pakai)\s+(.+)$", raw, re.IGNORECASE)
    if match:
        phrase = _clean_phrase(match.group(1))
        if phrase:
            return VoiceRule(kind="require", phrase=phrase, source=raw)
    if re.search(r"\bkeep it short\b", lowered) or "singkat" in lowered:
        return VoiceRule(kind="brevity", source=raw)
    if re.search(r"\bcasual\b", lowered) or "santai" in lowered:
        return VoiceRule(kind="casual", source=raw)
    if re.search(r"\bformal\b", lowered):
        return VoiceRule(kind="formal", source=raw)
    return None


@dataclass
class VoiceProfile:
    """The user's voice, built from their recorded memory facts."""

    user_id: str
    greeting: str | None = None
    closing: str | None = None
    rules: list[VoiceRule] = field(default_factory=list)
    style_notes: list[str] = field(default_factory=list)
    preferences: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)

    @property
    def is_casual(self) -> bool:
        return any(rule.kind == "casual" for rule in self.rules)

    @property
    def is_formal(self) -> bool:
        return any(rule.kind == "formal" for rule in self.rules)

    @property
    def wants_brevity(self) -> bool:
        return any(rule.kind == "brevity" for rule in self.rules)

    def default_greeting(self, language: str) -> str:
        """The greeting for a draft when none is recorded."""
        if self.greeting:
            return self.greeting
        if self.is_formal:
            return "Yth. Bapak/Ibu," if language == "id" else "Dear Sir/Madam,"
        return "Halo," if language == "id" else "Hi,"

    def default_closing(self, language: str) -> str:
        """The closing for a draft when none is recorded."""
        if self.closing:
            return self.closing
        return "Terima kasih," if language == "id" else "Thank you,"

    def detect_language(self, brief: str) -> str:
        """``"id"`` when the brief carries an Indonesian marker word."""
        words = set(_words(brief))
        return "id" if words & set(INDONESIAN_MARKERS) else "en"

    def flag_decisions(self, brief: str) -> tuple[list[str], list[str]]:
        """Check a brief against recorded past decisions.

        Returns ``(flags, related)``: ``flags`` are decisions the
        brief contradicts — a recorded ``never <phrase>``
        decision whose phrase shares a content word with the
        brief. ``related`` are the other decisions that share a
        content word with the brief, surfaced so the drafter
        sees them. Matching is word overlap, not meaning: a flag
        is a warning to the user, not proof of a contradiction.
        """
        brief_words = {_normalise_word(w) for w in _words(brief)}
        flags: list[str] = []
        related: list[str] = []
        for decision in self.decisions:
            decision_words = {_normalise_word(w) for w in _content_words(decision)}
            if not decision_words & brief_words:
                continue
            never_match = re.match(r"(?:never|jangan)\s+(.+)$", decision.strip().lower())
            if never_match:
                banned = {_normalise_word(w) for w in _content_words(never_match.group(1))}
                if banned & brief_words:
                    flags.append(decision)
                    continue
            related.append(decision)
        return flags, related

    def apply_rules(self, text: str) -> tuple[str, list[str], list[str]]:
        """Apply the parsed rules to a draft body.

        Returns ``(text, applied, unapplied)``: ``applied`` names
        the rules that changed or constrained the text,
        ``unapplied`` names rules whose shape is recorded but
        cannot be applied mechanically (a ``require`` phrase the
        draft does not contain is added; a ``ban`` phrase is
        removed). A rule that needs a human judgement is never
        silently claimed as applied.
        """
        applied: list[str] = []
        unapplied: list[str] = []
        for rule in self.rules:
            if rule.kind == "ban":
                cleaned = re.sub(re.escape(rule.phrase), "", text, flags=re.IGNORECASE)
                cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
                cleaned = re.sub(r"\n[ \t]+\n", "\n\n", cleaned).strip()
                if cleaned != text:
                    text = cleaned
                    applied.append(rule.describe())
                else:
                    applied.append(rule.describe() + " (phrase absent)")
            elif rule.kind == "require_start":
                lines = text.split("\n", 1)
                if lines[0].rstrip().endswith(",") and len(lines[0]) <= 40:
                    # The first line is a greeting: the rule replaces
                    # it instead of stacking a second greeting on top.
                    text = rule.phrase + ("\n" + lines[1] if len(lines) > 1 else "")
                elif not text.lower().startswith(rule.phrase.lower()):
                    text = f"{rule.phrase} {text}".strip()
                applied.append(rule.describe())
            elif rule.kind == "require":
                if rule.phrase.lower() not in text.lower():
                    text = f"{text.rstrip()}\n\n{rule.phrase}".strip()
                    applied.append(rule.describe() + " (appended)")
                else:
                    applied.append(rule.describe())
            elif rule.kind in ("brevity", "casual", "formal"):
                applied.append(rule.describe())
        for note in self.style_notes:
            unapplied.append(f"recorded, not auto-applied: {note}")
        return text, applied, unapplied


def build_voice_profile(user_id, facts: list[dict]) -> VoiceProfile:
    """Build a voice profile from the user's active memory facts.

    ``facts`` are the shared record shape from
    ``puspa.memory.facts.list_active_facts`` (``key`` /
    ``content``). Unknown keys are ignored: the voice only ever
    reads the ``voice.*``, ``preference.*``, and ``decision.*``
    namespaces.
    """
    profile = VoiceProfile(user_id=str(user_id))
    for fact in facts:
        key = str(fact.get("key") or "")
        content = str(fact.get("content") or "").strip()
        if not content:
            continue
        if key == "voice.greeting":
            profile.greeting = content
        elif key == "voice.closing":
            profile.closing = content
        elif key.startswith("voice.correction.") or key == "voice.style":
            rule = parse_rule(content)
            if rule is not None:
                profile.rules.append(rule)
            else:
                profile.style_notes.append(content)
        elif key.startswith("preference."):
            profile.preferences.append(content)
            rule = parse_rule(content)
            if rule is not None and rule not in profile.rules:
                profile.rules.append(rule)
        elif key.startswith("decision."):
            profile.decisions.append(content)
    return profile
