"""Local slash commands. These never call the Grok Bot webhook."""

from __future__ import annotations

from bridge.models import Task
from bridge.store import Store, utcnow

HELP_TEXT = """可用指令：
/help — 顯示這則說明
/status — 此對話進行中的任務
/history — 最近 5 筆任務
/ping — 檢查橋接是否在線

其他文字會交給 Javis（Grok Bot）處理。群組裡請 @ 機器人。"""

_STATUS_LABELS = {
    "queued": "排隊中",
    "card_sent": "送出中",
    "dispatched": "處理中",
    "failed": "送出失敗",
    "done": "已完成",
    "error": "失敗",
}


def render_command(command: str, *, store: Store, chat_id: str) -> str:
    if command == "/help":
        return HELP_TEXT
    if command == "/ping":
        return f"pong {utcnow()}"
    if command == "/status":
        return _format_tasks(
            "進行中的任務",
            store.inflight_tasks(chat_id),
            empty="目前沒有進行中的任務。",
        )
    if command == "/history":
        return _format_tasks(
            "最近的任務",
            store.recent_tasks(chat_id, 5),
            empty="還沒有任務。",
        )
    return HELP_TEXT


def _format_tasks(title: str, tasks: list[Task], *, empty: str) -> str:
    if not tasks:
        return empty
    lines = [f"{title}（{len(tasks)}）"]
    for task in tasks:
        label = _STATUS_LABELS.get(task.status, task.status)
        preview = " ".join(task.text.split())
        if len(preview) > 80:
            preview = preview[:79].rstrip() + "…"
        lines.append(f"- {task.created_at} [{label}] {preview}")
    return "\n".join(lines)
