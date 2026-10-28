"""Parse ``im.message.receive_v1`` events into plain text and a routing decision.

The handler that calls this must stay free of network I/O. Feishu re-pushes the
event when the callback raises or runs longer than about three seconds.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from bridge.models import Decision, ParsedMessage

logger = logging.getLogger(__name__)

COMMANDS = frozenset({"/help", "/status", "/history", "/ping"})
MAX_TASK_CHARS = 20_000

_UNSUPPORTED_NOTICE = "目前只支援文字與富文本訊息，請改用文字描述要交給 Javis 的任務。"
_EMPTY_NOTICE = "請在訊息裡寫下要交給 Javis 的任務。輸入 /help 可以查看指令。"


def parse_event(data: Any, *, bot_open_id: str = "") -> ParsedMessage | None:
    """Return a parsed message, or None when the payload has no usable ids."""

    try:
        header = getattr(data, "header", None)
        event = getattr(data, "event", None)
        message = getattr(event, "message", None)
        sender = getattr(event, "sender", None)
        event_id = _str(getattr(header, "event_id", None))
        message_id = _str(getattr(message, "message_id", None))
        if not event_id or not message_id or message is None or sender is None:
            logger.warning("ignored event missing event_id or message_id")
            return None
        sender_id = getattr(sender, "sender_id", None)
        mentions = list(getattr(message, "mentions", None) or [])
        message_type = _str(getattr(message, "message_type", None)) or "unknown"
        text = extract_text(message_type, getattr(message, "content", None), mentions)
        created = _created_at(
            getattr(header, "create_time", None),
            getattr(message, "create_time", None),
        )
        return ParsedMessage(
            event_id=event_id,
            message_id=message_id,
            chat_id=_str(getattr(message, "chat_id", None)),
            chat_type=_str(getattr(message, "chat_type", None)),
            root_id=_str(getattr(message, "root_id", None)),
            thread_id=_str(getattr(message, "thread_id", None)),
            sender_open_id=_str(getattr(sender_id, "open_id", None)),
            sender_type=_str(getattr(sender, "sender_type", None)),
            text=text,
            message_type=message_type,
            mentioned_bot=bot_is_mentioned(mentions, bot_open_id),
            command=parse_command(text),
            created_at=created,
        )
    except Exception as exc:
        logger.warning("ignored event that could not be parsed (%s)", type(exc).__name__)
        return None


def extract_text(message_type: str, content: Any, mentions: list[Any]) -> str:
    if message_type not in {"text", "post"}:
        return ""
    payload = _load_content(content)
    if payload is None:
        return ""
    if message_type == "text":
        raw = payload.get("text") if isinstance(payload, dict) else ""
        text = raw if isinstance(raw, str) else ""
    else:
        text = _post_to_text(payload)
    text = strip_mentions(text, _mention_keys(mentions))
    if len(text) > MAX_TASK_CHARS:
        text = text[:MAX_TASK_CHARS].rstrip() + "\n…（已截斷）"
    return text


def strip_mentions(text: str, keys: list[str]) -> str:
    """Remove Feishu mention placeholders such as ``@_user_1`` and ``@_all``."""

    for key in keys:
        if key:
            text = text.replace(key, " ")
    text = text.replace("@_all", " ")
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def bot_is_mentioned(mentions: list[Any], bot_open_id: str) -> bool:
    """Whether this group message addresses the bot.

    With ``FEISHU_BOT_OPEN_ID`` set, only that open_id counts. Otherwise any
    specific mention other than ``@_all`` counts. That matches apps whose only
    group scope is "messages that @ the bot", where Feishu has already filtered
    the stream. ``@_all`` alone does not count.
    """

    if bot_open_id:
        for mention in mentions:
            open_id = _str(getattr(getattr(mention, "id", None), "open_id", None))
            if open_id and open_id == bot_open_id:
                return True
        return False
    for mention in mentions:
        key = _str(getattr(mention, "key", None))
        if key and key != "@_all":
            return True
    return False


def parse_command(text: str) -> str | None:
    parts = text.strip().split()
    if not parts:
        return None
    command = parts[0].lower()
    if command in COMMANDS:
        return command
    return None


def decide(parsed: ParsedMessage, allowed_open_ids: frozenset[str]) -> Decision:
    """Route a message. An empty allowlist rejects every sender."""

    if parsed.sender_type != "user":
        return Decision("drop", "not_user")
    if not parsed.sender_open_id or parsed.sender_open_id not in allowed_open_ids:
        return Decision("drop", "allowlist")
    if parsed.chat_type == "group":
        if not parsed.mentioned_bot:
            return Decision("drop", "group_not_mentioned")
    elif parsed.chat_type != "p2p":
        return Decision("drop", "unsupported_chat")
    if not parsed.chat_id:
        return Decision("drop", "missing_chat_id")
    if parsed.message_type not in {"text", "post"}:
        return Decision("notice", "unsupported_type", _UNSUPPORTED_NOTICE)
    if parsed.command:
        return Decision("command", parsed.command)
    if not parsed.text:
        return Decision("notice", "empty", _EMPTY_NOTICE)
    return Decision("task", "enqueue")


def _post_to_text(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    paragraphs: list[str] = []
    title = payload.get("title")
    if isinstance(title, str) and title.strip():
        paragraphs.append(title.strip())
    content = payload.get("content")
    if not isinstance(content, list):
        # Some clients send a locale map: {"zh_cn": {"title": ..., "content": ...}}.
        for value in payload.values():
            if isinstance(value, dict) and "content" in value:
                return _post_to_text(value)
        return "\n".join(paragraphs)
    for paragraph in content:
        if not isinstance(paragraph, list):
            continue
        parts: list[str] = []
        for node in paragraph:
            if not isinstance(node, dict):
                continue
            tag = node.get("tag")
            if tag == "at":
                continue
            if tag == "img":
                parts.append("[圖片]")
                continue
            text = node.get("text")
            if isinstance(text, str) and text:
                parts.append(text)
                continue
            href = node.get("href")
            if isinstance(href, str) and href:
                parts.append(href)
        line = "".join(parts).strip()
        if line:
            paragraphs.append(line)
    return "\n".join(paragraphs)


def _mention_keys(mentions: list[Any]) -> list[str]:
    keys: list[str] = []
    for mention in mentions:
        if isinstance(mention, dict):
            key = mention.get("key")
        else:
            key = getattr(mention, "key", None)
        if isinstance(key, str) and key:
            keys.append(key)
    return keys


def _load_content(content: Any) -> Any:
    if isinstance(content, dict):
        return content
    if not isinstance(content, str) or not content:
        return None
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return None


def _created_at(header_time: Any, message_time: Any) -> str:
    raw = header_time if header_time not in (None, "") else message_time
    if raw is None or raw == "":
        return _now()
    try:
        millis = int(raw)
    except (TypeError, ValueError):
        return _now()
    # Feishu sends millisecond timestamps. Values below 10^12 are seconds.
    if millis < 10**12:
        millis *= 1000
    try:
        return datetime.fromtimestamp(millis / 1000, tz=UTC).isoformat()
    except (OverflowError, OSError, ValueError):
        return _now()


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()
