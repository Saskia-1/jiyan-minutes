"""A mixed scope freezes explicitly selected typed units without altering v1."""

from datetime import datetime, timezone

import pytest

from chat_to_howto.api.session import digest
from chat_to_howto.domain.generation import GenerationBudget
from chat_to_howto.domain.mixed_sources import SourceRef
from chat_to_howto.repositories.chat import ChatRepository
from chat_to_howto.repositories.content import ContentRepository


def _selection(batch_id: str, ids: tuple[str, ...], excluded: tuple[str, ...] = ()):
    from chat_to_howto.services.generation_planning import MixedGenerationSelection

    return MixedGenerationSelection(
        source_refs=(SourceRef(kind="content_batch", id=batch_id),), included_unit_ids=ids,
        excluded_unit_ids=excluded, topic="合成主题", mode="complete", summary_type="ideas",
        include_undated=True, start_time=None, end_time=None, provider="fake", model="fake-model",
        thinking="disabled", reasoning_effort=None, redaction_policy_revision="text-only-outbound-v1",
        budget=GenerationBudget(max_messages=100, max_input_tokens=100_000, max_provider_calls=4, max_output_tokens=4_000),
    )


def test_mixed_note_scope_reuses_chunk_planner_and_isolates_cache(tmp_path):
    from chat_to_howto.services.generation_planning import MixedGenerationScopeBuilder, build_generation_plan
    from chat_to_howto.services.generation_cache import GenerationCacheSettings, build_cache_identity
    from chat_to_howto.services.generation_scope import MixedGenerationScope
    from chat_to_howto.services.mixed_search import MixedSearchService

    chat = ChatRepository(tmp_path / "chat.sqlite3")
    content = ContentRepository(chat)
    unit_id = "content:v1:note:" + "a" * 64
    content.save_batch(batch_id="note-batch", session_digest=digest("owner"), created_at_utc="2026-10-12T00:00:00+00:00", images=[], units=[
        {"unit_id": unit_id, "image_id": None, "kind": "note_passage", "group_id": "note-1", "source_order": 0, "text": "合成主题的正文", "sender_name": None, "sent_at": None},
    ])
    try:
        builder = MixedGenerationScopeBuilder(MixedSearchService(chat, content))
        scope = builder.build(_selection("note-batch", (unit_id,)), session_id="owner")
        assert isinstance(scope, MixedGenerationScope)
        assert scope.schema_version == 2
        assert [item.message_id for item in scope.messages] == [unit_id]
        assert scope.messages[0].source_kind == "note_passage"
        assert scope.messages[0].speaker is None
        plan = build_generation_plan(scope, max_chunk_input_tokens=8_000)
        assert plan.provider_calls == 1
        assert b'E001' in plan.chunks[0].payload
        assert unit_id.encode() not in plan.chunks[0].payload
        settings = GenerationCacheSettings(prompt_version="synthetic", output_schema_version="synthetic", chunk_strategy_version="synthetic", max_chunk_input_tokens=8_000, provider_behavior_revision="synthetic")
        assert build_cache_identity(scope, settings).snapshot_id.startswith("mixed:v2:")
        assert MixedGenerationScope.model_validate_json(scope.model_dump_json()) == scope
    finally:
        chat.close()


def test_excluded_or_unowned_units_never_reenter_a_mixed_scope(tmp_path):
    from chat_to_howto.services.generation_planning import MixedGenerationScopeBuilder
    from chat_to_howto.services.mixed_search import MixedSearchService

    chat = ChatRepository(tmp_path / "chat.sqlite3")
    content = ContentRepository(chat)
    first = "content:v1:note:" + "a" * 64
    second = "content:v1:note:" + "b" * 64
    content.save_batch(batch_id="note-batch", session_digest=digest("owner"), created_at_utc="2026-10-12T00:00:00+00:00", images=[], units=[
        {"unit_id": value, "image_id": None, "kind": "note_passage", "group_id": "note-1", "source_order": index, "text": f"合成主题 {index}", "sender_name": None, "sent_at": None}
        for index, value in enumerate((first, second))
    ])
    try:
        builder = MixedGenerationScopeBuilder(MixedSearchService(chat, content))
        scope = builder.build(_selection("note-batch", (first, second), (second,)), session_id="owner")
        assert [item.message_id for item in scope.messages] == [first]
        assert scope.excluded_message_ids == (second,)
        with pytest.raises(ValueError):
            builder.build(_selection("note-batch", (first, "content:v1:note:" + "c" * 64)), session_id="owner")
        with pytest.raises(PermissionError):
            builder.build(_selection("note-batch", (first,)), session_id="other")
    finally:
        chat.close()


def test_mixed_note_uses_existing_consent_job_provider_and_document_pipeline(tmp_path):
    from chat_to_howto.providers.fake import FakeProvider
    from chat_to_howto.services.documents import DocumentService
    from chat_to_howto.services.generation_cache import GenerationCacheSettings
    from chat_to_howto.services.generation_jobs import GenerationJobOrchestrator
    from chat_to_howto.services.mixed_search import MixedSearchService

    chat = ChatRepository(tmp_path / "chat.sqlite3")
    content = ContentRepository(chat)
    unit_id = "content:v1:note:" + "d" * 64
    content.save_batch(batch_id="note-batch", session_digest=digest("owner"), created_at_utc="2026-10-12T00:00:00+00:00", images=[], units=[
        {"unit_id": unit_id, "image_id": None, "kind": "note_passage", "group_id": "note-1", "source_order": 0, "text": "合成主题的想法", "sender_name": None, "sent_at": None},
    ])
    provider = FakeProvider()
    settings = GenerationCacheSettings(prompt_version="synthetic", output_schema_version="synthetic", chunk_strategy_version="synthetic", max_chunk_input_tokens=8_000, provider_behavior_revision="synthetic")
    orchestrator = GenerationJobOrchestrator(repository=chat, provider_factory=lambda _: provider, cache_settings=settings, clock=lambda: datetime(2026, 10, 12, tzinfo=timezone.utc), mixed_search=MixedSearchService(chat, content))
    try:
        preview = orchestrator.create_mixed_preview(_selection("note-batch", (unit_id,)), session_id="owner")
        assert preview.provider_calls == 1
        assert unit_id not in preview.outbound_payloads[0]
        consent = orchestrator.issue_consent(preview.preview_id, session_id="owner", confirmed=True)
        job = orchestrator.start_job(consent.consent_id, session_id="owner", idempotency_key="mixed-job")
        completed = orchestrator.run_job(job.job_id)
        assert completed.state == "complete", (completed.error_code, provider.call_count, completed.completed_chunks)
        assert provider.call_count == 1
        assert completed.result.schema_version == 2
        assert completed.result.evidence[0].message_id == unit_id
        document = DocumentService(repository=chat, clock=lambda: datetime(2026, 10, 12, tzinfo=timezone.utc)).create_from_generation(job.job_id, session_id="owner")
        assert document.source_snapshot_id is None
        assert document.source_refs == (SourceRef(kind="content_batch", id="note-batch"),)
        assert document.source_evidence[0].quote == "合成主题的想法"
    finally:
        chat.close()
