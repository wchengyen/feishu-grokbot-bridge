"""Environment configuration. Secrets stay in the process environment."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

DEFAULT_FEISHU_DOMAIN = "https://open.feishu.cn"
DEFAULT_AUTH_HEADER = "Authorization"
DEFAULT_AUTH_SCHEME = "Bearer"

_LOCAL_HTTP_HOSTS = {"localhost", "127.0.0.1", "::1"}


class ConfigError(ValueError):
    """Raised when required settings are missing or invalid. Never includes secret values."""


@dataclass(frozen=True)
class Settings:
    feishu_app_id: str
    feishu_app_secret: str
    feishu_allowed_open_ids: frozenset[str]
    feishu_bot_open_id: str
    feishu_domain: str
    grokbot_webhook_url: str
    grokbot_webhook_key: str
    grokbot_webhook_auth_header: str
    grokbot_webhook_auth_scheme: str
    outbox_dir: Path
    db_path: Path
    history_turns: int
    health_file: Path | None
    log_level: str
    webhook_timeout_seconds: float
    webhook_max_retries: int
    webhook_retry_base_seconds: float
    webhook_retry_max_seconds: float
    outbox_poll_seconds: float
    outbox_partial_grace_seconds: float
    text_split_chars: int
    text_file_chars: int
    health_interval_seconds: float
    health_stale_seconds: float

    def auth_header(self) -> tuple[str, str]:
        """Return ``(header_name, header_value)`` for the Grok Bot sender key.

        The routine panel is the source of truth for this format. The default
        matches the panel field documented as ``Authorization: Bearer <key>``.
        Set ``GROKBOT_WEBHOOK_AUTH_SCHEME`` empty to send the key with no scheme.
        """

        scheme = self.grokbot_webhook_auth_scheme.strip()
        if scheme:
            value = f"{scheme} {self.grokbot_webhook_key}"
        else:
            value = self.grokbot_webhook_key
        return self.grokbot_webhook_auth_header, value

    @property
    def webhook_tls(self) -> bool:
        return urlsplit(self.grokbot_webhook_url).scheme == "https"


def load_settings(
    environ: Mapping[str, str] | None = None,
    *,
    dotenv_path: Path | None = None,
) -> Settings:
    """Load settings.

    When ``environ`` is omitted, the process environment is used and
    ``dotenv_path`` fills only keys that are not already set. Tests should
    pass ``environ`` explicitly so a developer ``.env`` cannot leak in.
    """

    if environ is None:
        merged: dict[str, str] = {}
        if dotenv_path is not None and dotenv_path.is_file():
            merged.update(parse_dotenv(dotenv_path.read_text(encoding="utf-8")))
        merged.update(os.environ)
        env = merged
    else:
        env = environ

    missing = [
        name
        for name in (
            "FEISHU_APP_ID",
            "FEISHU_APP_SECRET",
            "GROKBOT_WEBHOOK_URL",
            "GROKBOT_WEBHOOK_KEY",
        )
        if not _raw(env, name)
    ]
    if missing:
        raise ConfigError("missing required environment variables: " + ", ".join(missing))

    webhook_url = _raw(env, "GROKBOT_WEBHOOK_URL")
    _validate_webhook_url(webhook_url)
    header = _raw(env, "GROKBOT_WEBHOOK_AUTH_HEADER") or DEFAULT_AUTH_HEADER
    scheme = env.get("GROKBOT_WEBHOOK_AUTH_SCHEME", DEFAULT_AUTH_SCHEME)
    if scheme is None:
        scheme = DEFAULT_AUTH_SCHEME
    key = _raw(env, "GROKBOT_WEBHOOK_KEY")
    _reject_header_injection(
        "GROKBOT_WEBHOOK_AUTH_HEADER",
        header,
    )
    _reject_header_injection("GROKBOT_WEBHOOK_AUTH_SCHEME", scheme)
    _reject_header_injection("GROKBOT_WEBHOOK_KEY", key)

    app_id = _raw(env, "FEISHU_APP_ID")
    app_secret = _raw(env, "FEISHU_APP_SECRET")
    _reject_header_injection("FEISHU_APP_ID", app_id)
    _reject_header_injection("FEISHU_APP_SECRET", app_secret)

    domain = _raw(env, "FEISHU_DOMAIN") or DEFAULT_FEISHU_DOMAIN
    _validate_feishu_domain(domain)

    history_turns = _int(env, "BRIDGE_HISTORY_TURNS", 10, minimum=0, maximum=200)
    split_chars = _int(env, "BRIDGE_TEXT_SPLIT_CHARS", 4000, minimum=200, maximum=100_000)
    file_default = max(12_000, split_chars)
    file_chars = _int(
        env, "BRIDGE_TEXT_FILE_CHARS", file_default, minimum=split_chars, maximum=2_000_000
    )
    log_level = (_raw(env, "BRIDGE_LOG_LEVEL") or "INFO").upper()
    if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise ConfigError("BRIDGE_LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR, or CRITICAL")

    if "BRIDGE_HEALTH_FILE" in env:
        raw_health = env.get("BRIDGE_HEALTH_FILE", "").strip()
        health_file = Path(raw_health).expanduser() if raw_health else None
    else:
        health_file = Path("./data/health.json")

    return Settings(
        feishu_app_id=app_id,
        feishu_app_secret=app_secret,
        feishu_allowed_open_ids=_open_ids(_raw(env, "FEISHU_ALLOWED_OPEN_IDS")),
        feishu_bot_open_id=_raw(env, "FEISHU_BOT_OPEN_ID"),
        feishu_domain=domain.rstrip("/"),
        grokbot_webhook_url=webhook_url,
        grokbot_webhook_key=key,
        grokbot_webhook_auth_header=header,
        grokbot_webhook_auth_scheme=scheme,
        outbox_dir=Path(_raw(env, "BRIDGE_OUTBOX_DIR") or "./outbox").expanduser(),
        db_path=Path(_raw(env, "BRIDGE_DB_PATH") or "./data/bridge.db").expanduser(),
        history_turns=history_turns,
        health_file=health_file,
        log_level=log_level,
        webhook_timeout_seconds=_float(
            env, "BRIDGE_WEBHOOK_TIMEOUT_SECONDS", 30, minimum=1, maximum=120
        ),
        webhook_max_retries=_int(env, "BRIDGE_WEBHOOK_MAX_RETRIES", 5, minimum=1, maximum=10),
        webhook_retry_base_seconds=_float(
            env, "BRIDGE_WEBHOOK_RETRY_BASE_SECONDS", 1, minimum=0.1, maximum=60
        ),
        webhook_retry_max_seconds=_float(
            env, "BRIDGE_WEBHOOK_RETRY_MAX_SECONDS", 30, minimum=0.1, maximum=300
        ),
        outbox_poll_seconds=_float(env, "BRIDGE_OUTBOX_POLL_SECONDS", 2, minimum=0.2, maximum=60),
        outbox_partial_grace_seconds=_float(
            env, "BRIDGE_OUTBOX_PARTIAL_GRACE_SECONDS", 5, minimum=0, maximum=120
        ),
        text_split_chars=split_chars,
        text_file_chars=file_chars,
        health_interval_seconds=_float(
            env, "BRIDGE_HEALTH_INTERVAL_SECONDS", 10, minimum=1, maximum=300
        ),
        health_stale_seconds=_float(
            env, "BRIDGE_HEALTH_STALE_SECONDS", 30, minimum=1, maximum=3600
        ),
    )


def parse_dotenv(text: str) -> dict[str, str]:
    """Parse a dotenv file. Values are not logged. Existing process env wins later."""

    env: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key:
            env[key] = value
    return env


def _open_ids(raw: str) -> frozenset[str]:
    parts = []
    for item in raw.replace("\n", ",").split(","):
        item = item.strip()
        if item:
            parts.append(item)
    return frozenset(parts)


def _raw(env: Mapping[str, str], name: str) -> str:
    value = env.get(name)
    if value is None:
        return ""
    return value.strip()


def _validate_webhook_url(url: str) -> None:
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    if parsed.scheme not in {"https", "http"} or not host:
        raise ConfigError("GROKBOT_WEBHOOK_URL must be an absolute http(s) URL")
    if parsed.scheme != "https" and host not in _LOCAL_HTTP_HOSTS:
        raise ConfigError("GROKBOT_WEBHOOK_URL must use https")
    if parsed.username or parsed.password:
        raise ConfigError("GROKBOT_WEBHOOK_URL must not embed credentials")


def _validate_feishu_domain(domain: str) -> None:
    parsed = urlsplit(domain)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ConfigError("FEISHU_DOMAIN must be an https origin such as https://open.feishu.cn")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment or parsed.username:
        raise ConfigError("FEISHU_DOMAIN must be an origin without a path or query")


def _reject_header_injection(name: str, value: str) -> None:
    if any(ch in value for ch in "\r\n\x00"):
        raise ConfigError(f"{name} contains a forbidden control character")


def _int(
    env: Mapping[str, str],
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    raw = _raw(env, name)
    if not raw:
        value = default
    else:
        try:
            value = int(raw)
        except ValueError as exc:
            raise ConfigError(f"{name} must be an integer") from exc
    if value < minimum or value > maximum:
        raise ConfigError(f"{name} must be between {minimum} and {maximum}")
    return value


def _float(
    env: Mapping[str, str],
    name: str,
    default: float,
    *,
    minimum: float,
    maximum: float,
) -> float:
    raw = _raw(env, name)
    if not raw:
        value = default
    else:
        try:
            value = float(raw)
        except ValueError as exc:
            raise ConfigError(f"{name} must be a number") from exc
    if value < minimum or value > maximum:
        raise ConfigError(f"{name} must be between {minimum} and {maximum}")
    return value
