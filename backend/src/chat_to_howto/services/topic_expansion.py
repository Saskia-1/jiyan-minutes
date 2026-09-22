"""Bounded provider-assisted keyword expansion; no chat messages are sent."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import json

from pydantic import BaseModel, ConfigDict, Field

from chat_to_howto.providers.base import CancellationToken, LLMProvider, ProviderRequest


_PROMPT = (
    "Return JSON only: {\"terms\":[...]}. 为本地聊天的字面子串检索扩展5-10个关键词，必须包含原主题。"
    "聊天可能从未出现活动/项目全称，不要只返回专名的翻译、别名或重复拼接该专名。"
    "应包含主题的核心领域词，以及可能在口语聊天里独立出现的相关问题、产品机制、场景或功能概念；"
    "必须单独保留核心领域短词，例如音乐活动中的‘音乐’，不能只返回‘音乐科技’‘音乐App’这种限定复合词。"
    "至少一半是脱离活动名称也能独立检索的短词。不添加搜索运算符，不组成多词搜索句。"
    "例如就业可扩展求职、秋招、实习、面试、简历；领域活动还应拆出领域本身及相关创作/应用概念。"
    "例如音乐主题活动，可用音乐、旋律、BGM、歌单、推荐、音乐社交等口语检索词，而不是十种活动名称。"
    "避免问题、内容、事情等无主题区分度的泛词。这些词仅为召回线索，不代表聊天事实，不编写正文。"
)
_BROAD = frozenset({"相关", "问题", "情况", "事情", "内容", "工作", "其他", "讨论"})


class TopicExpansionView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    topic: str
    terms: tuple[str, ...]
    degraded: bool


class TopicExpansionService:
    def __init__(
        self, *, provider_factory: Callable[[str], LLMProvider], provider: str,
        model: str,
    ) -> None:
        self._provider_factory = provider_factory
        self._provider = provider
        self._model = model

    def expand(self, topic: str) -> TopicExpansionView:
        normalized = topic.strip()
        if not normalized or len(normalized) > 512:
            raise ValueError("topic must be nonblank and bounded")
        fallback = TopicExpansionView(topic=normalized, terms=(normalized,), degraded=True)
        try:
            provider = self._provider_factory(self._provider)
            response = asyncio.run(provider.generate(
                ProviderRequest(
                    model=self._model, system_prompt=_PROMPT,
                    payload=json.dumps(
                        {"operation": "expand_topic", "topic": normalized},
                        ensure_ascii=False, separators=(",", ":"),
                    ).encode("utf-8"),
                    max_output_tokens=256,
                ),
                CancellationToken(),
            ))
            if response.finish_reason != "stop":
                return fallback
            parsed = json.loads(response.content)
            if not isinstance(parsed, dict) or set(parsed) != {"terms"}:
                return fallback
            candidates = parsed["terms"]
            if not isinstance(candidates, list) or len(candidates) > 30:
                return fallback
            terms = [normalized]
            seen = {normalized.casefold()}
            for candidate in candidates:
                if not isinstance(candidate, str):
                    return fallback
                term = candidate.strip()
                if (
                    not 2 <= len(term) <= 40
                    or any(ord(character) < 32 or ord(character) == 127 for character in term)
                    or term in _BROAD
                    or term.casefold() in seen
                ):
                    continue
                terms.append(term)
                seen.add(term.casefold())
                if len(terms) == 10:
                    break
            return TopicExpansionView(
                topic=normalized, terms=tuple(terms), degraded=len(terms) == 1,
            )
        except Exception:
            return fallback
