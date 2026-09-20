"""Session-scoped search over immutable, imported chat snapshots."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from chat_to_howto.api.session import digest
from chat_to_howto.domain.jobs import ExportConversation, ExportScope
from chat_to_howto.domain.manifest import (
    ArtifactDigest,
    BatchManifest,
    ConversationEvidenceManifest,
    ConversationManifest,
)
from chat_to_howto.domain.messages import MessageInput, StoredMessage
from chat_to_howto.domain.search import SearchRequest
from chat_to_howto.repositories.chat import ChatRepository
from chat_to_howto.services.search import ChatSearchService


UTC = timezone.utc


def _scope() -> ExportScope:
    return ExportScope(
        source_id="synthetic-source",
        account_id="account-a",
        conversations=(
            ExportConversation(conversation_id="conversation-a", display_name="A"),
            ExportConversation(conversation_id="conversation-b", display_name="B"),
        ),
        range_start=datetime(2026, 9, 1, tzinfo=UTC),
        range_end=datetime(2026, 9, 4, tzinfo=UTC),
        display_timezone="Asia/Shanghai",
        output_root_id="approved-root",
        configuration_nonce="configuration-a",
    )


def _message(
    source_message_id: str,
    *,
    conversation_id: str,
    sent_at: datetime,
    text: str,
) -> StoredMessage:
    return StoredMessage.from_input(
        MessageInput(
            source_id="synthetic-source",
            account_id="account-a",
            conversation_id=conversation_id,
            source_message_id=source_message_id,
            source_file_id=None,
            page_slice_id=None,
            source_order=int(source_message_id.removeprefix("message-")),
            sent_at_utc=sent_at,
            original_timestamp=sent_at.isoformat(),
            sender_id="sender-a",
            sender_name="Synthetic Sender",
            message_type="text",
            text=text,
        )
    )


def _manifest(
    scope: ExportScope,
    messages: tuple[StoredMessage, ...],
    *,
    batch_id: str,
    snapshot_id: str,
) -> BatchManifest:
    timestamps = tuple(message.sent_at_utc for message in messages)
    return BatchManifest(
        export_batch_id=batch_id,
        snapshot_id=snapshot_id,
        artifact_set_id=f"artifact-{batch_id}",
        scope_fingerprint=scope.fingerprint(),
        output_root_id=scope.output_root_id,
        created_at_utc=datetime(2026, 9, 4, tzinfo=UTC),
        source_type="synthetic",
        source_id=scope.source_id,
        source_tool_version=scope.tool_version,
        client_version=scope.client_version,
        account_id=scope.account_id,
        source_account_namespace=scope.source_account_namespace,
        account_version=scope.account_version,
        conversations=tuple(
            ConversationManifest(
                conversation_id=item.conversation_id,
                display_name=item.display_name,
                conversation_type=item.conversation_type,
            )
            for item in scope.conversations
        ),
        requested_start_utc=scope.range_start,
        requested_end_utc=scope.range_end,
        display_timezone=scope.display_timezone,
        fetched_count=len(messages),
        saved_count=len(messages),
        duplicate_count=0,
        unknown_time_count=0,
        parse_error_count=0,
        earliest_saved_at_utc=min(timestamps),
        latest_saved_at_utc=max(timestamps),
        completeness="source_range_verified",
        completeness_reason="synthetic_search_fixture",
        conversation_evidence=tuple(
            ConversationEvidenceManifest(
                conversation_id=item.conversation_id,
                status="source_range_verified",
                reason="synthetic_search_fixture",
                requested_start_ms=int(scope.range_start.timestamp() * 1000),
                requested_end_ms=int(scope.range_end.timestamp() * 1000),
                observed_start_ms=int(
                    min(
                        message.sent_at_utc
                        for message in messages
                        if message.conversation_id == item.conversation_id
                    ).timestamp()
                    * 1000
                ),
                observed_end_ms=int(
                    max(
                        message.sent_at_utc
                        for message in messages
                        if message.conversation_id == item.conversation_id
                    ).timestamp()
                    * 1000
                ),
                fetched_count=sum(
                    message.conversation_id == item.conversation_id
                    for message in messages
                ),
                accepted_count=sum(
                    message.conversation_id == item.conversation_id
                    for message in messages
                ),
                duplicate_count=0,
                unknown_time_count=0,
                missing_id_count=0,
                parse_error_count=0,
                termination="fixture_exhausted",
            )
            for item in scope.conversations
        ),
        artifacts=(
            ArtifactDigest(
                relative_path="messages.jsonl", sha256="a" * 64, byte_size=1
            ),
            ArtifactDigest(
                relative_path="messages.txt", sha256="b" * 64, byte_size=1
            ),
        ),
        schema_version=scope.schema_version,
    )


def _make_searchable(
    repository: ChatRepository,
    *,
    scope: ExportScope,
    messages: tuple[StoredMessage, ...],
    session_id: str,
    suffix: str,
    searchable: bool = True,
) -> None:
    batch_id = f"batch-{suffix}"
    snapshot_id = f"snapshot-{suffix}"
    repository.import_snapshot_atomic(
        batch_id=batch_id,
        snapshot_id=snapshot_id,
        scope=scope,
        messages=messages,
        manifest=_manifest(
            scope, messages, batch_id=batch_id, snapshot_id=snapshot_id
        ),
    )
    now = datetime(2026, 9, 4, tzinfo=UTC)
    repository.create_export_consent(
        consent_id=f"consent-{suffix}",
        session_digest=digest(session_id),
        scope=scope,
        recorded_at_utc=now.isoformat(),
        expires_at_utc=(now + timedelta(minutes=5)).isoformat(),
    )
    row, created = repository.start_export_job(
        job_id=f"job-{suffix}",
        planned_batch_id=batch_id,
        planned_snapshot_id=snapshot_id,
        session_digest=digest(session_id),
        scope=scope,
        consent_id=f"consent-{suffix}",
        idempotency_key=f"idempotency-{suffix}",
        now_utc=now.isoformat(),
    )
    assert created
    assert repository.cas_export_job(
        str(row["job_id"]),
        expected_state="pending_authorization",
        expected_revision=0,
        new_state="complete",
        batch_id=batch_id,
        snapshot_id=snapshot_id,
        searchable=int(searchable),
    )


@pytest.fixture
def search_service(tmp_path):
    repository = ChatRepository(tmp_path / "chat.sqlite3")
    scope = _scope()
    messages = (
        _message(
            "message-1",
            conversation_id="conversation-a",
            sent_at=datetime(2026, 9, 1, 1, tzinfo=UTC),
            text="literal 100%_safe",
        ),
        _message(
            "message-2",
            conversation_id="conversation-b",
            sent_at=datetime(2026, 9, 2, 1, tzinfo=UTC),
            text="pagination target",
        ),
        _message(
            "message-3",
            conversation_id="conversation-a",
            sent_at=datetime(2026, 9, 3, 1, tzinfo=UTC),
            text="pagination target newest",
        ),
    )
    _make_searchable(
        repository,
        scope=scope,
        messages=messages,
        session_id="session-a",
        suffix="a",
    )
    yield ChatSearchService(repository), repository, messages
    repository.close()


def test_search_intersects_session_conversations_time_and_literal_query(search_service) -> None:
    service, _, messages = search_service
    request = SearchRequest(
        query="100%_safe",
        conversation_ids=("conversation-a",),
        start_time=datetime(2026, 9, 1, tzinfo=UTC),
        end_time=datetime(2026, 9, 2, tzinfo=UTC),
        limit=20,
    )

    page = service.search(request, session_id="session-a")

    assert [result.message_id for result in page.results] == [messages[0].message_id]
    result = page.results[0]
    assert result.conversation_id == "conversation-a"
    assert result.timestamp == datetime(2026, 9, 1, 1, tzinfo=UTC)
    assert result.sender_name == "Synthetic Sender"
    assert result.text == "literal 100%_safe"
    assert result.batch_id == "batch-a"
    assert result.snapshot_id == "snapshot-a"


def test_search_uses_deterministic_bound_cursor_and_deduplicates_snapshots(search_service) -> None:
    service, repository, messages = search_service
    _make_searchable(
        repository,
        scope=_scope(),
        messages=messages,
        session_id="session-a",
        suffix="duplicate-snapshot",
    )
    request = SearchRequest(
        query="pagination target",
        conversation_ids=("conversation-a", "conversation-b"),
        start_time=datetime(2026, 9, 1, tzinfo=UTC),
        end_time=datetime(2026, 9, 4, tzinfo=UTC),
        limit=1,
    )

    first = service.search(request, session_id="session-a")
    second = service.search(
        request.model_copy(update={"cursor": first.next_cursor}),
        session_id="session-a",
    )

    assert [item.message_id for item in first.results] == [messages[2].message_id]
    assert [item.message_id for item in second.results] == [messages[1].message_id]
    assert second.next_cursor is None
    assert len({item.message_id for item in (*first.results, *second.results)}) == 2
    with pytest.raises(ValueError, match="cursor does not match"):
        service.search(
            request.model_copy(
                update={"query": "different", "cursor": first.next_cursor}
            ),
            session_id="session-a",
        )


def test_search_never_exposes_another_session_or_unsearchable_snapshot(search_service) -> None:
    service, repository, _ = search_service
    hidden = (
        _message(
            "message-9",
            conversation_id="conversation-a",
            sent_at=datetime(2026, 9, 1, 2, tzinfo=UTC),
            text="private target",
        ),
        _message(
            "message-10",
            conversation_id="conversation-b",
            sent_at=datetime(2026, 9, 1, 3, tzinfo=UTC),
            text="private companion",
        ),
    )
    _make_searchable(
        repository,
        scope=_scope(),
        messages=hidden,
        session_id="session-b",
        suffix="private",
    )
    unsearchable = (
        _message(
            "message-11",
            conversation_id="conversation-a",
            sent_at=datetime(2026, 9, 1, 4, tzinfo=UTC),
            text="unsearchable target",
        ),
        _message(
            "message-12",
            conversation_id="conversation-b",
            sent_at=datetime(2026, 9, 1, 5, tzinfo=UTC),
            text="unsearchable companion",
        ),
    )
    _make_searchable(
        repository,
        scope=_scope(),
        messages=unsearchable,
        session_id="session-a",
        suffix="not-indexed",
        searchable=False,
    )

    page = service.search(
        SearchRequest(
            query="private target",
            conversation_ids=("conversation-a",),
            start_time=datetime(2026, 9, 1, tzinfo=UTC),
            end_time=datetime(2026, 9, 2, tzinfo=UTC),
        ),
        session_id="session-a",
    )

    assert page.results == ()
    assert service.search(
        SearchRequest(
            query="unsearchable target",
            conversation_ids=("conversation-a",),
            start_time=datetime(2026, 9, 1, tzinfo=UTC),
            end_time=datetime(2026, 9, 2, tzinfo=UTC),
        ),
        session_id="session-a",
    ).results == ()


def test_search_treats_sql_metacharacters_as_plain_text(search_service) -> None:
    service, _, _ = search_service

    page = service.search(
        SearchRequest(
            query="%' OR 1=1 --",
            conversation_ids=("conversation-a", "conversation-b"),
            start_time=datetime(2026, 9, 1, tzinfo=UTC),
            end_time=datetime(2026, 9, 4, tzinfo=UTC),
        ),
        session_id="session-a",
    )

    assert page.results == ()


def test_search_request_rejects_pathological_scope_and_page_size() -> None:
    base = {
        "query": "target",
        "conversation_ids": ("conversation-a",),
        "start_time": datetime(2026, 9, 1, tzinfo=UTC),
        "end_time": datetime(2026, 9, 2, tzinfo=UTC),
    }

    with pytest.raises(ValueError):
        SearchRequest(**base, limit=101)
    with pytest.raises(ValueError):
        SearchRequest(**{**base, "query": "x" * 513})
    with pytest.raises(ValueError):
        SearchRequest(**{**base, "conversation_ids": tuple(f"c-{i}" for i in range(51))})
    with pytest.raises(ValueError):
        SearchRequest(**{**base, "end_time": base["start_time"]})
