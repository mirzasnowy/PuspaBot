"""Group research and shopping: a room collects product links,
the agent compares price and seller for all of them, and the
group decides in chat (B12).

This is the group version of the B1 single-user price check
(``puspa/pricing/compare.py``): instead of one product across
three marketplaces, a room collects any number of product links
— one link per option the group is considering — and gets one
comparison answer with evidence per item.

Rules, all enforced here:

- **Every number carries its evidence, never invented.** Each
  checked link is read by an injected reader, then re-read by
  an independent verifier (``puspa/evidence/verifier.py``,
  same as B1). A price is shown only with its ``evidence_id``
  and verdict, and the evidence row is saved to the ``evidence``
  table. A link the reader cannot read is shown as unchecked
  with its reason — never as a guessed price.
- **The ToS gate (decision K-15) is enforced, not bypassed.**
  In ``live`` mode a marketplace source that forbids automated
  reading (Tokopedia, Shopee, Blibli — see
  ``puspa/pricing/tos.py``) is held: the reader is never called
  for it and the item is reported as held. ``fixture`` mode
  (test fixtures / local test data) is always allowed. No live
  marketplace read has been verified in this iteration.
- **The cheapest pick uses verified prices only.** Unlike B1,
  which ranks every price it read, a group decision must not
  rest on an unverified number: the cheapest item is the lowest
  price among items whose verdict is ``Verified``. An item whose
  re-read disagrees is shown with its verdict, but never wins.
- **The seller is reported, never invented.** The reader may
  report a seller name (``ResultRead.seller``, extracted from
  generic JSON-LD ``offers.seller`` when present); when the
  reader reports none, the answer says the seller is unknown.
- **The group decision is recorded with who agreed.** Each
  agreer is explicit and must be a room member — a non-member
  in the agreement list is rejected, and silence is never read
  as agreement. The decision is stored as room memory of kind
  ``decision`` (``puspa/rooms/memory.py``, same as B10), with
  the comparison task id attached so the shared graphs of B3
  can adopt it later. B3 itself is NOT built here.
- **Only room members take part.** Adding, listing, removing,
  comparing, and deciding all require room membership; the
  membership check is in this module, not in the caller.

Collecting links: members paste links in the group chat;
``extract_product_links`` pulls the allowlisted product URLs
out of a message. A link is stored once per room (same URL
re-added is a no-op returning the existing row).

Routing: ``handle_group_inbound`` answers a compare command
("@puspa compare", "@puspa bandingkan harga", ...) with the
rendered comparison when a ``ResearchService`` is passed as
``research`` — the same hook shape as the B9 board command.
Any other addressed message still goes to the agent.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urlparse

import psycopg
from psycopg.rows import dict_row

from puspa.accounts.users import resolve_dsn
from puspa.evidence.evidence import record_evidence, save_evidence
from puspa.evidence.verifier import verify
from puspa.pricing.readers import create_reader
from puspa.pricing.tos import may_read
from puspa.sandbox.allowlist import is_allowed

from .errors import NotRoomMemberError
from .memory import RoomMemory, RoomMemoryStore
from .rooms import RoomStore, _validate_uuid

__all__ = [
    "COMPARE_COMMANDS",
    "MAX_LABEL_CHARS",
    "GroupComparison",
    "ResearchItem",
    "ResearchLink",
    "ResearchService",
    "add_research_link",
    "answer_compare_command",
    "compare_group_links",
    "detect_source",
    "extract_product_links",
    "is_compare_command",
    "list_research_links",
    "record_group_decision",
    "remove_research_link",
    "render_group_comparison",
]

MARKETPLACE_HOSTS = {
    "tokopedia.com": "Tokopedia",
    "shopee.co.id": "Shopee",
    "blibli.com": "Blibli",
}

COMPARE_COMMANDS = (
    "compare",
    "compare prices",
    "bandingkan",
    "bandingkan harga",
    "research",
    "shopping",
)
_MENTION_RE = re.compile(r"@puspa\b", re.IGNORECASE)
_URL_RE = re.compile(r"https?://[^\s<>\")\]]+")
MAX_LABEL_CHARS = 200
UNKNOWN_SELLER = "unknown (the reader did not report one)"


def detect_source(url: str) -> str:
    """The marketplace a product URL belongs to, or ``"other"``.

    Matches the registered host or any of its subdomains
    (``www.tokopedia.com`` is Tokopedia; ``tokopedia.com.evil.com``
    is not). An unparseable URL is ``"other"``.
    """
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return "other"
    for domain, source in MARKETPLACE_HOSTS.items():
        if host == domain or host.endswith("." + domain):
            return source
    return "other"


def extract_product_links(text: str | None) -> list[str]:
    """Pull allowlisted product URLs out of a chat message.

    Returns the URLs in first-seen order, de-duplicated, with
    trailing punctuation stripped. A URL whose host is not on
    the allowlist is dropped, never collected: the group can only
    research sources Puspa is allowed to read.
    """
    if not isinstance(text, str):
        return []
    found: list[str] = []
    for match in _URL_RE.finditer(text):
        url = match.group(0).rstrip(".,;:!?)")
        if url not in found and is_allowed(url):
            found.append(url)
    return found


def is_compare_command(text: str | None) -> bool:
    """True when a group message asks for the link comparison.

    The @Puspa mention is stripped first; the rest must be a
    compare request ("compare", "compare prices", "bandingkan",
    "bandingkan harga", "research", "shopping"), optionally
    followed by punctuation. Anything else goes to the agent.
    """
    if not isinstance(text, str):
        return False
    cleaned = _MENTION_RE.sub("", text).strip().lower().rstrip(".!?").strip()
    return cleaned in COMPARE_COMMANDS


def _format_rupiah(price: float) -> str:
    """Format a Rupiah amount the Indonesian way: Rp 12.499.000."""
    return "Rp " + f"{int(price):,}".replace(",", ".")


@dataclass(frozen=True)
class ResearchLink:
    """One product link a room member collected for comparison."""

    id: str
    room_id: str
    url: str
    label: str
    source: str
    added_by_user_id: str
    created_at: datetime

    def display_name(self) -> str:
        """The label when the member gave one, else the URL."""
        return self.label or self.url


@dataclass
class ResearchItem:
    """One compared link: the reading, or why it could not be read.

    ``checked`` is True only when the reader returned a price.
    ``price`` is shown only together with ``evidence_id`` and
    ``verdict`` — a number without evidence is never rendered.
    """

    link: ResearchLink
    checked: bool
    price: float | None = None
    shipping: float = 0
    store_rating: float | None = None
    seller: str = ""
    evidence_id: str | None = None
    verdict: str | None = None
    reason: str = ""

    def seller_display(self) -> str:
        """The seller name, or an explicit unknown — never invented."""
        return self.seller.strip() or UNKNOWN_SELLER


@dataclass
class GroupComparison:
    """One comparison answer for a room: every collected link."""

    room_id: str
    task_id: str
    mode: str
    items: list[ResearchItem] = field(default_factory=list)

    @property
    def verified_items(self) -> list[ResearchItem]:
        """Checked items whose price the verifier confirmed."""
        return [
            i for i in self.items if i.checked and i.verdict == "Verified" and i.price is not None
        ]

    @property
    def cheapest(self) -> ResearchItem | None:
        """The lowest verified price, or None when nothing verified."""
        verified = self.verified_items
        if not verified:
            return None
        return min(verified, key=lambda i: i.price or 0)

    @property
    def spread(self) -> float:
        """Highest minus lowest verified price (0 when fewer than two)."""
        prices = [i.price for i in self.verified_items if i.price is not None]
        return (max(prices) - min(prices)) if len(prices) > 1 else 0

    @property
    def unchecked_count(self) -> int:
        return sum(1 for i in self.items if not i.checked)


def _link_row(row) -> ResearchLink:
    return ResearchLink(
        id=str(row["id"]),
        room_id=str(row["room_id"]),
        url=row["url"],
        label=row["label"] or "",
        source=row["source"],
        added_by_user_id=str(row["added_by"]),
        created_at=row["created_at"],
    )


def _connect(dsn=None):
    return psycopg.connect(resolve_dsn(dsn), row_factory=dict_row)


def _no_reread(url: str):
    """Default re-read: no independent re-read exists, so the
    verifier reports "Cannot be determined" — a price is never
    marked verified without a real second read."""


class ResearchService:
    """Postgres-backed group research for one room at a time.

    ``reader`` is one reader object with ``.read(url)`` or a
    mapping ``source -> reader`` (one per marketplace); when
    neither is given, a reader is created per source with
    ``puspa.pricing.readers.create_reader``. ``reread`` is the
    independent verifier's re-read callable
    ``reread(url) -> {"value", "name"}``; when it is None, no
    price can verify (see ``_no_reread``). ``mode`` is
    ``"fixture"`` (test data, always allowed) or ``"live"``
    (the ToS gate holds forbidden marketplace sources).
    """

    def __init__(
        self,
        dsn=None,
        *,
        room_store: RoomStore | None = None,
        memory_store: RoomMemoryStore | None = None,
        reader=None,
        reread=None,
        mode: str = "fixture",
    ):
        if mode not in ("fixture", "live"):
            raise ValueError(f"unknown mode: {mode!r}")
        self.dsn = resolve_dsn(dsn)
        self.rooms = room_store or RoomStore(dsn)
        self.memory = memory_store or RoomMemoryStore(dsn)
        self.reader = reader
        self.reread = reread
        self.mode = mode

    def _require_member(self, room_id: str, user_id: str) -> None:
        if not self.rooms.is_member(room_id, user_id):
            raise NotRoomMemberError(f"user {user_id} is not a member of room {room_id}")

    def _reader_for(self, source: str):
        if isinstance(self.reader, dict):
            reader = self.reader.get(source)
            if reader is not None:
                return reader
        elif self.reader is not None:
            return self.reader
        created, _path = create_reader(source)
        return created

    def add_link(
        self, room_id: str, *, added_by_user_id: str, url: str, label: str | None = None
    ) -> ResearchLink:
        """Collect one product link for the room. The adder must be
        a member; the URL must be http(s) on an allowlisted host.
        Re-adding the same URL is a no-op returning the existing
        row — a group collects a link once.

        Raises ``NotRoomMemberError`` for a non-member,
        ``ValueError`` for a bad URL or a host Puspa may not read.
        """
        room_id = _validate_uuid(room_id, "room_id")
        adder = _validate_uuid(added_by_user_id, "added_by_user_id")
        if not isinstance(url, str) or not url.strip():
            raise ValueError("url must be a non-empty string")
        url = url.strip()
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError(f"not a product URL: {url!r}")
        if not is_allowed(url):
            raise ValueError(f"host is not on the allowlist: {parsed.hostname!r}")
        if label is not None and len(label) > MAX_LABEL_CHARS:
            raise ValueError(f"label is longer than {MAX_LABEL_CHARS} characters")
        self._require_member(room_id, adder)
        source = detect_source(url)
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO room_research_links (room_id, url, label, source, added_by) "
                "VALUES (%s, %s, %s, %s, %s) "
                "ON CONFLICT (room_id, url) DO NOTHING RETURNING *",
                (room_id, url, (label or "").strip(), source, adder),
            )
            row = cur.fetchone()
            if row is None:
                cur.execute(
                    "SELECT * FROM room_research_links WHERE room_id = %s AND url = %s",
                    (room_id, url),
                )
                row = cur.fetchone()
            conn.commit()
        return _link_row(row)

    def list_links(self, room_id: str, *, viewer_user_id: str) -> list[ResearchLink]:
        """All links collected in a room, oldest first. Members only."""
        room_id = _validate_uuid(room_id, "room_id")
        viewer = _validate_uuid(viewer_user_id, "viewer_user_id")
        self._require_member(room_id, viewer)
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM room_research_links WHERE room_id = %s ORDER BY created_at ASC, id",
                (room_id,),
            )
            return [_link_row(row) for row in cur.fetchall()]

    def remove_link(self, link_id: str, *, removed_by: str) -> bool:
        """Remove a collected link. Allowed to the member who added
        it, or to a room owner. Returns True when a row was removed,
        False when the id does not exist. Raises
        ``NotRoomMemberError`` when the remover may not remove it.
        """
        link_id = _validate_uuid(link_id, "link_id")
        removed_by = _validate_uuid(removed_by, "removed_by")
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM room_research_links WHERE id = %s", (link_id,))
            row = cur.fetchone()
            if row is None:
                return False
            link = _link_row(row)
            is_author = link.added_by_user_id == removed_by
            is_owner = self.rooms.is_owner(link.room_id, removed_by)
            if not is_author and not is_owner:
                raise NotRoomMemberError(f"user {removed_by} may not remove link {link_id}")
            cur.execute("DELETE FROM room_research_links WHERE id = %s", (link_id,))
            conn.commit()
        return True

    def compare(
        self, room_id: str, *, requested_by_user_id: str, task_id: str | None = None
    ) -> GroupComparison:
        """Compare every link collected in the room, in one answer.

        Each link is read, then independently re-read by the
        verifier; the evidence row is saved. In ``live`` mode a
        marketplace source held by the ToS gate is never read.
        The requester must be a member. A room with no links
        returns an empty comparison — the renderer says so.
        """
        room_id = _validate_uuid(room_id, "room_id")
        requester = _validate_uuid(requested_by_user_id, "requested_by_user_id")
        self._require_member(room_id, requester)
        task_id = task_id or f"research-{uuid.uuid4().hex[:12]}"
        comparison = GroupComparison(room_id=room_id, task_id=task_id, mode=self.mode)
        for link in self.list_links(room_id, viewer_user_id=requester):
            comparison.items.append(self._check_link(link, task_id=task_id))
        return comparison

    def _check_link(self, link: ResearchLink, *, task_id: str) -> ResearchItem:
        """Read one link and verify its price, or record why not."""
        if self.mode == "live" and not may_read(link.source, mode="live"):
            return ResearchItem(
                link=link,
                checked=False,
                reason="tos_gate_held",
            )
        read = self._reader_for(link.source).read(link.url)
        if not read.success or read.price is None:
            return ResearchItem(
                link=link,
                checked=False,
                reason=read.reason or "could_not_read",
            )
        claim = read.name or link.display_name()
        evidence = record_evidence(
            task_id=task_id,
            claim=claim,
            value=read.price,
            unit="Rp",
            source_url=link.url,
            text_excerpt=read.text_excerpt or str(read.price),
            reader_id=f"research-{link.source.lower()}",
        )
        evidence = verify(evidence, reread=self.reread or _no_reread)
        save_evidence(self.dsn, evidence, link.added_by_user_id, task_id)
        return ResearchItem(
            link=link,
            checked=True,
            price=read.price,
            shipping=read.shipping or 0,
            store_rating=read.store_rating,
            seller=read.seller or "",
            evidence_id=evidence.evidence_id,
            verdict=evidence.verdict,
        )

    def record_group_decision(
        self,
        room_id: str,
        *,
        author_user_id: str,
        decision: str,
        agreed_by_user_ids: list[str] | None = None,
        comparison_task_id: str | None = None,
    ) -> RoomMemory:
        """Record the group's decision, with who agreed.

        The author and every agreer must be room members — a
        non-member in the agreement list is rejected, and an empty
        agreement list records the decision without claiming anyone
        agreed. The decision is stored as room memory of kind
        ``decision``; the comparison task id is attached when the
        decision follows a comparison, so the shared graphs of B3
        can adopt the record later.
        """
        room_id = _validate_uuid(room_id, "room_id")
        author = _validate_uuid(author_user_id, "author_user_id")
        if not isinstance(decision, str) or not decision.strip():
            raise ValueError("decision must be a non-empty string")
        agreed = [_validate_uuid(uid, "agreed_by_user_id") for uid in (agreed_by_user_ids or [])]
        self._require_member(room_id, author)
        for uid in agreed:
            if not self.rooms.is_member(room_id, uid):
                raise NotRoomMemberError(
                    f"user {uid} is not a member of room {room_id} and cannot agree"
                )
        text = decision.strip()
        if comparison_task_id:
            text += f" (based on comparison {comparison_task_id})"
        return self.memory.record_decision(
            room_id,
            author_user_id=author,
            decision=text,
            agreed_by_user_ids=agreed,
        )

    def answer_compare_command(self, room_id: str, *, requested_by_user_id: str) -> str:
        """Run the comparison and render the one answer for the room."""
        room = self.rooms.get_room(room_id)
        comparison = self.compare(room_id, requested_by_user_id=requested_by_user_id)
        return render_group_comparison(comparison, room_name=room.name if room else room_id)


def render_group_comparison(comparison: GroupComparison, *, room_name: str = "room") -> str:
    """Render one comparison as plain text for the group chat.

    Every price is printed with its evidence id and verdict;
    unchecked links are listed with their reason. A price is
    never printed without its evidence. Pure function: no
    database access.
    """
    lines = [f"Group comparison: {room_name}"]
    if not comparison.items:
        lines.append("No product links collected yet. Share links in the chat and ask again.")
        return "\n".join(lines)
    for index, item in enumerate(comparison.items, start=1):
        lines.append(f"{index}. {item.link.display_name()} — {item.link.source} — {item.link.url}")
        if not item.checked:
            if item.reason == "tos_gate_held":
                lines.append(
                    "   Could not be checked: live reading of this marketplace is held "
                    "until decision 15 (the ToS gate) is decided. No price is shown."
                )
            else:
                lines.append(f"   Could not be checked: {item.reason}. No price is shown.")
            continue
        if item.price is None:
            lines.append(
                "   Could not be checked: the reader returned no price. No price is shown."
            )
            continue
        lines.append(
            f"   Price: {_format_rupiah(item.price)} (evidence {item.evidence_id}, {item.verdict})"
        )
        lines.append(f"   Seller: {item.seller_display()}")
        if item.store_rating is not None:
            lines.append(f"   Store rating: {item.store_rating}")
        if item.shipping:
            lines.append(f"   Shipping: {_format_rupiah(item.shipping)}")
        if item.verdict != "Verified":
            lines.append(
                "   Note: this price is not verified — the independent re-read "
                "did not confirm it, so it does not count as the cheapest."
            )
    cheapest = comparison.cheapest
    if cheapest is not None and cheapest.price is not None:
        lines.append(
            f"Cheapest verified: {cheapest.link.display_name()} at "
            f"{_format_rupiah(cheapest.price)} — {cheapest.link.url} "
            f"(evidence {cheapest.evidence_id})"
        )
        if comparison.spread:
            lines.append(
                f"Price spread across verified options: {_format_rupiah(comparison.spread)}"
            )
    else:
        lines.append("No verified price yet — the group cannot decide on price alone.")
    if comparison.unchecked_count:
        lines.append(
            f"{comparison.unchecked_count} of {len(comparison.items)} link(s) "
            "could not be checked; see the reasons above."
        )
    return "\n".join(lines)


def _service(dsn=None) -> ResearchService:
    return ResearchService(dsn)


def add_research_link(
    room_id: str, *, added_by_user_id: str, url: str, label: str | None = None, dsn=None
) -> ResearchLink:
    return _service(dsn).add_link(room_id, added_by_user_id=added_by_user_id, url=url, label=label)


def list_research_links(room_id: str, *, viewer_user_id: str, dsn=None) -> list[ResearchLink]:
    return _service(dsn).list_links(room_id, viewer_user_id=viewer_user_id)


def remove_research_link(link_id: str, *, removed_by: str, dsn=None) -> bool:
    return _service(dsn).remove_link(link_id, removed_by=removed_by)


def compare_group_links(
    room_id: str,
    *,
    requested_by_user_id: str,
    reader=None,
    reread=None,
    mode: str = "fixture",
    task_id: str | None = None,
    dsn=None,
) -> GroupComparison:
    return ResearchService(dsn, reader=reader, reread=reread, mode=mode).compare(
        room_id, requested_by_user_id=requested_by_user_id, task_id=task_id
    )


def record_group_decision(
    room_id: str,
    *,
    author_user_id: str,
    decision: str,
    agreed_by_user_ids: list[str] | None = None,
    comparison_task_id: str | None = None,
    dsn=None,
) -> RoomMemory:
    return _service(dsn).record_group_decision(
        room_id,
        author_user_id=author_user_id,
        decision=decision,
        agreed_by_user_ids=agreed_by_user_ids,
        comparison_task_id=comparison_task_id,
    )


def answer_compare_command(room_id: str, *, requested_by_user_id: str, dsn=None) -> str:
    return _service(dsn).answer_compare_command(room_id, requested_by_user_id=requested_by_user_id)
