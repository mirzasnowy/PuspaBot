"""Frozen attack corpus and benign-content set (plan section 8.5).

The corpus file is versioned and frozen: it is never edited to
tune a prompt or a defense. New findings go into the live corpus
(a new version), and every finding becomes a regression test.

File layout (``data/attack_corpus_v1.json``):

- ``manifest``: version, date, counts, and the sha256 of the
  canonical JSON of ``live_records``. The loader recomputes the
  hash, so a silent edit is caught.
- ``live_records``: payloads with real text. In v1 these are all
  ``internal_handwritten``: one internal corpus author wrote them,
  because no people outside the team were available. The plan asks
  for 30% hand-written by people outside the team; that slice is
  NOT filled by outsiders here, and the scorecard says so.
- ``slots``: declared empty slots for the slices that are not
  filled yet. The loader expands them into records with
  ``status="empty_slot"`` and ``text=None`` so the corpus always
  has exactly 200 records and the slice accounting stays visible:

  - 60 ``adaptive_attacker`` slots: the harness for this slice
    exists (``TransformAttacker`` in ``attack_harness``), but no
    adaptive attacker model has been run, so no payloads exist.
  - 80 ``public_corpus`` slots (40 garak probes, 40 AgentDojo):
    named sources with recorded licenses. No payload text was
    imported in v1; the license of each source must be re-checked
    before any import.

Attacker goals (plan section 8.5):

1. leak data or credentials
2. trigger an outbound action without rights (send, share, post,
   pay, delete)
3. poison memory or profile so it sticks
4. weaken rules or guardrails
5. burn cost through loops
6. forge an approval card
7. install a skill or module
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"
ATTACK_CORPUS_PATH = DATA_DIR / "attack_corpus_v1.json"
BENIGN_CORPUS_PATH = DATA_DIR / "benign_indonesian_v1.json"

CORPUS_VERSION = "1.0.0"
TOTAL_RECORDS = 200

GOALS = {
    1: "leak_data_or_credentials",
    2: "unauthorized_outbound_action",
    3: "poison_memory_or_profile",
    4: "weaken_rules_or_guardrails",
    5: "burn_cost_with_loops",
    6: "forge_approval_card",
    7: "install_skill_or_module",
}

CHANNELS = (
    "email",
    "web_listing",
    "seller_name",
    "drive_file",
    "search_result",
    "tool_output",
    "room_message",
    "ocr_text",
    "voice_transcript",
    "contact_name",
)

TECHNIQUES = (
    "direct",
    "fake_role",
    "mixed_language",
    "slang",
    "leetspeak",
    "base64",
    "homoglyph",
    "zero_width",
    "hidden_html",
    "split_payload",
    "multi_turn",
    "social_pressure",
    "fraud_apk",
    "fraud_bank_ojk",
    "fraud_wrong_transfer",
    "fraud_like_task",
    "fraud_investment_loan",
)


@dataclass(frozen=True)
class AttackPayload:
    """One corpus record: a live payload or a declared empty slot."""

    id: str
    channel: str
    goal: int
    goal_name: str
    technique: str
    action: str
    text: str | None
    origin: str
    status: str  # "live" or "empty_slot"
    source: str | None = None
    license: str | None = None
    note: str = ""

    @property
    def is_live(self) -> bool:
        return self.status == "live"


@dataclass
class AttackCorpus:
    """A loaded corpus: manifest plus exactly 200 records."""

    manifest: dict
    records: list[AttackPayload] = field(default_factory=list)

    @property
    def live(self) -> list[AttackPayload]:
        return [r for r in self.records if r.is_live]

    @property
    def slots(self) -> list[AttackPayload]:
        return [r for r in self.records if not r.is_live]

    def slice_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for record in self.records:
            counts[record.origin] = counts.get(record.origin, 0) + 1
        return counts

    def live_by_goal(self) -> dict[int, list[AttackPayload]]:
        by_goal: dict[int, list[AttackPayload]] = {}
        for record in self.live:
            by_goal.setdefault(record.goal, []).append(record)
        return by_goal


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _live_records_sha256(live_records: list[dict]) -> str:
    return hashlib.sha256(_canonical_json(live_records).encode("utf-8")).hexdigest()


def _expand_slots(manifest: dict, slots: dict, start_id: int) -> list[AttackPayload]:
    """Expand the declared empty slots into records deterministically."""
    expanded: list[AttackPayload] = []
    next_n = start_id
    adaptive = slots.get("adaptive_attacker", {})
    for _ in range(int(adaptive.get("count", 0))):
        expanded.append(
            AttackPayload(
                id=f"ATK-{next_n:03d}",
                channel="",
                goal=0,
                goal_name="",
                technique="adaptive",
                action="",
                text=None,
                origin="adaptive_attacker",
                status="empty_slot",
                source=None,
                license=None,
                note=str(
                    adaptive.get(
                        "note",
                        "Slot reserved for an adaptive attacker model run; "
                        "no model has been run yet.",
                    )
                ),
            )
        )
        next_n += 1
    public = slots.get("public_corpus", {})
    for source in public.get("sources", []):
        for _ in range(int(source.get("count", 0))):
            expanded.append(
                AttackPayload(
                    id=f"ATK-{next_n:03d}",
                    channel="",
                    goal=0,
                    goal_name="",
                    technique="public_corpus",
                    action="",
                    text=None,
                    origin="public_corpus",
                    status="empty_slot",
                    source=str(source.get("name", "")),
                    license=str(source.get("license", "")),
                    note=str(source.get("note", "")),
                )
            )
            next_n += 1
    return expanded


def load_attack_corpus(path: str | Path | None = None) -> AttackCorpus:
    """Load and verify the frozen attack corpus.

    Verifies: the live-record hash in the manifest, the total
    record count (200), unique ids, known channels/goals/
    techniques on live records, and that live records have text
    while slots have none.
    """
    corpus_path = Path(path) if path is not None else ATTACK_CORPUS_PATH
    raw = json.loads(corpus_path.read_text(encoding="utf-8"))
    manifest = raw["manifest"]
    live_raw: list[dict] = raw["live_records"]
    expected_hash = manifest.get("live_records_sha256", "")
    actual_hash = _live_records_sha256(live_raw)
    if expected_hash != actual_hash:
        raise ValueError(
            f"attack corpus hash mismatch: manifest says {expected_hash}, "
            f"live records hash to {actual_hash} (the corpus was edited "
            "without bumping the version)"
        )
    records: list[AttackPayload] = []
    for item in live_raw:
        if item["channel"] not in CHANNELS:
            raise ValueError(f"unknown channel {item['channel']!r} in {item['id']}")
        if item["goal"] not in GOALS:
            raise ValueError(f"unknown goal {item['goal']!r} in {item['id']}")
        if item["technique"] not in TECHNIQUES:
            raise ValueError(f"unknown technique {item['technique']!r} in {item['id']}")
        if not item.get("text"):
            raise ValueError(f"live record {item['id']} has no payload text")
        records.append(
            AttackPayload(
                id=item["id"],
                channel=item["channel"],
                goal=int(item["goal"]),
                goal_name=GOALS[int(item["goal"])],
                technique=item["technique"],
                action=item.get("action", ""),
                text=item["text"],
                origin=item.get("origin", "internal_handwritten"),
                status="live",
                source=item.get("source"),
                license=item.get("license"),
                note=item.get("note", ""),
            )
        )
    records.extend(_expand_slots(manifest, raw.get("slots", {}), len(records) + 1))
    if len(records) != TOTAL_RECORDS:
        raise ValueError(f"attack corpus must hold {TOTAL_RECORDS} records, got {len(records)}")
    ids = [r.id for r in records]
    if len(set(ids)) != len(ids):
        raise ValueError("attack corpus has duplicate record ids")
    return AttackCorpus(manifest=manifest, records=records)


@dataclass(frozen=True)
class BenignMessage:
    """One normal Indonesian message for scanner false positives."""

    id: str
    text: str
    tricky: bool = False
    note: str = ""


def load_benign_corpus(path: str | Path | None = None) -> list[BenignMessage]:
    """Load the benign-content set (normal Indonesian messages)."""
    benign_path = Path(path) if path is not None else BENIGN_CORPUS_PATH
    raw = json.loads(benign_path.read_text(encoding="utf-8"))
    return [
        BenignMessage(
            id=item["id"],
            text=item["text"],
            tricky=bool(item.get("tricky", False)),
            note=item.get("note", ""),
        )
        for item in raw["messages"]
    ]
