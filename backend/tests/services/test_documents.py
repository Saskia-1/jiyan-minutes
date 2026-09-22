"""P3 documents keep AI output and every user edit as distinct immutable versions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

import pytest

from chat_to_howto.api.session import digest
from chat_to_howto.domain.generation import GenerationBudget, GenerationResult
from chat_to_howto.domain.jobs import ExportConversation, ExportScope
from chat_to_howto.domain.messages import MessageInput
from chat_to_howto.providers.fake import FakeProvider
from chat_to_howto.repositories.chat import ChatRepository
from chat_to_howto.services.documents import (
    DocumentAuthorizationError,
    DocumentConflict,
    DocumentService,
)
from chat_to_howto.services.generation_cache import GenerationCacheSettings
from chat_to_howto.services.generation_jobs import GenerationJobOrchestrator
from chat_to_howto.services.generation_planning import GenerationSelection


UTC = timezone.utc
NOW = datetime(2026, 9, 5, tzinfo=UTC)


def _generated(tmp_path, *, insufficient=False):
    repository = ChatRepository(tmp_path / "chat.sqlite3")
    scope = ExportScope(
        source_id="synthetic",
        account_id="account-a",
        conversations=(ExportConversation(conversation_id="conversation-a"),),
        range_start=datetime(2026, 9, 1, tzinfo=UTC),
        range_end=datetime(2026, 9, 3, tzinfo=UTC),
        display_timezone="UTC",
        output_root_id="approved-root",
        configuration_nonce="nonce-a",
    )
    repository.create_batch("batch-a", scope)
    message = repository.record_message(
        MessageInput(
            source_id="synthetic",
            account_id="account-a",
            conversation_id="conversation-a",
            source_message_id="m1",
            source_file_id=None,
            page_slice_id=None,
            source_order=1,
            sent_at_utc=datetime(2026, 9, 1, 1, tzinfo=UTC),
            original_timestamp="2026-09-01T01:00:00+00:00",
            sender_id="sender-a",
            sender_name="Synthetic Sender",
            text="synthetic instruction",
        )
    )
    repository.add_message_to_batch("batch-a", message.message_id)
    repository.create_snapshot("snapshot-a", "batch-a")
    repository.create_export_consent(
        consent_id="export-consent",
        session_digest=digest("session-a"),
        scope=scope,
        recorded_at_utc=NOW.isoformat(),
        expires_at_utc=(NOW + timedelta(minutes=5)).isoformat(),
    )
    export, _ = repository.start_export_job(
        job_id="export-job",
        planned_batch_id="batch-a",
        planned_snapshot_id="snapshot-a",
        session_digest=digest("session-a"),
        scope=scope,
        consent_id="export-consent",
        idempotency_key="export-key",
        now_utc=NOW.isoformat(),
    )
    assert repository.cas_export_job(
        "export-job",
        expected_state="pending_authorization",
        expected_revision=int(export["revision"]),
        new_state="complete",
        batch_id="batch-a",
        snapshot_id="snapshot-a",
        searchable=1,
    )
    provider = FakeProvider(content=json.dumps({"result": GenerationResult.insufficient().model_dump(), "evidence": []}) if insufficient else None)
    generator = GenerationJobOrchestrator(
        repository=repository,
        provider_factory=lambda _name: provider,
        cache_settings=GenerationCacheSettings(
            prompt_version="howto-prompt-v1",
            output_schema_version="generation-envelope-v1",
            chunk_strategy_version="message-boundary-v1",
            max_chunk_input_tokens=4_000,
            provider_behavior_revision="fake-v1",
        ),
        clock=lambda: NOW,
    )
    selection = GenerationSelection(
        snapshot_id="snapshot-a",
        conversation_ids=("conversation-a",),
        start_time=datetime(2026, 9, 1, tzinfo=UTC),
        end_time=datetime(2026, 9, 2, tzinfo=UTC),
        topic="instruction",
        mode="complete",
        provider="fake",
        model="deterministic",
        redaction_policy_revision="outbound-v1",
        excluded_message_ids=(),
        budget=GenerationBudget(
            max_messages=100,
            max_input_tokens=20_000,
            max_provider_calls=4,
            max_output_tokens=2_000,
        ),
    )
    preview = generator.create_preview(selection, session_id="session-a")
    consent = generator.issue_consent(
        preview.preview_id, session_id="session-a", confirmed=True
    )
    job = generator.start_job(
        consent.consent_id, session_id="session-a", idempotency_key="generation-key"
    )
    completed = generator.run_job(job.job_id)
    assert completed.state == "complete"
    return repository, provider, completed.job_id, message.message_id


def test_insufficient_ai_result_can_be_freely_edited_without_changing_ai_original(tmp_path):
    repository, provider, job_id, _ = _generated(tmp_path, insufficient=True)
    try:
        service = DocumentService(repository=repository, clock=lambda: NOW)
        original = service.create_from_generation(job_id, session_id="session-a")
        edited = service.create_version(
            original.document_id,
            parent_version_id=original.current_version.version_id,
            content=original.current_version.content.model_copy(update={
                "title": "用户自己的标题", "user_text": "用户自己的标题\n\n我的补充内容",
            }),
            change_summary="用户确认完整文本", session_id="session-a",
        )
        recovered = service.get_document(original.document_id, session_id="session-a")
        assert recovered.current_version == edited.current_version
        assert recovered.current_version.content.title == "用户自己的标题"
        assert recovered.current_version.content.user_text == "用户自己的标题\n\n我的补充内容"
        assert recovered.current_version.origin == "user_edited"
        assert recovered.versions[0] == original.current_version
        assert recovered.versions[0].content == GenerationResult.insufficient()
        assert provider.call_count == 1
    finally:
        repository.close()


def test_ai_version_and_user_edit_are_append_only_and_have_distinct_provenance(tmp_path):
    repository, provider, job_id, _message_id = _generated(tmp_path)
    service = DocumentService(repository=repository, clock=lambda: NOW)
    try:
        document = service.create_from_generation(job_id, session_id="session-a")
        again = service.create_from_generation(job_id, session_id="session-a")
        assert again.document_id == document.document_id
        assert len(document.versions) == 1
        original = document.current_version
        assert original.origin == "ai_generated"
        assert original.evidence_status == "verified"

        edited_content = original.content.model_copy(
            update={"title": "<script>alert(1)</script> **literal title**"}
        )
        edited = service.create_version(
            document.document_id,
            parent_version_id=original.version_id,
            content=edited_content,
            change_summary="Adjusted title",
            session_id="session-a",
        )
        assert edited.current_version.origin == "user_edited"
        assert edited.current_version.evidence_status == "unverified"
        assert edited.current_version.parent_version_id == original.version_id
        assert edited.current_version.content.title == "<script>alert(1)</script> **literal title**"
        assert edited.versions[0] == original
        assert provider.call_count == 1
    finally:
        repository.close()


def test_user_edit_cannot_invent_evidence_or_replay_a_stale_parent(tmp_path):
    repository, _provider, job_id, _message_id = _generated(tmp_path)
    service = DocumentService(repository=repository, clock=lambda: NOW)
    try:
        document = service.create_from_generation(job_id, session_id="session-a")
        original = document.current_version
        invented = original.content.model_copy(
            update={
                "steps": (
                    original.content.steps[0].model_copy(
                        update={"source_message_ids": ("message:v1:" + "9" * 64,)}
                    ),
                )
            }
        )
        with pytest.raises(DocumentConflict, match="evidence"):
            service.create_version(
                document.document_id,
                parent_version_id=original.version_id,
                content=invented,
                change_summary="bad",
                session_id="session-a",
            )

        first_edit = service.create_version(
            document.document_id,
            parent_version_id=original.version_id,
            content=original.content.model_copy(update={"title": "First edit"}),
            change_summary="first",
            session_id="session-a",
        )
        with pytest.raises(DocumentConflict, match="current"):
            service.create_version(
                document.document_id,
                parent_version_id=original.version_id,
                content=original.content.model_copy(update={"title": "Stale edit"}),
                change_summary="stale",
                session_id="session-a",
            )
        assert first_edit.current_version.content.title == "First edit"
    finally:
        repository.close()


def test_restore_creates_a_new_version_and_cross_session_access_is_rejected(tmp_path):
    repository, _provider, job_id, _message_id = _generated(tmp_path)
    service = DocumentService(repository=repository, clock=lambda: NOW)
    try:
        document = service.create_from_generation(job_id, session_id="session-a")
        original = document.current_version
        edited = service.create_version(
            document.document_id,
            parent_version_id=original.version_id,
            content=original.content.model_copy(update={"title": "Changed"}),
            change_summary="edit",
            session_id="session-a",
        )
        restored = service.restore_version(
            document.document_id,
            version_id=original.version_id,
            session_id="session-a",
        )
        assert len(restored.versions) == 3
        assert restored.current_version.origin == "restored"
        assert restored.current_version.parent_version_id == edited.current_version.version_id
        assert restored.current_version.restored_from_version_id == original.version_id
        assert restored.current_version.content == original.content
        assert restored.versions[0] == original

        with pytest.raises(DocumentAuthorizationError):
            service.get_document(document.document_id, session_id="session-b")
        with pytest.raises(DocumentAuthorizationError):
            service.create_from_generation(job_id, session_id="session-b")
    finally:
        repository.close()


def test_documents_can_be_listed_and_reopened_after_repository_restart(tmp_path):
    database_path = tmp_path / "chat.sqlite3"
    repository, _provider, job_id, _message_id = _generated(tmp_path)
    service = DocumentService(repository=repository, clock=lambda: NOW)
    document = service.create_from_generation(job_id, session_id="session-a")
    edited = service.create_version(
        document.document_id,
        parent_version_id=document.current_version.version_id,
        content=document.current_version.content.model_copy(update={"title": "指南 version 2"}),
        change_summary="P0 refresh recovery",
        session_id="session-a",
    )
    repository.close()

    reopened_repository = ChatRepository(database_path)
    reopened_service = DocumentService(repository=reopened_repository, clock=lambda: NOW)
    try:
        page = reopened_service.list_documents(session_id="session-a")
        assert [item.document_id for item in page.documents] == [document.document_id]
        recovered = reopened_service.get_document(document.document_id, session_id="session-a")
        assert recovered.current_version.version_id == edited.current_version.version_id
        assert recovered.current_version.ordinal == 2
        assert recovered.current_version.content.title == "指南 version 2"
    finally:
        reopened_repository.close()
