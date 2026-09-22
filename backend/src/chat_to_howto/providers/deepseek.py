"""Bounded DeepSeek JSON-output adapter with a fixed external destination."""

from __future__ import annotations

import asyncio
import json
import math
import os
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
_FINISH_REASONS = frozenset(
    {"stop", "length", "content_filter", "tool_calls", "insufficient_system_resource"}
)


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
        body = {
            "model": request.model,
            "messages": [
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": request.payload.decode("utf-8")},
            ],
            "response_format": {"type": "json_object"},
            # The bounded output budget is for the structured document, not hidden reasoning.
            "thinking": {"type": "disabled"},
            # This adapter produces grounded structured data, not creative chat.
            "temperature": 0,
            "max_tokens": request.max_output_tokens,
            "stream": False,
        }
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
                    if not 200 <= response.status_code < 300:
                        raise ProviderFailure("Provider request failed.")
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(chunk) > self._max_response_bytes - len(raw):
                            raise ProviderMalformedOutput("Provider response was malformed.")
                        raw.extend(chunk)
        except ProviderFailure:
            raise
        except ProviderMalformedOutput:
            raise
        except httpx.TimeoutException:
            raise ProviderTimeout("Provider request timed out.") from None
        except httpx.RequestError:
            raise ProviderFailure("Provider request failed.") from None
        return self._parse_response(bytes(raw), request.model)

    @staticmethod
    def _parse_response(raw: bytes, requested_model: str) -> ProviderResponse:
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
