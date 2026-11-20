"""Release scorecard (plan section 8.7).

One page per release: reference-task success, retrieval recall,
attack success rate, kill-drill time, restore-drill time, and
cost per task, each with a date, a corpus version, and an
uncertainty interval. A number that is not on the card must not
be quoted as a claim.

``build_scorecard`` is pure assembly over measurement inputs, so
it is deterministic and unit-testable. ``main`` runs the offline
measurements (reference tasks, retrieval comparison, attack
harness, benign false positives, utility under attack, gold
sets) and writes the card as JSON and Markdown.

Honest limits, printed on every card:

- the attack corpus authors are internal (one corpus author, not
  people outside the team), and the adaptive and public slices
  are empty slots in v1;
- the ASR measures pass-through of the deterministic stack, not
  end-to-end agent behavior with a live model;
- 0 successes in N trials is an upper bound of 3/N at 95%
  confidence (rule of three), never "safe";
- kill-drill and restore-drill times are NOT measured by this
  package and are reported as not measured, not as zero.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from .attack_harness import (
    AttackHarness,
    measure_scanner_false_positives,
    measure_utility_under_attack,
)
from .corpus import CORPUS_VERSION, load_attack_corpus, load_benign_corpus
from .gold_sets import evaluate_all_gold_sets
from .intervals import format_interval, rule_of_three_upper_bound, wilson_interval
from .reference_tasks import run_reference_tasks, summarize_runs
from .retrieval_sets import (
    INTERNAL_QUESTION_COUNT,
    PUBLIC_QUESTION_COUNT,
    RETRIEVAL_SET_VERSION,
    compare_retrievers,
)

HONEST_LIMITS = [
    (
        "Attack corpus v1 live payloads (60) were written by one internal corpus author, "
        "not by people outside the team as the plan asks; the 30% outside-author slice "
        "is filled by the internal author and flagged here."
    ),
    (
        "The 60 adaptive-attacker records and 80 public-corpus records are empty slots "
        "in v1: no attacker model has been run and no public payload text was imported."
    ),
    (
        "ASR measures pass-through of the deterministic defense stack (scanner, rails, "
        "approval policy), not end-to-end agent behavior with a live model."
    ),
    (
        "0 successes in N trials means an upper bound of 3/N at 95% confidence "
        "(rule of three), not 'safe'."
    ),
    (
        "The 50 public retrieval questions are empty slots: the 2WikiMultihopQA license "
        "was not confirmed, so nothing was imported."
    ),
    (
        "Reference-task cost is 0.0 because no model is called; the cost of a real "
        "model-backed task is a different measurement."
    ),
]

NOT_MEASURED = [
    {
        "metric": "kill_drill_seconds",
        "reason": "Kill-switch drill time is owned by the audit/kill-switch work, not by this package.",
    },
    {
        "metric": "restore_drill_minutes",
        "reason": "Restore-from-zero drill time is owned by the backup/restore work, not by this package.",
    },
    {
        "metric": "live_model_attack_success_rate",
        "reason": "End-to-end ASR with a live model has not been run; the deterministic-stack ASR below is not a substitute.",
    },
]


def build_scorecard(inputs: dict) -> dict:
    """Assemble a scorecard dict from measurement inputs.

    ``inputs`` carries the already-measured blocks (reference
    tasks, retrieval, attack, benign, utility, gold, mutation).
    Missing blocks are reported as not measured, never invented.
    """
    ref = inputs.get("reference_tasks", {})
    ref_successes = int(ref.get("successes", 0))
    ref_trials = int(ref.get("trials", 0))
    ref_low, ref_high = wilson_interval(ref_successes, ref_trials)
    retrieval = inputs.get("retrieval", {})
    attack = inputs.get("attack", {})
    live = int(attack.get("live", 0))
    success = int(attack.get("success", 0))
    asr_low, asr_high = wilson_interval(success, live)
    gold = inputs.get("gold", {})
    mutation = inputs.get("mutation", {})
    return {
        "scorecard_version": "1.0.0",
        "date": inputs.get("date", datetime.now(UTC).date().isoformat()),
        "corpus_version": CORPUS_VERSION,
        "retrieval_set_version": RETRIEVAL_SET_VERSION,
        "reference_tasks": {
            "successes": ref_successes,
            "trials": ref_trials,
            "success_rate": ref_successes / ref_trials if ref_trials else 0.0,
            "wilson_95": [ref_low, ref_high],
            "summary": format_interval(ref_successes, ref_trials),
            "total_seconds": ref.get("total_seconds"),
            "total_cost_rupiah": ref.get("total_cost_rupiah"),
        },
        "retrieval": {
            "internal_questions": INTERNAL_QUESTION_COUNT,
            "public_questions": PUBLIC_QUESTION_COUNT,
            "public_questions_live": 0,
            "retrievers": retrieval,
        },
        "attack": {
            "live_payloads": live,
            "empty_slots": int(attack.get("empty_slots", 0)),
            "blocked": int(attack.get("blocked", 0)),
            "held_at_approval": int(attack.get("held_at_approval", 0)),
            "success": success,
            "attack_success_rate": success / live if live else 0.0,
            "wilson_95": [asr_low, asr_high],
            "summary": format_interval(success, live),
            "rule_of_three_upper_bound_95": rule_of_three_upper_bound(live),
            "by_goal": attack.get("by_goal", {}),
            "broad_always_share_scenario": attack.get("broad_always_share_scenario", {}),
        },
        "scanner_false_positives": inputs.get("benign", {}),
        "utility_under_attack": inputs.get("utility", {}),
        "gold_sets": gold,
        "mutation": {
            "measured": bool(mutation.get("measured", False)),
            "score": mutation.get("score"),
            "module": mutation.get("module"),
            "killed": mutation.get("killed"),
            "survived": mutation.get("survived"),
            "survivors": mutation.get("survivors", []),
            "note": mutation.get("note", ""),
        },
        "not_measured": NOT_MEASURED,
        "honest_limits": HONEST_LIMITS,
    }


def render_scorecard_markdown(card: dict) -> str:
    """One-page Markdown rendering of a scorecard dict."""
    lines: list[str] = []
    lines.append(f"# Release scorecard — {card['date']}")
    lines.append("")
    lines.append(
        f"Corpus v{card['corpus_version']} · retrieval set v{card['retrieval_set_version']} · "
        f"scorecard v{card['scorecard_version']}"
    )
    lines.append("")
    ref = card["reference_tasks"]
    lines.append("## Reference tasks")
    lines.append("")
    lines.append(f"- Success: **{ref['summary']}**")
    if ref.get("total_seconds") is not None:
        lines.append(f"- Wall time: {ref['total_seconds']:.1f}s for {ref['trials']} runs")
    lines.append(f"- Cost: Rp{ref.get('total_cost_rupiah', 0.0)} (no model calls)")
    lines.append("")
    lines.append("## Retrieval (recall@5 / answer accuracy, 50 internal questions)")
    lines.append("")
    for name, scores in card["retrieval"]["retrievers"].items():
        lines.append(
            f"- {name}: recall@5 **{scores['recall_at_5']:.2f}** "
            f"({scores['hits']}/{scores['questions']}), "
            f"answer accuracy **{scores['answer_accuracy']:.2f}** "
            f"({scores['answered']}/{scores['questions']})"
        )
    lines.append("- Public questions: 50 reserved, 0 live (license not confirmed)")
    lines.append("")
    attack = card["attack"]
    lines.append("## Injection corpus")
    lines.append("")
    lines.append(
        f"- Live payloads: {attack['live_payloads']} (+{attack['empty_slots']} empty slots)"
    )
    lines.append(f"- Attack success rate: **{attack['summary']}**")
    lines.append(
        f"- Rule-of-three 95% upper bound: **{attack['rule_of_three_upper_bound_95']:.3f}**"
    )
    lines.append(
        f"- Blocked: {attack['blocked']} · held at approval: {attack['held_at_approval']} (not a defense success)"
    )
    for goal, counts in sorted(attack.get("by_goal", {}).items(), key=lambda item: int(item[0])):
        lines.append(
            f"- Goal {goal}: {counts.get('success', 0)} success / {sum(counts.values())} live"
        )
    scenario = attack.get("broad_always_share_scenario", {})
    if scenario:
        lines.append(
            f"- Broad 'always' share grant (stress scenario): "
            f"{scenario.get('success', 0)} success / {scenario.get('live', 0)} live"
        )
    lines.append("")
    benign = card.get("scanner_false_positives", {})
    if benign:
        lines.append("## Scanner false positives (normal Indonesian content)")
        lines.append("")
        lines.append(
            f"- False-positive rate: **{benign.get('false_positive_rate', 0.0):.2f}** "
            f"({benign.get('flagged', 0)}/{benign.get('total', 0)})"
        )
        lines.append(
            f"- Tricky subset: **{benign.get('tricky_false_positive_rate', 0.0):.2f}** "
            f"({benign.get('tricky_flagged', 0)}/{benign.get('tricky_total', 0)})"
        )
        lines.append("")
    utility = card.get("utility_under_attack", {})
    if utility:
        lines.append("## Utility under attack (benign requests still complete)")
        lines.append("")
        lines.append(
            f"- Utility rate: **{utility.get('utility_rate', 0.0):.2f}** "
            f"({utility.get('completed', 0)}/{utility.get('total', 0)})"
        )
        lines.append("")
    gold = card.get("gold_sets", {})
    if gold:
        lines.append("## Gold sets (30 cases each)")
        lines.append("")
        entity = gold.get("entity_identity", {})
        lines.append(
            f"- Entity identity: precision **{entity.get('precision', 0.0):.2f}**, "
            f"recall **{entity.get('recall', 0.0):.2f}**"
        )
        quoted = gold.get("quoted_numbers", {})
        lines.append(
            f"- Quoted numbers: accuracy **{quoted.get('accuracy', 0.0):.2f}** "
            f"({quoted.get('found', 0)}/{quoted.get('total', 0)})"
        )
        orphans = gold.get("orphan_numbers", {})
        lines.append(
            f"- Orphan numbers: hold rate **{orphans.get('hold_rate', 0.0):.2f}**, "
            f"wrong-hold rate **{orphans.get('wrong_hold_rate', 0.0):.2f}**"
        )
        lines.append("")
    mutation = card.get("mutation", {})
    lines.append("## Mutation testing")
    lines.append("")
    if mutation.get("measured"):
        lines.append(
            f"- {mutation.get('module')}: score **{mutation.get('score', 0.0):.3f}** "
            f"({mutation.get('killed', 0)} killed, {mutation.get('survived', 0)} survived)"
        )
        for survivor in mutation.get("survivors", []):
            lines.append(f"  - SURVIVED {survivor.get('id')}: {survivor.get('description')}")
    else:
        lines.append(f"- Not measured in this run. {mutation.get('note', '')}")
    lines.append("")
    lines.append("## Not measured")
    lines.append("")
    for item in card.get("not_measured", []):
        lines.append(f"- {item['metric']}: {item['reason']}")
    lines.append("")
    lines.append("## Honest limits")
    lines.append("")
    for limit in card.get("honest_limits", []):
        lines.append(f"- {limit}")
    lines.append("")
    return "\n".join(lines)


def run_measurements(*, mutation_report: dict | None = None) -> dict:
    """Run every offline measurement and assemble the scorecard."""
    runs = run_reference_tasks(repetitions=3)
    summary = summarize_runs(runs)
    retrieval = compare_retrievers(top_k=5)
    corpus = load_attack_corpus()
    harness = AttackHarness()
    attack_summary = harness.run_corpus(corpus.live)
    broad_harness = AttackHarness(broad_always_share=True)
    broad_summary = broad_harness.run_corpus(corpus.live)
    benign = load_benign_corpus()
    by_goal: dict[str, dict[str, int]] = {}
    for goal, counts in sorted(attack_summary.by_goal.items()):
        by_goal[str(goal)] = counts
    mutation = mutation_report or {
        "measured": False,
        "note": "Run mutmut (or python -m puspa.eval.mutation) on the security "
        "packages and pass the report with --mutation-report.",
    }
    inputs = {
        "reference_tasks": summary,
        "retrieval": retrieval,
        "attack": {
            "live": attack_summary.total_live,
            "empty_slots": len(corpus.slots),
            "blocked": attack_summary.blocked,
            "held_at_approval": attack_summary.held_at_approval,
            "success": attack_summary.success,
            "by_goal": by_goal,
            "broad_always_share_scenario": {
                "live": broad_summary.total_live,
                "blocked": broad_summary.blocked,
                "held_at_approval": broad_summary.held_at_approval,
                "success": broad_summary.success,
            },
        },
        "benign": measure_scanner_false_positives(benign),
        "utility": measure_utility_under_attack(benign),
        "gold": evaluate_all_gold_sets(),
        "mutation": mutation,
    }
    return build_scorecard(inputs)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the E-16 release scorecard")
    parser.add_argument(
        "--output-dir", default="eval-results", help="where to write scorecard.json/.md"
    )
    parser.add_argument("--mutation-report", default=None, help="mutation result JSON to include")
    args = parser.parse_args(argv)
    mutation_report = None
    if args.mutation_report:
        mutation_report = json.loads(Path(args.mutation_report).read_text(encoding="utf-8"))
        mutation_report["measured"] = True
    card = run_measurements(mutation_report=mutation_report)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "scorecard.json").write_text(json.dumps(card, indent=2), encoding="utf-8")
    (out_dir / "scorecard.md").write_text(render_scorecard_markdown(card), encoding="utf-8")
    print(render_scorecard_markdown(card))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
