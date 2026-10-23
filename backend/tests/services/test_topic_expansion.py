"""Topic-only expansion never promotes terms into chat facts."""

from chat_to_howto.providers.fake import FakeProvider
from chat_to_howto.services.topic_expansion import TopicExpansionService
from datetime import datetime, timedelta, timezone
import json
import pytest


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


def test_topic_preview_is_local_redacted_and_authorizes_exact_payload_once() -> None:
    provider = FakeProvider()
    service = _service(provider)
    preview = service.create_preview("联系 13800138000 讨论就业", session_id="owner")
    assert provider.call_count == 0
    assert "13800138000" not in preview.outbound_text
    assert "[已脱敏手机号]" in preview.outbound_text
    service.expand_authorized(preview.preview_id, preview_fingerprint=preview.preview_fingerprint,
        confirmed=True, session_id="owner")
    assert provider.call_count == 1
    assert provider.request_payloads[0].decode() == preview.outbound_text
    assert json.loads(provider.request_payloads[0])["topic"] == preview.topic
    with pytest.raises(PermissionError):
        service.expand_authorized(preview.preview_id, preview_fingerprint=preview.preview_fingerprint,
            confirmed=True, session_id="owner")
    assert provider.call_count == 1


def test_topic_expansion_rejects_unconfirmed_wrong_session_or_changed_fingerprint() -> None:
    provider = FakeProvider()
    service = _service(provider)
    preview = service.create_preview("就业", session_id="owner")
    for confirmed, session, fingerprint in (
        (False, "owner", preview.preview_fingerprint),
        (True, "other", preview.preview_fingerprint),
        (True, "owner", "0" * 64),
    ):
        with pytest.raises(PermissionError):
            service.expand_authorized(preview.preview_id, preview_fingerprint=fingerprint,
                confirmed=confirmed, session_id=session)
    assert provider.call_count == 0


def test_topic_expansion_authorization_expires_without_dispatch() -> None:
    provider = FakeProvider()
    now = datetime(2026, 10, 23, tzinfo=timezone.utc)
    service = TopicExpansionService(provider_factory=lambda _: provider,
        provider="fake", model="deterministic", clock=lambda: now)
    preview = service.create_preview("就业", session_id="owner")
    now += timedelta(minutes=5)
    with pytest.raises(PermissionError):
        service.expand_authorized(preview.preview_id, preview_fingerprint=preview.preview_fingerprint,
            confirmed=True, session_id="owner")
    assert provider.call_count == 0
