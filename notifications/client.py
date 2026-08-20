"""Minimal Feishu Open Platform REST client.

Implements the two calls the notification module needs (auth/v3 tenant-token
and im/v1 message send) with bounded retry, rate-limit handling, and exhaust
classification.  Secret values (app secret, tokens, Authorization headers,
receive_ids, request bodies) are never included in exceptions or logs.

Author:
    Ellen Song <jiaqi.song@z.ai>
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import requests

TENANT_TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
SEND_MESSAGE_URL = "https://open.feishu.cn/open-apis/im/v1/messages"

#: Retryable HTTP status codes for the message API.
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
#: Feishu business error codes that are safe to retry.
RETRYABLE_BIZ_CODES = frozenset({99991663, 99991664, 99991668, 99991661})
DEFAULT_TIMEOUT = 15
DEFAULT_RETRIES = 4
BASE_BACKOFF_SECONDS = 2.0


class FeishuError(RuntimeError):
    """Base class for all Feishu client errors (sanitized message)."""


class AuthError(FeishuError):
    """Raised when tenant_access_token cannot be obtained."""


class TransientError(FeishuError):
    """Raised when a request exhausts regression-safe retries."""


class PermanentError(FeishuError):
    """Raised for 4xx/permission/business errors that should not be retried."""


@dataclass(frozen=True)
class FeishuResponse:
    """Normalized, sanitized outcome of a single API call."""

    code: int
    msg: str
    message_id: str | None


def _redact_exc(exc: BaseException) -> str:
    """Returns a sanitized one-line representation of an exception for logs."""
    return type(exc).__name__


def _retry_delay_seconds(attempt: int, response: requests.Response | None) -> float:
    """Returns a backoff delay that honors ``Retry-After`` when present."""
    if response is not None:
        retry_after = response.headers.get("Retry-After")
        if retry_after and retry_after.isdigit():
            return min(float(retry_after), 60.0)
    return min(BASE_BACKOFF_SECONDS * (2 ** attempt), 60.0)


def _parse_business(payload: Any) -> tuple[int, str]:
    """Returns the Feishu business ``code`` and ``msg`` from a payload."""
    if not isinstance(payload, dict):
        return -1, "non-dict Feishu response"
    code = int(payload.get("code", -1))
    msg = str(payload.get("msg") or payload.get("message") or "")
    return code, msg


def fetch_tenant_token(
    session: requests.Session,
    app_id: str,
    app_secret: str,
    *,
    timeout: int = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
) -> str:
    """Obtains and caches a ``tenant_access_token`` for the current process.

    The token is only returned to the caller; it is never written to disk or
    inserted into log messages.  Raises :class:`AuthError` on any failure.
    """
    payload = {"app_id": app_id, "app_secret": app_secret}
    last_error = ""
    for attempt in range(retries):
        try:
            response = session.post(
                TENANT_TOKEN_URL,
                json=payload,
                timeout=timeout,
            )
        except requests.RequestException as error:
            last_error = _redact_exc(error)
            if attempt < retries - 1:
                time.sleep(_retry_delay_seconds(attempt, None))
                continue
            raise AuthError(
                f"network error while obtaining tenant token: {last_error}"
            ) from error
        if response.status_code in RETRYABLE_STATUS:
            if attempt < retries - 1:
                time.sleep(_retry_delay_seconds(attempt, response))
                continue
            raise AuthError(
                f"tenant token HTTP {response.status_code} after {retries} attempts"
            )
        if response.status_code >= 400:
            raise AuthError(f"tenant token HTTP {response.status_code}")
        try:
            body = response.json()
        except ValueError:
            raise AuthError("tenant token returned non-JSON body")
        code, msg = _parse_business(body)
        if code != 0:
            raise AuthError(f"tenant token business code {code}: {msg}")
        if not body.get("tenant_access_token"):
            raise AuthError("tenant token missing from response")
        return str(body["tenant_access_token"])


def send_message(
    session: requests.Session,
    tenant_token: str,
    receive_id_type: str,
    receive_id: str,
    content_json_string: str,
    *,
    timeout: int = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
) -> FeishuResponse:
    """Sends one interactive-card message.

    ``content_json_string`` must already be the result of
    :func:`notifications.cards.encode_content` (the inner card serialized to a
    JSON string), because Feishu wraps it once more in the request body.
    """
    payload = {
        "receive_id": receive_id,
        "msg_type": "interactive",
        "content": content_json_string,
    }
    last_status: int | None = None
    last_code: int | None = None
    last_msg = ""
    for attempt in range(retries):
        try:
            response = session.post(
                SEND_MESSAGE_URL,
                params={"receive_id_type": receive_id_type},
                headers={
                    "Authorization": f"Bearer {tenant_token}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=timeout,
            )
        except requests.RequestException as error:
            if attempt < retries - 1:
                time.sleep(_retry_delay_seconds(attempt, None))
                continue
            raise TransientError(
                f"network error while sending message: {_redact_exc(error)}"
            ) from error

        last_status = response.status_code
        if response.status_code in RETRYABLE_STATUS:
            if attempt < retries - 1:
                time.sleep(_retry_delay_seconds(attempt, response))
                continue
            raise TransientError(
                f"send message HTTP {response.status_code} after {retries} attempts"
            )

        try:
            body = response.json()
        except ValueError:
            raise PermanentError(
                f"send message HTTP {response.status_code} returned non-JSON body"
            )
        code, msg = _parse_business(body)
        last_code = code
        last_msg = msg
        if code != 0:
            if code in RETRYABLE_BIZ_CODES and attempt < retries - 1:
                time.sleep(_retry_delay_seconds(attempt, response))
                continue
            # 4xx permission/argument errors should not be blindly retried.
            raise PermanentError(
                f"send message business code {code} (http {response.status_code})"
            )
        if response.status_code >= 400:
            raise PermanentError(f"send message HTTP {response.status_code}")
        message_id_raw = body.get("data", {}).get("message_id")
        return FeishuResponse(
            code=code,
            msg=msg,
            message_id=str(message_id_raw) if message_id_raw else None,
        )

    # Defensive: the loop should always exit via return or raise.
    raise TransientError(
        f"send message exhausted retries (last http={last_status}, "
        f"code={last_code})"
    )