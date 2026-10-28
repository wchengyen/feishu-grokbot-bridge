"""Shared data objects for tasks and parsed Feishu events."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ParsedMessage:
    event_id: str
    message_id: str
    chat_id: str
    chat_type: str
    root_id: str
    thread_id: str
    sender_open_id: str
    sender_type: str
    text: str
    message_type: str
    mentioned_bot: bool
    command: str | None
    created_at: str


@dataclass(frozen=True)
class Decision:
    """What the fast path should do with a parsed message, before persistence."""

    action: str  # task, command, notice, drop
    reason: str
    notice_text: str = ""


@dataclass
class Task:
    task_id: str
    event_id: str
    chat_id: str
    chat_type: str
    message_id: str
    root_id: str
    thread_id: str
    sender_open_id: str
    sender_name: str
    text: str
    kind: str
    status: str
    card_message_id: str | None
    error: str | None
    created_at: str
    updated_at: str

    @property
    def reply_in_thread(self) -> bool:
        return bool(self.thread_id)
