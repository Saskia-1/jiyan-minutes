"""Provider-neutral, body-free language-model request contract."""

from __future__ import annotations

import asyncio
from threading import Event, Lock
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ProviderError(RuntimeError):
    """Safe base error which must never contain prompt or response content."""


class ProviderConfigurationError(ProviderError, ValueError):
    """The selected provider is missing required backend-only configuration."""


class ProviderCancelled(ProviderError):
    """Generation was cancelled before a complete provider result arrived."""


class ProviderTimeout(ProviderError):
    """The provider did not complete within the configured time budget."""


class ProviderFailure(ProviderError):
    """The provider request failed without exposing upstream details."""


class ProviderMalformedOutput(ProviderError):
    """The provider envelope was unsafe, oversized, or malformed."""


class ProviderRequest(BaseModel):
    """An already-authorized request. Content is deliberately hidden from repr/errors."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)

    model: str = Field(min_length=1, max_length=128)
    system_prompt: str = Field(min_length=1, max_length=100_000, repr=False)
    payload: bytes = Field(min_length=1, max_length=10_000_000, repr=False)
    max_output_tokens: int = Field(gt=0, le=1_000_000)
    thinking: Literal["disabled", "enabled"] = "disabled"
    reasoning_effort: Literal["low", "high", "max"] | None = None

    @model_validator(mode="after")
    def _validate_thinking(self) -> "ProviderRequest":
        if (self.thinking == "enabled") != (self.reasoning_effort is not None):
            raise ValueError("thinking mode and reasoning effort must be explicitly paired")
        return self

    @field_validator("model", "system_prompt")
    @classmethod
    def _reject_blank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("provider request fields must be nonblank")
        return value

    @field_validator("payload")
    @classmethod
    def _require_utf8_payload(cls, value: bytes) -> bytes:
        try:
            decoded = value.decode("utf-8")
        except UnicodeDecodeError:
            raise ValueError("provider payload must be UTF-8") from None
        if not decoded.strip():
            raise ValueError("provider payload must be nonblank")
        return value


class ProviderResponse(BaseModel):
    """Raw provider content; structured extraction validates it in the next stage."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)

    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    content: str = Field(min_length=1, repr=False)
    finish_reason: Literal[
        "stop",
        "length",
        "content_filter",
        "tool_calls",
        "insufficient_system_resource",
    ]
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    request_id: str | None = Field(default=None, min_length=1, max_length=256)

    @field_validator("provider", "model", "content")
    @classmethod
    def _reject_blank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("provider response fields must be nonblank")
        return value


class CancellationToken:
    """Cooperative cancellation shared by jobs and provider adapters."""

    __slots__ = ("_event", "_lock", "_waiters")

    def __init__(self) -> None:
        self._event = Event()
        self._lock = Lock()
        self._waiters: list[tuple[asyncio.AbstractEventLoop, asyncio.Future[None]]] = []

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self) -> None:
        self._event.set()
        with self._lock:
            waiters, self._waiters = self._waiters, []
        for loop, future in waiters:
            loop.call_soon_threadsafe(_finish_waiter, future)

    async def wait(self) -> None:
        if self.is_cancelled:
            return
        loop = asyncio.get_running_loop()
        future: asyncio.Future[None] = loop.create_future()
        with self._lock:
            if self.is_cancelled:
                future.set_result(None)
            else:
                self._waiters.append((loop, future))
        try:
            await future
        finally:
            with self._lock:
                self._waiters = [item for item in self._waiters if item[1] is not future]

    def raise_if_cancelled(self) -> None:
        if self.is_cancelled:
            raise ProviderCancelled("Provider request was cancelled.")


def _finish_waiter(future: asyncio.Future[None]) -> None:
    if not future.done():
        future.set_result(None)


@runtime_checkable
class LLMProvider(Protocol):
    """Provider abstraction used by the generation pipeline."""

    @property
    def name(self) -> str:
        ...

    async def generate(
        self,
        request: ProviderRequest,
        cancellation: CancellationToken,
    ) -> ProviderResponse:
        ...
