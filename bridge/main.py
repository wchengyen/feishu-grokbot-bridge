"""Process entrypoint: WebSocket client, dispatcher, and outbox watcher."""

from __future__ import annotations

import argparse
import json
import logging
import os
import queue
import signal
import sys
import threading
import time
from pathlib import Path

from bridge import __version__
from bridge.config import ConfigError, Settings, load_settings
from bridge.dispatcher import Dispatcher
from bridge.feishu_api import FeishuApi
from bridge.locking import SingleInstanceError, SingleInstanceLock
from bridge.logging_setup import configure_logging
from bridge.outbox import OutboxWatcher, ensure_outbox, release_inbox_claims
from bridge.store import Store, utcnow
from bridge.webhook import WebhookClient
from bridge.ws_client import MessageHandler, build_ws_client, request_ws_stop, run_ws_until_stopped

logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="feishu-grokbot-bridge")
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate configuration and, if present, the health file; then exit",
    )
    parser.add_argument("--dotenv", default=".env", help="dotenv file to read when keys are unset")
    parser.add_argument("--no-dotenv", action="store_true", help="do not read a dotenv file")
    args = parser.parse_args(argv)
    dotenv = None if args.no_dotenv else Path(args.dotenv)
    try:
        settings = load_settings(dotenv_path=dotenv)
    except ConfigError as exc:
        print(
            json.dumps({"config": "error", "problems": [str(exc)]}, ensure_ascii=False),
            file=sys.stderr,
        )
        return 2
    if args.check:
        return run_check(settings)
    configure_logging(settings)
    return run_bridge(settings)


def run_check(settings: Settings) -> int:
    problems: list[str] = []
    if not settings.webhook_tls:
        problems.append("webhook_url_not_https")
    outbox_problem = _check_directory(settings.outbox_dir)
    if outbox_problem:
        problems.append(outbox_problem)
    db_problem = _check_parent(settings.db_path)
    if db_problem:
        problems.append(db_problem)
    health = "disabled"
    if settings.health_file is not None:
        health_file = settings.health_file
        if health_file.exists():
            age = time.time() - health_file.stat().st_mtime
            if age > settings.health_stale_seconds:
                problems.append("health_stale")
                health = "stale"
            else:
                health = "fresh"
        else:
            health = "absent"
    report = {
        "config": "ok" if not problems else "error",
        "health": health,
        "problems": problems,
        "version": __version__,
    }
    print(json.dumps(report, ensure_ascii=False))
    return 0 if not problems else 1


def run_bridge(settings: Settings) -> int:
    lock = SingleInstanceLock(settings.db_path.with_suffix(settings.db_path.suffix + ".lock"))
    try:
        lock.acquire()
    except SingleInstanceError:
        logger.error("another bridge instance is already running")
        return 1

    try:
        return _run_locked(settings)
    finally:
        lock.release()


def _run_locked(settings: Settings) -> int:
    store = Store(settings.db_path)
    ensure_outbox(settings.outbox_dir)
    release_inbox_claims(store, settings.outbox_dir)
    feishu = FeishuApi.from_settings(settings)
    webhook = WebhookClient(settings)
    task_queue: queue.Queue[str] = queue.Queue()
    for task_id in store.incomplete_task_ids():
        task_queue.put(task_id)

    stop = threading.Event()
    health = _Health(settings, stop)
    dispatcher = Dispatcher(settings, store, feishu, webhook, task_queue, stop)
    watcher = OutboxWatcher(settings, store, feishu, stop)
    handler = MessageHandler(settings, store, task_queue)

    def _on_signal(_signum: int, _frame: object) -> None:
        stop.set()
        request_ws_stop()

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    dispatcher.start()
    watcher.start()
    health.start()
    health.set("dispatcher", "running")
    health.set("outbox", "running")
    health.write("ok")
    logger.info("bridge starting", extra={"version": __version__})
    exit_code = 0
    try:
        client = build_ws_client(settings, handler)
        health.set("ws", "running")
        health.write("ok")
        run_ws_until_stopped(client)
    except Exception as exc:
        exit_code = 1
        logger.error("websocket client stopped", extra={"error_type": type(exc).__name__})
    finally:
        stop.set()
        request_ws_stop()
        dispatcher.join(10)
        watcher.join(10)
        health.set("ws", "stopped")
        health.set("dispatcher", "stopped")
        health.set("outbox", "stopped")
        health.write("stopped")
        health.join(3)
        webhook.close()
        store.close()
    return exit_code


class _Health:
    def __init__(self, settings: Settings, stop: threading.Event) -> None:
        self._settings = settings
        self._stop = stop
        self._status = {"ws": "starting", "dispatcher": "starting", "outbox": "starting"}
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._loop, name="health", daemon=True)

    def set(self, name: str, value: str) -> None:
        with self._lock:
            self._status[name] = value

    def start(self) -> None:
        self._thread.start()

    def join(self, timeout: float) -> None:
        self._thread.join(timeout)

    def write(self, overall: str) -> None:
        path = self._settings.health_file
        if path is None:
            return
        with self._lock:
            components = dict(self._status)
        payload = {
            "status": overall,
            "pid": os.getpid(),
            "updated_at": utcnow(),
            "components": components,
            "version": __version__,
        }
        _atomic_write(path, json.dumps(payload, ensure_ascii=False))

    def _loop(self) -> None:
        while not self._stop.wait(self._settings.health_interval_seconds):
            try:
                self.write("ok")
            except OSError:
                logger.warning("health file write failed")


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _check_directory(path: Path) -> str | None:
    if path.exists():
        if not path.is_dir():
            return "outbox_not_directory"
        if not os.access(path, os.W_OK):
            return "outbox_not_writable"
        return None
    return _check_parent(path)


def _check_parent(path: Path) -> str | None:
    parent = path.parent
    if parent.exists() and parent.is_dir() and os.access(parent, os.W_OK):
        return None
    if not parent.exists():
        return "path_parent_missing"
    return "path_parent_not_writable"


if __name__ == "__main__":
    raise SystemExit(main())
