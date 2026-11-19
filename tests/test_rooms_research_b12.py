"""Group research and shopping: a room collects product links,
the agent compares them with evidence per item, and the group
decides in chat (B12).

All readings use a fake reader (no network) plus an independent
fake re-read. ``fixture`` mode tests the comparison logic;
``live`` mode tests the ToS gate (the reader is never called).
Tests run against the Postgres test database named by the
``test_dsn`` fixture (env PUSPA_TEST_DSN). The room schema is
applied by the autouse fixture from tests.rooms_helpers.
"""

import psycopg
import pytest

from puspa.pricing.readers import ResultRead
from puspa.rooms import ResearchService, list_room_memory
from puspa.rooms.errors import NotRoomMemberError
from puspa.rooms.research import (
    detect_source,
    extract_product_links,
    is_compare_command,
    render_group_comparison,
)
from tests.rooms_helpers import _rooms_schema, add_room_member, make_room

# The imported names are fixtures used by pytest injection (not
# by direct call); listing them keeps ruff from flagging the
# imports as unused or shadowed by the test parameters.
__all__ = ["_rooms_schema", "add_room_member", "make_room"]

URL_TOPED = "https://www.tokopedia.com/toko/galaxy-s24"
URL_SHOPEE = "https://shopee.co.id/galaxy-s24"
URL_BLIBLI = "https://www.blibli.com/p/galaxy-s24"
NAME = "Samsung Galaxy S24"

# url -> (price, seller, product name)
PRICES = {
    URL_TOPED: (12_999_000.0, "Toko Galaxy", NAME),
    URL_SHOPEE: (12_499_000.0, "Shopee Mall Cell", NAME),
    URL_BLIBLI: (12_750_000.0, "Blibli Official Store", NAME),
}


class FakeReader:
    """A reader that returns fixed prices per URL (no network)."""

    def __init__(self, prices=None, fail_urls=()):
        self.prices = dict(PRICES if prices is None else prices)
        self.fail_urls = set(fail_urls)
        self.calls = []

    def read(self, url):
        self.calls.append(url)
        if url in self.fail_urls:
            return ResultRead(
                success=False,
                marketplace=detect_source(url),
                listing_url=url,
                reason="source_blocked_http_403",
            )
        price, seller, name = self.prices[url]
        return ResultRead(
            success=True,
            marketplace=detect_source(url),
            listing_url=url,
            price=price,
            shipping=15000,
            store_rating=4.9,
            name=name,
            seller=seller,
            text_excerpt=f"[DATA: {name} Rp{int(price)}]",
        )


def _fake_reread(url):
    price, _seller, name = PRICES[url]
    return {"value": price, "name": name}


@pytest.fixture
def service(test_dsn):
    return ResearchService(test_dsn, reader=FakeReader(), reread=_fake_reread, mode="fixture")


def _collect_three(service, made):
    owner = made["owner"]["user_id"]
    return [
        service.add_link(made["room"].id, added_by_user_id=owner, url=URL_TOPED, label="Option A"),
        service.add_link(made["room"].id, added_by_user_id=owner, url=URL_SHOPEE, label="Option B"),
        service.add_link(made["room"].id, added_by_user_id=owner, url=URL_BLIBLI, label="Option C"),
    ]


# --- collecting links ---


def test_add_link_stores_source_label_and_is_idempotent(make_room, service):
    made = make_room()
    link = service.add_link(
        made["room"].id,
        added_by_user_id=made["owner"]["user_id"],
        url=URL_SHOPEE,
        label="Option B",
    )
    assert link.source == "Shopee"
    assert link.label == "Option B"
    assert link.added_by_user_id == made["owner"]["user_id"]
    again = service.add_link(
        made["room"].id,
        added_by_user_id=made["owner"]["user_id"],
        url=URL_SHOPEE,
        label="Option B again",
    )
    assert again.id == link.id
    assert len(service.list_links(made["room"].id, viewer_user_id=made["owner"]["user_id"])) == 1


def test_add_link_rejects_non_member_bad_url_and_other_hosts(make_room, make_user, service):
    made = make_room()
    outsider = make_user()
    with pytest.raises(NotRoomMemberError):
        service.add_link(made["room"].id, added_by_user_id=outsider["user_id"], url=URL_SHOPEE)
    with pytest.raises(ValueError):
        service.add_link(
            made["room"].id,
            added_by_user_id=made["owner"]["user_id"],
            url="not a url",
        )
    with pytest.raises(ValueError):
        service.add_link(
            made["room"].id,
            added_by_user_id=made["owner"]["user_id"],
            url="https://example.com/product/1",
        )


def test_list_links_member_only_and_oldest_first(make_room, add_room_member, make_user, service):
    made = make_room()
    member = add_room_member(made["room"].id, made["owner"]["user_id"])
    outsider = make_user()
    _collect_three(service, made)
    links = service.list_links(made["room"].id, viewer_user_id=member["user_id"])
    assert [link.url for link in links] == [URL_TOPED, URL_SHOPEE, URL_BLIBLI]
    with pytest.raises(NotRoomMemberError):
        service.list_links(made["room"].id, viewer_user_id=outsider["user_id"])


def test_remove_link_author_owner_and_refusals(make_room, add_room_member, service):
    made = make_room()
    member = add_room_member(made["room"].id, made["owner"]["user_id"])
    links = _collect_three(service, made)
    # Another member may not remove the owner's link.
    with pytest.raises(NotRoomMemberError):
        service.remove_link(links[0].id, removed_by=member["user_id"])
    # The owner may remove any link; a missing id returns False.
    assert service.remove_link(links[0].id, removed_by=made["owner"]["user_id"]) is True
    assert service.remove_link(links[0].id, removed_by=made["owner"]["user_id"]) is False
    # The author may remove their own link.
    own = service.add_link(
        made["room"].id,
        added_by_user_id=member["user_id"],
        url="https://www.tokopedia.com/toko/galaxy-s24-plus",
    )
    assert service.remove_link(own.id, removed_by=member["user_id"]) is True


def test_detect_source_and_extract_links():
    assert detect_source(URL_TOPED) == "Tokopedia"
    assert detect_source("https://www.shopee.co.id/x") == "Shopee"
    assert detect_source("https://tokopedia.com.evil.com/x") == "other"
    assert detect_source("garbage") == "other"
    text = (
        "look at this https://www.tokopedia.com/toko/galaxy-s24 and "
        "https://shopee.co.id/galaxy-s24, ignore https://example.com/x "
        "and https://www.tokopedia.com/toko/galaxy-s24 again."
    )
    assert extract_product_links(text) == [URL_TOPED, URL_SHOPEE]
    assert extract_product_links(None) == []
    assert extract_product_links("no links here") == []


def test_is_compare_command():
    assert is_compare_command("@Puspa compare") is True
    assert is_compare_command("@puspa bandingkan harga") is True
    assert is_compare_command("@Puspa compare prices!") is True
    assert is_compare_command("@Puspa summarise the chat") is False
    assert is_compare_command("just chatting") is False
    assert is_compare_command(None) is False


# --- comparing ---


def test_compare_fixture_three_links_verified_with_evidence(make_room, service, test_dsn):
    made = make_room()
    _collect_three(service, made)
    comparison = service.compare(made["room"].id, requested_by_user_id=made["owner"]["user_id"])
    assert comparison.mode == "fixture"
    assert len(comparison.items) == 3
    for item in comparison.items:
        assert item.checked is True
        assert item.verdict == "Verified"
        assert item.evidence_id
        assert item.seller
    assert comparison.cheapest.link.url == URL_SHOPEE
    assert comparison.cheapest.price == 12_499_000.0
    assert comparison.spread == 500_000.0
    # Evidence rows are saved, one per checked link.
    with psycopg.connect(test_dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM evidence WHERE task_id = %s", (comparison.task_id,))
        assert cur.fetchone()[0] == 3
    rendered = render_group_comparison(comparison, room_name=made["room"].name)
    for url, (_price, seller, _name) in PRICES.items():
        assert url in rendered
        assert seller in rendered
    assert "Rp 12.499.000" in rendered
    assert "Cheapest verified" in rendered
    assert "Shopee" in rendered


def test_compare_one_unreadable_still_presents_the_rest(make_room, test_dsn):
    reader = FakeReader(fail_urls={URL_BLIBLI})
    service = ResearchService(test_dsn, reader=reader, reread=_fake_reread, mode="fixture")
    made = make_room()
    _collect_three(service, made)
    comparison = service.compare(made["room"].id, requested_by_user_id=made["owner"]["user_id"])
    assert comparison.items[2].checked is False
    assert comparison.items[2].reason == "source_blocked_http_403"
    assert comparison.cheapest.link.url == URL_SHOPEE
    rendered = render_group_comparison(comparison)
    assert "Could not be checked: source_blocked_http_403" in rendered
    assert "1 of 3 link(s) could not be checked" in rendered


def test_compare_unverified_price_never_wins_cheapest(make_room, test_dsn):
    def reread_wrong_shopee(url):
        if url == URL_SHOPEE:
            return {"value": 9_999_000.0, "name": NAME}
        return _fake_reread(url)

    service = ResearchService(
        test_dsn, reader=FakeReader(), reread=reread_wrong_shopee, mode="fixture"
    )
    made = make_room()
    _collect_three(service, made)
    comparison = service.compare(made["room"].id, requested_by_user_id=made["owner"]["user_id"])
    shopee = next(i for i in comparison.items if i.link.url == URL_SHOPEE)
    assert shopee.checked is True
    assert shopee.verdict == "Uncertain"
    assert comparison.cheapest.link.url == URL_BLIBLI
    assert comparison.spread == 249_000.0
    rendered = render_group_comparison(comparison)
    assert "not verified" in rendered
    assert "does not count as the cheapest" in rendered


def test_compare_live_mode_held_by_tos_gate_reader_never_called(make_room, test_dsn):
    reader = FakeReader()
    service = ResearchService(test_dsn, reader=reader, reread=_fake_reread, mode="live")
    made = make_room()
    _collect_three(service, made)
    comparison = service.compare(made["room"].id, requested_by_user_id=made["owner"]["user_id"])
    assert reader.calls == []
    assert all(not item.checked for item in comparison.items)
    assert all(item.reason == "tos_gate_held" for item in comparison.items)
    assert comparison.cheapest is None
    rendered = render_group_comparison(comparison)
    assert "held until decision 15" in rendered
    assert "No verified price yet" in rendered
    assert "Rp 12.499.000" not in rendered


def test_compare_empty_room_says_so(make_room, service):
    made = make_room()
    comparison = service.compare(made["room"].id, requested_by_user_id=made["owner"]["user_id"])
    assert comparison.items == []
    assert comparison.cheapest is None
    assert "No product links collected yet" in render_group_comparison(comparison)


def test_compare_without_reread_nothing_verifies(make_room, test_dsn):
    service = ResearchService(test_dsn, reader=FakeReader(), reread=None, mode="fixture")
    made = make_room()
    _collect_three(service, made)
    comparison = service.compare(made["room"].id, requested_by_user_id=made["owner"]["user_id"])
    assert all(item.verdict == "Cannot be determined" for item in comparison.items)
    assert comparison.cheapest is None


def test_compare_unknown_seller_is_said_not_invented(make_room, test_dsn):
    class NoSellerReader(FakeReader):
        def read(self, url):
            result = super().read(url)
            return ResultRead(
                success=result.success,
                marketplace=result.marketplace,
                listing_url=result.listing_url,
                price=result.price,
                shipping=result.shipping,
                store_rating=result.store_rating,
                name=result.name,
                seller="",
                text_excerpt=result.text_excerpt,
            )

    service = ResearchService(
        test_dsn, reader=NoSellerReader(), reread=_fake_reread, mode="fixture"
    )
    made = make_room()
    service.add_link(
        made["room"].id, added_by_user_id=made["owner"]["user_id"], url=URL_SHOPEE, label="Option B"
    )
    comparison = service.compare(made["room"].id, requested_by_user_id=made["owner"]["user_id"])
    assert comparison.items[0].seller_display() == "unknown (the reader did not report one)"
    assert "Seller: unknown (the reader did not report one)" in render_group_comparison(comparison)


def test_compare_by_non_member_is_refused(make_room, make_user, service):
    made = make_room()
    outsider = make_user()
    with pytest.raises(NotRoomMemberError):
        service.compare(made["room"].id, requested_by_user_id=outsider["user_id"])


# --- the group decision ---


def test_record_group_decision_names_who_agreed(make_room, add_room_member, service):
    made = make_room()
    member = add_room_member(made["room"].id, made["owner"]["user_id"])
    decision = service.record_group_decision(
        made["room"].id,
        author_user_id=made["owner"]["user_id"],
        decision="Buy Option B from Shopee Mall Cell.",
        agreed_by_user_ids=[made["owner"]["user_id"], member["user_id"]],
        comparison_task_id="research-abc123",
    )
    assert decision.kind == "decision"
    assert "Buy Option B from Shopee Mall Cell." in decision.content
    assert made["owner"]["user_id"] in decision.content
    assert member["user_id"] in decision.content
    assert "research-abc123" in decision.content
    listed = list_room_memory(
        made["room"].id, viewer_user_id=member["user_id"], kind="decision", dsn=service.dsn
    )
    assert [m.id for m in listed] == [decision.id]


def test_record_group_decision_rejects_non_member_agreer(
    make_room, add_room_member, make_user, service
):
    made = make_room()
    add_room_member(made["room"].id, made["owner"]["user_id"])
    outsider = make_user()
    with pytest.raises(NotRoomMemberError):
        service.record_group_decision(
            made["room"].id,
            author_user_id=made["owner"]["user_id"],
            decision="Buy it.",
            agreed_by_user_ids=[outsider["user_id"]],
        )
    # No decision was recorded.
    assert (
        list_room_memory(
            made["room"].id,
            viewer_user_id=made["owner"]["user_id"],
            kind="decision",
            dsn=service.dsn,
        )
        == []
    )


def test_record_group_decision_empty_agreement_claims_nothing(make_room, service):
    made = make_room()
    decision = service.record_group_decision(
        made["room"].id,
        author_user_id=made["owner"]["user_id"],
        decision="Still discussing; no agreement yet.",
        agreed_by_user_ids=[],
    )
    assert "Agreed by" not in decision.content
    with pytest.raises(ValueError):
        service.record_group_decision(
            made["room"].id,
            author_user_id=made["owner"]["user_id"],
            decision="   ",
        )


# --- group chat routing: the compare command answers without the agent ---


def test_compare_command_answers_from_group_chat(make_room, service, test_dsn):
    from puspa.channels.events import MessageEvent
    from puspa.rooms import RoomChannelLinker, handle_group_inbound

    made = make_room()
    _collect_three(service, made)
    linker = RoomChannelLinker(test_dsn)
    linker.link_channel(
        made["room"].id,
        platform="telegram",
        chat_id="-100123",
        linked_by=made["owner"]["user_id"],
    )

    class FakeAdapter:
        platform = "telegram"

        def __init__(self):
            self.sent = []

        def send_text(self, chat_id, text):
            self.sent.append((chat_id, text))
            return {"ok": True}

    class FakeAgent:
        def __init__(self):
            self.calls = []

        def answer(self, event, *, user_id):
            self.calls.append((event, user_id))
            return "room reply"

    adapter = FakeAdapter()
    agent = FakeAgent()
    event = MessageEvent(
        platform="telegram",
        chat_id="-100123",
        sender_id=made["owner"]["user_id"],
        text="@Puspa compare",
        message_id="m-compare",
        is_group=True,
        mentions_bot=True,
    )
    outcome = handle_group_inbound(
        event, adapter=adapter, agent=agent, linker=linker, research=service
    )
    assert outcome is not None and outcome.outcome == "answered"
    assert agent.calls == []  # the comparison is data, not a turn
    assert len(adapter.sent) == 1
    assert "Cheapest verified" in adapter.sent[0][1]
    assert "Rp 12.499.000" in adapter.sent[0][1]
    assert "Shopee Mall Cell" in adapter.sent[0][1]

    # A non-compare message still goes to the agent.
    event2 = MessageEvent(
        platform="telegram",
        chat_id="-100123",
        sender_id=made["owner"]["user_id"],
        text="@Puspa summarise the chat",
        message_id="m-chat",
        is_group=True,
        mentions_bot=True,
    )
    outcome2 = handle_group_inbound(
        event2, adapter=adapter, agent=agent, linker=linker, research=service
    )
    assert outcome2 is not None and outcome2.outcome == "answered"
    assert len(agent.calls) == 1
    assert adapter.sent[-1][1] == "room reply"
