"""Nanobot pin record and fork rule (E-19, plan section 9.4).

Puspa uses nanobot (HKUDS/nanobot, MIT) as its agent chassis through a
git pin, not a fork: zero lines of nanobot upstream code are changed,
and all Puspa behaviour lives in the wrapper interface in
``puspa/chassis/``. This module is the machine-readable side of that
policy:

- :func:`parse_pin_from_requirements` reads the pinned commit out of
  ``requirements.txt`` (the single source of truth for the pin).
- :class:`PinRecord` is the JSON record kept at
  ``puspa/chassis/pin.json``: the current pin, when it was pinned, when
  it was last checked against upstream, the newest upstream commit seen
  at that check, and the open security advisories that affect the pin.
- :func:`evaluate_fork_rule` implements the fork rule: if two or more
  upstream security patches lag more than 30 days, or the wrapper
  needs monkeypatches in too many places, the pin policy stops and the
  chassis becomes a clearly-named vendor subtree (an admitted fork).
  The rule is a decision procedure, not a judgement call made in a
  hurry: it returns its reasons so the bump report can quote them.

No network access happens in this module. Fetching upstream state is
the job of ``scripts/nanobot_bump.py``; this module only decides what
the fetched facts mean.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

__all__ = [
    "FORK_LAG_THRESHOLD_DAYS",
    "FORK_MIN_LAGGING_PATCHES",
    "MONKEYPATCH_LIMIT",
    "UPSTREAM_OWNER",
    "UPSTREAM_REPO",
    "UPSTREAM_URL",
    "Advisory",
    "PinRecord",
    "evaluate_fork_rule",
    "lagging_advisories",
    "parse_pin_from_requirements",
    "read_pin_record",
    "record_matches_requirements",
    "write_pin_record",
]

UPSTREAM_OWNER = "HKUDS"
UPSTREAM_REPO = "nanobot"
UPSTREAM_URL = "https://github.com/HKUDS/nanobot"

#: A security patch counts as lagging once it has been available
#: upstream for more than this many days without being picked up.
FORK_LAG_THRESHOLD_DAYS = 30

#: Fork when at least this many security patches lag past the threshold.
FORK_MIN_LAGGING_PATCHES = 2

#: Fork when the wrapper needs monkeypatches in more than this many
#: distinct places. A monkeypatch here means runtime patching of a
#: nanobot object (attribute assignment on an imported nanobot module,
#: class, or instance) from Puspa code, as opposed to using a
#: documented extension point (constructor parameter, hook, registry,
#: or subclass). Five is deliberately small: a chassis that needs six
#: separate patches is no longer a clean dependency, it is a fork
#: hiding behind a pin.
MONKEYPATCH_LIMIT = 5

_PIN_LINE = re.compile(r"nanobot-ai\s*@\s*git\+https://github\.com/HKUDS/nanobot@([0-9a-f]{7,40})")


@dataclass(frozen=True)
class Advisory:
    """One upstream security advisory that affects the pinned nanobot."""

    advisory_id: str
    summary: str
    published_on: date
    fixed_in_commit: str | None = None
    severity: str = "unknown"

    def lag_days(self, on: date) -> int:
        """Days from publication to ``on`` (negative when not yet published)."""
        return (on - self.published_on).days


@dataclass
class PinRecord:
    """The recorded state of the nanobot pin (``puspa/chassis/pin.json``)."""

    commit: str
    pinned_on: date
    last_checked_on: date | None = None
    last_upstream_commit: str | None = None
    advisories_open: list[Advisory] = field(default_factory=list)
    forked: bool = False
    vendor_path: str | None = None

    def to_dict(self) -> dict:
        """Serialise to the JSON shape stored in ``pin.json``."""
        return {
            "commit": self.commit,
            "pinned_on": self.pinned_on.isoformat(),
            "last_checked_on": (
                self.last_checked_on.isoformat() if self.last_checked_on is not None else None
            ),
            "last_upstream_commit": self.last_upstream_commit,
            "advisories_open": [
                {
                    "advisory_id": advisory.advisory_id,
                    "summary": advisory.summary,
                    "published_on": advisory.published_on.isoformat(),
                    "fixed_in_commit": advisory.fixed_in_commit,
                    "severity": advisory.severity,
                }
                for advisory in self.advisories_open
            ],
            "forked": self.forked,
            "vendor_path": self.vendor_path,
        }

    @classmethod
    def from_dict(cls, data: dict) -> PinRecord:
        """Parse the JSON shape stored in ``pin.json``."""
        advisories = [
            Advisory(
                advisory_id=item["advisory_id"],
                summary=item["summary"],
                published_on=date.fromisoformat(item["published_on"]),
                fixed_in_commit=item.get("fixed_in_commit"),
                severity=item.get("severity", "unknown"),
            )
            for item in data.get("advisories_open", [])
        ]
        last_checked = data.get("last_checked_on")
        return cls(
            commit=data["commit"],
            pinned_on=date.fromisoformat(data["pinned_on"]),
            last_checked_on=date.fromisoformat(last_checked) if last_checked else None,
            last_upstream_commit=data.get("last_upstream_commit"),
            advisories_open=advisories,
            forked=bool(data.get("forked", False)),
            vendor_path=data.get("vendor_path"),
        )


def parse_pin_from_requirements(text: str) -> str:
    """Return the pinned nanobot commit from ``requirements.txt`` text.

    Raises ``ValueError`` when no ``nanobot-ai @ git+...`` pin line is
    present, so a missing pin fails loudly instead of silently
    defaulting to whatever happens to be installed.
    """
    for line in text.splitlines():
        match = _PIN_LINE.search(line.strip())
        if match is not None:
            return match.group(1)
    raise ValueError("no nanobot-ai git pin found in requirements text")


def read_pin_record(path: str | Path) -> PinRecord:
    """Read a ``PinRecord`` from its JSON file."""
    return PinRecord.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def write_pin_record(record: PinRecord, path: str | Path) -> None:
    """Write a ``PinRecord`` to its JSON file (two-space indent)."""
    Path(path).write_text(json.dumps(record.to_dict(), indent=2) + "\n", encoding="utf-8")


def record_matches_requirements(record: PinRecord, requirements_text: str) -> bool:
    """True when the record and ``requirements.txt`` pin the same commit."""
    try:
        return parse_pin_from_requirements(requirements_text) == record.commit
    except ValueError:
        return False


def lagging_advisories(
    advisories: list[Advisory],
    on: date,
    threshold_days: int = FORK_LAG_THRESHOLD_DAYS,
) -> list[Advisory]:
    """Advisories published more than ``threshold_days`` ago.

    An advisory stops lagging once it carries a fix commit: the fix is
    what the bump picks up, so a fixed advisory is a bump to schedule,
    not a fork trigger. Only unfixed advisories older than the
    threshold count.
    """
    return [
        advisory
        for advisory in advisories
        if advisory.fixed_in_commit is None and advisory.lag_days(on) > threshold_days
    ]


def evaluate_fork_rule(
    advisories: list[Advisory],
    monkeypatch_sites: int,
    on: date,
) -> tuple[bool, list[str]]:
    """Apply the fork rule and return ``(triggered, reasons)``.

    The rule fires when either condition holds:

    1. Two or more upstream security patches lag more than 30 days
       (see :func:`lagging_advisories`).
    2. The wrapper needs monkeypatches in more than
       :data:`MONKEYPATCH_LIMIT` places.

    When the rule fires, the correct response is to stop pretending the
    pin is a clean dependency: create a clearly-named vendor subtree
    (``vendor/nanobot/``), admit it as a fork in the docs and in
    ``pin.json`` (``forked: true`` with ``vendor_path``), and re-pin
    against the subtree. The rule never fires silently: every reason
    is returned as a human-readable string for the bump report.
    """
    reasons: list[str] = []
    lagging = lagging_advisories(advisories, on)
    if len(lagging) >= FORK_MIN_LAGGING_PATCHES:
        names = ", ".join(advisory.advisory_id for advisory in lagging)
        reasons.append(
            f"{len(lagging)} security patches lag more than {FORK_LAG_THRESHOLD_DAYS} days: {names}"
        )
    if monkeypatch_sites > MONKEYPATCH_LIMIT:
        reasons.append(
            f"wrapper needs monkeypatches in {monkeypatch_sites} places, "
            f"over the limit of {MONKEYPATCH_LIMIT}"
        )
    return (len(reasons) > 0, reasons)
