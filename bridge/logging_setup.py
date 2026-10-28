"""JSON logs that redact configured secrets."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime

from bridge.config import Settings

_STANDARD_ATTRS = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}


class RedactFilter(logging.Filter):
    def __init__(self, secrets: list[str]) -> None:
        super().__init__()
        self._secrets = [secret for secret in secrets if secret and len(secret) >= 8]

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = _redact(message, self._secrets)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        for key, value in list(record.__dict__.items()):
            if key in _STANDARD_ATTRS or not isinstance(value, str):
                continue
            record.__dict__[key] = _redact(value, self._secrets)
        return True


class JsonFormatter(logging.Formatter):
    def __init__(self, secrets: list[str]) -> None:
        super().__init__()
        self._secrets = [secret for secret in secrets if secret and len(secret) >= 8]

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        for key, value in record.__dict__.items():
            if key in _STANDARD_ATTRS or key.startswith("_"):
                continue
            payload[key] = value
        line = json.dumps(payload, ensure_ascii=False, default=str)
        return _redact(line, self._secrets)


def configure_logging(settings: Settings) -> None:
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    secrets = [settings.feishu_app_secret, settings.grokbot_webhook_key]
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(secrets))
    handler.addFilter(RedactFilter(secrets))
    root.addHandler(handler)
    root.setLevel(settings.log_level)
    # The SDK logs on its own logger. Keep it at the same level and make sure
    # DEBUG is opt-in: request traces can include headers.
    logging.getLogger("lark_oapi").setLevel(settings.log_level)


def _redact(text: str, secrets: list[str]) -> str:
    for secret in secrets:
        if secret in text:
            text = text.replace(secret, "***")
    return text
