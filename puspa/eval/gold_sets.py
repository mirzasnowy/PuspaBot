"""Gold sets with manual labels (plan section 8.4).

Three sets, 30 cases each, loaded from ``data/``:

- entity identity: a query against canonical products; the label
  is the expected product id, or null when no product matches.
  Measured as precision/recall of the top-1 match.
- quoted numbers: a draft with evidence markers plus the
  evidence; the label is the list of numbers the rendered draft
  must contain. Measured as quoted-number accuracy.
- orphan numbers: a draft plus the orphan numbers it contains;
  the label also says whether the draft must be held. Measured
  as orphan-number hold rate and wrong-hold rate (clean drafts
  held by mistake).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from puspa.evidence.evidence import EvidenceRecord
from puspa.evidence.renderer import find_orphan_numbers, render
from puspa.graphs.identity import rank_products
from puspa.guardrails.rails import RailSet, evidence_number_rail

DATA_DIR = Path(__file__).resolve().parent / "data"

_EVIDENCE_TIME = datetime(2026, 11, 20, 9, 0, 0, tzinfo=UTC)


def _load_json(name: str) -> dict:
    return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Entity identity
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EntityCase:
    id: str
    query: str
    expected: str | None


def load_entity_gold() -> tuple[list[dict], list[EntityCase]]:
    raw = _load_json("gold_entity_identity_v1.json")
    cases = [
        EntityCase(id=c["id"], query=c["query"], expected=c.get("expected")) for c in raw["cases"]
    ]
    return raw["products"], cases


def evaluate_entity_identity() -> dict:
    """Precision/recall of the top-1 product match."""
    products, cases = load_entity_gold()
    true_pos = 0
    false_pos = 0
    false_neg = 0
    per_case: list[dict] = []
    for case in cases:
        ranked = rank_products(products, case.query)
        top = ranked[0]["product"]["id"] if ranked else None
        if case.expected is None:
            correct = top is None
            if top is not None:
                false_pos += 1
        elif top == case.expected:
            true_pos += 1
            correct = True
        else:
            false_neg += 1
            correct = False
        per_case.append(
            {
                "id": case.id,
                "query": case.query,
                "expected": case.expected,
                "top": top,
                "correct": correct,
            }
        )
    precision = true_pos / (true_pos + false_pos) if (true_pos + false_pos) else 1.0
    recall = true_pos / (true_pos + false_neg) if (true_pos + false_neg) else 1.0
    return {
        "cases": len(cases),
        "true_positives": true_pos,
        "false_positives": false_pos,
        "false_negatives": false_neg,
        "precision": precision,
        "recall": recall,
        "per_case": per_case,
    }


# ---------------------------------------------------------------------------
# Quoted numbers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class QuotedCase:
    id: str
    draft: str
    evidence: tuple[dict, ...]
    expected_numbers: tuple[str, ...]


def load_quoted_gold() -> list[QuotedCase]:
    raw = _load_json("gold_quoted_numbers_v1.json")
    return [
        QuotedCase(
            id=c["id"],
            draft=c["draft"],
            evidence=tuple(c.get("evidence", [])),
            expected_numbers=tuple(c.get("expected_numbers", [])),
        )
        for c in raw["cases"]
    ]


def _evidence_record(item: dict) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=item["id"],
        claim=item.get("claim", ""),
        value=float(item["value"]),
        unit=item.get("unit", ""),
        source_url="https://example.invalid/evidence",
        time_take=_EVIDENCE_TIME,
        text_excerpt="",
        page_hash="0" * 64,
        reader_id="eval",
    )


def evaluate_quoted_numbers() -> dict:
    """Quoted-number accuracy: expected numbers found after render."""
    found = 0
    total = 0
    per_case: list[dict] = []
    for case in load_quoted_gold():
        by_id = {item["id"]: _evidence_record(item) for item in case.evidence}
        rendered, _missing = render(case.draft, by_id)
        case_found = sum(1 for number in case.expected_numbers if number in rendered)
        found += case_found
        total += len(case.expected_numbers)
        per_case.append(
            {
                "id": case.id,
                "expected": list(case.expected_numbers),
                "found": case_found,
                "rendered": rendered,
            }
        )
    return {
        "cases": len(per_case),
        "found": found,
        "total": total,
        "accuracy": found / total if total else 1.0,
        "per_case": per_case,
    }


# ---------------------------------------------------------------------------
# Orphan numbers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OrphanCase:
    id: str
    draft: str
    expected_orphans: tuple[str, ...]
    expect_hold: bool


def load_orphan_gold() -> list[OrphanCase]:
    raw = _load_json("gold_orphan_numbers_v1.json")
    return [
        OrphanCase(
            id=c["id"],
            draft=c["draft"],
            expected_orphans=tuple(c.get("expected_orphans", [])),
            expect_hold=bool(c.get("expect_hold", False)),
        )
        for c in raw["cases"]
    ]


def evaluate_orphan_numbers() -> dict:
    """Orphan-number hold rate and wrong-hold rate.

    Hold rate: orphan drafts the output rail blocks. Wrong-hold
    rate: clean drafts the output rail blocks by mistake.
    """
    rails = RailSet([evidence_number_rail()])
    orphan_cases = 0
    held = 0
    clean_cases = 0
    wrong_held = 0
    label_mismatches: list[dict] = []
    per_case: list[dict] = []
    for case in load_orphan_gold():
        found = find_orphan_numbers(case.draft)
        if sorted(found) != sorted(case.expected_orphans):
            label_mismatches.append(
                {"id": case.id, "expected": list(case.expected_orphans), "found": found}
            )
        verdict = rails.check_output(case.draft)
        blocked = not verdict.allowed
        if case.expect_hold:
            orphan_cases += 1
            if blocked:
                held += 1
        else:
            clean_cases += 1
            if blocked:
                wrong_held += 1
        per_case.append({"id": case.id, "orphans": found, "blocked": blocked})
    return {
        "cases": len(per_case),
        "orphan_cases": orphan_cases,
        "held": held,
        "hold_rate": held / orphan_cases if orphan_cases else 1.0,
        "clean_cases": clean_cases,
        "wrong_held": wrong_held,
        "wrong_hold_rate": wrong_held / clean_cases if clean_cases else 0.0,
        "label_mismatches": label_mismatches,
        "per_case": per_case,
    }


def evaluate_all_gold_sets() -> dict:
    """All three gold-set evaluations in one dict."""
    return {
        "entity_identity": evaluate_entity_identity(),
        "quoted_numbers": evaluate_quoted_numbers(),
        "orphan_numbers": evaluate_orphan_numbers(),
    }
