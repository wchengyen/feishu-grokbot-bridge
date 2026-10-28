"""In-memory stand-ins for Feishu and the webhook. No sockets."""

from __future__ import annotations

from bridge.models import Task


class FakeFeishu:
    def __init__(self) -> None:
        self.replies: list[dict] = []
        self.patches: list[dict] = []
        self.sent: list[dict] = []
        self.uploads: list[str] = []
        self.fail_patch = False

    def reply_interactive(self, message_id, card, *, idempotency_key, reply_in_thread=False) -> str:
        self.replies.append(
            {
                "message_id": message_id,
                "msg_type": "interactive",
                "card": card,
                "key": idempotency_key,
                "reply_in_thread": reply_in_thread,
            }
        )
        return "om_card"

    def reply_text(self, message_id, text, *, idempotency_key, reply_in_thread=False) -> str:
        self.replies.append(
            {
                "message_id": message_id,
                "msg_type": "text",
                "text": text,
                "key": idempotency_key,
                "reply_in_thread": reply_in_thread,
            }
        )
        return "om_text"

    def reply_file(self, message_id, file_key, *, idempotency_key, reply_in_thread=False) -> str:
        self.replies.append(
            {
                "message_id": message_id,
                "msg_type": "file",
                "file_key": file_key,
                "key": idempotency_key,
            }
        )
        return "om_file"

    def reply_image(self, message_id, image_key, *, idempotency_key, reply_in_thread=False) -> str:
        self.replies.append(
            {
                "message_id": message_id,
                "msg_type": "image",
                "image_key": image_key,
                "key": idempotency_key,
            }
        )
        return "om_image"

    def send_text(self, chat_id, text, *, idempotency_key) -> str:
        self.sent.append({"chat_id": chat_id, "text": text, "key": idempotency_key})
        return "om_sent"

    def patch_card(self, message_id, card) -> None:
        if self.fail_patch:
            from bridge.feishu_api import FeishuApiError

            raise FeishuApiError("patch_card", 500, "log", retryable=True)
        self.patches.append({"message_id": message_id, "card": card})

    def upload_path(self, path) -> tuple[str, str]:
        self.uploads.append(path.name)
        if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
            return "image", "img_key"
        return "file", "file_key"


class FakeWebhook:
    def __init__(self, *, fail: bool = False) -> None:
        self.payloads: list[dict] = []
        self.fail = fail

    def post_task(self, payload: dict) -> None:
        if self.fail:
            from bridge.webhook import PermanentWebhookError

            raise PermanentWebhookError("webhook failed status=500 attempts=5")
        self.payloads.append(payload)


def task_of(store, task_id: str) -> Task:
    task = store.get_task(task_id)
    assert task is not None
    return task
