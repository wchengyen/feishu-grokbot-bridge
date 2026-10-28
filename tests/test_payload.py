from bridge.dispatcher import process_task
from bridge.parsing import parse_event
from bridge.protocol import build_task_payload
from bridge.store import Store
from tests.events import make_event
from tests.fakes import FakeFeishu, FakeWebhook


def test_payload_matches_protocol_and_excludes_current_turn(settings, tmp_path) -> None:
    store = Store(tmp_path / "bridge.db")
    previous = make_event(event_id="evt_old", message_id="om_old", text="earlier question")
    store.record(parse_event(previous), kind="task", text="earlier question")
    store.append_history(
        chat_id="oc_1",
        role="assistant",
        text="earlier answer",
        ts="2020-01-01T00:00:00+00:00",
        message_id="reply-old",
    )
    current = parse_event(make_event(event_id="evt_new", message_id="om_new", text="do this"))
    assert current is not None
    task_id = store.record(current, kind="task", text=current.text)
    assert task_id is not None
    task = store.get_task(task_id)
    assert task is not None
    history = store.recent_history("oc_1", settings.history_turns, exclude_message_id="om_new")
    payload = build_task_payload(task, history, settings.outbox_dir)
    assert payload["version"] == 1
    assert payload["source"] == "feishu"
    assert payload["task_id"] == task_id
    assert payload["chat_type"] == "p2p"
    assert payload["text"] == "do this"
    assert payload["sender"] == {"open_id": "ou_owner", "name": ""}
    assert payload["reply_via"]["type"] == "outbox"
    assert payload["reply_via"]["dir"].endswith("/outbox")
    assert payload["reply_via"]["dir"].startswith("/")
    roles = [(item["role"], item["text"]) for item in payload["history"]]
    assert ("user", "do this") not in roles
    assert ("user", "earlier question") in roles
    assert ("assistant", "earlier answer") in roles
    store.close()


def test_history_is_capped(settings, env, tmp_path) -> None:
    from bridge.config import load_settings

    env["BRIDGE_HISTORY_TURNS"] = "2"
    settings = load_settings(env)
    store = Store(tmp_path / "bridge.db")
    for index in range(5):
        store.append_history(
            chat_id="oc_1",
            role="user",
            text=f"turn-{index}",
            ts=f"2020-01-01T00:00:0{index}+00:00",
            message_id=f"m{index}",
        )
    history = store.recent_history("oc_1", settings.history_turns)
    assert [item["text"] for item in history] == ["turn-3", "turn-4"]
    store.close()


def test_dispatcher_posts_payload_after_ack_card(settings, tmp_path) -> None:
    store = Store(settings.db_path)
    parsed = parse_event(make_event(text="ship the summary"))
    assert parsed is not None
    task_id = store.record(parsed, kind="task", text=parsed.text)
    assert task_id is not None
    task = store.get_task(task_id)
    assert task is not None
    feishu = FakeFeishu()
    webhook = FakeWebhook()
    process_task(task, settings=settings, store=store, feishu=feishu, webhook=webhook)
    assert feishu.replies[0]["msg_type"] == "interactive"
    assert feishu.replies[0]["card"]["header"]["title"]["content"] == "已收到，處理中…"
    assert webhook.payloads[0]["task_id"] == task_id
    assert webhook.payloads[0]["text"] == "ship the summary"
    updated = store.get_task(task_id)
    assert updated is not None
    assert updated.status == "dispatched"
    assert updated.card_message_id == "om_card"
    store.close()


def test_dispatcher_patches_error_card_when_webhook_fails(settings) -> None:
    store = Store(settings.db_path)
    parsed = parse_event(make_event(text="ship it"))
    assert parsed is not None
    task_id = store.record(parsed, kind="task", text=parsed.text)
    task = store.get_task(task_id)
    assert task is not None
    feishu = FakeFeishu()
    webhook = FakeWebhook(fail=True)
    process_task(task, settings=settings, store=store, feishu=feishu, webhook=webhook)
    assert webhook.payloads == []
    assert feishu.patches[0]["card"]["header"]["template"] == "red"
    updated = store.get_task(task_id)
    assert updated is not None
    assert updated.status == "failed"
    store.close()
