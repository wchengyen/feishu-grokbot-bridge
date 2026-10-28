"""Build ``P2ImMessageReceiveV1`` objects the same way the SDK unmarshals them."""

from __future__ import annotations

import json

from lark_oapi.api.im.v1.model.p2_im_message_receive_v1 import P2ImMessageReceiveV1
from lark_oapi.core.json import JSON


def make_event(
    *,
    event_id: str = "evt_1",
    message_id: str = "om_1",
    chat_id: str = "oc_1",
    chat_type: str = "p2p",
    message_type: str = "text",
    content: dict | str | None = None,
    text: str = "hello",
    open_id: str = "ou_owner",
    sender_type: str = "user",
    mentions: list[dict] | None = None,
    root_id: str = "",
    thread_id: str = "",
    create_time: str = "1600000000000",
) -> P2ImMessageReceiveV1:
    if content is None:
        content = {"text": text}
    content_str = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
    payload = {
        "schema": "2.0",
        "header": {
            "event_id": event_id,
            "event_type": "im.message.receive_v1",
            "create_time": create_time,
            "token": "",
            "app_id": "cli_test",
            "tenant_key": "t",
        },
        "event": {
            "sender": {
                "sender_id": {"open_id": open_id, "user_id": "", "union_id": ""},
                "sender_type": sender_type,
                "tenant_key": "t",
            },
            "message": {
                "message_id": message_id,
                "root_id": root_id,
                "parent_id": "",
                "create_time": create_time,
                "chat_id": chat_id,
                "thread_id": thread_id,
                "chat_type": chat_type,
                "message_type": message_type,
                "content": content_str,
                "mentions": mentions or [],
            },
        },
    }
    return JSON.unmarshal(json.dumps(payload), P2ImMessageReceiveV1)
