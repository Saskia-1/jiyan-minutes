"""Every model citation is re-read from immutable local evidence before verification."""

from __future__ import annotations

from datetime import datetime, timezone
import json

import pytest

from chat_to_howto.domain.generation import GenerationBudget, GenerationResult, OutboundMessage
from chat_to_howto.domain.messages import MessageInput, StoredMessage
from chat_to_howto.providers.base import ProviderResponse
from chat_to_howto.services.evidence_validation import (
    EvidenceValidationRejected,
    EvidenceValidator,
)
from chat_to_howto.services.generation_extraction import parse_structured_extraction
from chat_to_howto.services.generation_merge import merge_extractions
from chat_to_howto.services.generation_planning import GenerationChunk
from chat_to_howto.services.generation_scope import GenerationScope


UTC = timezone.utc
SENTINEL = "synthetic immutable evidence"


def _stored(
    source_message_id: str,
    *,
    conversation_id: str,
    sent_at: datetime,
    text: str,
) -> StoredMessage:
    return StoredMessage.from_input(
        MessageInput(
            source_id="synthetic",
            account_id="account-a",
            conversation_id=conversation_id,
            source_message_id=source_message_id,
            source_file_id=None,
            page_slice_id=None,
            source_order=1,
            sent_at_utc=sent_at,
            original_timestamp=sent_at.isoformat(),
            sender_id="sender-a",
            sender_name="Synthetic Sender",
            text=text,
        )
    )


ALLOWED = _stored(
    "allowed",
    conversation_id="conversation-a",
    sent_at=datetime(2026, 9, 1, 1, tzinfo=UTC),
    text=SENTINEL,
)
OUT_OF_CONVERSATION = _stored(
    "outside-conversation",
    conversation_id="conversation-b",
    sent_at=datetime(2026, 9, 1, 2, tzinfo=UTC),
    text="synthetic outside conversation",
)
OUT_OF_TIME = _stored(
    "outside-time",
    conversation_id="conversation-a",
    sent_at=datetime(2026, 9, 3, tzinfo=UTC),
    text="synthetic outside time",
)


class EvidenceRepositoryStub:
    def generation_snapshot_messages(self, *, session_digest: str, snapshot_id: str):
        assert len(session_digest) == 64
        assert snapshot_id == "snapshot-a"
        return "batch-a", (ALLOWED, OUT_OF_CONVERSATION, OUT_OF_TIME)


def _scope(*, messages: tuple[OutboundMessage, ...] | None = None, **changes: object) -> GenerationScope:
    values: dict[str, object] = {
        "batch_id": "batch-a",
        "snapshot_id": "snapshot-a",
        "search_run_id": None,
        "mode": "complete",
        "topic": "synthetic topic",
        "provider": "fake",
        "model": "deterministic",
        "redaction_policy_revision": "outbound-v1",
        "conversation_ids": ("conversation-a",),
        "range_start": datetime(2026, 9, 1, tzinfo=UTC),
        "range_end": datetime(2026, 9, 2, tzinfo=UTC),
        "messages": messages
        if messages is not None
        else (OutboundMessage(message_id=ALLOWED.message_id, text=SENTINEL),),
        "excluded_message_ids": (),
        "budget": GenerationBudget(
            max_messages=10,
            max_input_tokens=10_000,
            max_provider_calls=2,
        ),
    }
    values.update(changes)
    return GenerationScope(**values)


def _merged(message_id: str, quote: str):
    chunk = GenerationChunk(
        chunk_id="chunk:v1:" + "1" * 64,
        index=0,
        messages=(OutboundMessage(message_id=ALLOWED.message_id, text=SENTINEL),),
        estimated_input_tokens=10,
        payload=b'{"synthetic":true}',
    )
    payload = {
        "result": {
            "status": "complete",
            "title": "Synthetic guide",
            "preparations": [],
            "steps": [
                {
                    "instruction": "Use the cited evidence",
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
        },
        "evidence": [{"message_id": message_id, "quote": quote}],
    }
    extraction = parse_structured_extraction(
        chunk,
        ProviderResponse(
            provider="fake",
            model="deterministic",
            content=json.dumps(payload),
            finish_reason="stop",
        ),
    )
    return merge_extractions((extraction,))


def test_valid_evidence_is_rehydrated_from_local_snapshot_and_marked_verified() -> None:
    verified = EvidenceValidator(EvidenceRepositoryStub()).validate(
        _merged(ALLOWED.message_id, SENTINEL),
        scope=_scope(),
        session_id="session-a",
    )

    assert verified.evidence_status == "verified"
    assert verified.result.steps[0].source_message_ids == (ALLOWED.message_id,)
    assert len(verified.evidence) == 1
    assert verified.evidence[0].message_id == ALLOWED.message_id
    assert verified.evidence[0].quote == SENTINEL
    assert verified.evidence[0].conversation_id == "conversation-a"
    assert verified.snapshot_id == "snapshot-a"


def test_summary_citation_rehydrates_original_sender_time_and_body() -> None:
    merged = _merged(ALLOWED.message_id, SENTINEL)
    result = GenerationResult.model_validate({
        "status": "complete", "title": "Synthetic summary",
        "preparations": [], "steps": [], "checks": [],
        "missing_information": [], "conflicts": [],
        "summary": {
            "core_summary": [{"text": "Cited conclusion", "source_message_ids": [ALLOWED.message_id]}],
            "discussions": [], "decisions": [], "action_items": [], "open_questions": [],
        },
    })

    verified = EvidenceValidator(EvidenceRepositoryStub()).validate(
        merged.model_copy(update={"result": result}),
        scope=_scope(generation_kind="summary"), session_id="session-a",
    )

    assert verified.evidence[0].sender_name == "Synthetic Sender"
    assert verified.evidence[0].timestamp == datetime(2026, 9, 1, 1, tzinfo=UTC)
    assert verified.evidence[0].quote == SENTINEL


@pytest.mark.parametrize(
    ("message_id", "quote", "label"),
    [
        ("message:v1:" + "9" * 64, "made up", "missing"),
        (OUT_OF_CONVERSATION.message_id, OUT_OF_CONVERSATION.text, "outside"),
        (OUT_OF_TIME.message_id, OUT_OF_TIME.text, "outside"),
        (ALLOWED.message_id, SENTINEL + " modified", "quote"),
    ],
)
def test_nonexistent_out_of_scope_out_of_time_and_modified_quotes_are_rejected(
    message_id: str,
    quote: str,
    label: str,
) -> None:
    with pytest.raises(EvidenceValidationRejected, match=label) as raised:
        EvidenceValidator(EvidenceRepositoryStub()).validate(
            _merged(message_id, quote),
            scope=_scope(),
            session_id="session-a",
        )
    assert SENTINEL not in str(raised.value)


def test_scope_text_is_rechecked_against_immutable_snapshot() -> None:
    tampered_scope = _scope(
        messages=(OutboundMessage(message_id=ALLOWED.message_id, text="tampered outbound copy"),)
    )
    with pytest.raises(EvidenceValidationRejected, match="scope"):
        EvidenceValidator(EvidenceRepositoryStub()).validate(
            _merged(ALLOWED.message_id, SENTINEL),
            scope=tampered_scope,
            session_id="session-a",
        )


def test_unreferenced_or_duplicate_conflicting_evidence_quote_is_rejected() -> None:
    merged = _merged(ALLOWED.message_id, SENTINEL)
    duplicate = merged.model_copy(
        update={
            "evidence_quotes": (
                *merged.evidence_quotes,
                merged.evidence_quotes[0].model_copy(update={"quote": "changed"}),
            )
        }
    )
    with pytest.raises(EvidenceValidationRejected, match="quote"):
        EvidenceValidator(EvidenceRepositoryStub()).validate(
            duplicate,
            scope=_scope(),
            session_id="session-a",
        )

    extra = merged.model_copy(
        update={
            "evidence_quotes": (
                *merged.evidence_quotes,
                merged.evidence_quotes[0].model_copy(
                    update={"message_id": OUT_OF_CONVERSATION.message_id}
                ),
            )
        }
    )
    with pytest.raises(EvidenceValidationRejected, match="unreferenced"):
        EvidenceValidator(EvidenceRepositoryStub()).validate(
            extra,
            scope=_scope(),
            session_id="session-a",
        )
