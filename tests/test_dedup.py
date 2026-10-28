import queue

from bridge.store import Store
from bridge.ws_client import MessageHandler, build_event_handler
from tests.events import make_event


def _handler(settings, store):
    return MessageHandler(settings, store, queue.Queue())


def test_duplicate_event_id_is_ignored(settings, tmp_path) -> None:
    store = Store(tmp_path / "bridge.db")
    handler = _handler(settings, store)
    queued: queue.Queue[str] = handler._queue
    event = make_event(event_id="evt_same", message_id="om_1", text="one")
    handler.handle(event)
    handler.handle(event)
    assert queued.qsize() == 1
    store.close()


def test_same_message_id_new_event_id_is_ignored(settings, tmp_path) -> None:
    store = Store(tmp_path / "bridge.db")
    handler = _handler(settings, store)
    handler.handle(make_event(event_id="evt_a", message_id="om_same", text="one"))
    handler.handle(make_event(event_id="evt_b", message_id="om_same", text="two"))
    assert handler._queue.qsize() == 1
    store.close()


def test_distinct_messages_both_enqueue(settings, tmp_path) -> None:
    store = Store(tmp_path / "bridge.db")
    handler = _handler(settings, store)
    handler.handle(make_event(event_id="evt_a", message_id="om_a", text="one"))
    handler.handle(make_event(event_id="evt_b", message_id="om_b", text="two"))
    assert handler._queue.qsize() == 2
    store.close()


def test_sdk_dispatch_path_does_not_raise_on_handler_failure(settings, tmp_path) -> None:
    store = Store(tmp_path / "bridge.db")
    handler = _handler(settings, store)
    store.close()  # the next write fails; the callback must still swallow it
    event_handler = build_event_handler(handler)
    payload = {
        "schema": "2.0",
        "header": {
            "event_id": "evt_x",
            "event_type": "im.message.receive_v1",
            "create_time": "1600000000000",
            "token": "",
            "app_id": "cli_test",
            "tenant_key": "t",
        },
        "event": {
            "sender": {
                "sender_id": {"open_id": "ou_owner"},
                "sender_type": "user",
                "tenant_key": "t",
            },
            "message": {
                "message_id": "om_x",
                "chat_id": "oc_1",
                "chat_type": "p2p",
                "message_type": "text",
                "content": '{"text":"hi"}',
            },
        },
    }
    import json

    event_handler._do_without_validation(json.dumps(payload).encode())
