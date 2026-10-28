"""Worker that acknowledges a task in Feishu, then posts it to Grok Bot."""

from __future__ import annotations

import logging
import queue
import threading

from bridge.cards import ack_card, error_card
from bridge.commands import render_command
from bridge.config import Settings
from bridge.feishu_api import FeishuApi, FeishuApiError
from bridge.models import Task
from bridge.protocol import build_task_payload
from bridge.store import Store
from bridge.webhook import PermanentWebhookError, WebhookClient

logger = logging.getLogger(__name__)

_TERMINAL = {"dispatched", "done", "failed", "error"}


class Dispatcher:
    def __init__(
        self,
        settings: Settings,
        store: Store,
        feishu: FeishuApi,
        webhook: WebhookClient,
        task_queue: queue.Queue[str],
        stop: threading.Event,
    ) -> None:
        self._settings = settings
        self._store = store
        self._feishu = feishu
        self._webhook = webhook
        self._queue = task_queue
        self._stop = stop
        self._thread = threading.Thread(target=self._loop, name="dispatcher", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def join(self, timeout: float) -> None:
        self._thread.join(timeout)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                task_id = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            task = self._store.get_task(task_id)
            if task is None:
                continue
            try:
                process_task(
                    task,
                    settings=self._settings,
                    store=self._store,
                    feishu=self._feishu,
                    webhook=self._webhook,
                )
            except Exception as exc:
                logger.warning(
                    "dispatcher failed",
                    extra={"task_id": task_id, "error_type": type(exc).__name__},
                )
                self._store.mark_failed(task_id, "internal error")


def process_task(
    task: Task,
    *,
    settings: Settings,
    store: Store,
    feishu: FeishuApi,
    webhook: WebhookClient,
) -> None:
    current = store.get_task(task.task_id) or task
    if current.status in _TERMINAL:
        return
    if current.kind == "command":
        _reply_plain(
            feishu,
            current,
            render_command(current.text, store=store, chat_id=current.chat_id),
            idempotency_key=f"{current.task_id}:command",
        )
        store.mark_done(current.task_id)
        return
    if current.kind == "notice":
        _reply_plain(
            feishu,
            current,
            current.text,
            idempotency_key=f"{current.task_id}:notice",
        )
        store.mark_done(current.task_id)
        return
    if current.kind != "task":
        store.mark_failed(current.task_id, "unknown kind")
        return

    if not current.card_message_id:
        try:
            card_id = feishu.reply_interactive(
                current.message_id,
                ack_card(current.task_id, current.text),
                idempotency_key=f"{current.task_id}:ack",
                reply_in_thread=current.reply_in_thread,
            )
        except FeishuApiError as exc:
            logger.warning(
                "ack card failed",
                extra={"task_id": current.task_id, "code": exc.code, "log_id": exc.log_id},
            )
            store.mark_failed(current.task_id, "ack failed")
            return
        store.mark_card_sent(current.task_id, card_id)
        current.card_message_id = card_id

    history = store.recent_history(
        current.chat_id,
        settings.history_turns,
        exclude_message_id=current.message_id,
    )
    payload = build_task_payload(current, history, settings.outbox_dir)
    try:
        webhook.post_task(payload)
    except PermanentWebhookError as exc:
        logger.warning("webhook permanent failure", extra={"task_id": current.task_id})
        if current.card_message_id:
            _patch_quietly(
                feishu,
                current.card_message_id,
                error_card(current.task_id, "無法把任務送到 Grok Bot，請稍後再試。"),
            )
        store.mark_failed(current.task_id, str(exc)[:300])
        return
    store.mark_dispatched(current.task_id)
    logger.info("task dispatched", extra={"task_id": current.task_id})


def _reply_plain(feishu: FeishuApi, task: Task, text: str, *, idempotency_key: str) -> None:
    try:
        feishu.reply_text(
            task.message_id,
            text,
            idempotency_key=idempotency_key,
            reply_in_thread=task.reply_in_thread,
        )
    except FeishuApiError as exc:
        logger.warning(
            "reply failed",
            extra={"task_id": task.task_id, "code": exc.code, "log_id": exc.log_id},
        )


def _patch_quietly(feishu: FeishuApi, message_id: str, card: dict) -> None:
    try:
        feishu.patch_card(message_id, card)
    except FeishuApiError as exc:
        logger.warning("patch failed", extra={"code": exc.code, "log_id": exc.log_id})
