"""Thin wrapper over the lark-oapi im v1 client.

Token refresh is handled by the SDK. This module does not log request bodies.
Verified against lark-oapi 1.7.5:

* ``client.im.v1.message.reply`` → POST /open-apis/im/v1/messages/:message_id/reply
* ``client.im.v1.message.create`` → POST /open-apis/im/v1/messages
* ``client.im.v1.message.patch`` → PATCH /open-apis/im/v1/messages/:message_id
* ``client.im.v1.file.create`` → POST /open-apis/im/v1/files
* ``client.im.v1.image.create`` → POST /open-apis/im/v1/images
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import lark_oapi as lark
from lark_oapi.api.im.v1.model.create_file_request import CreateFileRequest
from lark_oapi.api.im.v1.model.create_file_request_body import CreateFileRequestBody
from lark_oapi.api.im.v1.model.create_image_request import CreateImageRequest
from lark_oapi.api.im.v1.model.create_image_request_body import CreateImageRequestBody
from lark_oapi.api.im.v1.model.create_message_request import CreateMessageRequest
from lark_oapi.api.im.v1.model.create_message_request_body import CreateMessageRequestBody
from lark_oapi.api.im.v1.model.patch_message_request import PatchMessageRequest
from lark_oapi.api.im.v1.model.patch_message_request_body import PatchMessageRequestBody
from lark_oapi.api.im.v1.model.reply_message_request import ReplyMessageRequest
from lark_oapi.api.im.v1.model.reply_message_request_body import ReplyMessageRequestBody

from bridge.config import Settings

_IMAGE_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".bmp",
    ".webp",
    ".ico",
    ".tif",
    ".tiff",
}
_FILE_TYPES = {
    ".pdf": "pdf",
    ".doc": "doc",
    ".docx": "doc",
    ".xls": "xls",
    ".xlsx": "xls",
    ".ppt": "ppt",
    ".pptx": "ppt",
    ".mp4": "mp4",
    ".opus": "opus",
    ".ogg": "opus",
}
_MAX_UPLOAD_BYTES = 30 * 1024 * 1024


class FeishuApiError(RuntimeError):
    def __init__(
        self,
        operation: str,
        code: int | None,
        log_id: str | None,
        *,
        retryable: bool = True,
    ) -> None:
        self.operation = operation
        self.code = code
        self.log_id = log_id
        self.retryable = retryable
        super().__init__(f"{operation} failed code={code} log_id={log_id}")


class FeishuApi:
    def __init__(self, client: object) -> None:
        self._client = client

    @classmethod
    def from_settings(cls, settings: Settings) -> FeishuApi:
        level = lark.LogLevel[settings.log_level]
        client = (
            lark.Client.builder()
            .app_id(settings.feishu_app_id)
            .app_secret(settings.feishu_app_secret)
            .domain(settings.feishu_domain)
            .log_level(level)
            .build()
        )
        return cls(client)

    def reply_interactive(
        self,
        message_id: str,
        card: dict,
        *,
        idempotency_key: str,
        reply_in_thread: bool = False,
    ) -> str:
        body = (
            ReplyMessageRequestBody.builder()
            .msg_type("interactive")
            .content(_dump(card))
            .reply_in_thread(reply_in_thread)
            .uuid(_uuid(idempotency_key))
            .build()
        )
        request = ReplyMessageRequest.builder().message_id(message_id).request_body(body).build()
        response = self._client.im.v1.message.reply(request)
        _raise_unless_ok(response, "reply_interactive")
        replied = response.data.message_id if response.data else None
        if not replied:
            raise FeishuApiError("reply_interactive", 0, _log_id(response))
        return replied

    def reply_text(
        self,
        message_id: str,
        text: str,
        *,
        idempotency_key: str,
        reply_in_thread: bool = False,
    ) -> str:
        return self._reply(
            message_id,
            "text",
            {"text": text},
            idempotency_key=idempotency_key,
            reply_in_thread=reply_in_thread,
        )

    def reply_file(
        self,
        message_id: str,
        file_key: str,
        *,
        idempotency_key: str,
        reply_in_thread: bool = False,
    ) -> str:
        return self._reply(
            message_id,
            "file",
            {"file_key": file_key},
            idempotency_key=idempotency_key,
            reply_in_thread=reply_in_thread,
        )

    def reply_image(
        self,
        message_id: str,
        image_key: str,
        *,
        idempotency_key: str,
        reply_in_thread: bool = False,
    ) -> str:
        return self._reply(
            message_id,
            "image",
            {"image_key": image_key},
            idempotency_key=idempotency_key,
            reply_in_thread=reply_in_thread,
        )

    def send_text(self, chat_id: str, text: str, *, idempotency_key: str) -> str:
        body = (
            CreateMessageRequestBody.builder()
            .receive_id(chat_id)
            .msg_type("text")
            .content(_dump({"text": text}))
            .uuid(_uuid(idempotency_key))
            .build()
        )
        request = (
            CreateMessageRequest.builder().receive_id_type("chat_id").request_body(body).build()
        )
        response = self._client.im.v1.message.create(request)
        _raise_unless_ok(response, "send_text")
        sent = response.data.message_id if response.data else None
        if not sent:
            raise FeishuApiError("send_text", 0, _log_id(response))
        return sent

    def patch_card(self, message_id: str, card: dict) -> None:
        body = PatchMessageRequestBody.builder().content(_dump(card)).build()
        request = PatchMessageRequest.builder().message_id(message_id).request_body(body).build()
        response = self._client.im.v1.message.patch(request)
        _raise_unless_ok(response, "patch_card")

    def upload_path(self, path: Path) -> tuple[str, str]:
        """Upload a local file. Returns ``(kind, key)`` where kind is image or file."""

        if not path.is_file():
            raise FeishuApiError("upload", None, None, retryable=False)
        size = path.stat().st_size
        if size > _MAX_UPLOAD_BYTES or size <= 0:
            raise FeishuApiError("upload", None, None, retryable=False)
        suffix = path.suffix.lower()
        if suffix in _IMAGE_SUFFIXES:
            return "image", self._upload_image(path)
        return "file", self._upload_file(path, _FILE_TYPES.get(suffix, "stream"))

    def _reply(
        self,
        message_id: str,
        msg_type: str,
        content: dict,
        *,
        idempotency_key: str,
        reply_in_thread: bool,
    ) -> str:
        body = (
            ReplyMessageRequestBody.builder()
            .msg_type(msg_type)
            .content(_dump(content))
            .reply_in_thread(reply_in_thread)
            .uuid(_uuid(idempotency_key))
            .build()
        )
        request = ReplyMessageRequest.builder().message_id(message_id).request_body(body).build()
        response = self._client.im.v1.message.reply(request)
        _raise_unless_ok(response, f"reply_{msg_type}")
        replied = response.data.message_id if response.data else None
        if not replied:
            raise FeishuApiError(f"reply_{msg_type}", 0, _log_id(response))
        return replied

    def _upload_file(self, path: Path, file_type: str) -> str:
        with path.open("rb") as handle:
            body = (
                CreateFileRequestBody.builder()
                .file_type(file_type)
                .file_name(path.name)
                .file(handle)
                .build()
            )
            request = CreateFileRequest.builder().request_body(body).build()
            response = self._client.im.v1.file.create(request)
        _raise_unless_ok(response, "upload_file")
        file_key = response.data.file_key if response.data else None
        if not file_key:
            raise FeishuApiError("upload_file", 0, _log_id(response))
        return file_key

    def _upload_image(self, path: Path) -> str:
        with path.open("rb") as handle:
            body = CreateImageRequestBody.builder().image_type("message").image(handle).build()
            request = CreateImageRequest.builder().request_body(body).build()
            response = self._client.im.v1.image.create(request)
        _raise_unless_ok(response, "upload_image")
        image_key = response.data.image_key if response.data else None
        if not image_key:
            raise FeishuApiError("upload_image", 0, _log_id(response))
        return image_key


def _dump(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _uuid(key: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"feishu-grokbot-bridge:{key}"))


def _raise_unless_ok(response: object, operation: str) -> None:
    success = getattr(response, "success", None)
    if callable(success) and success():
        return
    status = _status_code(response)
    retryable = status is None or status == 429 or status >= 500
    raise FeishuApiError(
        operation,
        getattr(response, "code", None),
        _log_id(response),
        retryable=retryable,
    )


def _status_code(response: object) -> int | None:
    raw = getattr(response, "raw", None)
    if raw is None:
        return None
    status = getattr(raw, "status_code", None)
    return status if isinstance(status, int) else None


def _log_id(response: object) -> str | None:
    getter = getattr(response, "get_log_id", None)
    if callable(getter):
        return getter()
    return None
