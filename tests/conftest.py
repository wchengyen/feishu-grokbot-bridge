"""Shared fixtures. Tests must not open real sockets."""

from __future__ import annotations

import socket

import pytest

from bridge.config import Settings, load_settings


@pytest.fixture
def env(tmp_path) -> dict[str, str]:
    return {
        "FEISHU_APP_ID": "cli_test",
        "FEISHU_APP_SECRET": "test-app-secret-value",
        "FEISHU_ALLOWED_OPEN_IDS": "ou_owner",
        "FEISHU_BOT_OPEN_ID": "ou_bot",
        "GROKBOT_WEBHOOK_URL": "https://example.com/hook",
        "GROKBOT_WEBHOOK_KEY": "test-sender-key-value",
        "BRIDGE_OUTBOX_DIR": str(tmp_path / "outbox"),
        "BRIDGE_DB_PATH": str(tmp_path / "bridge.db"),
        "BRIDGE_HEALTH_FILE": str(tmp_path / "health.json"),
        "BRIDGE_HISTORY_TURNS": "10",
        "BRIDGE_OUTBOX_PARTIAL_GRACE_SECONDS": "5",
        "BRIDGE_TEXT_SPLIT_CHARS": "4000",
        "BRIDGE_TEXT_FILE_CHARS": "12000",
    }


@pytest.fixture
def settings(env) -> Settings:
    return load_settings(env)


@pytest.fixture(autouse=True)
def _block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def connect(self: socket.socket, address: object) -> None:
        host = address[0] if isinstance(address, tuple) else ""
        if host in {"127.0.0.1", "::1", "localhost"}:
            return None
        raise AssertionError(f"unexpected network connection to {address!r}")

    monkeypatch.setattr(socket.socket, "connect", connect)
