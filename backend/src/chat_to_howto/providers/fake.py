"""Deterministic provider for safe unit, recovery, and synthetic E2E tests."""

from __future__ import annotations

import json
from typing import Literal

from chat_to_howto.providers.base import (
    CancellationToken,
    ProviderCancelled,
    ProviderFailure,
    ProviderRequest,
    ProviderResponse,
    ProviderTimeout,
)


FakeMode = Literal["success", "malformed", "timeout", "cancellation", "failure", "partial"]


class FakeProvider:
    """Never performs I/O and produces the same result for the same input."""

    def __init__(self, *, mode: FakeMode = "success", content: str | None = None) -> None:
        if mode not in {"success", "malformed", "timeout", "cancellation", "failure", "partial"}:
            raise ValueError("unsupported fake provider mode")
        self._mode: FakeMode = mode
        self._content = content
        self._call_count = 0
        self._request_payloads: list[bytes] = []

    def __repr__(self) -> str:
        return "<FakeProvider>"

    @property
    def name(self) -> str:
        return "fake"

    @property
    def call_count(self) -> int:
        return self._call_count

    @property
    def request_payloads(self) -> tuple[bytes, ...]:
        return tuple(self._request_payloads)

    async def generate(
        self,
        request: ProviderRequest,
        cancellation: CancellationToken,
    ) -> ProviderResponse:
        cancellation.raise_if_cancelled()
        self._call_count += 1
        self._request_payloads.append(bytes(request.payload))
        if self._mode == "timeout":
            raise ProviderTimeout("Provider request timed out.")
        if self._mode == "failure":
            raise ProviderFailure("Provider request failed.")
        if self._mode == "cancellation":
            await cancellation.wait()
            raise ProviderCancelled("Provider request was cancelled.")
        if self._mode == "malformed":
            content, finish_reason = "{", "stop"
        elif self._mode == "partial":
            content, finish_reason = '{"status":"complete"', "length"
        else:
            content = self._content or self._deterministic_result(request)
            finish_reason = "stop"
        cancellation.raise_if_cancelled()
        return ProviderResponse(
            provider=self.name,
            model=request.model,
            content=content,
            finish_reason=finish_reason,
            prompt_tokens=max(1, len(request.payload) // 4),
            completion_tokens=max(1, len(content.encode("utf-8")) // 4),
            request_id=f"fake-request-{self._call_count}",
        )

    @staticmethod
    def _deterministic_result(request: ProviderRequest) -> str:
        try:
            payload = json.loads(request.payload)
            messages = payload.get("messages", []) if isinstance(payload, dict) else []
            first = messages[0] if isinstance(messages, list) and messages else None
            message_id = first.get("message_id") if isinstance(first, dict) else None
        except (TypeError, UnicodeDecodeError, ValueError):
            message_id = None
        if not isinstance(message_id, str) or not message_id.strip():
            result: dict[str, object] = {
                "status": "insufficient_evidence",
                "title": "选定范围内未找到足够依据",
                "preparations": [],
                "steps": [],
                "checks": [],
                "missing_information": [],
                "conflicts": [],
            }
            evidence: list[dict[str, str]] = []
        else:
            first_text = first.get("text") if isinstance(first, dict) else None
            result = {
                "status": "complete",
                "title": "确定性测试操作指南",
                "preparations": [],
                "steps": [
                    {
                        "instruction": "按选定消息执行操作",
                        "location": None,
                        "parameters": [],
                        "conditions": [],
                        "warnings": [],
                        "expected_result": None,
                        "source_message_ids": [message_id],
                    }
                ],
                "checks": [],
                "missing_information": [],
                "conflicts": [],
            }
            evidence = [
                {
                    "message_id": message_id,
                    "quote": first_text if isinstance(first_text, str) else "synthetic evidence",
                }
            ]
        return json.dumps(
            {"result": result, "evidence": evidence},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
