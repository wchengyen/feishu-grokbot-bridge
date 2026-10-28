"""Task JSON posted to the Grok Bot routine webhook."""

from __future__ import annotations

from pathlib import Path

from bridge.models import Task

PROTOCOL_VERSION = 1


def build_task_payload(task: Task, history: list[dict[str, str]], outbox_dir: Path) -> dict:
    return {
        "version": PROTOCOL_VERSION,
        "task_id": task.task_id,
        "source": "feishu",
        "chat_id": task.chat_id,
        "chat_type": task.chat_type,
        "message_id": task.message_id,
        "root_id": task.root_id,
        "sender": {
            "open_id": task.sender_open_id,
            "name": task.sender_name,
        },
        "text": task.text,
        "history": [
            {"role": item["role"], "text": item["text"], "ts": item["ts"]} for item in history
        ],
        "reply_via": {
            "type": "outbox",
            "dir": str(outbox_dir.expanduser().resolve()),
        },
        "created_at": task.created_at,
    }
