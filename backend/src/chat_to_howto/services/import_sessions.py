"""Short-lived preview and atomic commit flow for user-supplied chat records."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import hmac
import json
from threading import RLock, Timer
from typing import Callable
import weakref

from chat_to_howto.api.session import digest, opaque
from chat_to_howto.domain.imports import (
    ImportAnalysis,
    ImportCommitView,
    ImportConversationHandle,
    ImportOptions,
    ImportOrigin,
    ImportPlatformHint,
    ImportPreviewView,
    ImportRecoveryPage,
    ManualImportCommit,
    ManualImportSelection,
)
from chat_to_howto.domain.jobs import ExportConversation, ExportScope
from chat_to_howto.domain.manifest import (
    ArtifactDigest,
    BatchManifest,
    ConversationEvidenceManifest,
    ConversationManifest,
)
from chat_to_howto.repositories.chat import ChatRepository
from chat_to_howto.services.chat_imports import (
    AnalyzedImport,
    ChatImportAnalyzer,
    aggregate_conversation_metadata,
)
from chat_to_howto.services.manual_import import BackupParser


class ImportAuthorizationError(PermissionError):
    pass


class ImportNotReady(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class _PendingImport:
    session_digest: str
    filename: str
    origin: ImportOrigin
    platform_hint: ImportPlatformHint
    options: ImportOptions
    payload: bytes
    analysis_commitment: str
    parsed_count: int
    unresolved_count: int
    preview_fingerprint: str
    expires_at_utc: datetime
    source_byte_size: int


def _analyzed_commitment(analyzed: AnalyzedImport) -> str:
    """Hash normalized output incrementally without retaining another message graph."""
    hasher = sha256()

    def update(value: str) -> None:
        encoded = value.encode("utf-8")
        hasher.update(len(encoded).to_bytes(8, "big"))
        hasher.update(encoded)

    update(analyzed.fingerprint)
    update(json.dumps(analyzed.mapping, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    update(analyzed.view.model_dump_json())
    for message in analyzed.messages:
        update(message.model_dump_json())
    return hasher.hexdigest()


def _expire_pending_preview(
    service_ref: "weakref.ReferenceType[ChatImportService]",
    preview_id: str,
    expires_at_utc: datetime,
) -> None:
    service = service_ref()
    if service is not None:
        service._expire_preview(preview_id, expires_at_utc=expires_at_utc)


class ChatImportService:
    def __init__(
        self,
        *,
        repository: ChatRepository,
        clock: Callable[[], datetime],
        preview_ttl_seconds: int = 300,
        selection_ttl_seconds: int = 1800,
        max_pending_previews: int = 8,
        max_pending_source_bytes: int = 128 * 1024 * 1024,
        analyzer: ChatImportAnalyzer | None = None,
    ) -> None:
        if (
            preview_ttl_seconds < 1
            or selection_ttl_seconds < 1
            or max_pending_previews < 1
            or max_pending_source_bytes < 1
        ):
            raise ValueError("import limits must be positive")
        self._repository = repository
        self._clock = clock
        self._preview_ttl = timedelta(seconds=preview_ttl_seconds)
        self._selection_ttl = timedelta(seconds=selection_ttl_seconds)
        self._max_pending_previews = max_pending_previews
        self._max_pending_source_bytes = max_pending_source_bytes
        self._analyzer = analyzer or ChatImportAnalyzer()
        self._parser = BackupParser()
        self._pending: dict[str, _PendingImport] = {}
        self._expiry_timers: dict[str, Timer] = {}
        self._lock = RLock()

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise RuntimeError("import clock must be timezone-aware")
        return value.astimezone(timezone.utc)

    def _discard_expired(self, now: datetime) -> None:
        for preview_id, pending in tuple(self._pending.items()):
            if pending.expires_at_utc <= now:
                del self._pending[preview_id]
                timer = self._expiry_timers.pop(preview_id, None)
                if timer is not None:
                    timer.cancel()

    def _ensure_capacity(
        self, source_byte_size: int, *, replacing_session_digest: str | None = None
    ) -> None:
        retained = tuple(
            item for item in self._pending.values()
            if item.session_digest != replacing_session_digest
        )
        if (
            len(retained) >= self._max_pending_previews
            or sum(item.source_byte_size for item in retained)
            + source_byte_size
            > self._max_pending_source_bytes
        ):
            raise ImportNotReady("manual import preview capacity is full")

    def _discard_session_previews(self, session_digest: str) -> None:
        for preview_id, pending in tuple(self._pending.items()):
            if pending.session_digest == session_digest:
                del self._pending[preview_id]
                timer = self._expiry_timers.pop(preview_id, None)
                if timer is not None:
                    timer.cancel()

    def _expire_preview(self, preview_id: str, *, expires_at_utc: datetime) -> None:
        with self._lock:
            pending = self._pending.get(preview_id)
            if pending is not None and pending.expires_at_utc == expires_at_utc:
                del self._pending[preview_id]
            self._expiry_timers.pop(preview_id, None)

    def close(self) -> None:
        with self._lock:
            for timer in self._expiry_timers.values():
                timer.cancel()
            self._expiry_timers.clear()
            self._pending.clear()

    def create_preview(
        self,
        *,
        filename: str,
        payload: bytes,
        origin: ImportOrigin,
        platform_hint: ImportPlatformHint,
        options: ImportOptions,
        session_id: str,
    ) -> ImportPreviewView:
        if not session_id:
            raise ImportAuthorizationError("local session is unavailable")
        now = self._now()
        session_digest = digest(session_id)
        with self._lock:
            self._discard_expired(now)
            self._ensure_capacity(
                len(payload), replacing_session_digest=session_digest
            )
        source = self._parser.preview(filename=filename, payload=payload)
        analyzed = self._analyzer.analyze(
            filename=filename,
            payload=payload,
            origin=origin,
            platform_hint=platform_hint,
            options=options,
        )
        canonical = json.dumps(
            {
                "v": 1,
                "source_fingerprint": analyzed.fingerprint,
                "origin": origin,
                "platform_hint": platform_hint,
                "options": options.model_dump(mode="json"),
                "mapping": analyzed.mapping,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        preview_fingerprint = sha256(canonical.encode()).hexdigest()
        preview_id = f"import-preview:{opaque()}"
        now = self._now()
        expires = now + self._preview_ttl
        with self._lock:
            self._discard_expired(now)
            self._ensure_capacity(
                len(payload), replacing_session_digest=session_digest
            )
            self._discard_session_previews(session_digest)
            self._pending[preview_id] = _PendingImport(
                session_digest=session_digest,
                filename=filename,
                origin=origin,
                platform_hint=platform_hint,
                options=options,
                payload=payload,
                analysis_commitment=_analyzed_commitment(analyzed),
                parsed_count=analyzed.view.parsed_count,
                unresolved_count=analyzed.view.unresolved_count,
                preview_fingerprint=preview_fingerprint,
                expires_at_utc=expires,
                source_byte_size=len(payload),
            )
            timer = Timer(
                self._preview_ttl.total_seconds(),
                _expire_pending_preview,
                args=(weakref.ref(self), preview_id, expires),
            )
            timer.daemon = True
            self._expiry_timers[preview_id] = timer
            timer.start()
        return ImportPreviewView(
            preview_id=preview_id,
            preview_fingerprint=preview_fingerprint,
            expires_at_utc=expires,
            source=source,
            applied_mapping=analyzed.mapping,
            analysis=analyzed.view,
            ready_to_commit=(
                analyzed.view.parsed_count > 0
                and analyzed.view.unresolved_count == 0
            ),
        )

    def commit_preview(
        self,
        preview_id: str,
        *,
        preview_fingerprint: str,
        confirmed: bool,
        session_id: str,
    ) -> ImportCommitView:
        if not confirmed or not session_id:
            raise ImportAuthorizationError("manual import confirmation is required")
        now = self._now()
        with self._lock:
            pending = self._pending.get(preview_id)
            if (
                pending is None
                or pending.session_digest != digest(session_id)
                or not hmac.compare_digest(
                    pending.preview_fingerprint, preview_fingerprint
                )
                or pending.expires_at_utc <= now
            ):
                self._discard_expired(now)
                raise ImportAuthorizationError("manual import preview is unavailable")
            if (
                pending.parsed_count == 0
                or pending.unresolved_count != 0
            ):
                raise ImportNotReady("manual import has unresolved rows")
            analyzed = self._analyzer.analyze(
                filename=pending.filename,
                payload=pending.payload,
                origin=pending.origin,
                platform_hint=pending.platform_hint,
                options=pending.options,
            )
            if not hmac.compare_digest(
                _analyzed_commitment(analyzed), pending.analysis_commitment
            ):
                raise ImportAuthorizationError("manual import preview changed")
            result = self._commit_pending(pending, analyzed=analyzed, now=now)
            del self._pending[preview_id]
            timer = self._expiry_timers.pop(preview_id, None)
            if timer is not None:
                timer.cancel()
            return result

    def recover_committed(
        self, *, session_id: str, cursor: str | None = None, limit: int = 20
    ) -> ImportRecoveryPage:
        if not session_id:
            raise ImportAuthorizationError("local session is unavailable")
        now = self._now()
        records, next_cursor = self._repository.recover_manual_imports(
            session_digest=digest(session_id),
            expires_at_utc=(now + self._selection_ttl).isoformat(),
            cursor=cursor,
            limit=limit,
        )
        imports = []
        for record in records:
            scope = record["scope"]
            analysis = record["analysis"]
            handles = record["handles"]
            if not isinstance(scope, ExportScope) or not isinstance(analysis, ImportAnalysis):
                raise RuntimeError("stored manual import is invalid")
            imports.append(ImportCommitView(
                import_id=str(record["import_id"]),
                batch_id=str(record["batch_id"]),
                snapshot_id=str(record["snapshot_id"]),
                account_id=scope.account_id,
                range_start=scope.range_start,
                range_end=scope.range_end,
                display_timezone=scope.display_timezone,
                conversations=tuple(
                    ImportConversationHandle(
                        conversation_selection_id=str(handle),
                        display_name=conversation.display_name,
                        conversation_type=conversation.conversation_type,
                    )
                    for handle, conversation in zip(
                        handles, scope.conversations, strict=True
                    )
                ),
                warnings=analysis.warnings,
            ))
        return ImportRecoveryPage(imports=tuple(imports), next_cursor=next_cursor)

    def _commit_pending(
        self, pending: _PendingImport, *, analyzed: AnalyzedImport, now: datetime
    ) -> ImportCommitView:
        messages = analyzed.messages
        timestamps = tuple(message.sent_at_utc for message in messages if message.sent_at_utc)
        range_start = min(timestamps)
        # The browser scope editor has whole-second precision. Use an exclusive
        # boundary it can round-trip without dropping the latest imported row.
        range_end = max(timestamps) + timedelta(seconds=1)
        by_conversation, _metadata_conflict = aggregate_conversation_metadata(messages)
        conversations = tuple(
            ExportConversation(
                conversation_id=conversation_id,
                display_name=metadata[0],
                conversation_type=metadata[1],
            )
            for conversation_id, metadata in sorted(by_conversation.items())
        )
        account_id = messages[0].account_id
        scope = ExportScope(
            source_id="manual",
            account_id=account_id,
            conversations=conversations,
            range_start=range_start,
            range_end=range_end,
            display_timezone=pending.options.timezone,
            output_root_id="local-database",
            configuration_nonce=f"manual:{analyzed.fingerprint[:32]}",
        )
        import_id = f"import:{opaque()}"
        batch_id = f"batch:manual:{opaque()}"
        snapshot_id = f"snapshot:manual:{opaque()}"
        raw_handles = tuple(f"selection:{opaque()}" for _ in conversations)
        selection_expiry = now + self._selection_ttl
        manual_import = ManualImportCommit(
            import_id=import_id,
            session_digest=pending.session_digest,
            source_kind=pending.origin,
            platform_hint=pending.platform_hint,
            source_fingerprint=analyzed.fingerprint,
            filename=pending.filename,
            analysis=analyzed.view,
            selections=tuple(
                ManualImportSelection(
                    handle_digest=digest(handle),
                    conversation_id=conversation.conversation_id,
                )
                for handle, conversation in zip(raw_handles, conversations, strict=True)
            ),
            created_at_utc=now,
            expires_at_utc=selection_expiry,
        )
        manifest = _manual_manifest(
            scope=scope,
            messages=messages,
            analyzed=analyzed,
            batch_id=batch_id,
            snapshot_id=snapshot_id,
            created_at=now,
        )
        self._repository.import_snapshot_atomic(
            batch_id=batch_id,
            snapshot_id=snapshot_id,
            scope=scope,
            messages=messages,
            manifest=manifest,
            manual_import=manual_import,
        )
        return ImportCommitView(
            import_id=import_id,
            batch_id=batch_id,
            snapshot_id=snapshot_id,
            account_id=account_id,
            range_start=range_start,
            range_end=range_end,
            display_timezone=scope.display_timezone,
            conversations=tuple(
                ImportConversationHandle(
                    conversation_selection_id=handle,
                    display_name=conversation.display_name,
                    conversation_type=conversation.conversation_type,
                )
                for handle, conversation in zip(raw_handles, conversations, strict=True)
            ),
            warnings=analyzed.view.warnings,
        )


def _manual_manifest(
    *, scope: ExportScope, messages, analyzed: AnalyzedImport,
    batch_id: str, snapshot_id: str, created_at: datetime,
) -> BatchManifest:
    jsonl = b"".join(message.model_dump_json().encode() + b"\n" for message in messages)
    text = b"\n".join((message.text or "").encode() for message in messages)
    evidence = []
    for conversation in scope.conversations:
        count = sum(message.conversation_id == conversation.conversation_id for message in messages)
        evidence.append(ConversationEvidenceManifest(
            conversation_id=conversation.conversation_id,
            status="unknown",
            reason="manual_user_supplied_import",
            fetched_count=None,
            accepted_count=count,
            duplicate_count=None,
            unknown_time_count=0,
            parse_error_count=None,
            warnings=analyzed.view.warnings,
            termination="user_supplied_input_exhausted",
        ))
    return BatchManifest(
        export_batch_id=batch_id,
        snapshot_id=snapshot_id,
        artifact_set_id=f"artifact:manual:{analyzed.fingerprint}",
        scope_fingerprint=scope.fingerprint(),
        output_root_id=scope.output_root_id,
        created_at_utc=created_at,
        source_type="manual",
        source_id=scope.source_id,
        account_id=scope.account_id,
        conversations=tuple(ConversationManifest(
            conversation_id=item.conversation_id,
            display_name=item.display_name,
            conversation_type=item.conversation_type,
        ) for item in scope.conversations),
        requested_start_utc=scope.range_start,
        requested_end_utc=scope.range_end,
        observed_start_utc=scope.range_start,
        observed_end_utc=max(message.sent_at_utc for message in messages),
        display_timezone=scope.display_timezone,
        fetched_count=analyzed.view.total_count,
        saved_count=len(messages),
        duplicate_count=analyzed.view.duplicate_count,
        unknown_time_count=0,
        parse_error_count=analyzed.view.unresolved_count,
        earliest_saved_at_utc=min(message.sent_at_utc for message in messages),
        latest_saved_at_utc=max(message.sent_at_utc for message in messages),
        completeness="unknown",
        completeness_reason="manual_user_supplied_import",
        limitations=("source_completeness_not_verified",),
        warnings=analyzed.view.warnings,
        conversation_evidence=tuple(evidence),
        artifacts=(
            ArtifactDigest(relative_path="messages.jsonl", byte_size=len(jsonl), sha256=sha256(jsonl).hexdigest()),
            ArtifactDigest(relative_path="messages.txt", byte_size=len(text), sha256=sha256(text).hexdigest()),
        ),
    )
