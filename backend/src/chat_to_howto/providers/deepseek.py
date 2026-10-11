"""Bounded DeepSeek JSON-output adapter with a fixed external destination."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
import json
import logging
import math
import os
import time
from typing import Any

import httpx

from chat_to_howto.providers.base import (
    CancellationToken,
    ProviderCancelled,
    ProviderConfigurationError,
    ProviderFailure,
    ProviderMalformedOutput,
    ProviderRequest,
    ProviderResponse,
    ProviderTimeout,
)


_ENDPOINT = "https://api.deepseek.com/chat/completions"
_LOGGER = logging.getLogger(__name__)
_FINISH_REASONS = frozenset(
    {"stop", "length", "content_filter", "tool_calls", "insufficient_system_resource"}
)
_SAFE_ERROR_TOKENS = frozenset({
    "invalid_request_error", "authentication_error", "invalid_api_key",
    "insufficient_balance", "rate_limit_error", "server_error",
    "service_unavailable_error", "model_not_found",
})
_STATUS_NOTES = {
    400: "invalid_format", 401: "authentication_failed", 402: "insufficient_balance",
    422: "invalid_parameters", 429: "rate_limit", 500: "server_error",
    503: "server_overloaded",
}


@dataclass(frozen=True, slots=True)
class DeepSeekFailureDiagnostic:
    """Body-free metadata for private diagnosis; never a public API error."""

    phase: str
    exception_type: str
    http_status: int | None
    service_error_code: str | None
    service_error_type: str | None
    status_note: str | None
    elapsed_ms: int
    received_bytes: int
    response_headers_received: bool
    response_body_complete: bool
    code_position: str


class DeepSeekRequestFailure(ProviderFailure):
    def __init__(self, diagnostic: DeepSeekFailureDiagnostic) -> None:
        super().__init__("Provider request failed.")
        self.diagnostic = diagnostic


def _record_failure(diagnostic: DeepSeekFailureDiagnostic) -> DeepSeekRequestFailure:
    _LOGGER.warning("DeepSeek provider failure: %s", json.dumps(asdict(diagnostic), sort_keys=True))
    return DeepSeekRequestFailure(diagnostic)


async def _read_safe_error_metadata(response: httpx.Response) -> tuple[int, bool, str | None, str | None]:
    """Inspect a small error envelope, never retaining its free-text message."""
    body = bytearray()
    bytes_read = 0
    try:
        async with asyncio.timeout(3):
            async for chunk in response.aiter_bytes():
                bytes_read += len(chunk)
                if len(body) + len(chunk) > 16_384:
                    return bytes_read, False, None, None
                body.extend(chunk)
    except (TimeoutError, httpx.RequestError):
        return bytes_read, False, None, None
    try:
        envelope = json.loads(body)
        error = envelope.get("error") if isinstance(envelope, dict) else None
        if not isinstance(error, dict):
            return bytes_read, True, None, None
        code, kind = error.get("code"), error.get("type")
        return (
            bytes_read, True,
            code if isinstance(code, str) and code in _SAFE_ERROR_TOKENS else None,
            kind if isinstance(kind, str) and kind in _SAFE_ERROR_TOKENS else None,
        )
    except (TypeError, ValueError):
        return bytes_read, True, None, None


class DeepSeekProvider:
    """Call only DeepSeek's fixed HTTPS Chat Completions JSON endpoint."""

    __slots__ = ("_api_key", "_max_response_bytes", "_timeout_seconds", "_transport")

    def __init__(
        self,
        *,
        api_key: str,
        timeout_seconds: float = 60.0,
        max_response_bytes: int = 1_048_576,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not isinstance(api_key, str) or not api_key.strip():
            raise ProviderConfigurationError("DEEPSEEK_API_KEY is not configured.")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise ValueError("provider timeout must be positive and finite")
        if (
            isinstance(max_response_bytes, bool)
            or not isinstance(max_response_bytes, int)
            or max_response_bytes <= 0
        ):
            raise ValueError("provider response limit must be positive")
        self._api_key = api_key
        self._timeout_seconds = float(timeout_seconds)
        self._max_response_bytes = max_response_bytes
        self._transport = transport

    def __repr__(self) -> str:
        return "<DeepSeekProvider>"

    @property
    def name(self) -> str:
        return "deepseek"

    @classmethod
    def from_environment(
        cls,
        *,
        timeout_seconds: float = 60.0,
        max_response_bytes: int = 1_048_576,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> "DeepSeekProvider":
        key = os.getenv("DEEPSEEK_API_KEY")
        if not key or not key.strip():
            raise ProviderConfigurationError("DEEPSEEK_API_KEY is not configured.")
        return cls(
            api_key=key,
            timeout_seconds=timeout_seconds,
            max_response_bytes=max_response_bytes,
            transport=transport,
        )

    async def generate(
        self,
        request: ProviderRequest,
        cancellation: CancellationToken,
    ) -> ProviderResponse:
        cancellation.raise_if_cancelled()
        request_task = asyncio.create_task(self._send(request))
        cancel_task = asyncio.create_task(cancellation.wait())
        try:
            done, _pending = await asyncio.wait(
                (request_task, cancel_task),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if cancel_task in done:
                request_task.cancel()
                await asyncio.gather(request_task, return_exceptions=True)
                raise ProviderCancelled("Provider request was cancelled.")
            cancel_task.cancel()
            await asyncio.gather(cancel_task, return_exceptions=True)
            return await request_task
        finally:
            for task in (request_task, cancel_task):
                if not task.done():
                    task.cancel()

    async def _send(self, request: ProviderRequest) -> ProviderResponse:
        started = time.monotonic()
        headers_received = False
        response_status: int | None = None
        bytes_read = 0
        stage = "await_headers"
        body = {
            "model": request.model,
            "messages": [
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": request.payload.decode("utf-8")},
            ],
            "response_format": {"type": "json_object"},
            "thinking": {"type": request.thinking},
            "max_tokens": request.max_output_tokens,
            "stream": False,
        }
        if request.thinking == "enabled":
            body["reasoning_effort"] = request.reasoning_effort
        else:
            # Preserve the existing deterministic non-thinking request.
            body["temperature"] = 0
        try:
            async with httpx.AsyncClient(
                transport=self._transport,
                follow_redirects=False,
                trust_env=False,
                timeout=httpx.Timeout(self._timeout_seconds),
            ) as client:
                async with client.stream(
                    "POST",
                    _ENDPOINT,
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json=body,
                ) as response:
                    headers_received = True
                    response_status = response.status_code
                    if not 200 <= response.status_code < 300:
                        bytes_read, body_complete, error_code, error_type = await _read_safe_error_metadata(response)
                        raise _record_failure(DeepSeekFailureDiagnostic(
                            phase="http_status", exception_type="HTTPResponseStatus",
                            http_status=response.status_code, service_error_code=error_code,
                            service_error_type=error_type,
                            status_note=_STATUS_NOTES.get(response.status_code, "http_error"),
                            elapsed_ms=max(0, int((time.monotonic() - started) * 1000)),
                            received_bytes=bytes_read, response_headers_received=True,
                            response_body_complete=body_complete,
                            code_position="deepseek._send.http_status",
                        ))
                    stage = "read_body"
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        bytes_read += len(chunk)
                        if len(chunk) > self._max_response_bytes - len(raw):
                            raise ProviderMalformedOutput("Provider response was malformed.")
                        raw.extend(chunk)
        except ProviderFailure:
            raise
        except ProviderMalformedOutput:
            raise
        except httpx.TimeoutException:
            raise ProviderTimeout("Provider request timed out.") from None
        except httpx.RequestError as error:
            phase = "connect" if isinstance(error, (httpx.ConnectError, httpx.ConnectTimeout)) else stage
            raise _record_failure(DeepSeekFailureDiagnostic(
                phase=phase, exception_type=type(error).__name__, http_status=response_status,
                service_error_code=None, service_error_type=None, status_note=None,
                elapsed_ms=max(0, int((time.monotonic() - started) * 1000)),
                received_bytes=bytes_read, response_headers_received=headers_received,
                response_body_complete=False,
                code_position=f"deepseek._send.{stage}",
            )) from None
        return self._parse_response(bytes(raw), request.model, require_model_match=request.thinking == "enabled")

    @staticmethod
    def _parse_response(raw: bytes, requested_model: str, *, require_model_match: bool = False) -> ProviderResponse:
        try:
            envelope = json.loads(raw)
            if not isinstance(envelope, dict):
                raise TypeError
            choices = envelope.get("choices")
            if not isinstance(choices, list) or len(choices) != 1:
                raise TypeError
            choice = choices[0]
            if not isinstance(choice, dict):
                raise TypeError
            finish_reason = choice.get("finish_reason")
            message = choice.get("message")
            if finish_reason not in _FINISH_REASONS or not isinstance(message, dict):
                raise TypeError
            content = message.get("content")
            if not isinstance(content, str) or not content.strip():
                raise TypeError
            usage = envelope.get("usage")
            prompt_tokens = _optional_token_count(usage, "prompt_tokens")
            completion_tokens = _optional_token_count(usage, "completion_tokens")
            model = envelope.get("model", requested_model)
            if not isinstance(model, str) or not model.strip():
                raise TypeError
            if require_model_match and envelope.get("model") != requested_model:
                raise TypeError
            request_id = envelope.get("id")
            if request_id is not None and (
                not isinstance(request_id, str)
                or not request_id.strip()
                or len(request_id) > 256
            ):
                raise TypeError
            return ProviderResponse(
                provider="deepseek",
                model=model,
                content=content,
                finish_reason=finish_reason,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                request_id=request_id,
            )
        except (KeyError, TypeError, UnicodeDecodeError, ValueError):
            raise ProviderMalformedOutput("Provider response was malformed.") from None


def _optional_token_count(usage: Any, key: str) -> int | None:
    if usage is None:
        return None
    if not isinstance(usage, dict):
        raise TypeError
    value = usage.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise TypeError
    return value
