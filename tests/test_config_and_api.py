import json

import pytest

from bridge.config import ConfigError, load_settings
from bridge.feishu_api import FeishuApi


def test_missing_required_names_only() -> None:
    with pytest.raises(ConfigError) as exc:
        load_settings({"FEISHU_APP_ID": "cli_test"})
    message = str(exc.value)
    assert "FEISHU_APP_SECRET" in message
    assert "GROKBOT_WEBHOOK_URL" in message
    assert "cli_test" not in message


def test_non_https_webhook_rejected() -> None:
    with pytest.raises(ConfigError):
        load_settings(
            {
                "FEISHU_APP_ID": "cli_test",
                "FEISHU_APP_SECRET": "secret-value-xx",
                "GROKBOT_WEBHOOK_URL": "http://example.com/hook",
                "GROKBOT_WEBHOOK_KEY": "sender-key-value",
            }
        )


def test_header_injection_rejected(env) -> None:
    env["GROKBOT_WEBHOOK_AUTH_HEADER"] = "Authorization\r\nX-Evil: 1"
    with pytest.raises(ConfigError):
        load_settings(env)


def test_check_ok(env, tmp_path, capsys) -> None:
    from bridge.main import run_check

    env["BRIDGE_OUTBOX_DIR"] = str(tmp_path / "outbox")
    env["BRIDGE_DB_PATH"] = str(tmp_path / "data" / "bridge.db")
    env["BRIDGE_HEALTH_FILE"] = ""
    (tmp_path / "data").mkdir()
    settings = load_settings(env)
    assert run_check(settings) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["config"] == "ok"
    assert report["health"] == "disabled"


def test_check_stale_health(env, tmp_path) -> None:
    from bridge.config import load_settings
    from bridge.main import run_check

    health = tmp_path / "health.json"
    health.write_text("{}", encoding="utf-8")
    import os

    os.utime(health, (1, 1))
    env["BRIDGE_HEALTH_FILE"] = str(health)
    env["BRIDGE_DB_PATH"] = str(tmp_path / "bridge.db")
    env["BRIDGE_OUTBOX_DIR"] = str(tmp_path / "outbox")
    (tmp_path / "outbox").mkdir()
    settings = load_settings(env)
    assert run_check(settings) == 1


def test_reply_interactive_uses_sdk_request_shape() -> None:
    captured = {}

    class _Data:
        message_id = "om_new"

    class _Response:
        code = 0
        msg = "ok"
        data = _Data()

        def success(self) -> bool:
            return True

        def get_log_id(self) -> str:
            return "log-1"

    class _Messages:
        def reply(self, request, option=None):
            captured["request"] = request
            return _Response()

    class _Client:
        def __init__(self) -> None:
            self.im = type("Im", (), {})()
            self.im.v1 = type("V1", (), {})()
            self.im.v1.message = _Messages()

    api = FeishuApi(_Client())
    message_id = api.reply_interactive(
        "om_user",
        {"header": {"title": {"content": "已收到，處理中…"}}},
        idempotency_key="task-ack",
        reply_in_thread=True,
    )
    assert message_id == "om_new"
    request = captured["request"]
    assert request.uri == "/open-apis/im/v1/messages/:message_id/reply"
    assert request.message_id == "om_user"
    assert request.body.msg_type == "interactive"
    assert request.body.reply_in_thread is True
    assert "已收到，處理中…" in request.body.content


def test_patch_and_upload_use_sdk_routes(tmp_path) -> None:
    captured = {}

    class _FileData:
        file_key = "file_1"

    class _Response:
        code = 0
        data = _FileData()

        def success(self) -> bool:
            return True

        def get_log_id(self) -> str:
            return "log"

    class _Messages:
        def patch(self, request, option=None):
            captured["patch"] = request
            return _Response()

    class _Files:
        def create(self, request, option=None):
            captured["file"] = request
            return _Response()

    class _Client:
        def __init__(self) -> None:
            self.im = type("Im", (), {})()
            self.im.v1 = type("V1", (), {})()
            self.im.v1.message = _Messages()
            self.im.v1.file = _Files()

    api = FeishuApi(_Client())
    api.patch_card("om_card", {"elements": []})
    path = tmp_path / "notes.md"
    path.write_text("hello", encoding="utf-8")
    kind, key = api.upload_path(path)
    assert kind == "file"
    assert key == "file_1"
    assert captured["patch"].uri == "/open-apis/im/v1/messages/:message_id"
    assert captured["file"].uri == "/open-apis/im/v1/files"
    assert captured["file"].body.file_type == "stream"
    assert captured["file"].body.file_name == "notes.md"
