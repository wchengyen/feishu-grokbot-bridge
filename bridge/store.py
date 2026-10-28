"""SQLite state: dedup, tasks, and per-chat history.

All access is serialized with a lock so the WebSocket thread and the worker
threads can share one connection. Writes stay short so the event handler can
return inside Feishu's three-second window.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path

from bridge.models import ParsedMessage, Task

_INFLIGHT = ("queued", "card_sent", "dispatched")


def utcnow() -> str:
    return datetime.now(UTC).isoformat()


class Store:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.path.parent, 0o700)
        except OSError:
            pass
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=3000")
        self._migrate()
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _migrate(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS seen_events (
                event_id TEXT PRIMARY KEY,
                seen_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS seen_messages (
                message_id TEXT PRIMARY KEY,
                seen_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY,
                event_id TEXT NOT NULL,
                chat_id TEXT NOT NULL,
                chat_type TEXT NOT NULL,
                message_id TEXT NOT NULL,
                root_id TEXT NOT NULL DEFAULT '',
                thread_id TEXT NOT NULL DEFAULT '',
                sender_open_id TEXT NOT NULL,
                sender_name TEXT NOT NULL DEFAULT '',
                text TEXT NOT NULL,
                kind TEXT NOT NULL,
                status TEXT NOT NULL,
                card_message_id TEXT,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status, created_at);
            CREATE INDEX IF NOT EXISTS idx_tasks_chat ON tasks(chat_id, created_at);
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT NOT NULL,
                message_id TEXT,
                role TEXT NOT NULL,
                text TEXT NOT NULL,
                ts TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_history_chat ON history(chat_id, id);
            CREATE TABLE IF NOT EXISTS processed_results (
                file_name TEXT PRIMARY KEY,
                task_id TEXT,
                processed_at TEXT NOT NULL
            );
            """
        )
        self._conn.commit()

    def record(self, parsed: ParsedMessage, *, kind: str, text: str) -> str | None:
        """Dedup and insert a task. Returns the new task id, or None if duplicate.

        Seen rows are written even when the caller later drops the message; the
        handler only calls ``record`` for work that should be queued. Use
        ``mark_seen`` for drops so a re-push does not get logged forever.
        """

        now = utcnow()
        task_id = str(uuid.uuid4())
        with self._lock:
            if not self._mark_seen_locked(parsed.event_id, parsed.message_id, now):
                self._conn.commit()
                return None
            self._conn.execute(
                """
                INSERT INTO tasks (
                    task_id, event_id, chat_id, chat_type, message_id, root_id,
                    thread_id, sender_open_id, sender_name, text, kind, status,
                    card_message_id, error, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', NULL, NULL, ?, ?)
                """,
                (
                    task_id,
                    parsed.event_id,
                    parsed.chat_id,
                    parsed.chat_type,
                    parsed.message_id,
                    parsed.root_id,
                    parsed.thread_id,
                    parsed.sender_open_id,
                    "",
                    text,
                    kind,
                    parsed.created_at or now,
                    now,
                ),
            )
            if kind == "task":
                self._append_history_locked(
                    chat_id=parsed.chat_id,
                    message_id=parsed.message_id,
                    role="user",
                    text=text,
                    ts=parsed.created_at or now,
                )
            self._conn.commit()
        return task_id

    def mark_seen(self, event_id: str, message_id: str) -> bool:
        """Remember an event that will not be queued. False means it was a duplicate."""

        with self._lock:
            fresh = self._mark_seen_locked(event_id, message_id, utcnow())
            self._conn.commit()
        return fresh

    def _mark_seen_locked(self, event_id: str, message_id: str, now: str) -> bool:
        self._conn.execute(
            "INSERT OR IGNORE INTO seen_events (event_id, seen_at) VALUES (?, ?)",
            (event_id, now),
        )
        event_new = self._conn.execute("SELECT changes()").fetchone()[0] == 1
        self._conn.execute(
            "INSERT OR IGNORE INTO seen_messages (message_id, seen_at) VALUES (?, ?)",
            (message_id, now),
        )
        message_new = self._conn.execute("SELECT changes()").fetchone()[0] == 1
        # Both keys are recorded in this transaction. Feishu may redeliver the
        # same message_id under a new event_id, so either hit is a duplicate.
        return event_new and message_new

    def get_task(self, task_id: str) -> Task | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        return _task(row) if row else None

    def incomplete_task_ids(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT task_id FROM tasks
                WHERE status IN ('queued', 'card_sent')
                ORDER BY created_at
                """
            ).fetchall()
        return [row["task_id"] for row in rows]

    def mark_card_sent(self, task_id: str, card_message_id: str) -> None:
        self._update_status(task_id, "card_sent", card_message_id=card_message_id, error=None)

    def mark_dispatched(self, task_id: str) -> None:
        self._update_status(task_id, "dispatched")

    def mark_failed(self, task_id: str, error: str) -> None:
        self._update_status(task_id, "failed", error=error[:500])

    def mark_done(self, task_id: str) -> None:
        self._update_status(task_id, "done")

    def mark_error(self, task_id: str, error: str) -> None:
        self._update_status(task_id, "error", error=error[:500])

    def _update_status(
        self,
        task_id: str,
        status: str,
        *,
        card_message_id: str | None = None,
        error: str | None = None,
    ) -> None:
        with self._lock:
            if card_message_id is not None:
                self._conn.execute(
                    """
                    UPDATE tasks
                    SET status = ?, card_message_id = ?, error = ?, updated_at = ?
                    WHERE task_id = ?
                    """,
                    (status, card_message_id, error, utcnow(), task_id),
                )
            elif error is not None:
                self._conn.execute(
                    "UPDATE tasks SET status = ?, error = ?, updated_at = ? WHERE task_id = ?",
                    (status, error, utcnow(), task_id),
                )
            else:
                self._conn.execute(
                    "UPDATE tasks SET status = ?, updated_at = ? WHERE task_id = ?",
                    (status, utcnow(), task_id),
                )
            self._conn.commit()

    def recent_history(
        self,
        chat_id: str,
        limit: int,
        *,
        exclude_message_id: str | None = None,
    ) -> list[dict[str, str]]:
        if limit <= 0:
            return []
        with self._lock:
            if exclude_message_id:
                rows = self._conn.execute(
                    """
                    SELECT role, text, ts FROM history
                    WHERE chat_id = ? AND IFNULL(message_id, '') != ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (chat_id, exclude_message_id, limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """
                    SELECT role, text, ts FROM history
                    WHERE chat_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (chat_id, limit),
                ).fetchall()
        return [
            {"role": row["role"], "text": row["text"], "ts": row["ts"]} for row in reversed(rows)
        ]

    def append_history(
        self,
        *,
        chat_id: str,
        role: str,
        text: str,
        ts: str | None = None,
        message_id: str | None = None,
    ) -> None:
        with self._lock:
            self._append_history_locked(
                chat_id=chat_id,
                message_id=message_id,
                role=role,
                text=text,
                ts=ts or utcnow(),
            )
            self._conn.commit()

    def _append_history_locked(
        self,
        *,
        chat_id: str,
        message_id: str | None,
        role: str,
        text: str,
        ts: str,
    ) -> None:
        if message_id:
            existing = self._conn.execute(
                "SELECT 1 FROM history WHERE chat_id = ? AND message_id = ? AND role = ?",
                (chat_id, message_id, role),
            ).fetchone()
            if existing:
                return
        self._conn.execute(
            """
            INSERT INTO history (chat_id, message_id, role, text, ts)
            VALUES (?, ?, ?, ?, ?)
            """,
            (chat_id, message_id, role, text, ts),
        )
        self._conn.execute(
            """
            DELETE FROM history
            WHERE chat_id = ?
              AND id NOT IN (
                SELECT id FROM history WHERE chat_id = ? ORDER BY id DESC LIMIT 200
              )
            """,
            (chat_id, chat_id),
        )

    def inflight_tasks(self, chat_id: str) -> list[Task]:
        placeholders = ",".join("?" for _ in _INFLIGHT)
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT * FROM tasks
                WHERE chat_id = ? AND kind = 'task' AND status IN ({placeholders})
                ORDER BY created_at
                """,
                (chat_id, *_INFLIGHT),
            ).fetchall()
        return [_task(row) for row in rows]

    def recent_tasks(self, chat_id: str, limit: int = 5) -> list[Task]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM tasks
                WHERE chat_id = ? AND kind = 'task'
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (chat_id, limit),
            ).fetchall()
        return [_task(row) for row in rows]

    def claim_result_file(self, file_name: str, task_id: str) -> bool:
        """Return True when this result file has not been delivered yet."""

        with self._lock:
            row = self._conn.execute(
                """
                INSERT OR IGNORE INTO processed_results (file_name, task_id, processed_at)
                VALUES (?, ?, ?)
                """,
                (file_name, task_id, utcnow()),
            )
            self._conn.commit()
        return row.rowcount == 1

    def release_result_file(self, file_name: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM processed_results WHERE file_name = ?", (file_name,))
            self._conn.commit()


def _task(row: sqlite3.Row) -> Task:
    return Task(
        task_id=row["task_id"],
        event_id=row["event_id"],
        chat_id=row["chat_id"],
        chat_type=row["chat_type"],
        message_id=row["message_id"],
        root_id=row["root_id"],
        thread_id=row["thread_id"],
        sender_open_id=row["sender_open_id"],
        sender_name=row["sender_name"],
        text=row["text"],
        kind=row["kind"],
        status=row["status"],
        card_message_id=row["card_message_id"],
        error=row["error"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
