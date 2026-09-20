"""P1 importer tests use only committed synthetic artifacts in temporary roots."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path

import pytest

from chat_to_howto.domain.jobs import ExportConversation, ExportScope
from chat_to_howto.domain.messages import MessageInput, StoredMessage
from chat_to_howto.repositories.chat import ChatRepository
from chat_to_howto.services.artifacts import ApprovedOutputRoot, ArtifactWriter
from chat_to_howto.sources.base import CompletenessEvidence


def _scope() -> ExportScope:
    return ExportScope(
        source_id="synthetic-source",
        account_id="account-a",
        source_account_namespace="qq:opaque-a",
        conversations=(
            ExportConversation(
                conversation_id="conversation-a",
                display_name="合成对话",
                conversation_type="group",
            ),
        ),
        range_start=datetime(2026, 9, 1, tzinfo=timezone.utc),
        range_end=datetime(2026, 9, 2, tzinfo=timezone.utc),
        display_timezone="Asia/Shanghai",
        output_root_id="approved-root",
        configuration_nonce="fixture-config",
        client_version="fixture-client",
        tool_version="fixture-tool",
        account_version="fixture-account",
    )


def _message(order: int = 1, *, text: str = "可检索的合成步骤") -> StoredMessage:
    value = MessageInput(
        workspace_id="fixture-workspace",
        platform="qq",
        source_id="synthetic-source",
        account_id="account-a",
        source_account_namespace="qq:opaque-a",
        conversation_id="conversation-a",
        conversation_name="合成对话",
        conversation_type="group",
        source_message_id=f"source-message-{order}",
        source_file_id=None,
        page_slice_id="slice-a",
        source_order=order,
        sent_at_utc=datetime(2026, 9, 1, 1, order, tzinfo=timezone.utc),
        original_timestamp=f"2026-09-01T01:{order:02d}:00+00:00",
        source_timezone="UTC",
        sender_id="sender-a",
        sender_name="合成发送者",
        message_type="text",
        text=text,
        reply_to_message_id=None,
        attachments=None,
    )
    return StoredMessage.from_input(value)


def _evidence(status: str = "source_range_verified") -> CompletenessEvidence:
    count = 0 if status == "empty" else 2
    return CompletenessEvidence(
        status=status,  # type: ignore[arg-type]
        reason="synthetic_fixture",
        requested_start_ms=1788220800000,
        requested_end_ms=1788307200000,
        observed_start_ms=1788224460000 if count else None,
        observed_end_ms=1788224520000 if count else None,
        fetched_count=count,
        accepted_count=count,
        duplicate_count=0,
        unknown_time_count=0,
        missing_id_count=0,
        parse_error_count=0,
        warnings=("synthetic_only",),
        termination="fixture_exhausted",
    )


def _committed(
    tmp_path: Path,
    *,
    batch_id: str = "batch-import",
    snapshot_id: str = "snapshot-import",
    messages: tuple[StoredMessage, ...] | None = None,
) -> tuple[ApprovedOutputRoot, ExportScope, Path]:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    output_root = tmp_path / "中文 导出"
    output_root.mkdir(mode=0o700)
    approved = ApprovedOutputRoot.approve(
        output_root, root_id="approved-root", repository_root=repository_root
    )
    selected_messages = messages if messages is not None else (_message(1), _message(2))
    artifacts = ArtifactWriter(approved).write_batch(
        batch_id=batch_id,
        snapshot_id=snapshot_id,
        scope=_scope(),
        messages=selected_messages,
        conversation_evidence={
            "conversation-a": _evidence("empty" if not selected_messages else "source_range_verified")
        },
        source_type="synthetic",
    )
    return approved, _scope(), artifacts.directory


def _rewrite_jsonl_and_digest(directory: Path, payload: bytes) -> None:
    jsonl_path = directory / "messages.jsonl"
    jsonl_path.chmod(0o600)
    jsonl_path.write_bytes(payload)
    jsonl_path.chmod(0o600)
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    for artifact in manifest["artifacts"]:
        if artifact["relative_path"] == "messages.jsonl":
            artifact["byte_size"] = len(payload)
            artifact["sha256"] = sha256(payload).hexdigest()
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    )
    manifest_path.chmod(0o600)


def test_import_verified_is_atomic_snapshot_bound_and_idempotent(tmp_path: Path) -> None:
    from chat_to_howto.services.importer import BatchImporter

    approved, scope, _ = _committed(tmp_path)
    repository = ChatRepository(tmp_path / "chat.sqlite3")
    try:
        importer = BatchImporter(repository, approved)
        first = importer.import_verified(batch_id="batch-import", expected_scope=scope)
        repeated = importer.import_verified(batch_id="batch-import", expected_scope=scope)

        assert first == repeated
        assert first.snapshot_id == "snapshot-import"
        assert first.batch_id == "batch-import"
        assert repository.message_count() == 2
        assert len(repository.batch_message_ids("batch-import")) == 2
        assert repository.snapshot_message_ids("snapshot-import") == repository.batch_message_ids(
            "batch-import"
        )
    finally:
        repository.close()


def test_importer_rejects_hash_tamper_before_database_writes(tmp_path: Path) -> None:
    from chat_to_howto.services.importer import BatchImporter

    approved, scope, directory = _committed(tmp_path)
    with (directory / "messages.jsonl").open("ab") as stream:
        stream.write(b"tamper")
    repository = ChatRepository(tmp_path / "chat.sqlite3")
    try:
        with pytest.raises(ValueError, match="hash|size"):
            BatchImporter(repository, approved).import_verified(
                batch_id="batch-import", expected_scope=scope
            )
        assert repository.message_count() == 0
        assert repository.batch_message_ids("batch-import") == ()
    finally:
        repository.close()


@pytest.mark.parametrize(
    "mutate,match",
    [
        (lambda record: record.update(export_batch_id="different-batch"), "batch"),
        (lambda record: record.update(snapshot_id="different-snapshot"), "snapshot"),
        (lambda record: record.update(account_id="different-account"), "scope"),
        (lambda record: record.update(conversation_id="not-selected"), "selected"),
        (lambda record: record.update(sent_at_utc="2026-09-02T00:00:00Z"), "range"),
    ],
)
def test_importer_rejects_record_identity_or_scope_mismatch_atomically(
    tmp_path: Path, mutate, match: str
) -> None:
    from chat_to_howto.services.importer import BatchImporter

    approved, scope, directory = _committed(tmp_path)
    records = [json.loads(line) for line in (directory / "messages.jsonl").read_text().splitlines()]
    mutate(records[1])
    payload = b"".join(
        (
            json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        for record in records
    )
    _rewrite_jsonl_and_digest(directory, payload)
    repository = ChatRepository(tmp_path / "chat.sqlite3")
    try:
        with pytest.raises(ValueError, match=match):
            BatchImporter(repository, approved).import_verified(
                batch_id="batch-import", expected_scope=scope
            )
        assert repository.message_count() == 0
    finally:
        repository.close()


def test_importer_rejects_corrupt_json_unknown_fields_and_oversized_line(tmp_path: Path) -> None:
    from chat_to_howto.services.importer import BatchImporter, ImportLimits

    for index, payload in enumerate(
        (
            b"not-json\n",
            b'{"unexpected":"field"}\n',
            b'{' + b'"padding":"' + (b"x" * 2048) + b'"}\n',
        )
    ):
        case_root = tmp_path / f"case-{index}"
        case_root.mkdir()
        approved, scope, directory = _committed(case_root)
        _rewrite_jsonl_and_digest(directory, payload)
        repository = ChatRepository(case_root / "chat.sqlite3")
        try:
            with pytest.raises(ValueError, match="JSONL|line|record"):
                BatchImporter(
                    repository,
                    approved,
                    limits=ImportLimits(max_jsonl_bytes=4096, max_line_bytes=1024),
                ).import_verified(batch_id="batch-import", expected_scope=scope)
            assert repository.message_count() == 0
        finally:
            repository.close()


def test_importer_rejects_manifest_scope_change_and_wrong_root_id(tmp_path: Path) -> None:
    from chat_to_howto.services.importer import BatchImporter

    approved, scope, _ = _committed(tmp_path)
    repository = ChatRepository(tmp_path / "chat.sqlite3")
    try:
        changed = scope.model_copy(update={"range_end": datetime(2026, 9, 3, tzinfo=timezone.utc)})
        with pytest.raises(ValueError, match="scope fingerprint"):
            BatchImporter(repository, approved).import_verified(
                batch_id="batch-import", expected_scope=changed
            )
        wrong_root = scope.model_copy(update={"output_root_id": "different-root"})
        with pytest.raises(PermissionError, match="output root"):
            BatchImporter(repository, approved).import_verified(
                batch_id="batch-import", expected_scope=wrong_root
            )
        assert repository.message_count() == 0
    finally:
        repository.close()


def test_importer_cross_checks_redundant_manifest_scope_fields(tmp_path: Path) -> None:
    from chat_to_howto.services.importer import BatchImporter

    approved, scope, directory = _committed(tmp_path)
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    # Keeping the signed/fingerprinted scope label untouched must not let an
    # attacker rewrite the human-readable account/version metadata.
    manifest["account_id"] = "different-account"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False) + "\n")
    manifest_path.chmod(0o600)
    repository = ChatRepository(tmp_path / "chat.sqlite3")
    try:
        with pytest.raises(ValueError, match="manifest fields"):
            BatchImporter(repository, approved).import_verified(
                batch_id="batch-import", expected_scope=scope
            )
        assert repository.message_count() == 0
    finally:
        repository.close()


def test_importer_uses_nofollow_for_batch_and_artifact_paths(tmp_path: Path) -> None:
    from chat_to_howto.services.importer import BatchImporter

    approved, scope, directory = _committed(tmp_path)
    outside = tmp_path / "outside.jsonl"
    outside.write_text((directory / "messages.jsonl").read_text())
    (directory / "messages.jsonl").unlink()
    (directory / "messages.jsonl").symlink_to(outside)
    repository = ChatRepository(tmp_path / "chat.sqlite3")
    try:
        with pytest.raises(OSError):
            BatchImporter(repository, approved).import_verified(
                batch_id="batch-import", expected_scope=scope
            )
        assert repository.message_count() == 0
    finally:
        repository.close()


def test_importer_refuses_manifest_count_mismatch_and_leaves_no_snapshot(tmp_path: Path) -> None:
    from chat_to_howto.services.importer import BatchImporter

    approved, scope, directory = _committed(tmp_path)
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["saved_count"] = 3
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False) + "\n")
    manifest_path.chmod(0o600)
    repository = ChatRepository(tmp_path / "chat.sqlite3")
    try:
        with pytest.raises(ValueError, match="saved count"):
            BatchImporter(repository, approved).import_verified(
                batch_id="batch-import", expected_scope=scope
            )
        assert repository.snapshot_message_ids("snapshot-import") == ()
    finally:
        repository.close()


def test_repository_conflict_rolls_back_entire_import_transaction(tmp_path: Path) -> None:
    from chat_to_howto.services.importer import BatchImporter

    approved, scope, _ = _committed(tmp_path)
    repository = ChatRepository(tmp_path / "chat.sqlite3")
    try:
        conflicting = _message(1, text="同一来源身份但不同的合成正文")
        conflicting_payload = conflicting.model_dump()
        conflicting_payload.pop("message_id")
        conflicting_payload.pop("export_batch_id", None)
        repository.record_message(MessageInput.model_validate(conflicting_payload))
        with pytest.raises(ValueError, match="identity conflict"):
            BatchImporter(repository, approved).import_verified(
                batch_id="batch-import", expected_scope=scope
            )

        assert repository.message_count() == 1
        assert repository.batch_message_ids("batch-import") == ()
        assert repository.snapshot_message_ids("snapshot-import") == ()
    finally:
        repository.close()


def test_existing_batch_with_another_scope_cannot_be_reused(tmp_path: Path) -> None:
    from chat_to_howto.services.importer import BatchImporter

    approved, scope, _ = _committed(tmp_path)
    repository = ChatRepository(tmp_path / "chat.sqlite3")
    try:
        different_scope = scope.model_copy(
            update={"configuration_nonce": "another-config"}
        )
        repository.create_batch("batch-import", different_scope)
        with pytest.raises(ValueError, match="another scope|scope"):
            BatchImporter(repository, approved).import_verified(
                batch_id="batch-import", expected_scope=scope
            )
        assert repository.message_count() == 0
        assert repository.snapshot_message_ids("snapshot-import") == ()
    finally:
        repository.close()
