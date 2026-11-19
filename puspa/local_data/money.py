"""Indonesian money amounts in everyday text (ticket B5, checklist 15.1).

Users write amounts the way they talk: ``Rp1.250.000`` (dot
thousands, comma decimal), ``50rb``, ``50k``, ``1,5 jt``,
``dua juta``, ``seratus ribu``. A wrong zero is a money
mistake, so this module parses those forms into integer
Rupiah with explicit rules and never guesses: a bare digit
run with no ``Rp``, no suffix, and no number word is not an
amount (it could be an account number or a quantity), and
anything unparsable returns ``None``.

:func:`parse_rupiah_amount` finds the first money mention in
a text. :func:`format_rupiah` renders an integer amount the
local way (``Rp1.250.000``, dots, no decimals) for every
user-facing surface.
"""

from __future__ import annotations

import re

#: Suffixes users attach to a number, and what they multiply by.
_SUFFIX_MULTIPLIERS: dict[str, int] = {
    "rb": 1_000,
    "k": 1_000,
    "ribu": 1_000,
    "jt": 1_000_000,
    "juta": 1_000_000,
    "jeti": 1_000_000,
    "miliar": 1_000_000_000,
    "milyar": 1_000_000_000,
}

#: Number words: ones, teens, tens, and the hundred/thousand
#: words. "se-" forms (seratus, seribu, sejuta) are handled by
#: the parser as one of the base word with an implicit one.
_ONES: dict[str, int] = {
    "nol": 0,
    "satu": 1,
    "dua": 2,
    "tiga": 3,
    "empat": 4,
    "lima": 5,
    "enam": 6,
    "tujuh": 7,
    "delapan": 8,
    "sembilan": 9,
    "sepuluh": 10,
    "sebelas": 11,
}

_TEENS: dict[str, int] = {
    "dua belas": 12,
    "tiga belas": 13,
    "empat belas": 14,
    "lima belas": 15,
    "enam belas": 16,
    "tujuh belas": 17,
    "delapan belas": 18,
    "sembilan belas": 19,
}

_TENS: dict[str, int] = {
    "dua puluh": 20,
    "tiga puluh": 30,
    "empat puluh": 40,
    "lima puluh": 50,
    "enam puluh": 60,
    "tujuh puluh": 70,
    "delapan puluh": 80,
    "sembilan puluh": 90,
}

#: Multiplier words after a number-word group.
_WORD_MULTIPLIERS: dict[str, int] = {
    "ribu": 1_000,
    "juta": 1_000_000,
    "jeti": 1_000_000,
    "miliar": 1_000_000_000,
    "milyar": 1_000_000_000,
}

#: "se-" words: one hundred / one thousand / one million.
_SE_WORDS: dict[str, int] = {
    "seratus": 100,
    "seribu": 1_000,
    "sejuta": 1_000_000,
    "semiliar": 1_000_000_000,
    "semilyar": 1_000_000_000,
}

_NUMBER_WORDS = frozenset(
    list(_ONES) + list(_TEENS) + list(_TENS) + ["ratus", "puluh"] + list(_SE_WORDS)
)

# A numeric money mention: optional "Rp", digits with dots/commas,
# optional suffix. The suffix and the "Rp" are what make a bare
# number an amount; a plain digit run matches here only so the
# caller can decide (see _parse_numeric_candidate).
_NUMERIC_RE = re.compile(
    r"(rp\s*)?(\d[\d.,]*)\s*(rb|k|jt|juta|jeti|ribu|miliar|milyar)?\b",
    re.IGNORECASE,
)


def _numeric_value(raw: str, suffix: str | None) -> int | None:
    """One numeric token plus its suffix, as integer Rupiah.

    With a suffix, a single separator followed by 1-2 digits is
    a decimal (``1,5 jt`` = 1.5 million); a separator followed
    by exactly 3 digits is a thousands separator (``1.500 rb``
    = 1500 thousand). Without a suffix the token must already
    be a plain integer or a thousands-grouped number.
    """
    digits = raw.replace(" ", "")
    if suffix:
        multiplier = _SUFFIX_MULTIPLIERS[suffix.lower()]
        separators = [char for char in digits if char in ".,"]
        if len(separators) == 1 and len(digits.split(separators[0])[1]) in (1, 2):
            try:
                return round(float(digits.replace(",", ".")) * multiplier)
            except ValueError:
                return None
        cleaned = digits.replace(".", "").replace(",", "")
        try:
            return int(cleaned) * multiplier
        except ValueError:
            return None
    if "," in digits:
        # A comma without a suffix is the decimal separator:
        # "Rp1.250.000,50" -> 1250000 (rounded).
        try:
            whole, _, _fraction = digits.rpartition(",")
            return round(float(whole.replace(".", "") + "." + _fraction))
        except ValueError:
            return None
    cleaned = digits.replace(".", "")
    if not cleaned.isdigit():
        return None
    return int(cleaned)


def _is_thousands_grouped(raw: str) -> bool:
    """True when the token is dotted in groups of three (``25.000``)."""
    groups = raw.split(".")
    return len(groups) >= 2 and all(len(group) == 3 for group in groups[1:])


def _parse_numeric_candidate(match: re.Match[str]) -> int | None:
    """A regex match, as Rupiah — or None when it is not an amount."""
    has_rp = bool(match.group(1))
    raw = match.group(2)
    suffix = match.group(3)
    if suffix:
        return _numeric_value(raw, suffix)
    if has_rp:
        return _numeric_value(raw, None)
    # Bare dotted thousands ("harganya 25.000") read as a price;
    # a bare plain digit run does not — it could be an account
    # number, a quantity, or a date.
    if "." in raw and _is_thousands_grouped(raw):
        return _numeric_value(raw, None)
    return None


def _parse_word_number(words: list[str]) -> int | None:
    """Indonesian number words (``dua ratus lima puluh`` -> 250).

    Handles ones, teens, tens, hundreds (``ratus``, ``seratus``),
    and the ``se-`` words. Returns None when the words do not
    form a number.
    """
    if not words:
        return None
    joined = " ".join(words)
    if joined in _SE_WORDS:
        return _SE_WORDS[joined]
    total = 0
    current = 0
    index = 0
    while index < len(words):
        word = words[index]
        two_word = " ".join(words[index : index + 2]) if index + 1 < len(words) else ""
        if two_word in _TEENS or two_word in _TENS:
            current += _TEENS.get(two_word, _TENS.get(two_word, 0))
            index += 2
            continue
        if word in _ONES:
            current += _ONES[word]
            index += 1
            continue
        if word in _SE_WORDS:
            current += _SE_WORDS[word]
            index += 1
            continue
        if word == "ratus":
            current = (current or 1) * 100
            index += 1
            continue
        if word == "puluh":
            current = (current or 1) * 10
            index += 1
            continue
        return None
    total += current
    return total if total > 0 else None


_WORD_RUN_RE = re.compile(
    r"\b((?:satu|dua|tiga|empat|lima|enam|tujuh|delapan|sembilan|sepuluh|sebelas"
    r"|belas|puluh|ratus|seratus|seribu|sejuta|semiliar|semilyar)(?:\s+"
    r"(?:satu|dua|tiga|empat|lima|enam|tujuh|delapan|sembilan|sepuluh|sebelas"
    r"|belas|puluh|ratus|seratus|seribu|sejuta|semiliar|semilyar))*)\s+"
    r"(ribu|juta|jeti|miliar|milyar)\b",
    re.IGNORECASE,
)

#: A lone "se-" word with a multiplier ("seribu", "sejuta" are
#: already amounts on their own; "seratus ribu" needs the run).
_SE_ALONE_RE = re.compile(r"\b(seribu|sejuta|semiliar|semilyar)\b", re.IGNORECASE)


def _parse_word_amount(text: str) -> int | None:
    """A number-word amount (``seratus ribu`` -> 100000).

    Compound amounts add up: ``satu juta lima ratus ribu`` is
    the ``satu juta`` group plus the ``lima ratus ribu`` group.
    A trailing number-word group with no multiplier (``satu
    juta lima ratus``) adds its plain value.
    """
    lowered = text.lower()
    total = 0
    found = False
    end = 0
    for match in _WORD_RUN_RE.finditer(lowered):
        words = match.group(1).split()
        number = _parse_word_number(words)
        if number is None:
            continue
        total += number * _WORD_MULTIPLIERS[match.group(2).lower()]
        found = True
        end = match.end()
    if found:
        tail = lowered[end:].strip()
        tail_match = re.match(
            r"((?:satu|dua|tiga|empat|lima|enam|tujuh|delapan|sembilan|sepuluh|sebelas"
            r"|belas|puluh|ratus|seratus|seribu|sejuta|semiliar|semilyar)"
            r"(?:\s+(?:satu|dua|tiga|empat|lima|enam|tujuh|delapan|sembilan|sepuluh"
            r"|sebelas|belas|puluh|ratus|seratus|seribu|sejuta|semiliar|semilyar))*)",
            tail,
        )
        if tail_match is not None:
            tail_number = _parse_word_number(tail_match.group(1).split())
            if tail_number is not None:
                total += tail_number
        return total if total > 0 else None
    alone = _SE_ALONE_RE.search(lowered)
    if alone is not None:
        return _SE_WORDS[alone.group(1).lower()]
    return None


def parse_rupiah_amount(text: str) -> int | None:
    """First money amount in everyday Indonesian text, in Rupiah.

    Understands ``Rp1.250.000``, ``Rp 1.250.000,50``, ``50rb``,
    ``50k``, ``1,5 jt``, ``2 juta``, ``500 ribu``, ``seratus
    ribu``, ``dua jeti``, ``satu juta lima ratus ribu``.
    Returns ``None`` when the text names no amount — never a
    guess, and never a bare digit run (an account number or a
    quantity is not money).
    """
    if not text:
        return None
    lowered = text.lower()
    for match in _NUMERIC_RE.finditer(lowered):
        amount = _parse_numeric_candidate(match)
        if amount is not None and amount > 0:
            return amount
    return _parse_word_amount(lowered)


def format_rupiah(amount: int) -> str:
    """Integer Rupiah as ``Rp1.250.000`` (dot thousands, no decimals)."""
    if not isinstance(amount, int) or isinstance(amount, bool):
        raise ValueError(f"amount must be an integer number of Rupiah, got {amount!r}")
    if amount < 0:
        raise ValueError(f"amount must not be negative, got {amount}")
    return "Rp" + f"{amount:,}".replace(",", ".")
