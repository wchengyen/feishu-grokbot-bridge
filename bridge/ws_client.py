"""Feishu long-connection client and the fast ``im.message.receive_v1`` handler.

``lark.ws.Client.start`` blocks. Handlers run on the SDK event loop via
``EventDispatcherHandler._do_without_validation``. They must not raise and must
not call Feishu or the webhook; both happen on the dispatcher thread.
"""

from __future__ import annotations

import asyncio
import logging
import queue

import lark_oapi as lark
from lark_oapi.api.im.v1.model.p2_im_message_receive_v1 import P2ImMessageReceiveV1

from bridge.config import Settings
from bridge.parsing import decide, parse_event
from bridge.store import Store

logger = logging.getLogger(__name__)


class MessageHandler:
    def __init__(
        self,
        settings: Settings,
        store: Store,
        task_queue: queue.Queue[str],
    ) -> None:
        self._settings = settings
        self._store = store
        self._queue = task_queue

    def handle(self, data: P2ImMessageReceiveV1) -> None:
        try:
            self._handle(data)
        except Exception as exc:
            # Swallow: a raised handler becomes HTTP 500 on the socket and Feishu re-pushes.
            logger.warning("event handler failed", extra={"error_type": type(exc).__name__})

    def _handle(self, data: P2ImMessageReceiveV1) -> None:
        parsed = parse_event(data, bot_open_id=self._settings.feishu_bot_open_id)
        if parsed is None:
            return
        decision = decide(parsed, self._settings.feishu_allowed_open_ids)
        if decision.action == "drop":
            fresh = self._store.mark_seen(parsed.event_id, parsed.message_id)
            if fresh and decision.reason == "allowlist":
                logger.warning(
                    "rejected sender",
                    extra={"reason": "allowlist", "sender_open_id": parsed.sender_open_id},
                )
            elif fresh:
                logger.info(
                    "dropped message",
                    extra={"reason": decision.reason, "chat_type": parsed.chat_type},
                )
            return
        if decision.action == "command":
            kind = "command"
            text = decision.reason
        elif decision.action == "notice":
            kind = "notice"
            text = decision.notice_text
        else:
            kind = "task"
            text = parsed.text
        task_id = self._store.record(parsed, kind=kind, text=text)
        if task_id is None:
            logger.info("duplicate message", extra={"message_id": parsed.message_id})
            return
        self._queue.put(task_id)
        logger.info("enqueued", extra={"task_id": task_id, "kind": kind})


def build_event_handler(message_handler: MessageHandler) -> lark.EventDispatcherHandler:
    def on_message(data: P2ImMessageReceiveV1) -> None:
        message_handler.handle(data)

    # Long connection authenticates with the app credential during the socket
    # handshake. The official sample passes empty encrypt key and verification
    # token; the socket path does not use webhook signature verification.
    return (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(on_message)
        .build()
    )


def build_ws_client(settings: Settings, message_handler: MessageHandler) -> lark.ws.Client:
    return lark.ws.Client(
        settings.feishu_app_id,
        settings.feishu_app_secret,
        event_handler=build_event_handler(message_handler),
        log_level=lark.LogLevel[settings.log_level],
        domain=settings.feishu_domain,
        auto_reconnect=True,
    )


def request_ws_stop() -> None:
    """Ask the SDK's module-level loop to stop. ``Client`` has no public stop()."""

    import lark_oapi.ws.client as ws_module

    loop = ws_module.loop

    def _stop() -> None:
        try:
            for task in asyncio.all_tasks(loop):
                task.cancel()
        except RuntimeError:
            pass
        loop.stop()

    try:
        loop.call_soon_threadsafe(_stop)
    except RuntimeError:
        logger.warning("websocket loop was not running during shutdown")


def run_ws_until_stopped(client: lark.ws.Client) -> None:
    try:
        client.start()
    except (KeyboardInterrupt, asyncio.CancelledError):
        return
    except RuntimeError as exc:
        text = str(exc).lower()
        if "stopped" in text or "closed" in text:
            return
        raise
