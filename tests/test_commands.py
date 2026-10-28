from bridge.commands import render_command
from bridge.dispatcher import process_task
from bridge.parsing import parse_event
from bridge.store import Store
from tests.events import make_event
from tests.fakes import FakeFeishu, FakeWebhook


def test_help_ping_status_and_history(settings) -> None:
    store = Store(settings.db_path)
    parsed = parse_event(make_event(text="summarize the notes"))
    assert parsed is not None
    task_id = store.record(parsed, kind="task", text=parsed.text)
    assert task_id is not None
    store.mark_dispatched(task_id)

    assert "/help" in render_command("/help", store=store, chat_id="oc_1")
    assert render_command("/ping", store=store, chat_id="oc_1").startswith("pong ")
    status = render_command("/status", store=store, chat_id="oc_1")
    assert "處理中" in status
    assert "summarize the notes" in status
    history = render_command("/history", store=store, chat_id="oc_1")
    assert "summarize the notes" in history
    store.close()


def test_commands_do_not_call_the_webhook(settings) -> None:
    store = Store(settings.db_path)
    parsed = parse_event(make_event(text="/ping"))
    assert parsed is not None
    task_id = store.record(parsed, kind="command", text="/ping")
    task = store.get_task(task_id)
    assert task is not None
    feishu = FakeFeishu()
    webhook = FakeWebhook()
    process_task(task, settings=settings, store=store, feishu=feishu, webhook=webhook)
    assert webhook.payloads == []
    assert feishu.replies[0]["text"].startswith("pong ")
    updated = store.get_task(task_id)
    assert updated is not None
    assert updated.status == "done"
    store.close()
