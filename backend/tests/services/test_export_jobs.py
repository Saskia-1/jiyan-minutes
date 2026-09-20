"""P1 durable export orchestration tests use synthetic local adapters only."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import stat
from threading import Event, Thread

import pytest

from chat_to_howto.domain.jobs import ExportConversation, ExportJobState, ExportScope
from chat_to_howto.domain.search import SearchRequest
from chat_to_howto.repositories.chat import ChatRepository, SnapshotRef
from chat_to_howto.services.artifacts import ApprovedOutputRoot, ArtifactWriter
from chat_to_howto.services.importer import BatchImporter
from chat_to_howto.services.search import ChatSearchService
from chat_to_howto.services.export_jobs import (
    AuthorizationError,
    ExportJobOrchestrator,
    JobConflict,
    JobNotFound,
    SourceBinding,
)
from chat_to_howto.sources.base import (
    CompletenessEvidence,
    ConversationRef,
    FetchCancelled,
    FetchPage,
    PaginationError,
    SourceAdapterError,
    SourceMessage,
    UpstreamProtocolError,
)


START = datetime(2026, 9, 1, tzinfo=timezone.utc)
END = datetime(2026, 9, 2, tzinfo=timezone.utc)
START_MS = int(START.timestamp() * 1000)
END_MS = int(END.timestamp() * 1000)


@dataclass
class MutableClock:
    now: datetime = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now


class SyntheticAdapter:
    def __init__(
        self,
        scope: ExportScope,
        pages: dict[str, FetchPage | BaseException],
        *,
        accounts: tuple[str, ...] | None = None,
        conversations: tuple[ConversationRef, ...] | None = None,
    ) -> None:
        self.scope = scope
        self.pages = pages
        self.accounts = accounts if accounts is not None else (scope.account_id,)
        self.conversations = conversations if conversations is not None else tuple(
            ConversationRef(
                source_id=scope.source_id,
                account_id=scope.account_id,
                conversation_id=item.conversation_id,
                display_name=item.display_name or "",
                conversation_type=item.conversation_type or "unknown",
            )
            for item in scope.conversations
        )
        self.fetch_calls: list[tuple[str, int, int]] = []
        self.cancelled = False
        self.entered: Event | None = None
        self.release: Event | None = None

    def health(self):  # pragma: no cover - the binding is already constructed
        raise AssertionError("orchestrator must not reinterpret P0 health as authorization")

    def list_accounts(self) -> tuple[str, ...]:
        return self.accounts

    def list_conversations(self) -> list[ConversationRef]:
        return list(self.conversations)

    def fetch_range(self, conversation_id: str, start_ms: int, end_ms: int) -> FetchPage:
        self.fetch_calls.append((conversation_id, start_ms, end_ms))
        if self.entered is not None:
            self.entered.set()
        if self.release is not None:
            self.release.wait(timeout=2)
        if self.cancelled:
            raise FetchCancelled("synthetic cancellation")
        result = self.pages[conversation_id]
        if isinstance(result, BaseException):
            raise result
        return result

    def cancel(self) -> None:
        self.cancelled = True


class SyntheticIndexer:
    def __init__(self, failures: int = 0) -> None:
        self.failures = failures
        self.calls: list[SnapshotRef] = []

    def __call__(self, snapshot: SnapshotRef) -> None:
        self.calls.append(snapshot)
        if self.failures:
            self.failures -= 1
            raise RuntimeError("synthetic index failure")


def _scope(**changes: object) -> ExportScope:
    values: dict[str, object] = {
        "source_id": "synthetic-source",
        "account_id": "account-a",
        "source_account_namespace": "synthetic:account-a",
        "conversations": (
            ExportConversation(
                conversation_id="conversation-a",
                display_name="合成会话 A",
                conversation_type="group",
            ),
        ),
        "range_start": START,
        "range_end": END,
        "output_root_id": "approved-root",
        "configuration_nonce": "configuration-a",
        "display_timezone": "Asia/Shanghai",
        "client_version": "client-1",
        "tool_version": "tool-1",
        "account_version": "account-1",
    }
    values.update(changes)
    return ExportScope(**values)


def _page(
    conversation_id: str,
    *,
    status: str = "source_range_verified",
    messages: tuple[SourceMessage, ...] | None = None,
) -> FetchPage:
    selected = messages
    if selected is None:
        selected = (
            SourceMessage(
                message_id=f"source-{conversation_id}",
                conversation_id=conversation_id,
                sent_at_ms=START_MS + 60_000,
                text="合成正文",
                source_order=1,
            ),
        )
    known = tuple(item.sent_at_ms for item in selected if item.sent_at_ms is not None)
    evidence = CompletenessEvidence(
        status=status,  # type: ignore[arg-type]
        reason=f"synthetic_{status}",
        requested_start_ms=START_MS,
        requested_end_ms=END_MS,
        observed_start_ms=min(known) if known else None,
        observed_end_ms=max(known) if known else None,
        fetched_count=len(selected),
        accepted_count=len(selected),
        duplicate_count=0,
        unknown_time_count=sum(item.sent_at_ms is None for item in selected),
        missing_id_count=sum(item.message_id is None for item in selected),
        parse_error_count=0,
        warnings=() if status == "source_range_verified" else ("synthetic_limitation",),
        termination="synthetic_exhausted",
    )
    return FetchPage(selected, None, evidence)


@dataclass
class Harness:
    repository: ChatRepository
    service: ExportJobOrchestrator
    adapter: SyntheticAdapter
    indexer: SyntheticIndexer
    output_root: Path
    clock: MutableClock

    def close(self) -> None:
        self.repository.close()


def _harness(
    tmp_path: Path,
    *,
    scope: ExportScope | None = None,
    pages: dict[str, FetchPage | BaseException] | None = None,
    index_failures: int = 0,
    binding_changes: dict[str, object] | None = None,
    accounts: tuple[str, ...] | None = None,
    source_retry_classifier=None,
    retry_wait=None,
) -> Harness:
    selected_scope = scope or _scope()
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    output_root = tmp_path / "批准的合成导出 目录"
    output_root.mkdir(mode=0o700)
    approved = ApprovedOutputRoot.approve(
        output_root,
        root_id=selected_scope.output_root_id,
        repository_root=repository_root,
    )
    repository = ChatRepository(tmp_path / "chat.sqlite3")
    selected_pages = pages or {
        item.conversation_id: _page(item.conversation_id)
        for item in selected_scope.conversations
    }
    adapter = SyntheticAdapter(selected_scope, selected_pages, accounts=accounts)
    binding_values: dict[str, object] = {
        "adapter": adapter,
        "source_id": selected_scope.source_id,
        "account_id": selected_scope.account_id,
        "source_account_namespace": selected_scope.source_account_namespace,
        "source_type": "synthetic",
        "client_version": selected_scope.client_version,
        "tool_version": selected_scope.tool_version,
        "account_version": selected_scope.account_version,
        "configuration_nonce": selected_scope.configuration_nonce,
    }
    binding_values.update(binding_changes or {})
    binding = SourceBinding(**binding_values)
    indexer = SyntheticIndexer(index_failures)
    clock = MutableClock()
    service = ExportJobOrchestrator(
        repository=repository,
        source_binding_factory=lambda _scope, *, session_digest: binding,
        artifact_writer_factory=lambda _scope: ArtifactWriter(approved),
        importer_factory=lambda _scope: BatchImporter(repository, approved),
        indexer=indexer,
        clock=clock,
        consent_ttl=timedelta(minutes=5),
        source_retry_classifier=source_retry_classifier,
        retry_wait=retry_wait,
    )
    return Harness(repository, service, adapter, indexer, output_root, clock)


def _authorize_and_start(
    harness: Harness,
    scope: ExportScope,
    *,
    session_id: str = "session-a",
    idempotency_key: str = "idempotency-a",
):
    consent = harness.service.issue_consent(
        session_id=session_id,
        scope=scope,
        confirmed=True,
    )
    return harness.service.start_export(
        session_id=session_id,
        scope=scope,
        consent_id=consent.consent_id,
        idempotency_key=idempotency_key,
    )


def _peer_service(
    harness: Harness, scope: ExportScope, tmp_path: Path
) -> tuple[ChatRepository, ExportJobOrchestrator]:
    repository = ChatRepository(harness.repository.path)
    approved = ApprovedOutputRoot.approve(
        harness.output_root,
        root_id=scope.output_root_id,
        repository_root=tmp_path / "repository",
    )
    binding = SourceBinding(
        adapter=harness.adapter,
        source_id=scope.source_id,
        account_id=scope.account_id,
        source_account_namespace=scope.source_account_namespace,
        source_type="synthetic",
        client_version=scope.client_version,
        tool_version=scope.tool_version,
        account_version=scope.account_version,
        configuration_nonce=scope.configuration_nonce,
    )
    service = ExportJobOrchestrator(
        repository=repository,
        source_binding_factory=lambda _scope, *, session_digest: binding,
        artifact_writer_factory=lambda _scope: ArtifactWriter(approved),
        importer_factory=lambda _scope: BatchImporter(repository, approved),
        indexer=harness.indexer,
        clock=harness.clock,
    )
    return repository, service


def test_consent_is_short_lived_single_use_and_idempotency_is_scope_bound(
    tmp_path: Path,
) -> None:
    scope = _scope()
    harness = _harness(tmp_path, scope=scope)
    try:
        consent = harness.service.issue_consent(
            session_id="session-a", scope=scope, confirmed=True
        )
        first = harness.service.start_export(
            session_id="session-a",
            scope=scope,
            consent_id=consent.consent_id,
            idempotency_key="same-key",
        )
        repeated = harness.service.start_export(
            session_id="session-a",
            scope=scope,
            consent_id=consent.consent_id,
            idempotency_key="same-key",
        )
        assert repeated.job_id == first.job_id
        assert repeated.state is ExportJobState.PENDING_AUTHORIZATION

        replay_consent = harness.service.issue_consent(
            session_id="session-a", scope=scope, confirmed=True
        )
        replayed_with_fresh_consent = harness.service.start_export(
            session_id="session-a",
            scope=scope,
            consent_id=replay_consent.consent_id,
            idempotency_key="same-key",
        )
        assert replayed_with_fresh_consent.job_id == first.job_id

        with pytest.raises(AuthorizationError, match="consent"):
            harness.service.start_export(
                session_id="session-a",
                scope=scope,
                consent_id=consent.consent_id,
                idempotency_key="different-key",
            )
        with pytest.raises(AuthorizationError, match="consent"):
            harness.service.start_export(
                session_id="session-a",
                scope=scope,
                consent_id=replay_consent.consent_id,
                idempotency_key="different-replay-key",
            )
        with pytest.raises(AuthorizationError, match="session"):
            harness.service.get_job(first.job_id, session_id="session-b")

        changed_scope = scope.model_copy(
            update={"range_end": END + timedelta(days=1)}
        )
        changed_consent = harness.service.issue_consent(
            session_id="session-a", scope=changed_scope, confirmed=True
        )
        changed = harness.service.start_export(
            session_id="session-a",
            scope=changed_scope,
            consent_id=changed_consent.consent_id,
            idempotency_key="same-key",
        )
        assert changed.job_id != first.job_id

        expiring = harness.service.issue_consent(
            session_id="session-a", scope=scope, confirmed=True
        )
        harness.clock.now += timedelta(minutes=6)
        with pytest.raises(AuthorizationError, match="expired"):
            harness.service.start_export(
                session_id="session-a",
                scope=scope,
                consent_id=expiring.consent_id,
                idempotency_key="expired-key",
            )
    finally:
        harness.close()


def test_start_from_consent_rehydrates_the_server_persisted_scope(
    tmp_path: Path,
) -> None:
    scope = _scope()
    harness = _harness(tmp_path, scope=scope)
    try:
        consent = harness.service.issue_consent(
            session_id="session-a", scope=scope, confirmed=True
        )

        started = harness.service.start_export_from_consent(
            session_id="session-a",
            consent_id=consent.consent_id,
            idempotency_key="persisted-scope",
        )

        assert started.state is ExportJobState.PENDING_AUTHORIZATION
        row = harness.repository.get_export_job(started.job_id)
        assert row["scope_fingerprint"] == scope.fingerprint()
        assert "conversation-a" in str(row["scope_json"])
    finally:
        harness.close()


def test_one_click_fetches_each_selected_id_writes_imports_and_indexes(
    tmp_path: Path,
) -> None:
    conversations = (
        ExportConversation(conversation_id="conversation-a", display_name="同名群", conversation_type="group"),
        ExportConversation(conversation_id="conversation-b", display_name="同名群", conversation_type="group"),
    )
    scope = _scope(conversations=conversations)
    harness = _harness(tmp_path, scope=scope)
    try:
        started = _authorize_and_start(harness, scope)
        completed = harness.service.run_job(started.job_id)

        assert completed.state is ExportJobState.COMPLETE
        assert completed.completeness == "source_range_verified"
        assert completed.searchable is True
        assert completed.progress_percent is None
        assert harness.adapter.fetch_calls == [
            ("conversation-a", START_MS, END_MS),
            ("conversation-b", START_MS, END_MS),
        ]
        assert len(harness.indexer.calls) == 1
        assert harness.repository.message_count() == 2
        assert completed.batch_id is not None
        assert completed.snapshot_id is not None
        assert (harness.output_root / "exports" / completed.batch_id / "manifest.json").is_file()
        assert harness.repository.export_job_state_history(started.job_id) == (
            "pending_authorization",
            "connecting",
            "reading",
            "saving",
            "verifying",
            "indexing",
            "complete",
        )
    finally:
        harness.close()


def test_successful_export_is_automatically_imported_and_immediately_searchable(
    tmp_path: Path,
) -> None:
    scope = _scope()
    harness = _harness(tmp_path, scope=scope)
    try:
        completed = harness.service.run_job(
            _authorize_and_start(harness, scope).job_id
        )

        page = ChatSearchService(harness.repository).search(
            SearchRequest(
                query="合成正文",
                conversation_ids=("conversation-a",),
                start_time=START,
                end_time=END,
            ),
            session_id="session-a",
        )

        assert completed.state is ExportJobState.COMPLETE
        assert completed.searchable is True
        assert len(page.results) == 1
        assert page.results[0].batch_id == completed.batch_id
        assert page.results[0].snapshot_id == completed.snapshot_id
        assert page.results[0].text == "合成正文"
    finally:
        harness.close()


@pytest.mark.parametrize(
    ("failure", "expected_state", "error_code"),
    (
        (PaginationError("synthetic pagination"), ExportJobState.FAILED, "pagination_failed"),
        (UpstreamProtocolError("synthetic protocol"), ExportJobState.FAILED, "protocol_failed"),
        (SourceAdapterError("synthetic transport"), ExportJobState.FAILED, "transport_failed"),
        (FetchCancelled("synthetic cancel"), ExportJobState.CANCELLED, "cancelled"),
    ),
)
def test_any_selected_source_failure_commits_no_batch(
    tmp_path: Path,
    failure: BaseException,
    expected_state: ExportJobState,
    error_code: str,
) -> None:
    conversations = (
        ExportConversation(conversation_id="conversation-a"),
        ExportConversation(conversation_id="conversation-b"),
    )
    scope = _scope(conversations=conversations)
    pages = {
        "conversation-a": _page("conversation-a"),
        "conversation-b": failure,
    }
    harness = _harness(tmp_path, scope=scope, pages=pages)
    try:
        started = _authorize_and_start(harness, scope)
        result = harness.service.run_job(started.job_id)

        assert result.state is expected_state
        assert result.error_code == error_code
        assert result.batch_id is None
        assert harness.repository.message_count() == 0
        assert not (harness.output_root / "exports").exists()
    finally:
        harness.close()


def test_unknown_time_and_missing_identity_are_excluded_and_counted(
    tmp_path: Path,
) -> None:
    scope = _scope()
    messages = (
        SourceMessage("known", "conversation-a", START_MS + 1_000, "保留", 1),
        SourceMessage("unknown-time", "conversation-a", None, "排除时间", 2),
        SourceMessage(None, "conversation-a", START_MS + 2_000, "排除身份", 3),
    )
    page = _page("conversation-a", status="partial", messages=messages)
    harness = _harness(tmp_path, scope=scope, pages={"conversation-a": page})
    try:
        started = _authorize_and_start(harness, scope)
        result = harness.service.run_job(started.job_id)
        batch = harness.service.get_batch(result.batch_id, session_id="session-a")

        assert result.state is ExportJobState.PARTIAL
        assert result.searchable is False
        assert result.saved_count == 1
        assert result.unknown_time_count == 1
        assert result.parse_error_count == 1
        assert batch["manifest_sha256"] == result.manifest_sha256
        assert batch["completeness"] == "partial"
        assert harness.repository.message_count() == 1
        assert harness.indexer.calls == []
    finally:
        harness.close()


@pytest.mark.parametrize("status", ("partial", "unknown"))
def test_incomplete_snapshot_requires_hash_bound_acceptance_before_index(
    tmp_path: Path,
    status: str,
) -> None:
    scope = _scope()
    harness = _harness(
        tmp_path,
        scope=scope,
        pages={"conversation-a": _page("conversation-a", status=status)},
    )
    try:
        started = _authorize_and_start(harness, scope)
        pending = harness.service.run_job(started.job_id)
        assert pending.state is ExportJobState.PARTIAL
        assert pending.searchable is False
        assert harness.indexer.calls == []
        assert harness.service.get_batch(
            pending.batch_id, session_id="session-a"
        )["generation_eligible"] is False

        with pytest.raises(JobConflict, match="manifest hash"):
            harness.service.accept_limitations(
                pending.job_id,
                session_id="session-a",
                manifest_sha256="0" * 64,
            )
        accepted = harness.service.accept_limitations(
            pending.job_id,
            session_id="session-a",
            manifest_sha256=pending.manifest_sha256,
        )

        assert accepted.state is ExportJobState.COMPLETE
        assert accepted.searchable is True
        assert len(harness.indexer.calls) == 1
        batch = harness.service.get_batch(accepted.batch_id, session_id="session-a")
        assert batch["limitations_accepted_manifest_sha256"] == pending.manifest_sha256
        assert batch["generation_eligible"] is True
    finally:
        harness.close()


def test_empty_batch_skips_index_and_is_never_generation_eligible(tmp_path: Path) -> None:
    scope = _scope()
    harness = _harness(
        tmp_path,
        scope=scope,
        pages={"conversation-a": _page("conversation-a", status="empty", messages=())},
    )
    try:
        result = harness.service.run_job(_authorize_and_start(harness, scope).job_id)
        batch = harness.service.get_batch(result.batch_id, session_id="session-a")

        assert result.state is ExportJobState.COMPLETE
        assert result.completeness == "empty"
        assert result.searchable is False
        assert batch["generation_eligible"] is False
        assert harness.indexer.calls == []
    finally:
        harness.close()


def test_index_failure_retries_snapshot_without_refetch(tmp_path: Path) -> None:
    scope = _scope()
    harness = _harness(tmp_path, scope=scope, index_failures=1)
    try:
        failed = harness.service.run_job(_authorize_and_start(harness, scope).job_id)
        calls_after_fetch = tuple(harness.adapter.fetch_calls)
        assert failed.state is ExportJobState.FAILED
        assert failed.error_code == "index_failed"
        assert failed.snapshot_id is not None

        completed = harness.service.retry_index(failed.job_id, session_id="session-a")

        assert completed.state is ExportJobState.COMPLETE
        assert tuple(harness.adapter.fetch_calls) == calls_after_fetch
        assert len(harness.indexer.calls) == 2
    finally:
        harness.close()


def test_stale_worker_cannot_write_after_cancel_or_delete(tmp_path: Path) -> None:
    scope = _scope()
    harness = _harness(tmp_path, scope=scope)
    try:
        started = _authorize_and_start(harness, scope)
        stale = harness.repository.get_export_job(started.job_id)
        cancelled = harness.service.cancel_job(started.job_id, session_id="session-a")
        assert cancelled.state is ExportJobState.CANCELLED
        assert harness.repository.cas_export_job(
            started.job_id,
            expected_state="pending_authorization",
            expected_revision=stale["revision"],
            new_state="complete",
        ) is False

        second = _authorize_and_start(
            harness, scope, idempotency_key="delete-key"
        )
        stale_second = harness.repository.get_export_job(second.job_id)
        harness.service.delete_job(second.job_id, session_id="session-a")
        assert harness.repository.cas_export_job(
            second.job_id,
            expected_state="pending_authorization",
            expected_revision=stale_second["revision"],
            new_state="complete",
        ) is False
        with pytest.raises(JobNotFound):
            harness.service.get_job(second.job_id, session_id="session-a")
    finally:
        harness.close()


def test_cancel_calls_active_adapter_and_prevents_artifact_commit(tmp_path: Path) -> None:
    scope = _scope()
    harness = _harness(tmp_path, scope=scope)
    entered = Event()
    release = Event()
    harness.adapter.entered = entered
    harness.adapter.release = release
    started = _authorize_and_start(harness, scope)
    result: list[object] = []
    runner = Thread(target=lambda: result.append(harness.service.run_job(started.job_id)))
    try:
        runner.start()
        assert entered.wait(timeout=2)
        cancelled = harness.service.cancel_job(started.job_id, session_id="session-a")
        release.set()
        runner.join(timeout=2)

        assert cancelled.state is ExportJobState.CANCELLED
        assert harness.adapter.cancelled is True
        assert result and result[0].state is ExportJobState.CANCELLED
        assert not (harness.output_root / "exports").exists()
    finally:
        release.set()
        runner.join(timeout=2)
        harness.close()


def test_cancel_retries_when_a_worker_wins_the_first_cas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A one-revision race cannot make cancellation falsely report a running state."""
    scope = _scope()
    harness = _harness(tmp_path, scope=scope)
    started = _authorize_and_start(harness, scope)
    real_cas = harness.repository.cas_export_job
    raced = False

    def race_once(job_id: str, **arguments: object) -> bool:
        nonlocal raced
        if not raced and arguments.get("new_state") == "cancelled":
            raced = True
            assert real_cas(
                job_id,
                expected_state=str(arguments["expected_state"]),
                expected_revision=int(arguments["expected_revision"]),
                new_state="connecting",
            )
            return False
        return real_cas(job_id, **arguments)

    monkeypatch.setattr(harness.repository, "cas_export_job", race_once)
    try:
        cancelled = harness.service.cancel_job(started.job_id, session_id="session-a")

        assert raced is True
        assert cancelled.state is ExportJobState.CANCELLED
    finally:
        harness.close()


def test_binding_or_selected_account_change_fails_before_fetch(tmp_path: Path) -> None:
    scope = _scope()
    for index, options in enumerate(
        (
            {"binding_changes": {"configuration_nonce": "changed"}},
            {"accounts": ("another-account",)},
        )
    ):
        case = tmp_path / f"case-{index}"
        case.mkdir()
        harness = _harness(case, scope=scope, **options)
        try:
            result = harness.service.run_job(_authorize_and_start(harness, scope).job_id)
            assert result.state is ExportJobState.FAILED
            assert result.error_code == "source_binding_changed"
            assert harness.adapter.fetch_calls == []
            assert not (harness.output_root / "exports").exists()
        finally:
            harness.close()


def test_restart_reconciliation_fails_only_interrupted_transient_jobs(
    tmp_path: Path,
) -> None:
    scope = _scope()
    harness = _harness(tmp_path, scope=scope)
    try:
        interrupted = _authorize_and_start(harness, scope, idempotency_key="interrupted")
        row = harness.repository.get_export_job(interrupted.job_id)
        assert harness.repository.cas_export_job(
            interrupted.job_id,
            expected_state="pending_authorization",
            expected_revision=row["revision"],
            new_state="connecting",
        )
        untouched = _authorize_and_start(harness, scope, idempotency_key="untouched")

        assert harness.service.reconcile_interrupted() == 1
        reconciled = harness.service.get_job(interrupted.job_id, session_id="session-a")
        assert reconciled.state is ExportJobState.FAILED
        assert reconciled.error_code == "interrupted_before_commit"
        assert harness.service.get_job(
            untouched.job_id, session_id="session-a"
        ).state is ExportJobState.PENDING_AUTHORIZATION
    finally:
        harness.close()


def test_reconcile_reloads_revision_and_honors_racing_durable_cancel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scope = _scope()
    harness = _harness(tmp_path, scope=scope)
    started = _authorize_and_start(harness, scope)
    row = harness.repository.get_export_job(started.job_id)
    assert harness.repository.cas_export_job(
        started.job_id,
        expected_state="pending_authorization",
        expected_revision=row["revision"],
        new_state="saving",
    )
    original_cas = harness.repository.cas_export_job
    raced = False

    def race_cancel_request(job_id: str, **arguments: object) -> bool:
        nonlocal raced
        if not raced and arguments.get("new_state") == "failed":
            raced = True
            assert original_cas(
                job_id,
                expected_state=str(arguments["expected_state"]),
                expected_revision=int(arguments["expected_revision"]),
                new_state=str(arguments["expected_state"]),
                cancellation_requested=1,
            )
            return False
        return original_cas(job_id, **arguments)

    monkeypatch.setattr(harness.repository, "cas_export_job", race_cancel_request)
    try:
        assert harness.service.reconcile_interrupted() == 1
        reconciled = harness.service.get_job(started.job_id, session_id="session-a")
        assert raced is True
        assert reconciled.state is ExportJobState.CANCELLED
        assert reconciled.cancellation_requested is True
        assert reconciled.error_code == "cancelled"
    finally:
        harness.close()


def test_restart_during_indexing_remains_retryable_without_refetch(tmp_path: Path) -> None:
    """A crash after immutable import must never force another source read."""
    scope = _scope()
    harness = _harness(tmp_path, scope=scope, index_failures=1)
    try:
        failed = harness.service.run_job(_authorize_and_start(harness, scope).job_id)
        fetch_calls = tuple(harness.adapter.fetch_calls)
        row = harness.repository.get_export_job(failed.job_id)
        assert harness.repository.cas_export_job(
            failed.job_id,
            expected_state="failed",
            expected_revision=row["revision"],
            new_state="indexing",
            error_code=None,
        )

        assert harness.service.reconcile_interrupted() == 1
        interrupted = harness.service.get_job(failed.job_id, session_id="session-a")
        assert interrupted.error_code == "index_interrupted"

        completed = harness.service.retry_index(failed.job_id, session_id="session-a")
        assert completed.state is ExportJobState.COMPLETE
        assert tuple(harness.adapter.fetch_calls) == fetch_calls
    finally:
        harness.close()


def test_progress_is_persisted_after_each_selected_conversation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conversations = (
        ExportConversation(conversation_id="conversation-a"),
        ExportConversation(conversation_id="conversation-b"),
    )
    scope = _scope(conversations=conversations)
    harness = _harness(tmp_path, scope=scope)
    entered_second = Event()
    release_second = Event()
    original_fetch = harness.adapter.fetch_range

    def blocking_second(conversation_id: str, start_ms: int, end_ms: int) -> FetchPage:
        if conversation_id == "conversation-b":
            entered_second.set()
            assert release_second.wait(timeout=2)
        return original_fetch(conversation_id, start_ms, end_ms)

    monkeypatch.setattr(harness.adapter, "fetch_range", blocking_second)
    started = _authorize_and_start(harness, scope)
    result: list[object] = []
    runner = Thread(target=lambda: result.append(harness.service.run_job(started.job_id)))
    try:
        runner.start()
        assert entered_second.wait(timeout=2)

        progress = harness.service.get_job(started.job_id, session_id="session-a")
        assert progress.state is ExportJobState.READING
        assert progress.completed_count == 1
        assert progress.fetched_count == 1
        assert progress.saved_count == 1
        assert progress.known_total is None
        assert progress.progress_percent is None

        release_second.set()
        runner.join(timeout=2)
        assert result and result[0].state is ExportJobState.COMPLETE
    finally:
        release_second.set()
        runner.join(timeout=2)
        harness.close()


def test_cancel_winning_before_commit_claim_prevents_artifact_side_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scope = _scope()
    harness = _harness(tmp_path, scope=scope)
    saving_claimable = Event()
    release_worker = Event()
    original_transition = harness.service._transition

    def pause_after_saving(row, state, **changes):
        changed = original_transition(row, state, **changes)
        if changed and state is ExportJobState.SAVING:
            saving_claimable.set()
            assert release_worker.wait(timeout=2)
        return changed

    monkeypatch.setattr(harness.service, "_transition", pause_after_saving)
    started = _authorize_and_start(harness, scope)
    result: list[object] = []
    runner = Thread(target=lambda: result.append(harness.service.run_job(started.job_id)))
    try:
        runner.start()
        assert saving_claimable.wait(timeout=2)
        cancelled = harness.service.cancel_job(started.job_id, session_id="session-a")
        release_worker.set()
        runner.join(timeout=2)

        assert cancelled.state is ExportJobState.CANCELLED
        assert result and result[0].state is ExportJobState.CANCELLED
        assert not (harness.output_root / "exports").exists()
        assert harness.repository.message_count() == 0
    finally:
        release_worker.set()
        runner.join(timeout=2)
        harness.close()


def test_delete_winning_before_commit_claim_prevents_artifact_side_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scope = _scope()
    harness = _harness(tmp_path, scope=scope)
    saving_claimable = Event()
    release_worker = Event()
    original_transition = harness.service._transition

    def pause_after_saving(row, state, **changes):
        changed = original_transition(row, state, **changes)
        if changed and state is ExportJobState.SAVING:
            saving_claimable.set()
            assert release_worker.wait(timeout=2)
        return changed

    monkeypatch.setattr(harness.service, "_transition", pause_after_saving)
    started = _authorize_and_start(harness, scope)
    errors: list[BaseException] = []

    def run() -> None:
        try:
            harness.service.run_job(started.job_id)
        except BaseException as error:
            errors.append(error)

    runner = Thread(target=run)
    try:
        runner.start()
        assert saving_claimable.wait(timeout=2)
        harness.service.delete_job(started.job_id, session_id="session-a")
        release_worker.set()
        runner.join(timeout=2)

        assert len(errors) == 1 and isinstance(errors[0], JobNotFound)
        assert not (harness.output_root / "exports").exists()
        assert harness.repository.message_count() == 0
    finally:
        release_worker.set()
        runner.join(timeout=2)
        harness.close()


def test_cancel_request_during_artifact_write_stops_unstarted_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scope = _scope()
    harness = _harness(tmp_path, scope=scope)
    writer_entered = Event()
    release_writer = Event()
    approved = ApprovedOutputRoot.approve(
        harness.output_root,
        root_id=scope.output_root_id,
        repository_root=tmp_path / "repository",
    )
    delegate = ArtifactWriter(approved)

    class BlockingWriter:
        def write_batch(self, **arguments):
            writer_entered.set()
            assert release_writer.wait(timeout=2)
            return delegate.write_batch(**arguments)

    monkeypatch.setattr(
        harness.service,
        "_artifact_writer_factory",
        lambda _scope: BlockingWriter(),
    )
    started = _authorize_and_start(harness, scope)
    result: list[object] = []
    runner = Thread(target=lambda: result.append(harness.service.run_job(started.job_id)))
    try:
        runner.start()
        assert writer_entered.wait(timeout=2)
        assert harness.service.reconcile_interrupted() == 0
        requested = harness.service.cancel_job(started.job_id, session_id="session-a")
        assert requested.state is ExportJobState.SAVING
        assert requested.cancellation_requested is True
        with pytest.raises(JobConflict, match="commit"):
            harness.service.delete_job(started.job_id, session_id="session-a")

        release_writer.set()
        runner.join(timeout=2)
        assert result and result[0].state is ExportJobState.CANCELLED
        assert harness.repository.message_count() == 0
        assert harness.indexer.calls == []
        assert len(tuple((harness.output_root / "exports").iterdir())) == 1
        lock_files = tuple(tmp_path.glob("chat.sqlite3.export.*.lock"))
        assert len(lock_files) == 1
        assert stat.S_ISREG(lock_files[0].stat().st_mode)
        assert stat.S_IMODE(lock_files[0].stat().st_mode) == 0o600
        assert started.job_id not in lock_files[0].name
    finally:
        release_writer.set()
        runner.join(timeout=2)
        harness.close()


def test_cancel_during_started_index_is_recorded_without_false_cancel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scope = _scope()
    harness = _harness(tmp_path, scope=scope)
    peer_repository, peer = _peer_service(harness, scope, tmp_path)
    index_entered = Event()
    release_index = Event()

    def blocking_index(snapshot: SnapshotRef) -> None:
        index_entered.set()
        assert release_index.wait(timeout=2)
        harness.indexer(snapshot)

    monkeypatch.setattr(harness.service, "_indexer", blocking_index)
    started = _authorize_and_start(harness, scope)
    result: list[object] = []
    runner = Thread(target=lambda: result.append(harness.service.run_job(started.job_id)))
    try:
        runner.start()
        assert index_entered.wait(timeout=2)
        requested = peer.cancel_job(started.job_id, session_id="session-a")
        assert requested.state is ExportJobState.INDEXING
        assert requested.cancellation_requested is True

        release_index.set()
        runner.join(timeout=2)
        assert result and result[0].state is ExportJobState.COMPLETE
        assert result[0].cancellation_requested is True
        assert len(harness.indexer.calls) == 1
    finally:
        release_index.set()
        runner.join(timeout=2)
        peer_repository.close()
        harness.close()


def test_peer_instance_fence_prevents_cancel_delete_and_reconcile_writeback_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scope = _scope()
    harness = _harness(tmp_path, scope=scope)
    peer_repository, peer = _peer_service(harness, scope, tmp_path)
    writer_entered = Event()
    release_writer = Event()
    approved = ApprovedOutputRoot.approve(
        harness.output_root,
        root_id=scope.output_root_id,
        repository_root=tmp_path / "repository",
    )
    delegate = ArtifactWriter(approved)

    class BlockingWriter:
        def write_batch(self, **arguments):
            writer_entered.set()
            assert release_writer.wait(timeout=2)
            return delegate.write_batch(**arguments)

    monkeypatch.setattr(
        harness.service,
        "_artifact_writer_factory",
        lambda _scope: BlockingWriter(),
    )
    started = _authorize_and_start(harness, scope)
    result: list[object] = []
    runner = Thread(target=lambda: result.append(harness.service.run_job(started.job_id)))
    try:
        runner.start()
        assert writer_entered.wait(timeout=2)
        assert peer.reconcile_interrupted() == 0
        requested = peer.cancel_job(started.job_id, session_id="session-a")
        assert requested.state is ExportJobState.SAVING
        assert requested.cancellation_requested is True
        with pytest.raises(JobConflict, match="commit"):
            peer.delete_job(started.job_id, session_id="session-a")

        release_writer.set()
        runner.join(timeout=2)
        assert result and result[0].state is ExportJobState.CANCELLED
        assert harness.repository.message_count() == 0
        assert harness.indexer.calls == []
        assert len(tuple((harness.output_root / "exports").iterdir())) == 1
    finally:
        release_writer.set()
        runner.join(timeout=2)
        peer_repository.close()
        harness.close()


def test_peer_cancel_during_import_binds_residue_and_skips_unstarted_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scope = _scope()
    harness = _harness(tmp_path, scope=scope)
    peer_repository, peer = _peer_service(harness, scope, tmp_path)
    import_entered = Event()
    release_import = Event()
    approved = ApprovedOutputRoot.approve(
        harness.output_root,
        root_id=scope.output_root_id,
        repository_root=tmp_path / "repository",
    )
    delegate = BatchImporter(harness.repository, approved)

    class BlockingImporter:
        def import_verified(self, **arguments):
            import_entered.set()
            assert release_import.wait(timeout=2)
            return delegate.import_verified(**arguments)

    monkeypatch.setattr(
        harness.service,
        "_importer_factory",
        lambda _scope: BlockingImporter(),
    )
    started = _authorize_and_start(harness, scope)
    result: list[object] = []
    runner = Thread(target=lambda: result.append(harness.service.run_job(started.job_id)))
    try:
        runner.start()
        assert import_entered.wait(timeout=2)
        requested = peer.cancel_job(started.job_id, session_id="session-a")
        assert requested.state is ExportJobState.VERIFYING
        assert requested.cancellation_requested is True

        release_import.set()
        runner.join(timeout=2)
        assert result and result[0].state is ExportJobState.CANCELLED
        assert result[0].batch_id is not None
        assert result[0].snapshot_id is not None
        assert result[0].searchable is False
        assert harness.repository.message_count() == 1
        assert harness.indexer.calls == []
    finally:
        release_import.set()
        runner.join(timeout=2)
        peer_repository.close()
        harness.close()


@pytest.mark.parametrize("simulate_restart", (False, True))
def test_import_failure_retries_committed_artifacts_without_refetch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    simulate_restart: bool,
) -> None:
    scope = _scope()
    harness = _harness(tmp_path, scope=scope)
    approved = ApprovedOutputRoot.approve(
        harness.output_root,
        root_id=scope.output_root_id,
        repository_root=tmp_path / "repository",
    )
    delegate = BatchImporter(harness.repository, approved)
    calls = 0

    class FailOnceImporter:
        def import_verified(self, **arguments):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("synthetic import failure")
            return delegate.import_verified(**arguments)

    monkeypatch.setattr(
        harness.service,
        "_importer_factory",
        lambda _scope: FailOnceImporter(),
    )
    try:
        failed = harness.service.run_job(_authorize_and_start(harness, scope).job_id)
        fetch_calls = tuple(harness.adapter.fetch_calls)

        assert failed.state is ExportJobState.FAILED
        assert failed.error_code == "import_failed"
        assert failed.batch_id is None
        assert (harness.output_root / "exports").is_dir()
        assert harness.repository.message_count() == 0

        if simulate_restart:
            interrupted_row = harness.repository.get_export_job(failed.job_id)
            assert harness.repository.cas_export_job(
                failed.job_id,
                expected_state="failed",
                expected_revision=interrupted_row["revision"],
                new_state="verifying",
                error_code=None,
            )
            assert harness.service.reconcile_interrupted() == 1
            interrupted = harness.service.get_job(failed.job_id, session_id="session-a")
            assert interrupted.error_code == "import_interrupted"

        completed = harness.service.retry_import(failed.job_id, session_id="session-a")
        assert completed.state is ExportJobState.COMPLETE
        assert completed.searchable is True
        assert tuple(harness.adapter.fetch_calls) == fetch_calls
        assert harness.repository.message_count() == 1
        assert calls == 2
    finally:
        harness.close()


def test_source_transport_failure_during_binding_is_not_misreported_as_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scope = _scope()
    harness = _harness(tmp_path, scope=scope)

    def unavailable_accounts() -> tuple[str, ...]:
        raise SourceAdapterError("synthetic private transport detail")

    monkeypatch.setattr(harness.adapter, "list_accounts", unavailable_accounts)
    try:
        result = harness.service.run_job(_authorize_and_start(harness, scope).job_id)
        assert result.state is ExportJobState.FAILED
        assert result.error_code == "transport_failed"
        assert harness.adapter.fetch_calls == []
        assert not (harness.output_root / "exports").exists()
    finally:
        harness.close()


def test_only_explicitly_classified_transient_source_errors_retry_with_backoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scope = _scope()
    delays: list[float] = []
    harness = _harness(
        tmp_path,
        scope=scope,
        source_retry_classifier=lambda error: str(error) == "synthetic transient",
        retry_wait=delays.append,
    )
    original_fetch = harness.adapter.fetch_range
    attempts = 0

    def flaky_fetch(conversation_id: str, start_ms: int, end_ms: int) -> FetchPage:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise SourceAdapterError("synthetic transient")
        return original_fetch(conversation_id, start_ms, end_ms)

    monkeypatch.setattr(harness.adapter, "fetch_range", flaky_fetch)
    try:
        result = harness.service.run_job(_authorize_and_start(harness, scope).job_id)
        assert result.state is ExportJobState.COMPLETE
        assert attempts == 2
        assert delays == [0.25]
        assert harness.repository.message_count() == 1
    finally:
        harness.close()
