"""Topic-only expansion never promotes terms into chat facts."""

from chat_to_howto.providers.fake import FakeProvider
from chat_to_howto.services.topic_expansion import TopicExpansionService


def _service(provider: FakeProvider) -> TopicExpansionService:
    return TopicExpansionService(
        provider_factory=lambda _name: provider, provider="fake", model="deterministic",
    )


def test_fake_expansion_retains_topic_and_returns_stable_related_terms() -> None:
    result = _service(FakeProvider()).expand(" 就业 ")

    assert result.topic == "就业"
    assert result.terms == ("就业", "求职", "秋招", "实习", "offer", "面试", "简历")
    assert result.degraded is False


def test_expansion_deduplicates_and_rejects_overly_broad_terms() -> None:
    provider = FakeProvider(content='{"terms":["求职","就业","求职","工作","面试","相关","offer"]}')

    result = _service(provider).expand("就业")

    assert result.terms == ("就业", "求职", "面试", "offer")
    assert result.degraded is False


def test_expansion_failure_falls_back_without_chat_messages() -> None:
    provider = FakeProvider(mode="failure")

    result = _service(provider).expand("就业")

    assert result.terms == ("就业",)
    assert result.degraded is True
    assert b"messages" not in provider.request_payloads[0]
