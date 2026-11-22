"""Automatic ontology repair (Kosmos K17, V2-22).

K9 (``ontology_review``) only *flags* what does not hold together
in the profile facts plus causal hypotheses: contradictory facts,
proposed facts that were never confirmed, hypotheses whose evidence
pulls both ways, and inferred facts dominated by one value on thin
evidence. This module turns those findings into automatic repairs:

- a stale proposal is rejected (low risk: it was never confirmed,
  and the row stays in history, so the rejection is reversible);
- a contradictory key is resolved by keeping the newest active
  value and superseding the rest (ontology change: shadow first);
- a contested hypothesis is retired when the evidence against it
  is at least as strong as the evidence for it, and the weaker of
  two competing claims for the same cause and condition is retired
  (ontology change: shadow first);
- a sampling-bias finding is never repaired automatically: it
  needs counter-evidence, which no repair can invent, so it is
  recorded as deferred.

Every applied repair is one ledger event (``task_completed`` with
``job == "ontology_repair"``) carrying the full undo payload, so
any repair can be reversed with :func:`undo_repair`. Nothing is
deleted: facts move between statuses, hypotheses move to a
terminal status, and history stays readable.

Shadow rule (the issue's done criterion): ontology-changing
repairs are first applied to an in-memory copy of the ontology
and scored against the current one with :func:`score_ontology`.
They are promoted to the real store only when the shadow score
matches or beats the current score. Low-risk repairs apply
directly.

Mechanisms adapted from the NVIDIA auto-ontology framework
(Apache-2.0, ``auto-ontology-server`` 1.3.0, studied from a local
clone, rewritten as Puspa code, never copied):

- ``semantic/`` (ontology building) -> :func:`compile_ontology`:
  facts and hypotheses compiled into one deterministic model of
  entities (fact keys with their active values and evidence),
  relations (cause -> effect claims with support counts) and
  open proposals.
- ``catalog/`` (model + parser) -> :func:`plan_repairs`: the K9
  findings are parsed into typed repair operations with an
  explicit risk class and an undo payload, the same way a
  catalog parses raw sources into a typed model.
- ``retrieval/`` (deterministic query, entity coverage) ->
  :func:`score_ontology`: a deterministic score over the compiled
  model. Entity coverage is the share of keys that resolve to
  exactly one active value; hypothesis health is the share of
  live hypotheses that are supported and uncontested; each K9
  finding kind subtracts a fixed penalty. No model call, no
  sampling: the same ontology always scores the same.
- ``mcp/`` (exposure) -> :data:`REPAIR_TOOLS`: the repair
  operations exposed as tool definitions (name, description,
  parameters) so the MCP server can register them later.

NOT taken from that framework: the FastAPI server, the NIM
endpoints, the enterprise connectors (Snowflake, HeavyDB) and
the frontend. See ``REUSE.md`` (Kosmos Phase 2, V2-22) and
``NOTICE.md`` for the attribution.
"""

from __future__ import annotations

import copy
import uuid
from datetime import UTC, datetime

import psycopg
from psycopg.rows import dict_row

from . import profile
from .ontology_review import (
    STALE_FACT_DAYS,
    SYSTEM_AGENT_ID,
    resolve_agent_id,
    review_ontology,
)
from .schema import resolve_dsn

#: Repair operations, in the order the planner emits them.
OP_REJECT_STALE_PROPOSAL = "reject_stale_proposal"
OP_SUPERSEDE_CONFLICT = "supersede_conflict"
OP_RETIRE_HYPOTHESIS = "retire_hypothesis"
OP_NEEDS_EVIDENCE = "needs_evidence"

#: Risk classes. ``low`` applies directly; ``shadow`` applies
#: only when the shadow run matches or beats the current
#: ontology; ``deferred`` never applies (no automatic fix exists).
RISK_LOW = "low"
RISK_SHADOW = "shadow"
RISK_DEFERRED = "deferred"

#: Score weights. Coverage and hypothesis health reward a
#: coherent ontology; each finding kind subtracts a fixed
#: penalty, so any repair that removes a finding without
#: creating a new one raises the score.
WEIGHT_COVERAGE = 100.0
WEIGHT_HYPOTHESIS_HEALTH = 10.0
PENALTY_CONTRADICTION = 25.0
PENALTY_STALE = 5.0
PENALTY_CONTESTED = 15.0
PENALTY_THIN_EVIDENCE = 5.0

#: Puspa's causal statuses have no "retired" value, so a
#: hypothesis retired by a repair is recorded as
#: "contradicted": the closest terminal status, and the repair
#: ledger event carries the true reason. Undo restores "active".
RETIRED_STATUS = "contradicted"


def _require_aware(moment, name):
    if moment is not None and moment.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")
    return moment


def _as_datetime(value):
    """A datetime, or an ISO string from a serialised fact/hypothesis dict."""
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    raise ValueError(f"not a datetime or ISO string: {value!r}")


def _as_iso(value):
    """An ISO string for a datetime (or a string already), else None."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return value.isoformat()


def _is_live_hypothesis(hypothesis, now):
    """A hypothesis is live when its status is active and its TTL has not passed."""
    if hypothesis.get("status") != "active":
        return False
    expires_at = _as_datetime(hypothesis.get("expires_at"))
    return not (expires_at is not None and expires_at <= now)


def _fact_sort_key(fact):
    """Oldest first; the id breaks ties so the order is deterministic."""
    created_at = _as_datetime(fact.get("created_at")) or datetime.min.replace(tzinfo=UTC)
    return (created_at, str(fact.get("id")))


# -- ontology model (catalog) -------------------------------------------


def compile_ontology(*, facts, hypotheses, now=None):
    """Compile profile facts plus causal hypotheses into one ontology model.

    ``facts`` are profile-fact dicts in the ``profile.list_facts``
    shape; ``hypotheses`` are causal-hypothesis dicts in the
    ``CausalLedger.list_all`` shape. Returns a dict with:

    - ``entities``: one entry per fact key, with the sorted active
      values, their sources, and the distinct evidence event ids
      behind them;
    - ``relations``: one entry per live hypothesis (cause, effect,
      condition, supporting/contradicting counts, strength);
    - ``proposals``: the proposed (unconfirmed) facts, oldest first.

    Pure function: it reads nothing and writes nothing.
    """
    now = _require_aware(now, "now") or datetime.now(UTC)
    entities: dict[str, dict] = {}
    proposals = []
    for fact in facts or []:
        key = str(fact.get("key") or "")
        if fact.get("status") == "proposed":
            proposals.append(
                {
                    "fact_id": str(fact.get("id")),
                    "key": key,
                    "value": str(fact.get("value")),
                    "created_at": _as_iso(_as_datetime(fact.get("created_at"))),
                }
            )
            continue
        if fact.get("status") != "active":
            continue
        entry = entities.setdefault(
            key, {"values": set(), "sources": set(), "evidence_event_ids": set()}
        )
        entry["values"].add(str(fact.get("value")))
        entry["sources"].add(str(fact.get("source") or ""))
        if fact.get("event_id"):
            entry["evidence_event_ids"].add(str(fact.get("event_id")))
    relations = []
    for hypothesis in hypotheses or []:
        if not _is_live_hypothesis(hypothesis, now):
            continue
        relations.append(
            {
                "hypothesis_id": str(hypothesis.get("id")),
                "cause": str(hypothesis.get("cause")),
                "effect": str(hypothesis.get("effect")),
                "condition": str(hypothesis.get("condition") or ""),
                "supporting": int(hypothesis.get("supporting") or 0),
                "contradicting": int(hypothesis.get("contradicting") or 0),
                "strength": float(hypothesis.get("strength") or 0.0),
                "created_at": _as_iso(_as_datetime(hypothesis.get("created_at"))),
            }
        )
    relations.sort(key=lambda r: r["hypothesis_id"])
    proposals.sort(key=lambda p: (p["created_at"] or "", p["fact_id"]))
    return {
        "entities": {
            key: {
                "values": sorted(entry["values"]),
                "sources": sorted(entry["sources"]),
                "evidence_event_ids": sorted(entry["evidence_event_ids"]),
            }
            for key, entry in sorted(entities.items())
        },
        "relations": relations,
        "proposals": proposals,
    }


# -- deterministic scoring (retrieval / entity coverage) ----------------


def score_ontology(*, facts, hypotheses, now=None):
    """Score one ontology deterministically; higher is better.

    Entity coverage is the share of keys that resolve to exactly
    one active value (1.0 when there are no keys). Hypothesis
    health is the share of live hypotheses that are supported and
    uncontested (1.0 when there are none live). Each K9 finding
    kind subtracts a fixed penalty (see the ``PENALTY_*`` weights
    above), counted through :func:`review_ontology` so the
    finding definitions stay in one place.

    Returns ``{"score", "entity_coverage", "hypothesis_health",
    "counts", "keys", "live_hypotheses"}``. Pure function.
    """
    now = _require_aware(now, "now") or datetime.now(UTC)
    model = compile_ontology(facts=facts, hypotheses=hypotheses, now=now)
    findings = review_ontology(facts=facts, hypotheses=hypotheses, now=now)
    counts = {
        "contradictory_fact": 0,
        "stale_fact": 0,
        "hidden_variable": 0,
        "sampling_bias": 0,
    }
    for finding in findings:
        counts[finding["kind"]] += 1
    counts["total"] = len(findings)

    keys = len(model["entities"])
    resolved = sum(1 for entry in model["entities"].values() if len(entry["values"]) == 1)
    coverage = (resolved / keys) if keys else 1.0
    live = model["relations"]
    supported = sum(
        1 for relation in live if relation["supporting"] >= 1 and relation["contradicting"] == 0
    )
    health = (supported / len(live)) if live else 1.0
    score = (
        WEIGHT_COVERAGE * coverage
        + WEIGHT_HYPOTHESIS_HEALTH * health
        - PENALTY_CONTRADICTION * counts["contradictory_fact"]
        - PENALTY_STALE * counts["stale_fact"]
        - PENALTY_CONTESTED * counts["hidden_variable"]
        - PENALTY_THIN_EVIDENCE * counts["sampling_bias"]
    )
    return {
        "score": round(score, 3),
        "entity_coverage": round(coverage, 3),
        "hypothesis_health": round(health, 3),
        "counts": counts,
        "keys": keys,
        "live_hypotheses": len(live),
    }


def should_promote(current_score, shadow_score):
    """True when the shadow run matches or beats the current ontology."""
    return shadow_score >= current_score


# -- repair planning (catalog parser) -----------------------------------


def _new_repair(op, kind, risk, summary, target, undo, finding_summary):
    return {
        "repair_id": f"repair-{uuid.uuid4().hex[:12]}",
        "op": op,
        "kind": kind,
        "risk": risk,
        "summary": summary,
        "target": target,
        "undo": undo,
        "finding": finding_summary,
    }


def _fact_undo(fact):
    return {
        "fact_id": str(fact.get("id")),
        "status": str(fact.get("status")),
        "valid_until": _as_iso(_as_datetime(fact.get("valid_until"))),
    }


def _hypothesis_undo(hypothesis):
    return {
        "hypothesis_id": str(hypothesis.get("id")),
        "status": str(hypothesis.get("status")),
        "valid_to": _as_iso(_as_datetime(hypothesis.get("valid_to"))),
    }


def plan_repairs(*, facts, hypotheses, now=None):
    """Parse K9 findings into repair operations.

    One repair per finding, in the findings' deterministic order
    (kind, summary). A repair carries its risk class, its target,
    and the undo payload that restores the prior state. Repairs
    with risk ``deferred`` change nothing: the finding has no
    automatic fix. Pure function.
    """
    now = _require_aware(now, "now") or datetime.now(UTC)
    findings = review_ontology(facts=facts, hypotheses=hypotheses, now=now)
    facts_by_id = {str(f.get("id")): f for f in (facts or [])}
    hyps_by_id = {str(h.get("id")): h for h in (hypotheses or [])}
    repairs = []

    for finding in findings:
        kind = finding["kind"]
        evidence = finding.get("evidence") or []
        if kind == "stale_fact":
            fact = facts_by_id.get(str(evidence[0].get("fact_id"))) if evidence else None
            if fact is None:
                continue
            repairs.append(
                _new_repair(
                    OP_REJECT_STALE_PROPOSAL,
                    kind,
                    RISK_LOW,
                    f"reject stale proposal for key {str(fact.get('key'))!r}: "
                    "never confirmed in "
                    f"{STALE_FACT_DAYS}+ days",
                    {"fact_id": str(fact.get("id")), "key": str(fact.get("key"))},
                    {"facts": [_fact_undo(fact)], "hypotheses": []},
                    finding["summary"],
                )
            )
        elif kind == "contradictory_fact":
            group = [
                facts_by_id[str(e.get("fact_id"))]
                for e in evidence
                if str(e.get("fact_id")) in facts_by_id
            ]
            if len(group) < 2:
                continue
            group.sort(key=_fact_sort_key)
            keep = group[-1]
            supersede = group[:-1]
            repairs.append(
                _new_repair(
                    OP_SUPERSEDE_CONFLICT,
                    kind,
                    RISK_SHADOW,
                    f"keep newest active value for key {str(keep.get('key'))!r} "
                    f"({str(keep.get('value'))!r}) and supersede "
                    f"{len(supersede)} older value(s)",
                    {
                        "key": str(keep.get("key")),
                        "keep_fact_id": str(keep.get("id")),
                        "supersede_fact_ids": [str(f.get("id")) for f in supersede],
                    },
                    {
                        "facts": [_fact_undo(f) for f in supersede],
                        "hypotheses": [],
                    },
                    finding["summary"],
                )
            )
        elif kind == "hidden_variable":
            first = evidence[0] if evidence else {}
            if "supporting" in first and "contradicting" in first:
                # One hypothesis whose evidence pulls both ways.
                hypothesis = hyps_by_id.get(str(first.get("hypothesis_id")))
                if hypothesis is None:
                    continue
                supporting = int(first.get("supporting") or 0)
                contradicting = int(first.get("contradicting") or 0)
                if contradicting < supporting:
                    repairs.append(
                        _new_repair(
                            OP_NEEDS_EVIDENCE,
                            kind,
                            RISK_DEFERRED,
                            f"hypothesis {str(hypothesis.get('id'))!r} is mostly "
                            "supported with some contradicting observations: "
                            "retiring it would drop a mostly-true claim, so it "
                            "needs a refined condition, not an automatic repair",
                            {"hypothesis_id": str(hypothesis.get("id"))},
                            {"facts": [], "hypotheses": []},
                            finding["summary"],
                        )
                    )
                else:
                    repairs.append(
                        _new_repair(
                            OP_RETIRE_HYPOTHESIS,
                            kind,
                            RISK_SHADOW,
                            f"retire hypothesis {str(hypothesis.get('id'))!r}: "
                            f"contradicting ({contradicting}) >= supporting "
                            f"({supporting}), so the stated condition does not "
                            "determine the effect",
                            {"hypothesis_id": str(hypothesis.get("id"))},
                            {"facts": [], "hypotheses": [_hypothesis_undo(hypothesis)]},
                            finding["summary"],
                        )
                    )
            else:
                # Two live hypotheses, same cause and condition,
                # different effects: retire the weaker one.
                group = [
                    hyps_by_id[str(e.get("hypothesis_id"))]
                    for e in evidence
                    if str(e.get("hypothesis_id")) in hyps_by_id
                ]
                if len(group) < 2:
                    continue

                def _weakest_key(hypothesis):
                    created_at = _as_datetime(hypothesis.get("created_at")) or datetime.min.replace(
                        tzinfo=UTC
                    )
                    return (
                        int(hypothesis.get("supporting") or 0),
                        float(hypothesis.get("strength") or 0.0),
                        created_at,
                        str(hypothesis.get("id")),
                    )

                group.sort(key=_weakest_key)
                weaker = group[0]
                repairs.append(
                    _new_repair(
                        OP_RETIRE_HYPOTHESIS,
                        kind,
                        RISK_SHADOW,
                        f"retire weaker hypothesis {str(weaker.get('id'))!r} "
                        f"({weaker.get('cause')!s} -> {weaker.get('effect')!s}): "
                        "a competing claim for the same cause and condition "
                        "has stronger support",
                        {"hypothesis_id": str(weaker.get("id"))},
                        {"facts": [], "hypotheses": [_hypothesis_undo(weaker)]},
                        finding["summary"],
                    )
                )
        elif kind == "sampling_bias":
            repairs.append(
                _new_repair(
                    OP_NEEDS_EVIDENCE,
                    kind,
                    RISK_DEFERRED,
                    "inferred facts are dominated by one value on thin "
                    "evidence: no repair can invent counter-evidence, so "
                    "this stays a finding until new observations arrive",
                    {"key": str(evidence[0].get("key")) if evidence else ""},
                    {"facts": [], "hypotheses": []},
                    finding["summary"],
                )
            )
    return repairs


# -- shadow run ---------------------------------------------------------


def apply_repairs_to_copy(facts, hypotheses, repairs, *, now=None):
    """Apply repairs to in-memory copies; the inputs are untouched.

    Returns ``(facts, hypotheses)`` as new lists of new dicts with
    the repair status changes applied. Deferred repairs change
    nothing. Pure function: this is what the shadow run scores.
    """
    now = _require_aware(now, "now") or datetime.now(UTC)
    new_facts = [copy.deepcopy(f) for f in (facts or [])]
    new_hyps = [copy.deepcopy(h) for h in (hypotheses or [])]
    facts_by_id = {str(f.get("id")): f for f in new_facts}
    hyps_by_id = {str(h.get("id")): h for h in new_hyps}
    for repair in repairs or []:
        op = repair.get("op")
        target = repair.get("target") or {}
        if op == OP_REJECT_STALE_PROPOSAL:
            fact = facts_by_id.get(str(target.get("fact_id")))
            if fact is not None and fact.get("status") == "proposed":
                fact["status"] = "rejected"
                fact["valid_until"] = now.isoformat()
        elif op == OP_SUPERSEDE_CONFLICT:
            for fact_id in target.get("supersede_fact_ids") or []:
                fact = facts_by_id.get(str(fact_id))
                if fact is not None and fact.get("status") == "active":
                    fact["status"] = "replaced"
                    fact["valid_until"] = now.isoformat()
        elif op == OP_RETIRE_HYPOTHESIS:
            hypothesis = hyps_by_id.get(str(target.get("hypothesis_id")))
            if hypothesis is not None and hypothesis.get("status") == "active":
                hypothesis["status"] = RETIRED_STATUS
                hypothesis["valid_to"] = now.isoformat()
    return new_facts, new_hyps


def run_shadow(*, facts, hypotheses, repairs, now=None):
    """Score the current ontology against the repaired (shadow) one.

    Only appliable repairs (risk ``low`` or ``shadow``) are applied
    to the shadow copy. Returns ``{"current", "shadow", "delta",
    "promote", "repairs_considered"}`` where ``promote`` is True
    when the shadow score matches or beats the current score.
    Pure function: no database writes.
    """
    now = _require_aware(now, "now") or datetime.now(UTC)
    appliable = [r for r in (repairs or []) if r.get("risk") in (RISK_LOW, RISK_SHADOW)]
    current = score_ontology(facts=facts, hypotheses=hypotheses, now=now)
    shadow_facts, shadow_hyps = apply_repairs_to_copy(facts, hypotheses, appliable, now=now)
    shadow = score_ontology(facts=shadow_facts, hypotheses=shadow_hyps, now=now)
    return {
        "current": current,
        "shadow": shadow,
        "delta": round(shadow["score"] - current["score"], 3),
        "promote": should_promote(current["score"], shadow["score"]),
        "repairs_considered": len(appliable),
    }


# -- database application -----------------------------------------------


def _connect(dsn=None):
    return psycopg.connect(resolve_dsn(dsn), row_factory=dict_row)


def _db_supersede_facts(user_id, fact_ids, *, dsn, now):
    """Mark active facts replaced (history kept); return how many changed."""
    if not fact_ids:
        return 0
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE profile_facts SET status = 'replaced', valid_until = %s "
                "WHERE user_id = %s AND id = ANY(%s::uuid[]) AND status = 'active'",
                (now, str(user_id), [str(f) for f in fact_ids]),
            )
            changed = cur.rowcount
        conn.commit()
    return changed


def _db_restore_facts(user_id, fact_undos, *, dsn):
    """Restore facts to their prior status (undo); return how many changed."""
    changed = 0
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            for undo in fact_undos or []:
                valid_until = _as_datetime(undo.get("valid_until"))
                cur.execute(
                    "UPDATE profile_facts SET status = %s, valid_until = %s "
                    "WHERE user_id = %s AND id = %s",
                    (str(undo["status"]), valid_until, str(user_id), str(undo["fact_id"])),
                )
                changed += cur.rowcount
        conn.commit()
    return changed


def _db_retire_hypothesis(user_id, agent_id, hypothesis_id, *, dsn, now):
    """Retire one hypothesis (status -> terminal); return True when found."""
    from puspa.world.store import load_causal_ledger, save_causal_hypotheses

    ledger = load_causal_ledger(str(user_id), str(agent_id), dsn=dsn)
    if ledger is None:
        return False
    data = ledger.to_dict()
    found = False
    for row in data.get("hypotheses") or []:
        if str(row.get("id")) == str(hypothesis_id) and row.get("status") == "active":
            row["status"] = RETIRED_STATUS
            row["valid_to"] = now.isoformat()
            found = True
    if not found:
        return False
    from puspa.world.causal import CausalLedger

    save_causal_hypotheses(str(user_id), str(agent_id), CausalLedger.from_dict(data), dsn=dsn)
    return True


def _db_restore_hypothesis(user_id, agent_id, hyp_undo, *, dsn):
    """Restore one hypothesis to its prior status (undo); return True when found."""
    from puspa.world.store import load_causal_ledger, save_causal_hypotheses

    ledger = load_causal_ledger(str(user_id), str(agent_id), dsn=dsn)
    if ledger is None:
        return False
    data = ledger.to_dict()
    found = False
    for row in data.get("hypotheses") or []:
        if str(row.get("id")) == str(hyp_undo["hypothesis_id"]):
            row["status"] = str(hyp_undo["status"])
            row["valid_to"] = _as_iso(_as_datetime(hyp_undo.get("valid_to")))
            found = True
    if not found:
        return False
    from puspa.world.causal import CausalLedger

    save_causal_hypotheses(str(user_id), str(agent_id), CausalLedger.from_dict(data), dsn=dsn)
    return True


def _apply_repair_to_db(user_id, repair, *, dsn, agent_id, now):
    """Apply one planned repair to the store; return True when it changed state."""
    op = repair["op"]
    target = repair.get("target") or {}
    if op == OP_REJECT_STALE_PROPOSAL:
        try:
            profile.reject_proposal(str(target["fact_id"]), str(user_id), dsn=dsn)
        except KeyError:
            return False
        return True
    if op == OP_SUPERSEDE_CONFLICT:
        return (
            _db_supersede_facts(user_id, target.get("supersede_fact_ids") or [], dsn=dsn, now=now)
            > 0
        )
    if op == OP_RETIRE_HYPOTHESIS:
        return _db_retire_hypothesis(
            user_id, agent_id, str(target["hypothesis_id"]), dsn=dsn, now=now
        )
    return False


def _load_inputs(user_id, *, dsn, facts, hypotheses, agent_id):
    """Load facts + hypotheses unless the caller passed them in."""
    if facts is None:
        facts = profile.list_facts(str(user_id), dsn=dsn) if dsn is not None else []
    if hypotheses is None:
        hypotheses = []
        if dsn is not None:
            resolved = agent_id if agent_id is not None else resolve_agent_id(user_id, dsn=dsn)
            if resolved is not None:
                from puspa.world.store import load_causal_ledger

                loaded = load_causal_ledger(str(user_id), resolved, dsn=dsn)
                if loaded is not None:
                    hypotheses = loaded.list_all(include_terminal=True)
    return facts, hypotheses


def run_ontology_evolution(
    user_id,
    *,
    dsn=None,
    facts=None,
    hypotheses=None,
    ledger=None,
    agent_id=None,
    task_id=None,
    now=None,
):
    """Plan K9 repairs, shadow-check the ontology changes, apply, and record.

    Loads the profile facts and causal hypotheses for the user
    unless they are passed in; plans repairs with
    :func:`plan_repairs`; runs the shadow comparison with
    :func:`run_shadow`. Low-risk repairs apply directly;
    shadow-risk repairs apply only when the shadow run matches
    or beats the current ontology; deferred repairs never apply.

    Every applied repair becomes one ``task_completed`` ledger
    event with ``job == "ontology_repair"`` carrying the repair
    (including its undo payload) and the shadow comparison. When
    nothing applies, one event records the run and why nothing
    applied. With neither a ledger nor a dsn the run is a dry
    run: planning and shadow still happen, nothing is applied
    or recorded.

    Returns ``{"status", "findings", "counts", "shadow",
    "applied", "deferred", "skipped", "ledger_event_ids"}``.
    ``ledger_event_ids`` is empty on a dry run.
    """
    now = _require_aware(now, "now") or datetime.now(UTC)
    facts, hypotheses = _load_inputs(
        user_id, dsn=dsn, facts=facts, hypotheses=hypotheses, agent_id=agent_id
    )
    findings = review_ontology(facts=facts, hypotheses=hypotheses, now=now)
    counts = {
        "contradictory_fact": 0,
        "stale_fact": 0,
        "hidden_variable": 0,
        "sampling_bias": 0,
    }
    for finding in findings:
        counts[finding["kind"]] += 1
    counts["total"] = len(findings)

    repairs = plan_repairs(facts=facts, hypotheses=hypotheses, now=now)
    shadow = run_shadow(facts=facts, hypotheses=hypotheses, repairs=repairs, now=now)
    deferred = [r for r in repairs if r["risk"] == RISK_DEFERRED]
    skipped = []
    applied = []

    dry_run = ledger is None and dsn is None
    if not dry_run:
        from puspa.ledger.ledger import Ledger

        writer = ledger if ledger is not None else Ledger(dsn)
        run_task_id = task_id or f"ontology-evolve-{uuid.uuid4().hex[:12]}"
        resolved_agent = agent_id
        if resolved_agent is None and dsn is not None:
            resolved_agent = resolve_agent_id(user_id, dsn=dsn)
        event_ids = []
        for repair in repairs:
            if repair["risk"] == RISK_DEFERRED:
                continue
            if repair["risk"] == RISK_SHADOW and not shadow["promote"]:
                skipped.append(repair)
                continue
            changed = _apply_repair_to_db(
                user_id, repair, dsn=dsn, agent_id=resolved_agent, now=now
            )
            if not changed:
                skipped.append(repair)
                continue
            applied.append(repair)
            event = writer.append_event(
                task_id=run_task_id,
                agent_id=SYSTEM_AGENT_ID,
                event_type="task_completed",
                content={
                    "job": "ontology_repair",
                    "user_id": str(user_id),
                    "repair": repair,
                    "shadow": {
                        "current_score": shadow["current"]["score"],
                        "shadow_score": shadow["shadow"]["score"],
                        "promote": shadow["promote"],
                    },
                },
            )
            event_ids.append(str(event["id"]))
        if not applied:
            event = writer.append_event(
                task_id=run_task_id,
                agent_id=SYSTEM_AGENT_ID,
                event_type="task_completed",
                content={
                    "job": "ontology_repair",
                    "user_id": str(user_id),
                    "repair": None,
                    "applied": [],
                    "deferred": deferred,
                    "skipped": skipped,
                    "shadow": {
                        "current_score": shadow["current"]["score"],
                        "shadow_score": shadow["shadow"]["score"],
                        "promote": shadow["promote"],
                    },
                    "counts": counts,
                },
            )
            event_ids.append(str(event["id"]))
    else:
        event_ids = []
        skipped = [r for r in repairs if r["risk"] == RISK_SHADOW and not shadow["promote"]]

    return {
        "status": "ok",
        "findings": findings,
        "counts": counts,
        "shadow": shadow,
        "applied": applied,
        "deferred": deferred,
        "skipped": skipped,
        "ledger_event_ids": event_ids,
    }


def undo_repair(
    user_id,
    repair,
    *,
    dsn=None,
    ledger=None,
    agent_id=None,
    task_id=None,
    now=None,
):
    """Reverse one applied repair and record the undo in the ledger.

    ``repair`` is a repair dict as returned in the ``applied``
    list of :func:`run_ontology_evolution` (or read back from
    its ledger event content). Fact statuses and hypothesis
    statuses return to the values in the repair's undo payload.
    Appends one ``task_completed`` event with
    ``job == "ontology_repair_undo"`` when a ledger or dsn is
    given; without either it still restores nothing, because
    there is no store to restore from — it returns
    ``{"status": "dry_run"}`` instead.

    Returns ``{"status": "ok", "repair_id", "restored",
    "ledger_event_id"}``; ``restored`` reports how many facts
    and hypotheses were put back.
    """
    now = _require_aware(now, "now") or datetime.now(UTC)
    if ledger is None and dsn is None:
        return {
            "status": "dry_run",
            "repair_id": repair.get("repair_id"),
            "restored": {"facts": 0, "hypotheses": 0},
            "ledger_event_id": None,
        }
    undo = repair.get("undo") or {}
    resolved_agent = agent_id
    if resolved_agent is None and dsn is not None:
        resolved_agent = resolve_agent_id(user_id, dsn=dsn)
    restored_facts = _db_restore_facts(user_id, undo.get("facts") or [], dsn=dsn)
    restored_hyps = 0
    for hyp_undo in undo.get("hypotheses") or []:
        if _db_restore_hypothesis(user_id, resolved_agent, hyp_undo, dsn=dsn):
            restored_hyps += 1

    from puspa.ledger.ledger import Ledger

    writer = ledger if ledger is not None else Ledger(dsn)
    run_task_id = task_id or f"ontology-undo-{uuid.uuid4().hex[:12]}"
    event = writer.append_event(
        task_id=run_task_id,
        agent_id=SYSTEM_AGENT_ID,
        event_type="task_completed",
        content={
            "job": "ontology_repair_undo",
            "user_id": str(user_id),
            "repair_id": repair.get("repair_id"),
            "op": repair.get("op"),
            "restored": {"facts": restored_facts, "hypotheses": restored_hyps},
        },
    )
    return {
        "status": "ok",
        "repair_id": repair.get("repair_id"),
        "restored": {"facts": restored_facts, "hypotheses": restored_hyps},
        "ledger_event_id": str(event["id"]),
    }


# -- tool exposure (mcp) --------------------------------------------------


REPAIR_TOOLS = [
    {
        "name": "ontology.reject_stale_proposal",
        "description": (
            "Reject a proposed profile fact that was never confirmed "
            f"in {STALE_FACT_DAYS}+ days. Low risk: the row stays in "
            "history and the rejection is reversible."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string"},
                "fact_id": {"type": "string"},
            },
            "required": ["user_id", "fact_id"],
        },
    },
    {
        "name": "ontology.supersede_conflict",
        "description": (
            "Resolve a contradictory fact key by keeping the newest "
            "active value and superseding the older ones. "
            "Ontology change: runs in shadow first and applies only "
            "when the shadow run matches or beats the current ontology."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string"},
                "key": {"type": "string"},
                "keep_fact_id": {"type": "string"},
                "supersede_fact_ids": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["user_id", "key", "keep_fact_id", "supersede_fact_ids"],
        },
    },
    {
        "name": "ontology.retire_hypothesis",
        "description": (
            "Retire a contested causal hypothesis whose evidence "
            "against it is at least as strong as the evidence for "
            "it, or the weaker of two competing claims for the same "
            "cause and condition. Ontology change: shadow first."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string"},
                "hypothesis_id": {"type": "string"},
            },
            "required": ["user_id", "hypothesis_id"],
        },
    },
    {
        "name": "ontology.undo_repair",
        "description": (
            "Reverse one applied ontology repair, restoring the "
            "prior fact and hypothesis statuses from the repair's "
            "ledger event, and record the undo in the ledger."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string"},
                "repair_id": {"type": "string"},
            },
            "required": ["user_id", "repair_id"],
        },
    },
]


def list_repair_tools():
    """The ontology repair operations as tool definitions.

    The MCP server registers these (the transport itself is
    V2-37 work, not this item); the definitions are the
    exposure surface, mirroring the upstream framework's
    ``mcp/`` package without its server.
    """
    return [dict(tool) for tool in REPAIR_TOOLS]
