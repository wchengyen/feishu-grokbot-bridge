"""Interactive card JSON for messages sent and later patched by the bot.

``im.v1.message.patch`` only updates interactive cards the bot itself sent,
so the acknowledgement must use ``msg_type=interactive``.
"""

from __future__ import annotations

CARD_TEXT_LIMIT = 1800


def card(title: str, template: str, markdown: str) -> dict:
    body = (markdown or " ").strip() or " "
    if len(body) > CARD_TEXT_LIMIT:
        body = body[: CARD_TEXT_LIMIT - 1].rstrip() + "…"
    return {
        "config": {"wide_screen_mode": True, "update_multi": True},
        "header": {
            "template": template,
            "title": {"tag": "plain_text", "content": title[:100]},
        },
        "elements": [
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": body},
            }
        ],
    }


def ack_card(task_id: str, preview: str) -> dict:
    preview = preview.strip().replace("\n", " ")
    if len(preview) > 180:
        preview = preview[:179].rstrip() + "…"
    lines = ["已把任務交給 Javis，完成後會回覆在這則訊息下面。", f"task `{task_id}`"]
    if preview:
        lines.append(f"內容：{preview}")
    return card("已收到，處理中…", "blue", "\n".join(lines))


def progress_card(task_id: str, text: str) -> dict:
    return card("處理中", "wathet", f"task `{task_id}`\n\n{text}")


def done_card(task_id: str, text: str) -> dict:
    note = "完整回覆在下面。"
    if text.strip():
        body = f"{note}\n\ntask `{task_id}`\n\n{text.strip()}"
    else:
        body = f"{note}\n\ntask `{task_id}`"
    return card("已完成", "green", body)


def error_card(task_id: str, text: str) -> dict:
    return card("處理失敗", "red", f"task `{task_id}`\n\n{text.strip() or '任務沒有完成。'}")
