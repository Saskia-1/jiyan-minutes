"""Preview-before-commit orchestration for local manual imports."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from chat_to_howto.domain.imports import ImportOptions
from chat_to_howto.api.session import digest
from chat_to_howto.repositories.chat import ChatRepository
from chat_to_howto.services.import_sessions import (
    ChatImportService,
    ImportAuthorizationError,
    ImportNotReady,
)
from chat_to_howto.services.deletion import DeletionService
from chat_to_howto.domain.manifest import BatchManifest


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 9, 13, 3, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now


def _payload() -> bytes:
    return (
        "conversation,time,sender,text,id\n"
        "project,2026-09-13 10:20,A,searchable imported step,m1\n"
    ).encode()


def test_preview_does_not_persist_and_confirmed_commit_returns_search_handles(tmp_path) -> None:
    repository = ChatRepository(tmp_path / "chat.sqlite3")
    clock = Clock()
    service = ChatImportService(repository=repository, clock=clock)

    preview = service.create_preview(
        filename="chat.csv",
        payload=_payload(),
        origin="imported_file",
        platform_hint="qq",
        options=ImportOptions(timezone="Asia/Shanghai"),
        session_id="session-a",
    )

    pending = service._pending[preview.preview_id]
    assert not hasattr(pending, "analyzed")
    assert pending.analysis_commitment

    assert preview.ready_to_commit is True
    assert preview.analysis.parsed_count == 1
    assert preview.applied_mapping["text"] == "text"
    assert repository.message_count() == 0

    committed = service.commit_preview(
        preview.preview_id,
        preview_fingerprint=preview.preview_fingerprint,
        confirmed=True,
        session_id="session-a",
    )

    assert repository.message_count() == 1
    assert committed.source_id == "manual"
    assert committed.snapshot_id.startswith("snapshot:manual:")
    assert committed.display_timezone == "Asia/Shanghai"
    assert committed.range_end - committed.range_start == timedelta(seconds=1)
    assert len(committed.conversations) == 1
    resolved = repository.resolve_manual_import_selections(
        session_digest=digest("session-a"),
        source_id="manual",
        account_id=committed.account_id,
        handle_digests=(digest(committed.conversations[0].conversation_selection_id),),
        now_utc=clock.now.isoformat(),
    )
    assert resolved[0]["snapshot_id"] == committed.snapshot_id
    repository.close()


def test_commit_reanalyzes_the_exact_cached_payload(tmp_path) -> None:
    from chat_to_howto.services.chat_imports import ChatImportAnalyzer

    class CountingAnalyzer:
        def __init__(self) -> None:
            self.delegate = ChatImportAnalyzer()
            self.calls: list[bytes] = []

        def analyze(self, **kwargs):
            self.calls.append(kwargs["payload"])
            return self.delegate.analyze(**kwargs)

    repository = ChatRepository(tmp_path / "chat.sqlite3")
    analyzer = CountingAnalyzer()
    service = ChatImportService(
        repository=repository,
        clock=lambda: datetime(2026, 9, 13, tzinfo=timezone.utc),
        analyzer=analyzer,  # type: ignore[arg-type]
    )
    preview = service.create_preview(
        filename="chat.csv", payload=_payload(), origin="imported_file",
        platform_hint="generic", options=ImportOptions(timezone="UTC"),
        session_id="session-a",
    )
    service.commit_preview(
        preview.preview_id, preview_fingerprint=preview.preview_fingerprint,
        confirmed=True, session_id="session-a",
    )

    assert analyzer.calls == [_payload(), _payload()]
    repository.close()


def test_committed_import_can_be_recovered_with_fresh_same_session_handles(tmp_path) -> None:
    repository = ChatRepository(tmp_path / "chat.sqlite3")
    clock = Clock()
    service = ChatImportService(repository=repository, clock=clock)
    preview = service.create_preview(
        filename="chat.csv", payload=_payload(), origin="imported_file",
        platform_hint="generic", options=ImportOptions(timezone="UTC"),
        session_id="session-a",
    )
    committed = service.commit_preview(
        preview.preview_id, preview_fingerprint=preview.preview_fingerprint,
        confirmed=True, session_id="session-a",
    )

    recovered = service.recover_committed(session_id="session-a")

    assert recovered.next_cursor is None
    assert [item.import_id for item in recovered.imports] == [committed.import_id]
    assert recovered.imports[0].display_timezone == "UTC"
    fresh = recovered.imports[0].conversations[0].conversation_selection_id
    assert fresh != committed.conversations[0].conversation_selection_id
    with pytest.raises(PermissionError):
        repository.resolve_manual_import_selections(
            session_digest=digest("session-a"), source_id="manual",
            account_id=committed.account_id,
            handle_digests=(digest(committed.conversations[0].conversation_selection_id),),
            now_utc=clock.now.isoformat(),
        )
    assert repository.resolve_manual_import_selections(
        session_digest=digest("session-a"), source_id="manual",
        account_id=committed.account_id, handle_digests=(digest(fresh),),
        now_utc=clock.now.isoformat(),
    )[0]["snapshot_id"] == committed.snapshot_id
    assert service.recover_committed(session_id="session-b").imports == ()
    repository.close()


def test_commit_scope_keeps_conversation_metadata_first_seen_on_later_row(tmp_path) -> None:
    repository = ChatRepository(tmp_path / "chat.sqlite3")
    service = ChatImportService(
        repository=repository,
        clock=lambda: datetime(2026, 9, 13, tzinfo=timezone.utc),
    )
    payload = (
        "conversation,name,type,time,text\n"
        "c1,,,2026-09-13 10:20,first\n"
        "c1,项目群,group,2026-09-13 10:21,second\n"
    ).encode()
    options = ImportOptions(
        mapping={
            "conversation_id": "conversation", "conversation_name": "name",
            "conversation_type": "type", "sent_at": "time", "text": "text",
        },
        timezone="Asia/Shanghai",
    )
    preview = service.create_preview(
        filename="chat.csv", payload=payload, origin="imported_file",
        platform_hint="generic", options=options, session_id="session-a",
    )
    committed = service.commit_preview(
        preview.preview_id, preview_fingerprint=preview.preview_fingerprint,
        confirmed=True, session_id="session-a",
    )

    assert committed.conversations[0].display_name == "项目群"
    assert committed.conversations[0].conversation_type == "group"
    repository.close()


def test_preview_is_bound_to_session_fingerprint_and_expiry(tmp_path) -> None:
    repository = ChatRepository(tmp_path / "chat.sqlite3")
    clock = Clock()
    service = ChatImportService(repository=repository, clock=clock, preview_ttl_seconds=60)
    preview = service.create_preview(
        filename="chat.csv", payload=_payload(), origin="imported_file",
        platform_hint="generic", options=ImportOptions(timezone="UTC"),
        session_id="session-a",
    )

    with pytest.raises(ImportAuthorizationError):
        service.commit_preview(
            preview.preview_id, preview_fingerprint=preview.preview_fingerprint,
            confirmed=True, session_id="session-b",
        )
    with pytest.raises(ImportAuthorizationError):
        service.commit_preview(
            preview.preview_id, preview_fingerprint="f" * 64,
            confirmed=True, session_id="session-a",
        )
    clock.now += timedelta(minutes=2)
    with pytest.raises(ImportAuthorizationError):
        service.commit_preview(
            preview.preview_id, preview_fingerprint=preview.preview_fingerprint,
            confirmed=True, session_id="session-a",
        )
    assert repository.message_count() == 0
    repository.close()


def test_unresolved_preview_cannot_be_committed(tmp_path) -> None:
    repository = ChatRepository(tmp_path / "chat.sqlite3")
    service = ChatImportService(
        repository=repository,
        clock=lambda: datetime(2026, 9, 13, tzinfo=timezone.utc),
    )
    preview = service.create_preview(
        filename="pasted-chat.txt", payload="[10:20] A：secret\n".encode(),
        origin="paste", platform_hint="generic",
        options=ImportOptions(timezone="Asia/Shanghai"), session_id="session-a",
    )

    assert preview.ready_to_commit is False
    with pytest.raises(ImportNotReady):
        service.commit_preview(
            preview.preview_id, preview_fingerprint=preview.preview_fingerprint,
            confirmed=True, session_id="session-a",
        )
    assert repository.message_count() == 0
    repository.close()


def test_pending_preview_cache_is_bounded_and_expired_entry_releases_capacity(tmp_path) -> None:
    repository = ChatRepository(tmp_path / "chat.sqlite3")
    clock = Clock()
    service = ChatImportService(
        repository=repository,
        clock=clock,
        preview_ttl_seconds=60,
        max_pending_previews=1,
        max_pending_source_bytes=len(_payload()),
    )
    service.create_preview(
        filename="chat.csv", payload=_payload(), origin="imported_file",
        platform_hint="generic", options=ImportOptions(timezone="UTC"),
        session_id="session-a",
    )

    with pytest.raises(ImportNotReady, match="capacity"):
        service.create_preview(
            filename="chat.csv", payload=_payload(), origin="imported_file",
            platform_hint="generic", options=ImportOptions(timezone="UTC"),
            session_id="session-b",
        )

    clock.now += timedelta(minutes=2)
    replacement = service.create_preview(
        filename="chat.csv", payload=_payload(), origin="imported_file",
        platform_hint="generic", options=ImportOptions(timezone="UTC"),
        session_id="session-b",
    )
    assert replacement.ready_to_commit is True
    service.close()
    repository.close()


def test_new_preview_in_one_session_supersedes_old_mapping_without_using_more_capacity(tmp_path) -> None:
    repository = ChatRepository(tmp_path / "chat.sqlite3")
    service = ChatImportService(
        repository=repository,
        clock=lambda: datetime(2026, 9, 13, tzinfo=timezone.utc),
        max_pending_previews=1,
        max_pending_source_bytes=len(_payload()),
    )
    first = service.create_preview(
        filename="chat.csv", payload=_payload(), origin="imported_file",
        platform_hint="generic", options=ImportOptions(timezone="UTC"),
        session_id="session-a",
    )
    replacement = service.create_preview(
        filename="chat.csv", payload=_payload(), origin="imported_file",
        platform_hint="generic", options=ImportOptions(timezone="Asia/Shanghai"),
        session_id="session-a",
    )

    with pytest.raises(ImportAuthorizationError):
        service.commit_preview(
            first.preview_id, preview_fingerprint=first.preview_fingerprint,
            confirmed=True, session_id="session-a",
        )
    committed = service.commit_preview(
        replacement.preview_id,
        preview_fingerprint=replacement.preview_fingerprint,
        confirmed=True,
        session_id="session-a",
    )
    assert committed.source_id == "manual"
    repository.close()


def test_manual_import_uses_existing_confirmed_batch_deletion(tmp_path) -> None:
    repository = ChatRepository(tmp_path / "chat.sqlite3")
    clock = Clock()
    imports = ChatImportService(repository=repository, clock=clock)
    preview = imports.create_preview(
        filename="chat.csv", payload=_payload(), origin="imported_file",
        platform_hint="generic", options=ImportOptions(timezone="UTC"),
        session_id="session-a",
    )
    committed = imports.commit_preview(
        preview.preview_id, preview_fingerprint=preview.preview_fingerprint,
        confirmed=True, session_id="session-a",
    )
    deletion = DeletionService(repository=repository, approved_roots={}, clock=clock)

    plan = deletion.create_plan(
        target_kind="batch", target_id=committed.batch_id,
        include_managed_files=False, session_id="session-a",
    )
    result = deletion.confirm(
        plan.plan_id, fingerprint=plan.fingerprint, confirmed=True,
        session_id="session-a",
    )

    assert result.deleted_counts["export_batches"] == 1
    assert result.deleted_counts["messages"] == 1
    assert result.managed_files_retained is False
    assert repository.message_count() == 0
    repository.close()


def test_deleting_one_identical_reimport_reports_shared_message_as_retained(tmp_path) -> None:
    repository = ChatRepository(tmp_path / "chat.sqlite3")
    clock = Clock()
    imports = ChatImportService(repository=repository, clock=clock)
    committed = []
    for _ in range(2):
        preview = imports.create_preview(
            filename="chat.csv", payload=_payload(), origin="imported_file",
            platform_hint="generic", options=ImportOptions(timezone="UTC"),
            session_id="session-a",
        )
        committed.append(imports.commit_preview(
            preview.preview_id, preview_fingerprint=preview.preview_fingerprint,
            confirmed=True, session_id="session-a",
        ))
    deletion = DeletionService(repository=repository, approved_roots={}, clock=clock)

    first_plan = deletion.create_plan(
        target_kind="batch", target_id=committed[0].batch_id,
        include_managed_files=False, session_id="session-a",
    )
    assert first_plan.counts["messages"] == 0
    assert any("共享消息：1" in effect for effect in first_plan.effects)
    first_result = deletion.confirm(
        first_plan.plan_id, fingerprint=first_plan.fingerprint,
        confirmed=True, session_id="session-a",
    )
    assert first_result.deleted_counts["messages"] == 0
    assert repository.message_count() == 1

    second_plan = deletion.create_plan(
        target_kind="batch", target_id=committed[1].batch_id,
        include_managed_files=False, session_id="session-a",
    )
    assert second_plan.counts["messages"] == 1
    second_result = deletion.confirm(
        second_plan.plan_id, fingerprint=second_plan.fingerprint,
        confirmed=True, session_id="session-a",
    )
    assert second_result.deleted_counts["messages"] == 1
    assert repository.message_count() == 0
    repository.close()


def test_multi_conversation_manifest_keeps_only_attributable_per_chat_counts(tmp_path) -> None:
    repository = ChatRepository(tmp_path / "chat.sqlite3")
    imports = ChatImportService(
        repository=repository,
        clock=lambda: datetime(2026, 9, 13, tzinfo=timezone.utc),
    )
    payload = (
        "conversation,time,text,id\n"
        "a,2026-09-13 10:20,one,m1\n"
        "a,2026-09-13 10:20,one,m1\n"
        "b,2026-09-13 10:21,two,m2\n"
    ).encode()
    preview = imports.create_preview(
        filename="chat.csv", payload=payload, origin="imported_file",
        platform_hint="generic", options=ImportOptions(timezone="UTC"),
        session_id="session-a",
    )
    committed = imports.commit_preview(
        preview.preview_id, preview_fingerprint=preview.preview_fingerprint,
        confirmed=True, session_id="session-a",
    )
    row = repository._connection.execute(
        "SELECT manifest_json FROM chat_export_batches WHERE batch_id = ?",
        (committed.batch_id,),
    ).fetchone()
    manifest = BatchManifest.model_validate_json(row["manifest_json"])

    assert (manifest.fetched_count, manifest.saved_count, manifest.duplicate_count) == (3, 2, 1)
    assert [item.accepted_count for item in manifest.conversation_evidence] == [1, 1]
    assert all(item.fetched_count is None for item in manifest.conversation_evidence)
    assert all(item.duplicate_count is None for item in manifest.conversation_evidence)
    assert all(item.parse_error_count is None for item in manifest.conversation_evidence)
    repository.close()


def test_partial_import_requires_separate_skip_consent_and_recovers_counts(tmp_path):
    repository = ChatRepository(tmp_path / "partial.sqlite3")
    service = ChatImportService(repository=repository, clock=Clock())
    try:
        preview = service.create_preview(filename="chat.csv",
            payload=_payload() + b"project,not-a-time,B,unparsed synthetic row,m2\n",
            origin="imported_file", platform_hint="qq", options=ImportOptions(), session_id="owner")
        assert preview.ready_to_commit and preview.analysis.unresolved_count == 1
        with pytest.raises(ImportNotReady):
            service.commit_preview(preview.preview_id, preview_fingerprint=preview.preview_fingerprint,
                confirmed=True, session_id="owner")
        assert repository.message_count() == 0
        committed = service.commit_preview(preview.preview_id, preview_fingerprint=preview.preview_fingerprint,
            confirmed=True, accept_skipped=True, session_id="owner")
        assert committed.imported_count == 1 and committed.skipped_count == 1
        recovered = service.recover_committed(session_id="owner").imports[0]
        assert (recovered.imported_count, recovered.skipped_count) == (1, 1)
        assert recovered.raw_expires_at_utc == committed.raw_expires_at_utc
    finally:
        service.close(); repository.close()


def test_skip_consent_cannot_commit_a_preview_without_valid_records(tmp_path):
    repository = ChatRepository(tmp_path / "empty.sqlite3")
    service = ChatImportService(repository=repository, clock=Clock())
    try:
        preview = service.create_preview(filename="chat.csv",
            payload=b"conversation,time,sender,text,id\nproject,invalid,A,synthetic,m1\n",
            origin="imported_file", platform_hint="qq", options=ImportOptions(), session_id="owner")
        assert not preview.ready_to_commit
        with pytest.raises(ImportNotReady):
            service.commit_preview(preview.preview_id, preview_fingerprint=preview.preview_fingerprint,
                confirmed=True, accept_skipped=True, session_id="owner")
        assert repository.message_count() == 0
    finally:
        service.close(); repository.close()
