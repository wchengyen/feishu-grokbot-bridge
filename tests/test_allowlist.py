import queue

from bridge.store import Store
from bridge.ws_client import MessageHandler
from tests.events import make_event

MENTION_BOT = [
    {
        "key": "@_user_1",
        "id": {"open_id": "ou_bot", "user_id": "", "union_id": ""},
        "name": "Javis",
        "tenant_key": "t",
    }
]


def _run(settings, store, event) -> queue.Queue[str]:
    handler = MessageHandler(settings, store, queue.Queue())
    handler.handle(event)
    return handler._queue


def test_empty_allowlist_rejects_everyone(settings, env, tmp_path) -> None:
    from bridge.config import load_settings

    env["FEISHU_ALLOWED_OPEN_IDS"] = ""
    settings = load_settings(env)
    store = Store(tmp_path / "bridge.db")
    queued = _run(settings, store, make_event(open_id="ou_owner", text="hi"))
    assert queued.qsize() == 0
    store.close()


def test_sender_outside_allowlist_is_dropped(settings, tmp_path) -> None:
    store = Store(tmp_path / "bridge.db")
    queued = _run(settings, store, make_event(open_id="ou_stranger", text="hi"))
    assert queued.qsize() == 0
    store.close()


def test_allowed_dm_is_enqueued(settings, tmp_path) -> None:
    store = Store(tmp_path / "bridge.db")
    queued = _run(settings, store, make_event(text="do the thing"))
    assert queued.qsize() == 1
    store.close()


def test_bot_sender_is_ignored(settings, tmp_path) -> None:
    store = Store(tmp_path / "bridge.db")
    queued = _run(settings, store, make_event(sender_type="bot", open_id="ou_owner", text="echo"))
    assert queued.qsize() == 0
    store.close()


def test_group_requires_bot_mention(settings, tmp_path) -> None:
    store = Store(tmp_path / "bridge.db")
    missed = _run(
        settings,
        store,
        make_event(chat_type="group", text="hello", message_id="om_a", event_id="evt_a"),
    )
    hit = _run(
        settings,
        store,
        make_event(
            chat_type="group",
            text="@_user_1 hello",
            mentions=MENTION_BOT,
            message_id="om_b",
            event_id="evt_b",
        ),
    )
    assert missed.qsize() == 0
    assert hit.qsize() == 1
    store.close()


def test_group_mention_of_someone_else_is_dropped(settings, tmp_path) -> None:
    store = Store(tmp_path / "bridge.db")
    mentions = [
        {
            "key": "@_user_1",
            "id": {"open_id": "ou_other", "user_id": "", "union_id": ""},
            "name": "Other",
            "tenant_key": "t",
        }
    ]
    queued = _run(
        settings,
        store,
        make_event(chat_type="group", text="@_user_1 hi", mentions=mentions),
    )
    assert queued.qsize() == 0
    store.close()
