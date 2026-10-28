import httpx
import pytest

from bridge.config import load_settings
from bridge.webhook import PermanentWebhookError, post_with_retry


class _Response:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class _Client:
    def __init__(self, results: list[object]) -> None:
        self.results = list(results)
        self.calls: list[dict] = []

    def post(self, url: str, json: dict | None = None, headers: dict | None = None) -> _Response:
        self.calls.append({"url": url, "json": json, "headers": headers})
        item = self.results.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _post(client: _Client, sleeps: list[float], **kwargs: object) -> None:
    post_with_retry(
        client,
        "https://example.com/hook",
        {"task_id": "t"},
        headers=kwargs.get("headers") or {"Authorization": "Bearer test-sender-key-value"},
        max_retries=kwargs.get("max_retries", 4),
        base_seconds=1,
        max_seconds=30,
        sleep=sleeps.append,
    )


def test_success_on_first_try() -> None:
    client = _Client([_Response(200)])
    sleeps: list[float] = []
    _post(client, sleeps)
    assert len(client.calls) == 1
    assert sleeps == []


def test_retries_500_then_200() -> None:
    client = _Client([_Response(500), _Response(502), _Response(200)])
    sleeps: list[float] = []
    _post(client, sleeps)
    assert len(client.calls) == 3
    assert sleeps == [1, 2]


def test_retries_429_and_transport_errors() -> None:
    client = _Client([httpx.ConnectError("down"), _Response(429), _Response(204)])
    sleeps: list[float] = []
    _post(client, sleeps)
    assert len(client.calls) == 3
    assert sleeps == [1, 2]


def test_client_error_is_not_retried() -> None:
    client = _Client([_Response(400), _Response(200)])
    sleeps: list[float] = []
    with pytest.raises(PermanentWebhookError) as exc:
        _post(client, sleeps)
    assert len(client.calls) == 1
    assert sleeps == []
    assert "test-sender-key-value" not in str(exc.value)


def test_exhausted_retries_hide_the_sender_key() -> None:
    client = _Client([httpx.ConnectError("test-sender-key-value")] * 3)
    sleeps: list[float] = []
    with pytest.raises(PermanentWebhookError) as exc:
        _post(client, sleeps, max_retries=3)
    assert "test-sender-key-value" not in str(exc.value)
    assert sleeps == [1, 2]


def test_default_auth_header_is_bearer(env) -> None:
    settings = load_settings(env)
    assert settings.auth_header() == ("Authorization", "Bearer test-sender-key-value")


def test_auth_header_and_scheme_are_configurable(env) -> None:
    env["GROKBOT_WEBHOOK_AUTH_HEADER"] = "X-Custom-Key"
    env["GROKBOT_WEBHOOK_AUTH_SCHEME"] = ""
    settings = load_settings(env)
    assert settings.auth_header() == ("X-Custom-Key", "test-sender-key-value")

    env["GROKBOT_WEBHOOK_AUTH_SCHEME"] = "Token"
    settings = load_settings(env)
    assert settings.auth_header() == ("X-Custom-Key", "Token test-sender-key-value")


def test_dispatcher_sends_configured_header(env) -> None:
    from bridge.webhook import WebhookClient

    env["GROKBOT_WEBHOOK_AUTH_HEADER"] = "X-Api-Key"
    env["GROKBOT_WEBHOOK_AUTH_SCHEME"] = "Bearer"
    settings = load_settings(env)
    client = _Client([_Response(200)])
    webhook = WebhookClient(settings, client=client, sleep=lambda _delay: None)
    webhook.post_task({"version": 1})
    assert client.calls[0]["headers"]["X-Api-Key"] == "Bearer test-sender-key-value"
    webhook.close()
