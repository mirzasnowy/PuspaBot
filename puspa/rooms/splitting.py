"""Shared splitting / patungan for a room (B11).

Split a bill or a purchase among friends or family; the agent
tracks who paid and who still owes, reminds debtors, and prepares
settlement. A split belongs to a room (B6); it never builds a
second group, a second task board, or a second money rule.

Money
-----
All amounts are integer sen (minor units): 1 rupiah = 100 sen.
No float ever touches money in this module — totals, shares,
payments, and settlement amounts are all ``int``, and anything
else raises ``SplitError`` (a bool is not an int here, even
though ``isinstance(True, int)``). ``format_rupiah`` renders
sen as ``Rp150.000`` (``Rp150.000,50`` when sen remain).

Shares
------
- ``equal`` (default): the total is divided evenly; leftover sen
  go one each to the earliest participants, in the order given,
  so the shares always sum exactly to the total.
- ``custom``: the caller gives every participant's share in sen;
  the shares must sum exactly to the total.
- ``weighted``: the caller gives a weight per participant; each
  share is ``floor(total * weight / weight_sum)`` and leftover
  sen go one each by largest fractional remainder (ties broken
  by participant order), so the shares always sum exactly.

The payer fronted the whole bill: their own share is settled at
creation (``paid_cents == share_cents``), everyone else starts
owing their full share. A split needs at least two participants
and the payer must be one of them; every participant must be an
owner or member of the room (a guest may read the room but never
owes or pays through a split).

Payments
---------
``record_payment`` records a partial payment against what one
debtor still owes: the amount must be positive and may never
exceed the remaining balance, so overpaying raises instead of
creating credit. Every payment is an append-only row in
``split_payments`` and moves the split status: ``open`` ->
``partially_paid`` -> ``settled`` (with ``settled_at``) once
every share is fully paid. The payer never "pays" themselves.

Reminders
---------
Queries only, never sends (same rule as B9/B10): ``debtors``
lists who still owes on one split, ``split_reminders`` /
``run_reminder_sweep`` list every outstanding debtor in a room,
and ``build_reminder_digest`` groups them per debtor. A debtor
who has paid in full is never reminded, and the payer is never
reminded — only people who still owe appear. The G8 scheduler
hook (``reminder_job_prompt`` / ``register_reminder_job``) reuses
the G8 cron store; the caller delivers the reminders.

Settlement (B8 prepare-only seam)
----------------------------------
B8 (agent payments, phase 1) is not built yet, so this module
does NOT build it: it builds the seam B8 will plug into. A
settlement is a *prepared* payment: ``prepare_settlement``
checks the B6 money gate (``MoneyGate`` — the room owner alone,
or the requester plus one other owner/member, for the action
``split-settlement:<split_id>:<debtor_id>``) and records a row
in ``split_settlements`` with status ``prepared``: verified
recipient (the payer), amount (what the debtor still owes),
and evidence (split, share, payments so far). The user then
pays in their own bank or e-wallet app; nothing executes here.
There is deliberately no ``execute_settlement`` in this module:
settling means preparing, the user paying outside Puspa, and
the payment being recorded with ``record_payment``. Preparing
again for the same debtor supersedes the earlier prepared row.
When a ledger is passed, the preparation is also appended as an
``evidence_recorded`` event (the closed 31-type set is
untouched); the ledger is optional so the split maths never
depends on it.

Indonesian context (B14): ``kind`` labels the occasion —
``meal`` (makan bareng), ``gift`` (kado bareng), ``arisan``,
``other``. An arisan round is one split: the member who
collects the pot is the payer, everyone else owes one equal
share to them; the next round is a new split, not a recurring
object. Reminders are gentle by construction: they name the
amount and the occasion, never single anyone out in the group
beyond what the group already agreed to split.

Honest limits: no live chat delivery is verified (same as B6);
no real payment rail is called — QRIS / virtual account / bank
transfer / GoPay / OVO / DANA naming belongs to B8, which will
fill in the rail when it lands; the prepared settlement carries
no rail field until then.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import psycopg
from psycopg.rows import dict_row

from puspa.accounts.users import resolve_dsn
from puspa.scheduler.due import classify_due

from .errors import MoneyApprovalError, NotRoomMemberError, SplitError
from .money import MoneyGate
from .rooms import APPROVER_ROLES, RoomStore, _validate_uuid

__all__ = [
    "DEFAULT_REMINDER_SCHEDULE",
    "DEFAULT_REMINDER_TIMEZONE",
    "SEN_PER_RUPIAH",
    "SPLIT_KINDS",
    "SPLIT_STATUSES",
    "Payment",
    "PreparedSettlement",
    "Share",
    "Split",
    "SplitService",
    "build_reminder_digest",
    "cents_to_rupiah",
    "create_split",
    "equal_shares",
    "format_rupiah",
    "get_split",
    "list_debtors",
    "list_settlements",
    "list_splits",
    "prepare_settlement",
    "record_payment",
    "register_reminder_job",
    "reminder_job_prompt",
    "render_split",
    "run_reminder_sweep",
    "rupiah_to_cents",
    "settlement_action_id",
    "split_reminder_lines",
    "split_reminders",
    "weighted_shares",
]

#: Sen (minor units) per rupiah. All money in this module is int sen.
SEN_PER_RUPIAH = 100

#: Occasion labels (B14 Indonesian context).
SPLIT_KINDS = ("meal", "gift", "arisan", "other")

#: Split statuses (stored on room_splits.status).
SPLIT_STATUSES = ("open", "partially_paid", "settled")

#: Settlement row statuses (stored on split_settlements.status).
SETTLEMENT_STATUSES = ("prepared", "superseded")

#: How often the reminder job runs by default.
DEFAULT_REMINDER_SCHEDULE = "every 12h"

#: Timezone for the default reminder job (Indonesian users first).
DEFAULT_REMINDER_TIMEZONE = "Asia/Jakarta"


# --- Money helpers (pure) -------------------------------------------------


def _require_int_cents(value, what: str) -> int:
    """An amount must be a real int (a bool is not an int here)."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise SplitError(f"{what} must be an integer number of sen, got {value!r}")
    return value


def rupiah_to_cents(rupiah: int) -> int:
    """Whole rupiah -> sen (``150000`` -> ``15000000``)."""
    rupiah = _require_int_cents(rupiah, "rupiah")
    if rupiah <= 0:
        raise SplitError(f"rupiah must be positive, got {rupiah}")
    return rupiah * SEN_PER_RUPIAH


def cents_to_rupiah(cents: int) -> int:
    """Sen -> whole rupiah; raises when sen remain (nothing is rounded)."""
    cents = _require_int_cents(cents, "cents")
    if cents % SEN_PER_RUPIAH:
        raise SplitError(f"{cents} sen is not a whole number of rupiah")
    return cents // SEN_PER_RUPIAH


def format_rupiah(cents: int) -> str:
    """Sen -> ``Rp150.000`` (``Rp150.000,50`` when sen remain)."""
    cents = _require_int_cents(cents, "cents")
    if cents < 0:
        raise SplitError(f"cannot format a negative amount: {cents}")
    rupiah, sen = divmod(cents, SEN_PER_RUPIAH)
    grouped = f"{rupiah:,}".replace(",", ".")
    if sen == 0:
        return f"Rp{grouped}"
    return f"Rp{grouped},{sen:02d}"


def equal_shares(total_cents: int, count: int) -> list[int]:
    """Split ``total_cents`` evenly over ``count`` people.

    Leftover sen go one each to the earliest participants, so the
    shares always sum exactly to the total.
    """
    total_cents = _require_int_cents(total_cents, "total_cents")
    if total_cents <= 0:
        raise SplitError(f"total_cents must be positive, got {total_cents}")
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise SplitError(f"count must be a positive integer, got {count!r}")
    base, leftover = divmod(total_cents, count)
    return [base + (1 if i < leftover else 0) for i in range(count)]


def weighted_shares(total_cents: int, weights: list[int]) -> list[int]:
    """Split ``total_cents`` by weights (largest-remainder method).

    Each share starts at ``floor(total * weight / weight_sum)``;
    leftover sen go one each to the largest fractional remainders
    (ties broken by participant order), so the shares always sum
    exactly to the total.
    """
    total_cents = _require_int_cents(total_cents, "total_cents")
    if total_cents <= 0:
        raise SplitError(f"total_cents must be positive, got {total_cents}")
    if not weights:
        raise SplitError("weights must not be empty")
    clean: list[int] = []
    for weight in weights:
        if isinstance(weight, bool) or not isinstance(weight, int) or weight <= 0:
            raise SplitError(f"each weight must be a positive integer, got {weight!r}")
        clean.append(weight)
    weight_sum = sum(clean)
    floors: list[int] = []
    remainders: list[int] = []
    for weight in clean:
        scaled = total_cents * weight
        floors.append(scaled // weight_sum)
        remainders.append(scaled % weight_sum)
    leftover = total_cents - sum(floors)
    order = sorted(range(len(clean)), key=lambda i: (-remainders[i], i))
    shares = list(floors)
    for i in order[:leftover]:
        shares[i] += 1
    return shares


# --- Rows -----------------------------------------------------------------


@dataclass(frozen=True)
class Split:
    """One split: a bill shared by a room, fronted by one payer."""

    id: str
    room_id: str
    title: str
    kind: str
    total_cents: int
    payer_user_id: str
    created_by: str
    due_at: datetime | None
    status: str
    created_at: datetime
    settled_at: datetime | None


@dataclass(frozen=True)
class Share:
    """One participant's share of a split: what they owe, paid, left."""

    split_id: str
    user_id: str
    share_cents: int
    paid_cents: int

    @property
    def remaining_cents(self) -> int:
        return self.share_cents - self.paid_cents

    @property
    def is_paid(self) -> bool:
        return self.remaining_cents <= 0


@dataclass(frozen=True)
class Payment:
    """One recorded (partial) payment by a debtor, append-only."""

    id: str
    split_id: str
    debtor_user_id: str
    amount_cents: int
    recorded_by: str
    created_at: datetime


@dataclass(frozen=True)
class PreparedSettlement:
    """A prepared settlement (B8 phase-1 seam): verified recipient
    + amount + evidence, recorded — never executed. The user pays
    in their own bank/e-wallet app and records it as a payment."""

    id: str
    split_id: str
    debtor_user_id: str
    recipient_user_id: str
    amount_cents: int
    action_id: str
    status: str
    prepared_by: str
    prepared_at: datetime


def _split_row(row) -> Split:
    return Split(
        id=str(row["id"]),
        room_id=str(row["room_id"]),
        title=row["title"],
        kind=row["kind"],
        total_cents=int(row["total_cents"]),
        payer_user_id=str(row["payer_user_id"]),
        created_by=str(row["created_by"]),
        due_at=row["due_at"],
        status=row["status"],
        created_at=row["created_at"],
        settled_at=row["settled_at"],
    )


def _share_row(row) -> Share:
    return Share(
        split_id=str(row["split_id"]),
        user_id=str(row["user_id"]),
        share_cents=int(row["share_cents"]),
        paid_cents=int(row["paid_cents"]),
    )


def _payment_row(row) -> Payment:
    return Payment(
        id=str(row["id"]),
        split_id=str(row["split_id"]),
        debtor_user_id=str(row["debtor_user_id"]),
        amount_cents=int(row["amount_cents"]),
        recorded_by=str(row["recorded_by"]),
        created_at=row["created_at"],
    )


def _settlement_row(row) -> PreparedSettlement:
    return PreparedSettlement(
        id=str(row["id"]),
        split_id=str(row["split_id"]),
        debtor_user_id=str(row["debtor_user_id"]),
        recipient_user_id=str(row["recipient_user_id"]),
        amount_cents=int(row["amount_cents"]),
        action_id=row["action_id"],
        status=row["status"],
        prepared_by=str(row["prepared_by"]),
        prepared_at=row["prepared_at"],
    )


def _connect(dsn=None):
    return psycopg.connect(resolve_dsn(dsn), row_factory=dict_row)


def _short_id(user_id: str | None) -> str:
    return (user_id or "?")[:8]


def settlement_action_id(split_id: str, debtor_user_id: str) -> str:
    """The B6 money-gate action id for one debtor's settlement."""
    split_id = _validate_uuid(split_id, "split_id")
    debtor = _validate_uuid(debtor_user_id, "debtor_user_id")
    return f"split-settlement:{split_id}:{debtor}"


def render_split(split: Split, shares: list[Share], *, now: datetime | None = None) -> str:
    """Plain-text summary of a split for the group chat.

    One line per participant (payer first, then debtors in
    participant order): what each owes, paid, and still owes.
    Never invents a payment: the numbers come from the rows.
    """
    moment = now or datetime.now(UTC)
    lines = [
        (
            f"Split '{split.title}' ({split.kind}): total {format_rupiah(split.total_cents)}, "
            f"status {split.status}."
        )
    ]
    if split.due_at is not None:
        lines[0] += (
            f" Due {split.due_at.date().isoformat()} ({classify_due(split.due_at, moment)})."
        )
    for share in shares:
        who = "payer" if share.user_id == split.payer_user_id else "owes"
        lines.append(
            f"- {_short_id(share.user_id)} ({who}): "
            f"share {format_rupiah(share.share_cents)}, "
            f"paid {format_rupiah(share.paid_cents)}, "
            f"remaining {format_rupiah(share.remaining_cents)}."
        )
    return "\n".join(lines)


def split_reminder_lines(
    split: Split, shares: list[Share], *, now: datetime | None = None
) -> list[str]:
    """One reminder line per debtor who still owes on this split.

    Debtors only: the payer and anyone fully paid get no line.
    This only writes the lines — the caller sends them.
    """
    moment = now or datetime.now(UTC)
    due = ""
    if split.due_at is not None:
        state = classify_due(split.due_at, moment)
        due = f" (due {split.due_at.date().isoformat()}, {state})"
    lines = []
    for share in shares:
        if share.remaining_cents <= 0:
            continue
        lines.append(
            f"Reminder: {_short_id(share.user_id)} still owes "
            f"{format_rupiah(share.remaining_cents)} for '{split.title}'"
            f" (paid {format_rupiah(share.paid_cents)} of "
            f"{format_rupiah(share.share_cents)}){due}."
        )
    return lines


class SplitService:
    """Postgres-backed splits for one room at a time.

    One instance per call site; the DSN is fixed at construction
    so tests can point at a test database without touching env
    vars. Every method opens its own short connection.
    """

    def __init__(
        self,
        dsn=None,
        room_store: RoomStore | None = None,
        money_gate: MoneyGate | None = None,
    ):
        self.dsn = resolve_dsn(dsn)
        self.rooms = room_store or RoomStore(dsn)
        self.money = money_gate or MoneyGate(dsn, room_store=self.rooms)

    # -- reads ----------------------------------------------------------

    def get_split(self, split_id: str) -> Split | None:
        """Return the split, or None when the id does not exist."""
        split_id = _validate_uuid(split_id, "split_id")
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM room_splits WHERE id = %s", (split_id,))
            row = cur.fetchone()
        return _split_row(row) if row is not None else None

    def require_split(self, split_id: str) -> Split:
        """Return the split, or raise ``SplitError``."""
        split = self.get_split(split_id)
        if split is None:
            raise SplitError(f"split not found: {split_id}")
        return split

    def list_splits(self, room_id: str, *, viewer_user_id: str) -> list[Split]:
        """All splits of a room, oldest first. Members only
        (a guest may read, like the rest of the room)."""
        room_id = _validate_uuid(room_id, "room_id")
        viewer = _validate_uuid(viewer_user_id, "viewer_user_id")
        self._require_member(room_id, viewer)
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM room_splits WHERE room_id = %s ORDER BY created_at, id",
                (room_id,),
            )
            return [_split_row(row) for row in cur.fetchall()]

    def list_shares(self, split_id: str, *, viewer_user_id: str) -> list[Share]:
        """Every participant's share of a split, in participant
        order (the order the split was created with)."""
        split = self.require_split(split_id)
        viewer = _validate_uuid(viewer_user_id, "viewer_user_id")
        self._require_member(split.room_id, viewer)
        return self._shares_in(split.id)

    def list_debtors(self, split_id: str, *, viewer_user_id: str) -> list[Share]:
        """Shares that still owe something — debtors only.

        The payer never appears here (their share is settled at
        creation) and neither does anyone who has paid in full.
        """
        shares = self.list_shares(split_id, viewer_user_id=viewer_user_id)
        return [share for share in shares if share.remaining_cents > 0]

    def list_payments(self, split_id: str, *, viewer_user_id: str) -> list[Payment]:
        """Recorded payments on a split, oldest first."""
        split = self.require_split(split_id)
        viewer = _validate_uuid(viewer_user_id, "viewer_user_id")
        self._require_member(split.room_id, viewer)
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM split_payments WHERE split_id = %s ORDER BY created_at, id",
                (split.id,),
            )
            return [_payment_row(row) for row in cur.fetchall()]

    def list_settlements(self, split_id: str, *, viewer_user_id: str) -> list[PreparedSettlement]:
        """Prepared settlements for a split, newest first."""
        split = self.require_split(split_id)
        viewer = _validate_uuid(viewer_user_id, "viewer_user_id")
        self._require_member(split.room_id, viewer)
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM split_settlements WHERE split_id = %s ORDER BY prepared_at DESC, id",
                (split.id,),
            )
            return [_settlement_row(row) for row in cur.fetchall()]

    # -- creation -------------------------------------------------------

    def create_split(
        self,
        room_id: str,
        title: str,
        total_cents: int,
        payer_user_id: str,
        participant_user_ids: list[str],
        *,
        created_by: str,
        kind: str = "other",
        shares: dict[str, int] | None = None,
        weights: list[int] | None = None,
        due_at: datetime | None = None,
    ) -> Split:
        """Create a split and its shares in one transaction.

        ``shares`` (custom, user id -> sen) and ``weights``
        (one weight per participant, in participant order) are
        alternatives to the default equal split; giving both
        raises. Shares must sum exactly to the total; weights
        use the largest-remainder method. The payer's own share
        is marked paid at creation — they fronted the bill.
        """
        room_id = _validate_uuid(room_id, "room_id")
        payer = _validate_uuid(payer_user_id, "payer_user_id")
        creator = _validate_uuid(created_by, "created_by")
        if not isinstance(title, str) or not title.strip():
            raise SplitError("split title must be a non-empty string")
        if kind not in SPLIT_KINDS:
            raise SplitError(f"unknown split kind: {kind!r} (must be one of {SPLIT_KINDS})")
        total_cents = _require_int_cents(total_cents, "total_cents")
        if total_cents <= 0:
            raise SplitError(f"total_cents must be positive, got {total_cents}")
        if due_at is not None and not isinstance(due_at, datetime):
            raise SplitError(f"due_at must be a datetime or None, got {due_at!r}")
        self.rooms.require_room(room_id)
        self._require_approver(room_id, creator, "create a split")
        self._require_approver(room_id, payer, "front a split")
        participants: list[str] = []
        for raw in participant_user_ids:
            user_id = _validate_uuid(raw, "participant_user_id")
            if user_id in participants:
                raise SplitError(f"participant listed twice: {user_id}")
            self._require_approver(room_id, user_id, "take part in a split")
            participants.append(user_id)
        if len(participants) < 2:
            raise SplitError("a split needs at least two participants")
        if payer not in participants:
            raise SplitError("the payer must be one of the participants")
        share_amounts = self._resolve_shares(total_cents, participants, shares, weights)
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO room_splits "
                "(room_id, title, kind, total_cents, payer_user_id, created_by, due_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING *",
                (room_id, title.strip(), kind, total_cents, payer, creator, due_at),
            )
            split = _split_row(cur.fetchone())
            for index, user_id in enumerate(participants):
                paid = share_amounts[index] if user_id == payer else 0
                cur.execute(
                    "INSERT INTO split_shares (split_id, user_id, share_cents, paid_cents, position) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (split.id, user_id, share_amounts[index], paid, index),
                )
            conn.commit()
        return split

    def _resolve_shares(
        self,
        total_cents: int,
        participants: list[str],
        shares: dict[str, int] | None,
        weights: list[int] | None,
    ) -> list[int]:
        if shares is not None and weights is not None:
            raise SplitError("give either shares or weights, not both")
        if shares is not None:
            if set(shares) != set(participants):
                raise SplitError("custom shares must cover exactly the participants")
            amounts = [
                _require_int_cents(shares[user_id], f"share for {user_id}")
                for user_id in participants
            ]
            if any(amount <= 0 for amount in amounts):
                raise SplitError("every share must be positive")
            if sum(amounts) != total_cents:
                raise SplitError(
                    f"shares sum to {sum(amounts)} sen, not the total {total_cents} sen"
                )
            return amounts
        if weights is not None:
            if len(weights) != len(participants):
                raise SplitError("weights must have one entry per participant")
            return weighted_shares(total_cents, weights)
        return equal_shares(total_cents, len(participants))

    # -- payments -------------------------------------------------------

    def record_payment(
        self, split_id: str, debtor_user_id: str, amount_cents: int, *, recorded_by: str
    ) -> Payment:
        """Record a (partial) payment against what a debtor owes.

        The amount must be positive and no larger than the
        remaining balance — overpaying raises instead of creating
        credit. The payer cannot pay themselves. Recording is
        member-only (owner/member); the debtor may record their
        own payment. Updates the split status in the same
        transaction: ``partially_paid`` while anyone still owes,
        ``settled`` (with ``settled_at``) once nobody does.
        """
        split = self.require_split(split_id)
        debtor = _validate_uuid(debtor_user_id, "debtor_user_id")
        recorder = _validate_uuid(recorded_by, "recorded_by")
        amount_cents = _require_int_cents(amount_cents, "amount_cents")
        if amount_cents <= 0:
            raise SplitError(f"amount_cents must be positive, got {amount_cents}")
        self._require_approver(split.room_id, recorder, "record a payment")
        if debtor == split.payer_user_id:
            raise SplitError("the payer cannot pay themselves: their share is already settled")
        shares = self._shares_in(split.id)
        share = next((s for s in shares if s.user_id == debtor), None)
        if share is None:
            raise SplitError(f"user {debtor} is not a participant of split {split.id}")
        if amount_cents > share.remaining_cents:
            raise SplitError(
                f"payment of {format_rupiah(amount_cents)} exceeds what "
                f"{_short_id(debtor)} still owes ({format_rupiah(share.remaining_cents)})"
            )
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO split_payments (split_id, debtor_user_id, amount_cents, recorded_by) "
                "VALUES (%s, %s, %s, %s) RETURNING *",
                (split.id, debtor, amount_cents, recorder),
            )
            payment = _payment_row(cur.fetchone())
            cur.execute(
                "UPDATE split_shares SET paid_cents = paid_cents + %s "
                "WHERE split_id = %s AND user_id = %s",
                (amount_cents, split.id, debtor),
            )
            remaining = self._remaining_total_in(conn, split.id)
            status = "settled" if remaining == 0 else "partially_paid"
            cur.execute(
                "UPDATE room_splits SET status = %s, settled_at = "
                "CASE WHEN %s THEN now() ELSE settled_at END WHERE id = %s",
                (status, status == "settled", split.id),
            )
            conn.commit()
        return payment

    # -- settlement (B8 prepare-only seam) ------------------------------

    def prepare_settlement(
        self,
        split_id: str,
        debtor_user_id: str,
        *,
        requested_by: str,
        ledger=None,
        agent_id: str | None = None,
    ) -> PreparedSettlement:
        """Prepare (never execute) a debtor's settlement payment.

        The amount is what the debtor still owes, the recipient is
        the payer, and the preparation is gated by the B6 money
        gate for ``split-settlement:<split>:<debtor>``: the room
        owner alone passes; anyone else needs their own approval
        plus one other owner/member (see ``MoneyGate``). Without
        that approval this raises ``MoneyApprovalError`` and
        nothing is recorded. Preparing again supersedes the
        debtor's earlier prepared row. When a ledger is passed,
        the preparation is also appended as an
        ``evidence_recorded`` event (``agent_id`` required with
        it); the ledger is optional so the split maths never
        depends on it.
        """
        split = self.require_split(split_id)
        debtor = _validate_uuid(debtor_user_id, "debtor_user_id")
        requester = _validate_uuid(requested_by, "requested_by")
        if ledger is not None and agent_id is None:
            raise SplitError("agent_id is required when a ledger is passed")
        if debtor == split.payer_user_id:
            raise SplitError("the payer has nothing to settle")
        shares = self._shares_in(split.id)
        share = next((s for s in shares if s.user_id == debtor), None)
        if share is None:
            raise SplitError(f"user {debtor} is not a participant of split {split.id}")
        if share.remaining_cents <= 0:
            raise SplitError(f"user {debtor} has nothing left to settle on split {split.id}")
        action_id = settlement_action_id(split.id, debtor)
        if not self.money.is_money_approved(split.room_id, action_id, requester_user_id=requester):
            raise MoneyApprovalError(
                f"settlement for split {split.id} is not money-approved "
                f"(action {action_id}); the room owner, or the requester plus "
                "one other owner/member, must approve it first"
            )
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE split_settlements SET status = 'superseded' "
                "WHERE split_id = %s AND debtor_user_id = %s AND status = 'prepared'",
                (split.id, debtor),
            )
            cur.execute(
                "INSERT INTO split_settlements "
                "(split_id, debtor_user_id, recipient_user_id, amount_cents, action_id, prepared_by) "
                "VALUES (%s, %s, %s, %s, %s, %s) RETURNING *",
                (
                    split.id,
                    debtor,
                    split.payer_user_id,
                    share.remaining_cents,
                    action_id,
                    requester,
                ),
            )
            settlement = _settlement_row(cur.fetchone())
            conn.commit()
        if ledger is not None:
            ledger.append_event(
                task_id=split.id,
                agent_id=agent_id,
                event_type="evidence_recorded",
                content={
                    "kind": "split_settlement_prepared",
                    "split_id": split.id,
                    "room_id": split.room_id,
                    "debtor_user_id": debtor,
                    "recipient_user_id": split.payer_user_id,
                    "amount_cents": share.remaining_cents,
                    "action_id": action_id,
                    "executed": False,
                    "note": "prepared only; the user pays in their own bank/e-wallet app",
                },
            )
        return settlement

    # -- reminders (queries only, never sends) --------------------------

    def split_reminders(
        self, room_id: str, *, viewer_user_id: str, now: datetime | None = None
    ) -> list[dict]:
        """Every outstanding debtor in a room, one entry per debtor.

        Each entry: ``split_id``, ``title``, ``kind``,
        ``debtor_user_id``, ``share_cents``, ``paid_cents``,
        ``remaining_cents``, ``due_at``, ``urgency``
        (G8 ``classify_due`` when the split has a due date, else
        ``"open"``), and ``text``. Debtors only — never the payer,
        never anyone fully paid. The caller sends the reminders.
        """
        room_id = _validate_uuid(room_id, "room_id")
        viewer = _validate_uuid(viewer_user_id, "viewer_user_id")
        self._require_member(room_id, viewer)
        moment = now or datetime.now(UTC)
        entries: list[dict] = []
        for split in self.list_splits(room_id, viewer_user_id=viewer):
            if split.status == "settled":
                continue
            shares = self._shares_in(split.id)
            for share in shares:
                if share.remaining_cents <= 0:
                    continue
                urgency = classify_due(split.due_at, moment) if split.due_at is not None else "open"
                entries.append(
                    {
                        "split_id": split.id,
                        "title": split.title,
                        "kind": split.kind,
                        "debtor_user_id": share.user_id,
                        "share_cents": share.share_cents,
                        "paid_cents": share.paid_cents,
                        "remaining_cents": share.remaining_cents,
                        "due_at": split.due_at.isoformat() if split.due_at else None,
                        "urgency": urgency,
                        "text": split_reminder_lines(split, [share], now=moment)[0],
                    }
                )
        return entries

    def build_reminder_digest(
        self, room_id: str, *, viewer_user_id: str, now: datetime | None = None
    ) -> list[dict]:
        """Per-debtor reminder digest: one entry per debtor with
        outstanding splits, most urgent first within each debtor.
        """
        entries = self.split_reminders(room_id, viewer_user_id=viewer_user_id, now=now)
        urgency_rank = {"missed": 0, "late": 1, "due": 2, "open": 3, "not_yet": 4}
        by_debtor: dict[str, list[dict]] = {}
        for entry in entries:
            by_debtor.setdefault(entry["debtor_user_id"], []).append(entry)
        digest = []
        for debtor in sorted(by_debtor):
            items = sorted(
                by_debtor[debtor], key=lambda e: (urgency_rank.get(e["urgency"], 3), e["title"])
            )
            digest.append({"debtor_user_id": debtor, "reminders": items})
        return digest

    # -- helpers ----------------------------------------------------------

    def _require_member(self, room_id: str, user_id: str) -> None:
        if not self.rooms.is_member(room_id, user_id):
            raise NotRoomMemberError(f"user {user_id} is not a member of room {room_id}")

    def _require_approver(self, room_id: str, user_id: str, action: str) -> None:
        if self.rooms.role_of(room_id, user_id) not in APPROVER_ROLES:
            raise NotRoomMemberError(
                f"user {user_id} may not {action} in room {room_id} "
                "(only an owner or member may; a guest may not)"
            )

    def _shares_in(self, split_id: str) -> list[Share]:
        """Shares in participant order (creation order, stored as
        ``split_shares.position`` — the order decides who gets the
        leftover sen on an equal split)."""
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM split_shares WHERE split_id = %s ORDER BY position, user_id",
                (split_id,),
            )
            return [_share_row(row) for row in cur.fetchall()]

    def _remaining_total_in(self, conn, split_id: str) -> int:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COALESCE(SUM(share_cents - paid_cents), 0) AS remaining "
                "FROM split_shares WHERE split_id = %s",
                (split_id,),
            )
            return int(cur.fetchone()["remaining"])


# --- Module-level functions (thin wrappers over a fresh service) --------


def _service(dsn=None) -> SplitService:
    return SplitService(dsn)


def create_split(
    room_id: str,
    title: str,
    total_cents: int,
    payer_user_id: str,
    participant_user_ids: list[str],
    *,
    created_by: str,
    kind: str = "other",
    shares: dict[str, int] | None = None,
    weights: list[int] | None = None,
    due_at: datetime | None = None,
    dsn=None,
) -> Split:
    return _service(dsn).create_split(
        room_id,
        title,
        total_cents,
        payer_user_id,
        participant_user_ids,
        created_by=created_by,
        kind=kind,
        shares=shares,
        weights=weights,
        due_at=due_at,
    )


def get_split(split_id: str, dsn=None) -> Split | None:
    return _service(dsn).get_split(split_id)


def list_splits(room_id: str, *, viewer_user_id: str, dsn=None) -> list[Split]:
    return _service(dsn).list_splits(room_id, viewer_user_id=viewer_user_id)


def list_debtors(split_id: str, *, viewer_user_id: str, dsn=None) -> list[Share]:
    return _service(dsn).list_debtors(split_id, viewer_user_id=viewer_user_id)


def record_payment(
    split_id: str, debtor_user_id: str, amount_cents: int, *, recorded_by: str, dsn=None
) -> Payment:
    return _service(dsn).record_payment(
        split_id, debtor_user_id, amount_cents, recorded_by=recorded_by
    )


def prepare_settlement(
    split_id: str,
    debtor_user_id: str,
    *,
    requested_by: str,
    ledger=None,
    agent_id: str | None = None,
    dsn=None,
) -> PreparedSettlement:
    return _service(dsn).prepare_settlement(
        split_id, debtor_user_id, requested_by=requested_by, ledger=ledger, agent_id=agent_id
    )


def list_settlements(split_id: str, *, viewer_user_id: str, dsn=None) -> list[PreparedSettlement]:
    return _service(dsn).list_settlements(split_id, viewer_user_id=viewer_user_id)


def split_reminders(
    room_id: str, *, viewer_user_id: str, now: datetime | None = None, dsn=None
) -> list[dict]:
    return _service(dsn).split_reminders(room_id, viewer_user_id=viewer_user_id, now=now)


def build_reminder_digest(
    room_id: str, *, viewer_user_id: str, now: datetime | None = None, dsn=None
) -> list[dict]:
    return _service(dsn).build_reminder_digest(room_id, viewer_user_id=viewer_user_id, now=now)


def run_reminder_sweep(
    room_id: str, *, viewer_user_id: str, now: datetime | None = None, dsn=None
) -> list[dict]:
    """The scheduler hook: the flat list from ``split_reminders``.

    One entry per outstanding debtor per split. The caller (a G8
    cron job run, or a room routine) delivers each entry to its
    debtor; this function never sends anything itself.
    """
    return split_reminders(room_id, viewer_user_id=viewer_user_id, now=now, dsn=dsn)


def reminder_job_prompt(room_id: str) -> str:
    """Prompt for the G8 cron job that sends split reminders.

    The fresh agent running this prompt calls
    ``run_reminder_sweep`` for the room and delivers one reminder
    per debtor; it never invents a split, a debtor, or an amount,
    and never reminds the payer or anyone who already paid.
    """
    room_id = _validate_uuid(room_id, "room_id")
    return (
        "Shared-split reminders for room " + room_id + ". "
        "Call puspa.rooms.run_reminder_sweep for this room and "
        "deliver each returned reminder text to its debtor. "
        "Send only the reminders returned: never invent a split, "
        "a debtor, or an amount, and never remind the payer or "
        "anyone who already paid in full."
    )


def register_reminder_job(
    store,
    room_id: str,
    *,
    deliver_to: str = "",
    schedule: str = DEFAULT_REMINDER_SCHEDULE,
    timezone: str = DEFAULT_REMINDER_TIMEZONE,
) -> dict:
    """Create the G8 cron job that sends split reminders.

    ``store`` is a ``puspa.scheduler.cron.CronJobStore``. Returns
    the ``cronjob_manage`` result (``{"action": "create", "job":
    {...}}``). This reuses the G8 scheduler: no second scheduler
    is built here.
    """
    from puspa.scheduler.cron import cronjob_manage

    room_id = _validate_uuid(room_id, "room_id")
    return cronjob_manage(
        store,
        "create",
        prompt=reminder_job_prompt(room_id),
        schedule=schedule,
        deliver_to=deliver_to,
        timezone=timezone,
    )
