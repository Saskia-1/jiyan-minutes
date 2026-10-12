"""Mixed evidence must be rehydrated from its owned local source, not AI text."""

import pytest

from chat_to_howto.api.session import digest
from chat_to_howto.domain.generation import GenerationBudget, GenerationResult, TopicSection, TopicSummary
from chat_to_howto.domain.mixed_sources import SourceRef
from chat_to_howto.repositories.chat import ChatRepository
from chat_to_howto.repositories.content import ContentRepository
from chat_to_howto.services.evidence_validation import EvidenceValidationRejected, EvidenceValidator
from chat_to_howto.services.generation_extraction import EvidenceQuote
from chat_to_howto.services.generation_merge import MergedGeneration
from chat_to_howto.services.generation_planning import MixedGenerationScopeBuilder, MixedGenerationSelection
from chat_to_howto.services.mixed_search import MixedSearchService


def _selection(batch_id: str, unit_id: str) -> MixedGenerationSelection:
    return MixedGenerationSelection(
        source_refs=(SourceRef(kind="content_batch", id=batch_id),),
        included_unit_ids=(unit_id,), topic="合成主题", mode="complete",
        summary_type="ideas", include_undated=True, start_time=None, end_time=None,
        provider="fake", model="fake-model", thinking="disabled", reasoning_effort=None,
        redaction_policy_revision="text-only-outbound-v1",
        budget=GenerationBudget(max_messages=10, max_input_tokens=100_000, max_provider_calls=4),
    )


def _merged(unit_id: str, quote: str) -> MergedGeneration:
    return MergedGeneration(
        source_extraction_ids=("extraction:v1:synthetic",),
        result=GenerationResult(
            status="complete", title="合成主题", preparations=(), steps=(), checks=(),
            missing_information=(), conflicts=(),
            summary=TopicSummary(summary_type="ideas", sections=(TopicSection(
                heading="合成想法", text="合成内容", source_message_ids=(unit_id,),
            ),)),
        ),
        evidence_quotes=(EvidenceQuote(message_id=unit_id, quote=quote),),
    )


def test_mixed_note_evidence_restores_original_body_and_unknown_metadata(tmp_path):
    chat = ChatRepository(tmp_path / "chat.sqlite3")
    content = ContentRepository(chat)
    unit_id = "content:v1:note:" + "a" * 64
    body = "一段合成笔记"
    content.save_batch(batch_id="batch-a", session_digest=digest("owner"), created_at_utc="2026-10-12T00:00:00+00:00", images=[], units=[
        {"unit_id": unit_id, "image_id": None, "kind": "note_passage", "group_id": "note-1", "source_order": 0, "text": body, "sender_name": None, "sent_at": None},
    ])
    try:
        scope = MixedGenerationScopeBuilder(MixedSearchService(chat, content)).build(_selection("batch-a", unit_id), session_id="owner")
        validator = EvidenceValidator(chat, content)
        verified = validator.validate(_merged(unit_id, body), scope=scope, session_id="owner")
        assert verified.schema_version == 2
        assert verified.evidence[0].quote == body
        assert verified.evidence[0].source_kind == "note_passage"
        assert verified.evidence[0].source_batch_id == "batch-a"
        assert verified.evidence[0].timestamp is None
        assert verified.evidence[0].sender_name is None
        with pytest.raises(EvidenceValidationRejected):
            validator.validate(_merged(unit_id, "changed"), scope=scope, session_id="owner")
        with pytest.raises(EvidenceValidationRejected):
            validator.validate(_merged(unit_id, body), scope=scope, session_id="other")
    finally:
        chat.close()
