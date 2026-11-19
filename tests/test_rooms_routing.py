"""Room routing: group messages to the room principal (B6).

No live Telegram or WhatsApp bot is involved here: the adapters
are fakes recording what would be sent, and the agent is a fake
returning a canned reply — the same MockTransport-level of
testing as G14. Tests run against the Postgres test database
named by the ``test_dsn`` fixture (env PUSPA_TEST_DSN). The room
schema is applied by the autouse fixture from tests.rooms_helpers.
"""

import pytest

from puspa.channels.authz import ChannelPairing
from puspa.channels.events import MessageEvent, mentions_puspa
from puspa.channels.inbound import handle_inbound
from puspa.channels.ledger import InMemoryDeliveryLedger
from puspa.rooms import RoomChannelLinker, handle_group_inbound, is_addressed_to_puspa
from puspa.rooms.errors import NotRoomMemberError
from tests.rooms_helpers import _rooms_schema, add_room_member, make_room

# The imported names are fixtures used by pytest injection (not
# by direct call); listing them keeps ruff from flagging the
# imports as unused or shadowed by the test parameters.
__all__ = ["_rooms_schema", "add_room_member", "make_room"]


class FakeAdapter:
    """A channel adapter that records sent texts instead of sending."""

    platform = "telegram"

    def __init__(self):
        self.sent = []

    def parse_update(self, payload):
        raise NotImplementedError

    def send_text(self, chat_id, text):
        self.sent.append((chat_id, text))
        return {"ok": True}


class FakeAgent:
    """An agent that records what it was asked, with which principal."""

    def __init__(self, reply="room reply"):
        self.reply = reply
        self.calls = []

    def answer(self, event, *, user_id):
        self.calls.append((event, user_id))
        return self.reply


@pytest.fixture
def linker(test_dsn):
    return RoomChannelLinker(test_dsn)


def _group_event(**overrides):
    event = {
        "platform": "telegram",
        "chat_id": "-100123",
        "sender_id": "user-1",
        "text": "@Puspa summarise the chat",
        "message_id": "m1",
        "is_group": True,
        "mentions_bot": True,
    }
    event.update(overrides)
    return MessageEvent(**event)


def test_mentions_puspa_text_scan():
    assert mentions_puspa("@Puspa hello") is True
    assert mentions_puspa("hi @PUSPA!") is True
    assert mentions_puspa("no mention here") is False
    assert mentions_puspa("@puspabot hello") is False  # @puspa + word char is not a mention


def test_is_addressed_to_puspa():
    assert is_addressed_to_puspa(_group_event()) is True
    assert is_addressed_to_puspa(_group_event(mentions_bot=False)) is True  # text scan
    assert is_addressed_to_puspa(_group_event(text="just chatting", mentions_bot=False)) is False
    assert is_addressed_to_puspa(_group_event(text="just chatting", mentions_bot=True)) is True


def test_link_and_resolve_room(make_room, linker):
    made = make_room()
    link = linker.link_channel(
        made["room"].id,
        platform="telegram",
        chat_id="-100123",
        linked_by=made["owner"]["user_id"],
    )
    assert link.room_id == made["room"].id
    assert linker.resolve_room(platform="telegram", chat_id="-100123") == made["room"].id
    assert linker.resolve_room(platform="telegram", chat_id="-999") is None
    assert [link.chat_id for link in linker.list_links(made["room"].id)] == ["-100123"]


def test_link_by_non_member_is_refused(make_room, make_user, linker):
    made = make_room()
    outsider = make_user()
    with pytest.raises(NotRoomMemberError):
        linker.link_channel(
            made["room"].id, platform="telegram", chat_id="-100123", linked_by=outsider["user_id"]
        )


def test_link_rejects_unknown_platform(make_room, linker):
    made = make_room()
    with pytest.raises(ValueError):
        linker.link_channel(
            made["room"].id,
            platform="signal",
            chat_id="-100123",
            linked_by=made["owner"]["user_id"],
        )


def test_unlink_removes_link(make_room, linker):
    made = make_room()
    linker.link_channel(
        made["room"].id,
        platform="telegram",
        chat_id="-100123",
        linked_by=made["owner"]["user_id"],
    )
    assert (
        linker.unlink_channel(
            platform="telegram", chat_id="-100123", unlinked_by=made["owner"]["user_id"]
        )
        is True
    )
    assert linker.resolve_room(platform="telegram", chat_id="-100123") is None
    assert (
        linker.unlink_channel(
            platform="telegram", chat_id="-100123", unlinked_by=made["owner"]["user_id"]
        )
        is False
    )


def test_unlink_by_non_member_is_refused(make_room, make_user, linker):
    made = make_room()
    outsider = make_user()
    linker.link_channel(
        made["room"].id,
        platform="telegram",
        chat_id="-100123",
        linked_by=made["owner"]["user_id"],
    )
    with pytest.raises(NotRoomMemberError):
        linker.unlink_channel(
            platform="telegram", chat_id="-100123", unlinked_by=outsider["user_id"]
        )


def test_group_message_answered_with_room_as_principal(make_room, linker):
    made = make_room()
    linker.link_channel(
        made["room"].id,
        platform="telegram",
        chat_id="-100123",
        linked_by=made["owner"]["user_id"],
    )
    adapter = FakeAdapter()
    agent = FakeAgent()
    ledger = InMemoryDeliveryLedger()
    outcome = handle_group_inbound(
        _group_event(), adapter=adapter, agent=agent, linker=linker, ledger=ledger
    )
    assert outcome.outcome == "answered"
    assert outcome.user_id == f"room:{made['room'].id}"  # the room is the principal
    assert outcome.reply == "room reply"
    assert agent.calls[0][1] == f"room:{made['room'].id}"
    assert adapter.sent == [("-100123", "room reply")]


def test_unlinked_group_is_dropped(linker):
    adapter = FakeAdapter()
    agent = FakeAgent()
    outcome = handle_group_inbound(_group_event(), adapter=adapter, agent=agent, linker=linker)
    assert outcome.outcome == "dropped_group"
    assert agent.calls == []
    assert adapter.sent == []


def test_group_chatter_without_mention_is_dropped(make_room, linker):
    made = make_room()
    linker.link_channel(
        made["room"].id,
        platform="telegram",
        chat_id="-100123",
        linked_by=made["owner"]["user_id"],
    )
    adapter = FakeAdapter()
    agent = FakeAgent()
    outcome = handle_group_inbound(
        _group_event(text="just chatting", mentions_bot=False),
        adapter=adapter,
        agent=agent,
        linker=linker,
    )
    assert outcome.outcome == "dropped_not_addressed"
    assert agent.calls == []
    assert adapter.sent == []


def test_bot_sender_in_group_is_dropped(make_room, linker):
    made = make_room()
    linker.link_channel(
        made["room"].id,
        platform="telegram",
        chat_id="-100123",
        linked_by=made["owner"]["user_id"],
    )
    adapter = FakeAdapter()
    agent = FakeAgent()
    outcome = handle_group_inbound(
        _group_event(is_bot=True), adapter=adapter, agent=agent, linker=linker
    )
    assert outcome.outcome == "dropped_bot"
    assert agent.calls == []


def test_duplicate_group_message_is_dropped_once(make_room, linker):
    made = make_room()
    linker.link_channel(
        made["room"].id,
        platform="telegram",
        chat_id="-100123",
        linked_by=made["owner"]["user_id"],
    )
    adapter = FakeAdapter()
    agent = FakeAgent()
    ledger = InMemoryDeliveryLedger()
    first = handle_group_inbound(
        _group_event(), adapter=adapter, agent=agent, linker=linker, ledger=ledger
    )
    second = handle_group_inbound(
        _group_event(), adapter=adapter, agent=agent, linker=linker, ledger=ledger
    )
    assert first.outcome == "answered"
    assert second.outcome == "dropped_duplicate"
    assert len(agent.calls) == 1


def test_non_group_event_returns_none(linker):
    adapter = FakeAdapter()
    agent = FakeAgent()
    assert (
        handle_group_inbound(
            _group_event(is_group=False), adapter=adapter, agent=agent, linker=linker
        )
        is None
    )


def test_pipeline_routes_group_via_handler_and_drops_without_one(make_room, linker):
    made = make_room()
    linker.link_channel(
        made["room"].id,
        platform="telegram",
        chat_id="-100123",
        linked_by=made["owner"]["user_id"],
    )
    adapter = FakeAdapter()
    agent = FakeAgent()
    pairing = ChannelPairing()

    def group_handler(event, *, adapter, agent, ledger, delivery):
        return handle_group_inbound(
            event, adapter=adapter, agent=agent, linker=linker, ledger=ledger, delivery=delivery
        )

    outcome = handle_inbound(
        _group_event(), adapter=adapter, agent=agent, pairing=pairing, group_handler=group_handler
    )
    assert outcome.outcome == "answered"
    assert outcome.user_id == f"room:{made['room'].id}"
    # Without a handler, the same event is dropped exactly as before (G14).
    dropped = handle_inbound(_group_event(), adapter=adapter, agent=agent, pairing=pairing)
    assert dropped.outcome == "dropped_group"
    assert len(agent.calls) == 1


def test_pipeline_group_handler_declining_falls_through_to_drop(linker):
    adapter = FakeAdapter()
    agent = FakeAgent()
    pairing = ChannelPairing()
    outcome = handle_inbound(
        _group_event(),
        adapter=adapter,
        agent=agent,
        pairing=pairing,
        group_handler=lambda event, **kwargs: None,
    )
    assert outcome.outcome == "dropped_group"


def test_telegram_adapter_marks_group_and_mention():
    from puspa.channels.telegram import TelegramAdapter

    adapter = TelegramAdapter("fake-token")
    events = adapter.parse_update(
        {
            "update_id": 1,
            "message": {
                "message_id": 5,
                "date": 1700000000,
                "chat": {"id": -100123, "type": "supergroup", "title": "Family"},
                "from": {"id": 42, "is_bot": False},
                "text": "@Puspa summarise this",
                "entities": [{"type": "mention", "offset": 0, "length": 6}],
            },
        }
    )
    assert len(events) == 1
    assert events[0].is_group is True
    assert events[0].mentions_bot is True
    assert events[0].chat_id == "-100123"


def test_telegram_adapter_dm_has_no_group_or_mention():
    from puspa.channels.telegram import TelegramAdapter

    adapter = TelegramAdapter("fake-token")
    events = adapter.parse_update(
        {
            "update_id": 2,
            "message": {
                "message_id": 6,
                "date": 1700000000,
                "chat": {"id": 111, "type": "private"},
                "from": {"id": 42, "is_bot": False},
                "text": "hello",
            },
        }
    )
    assert events[0].is_group is False
    assert events[0].mentions_bot is False


def test_whatsapp_adapter_marks_mention_but_never_group():
    from puspa.channels.whatsapp import WhatsAppAdapter

    adapter = WhatsAppAdapter("fake-token", "12345")
    events = adapter.parse_update(
        {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "messages": [
                                    {
                                        "id": "w1",
                                        "from": "628123456789",
                                        "timestamp": "1700000000",
                                        "type": "text",
                                        "text": {"body": "@Puspa what is the price?"},
                                    }
                                ]
                            }
                        }
                    ]
                }
            ]
        }
    )
    assert len(events) == 1
    assert events[0].is_group is False  # Cloud API has no groups (plan 12.4)
    assert events[0].mentions_bot is True
