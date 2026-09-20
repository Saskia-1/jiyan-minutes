"""Durable, local-only P1 export orchestration with CAS state transitions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import re
from threading import Lock, RLock
from time import sleep
from typing import Callable, Literal, Protocol
from uuid import uuid4
from weakref import WeakValueDictionary

from pydantic import BaseModel, ConfigDict

from chat_to_howto.domain.jobs import ExportJobState, ExportScope
from chat_to_howto.domain.messages import MessageInput, StoredMessage
from chat_to_howto.repositories.chat import ChatRepository, SnapshotRef
from chat_to_howto.services.artifacts import ArtifactWriter
from chat_to_howto.services.importer import BatchImporter
from chat_to_howto.sources.base import (
    CompletenessEvidence,
    FetchCancelled,
    FetchPage,
    PaginationError,
    SourceAdapter,
    SourceAdapterError,
    UpstreamProtocolError,
)


_OPAQUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_TRANSIENT_STATES = (
    "connecting",
    "reading",
    "saving",
    "verifying",
    "indexing",
)


class AuthorizationError(PermissionError):
    """A safe P1 session/consent boundary failure."""


class JobConflict(RuntimeError):
    """The requested operation does not match the current durable job state."""


class JobNotFound(LookupError):
    """The session cannot access the requested job or batch."""


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class ConsentGrant(_FrozenModel):
    consent_id: str
    scope_fingerprint: str
    expires_at_utc: datetime


class ExportJobView(_FrozenModel):
    job_id: str
    state: ExportJobState
    batch_id: str | None
    snapshot_id: str | None
    scope_fingerprint: str
    known_total: int | None
    completed_count: int | None
    fetched_count: int | None
    saved_count: int | None
    duplicate_count: int | None
    unknown_time_count: int | None
    parse_error_count: int | None
    manifest_sha256: str | None
    completeness: Literal["source_range_verified", "partial", "unknown", "empty"] | None
    error_code: str | None
    searchable: bool
    cancellation_requested: bool = False
    progress_percent: int | None
    revision: int


@dataclass(frozen=True, slots=True)
class SourceBinding:
    """Observed local adapter identity bound to the approved ExportScope."""

    adapter: SourceAdapter
    source_id: str
    account_id: str
    source_account_namespace: str | None
    source_type: Literal["qq", "wechat", "synthetic", "manual"]
    client_version: str | None
    tool_version: str | None
    account_version: str | None
    configuration_nonce: str


@dataclass(slots=True)
class _SideEffectClaim:
    repository: ChatRepository
    thread_lock: Lock
    descriptor: int

    def release(self) -> None:
        try:
            self.repository.release_export_side_effect_lock(self.descriptor)
        finally:
            self.thread_lock.release()


class SourceBindingFactory(Protocol):
    def __call__(
        self, scope: ExportScope, *, session_digest: str
    ) -> SourceBinding: ...


class ArtifactWriterFactory(Protocol):
    def __call__(self, scope: ExportScope) -> ArtifactWriter: ...


class ImporterFactory(Protocol):
    def __call__(self, scope: ExportScope) -> BatchImporter: ...


class ExportJobOrchestrator:
    """Fetch all selected conversations, then atomically write/import/index once."""

    def __init__(
        self,
        *,
        repository: ChatRepository,
        source_binding_factory: SourceBindingFactory,
        artifact_writer_factory: ArtifactWriterFactory,
        importer_factory: ImporterFactory,
        indexer: Callable[[SnapshotRef], None],
        clock: Callable[[], datetime] | None = None,
        consent_ttl: timedelta = timedelta(minutes=5),
        source_retry_classifier: Callable[[SourceAdapterError], bool] | None = None,
        retry_wait: Callable[[float], None] | None = None,
        max_source_retries: int = 2,
    ) -> None:
        if consent_ttl <= timedelta(0) or consent_ttl > timedelta(minutes=15):
            raise ValueError("export consent TTL must be between zero and fifteen minutes")
        if not isinstance(max_source_retries, int) or not 0 <= max_source_retries <= 3:
            raise ValueError("source retry count must be between zero and three")
        self._repository = repository
        self._source_binding_factory = source_binding_factory
        self._artifact_writer_factory = artifact_writer_factory
        self._importer_factory = importer_factory
        self._indexer = indexer
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._consent_ttl = consent_ttl
        self._source_retry_classifier = source_retry_classifier or (lambda _error: False)
        self._retry_wait = retry_wait or sleep
        self._max_source_retries = max_source_retries
        self._active_lock = RLock()
        self._active_adapters: dict[str, SourceAdapter] = {}
        self._side_effect_locks: WeakValueDictionary[str, Lock] = WeakValueDictionary()

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("orchestrator clock must be timezone-aware")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _session_digest(session_id: str) -> str:
        if not isinstance(session_id, str) or not session_id or len(session_id) > 4096:
            raise AuthorizationError("a bounded local session is required")
        return sha256(session_id.encode("utf-8")).hexdigest()

    @staticmethod
    def _require_opaque(value: str, label: str) -> str:
        if not isinstance(value, str) or not _OPAQUE.fullmatch(value) or value in {".", ".."}:
            raise ValueError(f"opaque {label} is required")
        return value

    def issue_consent(
        self,
        *,
        session_id: str,
        scope: ExportScope,
        confirmed: bool,
    ) -> ConsentGrant:
        if confirmed is not True:
            raise AuthorizationError("explicit export consent is required")
        session_digest = self._session_digest(session_id)
        now = self._now()
        expires = now + self._consent_ttl
        consent_id = f"consent:{uuid4().hex}"
        self._repository.create_export_consent(
            consent_id=consent_id,
            session_digest=session_digest,
            scope=scope,
            recorded_at_utc=now.isoformat(),
            expires_at_utc=expires.isoformat(),
        )
        return ConsentGrant(
            consent_id=consent_id,
            scope_fingerprint=scope.fingerprint(),
            expires_at_utc=expires,
        )

    def start_export(
        self,
        *,
        session_id: str,
        scope: ExportScope,
        consent_id: str,
        idempotency_key: str,
    ) -> ExportJobView:
        session_digest = self._session_digest(session_id)
        self._require_opaque(consent_id, "consent ID")
        self._require_opaque(idempotency_key, "idempotency key")
        now = self._now().isoformat()
        try:
            row, _created = self._repository.start_export_job(
                job_id=f"job:{uuid4().hex}",
                planned_batch_id=f"batch:{uuid4().hex}",
                planned_snapshot_id=f"snapshot:{uuid4().hex}",
                session_digest=session_digest,
                scope=scope,
                consent_id=consent_id,
                idempotency_key=idempotency_key,
                now_utc=now,
            )
        except PermissionError as error:
            raise AuthorizationError(str(error)) from None
        return self._view(row)

    def start_export_from_consent(
        self,
        *,
        session_id: str,
        consent_id: str,
        idempotency_key: str,
    ) -> ExportJobView:
        """Start from the exact server-held scope without exposing it to the UI."""
        session_digest = self._session_digest(session_id)
        self._require_opaque(consent_id, "consent ID")
        try:
            scope = self._repository.export_consent_scope(
                consent_id=consent_id,
                session_digest=session_digest,
            )
        except PermissionError as error:
            raise AuthorizationError(str(error)) from None
        return self.start_export(
            session_id=session_id,
            scope=scope,
            consent_id=consent_id,
            idempotency_key=idempotency_key,
        )

    def get_job(self, job_id: str, *, session_id: str) -> ExportJobView:
        row = self._job_for_session(job_id, session_id=session_id)
        return self._view(row)

    def _job_for_session(self, job_id: str, *, session_id: str) -> dict[str, object]:
        self._require_opaque(job_id, "job ID")
        digest = self._session_digest(session_id)
        try:
            row = self._repository.get_export_job(job_id)
        except ValueError:
            raise JobNotFound("export job was not found") from None
        if row["session_digest"] != digest:
            raise AuthorizationError("export job belongs to another session")
        return row

    @staticmethod
    def _scope_from_row(row: dict[str, object]) -> ExportScope:
        payload = row.get("scope_json")
        if not isinstance(payload, str):
            raise JobConflict("export job has no recoverable scope")
        return ExportScope.model_validate_json(payload)

    @staticmethod
    def _view(row: dict[str, object]) -> ExportJobView:
        known_total = row.get("known_total")
        completed = row.get("completed_count")
        percent: int | None = None
        if isinstance(known_total, int) and known_total > 0 and isinstance(completed, int):
            percent = min(100, (completed * 100) // known_total)
        return ExportJobView(
            job_id=str(row["job_id"]),
            state=ExportJobState(str(row["state"])),
            batch_id=str(row["batch_id"]) if row.get("batch_id") is not None else None,
            snapshot_id=(
                str(row["snapshot_id"]) if row.get("snapshot_id") is not None else None
            ),
            scope_fingerprint=str(row["scope_fingerprint"]),
            known_total=known_total if isinstance(known_total, int) else None,
            completed_count=completed if isinstance(completed, int) else None,
            fetched_count=_optional_int(row.get("fetched_count")),
            saved_count=_optional_int(row.get("saved_count")),
            duplicate_count=_optional_int(row.get("duplicate_count")),
            unknown_time_count=_optional_int(row.get("unknown_time_count")),
            parse_error_count=_optional_int(row.get("parse_error_count")),
            manifest_sha256=(
                str(row["manifest_sha256"])
                if row.get("manifest_sha256") is not None
                else None
            ),
            completeness=row.get("completeness"),  # type: ignore[arg-type]
            error_code=str(row["error_code"]) if row.get("error_code") is not None else None,
            searchable=bool(row.get("searchable")),
            cancellation_requested=bool(row.get("cancellation_requested")),
            progress_percent=percent,
            revision=int(row["revision"]),
        )

    def run_job(self, job_id: str) -> ExportJobView:
        self._require_opaque(job_id, "job ID")
        try:
            row = self._repository.get_export_job(job_id)
        except ValueError:
            raise JobNotFound("export job was not found") from None
        if row["state"] != ExportJobState.PENDING_AUTHORIZATION.value:
            return self._view(row)
        if not self._transition(row, ExportJobState.CONNECTING):
            return self._current_view(job_id)
        row = self._repository.get_export_job(job_id)
        scope = self._scope_from_row(row)

        try:
            binding = self._source_binding_factory(
                scope, session_digest=str(row["session_digest"])
            )
            self._validate_binding(scope, binding)
        except SourceAdapterError:
            return self._fail(row, "transport_failed")
        except Exception:
            return self._fail(row, "source_binding_changed")

        with self._active_lock:
            self._active_adapters[job_id] = binding.adapter
        try:
            if not self._transition(row, ExportJobState.READING):
                return self._current_view(job_id)
            row = self._repository.get_export_job(job_id)
            messages: list[StoredMessage] = []
            evidence: dict[str, CompletenessEvidence] = {}
            fetched_counts: list[int | None] = []
            duplicate_counts: list[int | None] = []
            unknown_counts: list[int | None] = []
            parse_counts: list[int | None] = []
            seen_payloads: dict[str, StoredMessage] = {}
            for completed, conversation in enumerate(scope.conversations, start=1):
                page = self._fetch_range_with_retry(
                    binding.adapter,
                    conversation.conversation_id,
                    _to_ms(scope.range_start),
                    _to_ms(scope.range_end),
                )
                normalized, rejected_identity = self._normalize_page(
                    scope=scope,
                    conversation_id=conversation.conversation_id,
                    page=page,
                )
                for message in normalized:
                    previous = seen_payloads.get(message.message_id)
                    if previous is not None and previous != message:
                        raise UpstreamProtocolError("stable message identity conflict")
                    if previous is None:
                        seen_payloads[message.message_id] = message
                        messages.append(message)
                evidence[conversation.conversation_id] = page.completeness
                fetched_counts.append(page.completeness.fetched_count)
                duplicate_counts.append(page.completeness.duplicate_count)
                unknown_counts.append(page.completeness.unknown_time_count)
                parse_count = page.completeness.parse_error_count
                parse_counts.append(
                    None if parse_count is None else parse_count + rejected_identity
                )
                # Persist bounded progress. A cancellation changes the revision while
                # fetch_range is active, so the same CAS also fences stale readers.
                current = self._repository.get_export_job(job_id)
                if (
                    current["state"] != ExportJobState.READING.value
                    or current["revision"] != row["revision"]
                ):
                    return self._view(current)
                if not self._transition(
                    current,
                    ExportJobState.READING,
                    completed_count=completed,
                    fetched_count=_nullable_sum(fetched_counts),
                    saved_count=len(messages),
                    duplicate_count=_nullable_sum(duplicate_counts),
                    unknown_time_count=_nullable_sum(unknown_counts),
                    parse_error_count=_nullable_sum(parse_counts),
                ):
                    return self._current_view(job_id)
                row = self._repository.get_export_job(job_id)

            if not self._transition(
                row,
                ExportJobState.SAVING,
                completed_count=len(scope.conversations),
                fetched_count=_nullable_sum(fetched_counts),
                saved_count=len(messages),
                duplicate_count=_nullable_sum(duplicate_counts),
                unknown_time_count=_nullable_sum(unknown_counts),
                parse_error_count=_nullable_sum(parse_counts),
            ):
                return self._current_view(job_id)
        except FetchCancelled:
            return self._fail_current(job_id, "cancelled", cancelled=True)
        except PaginationError:
            return self._fail_current(job_id, "pagination_failed")
        except UpstreamProtocolError:
            return self._fail_current(job_id, "protocol_failed")
        except SourceAdapterError:
            return self._fail_current(job_id, "transport_failed")
        except AuthorizationError:
            return self._fail_current(job_id, "source_binding_changed")
        except Exception:
            return self._fail_current(job_id, "source_read_failed")
        finally:
            with self._active_lock:
                self._active_adapters.pop(job_id, None)

        return self._commit_artifacts(
            job_id=job_id,
            scope=scope,
            binding=binding,
            messages=tuple(messages),
            evidence=evidence,
        )

    def _fetch_range_with_retry(
        self,
        adapter: SourceAdapter,
        conversation_id: str,
        start_ms: int,
        end_ms: int,
    ) -> FetchPage:
        """Retry only explicitly classified transient, body-free source failures."""
        for attempt in range(self._max_source_retries + 1):
            try:
                return adapter.fetch_range(conversation_id, start_ms, end_ms)
            except (FetchCancelled, PaginationError, UpstreamProtocolError):
                raise
            except SourceAdapterError as error:
                try:
                    retryable = bool(self._source_retry_classifier(error))
                except Exception:
                    retryable = False
                if not retryable or attempt >= self._max_source_retries:
                    raise
                self._retry_wait(0.25 * (2**attempt))
        raise AssertionError("bounded source retry loop exhausted")  # pragma: no cover

    def _commit_artifacts(
        self,
        *,
        job_id: str,
        scope: ExportScope,
        binding: SourceBinding,
        messages: tuple[StoredMessage, ...],
        evidence: dict[str, CompletenessEvidence],
    ) -> ExportJobView:
        """Claim all irreversible local side effects and recheck durable state."""
        claim = self._claim_side_effect(job_id)
        try:
            try:
                row = self._repository.get_export_job(job_id)
            except ValueError:
                raise JobNotFound("export job was deleted") from None
            if row["state"] != ExportJobState.SAVING.value:
                return self._view(row)
            cancelled = self._cancel_before_action(row)
            if cancelled is not None:
                return cancelled
            try:
                artifacts = self._artifact_writer_factory(scope).write_batch(
                    batch_id=str(row["planned_batch_id"]),
                    snapshot_id=str(row["planned_snapshot_id"]),
                    scope=scope,
                    messages=messages,
                    conversation_evidence=evidence,
                    source_type=binding.source_type,
                    created_at=self._now(),
                )
            except Exception:
                return self._fail_current(job_id, "artifact_write_failed")
            row = self._repository.get_export_job(job_id)
            interrupted = self._advance_or_cancel(row, ExportJobState.VERIFYING)
            if interrupted is not None:
                return interrupted
            row = self._repository.get_export_job(job_id)
            cancelled = self._cancel_before_action(row)
            if cancelled is not None:
                return cancelled
            try:
                snapshot = self._importer_factory(scope).import_verified(
                    batch_id=artifacts.manifest.export_batch_id,
                    expected_scope=scope,
                )
            except Exception:
                return self._fail_current(job_id, "import_failed")

            manifest_json = json.dumps(
                artifacts.manifest.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            manifest_sha256 = sha256(manifest_json.encode("utf-8")).hexdigest()
            return self._finish_import(
                row,
                snapshot=snapshot,
                manifest_sha256=manifest_sha256,
                completeness=artifacts.manifest.completeness,
            )
        finally:
            claim.release()

    def _finish_import(
        self,
        row: dict[str, object],
        *,
        snapshot: SnapshotRef,
        manifest_sha256: str,
        completeness: Literal[
            "source_range_verified", "partial", "unknown", "empty"
        ],
    ) -> ExportJobView:
        """Bind an immutable import to its job and continue without another fetch."""
        job_id = str(row["job_id"])
        common = {
            "batch_id": snapshot.batch_id,
            "snapshot_id": snapshot.snapshot_id,
            "manifest_sha256": manifest_sha256,
            "completeness": completeness,
        }
        if completeness in {"partial", "unknown"}:
            interrupted = self._advance_or_cancel(
                self._repository.get_export_job(job_id),
                ExportJobState.PARTIAL,
                searchable=0,
                **common,
            )
            return interrupted or self._current_view(job_id)
        if completeness == "empty":
            interrupted = self._advance_or_cancel(
                self._repository.get_export_job(job_id),
                ExportJobState.COMPLETE,
                searchable=0,
                **common,
            )
            return interrupted or self._current_view(job_id)
        interrupted = self._advance_or_cancel(
            self._repository.get_export_job(job_id),
            ExportJobState.INDEXING,
            searchable=0,
            **common,
        )
        if interrupted is not None:
            return interrupted
        return self._run_index(self._repository.get_export_job(job_id))

    def _validate_binding(self, scope: ExportScope, binding: SourceBinding) -> None:
        if (
            binding.source_id != scope.source_id
            or binding.account_id != scope.account_id
            or binding.source_account_namespace != scope.source_account_namespace
            or binding.client_version != scope.client_version
            or binding.tool_version != scope.tool_version
            or binding.account_version != scope.account_version
            or binding.configuration_nonce != scope.configuration_nonce
        ):
            raise AuthorizationError("source binding changed")
        if scope.account_id not in binding.adapter.list_accounts():
            raise AuthorizationError("selected account changed")
        available = {
            (item.source_id, item.account_id, item.conversation_id)
            for item in binding.adapter.list_conversations()
        }
        expected = {
            (scope.source_id, scope.account_id, item.conversation_id)
            for item in scope.conversations
        }
        if not expected <= available:
            raise AuthorizationError("selected conversation changed")

    @staticmethod
    def _normalize_page(
        *, scope: ExportScope, conversation_id: str, page: FetchPage
    ) -> tuple[tuple[StoredMessage, ...], int]:
        evidence = page.completeness
        if page.next_cursor is not None:
            raise PaginationError("adapter returned unfinished pagination")
        start_ms = _to_ms(scope.range_start)
        end_ms = _to_ms(scope.range_end)
        if (
            evidence.requested_start_ms is not None
            and evidence.requested_start_ms != start_ms
        ) or (
            evidence.requested_end_ms is not None
            and evidence.requested_end_ms != end_ms
        ):
            raise UpstreamProtocolError("adapter evidence range mismatch")
        if evidence.accepted_count is not None and evidence.accepted_count != len(page.messages):
            raise UpstreamProtocolError("adapter accepted count mismatch")
        if evidence.status == "empty" and page.messages:
            raise UpstreamProtocolError("adapter empty evidence mismatch")
        if evidence.status != "empty" and not page.messages:
            raise UpstreamProtocolError("adapter nonempty evidence mismatch")

        actual_unknown = sum(item.sent_at_ms is None for item in page.messages)
        actual_missing = sum(item.message_id is None for item in page.messages)
        if (
            evidence.unknown_time_count is not None
            and evidence.unknown_time_count < actual_unknown
        ) or (
            evidence.missing_id_count is not None
            and evidence.missing_id_count < actual_missing
        ):
            raise UpstreamProtocolError("adapter error counters are inconsistent")

        conversation = next(
            item for item in scope.conversations if item.conversation_id == conversation_id
        )
        normalized: list[StoredMessage] = []
        rejected_identity = 0
        for source in page.messages:
            if source.conversation_id != conversation_id:
                raise UpstreamProtocolError("adapter returned another conversation")
            if source.sent_at_ms is None:
                continue
            if source.sent_at_ms < start_ms or source.sent_at_ms >= end_ms:
                raise UpstreamProtocolError("adapter returned an out-of-range message")
            if source.message_id is None:
                rejected_identity += 1
                continue
            value = MessageInput(
                workspace_id=None,
                platform=source_platform(scope.source_id),
                source_id=scope.source_id,
                account_id=scope.account_id,
                source_account_namespace=scope.source_account_namespace,
                conversation_id=conversation_id,
                conversation_name=conversation.display_name,
                conversation_type=conversation.conversation_type,
                source_message_id=source.message_id,
                source_file_id=None,
                page_slice_id=None,
                source_order=source.source_order,
                sent_at_utc=datetime.fromtimestamp(
                    source.sent_at_ms / 1000, tz=timezone.utc
                ),
                original_timestamp=None,
                source_timezone="UTC",
                sender_id=None,
                sender_name=None,
                message_type="text" if source.text is not None else None,
                text=source.text,
                reply_to_message_id=None,
                export_batch_id=None,
                attachments=None,
            )
            normalized.append(StoredMessage.from_input(value))
        known = tuple(item.sent_at_ms for item in page.messages if item.sent_at_ms is not None)
        if evidence.observed_start_ms is not None and evidence.observed_start_ms != min(known):
            raise UpstreamProtocolError("adapter observed range mismatch")
        if evidence.observed_end_ms is not None and evidence.observed_end_ms != max(known):
            raise UpstreamProtocolError("adapter observed range mismatch")
        return tuple(normalized), rejected_identity

    def _transition(
        self,
        row: dict[str, object],
        state: ExportJobState,
        **changes: object,
    ) -> bool:
        return self._repository.cas_export_job(
            str(row["job_id"]),
            expected_state=str(row["state"]),
            expected_revision=int(row["revision"]),
            new_state=state.value,
            updated_at_utc=self._now().isoformat(),
            error_code=None,
            **changes,
        )

    def _side_effect_lock(self, job_id: str) -> Lock:
        with self._active_lock:
            lock = self._side_effect_locks.get(job_id)
            if lock is None:
                lock = Lock()
                self._side_effect_locks[job_id] = lock
            return lock

    def _try_claim_side_effect(
        self, job_id: str, *, blocking: bool
    ) -> _SideEffectClaim | None:
        thread_lock = self._side_effect_lock(job_id)
        if not thread_lock.acquire(blocking=blocking):
            return None
        try:
            descriptor = self._repository.acquire_export_side_effect_lock(
                job_id,
                blocking=blocking,
            )
        except BaseException:
            thread_lock.release()
            raise
        if descriptor is None:
            thread_lock.release()
            return None
        return _SideEffectClaim(self._repository, thread_lock, descriptor)

    def _claim_side_effect(self, job_id: str) -> _SideEffectClaim:
        claim = self._try_claim_side_effect(job_id, blocking=True)
        if claim is None:  # pragma: no cover - blocking acquisition cannot return None
            raise JobConflict("export commit lock is unavailable")
        return claim

    def _claim_mutation(self, job_id: str) -> _SideEffectClaim:
        claim = self._try_claim_side_effect(job_id, blocking=False)
        if claim is None:
            raise JobConflict("export commit is already in progress")
        return claim

    def _cancel_before_action(
        self, row: dict[str, object], **residue: object
    ) -> ExportJobView | None:
        """Honor a durable request at an action boundary while holding its fence."""
        job_id = str(row["job_id"])
        expected_state = str(row["state"])
        cancellation_changes = dict(residue)
        cancellation_changes["searchable"] = 0
        for _attempt in range(16):
            if str(row["state"]) != expected_state:
                return self._view(row)
            if not bool(row.get("cancellation_requested")):
                return None
            if self._repository.cas_export_job(
                job_id,
                expected_state=expected_state,
                expected_revision=int(row["revision"]),
                new_state=ExportJobState.CANCELLED.value,
                updated_at_utc=self._now().isoformat(),
                error_code="cancelled",
                cancellation_requested=1,
                **cancellation_changes,
            ):
                return self._current_view(job_id)
            row = self._repository.get_export_job(job_id)
        raise JobConflict("export job changed repeatedly during cancellation")

    def _advance_or_cancel(
        self,
        row: dict[str, object],
        state: ExportJobState,
        **changes: object,
    ) -> ExportJobView | None:
        """Atomically choose cancellation or the next stage at one boundary."""
        job_id = str(row["job_id"])
        expected_state = str(row["state"])
        for _attempt in range(16):
            if str(row["state"]) != expected_state:
                return self._view(row)
            cancelled = self._cancel_before_action(row, **changes)
            if cancelled is not None:
                return cancelled
            if self._transition(row, state, **changes):
                return None
            row = self._repository.get_export_job(job_id)
        raise JobConflict("export job changed repeatedly at a stage boundary")

    def _current_view(self, job_id: str) -> ExportJobView:
        try:
            return self._view(self._repository.get_export_job(job_id))
        except ValueError:
            raise JobNotFound("export job was deleted") from None

    def _fail(
        self,
        row: dict[str, object],
        error_code: str,
        *,
        cancelled: bool = False,
    ) -> ExportJobView:
        state = ExportJobState.CANCELLED if cancelled else ExportJobState.FAILED
        job_id = str(row["job_id"])
        for _attempt in range(16):
            if row["state"] in {
                ExportJobState.COMPLETE.value,
                ExportJobState.PARTIAL.value,
                ExportJobState.FAILED.value,
                ExportJobState.CANCELLED.value,
            }:
                return self._view(row)
            if self._repository.cas_export_job(
                job_id,
                expected_state=str(row["state"]),
                expected_revision=int(row["revision"]),
                new_state=state.value,
                updated_at_utc=self._now().isoformat(),
                error_code=error_code,
                cancellation_requested=(
                    1 if cancelled else row.get("cancellation_requested", 0)
                ),
                searchable=0,
            ):
                return self._current_view(job_id)
            row = self._repository.get_export_job(job_id)
        raise JobConflict("export job changed repeatedly while recording failure")

    def _fail_current(
        self, job_id: str, error_code: str, *, cancelled: bool = False
    ) -> ExportJobView:
        try:
            row = self._repository.get_export_job(job_id)
        except ValueError:
            raise JobNotFound("export job was deleted") from None
        if row["state"] == ExportJobState.CANCELLED.value:
            return self._view(row)
        return self._fail(row, error_code, cancelled=cancelled)

    def _run_index(self, row: dict[str, object]) -> ExportJobView:
        job_id = str(row["job_id"])
        row = self._repository.get_export_job(job_id)
        if row["state"] != ExportJobState.INDEXING.value:
            return self._view(row)
        cancelled = self._cancel_before_action(row)
        if cancelled is not None:
            return cancelled
        snapshot_id = row.get("snapshot_id")
        batch_id = row.get("batch_id")
        if not isinstance(snapshot_id, str) or not isinstance(batch_id, str):
            return self._fail_current(job_id, "index_snapshot_missing")
        try:
            self._indexer(SnapshotRef(snapshot_id=snapshot_id, batch_id=batch_id))
        except Exception:
            return self._fail_current(job_id, "index_failed")
        for _attempt in range(16):
            row = self._repository.get_export_job(job_id)
            if row["state"] != ExportJobState.INDEXING.value:
                return self._view(row)
            if self._transition(row, ExportJobState.COMPLETE, searchable=1):
                return self._current_view(job_id)
        raise JobConflict("export job changed repeatedly after indexing")

    def cancel_job(self, job_id: str, *, session_id: str) -> ExportJobView:
        row = self._job_for_session(job_id, session_id=session_id)
        commit_lock = self._try_claim_side_effect(job_id, blocking=False)
        if commit_lock is None:
            return self._request_cancellation(row, session_id=session_id)
        try:
            with self._active_lock:
                adapter = self._active_adapters.get(job_id)
            if adapter is not None:
                adapter.cancel()
            for _attempt in range(16):
                if row["state"] in {
                    ExportJobState.COMPLETE.value,
                    ExportJobState.FAILED.value,
                    ExportJobState.CANCELLED.value,
                }:
                    return self._view(row)
                if self._repository.cas_export_job(
                    job_id,
                    expected_state=str(row["state"]),
                    expected_revision=int(row["revision"]),
                    new_state=ExportJobState.CANCELLED.value,
                    updated_at_utc=self._now().isoformat(),
                    error_code="cancelled",
                    cancellation_requested=1,
                    searchable=0,
                ):
                    return self._current_view(job_id)
                row = self._job_for_session(job_id, session_id=session_id)
            raise JobConflict("export job changed repeatedly during cancellation")
        finally:
            commit_lock.release()

    def _request_cancellation(
        self, row: dict[str, object], *, session_id: str
    ) -> ExportJobView:
        """Record cancellation while a started local action owns the fence."""
        job_id = str(row["job_id"])
        for _attempt in range(16):
            if row["state"] in {
                ExportJobState.COMPLETE.value,
                ExportJobState.CANCELLED.value,
            }:
                return self._view(row)
            if bool(row.get("cancellation_requested")):
                return self._view(row)
            if self._repository.cas_export_job(
                job_id,
                expected_state=str(row["state"]),
                expected_revision=int(row["revision"]),
                new_state=str(row["state"]),
                updated_at_utc=self._now().isoformat(),
                cancellation_requested=1,
            ):
                return self._current_view(job_id)
            row = self._job_for_session(job_id, session_id=session_id)
        raise JobConflict("export job changed repeatedly during cancellation request")

    def delete_job(self, job_id: str, *, session_id: str) -> None:
        self._job_for_session(job_id, session_id=session_id)
        commit_lock = self._claim_mutation(job_id)
        try:
            with self._active_lock:
                adapter = self._active_adapters.get(job_id)
            if adapter is not None:
                adapter.cancel()
            if not self._repository.delete_export_job(
                job_id, session_digest=self._session_digest(session_id)
            ):
                raise JobNotFound("export job was not found")
        finally:
            commit_lock.release()

    def accept_limitations(
        self,
        job_id: str,
        *,
        session_id: str,
        manifest_sha256: str,
    ) -> ExportJobView:
        self._require_opaque(job_id, "job ID")
        if not re.fullmatch(r"[0-9a-f]{64}", manifest_sha256):
            raise ValueError("a canonical manifest hash is required")
        self._job_for_session(job_id, session_id=session_id)
        commit_lock = self._claim_mutation(job_id)
        try:
            try:
                row = self._repository.accept_limitations_and_begin_index(
                    job_id=job_id,
                    session_digest=self._session_digest(session_id),
                    manifest_sha256=manifest_sha256,
                    accepted_at_utc=self._now().isoformat(),
                )
            except PermissionError as error:
                raise AuthorizationError(str(error)) from None
            except ValueError as error:
                if "does not exist" in str(error):
                    raise JobNotFound("export job was not found") from None
                raise JobConflict(str(error)) from None
            return self._run_index(row)
        finally:
            commit_lock.release()

    def retry_index(self, job_id: str, *, session_id: str) -> ExportJobView:
        row = self._job_for_session(job_id, session_id=session_id)
        commit_lock = self._claim_mutation(job_id)
        try:
            row = self._job_for_session(job_id, session_id=session_id)
            if row["state"] != ExportJobState.FAILED.value or row["error_code"] not in {
                "index_failed",
                "index_interrupted",
            }:
                raise JobConflict("only an index failure can retry without fetching")
            interrupted = self._advance_or_cancel(
                row, ExportJobState.INDEXING, searchable=0
            )
            if interrupted is not None:
                return interrupted
            return self._run_index(self._repository.get_export_job(job_id))
        finally:
            commit_lock.release()

    def retry_import(self, job_id: str, *, session_id: str) -> ExportJobView:
        """Retry verification/import of the already committed local artifact only."""
        row = self._job_for_session(job_id, session_id=session_id)
        commit_lock = self._claim_mutation(job_id)
        try:
            row = self._job_for_session(job_id, session_id=session_id)
            if row["state"] != ExportJobState.FAILED.value or row["error_code"] not in {
                "import_failed",
                "import_interrupted",
            }:
                raise JobConflict("only a saved artifact import can retry without fetching")
            scope = self._scope_from_row(row)
            interrupted = self._advance_or_cancel(
                row, ExportJobState.VERIFYING, searchable=0
            )
            if interrupted is not None:
                return interrupted
            row = self._repository.get_export_job(job_id)
            cancelled = self._cancel_before_action(row)
            if cancelled is not None:
                return cancelled
            try:
                snapshot = self._importer_factory(scope).import_verified(
                    batch_id=str(row["planned_batch_id"]),
                    expected_scope=scope,
                )
                batch = self._repository.export_batch_evidence(snapshot.batch_id)
                manifest_sha256 = batch.get("manifest_sha256")
                completeness = batch.get("completeness")
                if not isinstance(manifest_sha256, str) or not re.fullmatch(
                    r"[0-9a-f]{64}", manifest_sha256
                ):
                    raise ValueError("imported batch has no canonical manifest hash")
                if completeness not in {
                    "source_range_verified",
                    "partial",
                    "unknown",
                    "empty",
                }:
                    raise ValueError("imported batch has invalid completeness")
            except Exception:
                return self._fail(row, "import_failed")
            return self._finish_import(
                row,
                snapshot=snapshot,
                manifest_sha256=manifest_sha256,
                completeness=completeness,
            )
        finally:
            commit_lock.release()

    def reconcile_interrupted(self) -> int:
        reconciled = 0
        for row in self._repository.list_export_jobs_in_states(_TRANSIENT_STATES):
            job_id = str(row["job_id"])
            commit_lock = self._try_claim_side_effect(job_id, blocking=False)
            if commit_lock is None:
                continue
            try:
                for _attempt in range(16):
                    try:
                        row = self._repository.get_export_job(job_id)
                    except ValueError:
                        break
                    if row["state"] not in _TRANSIENT_STATES:
                        break
                    cancelled = self._cancel_before_action(row)
                    if cancelled is not None:
                        if cancelled.state is ExportJobState.CANCELLED:
                            reconciled += 1
                        break
                    if (
                        row["state"] == ExportJobState.INDEXING.value
                        and row.get("snapshot_id")
                    ):
                        error_code = "index_interrupted"
                    elif row["state"] in {
                        ExportJobState.SAVING.value,
                        ExportJobState.VERIFYING.value,
                    }:
                        error_code = "import_interrupted"
                    else:
                        error_code = "interrupted_before_commit"
                    if self._repository.cas_export_job(
                        job_id,
                        expected_state=str(row["state"]),
                        expected_revision=int(row["revision"]),
                        new_state=ExportJobState.FAILED.value,
                        updated_at_utc=self._now().isoformat(),
                        error_code=error_code,
                        searchable=0,
                    ):
                        reconciled += 1
                        break
            finally:
                commit_lock.release()
        return reconciled

    def get_batch(self, batch_id: str | None, *, session_id: str) -> dict[str, object]:
        if not isinstance(batch_id, str):
            raise JobNotFound("export batch was not found")
        self._require_opaque(batch_id, "batch ID")
        try:
            row = self._repository.export_batch_for_session(
                batch_id,
                session_digest=self._session_digest(session_id),
            )
        except ValueError:
            raise JobNotFound("export batch was not found") from None
        completeness = row.get("completeness")
        searchable = bool(row.get("searchable"))
        return {
            "batch_id": str(row["batch_id"]),
            "snapshot_id": row.get("snapshot_id"),
            "manifest_sha256": row.get("manifest_sha256"),
            "completeness": completeness,
            "limitations_accepted_at_utc": row.get("limitations_accepted_at_utc"),
            "limitations_accepted_manifest_sha256": row.get(
                "limitations_accepted_manifest_sha256"
            ),
            "searchable": searchable,
            "generation_eligible": searchable and completeness != "empty",
        }


def _to_ms(value: datetime) -> int:
    return int(value.astimezone(timezone.utc).timestamp() * 1000)


def _nullable_sum(values: list[int | None]) -> int | None:
    if any(value is None for value in values):
        return None
    return sum(value for value in values if value is not None)


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) else None


def source_platform(source_id: str) -> str | None:
    if source_id in {"qq", "wechat"}:
        return source_id
    return "synthetic" if source_id.startswith("synthetic") else None
