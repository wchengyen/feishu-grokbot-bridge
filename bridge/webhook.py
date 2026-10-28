"""POST a task to the Grok Bot routine webhook.

The sender-key header is configurable because the routine panel, not this
bridge, defines the exact format. The default is ``Authorization: Bearer <key>``.
Retries use exponential backoff for transport errors, HTTP 429, and HTTP 5xx.
A 2xx response means the routine run was accepted, not that the task finished.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

import httpx

from bridge.config import Settings

logger = logging.getLogger(__name__)


class PermanentWebhookError(RuntimeError):
    """The webhook will not succeed by retrying. The message never contains the key."""


class WebhookClient:
    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self._settings = settings
        self._client = client or httpx.Client(
            timeout=settings.webhook_timeout_seconds,
            follow_redirects=False,
        )
        self._owns_client = client is None
        self._sleep = sleep or time.sleep

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def post_task(self, payload: dict[str, Any]) -> None:
        header_name, header_value = self._settings.auth_header()
        post_with_retry(
            self._client,
            self._settings.grokbot_webhook_url,
            payload,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "feishu-grokbot-bridge/0.1.0",
                header_name: header_value,
            },
            max_retries=self._settings.webhook_max_retries,
            base_seconds=self._settings.webhook_retry_base_seconds,
            max_seconds=self._settings.webhook_retry_max_seconds,
            sleep=self._sleep,
        )


def post_with_retry(
    client: Any,
    url: str,
    payload: dict[str, Any],
    *,
    headers: dict[str, str],
    max_retries: int,
    base_seconds: float,
    max_seconds: float,
    sleep: Callable[[float], None],
) -> None:
    delay = base_seconds
    last_status: int | None = None
    for attempt in range(1, max_retries + 1):
        try:
            response = client.post(url, json=payload, headers=headers)
        except httpx.TransportError as exc:
            logger.warning(
                "webhook transport error",
                extra={"attempt": attempt, "error_type": type(exc).__name__},
            )
            if attempt == max_retries:
                raise PermanentWebhookError(
                    f"webhook transport error after {attempt} attempts ({type(exc).__name__})"
                ) from None
        else:
            status = response.status_code
            if 200 <= status < 300:
                logger.info("webhook accepted", extra={"attempt": attempt, "status": status})
                return
            last_status = status
            retryable = status == 429 or 500 <= status <= 599
            logger.warning(
                "webhook rejected",
                extra={"attempt": attempt, "status": status, "retryable": retryable},
            )
            if not retryable or attempt == max_retries:
                raise PermanentWebhookError(f"webhook failed status={status} attempts={attempt}")
        sleep(delay)
        delay = min(delay * 2, max_seconds)
    raise PermanentWebhookError(f"webhook failed status={last_status}")
