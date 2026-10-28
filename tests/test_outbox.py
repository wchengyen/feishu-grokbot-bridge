import json
import threading
from pathlib import Path

from bridge.outbox import OutboxWatcher, ensure_outbox, handle_result_path
from bridge.parsing import parse_event
from bridge.store import Store
from tests.events import make_event
from tests.fakes import FakeFeishu


def _task(settings, store, text="question"):
    parsed = parse_event(make_event(text=text))
    assert parsed is not None
    task_id = store.record(parsed, kind="task", text=parsed.text)
    assert task_id is not None
    store.mark_card_sent(task_id, "om_card")
    task = store.get_task(task_id)
    assert task is not None
    return task


def _write(outbox: Path, task_id: str, seq: int, body: dict) -> Path:
    path = outbox / f"{task_id}.{seq}.json"
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


def test_progress_patches_card(settings) -> None:
    store = Store(settings.db_path)
    ensure_outbox(settings.outbox_dir)
    task = _task(settings, store)
    feishu = FakeFeishu()
    _write(
        settings.outbox_dir,
        task.task_id,
        1,
        {"version": 1, "task_id": task.task_id, "kind": "progress", "text": "還在查"},
    )
    result = handle_result_path(
        settings.outbox_dir / f"{task.task_id}.1.json",
        settings=settings,
        store=store,
        feishu=feishu,
    )
    assert result == "processed"
    assert feishu.patches[0]["card"]["header"]["title"]["content"] == "處理中"
    assert "還在查" in feishu.patches[0]["card"]["elements"][0]["text"]["content"]
    assert (settings.outbox_dir / "processed" / f"{task.task_id}.1.json").is_file()
    store.close()


def test_final_patches_done_and_sends_text(settings) -> None:
    store = Store(settings.db_path)
    ensure_outbox(settings.outbox_dir)
    task = _task(settings, store)
    feishu = FakeFeishu()
    _write(
        settings.outbox_dir,
        task.task_id,
        2,
        {"version": 1, "task_id": task.task_id, "kind": "final", "text": "完成了"},
    )
    handle_result_path(
        settings.outbox_dir / f"{task.task_id}.2.json",
        settings=settings,
        store=store,
        feishu=feishu,
    )
    assert feishu.patches[-1]["card"]["header"]["template"] == "green"
    texts = [item["text"] for item in feishu.replies if item["msg_type"] == "text"]
    assert texts == ["完成了"]
    updated = store.get_task(task.task_id)
    assert updated is not None
    assert updated.status == "done"
    history = store.recent_history(task.chat_id, 10)
    assert history[-1]["role"] == "assistant"
    assert history[-1]["text"] == "完成了"
    store.close()


def test_error_patches_red_card(settings) -> None:
    store = Store(settings.db_path)
    ensure_outbox(settings.outbox_dir)
    task = _task(settings, store)
    feishu = FakeFeishu()
    _write(
        settings.outbox_dir,
        task.task_id,
        1,
        {"version": 1, "task_id": task.task_id, "kind": "error", "text": "做不到"},
    )
    handle_result_path(
        settings.outbox_dir / f"{task.task_id}.1.json",
        settings=settings,
        store=store,
        feishu=feishu,
    )
    assert feishu.patches[0]["card"]["header"]["template"] == "red"
    updated = store.get_task(task.task_id)
    assert updated is not None
    assert updated.status == "error"
    store.close()


def test_malformed_file_moves_to_failed(settings) -> None:
    store = Store(settings.db_path)
    ensure_outbox(settings.outbox_dir)
    path = settings.outbox_dir / "not-a-task.json"
    path.write_text("{", encoding="utf-8")
    # Older than the partial-write grace period.
    import os

    os.utime(path, (1, 1))
    result = handle_result_path(path, settings=settings, store=store, feishu=FakeFeishu(), now=100)
    assert result == "failed"
    assert not path.exists()
    assert list((settings.outbox_dir / "failed").glob("*.json"))
    store.close()


def test_fresh_partial_json_is_left_in_place(settings) -> None:
    store = Store(settings.db_path)
    ensure_outbox(settings.outbox_dir)
    task_id = "11111111-1111-1111-1111-111111111111"
    path = settings.outbox_dir / f"{task_id}.1.json"
    path.write_text("{", encoding="utf-8")
    result = handle_result_path(
        path,
        settings=settings,
        store=store,
        feishu=FakeFeishu(),
        now=path.stat().st_mtime,
    )
    assert result == "retry"
    assert path.is_file()
    store.close()


def test_unknown_task_is_failed(settings) -> None:
    store = Store(settings.db_path)
    ensure_outbox(settings.outbox_dir)
    task_id = "11111111-1111-1111-1111-111111111111"
    path = _write(
        settings.outbox_dir,
        task_id,
        1,
        {"version": 1, "task_id": task_id, "kind": "final", "text": "orphan"},
    )
    result = handle_result_path(path, settings=settings, store=store, feishu=FakeFeishu())
    assert result == "failed"
    store.close()


def test_attachment_is_uploaded(settings, tmp_path) -> None:
    store = Store(settings.db_path)
    ensure_outbox(settings.outbox_dir)
    task = _task(settings, store)
    image = tmp_path / "shot.png"
    image.write_bytes(b"png")
    feishu = FakeFeishu()
    _write(
        settings.outbox_dir,
        task.task_id,
        1,
        {
            "version": 1,
            "task_id": task.task_id,
            "kind": "final",
            "text": "見圖",
            "attachments": [{"path": str(image)}],
        },
    )
    handle_result_path(
        settings.outbox_dir / f"{task.task_id}.1.json",
        settings=settings,
        store=store,
        feishu=feishu,
    )
    assert feishu.uploads == ["shot.png"]
    assert any(item["msg_type"] == "image" for item in feishu.replies)
    store.close()


def test_scan_once_delivers_a_final_file(settings) -> None:
    store = Store(settings.db_path)
    ensure_outbox(settings.outbox_dir)
    task = _task(settings, store)
    feishu = FakeFeishu()
    _write(
        settings.outbox_dir,
        task.task_id,
        1,
        {"version": 1, "task_id": task.task_id, "kind": "final", "text": "掃描到了"},
    )
    watcher = OutboxWatcher(settings, store, feishu, threading.Event())
    watcher.scan_once()
    assert any(item.get("text") == "掃描到了" for item in feishu.replies)
    assert (settings.outbox_dir / "processed" / f"{task.task_id}.1.json").is_file()
    store.close()


def test_bad_kind_is_failed(settings) -> None:
    store = Store(settings.db_path)
    ensure_outbox(settings.outbox_dir)
    task = _task(settings, store)
    path = _write(
        settings.outbox_dir,
        task.task_id,
        1,
        {"version": 1, "task_id": task.task_id, "kind": "nope", "text": "x"},
    )
    assert handle_result_path(path, settings=settings, store=store, feishu=FakeFeishu()) == "failed"
    store.close()
