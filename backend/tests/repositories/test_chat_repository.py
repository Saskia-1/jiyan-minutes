"""Durable identity and snapshot tests using only synthetic message metadata."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path

import pytest

import chat_to_howto.domain.messages as message_domain
from chat_to_howto.domain.messages import AttachmentPlaceholder, MessageInput
from chat_to_howto.domain.jobs import ExportConversation, ExportJobState, ExportScope
from chat_to_howto.domain.manifest import (
    ArtifactDigest,
    BatchManifest,
    ConversationEvidenceManifest,
    ConversationManifest,
)
from chat_to_howto.domain.messages import StoredMessage
from chat_to_howto.domain.imports import (
    ImportAnalysis,
    ImportMessageTypeCounts,
    ManualImportCommit,
    ManualImportSelection,
)
from chat_to_howto.migrations import open_connection
from chat_to_howto.repositories.chat import ChatRepository


def _message(**changes: object) -> MessageInput:
    values: dict[str, object] = {
        "workspace_id": "workspace-1",
        "platform": "qq",
        "source_id": "synthetic-source",
        "account_id": "account-a",
        "source_account_namespace": "qq:account-a",
        "conversation_id": "conversation-1",
        "conversation_name": "synthetic conversation",
        "conversation_type": "group",
        "source_message_id": "source-message-1",
        "source_file_id": None,
        "page_slice_id": None,
        "source_order": 1,
        "sent_at_utc": datetime(2026, 9, 1, tzinfo=timezone.utc),
        "original_timestamp": "2026-09-01T00:00:00+00:00",
        "source_timezone": "UTC",
        "sender_id": "sender-1",
        "sender_name": "synthetic sender",
        "message_type": "text",
        "reply_to_message_id": None,
        "export_batch_id": None,
        "attachments": None,
        "text": "synthetic body",
    }
    values.update(changes)
    return MessageInput(**values)


def _repository(tmp_path: Path) -> ChatRepository:
    return ChatRepository(tmp_path / "synthetic.sqlite3")


def _scope(*, conversations: tuple[ExportConversation, ...] | None = None, **changes: object) -> ExportScope:
    values: dict[str, object] = {
        "source_id": "synthetic-source",
        "account_id": "account-a",
        "conversations": conversations or (
            ExportConversation(
                conversation_id="conversation-1",
                display_name="同名群",
                conversation_type="group",
            ),
        ),
        "range_start": datetime(2026, 9, 1, tzinfo=timezone.utc),
        "range_end": datetime(2026, 9, 2, tzinfo=timezone.utc),
        "display_timezone": "Asia/Shanghai",
        "output_root_id": "approved-root-1",
        "configuration_nonce": "synthetic-nonce",
    }
    values.update(changes)
    return ExportScope(**values)


def _stored(**changes: object) -> StoredMessage:
    return StoredMessage.from_input(_message(**changes))


def _manual_commit(*, session_digest: str, duplicate_handles: bool = False) -> ManualImportCommit:
    selections = (
        ManualImportSelection(handle_digest="a" * 64, conversation_id="conversation-1"),
        ManualImportSelection(handle_digest="a" * 64, conversation_id="conversation-1"),
    ) if duplicate_handles else (
        ManualImportSelection(handle_digest="a" * 64, conversation_id="conversation-1"),
    )
    return ManualImportCommit(
        import_id="import-a",
        session_digest=session_digest,
        source_kind="imported_file",
        platform_hint="qq",
        source_fingerprint="b" * 64,
        filename="fixture.csv",
        analysis=ImportAnalysis(
            total_count=1,
            parsed_count=1,
            unresolved_count=0,
            duplicate_count=0,
            sender_count=1,
            time_start=datetime(2026, 9, 1, tzinfo=timezone.utc),
            time_end=datetime(2026, 9, 1, tzinfo=timezone.utc),
            message_types=ImportMessageTypeCounts(text=1),
            conversations=(),
            warnings=("completeness_is_not_inferred",),
            issues=(),
            samples=(),
        ),
        selections=selections,
        created_at_utc=datetime(2026, 9, 2, tzinfo=timezone.utc),
        expires_at_utc=datetime(2026, 9, 2, 1, tzinfo=timezone.utc),
    )


def _manifest(
    scope: ExportScope,
    messages: tuple[StoredMessage, ...],
    *,
    batch_id: str = "batch-import",
    snapshot_id: str = "snapshot-import",
    completeness: str = "source_range_verified",
    completeness_reason: str = "synthetic_repository_fixture",
) -> BatchManifest:
    timestamps = tuple(
        message.sent_at_utc for message in messages if message.sent_at_utc is not None
    )
    return BatchManifest(
        export_batch_id=batch_id,
        snapshot_id=snapshot_id,
        artifact_set_id="artifact-set-import",
        scope_fingerprint=scope.fingerprint(),
        output_root_id=scope.output_root_id,
        created_at_utc=datetime(2026, 9, 2, tzinfo=timezone.utc),
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
        earliest_saved_at_utc=min(timestamps) if timestamps else None,
        latest_saved_at_utc=max(timestamps) if timestamps else None,
        completeness=completeness,  # type: ignore[arg-type]
        completeness_reason=completeness_reason,
        limitations=(),
        warnings=(),
        conversation_evidence=tuple(
            ConversationEvidenceManifest(
                conversation_id=item.conversation_id,
                status=completeness,  # type: ignore[arg-type]
                reason=completeness_reason,
                fetched_count=sum(
                    message.conversation_id == item.conversation_id for message in messages
                ),
                accepted_count=sum(
                    message.conversation_id == item.conversation_id for message in messages
                ),
            )
            for item in scope.conversations
        ),
        artifacts=(
            ArtifactDigest(relative_path="messages.jsonl", byte_size=0, sha256="0" * 64),
            ArtifactDigest(relative_path="messages.txt", byte_size=0, sha256="1" * 64),
        ),
    )


def test_multi_conversation_export_scope_is_fingerprinted_and_rejects_duplicate_ids() -> None:
    """P1 batch consent cannot collapse two visible names into one implicit scope."""
    selected = (
        ExportConversation(conversation_id="conversation-1", display_name="同名群", conversation_type="group"),
        ExportConversation(conversation_id="conversation-2", display_name="同名群", conversation_type="group"),
    )
    scope = _scope(conversations=selected)

    assert scope.fingerprint() != scope.model_copy(update={"output_root_id": "other-root"}).fingerprint()
    with pytest.raises(ValueError, match="duplicate conversation"):
        _scope(conversations=(selected[0], selected[0]))


def test_export_scope_preserves_nullable_account_namespace_in_its_fingerprint() -> None:
    """An empty export still carries an observed namespace without guessing one."""
    unknown = _scope()
    observed = _scope(source_account_namespace="qq:synthetic-account")

    assert unknown.source_account_namespace is None
    assert observed.source_account_namespace == "qq:synthetic-account"
    assert observed.fingerprint() != unknown.fingerprint()
    with pytest.raises(ValueError, match="account namespace"):
        _scope(source_account_namespace="   ")


def test_export_scope_requires_aware_ordered_range_valid_timezone_and_opaque_root() -> None:
    """A scope never silently guesses time semantics or accepts a filesystem path as its ID."""
    assert _scope().schema_version == 1
    with pytest.raises(ValueError, match="timezone"):
        _scope(display_timezone="not/a-timezone")
    with pytest.raises(ValueError, match="timezone-aware"):
        _scope(range_start=datetime(2026, 9, 1))
    with pytest.raises(ValueError, match="before end"):
        _scope(range_end=datetime(2026, 9, 1, tzinfo=timezone.utc))
    with pytest.raises(ValueError, match="opaque output root"):
        _scope(output_root_id="/private/synthetic-root")
    with pytest.raises(ValueError, match="conversation ID"):
        ExportConversation(conversation_id="   ")


def test_message_repr_and_validation_never_echo_synthetic_body() -> None:
    """Validation is safe to surface without exposing local message contents."""
    sentinel = "synthetic-private-body-must-not-leak"
    assert sentinel not in repr(_message(text=sentinel))
    with pytest.raises(ValueError) as raised:
        _message(
            text=sentinel,
            source_message_id=None,
            source_file_id=None,
            page_slice_id=None,
            source_order=None,
        )
    assert sentinel not in str(raised.value)

    assert hasattr(message_domain, "safe_validation_errors")
    safe_errors = message_domain.safe_validation_errors(raised.value)
    assert len(safe_errors) == 1
    assert "input" not in safe_errors[0]
    assert sentinel not in json.dumps(safe_errors, ensure_ascii=False, default=str)


def test_source_provenance_path_is_hidden_from_message_repr() -> None:
    """A rejected source path is never echoed by diagnostics."""
    path_sentinel = "/private/synthetic-source/chat.db"

    with pytest.raises(ValueError) as raised:
        _message(source_file_id=path_sentinel)

    assert path_sentinel not in str(raised.value)
    assert path_sentinel not in json.dumps(
        message_domain.safe_validation_errors(raised.value),
        ensure_ascii=False,
        default=str,
    )


@pytest.mark.parametrize("field", ("source_file_id", "page_slice_id"))
@pytest.mark.parametrize(
    "unsafe_value",
    (
        "",
        "   ",
        ".",
        "..",
        "../fixture-log",
        "folder/fixture-log",
        "folder\\fixture-log",
        "https://example.invalid/fixture-log",
        "file:///private/fixture-log",
        "fixture\x00log",
        "x" * 513,
    ),
)
def test_source_provenance_requires_bounded_opaque_identifiers(
    field: str,
    unsafe_value: str,
) -> None:
    """Fallback provenance IDs cannot become filesystem paths, URLs, or unbounded input."""
    values: dict[str, object] = {
        "source_message_id": None,
        "source_file_id": "fixture-log-1",
        "page_slice_id": "slice-1",
        "source_order": 1,
    }
    values[field] = unsafe_value

    with pytest.raises(ValueError, match="provenance"):
        _message(**values)


def test_attachment_placeholders_persist_observed_metadata_without_path_fields(tmp_path: Path) -> None:
    """P1 stores only explicitly observed attachment metadata, never a media path."""
    path = tmp_path / "synthetic.sqlite3"
    repository = ChatRepository(path)
    message = repository.record_message(
        _message(
            attachments=(
                AttachmentPlaceholder(kind="image", availability="metadata_only", original_name="photo.jpg"),
            )
        )
    )
    connection = open_connection(path)
    try:
        row = connection.execute(
            "SELECT attachments_json FROM chat_messages WHERE message_id = ?", (message.message_id,)
        ).fetchone()
        assert json.loads(row[0]) == [
            {"availability": "metadata_only", "kind": "image", "original_name": "photo.jpg"}
        ]
    finally:
        connection.close()
        repository.close()
    with pytest.raises(ValueError):
        AttachmentPlaceholder(kind="image", local_path="/private/synthetic.jpg")  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "unsafe_name",
    (
        "../synthetic.jpg",
        "folder/synthetic.jpg",
        "folder\\synthetic.jpg",
        "https://example.invalid/synthetic.jpg",
        "file:///private/synthetic.jpg",
    ),
)
def test_attachment_original_name_rejects_paths_and_urls(unsafe_name: str) -> None:
    """The filename slot cannot be repurposed to persist a path or URL."""
    with pytest.raises(ValueError, match="basename"):
        AttachmentPlaceholder(
            kind="image",
            availability="metadata_only",
            original_name=unsafe_name,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("kind", "arbitrary-script"),
        ("availability", "downloaded_and_ocr_complete"),
    ),
)
def test_attachment_kind_and_availability_are_bounded(field: str, value: str) -> None:
    """Attachment metadata cannot claim unsupported reads or invent executable kinds."""
    values = {"kind": "image", "availability": "not_read"}
    values[field] = value

    with pytest.raises(ValueError):
        AttachmentPlaceholder(**values)


def test_source_message_identity_is_scoped_to_account_and_idempotent(tmp_path: Path) -> None:
    """The same upstream ID must not merge messages from different accounts."""
    repository = _repository(tmp_path)

    first = repository.record_message(_message())
    repeated = repository.record_message(_message())
    other_account = repository.record_message(_message(account_id="account-b"))

    assert first.message_id == repeated.message_id
    assert first.message_id != other_account.message_id
    assert repository.message_count() == 2


def test_same_identity_with_changed_payload_fails_instead_of_returning_an_unstored_value(tmp_path: Path) -> None:
    """Idempotency never hides a differing source payload behind an old message ID."""
    repository = _repository(tmp_path)
    repository.record_message(_message())

    with pytest.raises(ValueError, match="message identity conflict"):
        repository.record_message(_message(text="changed synthetic body"))


def test_export_batch_identity_is_separate_from_stable_message_payload(tmp_path: Path) -> None:
    """One stable message may belong to multiple export batches without an identity conflict."""
    repository = _repository(tmp_path)

    first = repository.record_message(_message(export_batch_id="batch-1"))
    second = repository.record_message(_message(export_batch_id="batch-2"))

    assert first == second
    assert first.export_batch_id is None
    assert repository.message_count() == 1


def test_sent_at_utc_is_normalized_before_persistence_and_orders_chronologically(
    tmp_path: Path,
) -> None:
    """Mixed source offsets cannot corrupt SQLite's chronological text ordering."""
    repository = _repository(tmp_path)
    earlier = repository.record_message(
        _message(
            source_message_id="earlier",
            source_order=1,
            sent_at_utc=datetime(
                2026,
                9,
                1,
                8,
                tzinfo=timezone(timedelta(hours=8)),
            ),
        )
    )
    later = repository.record_message(
        _message(
            source_message_id="later",
            source_order=2,
            sent_at_utc=datetime(2026, 9, 1, 0, 30, tzinfo=timezone.utc),
        )
    )

    assert earlier.sent_at_utc == datetime(2026, 9, 1, tzinfo=timezone.utc)
    connection = open_connection(repository.path)
    try:
        ordered = connection.execute(
            "SELECT message_id FROM chat_messages ORDER BY sent_at_utc, source_order"
        ).fetchall()
    finally:
        connection.close()
        repository.close()
    assert [row[0] for row in ordered] == [earlier.message_id, later.message_id]


def test_nullable_source_id_uses_conservative_position_fallback_not_text(tmp_path: Path) -> None:
    """Identical repeated speech remains distinct when source positions differ."""
    repository = _repository(tmp_path)

    first = repository.record_message(
        _message(
            source_message_id=None,
            source_file_id="fixture-log-1",
            page_slice_id="slice-1",
            source_order=7,
            text="好的",
        )
    )
    repeated_speech = repository.record_message(
        _message(
            source_message_id=None,
            source_file_id="fixture-log-1",
            page_slice_id="slice-1",
            source_order=8,
            text="好的",
        )
    )
    same_position = repository.record_message(
        _message(
            source_message_id=None,
            source_file_id="fixture-log-1",
            page_slice_id="slice-1",
            source_order=7,
            text="好的",
        )
    )

    assert first.source_message_id is None
    assert first.message_id != repeated_speech.message_id
    assert first.message_id == same_position.message_id
    assert repository.message_count() == 2


def test_source_id_absence_without_provenance_is_rejected(tmp_path: Path) -> None:
    """A body is never an acceptable replacement for stable source provenance."""
    repository = _repository(tmp_path)

    with pytest.raises(ValueError, match="source provenance"):
        repository.record_message(
            _message(
                source_message_id=None,
                source_file_id=None,
                page_slice_id=None,
                source_order=None,
            )
        )

    for source_file_id, page_slice_id in (("", None), ("   ", None), (None, ""), (None, "   ")):
        with pytest.raises(ValueError, match="source provenance"):
            repository.record_message(
                _message(
                    source_message_id=None,
                    source_file_id=source_file_id,
                    page_slice_id=page_slice_id,
                    source_order=1,
                )
            )


def test_batch_membership_is_idempotent_and_separate_from_messages(tmp_path: Path) -> None:
    """Repeated exports may reference a stable message without counting it twice."""
    repository = _repository(tmp_path)
    message = repository.record_message(_message())
    repository.create_batch("batch-1", _scope())

    assert repository.add_message_to_batch("batch-1", message.message_id) is True
    assert repository.add_message_to_batch("batch-1", message.message_id) is False
    assert repository.batch_message_ids("batch-1") == (message.message_id,)
    assert repository.message_count() == 1


def test_batch_persists_scope_and_database_rejects_unselected_or_cross_account_membership(tmp_path: Path) -> None:
    """The persisted batch scope is an FK boundary, not only a repository convention."""
    path = tmp_path / "synthetic.sqlite3"
    repository = ChatRepository(path)
    selected = repository.record_message(_message())
    unselected = repository.record_message(_message(conversation_id="conversation-2", source_message_id="source-message-2"))
    other_account = repository.record_message(_message(account_id="account-b", source_message_id="source-message-3"))
    scope = _scope()
    repository.create_batch("batch-1", scope)

    assert repository.batch_scope("batch-1") == scope
    assert repository.add_message_to_batch("batch-1", selected.message_id) is True
    with pytest.raises(ValueError, match="not selected"):
        repository.add_message_to_batch("batch-1", unselected.message_id)
    with pytest.raises(ValueError, match="cross accounts"):
        repository.add_message_to_batch("batch-1", other_account.message_id)

    connection = open_connection(path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO chat_batch_messages(
                    batch_id, message_id, source_id, account_id, conversation_id
                ) VALUES (?, ?, ?, ?, ?)
                """,
                ("batch-1", unselected.message_id, "synthetic-source", "account-a", "conversation-2"),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO chat_batch_messages(
                    batch_id, message_id, source_id, account_id, conversation_id
                ) VALUES (?, ?, ?, ?, ?)
                """,
                ("batch-1", other_account.message_id, "synthetic-source", "account-b", "conversation-1"),
            )
    finally:
        connection.close()
        repository.close()


def test_snapshot_copies_batch_membership_and_cannot_be_rebound(tmp_path: Path) -> None:
    """Later batch changes cannot silently widen an immutable snapshot."""
    repository = _repository(tmp_path)
    first = repository.record_message(_message(source_message_id="source-message-1"))
    second = repository.record_message(_message(source_message_id="source-message-2", source_order=2))
    repository.create_batch("batch-1", _scope())
    repository.add_message_to_batch("batch-1", first.message_id)
    snapshot = repository.create_snapshot("snapshot-1", "batch-1")

    repository.add_message_to_batch("batch-1", second.message_id)

    assert snapshot.snapshot_id == "snapshot-1"
    assert repository.snapshot_message_ids("snapshot-1") == (first.message_id,)
    with pytest.raises(ValueError, match="immutable snapshot"):
        repository.create_snapshot("snapshot-1", "batch-1")


def test_snapshot_reference_is_immutable(tmp_path: Path) -> None:
    """Callers cannot mutate a handle and accidentally refer to another snapshot."""
    repository = _repository(tmp_path)
    repository.create_batch("batch-1", _scope())
    snapshot = repository.create_snapshot("snapshot-1", "batch-1")

    with pytest.raises(AttributeError):
        snapshot.snapshot_id = "snapshot-2"  # type: ignore[misc]


def test_snapshot_keeps_a_frozen_message_payload_when_base_message_changes(tmp_path: Path) -> None:
    """A snapshot is a frozen evidence copy, not a view of mutable base rows.

    Retention definition: later deletion must explicitly revoke/delete related
    snapshots before deleting base data; snapshot creation never cascades away
    its evidence implicitly.
    """
    path = tmp_path / "synthetic.sqlite3"
    repository = ChatRepository(path)
    message = repository.record_message(_message())
    repository.create_batch("batch-1", _scope())
    repository.add_message_to_batch("batch-1", message.message_id)
    repository.create_snapshot("snapshot-1", "batch-1")

    connection = open_connection(path)
    try:
        connection.execute(
            "UPDATE chat_messages SET text = ? WHERE message_id = ?",
            ("changed synthetic body", message.message_id),
        )
        connection.commit()
    finally:
        connection.close()

    assert repository.snapshot_messages("snapshot-1")[0].text == "synthetic body"


def test_sealed_snapshot_rejects_later_membership_insertion(tmp_path: Path) -> None:
    """A completed snapshot remains immutable even outside the repository API."""
    path = tmp_path / "synthetic.sqlite3"
    repository = ChatRepository(path)
    first = repository.record_message(_message(source_message_id="source-message-1"))
    second = repository.record_message(_message(source_message_id="source-message-2", source_order=2))
    repository.create_batch("batch-1", _scope())
    repository.add_message_to_batch("batch-1", first.message_id)
    repository.create_snapshot("snapshot-1", "batch-1")

    connection = open_connection(path)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="immutable snapshot"):
            connection.execute(
                "INSERT INTO chat_snapshot_messages(snapshot_id, message_id) VALUES (?, ?)",
                ("snapshot-1", second.message_id),
            )
    finally:
        connection.close()
        repository.close()


def test_messages_batches_and_snapshots_survive_repository_restart(tmp_path: Path) -> None:
    """Persisted P1 identity and membership do not depend on process memory."""
    path = tmp_path / "synthetic.sqlite3"
    initial = ChatRepository(path)
    message = initial.record_message(_message())
    initial.create_batch("batch-1", _scope())
    initial.add_message_to_batch("batch-1", message.message_id)
    initial.create_snapshot("snapshot-1", "batch-1")
    initial.close()

    restarted = ChatRepository(path)

    assert restarted.message_count() == 1
    assert restarted.batch_message_ids("batch-1") == (message.message_id,)
    assert restarted.snapshot_message_ids("snapshot-1") == (message.message_id,)
    restarted.close()


def test_atomic_import_binds_canonical_manifest_and_is_idempotent(tmp_path: Path) -> None:
    """One verified import seals membership, payload copies, and manifest evidence once."""
    path = tmp_path / "synthetic.sqlite3"
    repository = ChatRepository(path)
    scope = _scope(
        source_account_namespace="qq:account-a",
        client_version="fixture-client",
        tool_version="fixture-tool",
        account_version="fixture-account",
    )
    messages = (
        _stored(source_message_id="source-message-1", source_order=1),
        _stored(source_message_id="source-message-2", source_order=2),
    )
    manifest = _manifest(scope, messages)

    first = repository.import_snapshot_atomic(
        batch_id="batch-import",
        snapshot_id="snapshot-import",
        scope=scope,
        messages=messages,
        manifest=manifest,
    )
    repeated = repository.import_snapshot_atomic(
        batch_id="batch-import",
        snapshot_id="snapshot-import",
        scope=scope,
        messages=messages,
        manifest=manifest,
    )

    canonical_manifest = json.dumps(
        manifest.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    connection = open_connection(path)
    try:
        batch = connection.execute(
            """
            SELECT manifest_json, manifest_sha256, completeness,
                   limitations_accepted_at_utc,
                   limitations_accepted_manifest_sha256
            FROM chat_export_batches WHERE batch_id = ?
            """,
            ("batch-import",),
        ).fetchone()
    finally:
        connection.close()
        repository.close()

    assert first == repeated
    assert first.snapshot_id == "snapshot-import"
    assert first.batch_id == "batch-import"
    assert batch == (
        canonical_manifest,
        sha256(canonical_manifest.encode("utf-8")).hexdigest(),
        "source_range_verified",
        None,
        None,
    )


def test_atomic_import_rolls_back_all_writes_on_message_identity_conflict(
    tmp_path: Path,
) -> None:
    """A late stable-message conflict cannot leave a batch or prefix behind."""
    path = tmp_path / "synthetic.sqlite3"
    repository = ChatRepository(path)
    scope = _scope(source_account_namespace="qq:account-a")
    messages = (
        _stored(source_message_id="source-message-new", source_order=1),
        _stored(source_message_id="source-message-conflict", source_order=2),
    )
    repository.record_message(
        _message(
            source_message_id="source-message-conflict",
            source_order=2,
            text="different pre-existing synthetic body",
        )
    )

    with pytest.raises(ValueError, match="message identity conflict"):
        repository.import_snapshot_atomic(
            batch_id="batch-import",
            snapshot_id="snapshot-import",
            scope=scope,
            messages=messages,
            manifest=_manifest(scope, messages),
        )

    connection = open_connection(path)
    try:
        assert connection.execute(
            "SELECT COUNT(*) FROM chat_export_batches WHERE batch_id = 'batch-import'"
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT COUNT(*) FROM chat_messages"
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT COUNT(*) FROM chat_batch_messages"
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT COUNT(*) FROM chat_snapshots"
        ).fetchone() == (0,)
    finally:
        connection.close()
        repository.close()


def test_atomic_import_rejects_changed_manifest_without_mutating_sealed_import(
    tmp_path: Path,
) -> None:
    """Idempotence is hash-bound: changed evidence is a conflict, not a retry."""
    path = tmp_path / "synthetic.sqlite3"
    repository = ChatRepository(path)
    scope = _scope(source_account_namespace="qq:account-a")
    messages = (_stored(),)
    manifest = _manifest(scope, messages)
    repository.import_snapshot_atomic(
        batch_id="batch-import",
        snapshot_id="snapshot-import",
        scope=scope,
        messages=messages,
        manifest=manifest,
    )
    changed = manifest.model_copy(
        update={
            "completeness": "partial",
            "completeness_reason": "changed synthetic evidence",
        }
    )

    with pytest.raises(ValueError, match="manifest conflict"):
        repository.import_snapshot_atomic(
            batch_id="batch-import",
            snapshot_id="snapshot-import",
            scope=scope,
            messages=messages,
            manifest=changed,
        )

    connection = open_connection(path)
    try:
        assert connection.execute(
            "SELECT completeness FROM chat_export_batches WHERE batch_id = 'batch-import'"
        ).fetchone() == ("source_range_verified",)
        assert connection.execute("SELECT COUNT(*) FROM chat_messages").fetchone() == (1,)
        assert connection.execute("SELECT COUNT(*) FROM chat_snapshots").fetchone() == (1,)
    finally:
        connection.close()
        repository.close()


def test_atomic_import_rolls_back_new_batch_when_snapshot_identity_conflicts(
    tmp_path: Path,
) -> None:
    """A snapshot already bound elsewhere rejects the whole incoming transaction."""
    path = tmp_path / "synthetic.sqlite3"
    repository = ChatRepository(path)
    scope = _scope(source_account_namespace="qq:account-a")
    repository.create_batch("existing-batch", scope)
    repository.create_snapshot("shared-snapshot", "existing-batch")
    messages = (_stored(source_message_id="new-import-message"),)

    with pytest.raises(ValueError, match="snapshot identity conflict"):
        repository.import_snapshot_atomic(
            batch_id="new-batch",
            snapshot_id="shared-snapshot",
            scope=scope,
            messages=messages,
            manifest=_manifest(
                scope,
                messages,
                batch_id="new-batch",
                snapshot_id="shared-snapshot",
            ),
        )

    connection = open_connection(path)
    try:
        assert connection.execute(
            "SELECT COUNT(*) FROM chat_export_batches WHERE batch_id = 'new-batch'"
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT COUNT(*) FROM chat_messages WHERE message_id = ?",
            (messages[0].message_id,),
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT batch_id FROM chat_snapshots WHERE snapshot_id = 'shared-snapshot'"
        ).fetchone() == ("existing-batch",)
    finally:
        connection.close()
        repository.close()


def test_manual_import_is_same_session_searchable_and_generation_authorized(
    tmp_path: Path,
) -> None:
    """A committed manual import joins the canonical snapshot flow without a fake export job."""
    repository = _repository(tmp_path)
    scope = _scope()
    messages = (_stored(text="manual import searchable fixture"),)
    session_digest = "c" * 64

    repository.import_snapshot_atomic(
        batch_id="batch-import",
        snapshot_id="snapshot-import",
        scope=scope,
        messages=messages,
        manifest=_manifest(scope, messages),
        manual_import=_manual_commit(session_digest=session_digest),
    )

    rows = repository.search_messages(
        session_digest=session_digest,
        query="searchable",
        conversation_ids=("conversation-1",),
        snapshot_id=None,
        start_time_utc=scope.range_start.isoformat(),
        end_time_utc=scope.range_end.isoformat(),
        cursor=None,
        limit=20,
    )
    batch_id, generation_messages = repository.generation_snapshot_messages(
        session_digest=session_digest,
        snapshot_id="snapshot-import",
    )

    assert [row["message_id"] for row in rows] == [messages[0].message_id]
    assert batch_id == "batch-import"
    assert generation_messages == messages
    with pytest.raises(PermissionError):
        repository.generation_snapshot_messages(
            session_digest="d" * 64,
            snapshot_id="snapshot-import",
        )
    repository.close()


def test_manual_import_registration_conflict_rolls_back_new_snapshot(
    tmp_path: Path,
) -> None:
    """Import ownership and its sealed snapshot are one atomic database change."""
    path = tmp_path / "synthetic.sqlite3"
    repository = ChatRepository(path)
    scope = _scope()
    messages = (_stored(),)
    manual_import = _manual_commit(session_digest="c" * 64)
    repository.import_snapshot_atomic(
        batch_id="batch-import",
        snapshot_id="snapshot-import",
        scope=scope,
        messages=messages,
        manifest=_manifest(scope, messages),
        manual_import=manual_import,
    )

    with pytest.raises(ValueError, match="manual import identity conflict"):
        repository.import_snapshot_atomic(
            batch_id="batch-conflict",
            snapshot_id="snapshot-conflict",
            scope=scope,
            messages=messages,
            manifest=_manifest(
                scope,
                messages,
                batch_id="batch-conflict",
                snapshot_id="snapshot-conflict",
            ),
            manual_import=manual_import,
        )

    connection = open_connection(path)
    try:
        assert connection.execute(
            "SELECT COUNT(*) FROM chat_export_batches WHERE batch_id = 'batch-conflict'"
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT COUNT(*) FROM chat_snapshots WHERE snapshot_id = 'snapshot-conflict'"
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT batch_id, snapshot_id FROM chat_manual_imports"
        ).fetchone() == ("batch-import", "snapshot-import")
    finally:
        connection.close()
        repository.close()
