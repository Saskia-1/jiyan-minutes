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
            if isinstance(payload, dict) and payload.get("operation") == "expand_topic":
                topic = payload.get("topic")
                terms = {
                    "就业": ["就业", "求职", "秋招", "实习", "offer", "面试", "简历"],
                    "腾讯音乐黑客松": ["腾讯音乐黑客松", "音乐", "BGM", "哼鸣", "歌单", "标签", "music", "创意"],
                }.get(topic, [topic])
                return json.dumps({"terms": terms}, ensure_ascii=False, sort_keys=True)
            summary = isinstance(payload, dict) and payload.get("generation_kind") == "summary"
            summary_type = payload.get("summary_type") if isinstance(payload, dict) else None
            messages = payload.get("messages", []) if isinstance(payload, dict) else []
            first = messages[0] if isinstance(messages, list) and messages else None
            message_id = first.get("message_id") if isinstance(first, dict) else None
        except (TypeError, UnicodeDecodeError, ValueError):
            summary = False
            summary_type = None
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
        elif summary_type in {"viewpoints", "ideas", "how_to", "mixed"}:
            # Contract fixture, deliberately not a semantic-quality model.
            identifiers = [message["message_id"] for message in messages]
            text = "\n".join(message["text"] for message in messages)
            result = {
                "status": "complete", "title": f"确定性测试：{payload.get('topic', '')}",
                "preparations": [], "steps": [], "checks": [],
                "missing_information": [], "conflicts": [],
            }
            if summary_type == "how_to":
                result["steps"] = [{
                    "instruction": text, "location": None, "parameters": [],
                    "conditions": [], "warnings": [], "expected_result": None,
                    "source_message_ids": identifiers,
                }]
            else:
                result["summary"] = {"summary_type": summary_type, "sections": [{
                    "heading": {"viewpoints": "观点及依据", "ideas": "构思与使用场景", "mixed": "主题信息"}[summary_type],
                    "text": text, "source_message_ids": identifiers,
                }]}
            evidence = [{"message_id": message["message_id"]} for message in messages]
        else:
            first_text = first.get("text") if isinstance(first, dict) else None
            result = (
                {
                    "status": "complete", "title": "确定性测试聊天总结",
                    "preparations": [], "steps": [], "checks": [],
                    "missing_information": [], "conflicts": [],
                    "summary": {
                        "core_summary": [{"text": first_text, "source_message_ids": [message_id]}],
                        "discussions": [], "decisions": [], "action_items": [],
                        "open_questions": [],
                    },
                }
                if summary else {
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
            )
            evidence = [
                {
                    "message_id": message_id,
                    "quote": first_text if isinstance(first_text, str) else "synthetic evidence",
                }
            ]
        return json.dumps(
            {"result": result} if summary_type else {"result": result, "evidence": evidence},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
