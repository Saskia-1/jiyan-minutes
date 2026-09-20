"""P2 frozen outbound-scope tests; no provider or network is used."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from chat_to_howto.domain.generation import GenerationBudget, OutboundMessage
from chat_to_howto.services.generation_scope import (
    GenerationScope,
    OutboundConsent,
    build_outbound_preview,
)


def _scope(**changes: object) -> GenerationScope:
    values: dict[str, object] = {
        "batch_id": "batch-a",
        "snapshot_id": "snapshot-a",
        "search_run_id": "search-a",
        "mode": "fast",
        "topic": "整理合成交付步骤",
        "provider": "openai",
        "model": "configured-model",
        "redaction_policy_revision": "redaction-v1",
        "conversation_ids": ("conversation-a",),
        "range_start": datetime(2026, 9, 1, tzinfo=timezone.utc),
        "range_end": datetime(2026, 9, 2, tzinfo=timezone.utc),
        "messages": (
            OutboundMessage(message_id="m1", text="这次交付统一为16:9。"),
            OutboundMessage(message_id="m2", text="只导出第3到8页，格式用PNG。"),
        ),
        "excluded_message_ids": ("m9",),
        "budget": GenerationBudget(max_messages=20, max_input_tokens=4000, max_provider_calls=3),
    }
    values.update(changes)
    return GenerationScope(**values)


def test_preview_payload_is_exactly_the_frozen_provider_payload() -> None:
    scope = _scope()
    preview = build_outbound_preview(scope)

    assert preview.payload == scope.provider_payload()
    assert preview.payload_sha256 == scope.payload_sha256()
    assert preview.message_count == 2
    assert preview.token_estimate_low <= preview.token_estimate_high
    assert preview.price_status == "unknown"
    assert b"account" not in preview.payload
    assert b"token" not in preview.payload.lower()


@pytest.mark.parametrize(
    "change",
    [
        {"batch_id": "batch-b"},
        {"snapshot_id": "snapshot-b"},
        {"search_run_id": "search-b"},
        {"mode": "complete"},
        {"topic": "另一个话题"},
        {"provider": "different-provider"},
        {"model": "different-model"},
        {"redaction_policy_revision": "redaction-v2"},
        {"conversation_ids": ("conversation-b",)},
        {"range_end": datetime(2026, 9, 3, tzinfo=timezone.utc)},
        {"messages": (OutboundMessage(message_id="m1", text="文字改变"),)},
        {"excluded_message_ids": ("m8",)},
        {"budget": GenerationBudget(max_messages=20, max_input_tokens=5000, max_provider_calls=3)},
    ],
)
def test_every_scope_payload_provider_model_or_budget_change_invalidates_fingerprint(
    change: dict[str, object]
) -> None:
    assert _scope().fingerprint() != _scope(**change).fingerprint()


def test_scope_rejects_duplicate_ids_excluded_overlap_empty_text_and_budget_overflow() -> None:
    with pytest.raises(ValidationError, match="duplicate"):
        _scope(
            messages=(
                OutboundMessage(message_id="m1", text="a"),
                OutboundMessage(message_id="m1", text="b"),
            )
        )
    with pytest.raises(ValidationError, match="excluded"):
        _scope(excluded_message_ids=("m1",))
    with pytest.raises(ValidationError):
        OutboundMessage(message_id="m1", text="")
    with pytest.raises(ValidationError, match="message budget"):
        _scope(budget=GenerationBudget(max_messages=1, max_input_tokens=4000, max_provider_calls=3))
    with pytest.raises(ValidationError, match="token budget"):
        _scope(
            messages=(OutboundMessage(message_id="m1", text="合" * 100),),
            budget=GenerationBudget(max_messages=20, max_input_tokens=10, max_provider_calls=3),
        )


def test_consent_matches_once_only_before_expiry_and_only_exact_scope() -> None:
    now = datetime(2026, 9, 1, tzinfo=timezone.utc)
    scope = _scope()
    consent = OutboundConsent.grant(
        scope,
        recorded_at=now,
        expires_at=now + timedelta(minutes=5),
    )

    assert consent.matches(scope, now=now + timedelta(minutes=4))
    assert not consent.matches(scope, now=now + timedelta(minutes=5))
    assert not consent.matches(_scope(model="different-model"), now=now)
    assert consent.consumed_at is None
    consumed = consent.consume(scope, now=now + timedelta(minutes=1))
    assert consumed.consumed_at == now + timedelta(minutes=1)
    assert not consumed.matches(scope, now=now + timedelta(minutes=2))


def test_scope_repr_and_validation_errors_hide_outbound_text() -> None:
    sentinel = "synthetic-private-outbound-body"
    scope = _scope(messages=(OutboundMessage(message_id="m1", text=sentinel),))
    assert sentinel not in repr(scope)
    assert sentinel not in repr(scope.messages[0])

    with pytest.raises(ValidationError) as raised:
        _scope(messages=(OutboundMessage(message_id="m1", text=sentinel),), provider="")
    assert sentinel not in str(raised.value)
