"""Watch the outbox directory and deliver result files to Feishu."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from bridge.cards import CARD_TEXT_LIMIT, done_card, error_card, progress_card
from bridge.config import Settings
from bridge.feishu_api import FeishuApi, FeishuApiError
from bridge.models import Task
from bridge.parsing import MAX_TASK_CHARS
from bridge.store import Store, utcnow
from bridge.textutil import delivery_plan

logger = logging.getLogger(__name__)

RESULT_NAME = re.compile(
    r"^(?P<task_id>[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\.(?P<seq>\d+)\.json$"
)
_KINDS = {"progress", "final", "error"}


@dataclass(frozen=True)
class ResultFile:
    task_id: str
    seq: int
    kind: str
    text: str
    attachments: tuple[Path, ...]


class OutboxWatcher:
    def __init__(
        self,
        settings: Settings,
        store: Store,
        feishu: FeishuApi,
        stop: threading.Event,
    ) -> None:
        self._settings = settings
        self._store = store
        self._feishu = feishu
        self._stop = stop
        self._wake = threading.Event()
        self._scan_lock = threading.Lock()
        self._thread = threading.Thread(target=self._loop, name="outbox", daemon=True)
        self._observer = None

    def start(self) -> None:
        ensure_outbox(self._settings.outbox_dir)
        self._thread.start()
        self._start_watchdog()

    def join(self, timeout: float) -> None:
        self._wake.set()
        self._thread.join(timeout)
        observer = self._observer
        if observer is not None:
            observer.stop()
            observer.join(timeout=5)

    def scan_once(self) -> None:
        if not self._scan_lock.acquire(blocking=False):
            return
        try:
            root = self._settings.outbox_dir
            if not root.is_dir():
                return
            paths = [
                path
                for path in root.iterdir()
                if path.is_file()
                and not path.name.startswith(".")
                and path.suffix.lower() == ".json"
            ]
            paths.sort(key=_sort_key)
            for path in paths:
                try:
                    handle_result_path(
                        path,
                        settings=self._settings,
                        store=self._store,
                        feishu=self._feishu,
                    )
                except Exception as exc:
                    logger.warning(
                        "outbox file failed",
                        extra={"file": path.name, "error_type": type(exc).__name__},
                    )
        finally:
            self._scan_lock.release()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.scan_once()
            except Exception as exc:
                logger.warning("outbox scan failed", extra={"error_type": type(exc).__name__})
            self._wake.wait(self._settings.outbox_poll_seconds)
            self._wake.clear()

    def _start_watchdog(self) -> None:
        try:
            from watchdog.events import FileSystemEventHandler
            from watchdog.observers import Observer
        except ImportError:
            logger.warning("watchdog is not installed; using polling only")
            return

        wake = self._wake

        class _Handler(FileSystemEventHandler):
            def on_any_event(self, event: object) -> None:
                if not getattr(event, "is_directory", False):
                    wake.set()

        try:
            observer = Observer()
            observer.schedule(_Handler(), str(self._settings.outbox_dir), recursive=False)
            observer.start()
        except Exception as exc:
            logger.warning(
                "watchdog unavailable; using polling only",
                extra={"error_type": type(exc).__name__},
            )
            self._observer = None
            return
        self._observer = observer


def ensure_outbox(path: Path) -> None:
    for directory in (path, path / "processed", path / "failed"):
        directory.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(directory, 0o700)
        except OSError:
            logger.warning("could not restrict outbox directory permissions")


def release_inbox_claims(store: Store, outbox_dir: Path) -> None:
    """Let files still sitting in the inbox be retried after a crash."""

    if not outbox_dir.is_dir():
        return
    for path in outbox_dir.iterdir():
        if path.is_file() and RESULT_NAME.match(path.name):
            store.release_result_file(path.name)


def handle_result_path(
    path: Path,
    *,
    settings: Settings,
    store: Store,
    feishu: FeishuApi,
    now: float | None = None,
) -> str:
    """Process one result file. Returns ``processed``, ``failed``, or ``retry``."""

    clock = time.time() if now is None else now
    parsed, reason = parse_result(path)
    if parsed is None:
        if reason == "json" and _age_seconds(path, clock) < settings.outbox_partial_grace_seconds:
            return "retry"
        logger.warning("malformed outbox file", extra={"file": path.name, "reason": reason})
        _move(path, settings.outbox_dir / "failed")
        return "failed"

    task = store.get_task(parsed.task_id)
    if task is None:
        logger.warning("outbox file for unknown task", extra={"file": path.name})
        _move(path, settings.outbox_dir / "failed")
        return "failed"

    if not store.claim_result_file(path.name, parsed.task_id):
        _move(path, settings.outbox_dir / "processed")
        return "processed"

    try:
        deliver_result(task, parsed, settings=settings, store=store, feishu=feishu)
    except FeishuApiError as exc:
        store.release_result_file(path.name)
        logger.warning(
            "feishu delivery failed; will retry",
            extra={"file": path.name, "code": exc.code, "log_id": exc.log_id},
        )
        return "retry"
    _move(path, settings.outbox_dir / "processed")
    return "processed"


def parse_result(path: Path) -> tuple[ResultFile | None, str | None]:
    match = RESULT_NAME.match(path.name)
    if not match:
        return None, "filename"
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None, "json"
    if not isinstance(data, dict):
        return None, "shape"
    if data.get("version") != 1:
        return None, "version"
    if data.get("task_id") != match.group("task_id"):
        return None, "task_id"
    kind = data.get("kind")
    if kind not in _KINDS:
        return None, "kind"
    text = data.get("text", "")
    if text is None:
        text = ""
    if not isinstance(text, str):
        return None, "text"
    attachments: list[Path] = []
    raw_attachments = data.get("attachments", [])
    if raw_attachments is None:
        raw_attachments = []
    if not isinstance(raw_attachments, list):
        return None, "attachments"
    for item in raw_attachments:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            return None, "attachments"
        file_path = Path(item["path"])
        if not file_path.is_absolute():
            return None, "attachments"
        attachments.append(file_path)
    return (
        ResultFile(
            task_id=match.group("task_id"),
            seq=int(match.group("seq")),
            kind=kind,
            text=text,
            attachments=tuple(attachments),
        ),
        None,
    )


def deliver_result(
    task: Task,
    result: ResultFile,
    *,
    settings: Settings,
    store: Store,
    feishu: FeishuApi,
) -> None:
    if result.kind == "progress":
        _patch_or_reply(
            feishu,
            task,
            progress_card(task.task_id, result.text),
            fallback=result.text,
            idempotency_key=f"{task.task_id}:progress:{result.seq}",
        )
    elif result.kind == "error":
        if task.card_message_id:
            feishu.patch_card(task.card_message_id, error_card(task.task_id, result.text))
        if not task.card_message_id or len(result.text) > CARD_TEXT_LIMIT:
            _send_text_body(feishu, task, result, settings)
        store.append_history(
            chat_id=task.chat_id,
            role="assistant",
            text=_history_text(result.text),
            ts=utcnow(),
            message_id=f"{task.task_id}:error:{result.seq}",
        )
        store.mark_error(task.task_id, result.text[:500] or "error")
    else:
        if task.card_message_id:
            feishu.patch_card(task.card_message_id, done_card(task.task_id, result.text))
        _send_text_body(feishu, task, result, settings)
        store.append_history(
            chat_id=task.chat_id,
            role="assistant",
            text=_history_text(result.text),
            ts=utcnow(),
            message_id=f"{task.task_id}:final:{result.seq}",
        )
        store.mark_done(task.task_id)
    _send_attachments(feishu, task, result)


def _patch_or_reply(
    feishu: FeishuApi,
    task: Task,
    card: dict,
    *,
    fallback: str,
    idempotency_key: str,
) -> None:
    if task.card_message_id:
        feishu.patch_card(task.card_message_id, card)
    elif fallback:
        feishu.reply_text(
            task.message_id,
            fallback[:CARD_TEXT_LIMIT],
            idempotency_key=idempotency_key,
            reply_in_thread=task.reply_in_thread,
        )


def _send_text_body(
    feishu: FeishuApi,
    task: Task,
    result: ResultFile,
    settings: Settings,
) -> None:
    if not result.text:
        return
    mode, chunks = delivery_plan(
        result.text,
        split_at=settings.text_split_chars,
        file_at=settings.text_file_chars,
    )
    for index, chunk in enumerate(chunks):
        feishu.reply_text(
            task.message_id,
            chunk,
            idempotency_key=f"{task.task_id}:text:{result.seq}:{index}",
            reply_in_thread=task.reply_in_thread,
        )
    if mode == "file":
        tmp = settings.outbox_dir / f".{task.task_id}.{result.seq}.md"
        tmp.write_text(result.text, encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        try:
            _kind, key = feishu.upload_path(tmp)
            feishu.reply_file(
                task.message_id,
                key,
                idempotency_key=f"{task.task_id}:file:{result.seq}",
                reply_in_thread=task.reply_in_thread,
            )
        finally:
            tmp.unlink(missing_ok=True)


def _send_attachments(feishu: FeishuApi, task: Task, result: ResultFile) -> None:
    for index, attachment in enumerate(result.attachments):
        name = attachment.name
        if not attachment.is_file():
            feishu.reply_text(
                task.message_id,
                f"找不到附件：{name}",
                idempotency_key=f"{task.task_id}:missing:{result.seq}:{index}",
                reply_in_thread=task.reply_in_thread,
            )
            continue
        try:
            kind, key = feishu.upload_path(attachment)
        except FeishuApiError as exc:
            if exc.retryable:
                raise
            feishu.reply_text(
                task.message_id,
                f"無法上傳附件：{name}",
                idempotency_key=f"{task.task_id}:att-failed:{result.seq}:{index}",
                reply_in_thread=task.reply_in_thread,
            )
            continue
        if kind == "image":
            feishu.reply_image(
                task.message_id,
                key,
                idempotency_key=f"{task.task_id}:att:{result.seq}:{index}",
                reply_in_thread=task.reply_in_thread,
            )
        else:
            feishu.reply_file(
                task.message_id,
                key,
                idempotency_key=f"{task.task_id}:att:{result.seq}:{index}",
                reply_in_thread=task.reply_in_thread,
            )


def _history_text(text: str) -> str:
    if len(text) <= MAX_TASK_CHARS:
        return text
    return text[:MAX_TASK_CHARS].rstrip() + "\n…（已截斷）"


def _age_seconds(path: Path, now: float) -> float:
    try:
        return now - path.stat().st_mtime
    except OSError:
        return 0


def _sort_key(path: Path) -> tuple[str, int, str]:
    match = RESULT_NAME.match(path.name)
    if not match:
        return ("", 0, path.name)
    return (match.group("task_id"), int(match.group("seq")), path.name)


def _move(path: Path, dest_dir: Path) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(dest_dir, 0o700)
    except OSError:
        pass
    target = dest_dir / path.name
    if target.exists():
        target = dest_dir / f"{path.stem}-{int(time.time())}{path.suffix}"
    shutil.move(str(path), str(target))
