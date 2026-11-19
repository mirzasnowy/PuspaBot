"""Turn-loop entry point for the Check Before You Buy slice.

``handle_check_before_buy`` is what the turn loop's intercepted
``check_before_buy`` tool calls. It takes the forwarded text,
answers from recorded data only, and — when a DSN is supplied —
writes the outcome through the existing evidence + ledger path.
It never sends a message and never moves money: there is no send
or pay path in this slice at all.
"""

from __future__ import annotations

from puspa.local_data.rates import RateStore

from .answer import check_before_buy
from .outcome import record_outcome
from .records import RecordStore


def handle_check_before_buy(
    arguments: dict,
    *,
    store: RecordStore | None = None,
    user_id: str | None = None,
    agent_id: str = "puspa",
    task_id: str | None = None,
    dsn: str | None = None,
    mode: str = "recorded",
    shared_dsn: str | None = None,
    rate_store: RateStore | None = None,
) -> dict:
    """Run one check and return the structured answer plus its reply.

    ``arguments`` carries ``text`` (the forwarded input) and
    optionally ``product_key``. When ``dsn`` is given the outcome
    is also recorded; without it the answer is computed only.
    ``shared_dsn`` (ticket B3) consults the shared graphs for
    the seller signal and the shared fair-price fallback.
    ``rate_store`` (ticket B5) supplies the shipping rates for
    the lane named in the input; it defaults to the seeded
    sample rates.
    """
    text = arguments.get("text") or arguments.get("input") or ""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("check_before_buy needs non-empty 'text'")
    answer = check_before_buy(
        text,
        store=store or RecordStore(),
        product_key=arguments.get("product_key"),
        mode=arguments.get("mode", mode),
        shared_dsn=shared_dsn,
        rate_store=rate_store,
    )
    recorded: dict | None = None
    if dsn is not None and user_id is not None:
        recorded = record_outcome(
            answer,
            user_id=user_id,
            agent_id=agent_id,
            task_id=task_id or "check-before-buy",
            dsn=dsn,
        )
    return {
        "answer": answer.to_dict(),
        "reply": answer.text,
        "evidence_ids": (recorded or {}).get("evidence_ids", []),
        "recorded": recorded is not None,
    }
