"""Write a Check Before You Buy outcome through the existing paths.

No second ledger is invented here. Price observations the answer
rests on become evidence rows through the existing evidence path
(``puspa.evidence``: record, independent re-read against the
recorded store, save), and the whole answer is written as one
``evidence_recorded`` event on the existing append-only ledger
(``puspa.ledger``). Seller and account findings have no listing URL
of their own, so they travel inside that ledger event's content
instead of as fabricated evidence rows.
"""

from __future__ import annotations

import os

from puspa.evidence.evidence import record_evidence, save_evidence
from puspa.evidence.proof import answer_proof
from puspa.evidence.verifier import verify
from puspa.ledger import Ledger

from .answer import CheckAnswer

DEFAULT_DSN = "dbname=puspa_test user=root host=/var/run/postgresql"


def record_outcome(
    answer: CheckAnswer,
    *,
    user_id: str,
    agent_id: str,
    task_id: str,
    dsn: str | None = None,
    reader_id: str = "check-before-buy",
) -> dict:
    """Persist one answer: evidence rows + one ledger event.

    Returns ``{"evidence_ids": [...], "ledger_event": {...},
    "proof": {...}}``. The ``proof`` block is the B4 proof
    surface on this answer: its evidence entries, the receipt
    for the ledger event it was recorded as, and — when the
    answer could not verify anything — the unverified sections
    named, never silently shown as proven.
    """
    dsn = dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN)
    by_url = {obs.source_url: obs for obs in answer.observations}
    evidence_ids: list[str] = []
    for observation in answer.observations:
        if not observation.source_url:
            continue
        evidence = record_evidence(
            task_id=task_id,
            claim=f"fair price of {answer.product_key or 'product'} at {observation.source}",
            value=observation.price,
            unit="Rp",
            source_url=observation.source_url,
            text_excerpt=observation.excerpt or str(observation.price),
            reader_id=reader_id,
        )
        evidence = verify(
            evidence,
            reread=lambda url, _by_url=by_url: (
                {"value": _by_url[url].price} if url in _by_url else None
            ),
        )
        save_evidence(dsn, evidence, user_id, task_id)
        evidence_ids.append(evidence.evidence_id)
    answer.fair_price.evidence_ids = evidence_ids

    content = {
        "slice": "check_before_buy",
        "input_kind": answer.input_kind,
        "product_key": answer.product_key,
        "fair_price": answer.fair_price.to_dict(),
        "seller": answer.seller.to_dict(),
        "account": answer.account.to_dict(),
        "advice": answer.advice,
        "advice_reasons": list(answer.advice_reasons),
        "cannot_verify": list(answer.cannot_verify),
        "live_execution_held": answer.live_execution_held,
    }
    ledger = Ledger(dsn)
    event = ledger.append_event(
        task_id=task_id,
        agent_id=agent_id,
        event_type="evidence_recorded",
        content=content,
    )
    return {
        "evidence_ids": evidence_ids,
        "ledger_event": event,
        "proof": answer_proof(answer, event, ledger),
    }
