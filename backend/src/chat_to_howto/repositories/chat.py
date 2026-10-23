"""Transactional local repository for normalized P1 messages, batches, and snapshots."""

from __future__ import annotations

from contextlib import contextmanager
from base64 import b64decode, b64encode
from binascii import Error as Base64Error
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
import secrets
import sqlite3
import stat
from threading import RLock
from typing import Iterator

from chat_to_howto.domain.jobs import ExportConversation, ExportScope
from chat_to_howto.domain.imports import ImportAnalysis, ManualImportCommit
from chat_to_howto.domain.manifest import BatchManifest
from chat_to_howto.domain.messages import AttachmentPlaceholder, MessageInput, StoredMessage
from chat_to_howto.migrations import apply_migrations, open_connection


class RawSourceExpired(PermissionError):
    """An imported source has reached its absolute seven-day deadline."""


@dataclass(frozen=True, slots=True)
class SnapshotRef:
    """A handle to a sealed copy of one batch's normalized message payloads."""

    snapshot_id: str
    batch_id: str


def _encode_manual_import_cursor(created_at_utc: str, import_id: str) -> str:
    return b64encode(
        f"{created_at_utc}\0{import_id}".encode("utf-8"), altchars=b"-_"
    ).decode("ascii").rstrip("=")


def _decode_manual_import_cursor(cursor: str) -> tuple[str, str]:
    try:
        padding = "=" * (-len(cursor) % 4)
        raw = b64decode(
            (cursor + padding).encode("ascii"), altchars=b"-_", validate=True
        ).decode("utf-8")
        created_at_utc, import_id = raw.split("\0", 1)
        created = datetime.fromisoformat(created_at_utc)
        if (
            created.tzinfo is None
            or created.utcoffset() is None
            or not import_id
            or len(import_id) > 256
            or any(ord(character) < 32 or ord(character) == 127 for character in import_id)
        ):
            raise ValueError
        return created_at_utc, import_id
    except (Base64Error, UnicodeError, ValueError):
        raise ValueError("manual import recovery cursor is invalid") from None


def _encode_document_cursor(updated_at_utc: str, document_id: str) -> str:
    return b64encode(
        f"{updated_at_utc}\0{document_id}".encode("utf-8"), altchars=b"-_"
    ).decode("ascii").rstrip("=")


def _decode_document_cursor(cursor: str) -> tuple[str, str]:
    try:
        padding = "=" * (-len(cursor) % 4)
        raw = b64decode(
            (cursor + padding).encode("ascii"), altchars=b"-_", validate=True
        ).decode("utf-8")
        updated_at_utc, document_id = raw.split("\0", 1)
        updated = datetime.fromisoformat(updated_at_utc)
        if (
            updated.tzinfo is None
            or updated.utcoffset() is None
            or not document_id
            or len(document_id) > 128
            or any(ord(character) < 32 or ord(character) == 127 for character in document_id)
        ):
            raise ValueError
        return updated_at_utc, document_id
    except (Base64Error, UnicodeError, ValueError):
        raise ValueError("document recovery cursor is invalid") from None


class ChatRepository:
    """P1 persistence isolated from P0 tables and external data sources.

    A snapshot stores a payload copy. A future deletion workflow must explicitly
    revoke/delete associated snapshots before base messages; there is no silent
    cascade that makes retained snapshot evidence disappear.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        apply_migrations(self.path)
        self._connection = open_connection(self.path)
        self._connection.row_factory = sqlite3.Row
        self._lock = RLock()

    def close(self) -> None:
        self._connection.close()

    def acquire_export_side_effect_lock(
        self, job_id: str, *, blocking: bool
    ) -> int | None:
        """Acquire a crash-released interprocess fence beside the private database."""
        lock_name = f"{self.path.name}.export.{sha256(job_id.encode()).hexdigest()}.lock"
        lock_path = self.path.with_name(lock_name)
        flags = os.O_RDWR | os.O_CREAT | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(lock_path, flags, 0o600)
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise ValueError("export lock must be a regular file")
            os.fchmod(descriptor, 0o600)
            operation = fcntl.LOCK_EX
            if not blocking:
                operation |= fcntl.LOCK_NB
            try:
                fcntl.flock(descriptor, operation)
            except BlockingIOError:
                os.close(descriptor)
                return None
            return descriptor
        except BaseException:
            try:
                os.close(descriptor)
            except OSError:
                pass
            raise

    @staticmethod
    def release_export_side_effect_lock(descriptor: int) -> None:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                yield
                self._connection.commit()
            except BaseException:
                self._connection.rollback()
                raise

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _ensure_account(self, source_id: str, account_id: str, namespace: str | None) -> None:
        self._connection.execute(
            "INSERT OR IGNORE INTO chat_sources(source_id) VALUES (?)", (source_id,)
        )
        self._connection.execute(
            """
            INSERT OR IGNORE INTO chat_accounts(source_id, account_id, source_account_namespace)
            VALUES (?, ?, ?)
            """,
            (source_id, account_id, namespace),
        )

    def _ensure_conversation(
        self,
        source_id: str,
        account_id: str,
        conversation_id: str,
        conversation_name: str | None,
        conversation_type: str | None,
    ) -> None:
        self._connection.execute(
            """
            INSERT OR IGNORE INTO chat_conversations(
                source_id, account_id, conversation_id, conversation_name, conversation_type
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (source_id, account_id, conversation_id, conversation_name, conversation_type),
        )

    @staticmethod
    def _payload(value: StoredMessage) -> str:
        return value.model_dump_json()

    def _store_message_in_transaction(self, stored: StoredMessage) -> StoredMessage:
        """Store one already-normalized message on the caller's transaction."""
        payload = self._payload(stored)
        self._ensure_account(
            stored.source_id,
            stored.account_id,
            stored.source_account_namespace,
        )
        self._ensure_conversation(
            stored.source_id,
            stored.account_id,
            stored.conversation_id,
            stored.conversation_name,
            stored.conversation_type,
        )
        existing = self._connection.execute(
            "SELECT payload_json FROM chat_messages WHERE message_id = ?",
            (stored.message_id,),
        ).fetchone()
        if existing is not None:
            persisted = StoredMessage.model_validate_json(existing["payload_json"])
            if persisted != stored:
                raise ValueError("message identity conflict")
            return persisted
        self._connection.execute(
            """
            INSERT INTO chat_messages(
                message_id, source_id, account_id, conversation_id, workspace_id, platform,
                source_account_namespace, conversation_name, conversation_type, source_message_id,
                sender_id, sender_name, sent_at_utc, original_timestamp, source_timezone,
                message_type, text, reply_to_message_id, source_file_id, page_slice_id,
                source_order, attachments_json, payload_json, created_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                stored.message_id, stored.source_id, stored.account_id, stored.conversation_id,
                stored.workspace_id, stored.platform, stored.source_account_namespace,
                stored.conversation_name, stored.conversation_type, stored.source_message_id,
                stored.sender_id, stored.sender_name,
                stored.sent_at_utc.isoformat() if stored.sent_at_utc else None,
                stored.original_timestamp, stored.source_timezone, stored.message_type, stored.text,
                stored.reply_to_message_id, stored.source_file_id, stored.page_slice_id,
                stored.source_order,
                json_or_none(stored.attachments), payload, self._now(),
            ),
        )
        return stored

    def record_message(self, value: MessageInput) -> StoredMessage:
        """Insert an idempotent message or reject a different payload sharing its ID."""
        stored = StoredMessage.from_input(value)
        with self._transaction():
            return self._store_message_in_transaction(stored)

    def message_count(self) -> int:
        with self._lock:
            row = self._connection.execute("SELECT COUNT(*) FROM chat_messages").fetchone()
        return int(row[0])

    @staticmethod
    def _canonical_scope(scope: ExportScope) -> str:
        return json.dumps(
            scope.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    def create_export_consent(
        self,
        *,
        consent_id: str,
        session_digest: str,
        scope: ExportScope,
        recorded_at_utc: str,
        expires_at_utc: str,
    ) -> None:
        """Persist one short-lived P1 authorization without retaining a raw session ID."""
        with self._transaction():
            self._connection.execute(
                """
                INSERT INTO chat_export_consents(
                    consent_id, session_digest, scope_fingerprint, scope_json,
                    recorded_at_utc, expires_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    consent_id,
                    session_digest,
                    scope.fingerprint(),
                    self._canonical_scope(scope),
                    recorded_at_utc,
                    expires_at_utc,
                ),
            )

    def export_consent_scope(
        self, *, consent_id: str, session_digest: str
    ) -> ExportScope:
        """Return the exact server-persisted scope for a same-session start."""
        with self._lock:
            row = self._connection.execute(
                """
                SELECT scope_json FROM chat_export_consents
                WHERE consent_id = ? AND session_digest = ?
                """,
                (consent_id, session_digest),
            ).fetchone()
        if row is None:
            raise PermissionError("export consent is unavailable")
        try:
            return ExportScope.model_validate_json(row["scope_json"])
        except (TypeError, ValueError):
            raise ValueError("export consent scope is invalid") from None

    def start_export_job(
        self,
        *,
        job_id: str,
        planned_batch_id: str,
        planned_snapshot_id: str,
        session_digest: str,
        scope: ExportScope,
        consent_id: str,
        idempotency_key: str,
        now_utc: str,
    ) -> tuple[dict[str, object], bool]:
        """Consume consent and create a job, or return its exact idempotent predecessor."""
        fingerprint = scope.fingerprint()
        canonical_scope = self._canonical_scope(scope)
        with self._transaction():
            existing = self._connection.execute(
                """
                SELECT * FROM chat_export_jobs
                WHERE session_digest = ? AND scope_fingerprint = ? AND idempotency_key = ?
                """,
                (session_digest, fingerprint, idempotency_key),
            ).fetchone()
            if existing is not None:
                if existing["consent_id"] == consent_id:
                    return dict(existing), False
                replay_consent = self._connection.execute(
                    "SELECT * FROM chat_export_consents WHERE consent_id = ?",
                    (consent_id,),
                ).fetchone()
                if replay_consent is None:
                    raise PermissionError("export consent is unavailable")
                if replay_consent["session_digest"] != session_digest:
                    raise PermissionError("export consent belongs to another session")
                if (
                    replay_consent["scope_fingerprint"] != fingerprint
                    or replay_consent["scope_json"] != canonical_scope
                ):
                    raise PermissionError("export consent belongs to another scope")
                if replay_consent["expires_at_utc"] <= now_utc:
                    raise PermissionError("export consent expired")
                if replay_consent["consumed_at_utc"] is not None:
                    raise PermissionError("export consent was already consumed")
                consumed = self._connection.execute(
                    """
                    UPDATE chat_export_consents
                    SET consumed_at_utc = ?, consumed_job_id = ?
                    WHERE consent_id = ? AND consumed_at_utc IS NULL
                    """,
                    (now_utc, existing["job_id"], consent_id),
                )
                if consumed.rowcount != 1:
                    raise PermissionError("export consent was already consumed")
                return dict(existing), False

            consent = self._connection.execute(
                "SELECT * FROM chat_export_consents WHERE consent_id = ?",
                (consent_id,),
            ).fetchone()
            if consent is None:
                raise PermissionError("export consent is unavailable")
            if consent["session_digest"] != session_digest:
                raise PermissionError("export consent belongs to another session")
            if (
                consent["scope_fingerprint"] != fingerprint
                or consent["scope_json"] != canonical_scope
            ):
                raise PermissionError("export consent belongs to another scope")
            if consent["expires_at_utc"] <= now_utc:
                raise PermissionError("export consent expired")
            if consent["consumed_at_utc"] is not None:
                raise PermissionError("export consent was already consumed")

            self._connection.execute(
                """
                INSERT INTO chat_export_jobs(
                    job_id, planned_batch_id, planned_snapshot_id, state,
                    idempotency_key, session_digest, scope_fingerprint, scope_json,
                    consent_id, created_at_utc, updated_at_utc, revision,
                    cancellation_requested, completed_count, fetched_count,
                    saved_count, duplicate_count, unknown_time_count,
                    parse_error_count, searchable
                ) VALUES (?, ?, ?, 'pending_authorization', ?, ?, ?, ?, ?, ?, ?, 0,
                          0, 0, 0, 0, 0, 0, 0, 0)
                """,
                (
                    job_id,
                    planned_batch_id,
                    planned_snapshot_id,
                    idempotency_key,
                    session_digest,
                    fingerprint,
                    canonical_scope,
                    consent_id,
                    now_utc,
                    now_utc,
                ),
            )
            consumed = self._connection.execute(
                """
                UPDATE chat_export_consents
                SET consumed_at_utc = ?, consumed_job_id = ?
                WHERE consent_id = ? AND consumed_at_utc IS NULL
                """,
                (now_utc, job_id, consent_id),
            )
            if consumed.rowcount != 1:
                raise PermissionError("export consent was already consumed")
            self._connection.execute(
                """
                INSERT INTO chat_export_job_events(
                    job_id, state, revision, created_at_utc
                ) VALUES (?, 'pending_authorization', 0, ?)
                """,
                (job_id, now_utc),
            )
            row = self._connection.execute(
                "SELECT * FROM chat_export_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            return dict(row), True

    def get_export_job(self, job_id: str) -> dict[str, object]:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM chat_export_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        if row is None:
            raise ValueError("export job does not exist")
        return dict(row)

    def export_job_state_history(self, job_id: str) -> tuple[str, ...]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT state FROM chat_export_job_events
                WHERE job_id = ? ORDER BY revision
                """,
                (job_id,),
            ).fetchall()
        return tuple(str(row["state"]) for row in rows)

    def cas_export_job(
        self,
        job_id: str,
        *,
        expected_state: str,
        expected_revision: int,
        new_state: str,
        updated_at_utc: str | None = None,
        **changes: object,
    ) -> bool:
        """Compare-and-swap one job revision; stale/deleted workers write nothing."""
        allowed = {
            "batch_id",
            "snapshot_id",
            "known_total",
            "completed_count",
            "fetched_count",
            "saved_count",
            "duplicate_count",
            "unknown_time_count",
            "parse_error_count",
            "error_code",
            "manifest_sha256",
            "completeness",
            "searchable",
            "cancellation_requested",
        }
        if not set(changes) <= allowed:
            raise ValueError("unsupported export job update")
        timestamp = updated_at_utc or self._now()
        assignments = ["state = ?", "updated_at_utc = ?", "revision = revision + 1"]
        parameters: list[object] = [new_state, timestamp]
        for name, value in changes.items():
            assignments.append(f"{name} = ?")
            parameters.append(value)
        parameters.extend((job_id, expected_state, expected_revision))
        with self._transaction():
            updated = self._connection.execute(
                f"""
                UPDATE chat_export_jobs SET {', '.join(assignments)}
                WHERE job_id = ? AND state = ? AND revision = ?
                """,
                tuple(parameters),
            )
            if updated.rowcount != 1:
                return False
            if new_state != expected_state:
                self._connection.execute(
                    """
                    INSERT INTO chat_export_job_events(
                        job_id, state, revision, created_at_utc, error_code
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        job_id,
                        new_state,
                        expected_revision + 1,
                        timestamp,
                        changes.get("error_code"),
                    ),
                )
            return True

    def delete_export_job(self, job_id: str, *, session_digest: str) -> bool:
        with self._transaction():
            deleted = self._connection.execute(
                "DELETE FROM chat_export_jobs WHERE job_id = ? AND session_digest = ?",
                (job_id, session_digest),
            )
            return deleted.rowcount == 1

    def list_export_jobs_in_states(self, states: tuple[str, ...]) -> tuple[dict[str, object], ...]:
        if not states:
            return ()
        placeholders = ",".join("?" for _ in states)
        with self._lock:
            rows = self._connection.execute(
                f"SELECT * FROM chat_export_jobs WHERE state IN ({placeholders})",
                states,
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def accept_limitations_and_begin_index(
        self,
        *,
        job_id: str,
        session_digest: str,
        manifest_sha256: str,
        accepted_at_utc: str,
    ) -> dict[str, object]:
        """Hash-bind one explicit acceptance and atomically claim the index stage."""
        with self._transaction():
            job = self._connection.execute(
                "SELECT * FROM chat_export_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if job is None:
                raise ValueError("export job does not exist")
            if job["session_digest"] != session_digest:
                raise PermissionError("export job belongs to another session")
            if job["state"] != "partial" or job["batch_id"] is None:
                raise ValueError("export job is not awaiting limitation acceptance")
            if job["manifest_sha256"] != manifest_sha256:
                raise ValueError("manifest hash does not match the export job")
            batch = self._connection.execute(
                """
                SELECT manifest_sha256, completeness FROM chat_export_batches
                WHERE batch_id = ?
                """,
                (job["batch_id"],),
            ).fetchone()
            if (
                batch is None
                or batch["manifest_sha256"] != manifest_sha256
                or batch["completeness"] not in {"partial", "unknown"}
            ):
                raise ValueError("manifest hash is not eligible for limitation acceptance")
            self._connection.execute(
                """
                UPDATE chat_export_batches
                SET limitations_accepted_at_utc = ?,
                    limitations_accepted_manifest_sha256 = ?
                WHERE batch_id = ?
                """,
                (accepted_at_utc, manifest_sha256, job["batch_id"]),
            )
            revision = int(job["revision"]) + 1
            updated = self._connection.execute(
                """
                UPDATE chat_export_jobs
                SET state = 'indexing', revision = ?, updated_at_utc = ?, error_code = NULL
                WHERE job_id = ? AND state = 'partial' AND revision = ?
                """,
                (revision, accepted_at_utc, job_id, job["revision"]),
            )
            if updated.rowcount != 1:
                raise ValueError("export job changed during limitation acceptance")
            self._connection.execute(
                """
                INSERT INTO chat_export_job_events(
                    job_id, state, revision, created_at_utc
                ) VALUES (?, 'indexing', ?, ?)
                """,
                (job_id, revision, accepted_at_utc),
            )
            return dict(
                self._connection.execute(
                    "SELECT * FROM chat_export_jobs WHERE job_id = ?", (job_id,)
                ).fetchone()
            )

    def export_batch_for_session(
        self, batch_id: str, *, session_digest: str
    ) -> dict[str, object]:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT batch.batch_id, batch.manifest_sha256, batch.completeness,
                       batch.limitations_accepted_at_utc,
                       batch.limitations_accepted_manifest_sha256,
                       job.searchable, job.snapshot_id
                FROM chat_export_batches AS batch
                JOIN chat_export_jobs AS job ON job.batch_id = batch.batch_id
                WHERE batch.batch_id = ? AND job.session_digest = ?
                ORDER BY job.created_at_utc DESC LIMIT 1
                """,
                (batch_id, session_digest),
            ).fetchone()
        if row is None:
            raise ValueError("export batch does not exist")
        return dict(row)

    def export_batch_evidence(self, batch_id: str) -> dict[str, object]:
        """Read canonical evidence for an internal import-recovery continuation."""
        with self._lock:
            row = self._connection.execute(
                """
                SELECT batch_id, manifest_sha256, completeness
                FROM chat_export_batches WHERE batch_id = ?
                """,
                (batch_id,),
            ).fetchone()
        if row is None:
            raise ValueError("export batch does not exist")
        return dict(row)

    def create_batch(self, batch_id: str, scope: ExportScope) -> None:
        """Persist the complete approved scope and selected-conversation FK boundary."""
        with self._transaction():
            self._ensure_account(
                scope.source_id,
                scope.account_id,
                scope.source_account_namespace,
            )
            existing = self._connection.execute(
                "SELECT scope_fingerprint FROM chat_export_batches WHERE batch_id = ?", (batch_id,)
            ).fetchone()
            if existing is not None:
                if existing["scope_fingerprint"] != scope.fingerprint():
                    raise ValueError("batch ID belongs to another scope")
                return
            self._connection.execute(
                """
                INSERT INTO chat_export_batches(
                    batch_id, source_id, account_id, source_account_namespace,
                    schema_version, scope_fingerprint, scope_json, requested_start_utc,
                    requested_end_utc, display_timezone, created_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    batch_id, scope.source_id, scope.account_id,
                    scope.source_account_namespace, scope.schema_version, scope.fingerprint(),
                    scope.model_dump_json(),
                    scope.range_start.astimezone(timezone.utc).isoformat(),
                    scope.range_end.astimezone(timezone.utc).isoformat(),
                    scope.display_timezone, self._now(),
                ),
            )
            for conversation in scope.conversations:
                self._ensure_conversation(
                    scope.source_id, scope.account_id, conversation.conversation_id,
                    conversation.display_name, conversation.conversation_type,
                )
                self._connection.execute(
                    """
                    INSERT INTO chat_batch_conversations(
                        batch_id, source_id, account_id, conversation_id
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (batch_id, scope.source_id, scope.account_id, conversation.conversation_id),
                )

    def batch_scope(self, batch_id: str) -> ExportScope:
        row = self._connection.execute(
            "SELECT scope_json FROM chat_export_batches WHERE batch_id = ?", (batch_id,)
        ).fetchone()
        if row is None:
            raise ValueError("batch must exist")
        if row["scope_json"] is None:
            raise ValueError("legacy batch has no export scope")
        return ExportScope.model_validate_json(row["scope_json"])

    def add_message_to_batch(self, batch_id: str, message_id: str) -> bool:
        """Add selected same-account message membership once, returning whether it was new."""
        with self._transaction():
            batch = self._connection.execute(
                "SELECT source_id, account_id FROM chat_export_batches WHERE batch_id = ?", (batch_id,)
            ).fetchone()
            message = self._connection.execute(
                """
                SELECT source_id, account_id, conversation_id FROM chat_messages WHERE message_id = ?
                """,
                (message_id,),
            ).fetchone()
            if batch is None or message is None:
                raise ValueError("batch and message must exist")
            if (batch["source_id"], batch["account_id"]) != (message["source_id"], message["account_id"]):
                raise ValueError("batch membership cannot cross accounts")
            selected = self._connection.execute(
                """
                SELECT 1 FROM chat_batch_conversations
                WHERE batch_id = ? AND source_id = ? AND account_id = ? AND conversation_id = ?
                """,
                (batch_id, message["source_id"], message["account_id"], message["conversation_id"]),
            ).fetchone()
            if selected is None:
                raise ValueError("message conversation was not selected")
            result = self._connection.execute(
                """
                INSERT OR IGNORE INTO chat_batch_messages(
                    batch_id, message_id, source_id, account_id, conversation_id
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (batch_id, message_id, message["source_id"], message["account_id"], message["conversation_id"]),
            )
            return result.rowcount == 1

    def batch_message_ids(self, batch_id: str) -> tuple[str, ...]:
        rows = self._connection.execute(
            "SELECT message_id FROM chat_batch_messages WHERE batch_id = ? ORDER BY message_id",
            (batch_id,),
        ).fetchall()
        return tuple(str(row["message_id"]) for row in rows)

    @staticmethod
    def _canonical_manifest(manifest: BatchManifest) -> tuple[str, str]:
        payload = json.dumps(
            manifest.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return payload, sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _validate_import_contract(
        *,
        batch_id: str,
        snapshot_id: str,
        scope: ExportScope,
        messages: tuple[StoredMessage, ...],
        manifest: BatchManifest,
        allow_undated: bool = False,
    ) -> tuple[StoredMessage, ...]:
        """Recheck importer inputs without echoing message bodies in errors."""
        scope_conversations = tuple(
            (item.conversation_id, item.display_name, item.conversation_type)
            for item in scope.conversations
        )
        manifest_conversations = tuple(
            (item.conversation_id, item.display_name, item.conversation_type)
            for item in manifest.conversations
        )
        if (
            manifest.export_batch_id != batch_id
            or manifest.snapshot_id != snapshot_id
        ):
            raise ValueError("manifest identity conflict")
        if (
            manifest.schema_version != scope.schema_version
            or manifest.scope_fingerprint != scope.fingerprint()
            or manifest.output_root_id != scope.output_root_id
            or manifest.source_id != scope.source_id
            or manifest.account_id != scope.account_id
            or manifest.source_account_namespace != scope.source_account_namespace
            or manifest_conversations != scope_conversations
            or manifest.requested_start_utc != scope.range_start
            or manifest.requested_end_utc != scope.range_end
            or manifest.display_timezone != scope.display_timezone
            or manifest.source_tool_version != scope.tool_version
            or manifest.client_version != scope.client_version
            or manifest.account_version != scope.account_version
        ):
            raise ValueError("manifest does not match the export scope")
        if manifest.saved_count != len(messages):
            raise ValueError("manifest saved count does not match messages")

        selected = {item.conversation_id for item in scope.conversations}
        normalized: list[StoredMessage] = []
        seen_ids: set[str] = set()
        for message in messages:
            payload = message.model_dump()
            supplied_message_id = payload.pop("message_id")
            payload["export_batch_id"] = None
            stable = StoredMessage.from_input(MessageInput.model_validate(payload))
            if supplied_message_id != stable.message_id:
                raise ValueError("invalid stable message identity")
            if stable.message_id in seen_ids:
                raise ValueError("duplicate stable message identity")
            seen_ids.add(stable.message_id)
            if (stable.source_id, stable.account_id) != (scope.source_id, scope.account_id):
                raise ValueError("message is outside the export scope")
            if (
                scope.source_account_namespace is not None
                and stable.source_account_namespace != scope.source_account_namespace
            ):
                raise ValueError("message account namespace is outside the export scope")
            if stable.conversation_id not in selected:
                raise ValueError("message conversation was not selected")
            if stable.sent_at_utc is None and not (allow_undated and scope.source_id == "manual"):
                raise ValueError("snapshot message has no precise timestamp")
            if stable.sent_at_utc is not None and (stable.sent_at_utc < scope.range_start or stable.sent_at_utc >= scope.range_end):
                raise ValueError("snapshot message is outside the export range")
            normalized.append(stable)

        timestamps = tuple(message.sent_at_utc for message in normalized if message.sent_at_utc is not None)
        expected_earliest = min(timestamps) if timestamps else None
        expected_latest = max(timestamps) if timestamps else None
        if (
            manifest.earliest_saved_at_utc != expected_earliest
            or manifest.latest_saved_at_utc != expected_latest
        ):
            raise ValueError("manifest saved timestamp bounds do not match messages")
        return tuple(normalized)

    def import_snapshot_atomic(
        self,
        *,
        batch_id: str,
        snapshot_id: str,
        scope: ExportScope,
        messages: tuple[StoredMessage, ...],
        manifest: BatchManifest,
        manual_import: ManualImportCommit | None = None,
    ) -> SnapshotRef:
        """Idempotently import one verified artifact set in one write transaction."""
        normalized = self._validate_import_contract(
            batch_id=batch_id,
            snapshot_id=snapshot_id,
            scope=scope,
            messages=messages,
            manifest=manifest,
            allow_undated=manual_import is not None,
        )
        manifest_json, manifest_sha256 = self._canonical_manifest(manifest)
        scope_fingerprint = scope.fingerprint()
        expected_payloads = {
            message.message_id: self._payload(message) for message in normalized
        }
        if manual_import is not None:
            if manual_import.analysis.parsed_count != len(normalized):
                raise ValueError("manual import analysis does not match messages")
            selected_conversations = {item.conversation_id for item in manual_import.selections}
            if selected_conversations != {item.conversation_id for item in scope.conversations}:
                raise ValueError("manual import selections do not match scope")
            if manual_import.expires_at_utc <= manual_import.created_at_utc:
                raise ValueError("manual import selection expiry is invalid")

        with self._transaction():
            self._ensure_account(
                scope.source_id,
                scope.account_id,
                scope.source_account_namespace,
            )
            for conversation in scope.conversations:
                self._ensure_conversation(
                    scope.source_id,
                    scope.account_id,
                    conversation.conversation_id,
                    conversation.display_name,
                    conversation.conversation_type,
                )

            batch = self._connection.execute(
                """
                SELECT source_id, account_id, source_account_namespace,
                       scope_fingerprint, scope_json, manifest_json,
                       manifest_sha256, completeness
                FROM chat_export_batches WHERE batch_id = ?
                """,
                (batch_id,),
            ).fetchone()
            manifest_was_bound = batch is not None and batch["manifest_sha256"] is not None
            if batch is None:
                self._connection.execute(
                    """
                    INSERT INTO chat_export_batches(
                        batch_id, source_id, account_id, source_account_namespace,
                        schema_version, scope_fingerprint, scope_json,
                        requested_start_utc, requested_end_utc, display_timezone,
                        created_at_utc, manifest_json, manifest_sha256, completeness
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        batch_id,
                        scope.source_id,
                        scope.account_id,
                        scope.source_account_namespace,
                        scope.schema_version,
                        scope_fingerprint,
                        scope.model_dump_json(),
                        scope.range_start.astimezone(timezone.utc).isoformat(),
                        scope.range_end.astimezone(timezone.utc).isoformat(),
                        scope.display_timezone,
                        self._now(),
                        manifest_json,
                        manifest_sha256,
                        manifest.completeness,
                    ),
                )
                for conversation in scope.conversations:
                    self._connection.execute(
                        """
                        INSERT INTO chat_batch_conversations(
                            batch_id, source_id, account_id, conversation_id
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (
                            batch_id,
                            scope.source_id,
                            scope.account_id,
                            conversation.conversation_id,
                        ),
                    )
            else:
                try:
                    persisted_scope = ExportScope.model_validate_json(batch["scope_json"])
                except (TypeError, ValueError):
                    raise ValueError("batch ID belongs to another scope") from None
                if (
                    batch["scope_fingerprint"] != scope_fingerprint
                    or persisted_scope != scope
                    or (batch["source_id"], batch["account_id"])
                    != (scope.source_id, scope.account_id)
                    or batch["source_account_namespace"] != scope.source_account_namespace
                ):
                    raise ValueError("batch ID belongs to another scope")
                if batch["manifest_sha256"] is None:
                    if batch["manifest_json"] is not None or batch["completeness"] is not None:
                        raise ValueError("batch manifest conflict")
                    self._connection.execute(
                        """
                        UPDATE chat_export_batches
                        SET manifest_json = ?, manifest_sha256 = ?, completeness = ?
                        WHERE batch_id = ?
                        """,
                        (
                            manifest_json,
                            manifest_sha256,
                            manifest.completeness,
                            batch_id,
                        ),
                    )
                elif (
                    batch["manifest_sha256"] != manifest_sha256
                    or batch["manifest_json"] != manifest_json
                    or batch["completeness"] != manifest.completeness
                ):
                    raise ValueError("batch manifest conflict")

            for message in normalized:
                persisted = self._store_message_in_transaction(message)
                self._connection.execute(
                    """
                    INSERT OR IGNORE INTO chat_batch_messages(
                        batch_id, message_id, source_id, account_id, conversation_id
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        batch_id,
                        persisted.message_id,
                        persisted.source_id,
                        persisted.account_id,
                        persisted.conversation_id,
                    ),
                )

            membership_ids = {
                str(row["message_id"])
                for row in self._connection.execute(
                    "SELECT message_id FROM chat_batch_messages WHERE batch_id = ?",
                    (batch_id,),
                )
            }
            if membership_ids != set(expected_payloads):
                raise ValueError("batch membership conflict")

            snapshot = self._connection.execute(
                """
                SELECT batch_id, sealed FROM chat_snapshots WHERE snapshot_id = ?
                """,
                (snapshot_id,),
            ).fetchone()
            if snapshot is not None:
                if (
                    snapshot["batch_id"] != batch_id
                    or snapshot["sealed"] != 1
                    or not manifest_was_bound
                ):
                    raise ValueError("snapshot identity conflict")
                snapshot_rows = self._connection.execute(
                    """
                    SELECT message_id, payload_json
                    FROM chat_snapshot_messages WHERE snapshot_id = ?
                    """,
                    (snapshot_id,),
                ).fetchall()
                try:
                    persisted_payloads = {
                        str(row["message_id"]): self._payload(
                            StoredMessage.model_validate_json(row["payload_json"])
                        )
                        for row in snapshot_rows
                    }
                except ValueError:
                    raise ValueError("snapshot payload conflict") from None
                if persisted_payloads != expected_payloads:
                    raise ValueError("snapshot payload conflict")
                if manual_import is not None:
                    self._register_manual_import_in_transaction(
                        manual_import, batch_id=batch_id, snapshot_id=snapshot_id
                    )
                return SnapshotRef(snapshot_id, batch_id)

            self._connection.execute(
                """
                INSERT INTO chat_snapshots(snapshot_id, batch_id, created_at_utc, sealed)
                VALUES (?, ?, ?, 0)
                """,
                (snapshot_id, batch_id, self._now()),
            )
            self._connection.execute(
                """
                INSERT INTO chat_snapshot_messages(snapshot_id, message_id, payload_json)
                SELECT ?, membership.message_id, message.payload_json
                FROM chat_batch_messages AS membership
                JOIN chat_messages AS message ON message.message_id = membership.message_id
                WHERE membership.batch_id = ?
                """,
                (snapshot_id, batch_id),
            )
            self._connection.execute(
                "UPDATE chat_snapshots SET sealed = 1 WHERE snapshot_id = ?",
                (snapshot_id,),
            )
            if manual_import is not None:
                self._register_manual_import_in_transaction(
                    manual_import, batch_id=batch_id, snapshot_id=snapshot_id
                )
        return SnapshotRef(snapshot_id, batch_id)

    def _register_manual_import_in_transaction(
        self,
        value: ManualImportCommit,
        *,
        batch_id: str,
        snapshot_id: str,
    ) -> None:
        analysis_json = value.analysis.model_dump_json()
        existing = self._connection.execute(
            "SELECT * FROM chat_manual_imports WHERE import_id = ?",
            (value.import_id,),
        ).fetchone()
        expected = (
            value.session_digest,
            batch_id,
            snapshot_id,
            value.source_kind,
            value.platform_hint,
            value.source_fingerprint,
            value.filename,
            analysis_json,
            value.created_at_utc.astimezone(timezone.utc).isoformat(),
        )
        if existing is not None:
            actual = tuple(existing[key] for key in (
                "session_digest", "batch_id", "snapshot_id", "source_kind",
                "platform_hint", "source_fingerprint", "filename", "analysis_json",
                "created_at_utc",
            ))
            if actual != expected:
                raise ValueError("manual import identity conflict")
        else:
            self._connection.execute(
                """
                INSERT INTO chat_manual_imports(
                    import_id, session_digest, batch_id, snapshot_id, source_kind,
                    platform_hint, source_fingerprint, filename, analysis_json,
                    created_at_utc, state
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'complete')
                """,
                (value.import_id, *expected),
            )
        expected_selections = {
            (item.handle_digest, item.conversation_id) for item in value.selections
        }
        if len(expected_selections) != len(value.selections):
            raise ValueError("manual import selection conflict")
        persisted_selections = {
            (str(row["handle_digest"]), str(row["conversation_id"]))
            for row in self._connection.execute(
                """
                SELECT handle_digest, conversation_id
                FROM chat_manual_import_selections WHERE import_id = ?
                """,
                (value.import_id,),
            ).fetchall()
        }
        if persisted_selections and persisted_selections != expected_selections:
            raise ValueError("manual import selection conflict")
        if not persisted_selections:
            for selection in value.selections:
                try:
                    self._connection.execute(
                        """
                        INSERT INTO chat_manual_import_selections(
                            handle_digest, import_id, conversation_id, expires_at_utc
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (
                            selection.handle_digest,
                            value.import_id,
                            selection.conversation_id,
                            value.expires_at_utc.astimezone(timezone.utc).isoformat(),
                        ),
                    )
                except sqlite3.IntegrityError:
                    raise ValueError("manual import selection conflict") from None

    def create_snapshot(self, snapshot_id: str, batch_id: str) -> SnapshotRef:
        """Copy current membership payloads once; later base changes cannot alter it."""
        with self._transaction():
            if self._connection.execute(
                "SELECT 1 FROM chat_snapshots WHERE snapshot_id = ?", (snapshot_id,)
            ).fetchone() is not None:
                raise ValueError("immutable snapshot already exists")
            if self._connection.execute(
                "SELECT 1 FROM chat_export_batches WHERE batch_id = ?", (batch_id,)
            ).fetchone() is None:
                raise ValueError("batch must exist")
            self._connection.execute(
                "INSERT INTO chat_snapshots(snapshot_id, batch_id, created_at_utc, sealed) VALUES (?, ?, ?, 0)",
                (snapshot_id, batch_id, self._now()),
            )
            self._connection.execute(
                """
                INSERT INTO chat_snapshot_messages(snapshot_id, message_id, payload_json)
                SELECT ?, membership.message_id, message.payload_json
                FROM chat_batch_messages AS membership
                JOIN chat_messages AS message ON message.message_id = membership.message_id
                WHERE membership.batch_id = ?
                """,
                (snapshot_id, batch_id),
            )
            self._connection.execute(
                "UPDATE chat_snapshots SET sealed = 1 WHERE snapshot_id = ?", (snapshot_id,)
            )
        return SnapshotRef(snapshot_id, batch_id)

    def snapshot_message_ids(self, snapshot_id: str) -> tuple[str, ...]:
        return tuple(message.message_id for message in self.snapshot_messages(snapshot_id))

    def snapshot_messages(self, snapshot_id: str) -> tuple[StoredMessage, ...]:
        rows = self._connection.execute(
            """
            SELECT payload_json FROM chat_snapshot_messages
            WHERE snapshot_id = ? ORDER BY message_id
            """,
            (snapshot_id,),
        ).fetchall()
        return tuple(StoredMessage.model_validate_json(row["payload_json"]) for row in rows)

    def generation_snapshot_messages(
        self, *, session_digest: str, snapshot_id: str
    ) -> tuple[str, tuple[StoredMessage, ...]]:
        """Load only a same-session searchable sealed snapshot from its immutable copies."""
        with self._lock:
            snapshots = self._connection.execute(
                """
                WITH authorized_snapshots AS (
                    SELECT snapshot_id, batch_id
                    FROM chat_export_jobs
                    WHERE session_digest = ? AND state = 'complete' AND searchable = 1
                      AND NOT EXISTS (SELECT 1 FROM chat_raw_retirements retired WHERE retired.target_kind='batch' AND retired.target_id=chat_export_jobs.batch_id)
                    UNION
                    SELECT snapshot_id, batch_id
                    FROM chat_manual_imports
                    WHERE session_digest = ? AND state = 'complete'
                      AND NOT EXISTS (SELECT 1 FROM chat_raw_retirements retired WHERE retired.target_kind='batch' AND retired.target_id=chat_manual_imports.batch_id)
                )
                SELECT DISTINCT snapshot.batch_id
                FROM authorized_snapshots AS authorized
                JOIN chat_snapshots AS snapshot
                  ON snapshot.snapshot_id = authorized.snapshot_id
                 AND snapshot.batch_id = authorized.batch_id
                WHERE snapshot.snapshot_id = ? AND snapshot.sealed = 1
                """,
                (session_digest, session_digest, snapshot_id),
            ).fetchall()
            if len(snapshots) != 1:
                raise PermissionError("generation snapshot is unavailable")
            rows = self._connection.execute(
                """
                SELECT payload_json FROM chat_snapshot_messages
                WHERE snapshot_id = ? ORDER BY message_id
                """,
                (snapshot_id,),
            ).fetchall()
        try:
            messages = tuple(
                StoredMessage.model_validate_json(row["payload_json"]) for row in rows
            )
        except (TypeError, ValueError):
            raise RuntimeError("stored generation snapshot is invalid") from None
        return str(snapshots[0]["batch_id"]), messages

    def resolve_manual_import_selections(
        self,
        *,
        session_digest: str,
        source_id: str,
        account_id: str,
        handle_digests: tuple[str, ...],
        now_utc: str,
    ) -> tuple[dict[str, object], ...]:
        """Resolve browser handles without exposing persisted conversation IDs to listing APIs."""
        if source_id != "manual" or not handle_digests or len(set(handle_digests)) != len(handle_digests):
            raise PermissionError("manual import selection is unavailable")
        resolved: list[dict[str, object]] = []
        import_id: str | None = None
        now = datetime.fromisoformat(now_utc)
        with self._lock:
            for handle_digest in handle_digests:
                row = self._connection.execute(
                    """
                    SELECT selection.import_id, selection.conversation_id,
                           selection.expires_at_utc, imported.batch_id,
                           imported.snapshot_id, batch.source_id, batch.account_id,
                           conversation.conversation_name,
                           conversation.conversation_type
                    FROM chat_manual_import_selections AS selection
                    JOIN chat_manual_imports AS imported
                      ON imported.import_id = selection.import_id
                    JOIN chat_export_batches AS batch
                      ON batch.batch_id = imported.batch_id
                    JOIN chat_conversations AS conversation
                      ON conversation.source_id = batch.source_id
                     AND conversation.account_id = batch.account_id
                     AND conversation.conversation_id = selection.conversation_id
                    WHERE selection.handle_digest = ?
                      AND imported.session_digest = ?
                      AND imported.state = 'complete'
                      AND NOT EXISTS (SELECT 1 FROM chat_raw_retirements retired WHERE retired.target_kind='batch' AND retired.target_id=imported.batch_id)
                    """,
                    (handle_digest, session_digest),
                ).fetchone()
                if (
                    row is None
                    or row["source_id"] != source_id
                    or row["account_id"] != account_id
                    or datetime.fromisoformat(str(row["expires_at_utc"])) <= now
                    or (import_id is not None and row["import_id"] != import_id)
                ):
                    raise PermissionError("manual import selection is unavailable")
                import_id = str(row["import_id"])
                resolved.append(dict(row))
        return tuple(resolved)

    def recover_manual_imports(
        self,
        *,
        session_digest: str,
        expires_at_utc: str,
        cursor: str | None,
        limit: int,
    ) -> tuple[tuple[dict[str, object], ...], str | None]:
        """Page same-session imports and atomically replace their browser handles."""
        if (
            len(session_digest) != 64
            or any(character not in "0123456789abcdef" for character in session_digest)
            or not 1 <= limit <= 50
            or (cursor is not None and (not cursor or len(cursor) > 256))
        ):
            raise ValueError("manual import recovery request is invalid")
        with self._transaction():
            cursor_values = (
                _decode_manual_import_cursor(cursor) if cursor is not None else None
            )
            parameters: list[object] = [session_digest]
            cursor_filter = ""
            if cursor_values is not None:
                cursor_filter = """
                  AND (
                    imported.created_at_utc < ?
                    OR (imported.created_at_utc = ? AND imported.import_id < ?)
                  )
                """
                parameters.extend((cursor_values[0], cursor_values[0], cursor_values[1]))
            parameters.append(limit + 1)
            rows = self._connection.execute(
                f"""
                SELECT imported.import_id, imported.batch_id, imported.snapshot_id,
                       imported.created_at_utc,
                       imported.analysis_json, batch.scope_json
                FROM chat_manual_imports AS imported
                JOIN chat_export_batches AS batch ON batch.batch_id = imported.batch_id
                JOIN chat_snapshots AS snapshot
                  ON snapshot.snapshot_id = imported.snapshot_id
                 AND snapshot.batch_id = imported.batch_id
                WHERE imported.session_digest = ? AND imported.state = 'complete'
                  AND NOT EXISTS (SELECT 1 FROM chat_raw_retirements retired WHERE retired.target_kind='batch' AND retired.target_id=imported.batch_id)
                  AND snapshot.sealed = 1
                  {cursor_filter}
                ORDER BY imported.created_at_utc DESC, imported.import_id DESC
                LIMIT ?
                """,
                tuple(parameters),
            ).fetchall()
            page_rows = rows[:limit]
            recovered: list[dict[str, object]] = []
            for row in page_rows:
                try:
                    scope = ExportScope.model_validate_json(row["scope_json"])
                    analysis = ImportAnalysis.model_validate_json(row["analysis_json"])
                except (TypeError, ValueError):
                    raise RuntimeError("stored manual import is invalid") from None
                if scope.source_id != "manual" or len(scope.conversations) > 500:
                    raise RuntimeError("stored manual import is invalid")
                handles: list[str] = []
                self._connection.execute(
                    "DELETE FROM chat_manual_import_selections WHERE import_id = ?",
                    (row["import_id"],),
                )
                for conversation in scope.conversations:
                    handle = f"selection:{secrets.token_urlsafe(32)}"
                    self._connection.execute(
                        """
                        INSERT INTO chat_manual_import_selections(
                            handle_digest, import_id, conversation_id, expires_at_utc
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (
                            sha256(handle.encode("utf-8")).hexdigest(),
                            row["import_id"],
                            conversation.conversation_id,
                            expires_at_utc,
                        ),
                    )
                    handles.append(handle)
                recovered.append({
                    "import_id": str(row["import_id"]),
                    "batch_id": str(row["batch_id"]),
                    "snapshot_id": str(row["snapshot_id"]),
                    "created_at_utc": str(row["created_at_utc"]),
                    "scope": scope,
                    "analysis": analysis,
                    "handles": tuple(handles),
                })
            next_cursor = (
                _encode_manual_import_cursor(
                    str(page_rows[-1]["created_at_utc"]),
                    str(page_rows[-1]["import_id"]),
                )
                if len(rows) > limit and page_rows
                else None
            )
            return tuple(recovered), next_cursor

    def get_generation_cache(
        self, *, cache_key: str, session_digest: str
    ) -> dict[str, object] | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT identity_json, result_json
                FROM chat_generation_cache
                WHERE cache_key = ? AND session_digest = ?
                """,
                (cache_key, session_digest),
            ).fetchone()
            if row is None:
                row = self._connection.execute(
                    "SELECT identity_json, result_json FROM chat_generation_cache_v2 WHERE cache_key = ? AND session_digest = ?",
                    (cache_key, session_digest),
                ).fetchone()
        return dict(row) if row is not None else None

    def require_generation_sources(self, scope_json: str, *, session_digest: str, now_utc: str | None = None) -> None:
        """Revalidate persisted scopes, including time while workers run without HTTP."""
        from chat_to_howto.repositories.content import ContentRepository
        scope = json.loads(scope_json)
        refs = scope.get("source_refs") or [{"kind": "chat_snapshot", "id": scope["snapshot_id"]}]
        current = datetime.fromisoformat(now_utc) if now_utc is not None else None
        with self._lock:
            deadlines = self.raw_retention_targets() if current is not None else ()
            for ref in refs:
                if ref["kind"] == "chat_snapshot":
                    target_id, _ = self.generation_snapshot_messages(session_digest=session_digest, snapshot_id=ref["id"])
                    target_kind = "batch"
                else:
                    target_id, target_kind = ref["id"], "content_batch"
                    if not ContentRepository(self).list_units(target_id, session_digest=session_digest):
                        raise PermissionError("generation source is unavailable")
                if current is not None and any(
                    item["target_kind"] == target_kind and item["target_id"] == target_id
                    and item["session_digest"] == session_digest
                    and datetime.fromisoformat(str(item["created_at_utc"])) + timedelta(days=7) <= current
                    for item in deadlines
                ):
                    raise RawSourceExpired("generation source expired")

    def create_generation_preview(
        self,
        *,
        preview_id: str,
        session_digest: str,
        scope_fingerprint: str,
        payload_sha256: str,
        scope_json: str,
        cache_identity_json: str,
        settings_json: str,
        message_count: int,
        character_count: int,
        estimated_input_tokens: int,
        max_output_tokens: int,
        provider_calls: int,
        cache_hit: bool,
        will_send_external: bool,
        created_at_utc: str,
        expires_at_utc: str,
    ) -> None:
        with self._transaction():
            self.require_generation_sources(scope_json, session_digest=session_digest, now_utc=created_at_utc)
            self._connection.execute(
                """
                INSERT INTO chat_generation_previews(
                    preview_id, session_digest, scope_fingerprint, payload_sha256,
                    scope_json, cache_identity_json, settings_json,
                    message_count, character_count, estimated_input_tokens,
                    max_output_tokens, provider_calls, cache_hit, will_send_external,
                    created_at_utc, expires_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    preview_id,
                    session_digest,
                    scope_fingerprint,
                    payload_sha256,
                    scope_json,
                    cache_identity_json,
                    settings_json,
                    message_count,
                    character_count,
                    estimated_input_tokens,
                    max_output_tokens,
                    provider_calls,
                    int(cache_hit),
                    int(will_send_external),
                    created_at_utc,
                    expires_at_utc,
                ),
            )

    def get_generation_preview(
        self,
        *,
        preview_id: str,
        session_digest: str,
        now_utc: str,
    ) -> dict[str, object]:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT * FROM chat_generation_previews
                WHERE preview_id = ? AND session_digest = ? AND expires_at_utc > ?
                """,
                (preview_id, session_digest, now_utc),
            ).fetchone()
        if row is None:
            raise PermissionError("generation preview is unavailable")
        return dict(row)

    def create_generation_consent(
        self,
        *,
        consent_id: str,
        preview_id: str,
        session_digest: str,
        recorded_at_utc: str,
        expires_at_utc: str,
    ) -> dict[str, object]:
        with self._transaction():
            preview = self._connection.execute(
                """
                SELECT * FROM chat_generation_previews
                WHERE preview_id = ? AND session_digest = ? AND expires_at_utc > ?
                """,
                (preview_id, session_digest, recorded_at_utc),
            ).fetchone()
            if preview is None:
                raise PermissionError("generation preview is unavailable")
            self.require_generation_sources(str(preview["scope_json"]), session_digest=session_digest, now_utc=recorded_at_utc)
            try:
                self._connection.execute(
                    """
                    INSERT INTO chat_generation_consents(
                        consent_id, preview_id, session_digest, scope_fingerprint,
                        payload_sha256, outbound_authorized, recorded_at_utc,
                        expires_at_utc
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        consent_id,
                        preview_id,
                        session_digest,
                        preview["scope_fingerprint"],
                        preview["payload_sha256"],
                        preview["will_send_external"],
                        recorded_at_utc,
                        expires_at_utc,
                    ),
                )
            except sqlite3.IntegrityError:
                raise PermissionError("generation preview was already authorized") from None
            row = self._connection.execute(
                "SELECT * FROM chat_generation_consents WHERE consent_id = ?",
                (consent_id,),
            ).fetchone()
            return dict(row)

    def start_generation_job(
        self,
        *,
        job_id: str,
        consent_id: str,
        session_digest: str,
        idempotency_key: str,
        now_utc: str,
    ) -> tuple[dict[str, object], bool]:
        with self._transaction():
            consent = self._connection.execute(
                """
                SELECT consent.*, preview.scope_json, preview.cache_identity_json,
                       preview.settings_json, preview.provider_calls
                FROM chat_generation_consents AS consent
                JOIN chat_generation_previews AS preview
                  ON preview.preview_id = consent.preview_id
                WHERE consent.consent_id = ?
                """,
                (consent_id,),
            ).fetchone()
            if consent is None or consent["session_digest"] != session_digest:
                raise PermissionError("generation consent is unavailable")
            self.require_generation_sources(str(consent["scope_json"]), session_digest=session_digest, now_utc=now_utc)
            existing = self._connection.execute(
                """
                SELECT * FROM chat_generation_jobs
                WHERE session_digest = ? AND scope_fingerprint = ? AND idempotency_key = ?
                """,
                (session_digest, consent["scope_fingerprint"], idempotency_key),
            ).fetchone()
            if existing is not None:
                if existing["consent_id"] != consent_id:
                    raise PermissionError("idempotency key belongs to another consent")
                return dict(existing), False
            if consent["expires_at_utc"] <= now_utc:
                raise PermissionError("generation consent expired")
            if consent["consumed_at_utc"] is not None:
                raise PermissionError("generation consent was already consumed")
            self._connection.execute(
                """
                INSERT INTO chat_generation_jobs(
                    job_id, session_digest, consent_id, idempotency_key, state,
                    scope_fingerprint, scope_json, cache_identity_json, settings_json,
                    outbound_authorized, total_chunks, created_at_utc, updated_at_utc
                ) VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    session_digest,
                    consent_id,
                    idempotency_key,
                    consent["scope_fingerprint"],
                    consent["scope_json"],
                    consent["cache_identity_json"],
                    consent["settings_json"],
                    consent["outbound_authorized"],
                    consent["provider_calls"],
                    now_utc,
                    now_utc,
                ),
            )
            updated = self._connection.execute(
                """
                UPDATE chat_generation_consents
                SET consumed_at_utc = ?, consumed_job_id = ?
                WHERE consent_id = ? AND consumed_at_utc IS NULL
                """,
                (now_utc, job_id, consent_id),
            )
            if updated.rowcount != 1:
                raise PermissionError("generation consent was already consumed")
            self._connection.execute(
                """
                INSERT INTO chat_generation_job_events(
                    job_id, state, revision, created_at_utc
                ) VALUES (?, 'pending', 0, ?)
                """,
                (job_id, now_utc),
            )
            row = self._connection.execute(
                "SELECT * FROM chat_generation_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            return dict(row), True

    def get_generation_job(self, job_id: str) -> dict[str, object]:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM chat_generation_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        if row is None:
            raise ValueError("generation job does not exist")
        return dict(row)

    def _daily_provider_request_count(self, day_start_utc: str, day_end_utc: str) -> int:
        return int(self._connection.execute(
            """SELECT
                (SELECT COUNT(*) FROM chat_generation_requests WHERE created_at_utc >= ? AND created_at_utc < ?)
                + (SELECT COUNT(*) FROM chat_agent_plan_requests WHERE created_at_utc >= ? AND created_at_utc < ?)
            """, (day_start_utc, day_end_utc, day_start_utc, day_end_utc),
        ).fetchone()[0])

    def reserve_agent_plan_request(self, *, request_record_id: str, provider: str, model: str,
        created_at_utc: str, day_start_utc: str, day_end_utc: str, daily_request_limit: int,
    ) -> None:
        """Reserve a body-free planning attempt under the same durable budget as generation."""
        with self._transaction():
            if self._daily_provider_request_count(day_start_utc, day_end_utc) >= daily_request_limit:
                raise PermissionError("daily provider request limit reached")
            self._connection.execute(
                """INSERT INTO chat_agent_plan_requests(
                    request_record_id, provider, model, status, created_at_utc
                ) VALUES (?, ?, ?, 'started', ?)""",
                (request_record_id, provider, model, created_at_utc),
            )

    def finish_agent_plan_request(self, request_record_id: str, *, status: str,
        prompt_tokens: int | None, completion_tokens: int | None, completed_at_utc: str,
    ) -> bool:
        if status not in {"completed", "failed", "cancelled"}:
            raise ValueError("unsupported agent request status")
        with self._transaction():
            changed = self._connection.execute(
                """UPDATE chat_agent_plan_requests SET status = ?, prompt_tokens = ?,
                    completion_tokens = ?, completed_at_utc = ?
                    WHERE request_record_id = ? AND status = 'started'""",
                (status, prompt_tokens, completion_tokens, completed_at_utc, request_record_id),
            ).rowcount
        return changed == 1

    def create_generation_request(
        self, *, request_record_id: str, job_id: str, provider: str, model: str,
        created_at_utc: str, day_start_utc: str, day_end_utc: str,
        daily_request_limit: int,
    ) -> dict[str, object]:
        with self._transaction():
            job = self._connection.execute(
                "SELECT state FROM chat_generation_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if job is None or job["state"] != "generating":
                raise ValueError("generation job cannot start a provider request")
            used_today = self._daily_provider_request_count(day_start_utc, day_end_utc)
            if used_today >= daily_request_limit:
                raise PermissionError("daily provider request limit reached")
            ordinal = int(self._connection.execute(
                "SELECT COALESCE(MAX(ordinal), 0) + 1 FROM chat_generation_requests WHERE job_id = ?",
                (job_id,),
            ).fetchone()[0])
            self._connection.execute(
                """
                INSERT INTO chat_generation_requests(
                    request_record_id, job_id, ordinal, provider, model, status,
                    price_status, created_at_utc
                ) VALUES (?, ?, ?, ?, ?, 'started', 'unavailable', ?)
                """,
                (request_record_id, job_id, ordinal, provider, model, created_at_utc),
            )
            row = self._connection.execute(
                "SELECT * FROM chat_generation_requests WHERE request_record_id = ?",
                (request_record_id,),
            ).fetchone()
            return dict(row)

    def finish_generation_request(
        self, request_record_id: str, *, status: str, provider_request_id: str | None,
        prompt_tokens: int | None, completion_tokens: int | None,
        completed_at_utc: str,
    ) -> bool:
        if status not in {"completed", "failed", "cancelled", "unknown"}:
            raise ValueError("unsupported provider request status")
        with self._transaction():
            changed = self._connection.execute(
                """
                UPDATE chat_generation_requests
                SET status = ?, provider_request_id = ?, prompt_tokens = ?,
                    completion_tokens = ?, completed_at_utc = ?
                WHERE request_record_id = ? AND status = 'started'
                """,
                (status, provider_request_id, prompt_tokens, completion_tokens,
                 completed_at_utc, request_record_id),
            ).rowcount
        return changed == 1

    def generation_request_records(self, job_id: str) -> tuple[dict[str, object], ...]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM chat_generation_requests
                WHERE job_id = ? ORDER BY ordinal
                """,
                (job_id,),
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def cas_generation_job(
        self,
        job_id: str,
        *,
        expected_state: str,
        expected_revision: int,
        new_state: str,
        updated_at_utc: str,
        **changes: object,
    ) -> bool:
        allowed = {
            "completed_chunks",
            "total_chunks",
            "cancellation_requested",
            "cache_hit",
            "evidence_status",
            "result_json",
            "error_code",
        }
        if not set(changes) <= allowed:
            raise ValueError("unsupported generation job update")
        assignments = ["state = ?", "updated_at_utc = ?", "revision = revision + 1"]
        parameters: list[object] = [new_state, updated_at_utc]
        for field, value in changes.items():
            assignments.append(f"{field} = ?")
            parameters.append(value)
        parameters.extend((job_id, expected_state, expected_revision))
        with self._transaction():
            updated = self._connection.execute(
                f"""
                UPDATE chat_generation_jobs SET {', '.join(assignments)}
                WHERE job_id = ? AND state = ? AND revision = ?
                """,
                tuple(parameters),
            )
            if updated.rowcount != 1:
                return False
            row = self._connection.execute(
                "SELECT revision, error_code FROM chat_generation_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            self._connection.execute(
                """
                INSERT INTO chat_generation_job_events(
                    job_id, state, revision, created_at_utc, error_code
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (job_id, new_state, row["revision"], updated_at_utc, row["error_code"]),
            )
            return True

    def nonterminal_generation_jobs(self) -> tuple[dict[str, object], ...]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM chat_generation_jobs
                WHERE state NOT IN ('complete', 'failed', 'cancelled')
                ORDER BY created_at_utc, job_id
                """
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def put_generation_cache(self, **kwargs) -> None:
        # Source validation and the write share the deletion lock. Otherwise a
        # validated in-memory result could recreate a cache after raw cleanup.
        with self._lock:
            self._put_generation_cache_locked(**kwargs)

    def _put_generation_cache_locked(
        self,
        *,
        cache_key: str,
        session_digest: str,
        snapshot_id: str,
        scope_fingerprint: str,
        identity_json: str,
        result_json: str,
        now_utc: str | None = None,
    ) -> None:
        """Idempotently store a verified derived result for one local session."""
        self.require_generation_sources(result_json, session_digest=session_digest, now_utc=now_utc)
        if snapshot_id.startswith("mixed:v2:"):
            from chat_to_howto.repositories.content import ContentRepository
            from chat_to_howto.services.evidence_validation import ValidatedMixedGeneration
            from chat_to_howto.services.generation_cache import GenerationCacheIdentity

            result = ValidatedMixedGeneration.model_validate_json(result_json)
            identity = GenerationCacheIdentity.model_validate_json(identity_json)
            if result.snapshot_id != snapshot_id or result.scope_fingerprint != scope_fingerprint or identity.snapshot_id != snapshot_id or identity.scope_fingerprint != scope_fingerprint:
                raise ValueError("mixed cache identity mismatch")
            for ref in result.source_refs:
                if ref.kind == "chat_snapshot":
                    self.generation_snapshot_messages(session_digest=session_digest, snapshot_id=ref.id)
                elif not ContentRepository(self).list_units(ref.id, session_digest=session_digest):
                    raise PermissionError("mixed cache source is unavailable")
        else:
            self.generation_snapshot_messages(
                session_digest=session_digest,
                snapshot_id=snapshot_id,
            )
        expected = (
            snapshot_id,
            scope_fingerprint,
            identity_json,
            result_json,
        )
        table = "chat_generation_cache_v2" if snapshot_id.startswith("mixed:v2:") else "chat_generation_cache"
        with self._transaction():
            self._connection.execute(
                f"""
                INSERT OR IGNORE INTO {table}(
                    cache_key, session_digest, snapshot_id, scope_fingerprint,
                    identity_json, result_json, created_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cache_key,
                    session_digest,
                    snapshot_id,
                    scope_fingerprint,
                    identity_json,
                    result_json,
                    self._now(),
                ),
            )
            row = self._connection.execute(
                f"""
                SELECT snapshot_id, scope_fingerprint, identity_json, result_json
                FROM {table}
                WHERE cache_key = ? AND session_digest = ?
                """,
                (cache_key, session_digest),
            ).fetchone()
            if row is None or tuple(row) != expected:
                raise ValueError("generation cache identity conflict")

    def create_document_from_generation(
        self,
        *,
        document_id: str,
        version_id: str,
        session_digest: str,
        source_generation_job_id: str,
        source_result_json: str,
        content_json: str,
        created_at_utc: str,
    ) -> dict[str, object]:
        """Create the immutable AI version once for one verified generation job."""
        with self._transaction():
            existing = self._connection.execute(
                "SELECT * FROM chat_documents WHERE source_generation_job_id = ?",
                (source_generation_job_id,),
            ).fetchone()
            if existing is not None:
                if existing["session_digest"] != session_digest:
                    raise PermissionError("document belongs to another session")
                return self._document_row(str(existing["document_id"]))
            job = self._connection.execute(
                "SELECT * FROM chat_generation_jobs WHERE job_id = ?",
                (source_generation_job_id,),
            ).fetchone()
            if (
                job is None
                or job["session_digest"] != session_digest
                or job["state"] != "complete"
                or job["evidence_status"] != "verified"
                or job["result_json"] != source_result_json
            ):
                raise PermissionError("verified generation job is unavailable")
            self.require_generation_sources(str(job["scope_json"]), session_digest=session_digest, now_utc=created_at_utc)
            self._connection.execute(
                """
                INSERT INTO chat_documents(
                    document_id, session_digest, source_generation_job_id,
                    current_version_id, source_result_json, created_at_utc,
                    updated_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    document_id,
                    session_digest,
                    source_generation_job_id,
                    version_id,
                    source_result_json,
                    created_at_utc,
                    created_at_utc,
                ),
            )
            self._connection.execute(
                """
                INSERT INTO chat_document_versions(
                    version_id, document_id, ordinal, origin,
                    parent_version_id, restored_from_version_id, content_json,
                    change_summary, created_at_utc
                ) VALUES (?, ?, 1, 'ai_generated', NULL, NULL, ?, '', ?)
                """,
                (version_id, document_id, content_json, created_at_utc),
            )
            return self._document_row(document_id)

    def _document_row(self, document_id: str) -> dict[str, object]:
        row = self._connection.execute(
            "SELECT * FROM chat_documents WHERE document_id = ?", (document_id,)
        ).fetchone()
        if row is None:
            raise ValueError("document does not exist")
        versions = self._connection.execute(
            """
            SELECT * FROM chat_document_versions
            WHERE document_id = ? ORDER BY ordinal
            """,
            (document_id,),
        ).fetchall()
        result = dict(row)
        result["versions"] = tuple(dict(item) for item in versions)
        return result

    def get_document(
        self, document_id: str, *, session_digest: str
    ) -> dict[str, object]:
        with self._lock:
            row = self._connection.execute(
                "SELECT session_digest FROM chat_documents WHERE document_id = ?",
                (document_id,),
            ).fetchone()
            if row is None:
                raise ValueError("document does not exist")
            if row["session_digest"] != session_digest:
                raise PermissionError("document belongs to another session")
            return self._document_row(document_id)

    def list_documents(
        self, *, session_digest: str, cursor: str | None = None, limit: int = 20
    ) -> tuple[tuple[dict[str, object], ...], str | None]:
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 50:
            raise ValueError("document recovery limit is invalid")
        cursor_filter = ""
        parameters: list[object] = [session_digest]
        if cursor is not None:
            updated_at_utc, document_id = _decode_document_cursor(cursor)
            cursor_filter = """
              AND (
                updated_at_utc < ?
                OR (updated_at_utc = ? AND document_id < ?)
              )
            """
            parameters.extend((updated_at_utc, updated_at_utc, document_id))
        parameters.append(limit + 1)
        with self._lock:
            rows = self._connection.execute(
                f"""
                SELECT document_id, updated_at_utc
                FROM chat_documents
                WHERE session_digest = ?
                  {cursor_filter}
                ORDER BY updated_at_utc DESC, document_id DESC
                LIMIT ?
                """,
                tuple(parameters),
            ).fetchall()
            page_rows = rows[:limit]
            documents = tuple(
                self._document_row(str(row["document_id"])) for row in page_rows
            )
            next_cursor = (
                _encode_document_cursor(
                    str(page_rows[-1]["updated_at_utc"]),
                    str(page_rows[-1]["document_id"]),
                )
                if len(rows) > limit and page_rows
                else None
            )
            return documents, next_cursor

    def append_document_version(
        self,
        *,
        document_id: str,
        version_id: str,
        session_digest: str,
        parent_version_id: str,
        restored_from_version_id: str | None,
        origin: str,
        content_json: str,
        change_summary: str,
        created_at_utc: str,
    ) -> dict[str, object]:
        with self._transaction():
            document = self._connection.execute(
                "SELECT * FROM chat_documents WHERE document_id = ?", (document_id,)
            ).fetchone()
            if document is None:
                raise ValueError("document does not exist")
            if document["session_digest"] != session_digest:
                raise PermissionError("document belongs to another session")
            if document["current_version_id"] != parent_version_id:
                raise ValueError("parent version is not current")
            parent = self._connection.execute(
                """
                SELECT ordinal FROM chat_document_versions
                WHERE version_id = ? AND document_id = ?
                """,
                (parent_version_id, document_id),
            ).fetchone()
            if parent is None:
                raise ValueError("parent version does not exist")
            if restored_from_version_id is not None:
                restored = self._connection.execute(
                    """
                    SELECT content_json FROM chat_document_versions
                    WHERE version_id = ? AND document_id = ?
                    """,
                    (restored_from_version_id, document_id),
                ).fetchone()
                if restored is None or restored["content_json"] != content_json:
                    raise ValueError("restored version does not match its source")
            ordinal = int(parent["ordinal"]) + 1
            self._connection.execute(
                """
                INSERT INTO chat_document_versions(
                    version_id, document_id, ordinal, origin,
                    parent_version_id, restored_from_version_id, content_json,
                    change_summary, created_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    version_id,
                    document_id,
                    ordinal,
                    origin,
                    parent_version_id,
                    restored_from_version_id,
                    content_json,
                    change_summary,
                    created_at_utc,
                ),
            )
            updated = self._connection.execute(
                """
                UPDATE chat_documents
                SET current_version_id = ?, updated_at_utc = ?
                WHERE document_id = ? AND current_version_id = ?
                """,
                (version_id, created_at_utc, document_id, parent_version_id),
            )
            if updated.rowcount != 1:
                raise ValueError("document current version changed")
            return self._document_row(document_id)

    def restore_document_version(
        self,
        *,
        document_id: str,
        version_id: str,
        restored_from_version_id: str,
        session_digest: str,
        created_at_utc: str,
    ) -> dict[str, object]:
        with self._lock:
            document = self.get_document(document_id, session_digest=session_digest)
            restored = next(
                (
                    item
                    for item in document["versions"]
                    if item["version_id"] == restored_from_version_id
                ),
                None,
            )
            if restored is None:
                raise ValueError("restored version does not exist")
            parent_version_id = str(document["current_version_id"])
            content_json = str(restored["content_json"])
        return self.append_document_version(
            document_id=document_id,
            version_id=version_id,
            session_digest=session_digest,
            parent_version_id=parent_version_id,
            restored_from_version_id=restored_from_version_id,
            origin="restored",
            content_json=content_json,
            change_summary=f"Restored version {restored['ordinal']}",
            created_at_utc=created_at_utc,
        )

    def record_document_artifact(
        self,
        *,
        artifact_id: str,
        session_digest: str,
        document_id: str,
        version_id: str,
        output_root_id: str,
        filename: str,
        format: str,
        source_mode: str,
        byte_size: int,
        sha256_digest: str,
        created_at_utc: str,
    ) -> dict[str, object]:
        with self._transaction():
            document = self._connection.execute(
                "SELECT session_digest FROM chat_documents WHERE document_id = ?",
                (document_id,),
            ).fetchone()
            version = self._connection.execute(
                """
                SELECT 1 FROM chat_document_versions
                WHERE document_id = ? AND version_id = ?
                """,
                (document_id, version_id),
            ).fetchone()
            if document is None or version is None:
                raise ValueError("document version does not exist")
            if document["session_digest"] != session_digest:
                raise PermissionError("document belongs to another session")
            self._connection.execute(
                """
                INSERT INTO chat_document_artifacts(
                    artifact_id, session_digest, document_id, version_id,
                    output_root_id, filename, format, source_mode, byte_size,
                    sha256, created_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    artifact_id,
                    session_digest,
                    document_id,
                    version_id,
                    output_root_id,
                    filename,
                    format,
                    source_mode,
                    byte_size,
                    sha256_digest,
                    created_at_utc,
                ),
            )
            row = self._connection.execute(
                "SELECT * FROM chat_document_artifacts WHERE artifact_id = ?",
                (artifact_id,),
            ).fetchone()
            return dict(row)

    def document_artifacts(
        self, document_id: str, *, session_digest: str
    ) -> tuple[dict[str, object], ...]:
        with self._lock:
            document = self._connection.execute(
                "SELECT session_digest FROM chat_documents WHERE document_id = ?",
                (document_id,),
            ).fetchone()
            if document is None:
                raise ValueError("document does not exist")
            if document["session_digest"] != session_digest:
                raise PermissionError("document belongs to another session")
            rows = self._connection.execute(
                """
                SELECT * FROM chat_document_artifacts
                WHERE document_id = ? ORDER BY created_at_utc, artifact_id
                """,
                (document_id,),
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def create_deletion_plan(
        self,
        *,
        plan_id: str,
        session_digest: str,
        target_kind: str,
        target_id: str,
        include_managed_files: bool,
        impact_json: str,
        fingerprint: str,
        created_at_utc: str,
        expires_at_utc: str,
        preserve_documents: bool = False,
    ) -> None:
        with self._transaction():
            self._connection.execute(
                """
                INSERT INTO chat_deletion_plans(
                    plan_id, session_digest, target_kind, target_id,
                    include_managed_files, impact_json, fingerprint,
                    created_at_utc, expires_at_utc, state, preserve_documents
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'planned', ?)
                """,
                (plan_id, session_digest, target_kind, target_id,
                 int(include_managed_files), impact_json, fingerprint,
                 created_at_utc, expires_at_utc, int(preserve_documents)),
            )

    def get_deletion_plan(self, plan_id: str) -> dict[str, object]:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM chat_deletion_plans WHERE plan_id = ?", (plan_id,)
            ).fetchone()
        if row is None:
            raise ValueError("deletion plan does not exist")
        return dict(row)

    def deletion_impact(
        self, *, target_kind: str, target_id: str, session_digest: str, preserve_documents: bool = False
    ) -> dict[str, object]:
        with self._lock:
            return self._deletion_impact_locked(
                target_kind=target_kind,
                target_id=target_id,
                session_digest=session_digest, preserve_documents=preserve_documents,
            )

    def _deletion_impact_locked(
        self, *, target_kind: str, target_id: str, session_digest: str, preserve_documents: bool = False
    ) -> dict[str, object]:
        content_batch_ids: list[str] = []
        content_unit_ids: list[str] = []
        private_images: list[dict[str, str]] = []
        if target_kind == "document":
            document = self._connection.execute(
                "SELECT * FROM chat_documents WHERE document_id = ?", (target_id,)
            ).fetchone()
            if document is None:
                raise ValueError("deletion target does not exist")
            if document["session_digest"] != session_digest:
                raise PermissionError("deletion target belongs to another session")
            document_ids = [target_id]
            generation_job_ids = [str(document["source_generation_job_id"])] if self._connection.execute(
                "SELECT 1 FROM chat_generation_jobs WHERE job_id=?", (document["source_generation_job_id"],)
            ).fetchone() is not None else []
            batch_ids: list[str] = []
            snapshot_ids: list[str] = []
            message_ids: list[str] = []
            shared_message_count = 0
            source_files: list[dict[str, str]] = []
            manual_import_ids: list[str] = []
        elif target_kind == "batch":
            owned_export = self._connection.execute(
                """
                SELECT 1 FROM chat_export_jobs
                WHERE (batch_id = ? OR planned_batch_id = ?) AND session_digest = ?
                """,
                (target_id, target_id, session_digest),
            ).fetchone()
            manual_import_ids = [
                str(row["import_id"])
                for row in self._connection.execute(
                    """
                    SELECT import_id FROM chat_manual_imports
                    WHERE batch_id = ? AND session_digest = ? AND state = 'complete'
                    """,
                    (target_id, session_digest),
                ).fetchall()
            ]
            if owned_export is None and not manual_import_ids:
                raise PermissionError("deletion target is unavailable")
            batch = self._connection.execute(
                "SELECT scope_json FROM chat_export_batches WHERE batch_id = ?",
                (target_id,),
            ).fetchone()
            if batch is None:
                raise ValueError("deletion target does not exist")
            batch_ids = [target_id]
            snapshot_ids = [str(row["snapshot_id"]) for row in self._connection.execute(
                "SELECT snapshot_id FROM chat_snapshots WHERE batch_id = ? ORDER BY snapshot_id",
                (target_id,),
            ).fetchall()]
            # A crash before snapshot sealing may leave only batch membership.
            # Include both stores and retain identities still owned elsewhere.
            candidates_sql = """
                SELECT message_id FROM chat_batch_messages WHERE batch_id = ?
                UNION
                SELECT m.message_id FROM chat_snapshot_messages m
                JOIN chat_snapshots s ON s.snapshot_id=m.snapshot_id WHERE s.batch_id = ?
            """
            candidate_message_ids = self._connection.execute(candidates_sql, (target_id, target_id)).fetchall()
            message_ids = [str(row["message_id"]) for row in self._connection.execute(
                f"""WITH candidates AS ({candidates_sql})
                SELECT message_id FROM candidates c
                WHERE NOT EXISTS (
                    SELECT 1 FROM chat_batch_messages b WHERE b.message_id=c.message_id AND b.batch_id != ?
                ) AND NOT EXISTS (
                    SELECT 1 FROM chat_snapshot_messages m JOIN chat_snapshots s ON s.snapshot_id=m.snapshot_id
                    WHERE m.message_id=c.message_id AND s.batch_id != ?
                ) ORDER BY message_id""",
                (target_id, target_id, target_id, target_id),
            ).fetchall()]
            shared_message_count = len(candidate_message_ids) - len(message_ids)
            generation_job_ids = []
            for row in self._connection.execute(
                "SELECT job_id, scope_json FROM chat_generation_jobs WHERE session_digest = ?",
                (session_digest,),
            ).fetchall():
                try:
                    scope = json.loads(str(row["scope_json"]))
                except (TypeError, ValueError):
                    continue
                refs = scope.get("source_refs", []) if isinstance(scope, dict) else []
                mixed_ref = any(isinstance(ref, dict) and ref.get("kind") == "chat_snapshot" and ref.get("id") in snapshot_ids for ref in refs) if isinstance(refs, list) else False
                if isinstance(scope, dict) and (scope.get("snapshot_id") in snapshot_ids or mixed_ref):
                    generation_job_ids.append(str(row["job_id"]))
            if generation_job_ids:
                placeholders = ",".join("?" for _ in generation_job_ids)
                document_ids = [str(row["document_id"]) for row in self._connection.execute(
                    f"SELECT document_id FROM chat_documents WHERE source_generation_job_id IN ({placeholders}) ORDER BY document_id",
                    tuple(generation_job_ids),
                ).fetchall()]
            else:
                document_ids = []
            source_files = []
            if not manual_import_ids:
                try:
                    scope = json.loads(str(batch["scope_json"]))
                    root_id = scope.get("output_root_id") if isinstance(scope, dict) else None
                    if isinstance(root_id, str):
                        source_files.append({"root_id": root_id, "parent": "exports", "name": target_id, "kind": "directory"})
                except (TypeError, ValueError):
                    pass
        elif target_kind == "content_batch":
            owned = self._connection.execute(
                "SELECT 1 FROM content_batches WHERE batch_id = ? AND session_digest = ?",
                (target_id, session_digest),
            ).fetchone()
            if owned is None:
                raise PermissionError("deletion target is unavailable")
            content_batch_ids = [target_id]
            content_unit_ids = [str(row["unit_id"]) for row in self._connection.execute(
                "SELECT unit_id FROM content_units WHERE batch_id = ? ORDER BY unit_id", (target_id,),
            ).fetchall()]
            private_images = [{"root_id": "private-content-images", "parent": ".", "name": str(row["basename"]), "kind": "file"}
                for row in self._connection.execute("SELECT basename FROM content_images WHERE batch_id = ? ORDER BY image_id", (target_id,)).fetchall()]
            batch_ids = []; snapshot_ids = []; message_ids = []; shared_message_count = 0
            manual_import_ids = []; source_files = []
            generation_job_ids = []
            for row in self._connection.execute(
                "SELECT job_id, scope_json FROM chat_generation_jobs WHERE session_digest = ?", (session_digest,),
            ).fetchall():
                try:
                    scope = json.loads(str(row["scope_json"]))
                except (TypeError, ValueError):
                    continue
                refs = scope.get("source_refs", []) if isinstance(scope, dict) else []
                if isinstance(refs, list) and any(isinstance(ref, dict) and ref.get("kind") == "content_batch" and ref.get("id") == target_id for ref in refs):
                    generation_job_ids.append(str(row["job_id"]))
            if generation_job_ids:
                placeholders = ",".join("?" for _ in generation_job_ids)
                document_ids = [str(row["document_id"]) for row in self._connection.execute(
                    f"SELECT document_id FROM chat_documents WHERE source_generation_job_id IN ({placeholders}) ORDER BY document_id",
                    tuple(generation_job_ids),
                ).fetchall()]
            else:
                document_ids = []
        else:
            raise ValueError("unsupported deletion target")

        retained_document_ids = sorted(document_ids) if preserve_documents else []
        if preserve_documents:
            document_ids = []
        version_ids: list[str] = []
        document_files: list[dict[str, str]] = []
        if document_ids:
            placeholders = ",".join("?" for _ in document_ids)
            version_ids = [str(row["version_id"]) for row in self._connection.execute(
                f"SELECT version_id FROM chat_document_versions WHERE document_id IN ({placeholders}) ORDER BY document_id, ordinal",
                tuple(document_ids),
            ).fetchall()]
            document_files = [
                {"root_id": str(row["output_root_id"]), "parent": "documents", "name": str(row["filename"]), "kind": "file"}
                for row in self._connection.execute(
                    f"SELECT output_root_id, filename FROM chat_document_artifacts WHERE document_id IN ({placeholders}) ORDER BY artifact_id",
                    tuple(document_ids),
                ).fetchall()
            ]
        generation_consent_ids: list[str] = []
        generation_preview_ids: list[str] = []
        generation_request_ids: list[str] = []
        cache_keys: list[str] = []
        if generation_job_ids:
            placeholders = ",".join("?" for _ in generation_job_ids)
            generation_request_ids = [str(row["request_record_id"]) for row in self._connection.execute(
                f"SELECT request_record_id FROM chat_generation_requests WHERE job_id IN ({placeholders}) ORDER BY job_id, ordinal",
                tuple(generation_job_ids),
            ).fetchall()]
            jobs = self._connection.execute(
                f"SELECT consent_id, cache_identity_json FROM chat_generation_jobs WHERE job_id IN ({placeholders})",
                tuple(generation_job_ids),
            ).fetchall()
            generation_consent_ids = sorted({str(row["consent_id"]) for row in jobs})
            for row in jobs:
                try:
                    identity = json.loads(str(row["cache_identity_json"]))
                    if isinstance(identity, dict) and isinstance(identity.get("cache_key"), str):
                        cache_keys.append(identity["cache_key"])
                except (TypeError, ValueError):
                    pass
            if generation_consent_ids:
                placeholders = ",".join("?" for _ in generation_consent_ids)
                generation_preview_ids = sorted({str(row["preview_id"]) for row in self._connection.execute(
                    f"SELECT preview_id FROM chat_generation_consents WHERE consent_id IN ({placeholders})",
                    tuple(generation_consent_ids),
                ).fetchall()})
        if target_kind in {"batch", "content_batch"}:
            for row in self._connection.execute(
                "SELECT preview_id, cache_identity_json, scope_json FROM chat_generation_previews WHERE session_digest = ?",
                (session_digest,),
            ).fetchall():
                try:
                    scope = json.loads(str(row["scope_json"]))
                    refs = scope.get("source_refs", []) if isinstance(scope, dict) else []
                    hit = (target_kind == "batch" and (
                        scope.get("snapshot_id") in snapshot_ids or
                        any(isinstance(ref, dict) and ref.get("kind") == "chat_snapshot" and ref.get("id") in snapshot_ids for ref in refs)
                    )) or (target_kind == "content_batch" and any(
                        isinstance(ref, dict) and ref.get("kind") == "content_batch" and ref.get("id") == target_id for ref in refs
                    ))
                    if hit:
                        generation_preview_ids.append(str(row["preview_id"]))
                        identity = json.loads(str(row["cache_identity_json"]))
                        if isinstance(identity, dict) and isinstance(identity.get("cache_key"), str):
                            cache_keys.append(identity["cache_key"])
                except (TypeError, ValueError):
                    continue
            generation_preview_ids = sorted(set(generation_preview_ids))
            if generation_preview_ids:
                placeholders = ",".join("?" for _ in generation_preview_ids)
                generation_consent_ids = sorted(set(generation_consent_ids) | {
                    str(row["consent_id"]) for row in self._connection.execute(
                        f"SELECT consent_id FROM chat_generation_consents WHERE preview_id IN ({placeholders})",
                        tuple(generation_preview_ids),
                    ).fetchall()
                })
        export_job_ids: list[str] = []
        export_consent_ids: list[str] = []
        if batch_ids:
            export_jobs = self._connection.execute(
                "SELECT job_id, consent_id FROM chat_export_jobs WHERE (batch_id = ? OR planned_batch_id = ?) AND session_digest = ?",
                (batch_ids[0], batch_ids[0], session_digest),
            ).fetchall()
            export_job_ids = [str(row["job_id"]) for row in export_jobs]
            export_consent_ids = sorted({str(row["consent_id"]) for row in export_jobs if row["consent_id"] is not None})
        return {
            "target_kind": target_kind,
            "target_id": target_id,
            "retained_document_ids": retained_document_ids,
            "document_ids": sorted(document_ids),
            "version_ids": sorted(version_ids),
            "generation_job_ids": sorted(generation_job_ids),
            "generation_request_ids": generation_request_ids,
            "generation_consent_ids": generation_consent_ids,
            "generation_preview_ids": generation_preview_ids,
            "cache_keys": sorted(set(cache_keys)),
            "batch_ids": batch_ids,
            "snapshot_ids": sorted(snapshot_ids),
            "message_ids": sorted(message_ids),
            "shared_message_count": shared_message_count,
            "export_job_ids": sorted(export_job_ids),
            "export_consent_ids": export_consent_ids,
            "manual_import_ids": manual_import_ids,
            "content_batch_ids": content_batch_ids,
            "content_unit_ids": content_unit_ids,
            "private_images": private_images,
            "managed_files": document_files + source_files,
        }

    def delete_planned_impact(
        self, *, plan_id: str, session_digest: str, fingerprint: str,
        expected_impact_json: str, confirmed_at_utc: str, staged_files_json: str | None,
    ) -> dict[str, object]:
        with self._transaction():
            plan = self._connection.execute(
                "SELECT * FROM chat_deletion_plans WHERE plan_id = ?", (plan_id,)
            ).fetchone()
            if plan is None or plan["session_digest"] != session_digest or plan["state"] != "planned":
                raise PermissionError("deletion plan is unavailable")
            if plan["fingerprint"] != fingerprint or plan["impact_json"] != expected_impact_json:
                raise ValueError("deletion plan changed")
            current = self._deletion_impact_locked(
                target_kind=str(plan["target_kind"]), target_id=str(plan["target_id"]),
                session_digest=session_digest, preserve_documents=bool(plan["preserve_documents"]),
            )
            current_json = json.dumps(current, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            if current_json != expected_impact_json:
                raise ValueError("deletion target changed")
            impact = current
            self._detach_document_evidence(impact["retained_document_ids"])
            self._delete_ids("chat_document_artifacts", "document_id", impact["document_ids"])
            self._delete_ids("chat_documents", "document_id", impact["document_ids"])
            self._delete_ids("chat_generation_job_events", "job_id", impact["generation_job_ids"])
            self._delete_ids("chat_generation_jobs", "job_id", impact["generation_job_ids"])
            self._delete_ids("chat_generation_consents", "consent_id", impact["generation_consent_ids"])
            self._delete_ids("chat_generation_previews", "preview_id", impact["generation_preview_ids"])
            if impact["cache_keys"]:
                placeholders = ",".join("?" for _ in impact["cache_keys"])
                self._connection.execute(
                    f"DELETE FROM chat_generation_cache WHERE session_digest = ? AND cache_key IN ({placeholders})",
                    (session_digest, *impact["cache_keys"]),
                )
                self._connection.execute(
                    f"DELETE FROM chat_generation_cache_v2 WHERE session_digest = ? AND cache_key IN ({placeholders})",
                    (session_digest, *impact["cache_keys"]),
                )
            self._delete_ids("chat_export_job_events", "job_id", impact["export_job_ids"])
            self._delete_ids("chat_export_jobs", "job_id", impact["export_job_ids"])
            self._delete_ids("chat_export_consents", "consent_id", impact["export_consent_ids"])
            self._delete_ids("chat_manual_imports", "import_id", impact["manual_import_ids"])
            self._delete_ids("chat_search_runs", "snapshot_id", impact["snapshot_ids"])
            if impact["snapshot_ids"]:
                self._connection.execute("DROP TRIGGER IF EXISTS chat_snapshot_messages_immutable_delete")
                self._connection.execute("DROP TRIGGER IF EXISTS chat_snapshots_immutable_delete")
                self._delete_ids("chat_snapshot_messages", "snapshot_id", impact["snapshot_ids"])
                self._delete_ids("chat_snapshots", "snapshot_id", impact["snapshot_ids"])
                self._connection.execute("""
                    CREATE TRIGGER chat_snapshots_immutable_delete BEFORE DELETE ON chat_snapshots
                    BEGIN SELECT RAISE(ABORT, 'immutable snapshot'); END
                """)
                self._connection.execute("""
                    CREATE TRIGGER chat_snapshot_messages_immutable_delete BEFORE DELETE ON chat_snapshot_messages
                    BEGIN SELECT RAISE(ABORT, 'immutable snapshot'); END
                """)
            self._delete_ids("chat_batch_messages", "batch_id", impact["batch_ids"])
            self._delete_ids("chat_batch_conversations", "batch_id", impact["batch_ids"])
            self._delete_ids("chat_export_batches", "batch_id", impact["batch_ids"])
            self._delete_ids("content_units", "unit_id", impact["content_unit_ids"])
            self._delete_ids("content_images", "batch_id", impact["content_batch_ids"])
            self._delete_ids("content_batches", "batch_id", impact["content_batch_ids"])
            if impact["message_ids"]:
                placeholders = ",".join("?" for _ in impact["message_ids"])
                self._connection.execute(
                    f"DELETE FROM chat_messages WHERE message_id IN ({placeholders}) AND NOT EXISTS (SELECT 1 FROM chat_batch_messages membership WHERE membership.message_id = chat_messages.message_id) AND NOT EXISTS (SELECT 1 FROM chat_snapshot_messages snapshot WHERE snapshot.message_id = chat_messages.message_id)",
                    tuple(impact["message_ids"]),
                )
            self._connection.execute("DELETE FROM chat_conversations WHERE NOT EXISTS (SELECT 1 FROM chat_messages message WHERE message.source_id = chat_conversations.source_id AND message.account_id = chat_conversations.account_id AND message.conversation_id = chat_conversations.conversation_id)")
            self._connection.execute("DELETE FROM chat_accounts WHERE NOT EXISTS (SELECT 1 FROM chat_conversations conversation WHERE conversation.source_id = chat_accounts.source_id AND conversation.account_id = chat_accounts.account_id) AND NOT EXISTS (SELECT 1 FROM chat_export_batches batch WHERE batch.source_id = chat_accounts.source_id AND batch.account_id = chat_accounts.account_id)")
            self._connection.execute("DELETE FROM chat_sources WHERE NOT EXISTS (SELECT 1 FROM chat_accounts account WHERE account.source_id = chat_sources.source_id)")
            result = {"deleted": {
                "documents": len(impact["document_ids"]),
                "versions": len(impact["version_ids"]),
                "document_artifacts": len([item for item in impact["managed_files"] if item["kind"] == "file"]),
                "generation_jobs": len(impact["generation_job_ids"]),
                "generation_requests": len(impact["generation_request_ids"]),
                "generation_consents": len(impact["generation_consent_ids"]),
                "generation_previews": len(impact["generation_preview_ids"]),
                "cache_entries": len(impact["cache_keys"]),
                "export_batches": len(impact["batch_ids"]),
                "export_jobs": len(impact["export_job_ids"]),
                "export_consents": len(impact["export_consent_ids"]),
                "snapshots": len(impact["snapshot_ids"]),
                "messages": len(impact["message_ids"]),
                "managed_files": len(impact["managed_files"]),
                "content_batches": len(impact["content_batch_ids"]),
                "content_units": len(impact["content_unit_ids"]),
                "content_images": len(impact["private_images"]),
            }}
            self._connection.execute(
                """
                UPDATE chat_deletion_plans SET confirmed_at_utc = ?, state = 'cleanup_pending',
                    staged_files_json = ?, result_json = ? WHERE plan_id = ?
                """,
                (confirmed_at_utc, staged_files_json,
                 json.dumps(result, sort_keys=True, separators=(",", ":")), plan_id),
            )
            return result

    def _delete_ids(self, table: str, column: str, values: list[str]) -> None:
        allowed = {
            ("chat_document_artifacts", "document_id"), ("chat_documents", "document_id"),
            ("chat_generation_job_events", "job_id"), ("chat_generation_jobs", "job_id"),
            ("chat_generation_consents", "consent_id"), ("chat_generation_previews", "preview_id"),
            ("chat_export_job_events", "job_id"), ("chat_export_jobs", "job_id"),
            ("chat_export_consents", "consent_id"), ("chat_search_runs", "snapshot_id"),
            ("chat_manual_imports", "import_id"),
            ("chat_snapshot_messages", "snapshot_id"), ("chat_snapshots", "snapshot_id"),
            ("chat_batch_messages", "batch_id"), ("chat_batch_conversations", "batch_id"),
            ("chat_export_batches", "batch_id"),
            ("content_units", "unit_id"), ("content_images", "batch_id"),
            ("content_batches", "batch_id"),
        }
        if (table, column) not in allowed:
            raise ValueError("unsupported deletion table")
        if not values:
            return
        placeholders = ",".join("?" for _ in values)
        self._connection.execute(
            f"DELETE FROM {table} WHERE {column} IN ({placeholders})", tuple(values)
        )

    def record_deletion_staging(self, plan_id: str, staged_files_json: str | None) -> None:
        """Write-ahead journal for file renames before the database commit."""
        with self._transaction():
            self._connection.execute("UPDATE chat_deletion_plans SET staged_files_json=? WHERE plan_id=? AND state='planned'",
                (staged_files_json, plan_id))

    def interrupted_deletion_staging(self) -> tuple[dict[str, object], ...]:
        with self._lock:
            return tuple(dict(row) for row in self._connection.execute(
                "SELECT plan_id,staged_files_json FROM chat_deletion_plans WHERE state='planned' AND staged_files_json IS NOT NULL"))

    def complete_deletion_cleanup(self, plan_id: str, *, success: bool) -> None:
        with self._transaction():
            self._connection.execute(
                "UPDATE chat_deletion_plans SET state = ? WHERE plan_id = ? AND state = 'cleanup_pending'",
                ("complete" if success else "cleanup_pending", plan_id),
            )

    def pending_deletion_cleanups(self) -> tuple[dict[str, object], ...]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT plan_id, staged_files_json
                FROM chat_deletion_plans
                WHERE state = 'cleanup_pending'
                ORDER BY confirmed_at_utc, plan_id
                """
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def _detach_document_evidence(self, document_ids: list[str]) -> None:
        from chat_to_howto.domain.generation import GenerationResult
        for document_id in document_ids:
            row = self._connection.execute("SELECT source_result_json FROM chat_documents WHERE document_id=?", (document_id,)).fetchone()
            if row is None:
                continue
            source = json.loads(str(row[0]))
            source["evidence"] = []
            source["source_extraction_ids"] = []
            source["result"] = GenerationResult.insufficient().model_dump(mode="json")
            self._connection.execute("UPDATE chat_documents SET source_result_json=?,source_deleted=1 WHERE document_id=?",
                (json.dumps(source, ensure_ascii=False, separators=(",", ":")), document_id))

    def raw_retention_targets(self) -> tuple[dict[str, object], ...]:
        """Ownership survives failed indexing and loss of a job's bound snapshot."""
        with self._lock:
            rows = self._connection.execute("""
                SELECT 'batch' AS target_kind, b.batch_id AS target_id,
                       COALESCE(m.session_digest,j.session_digest) AS session_digest,
                       COALESCE(m.created_at_utc,j.created_at_utc,b.created_at_utc) AS created_at_utc
                FROM chat_export_batches b
                LEFT JOIN chat_manual_imports m ON m.batch_id=b.batch_id
                LEFT JOIN chat_export_jobs j ON j.batch_id=b.batch_id OR j.planned_batch_id=b.batch_id
                WHERE COALESCE(m.session_digest,j.session_digest) IS NOT NULL
                UNION
                SELECT 'content_batch',batch_id,session_digest,created_at_utc FROM content_batches
            """).fetchall()
            return tuple(dict(row) for row in rows)

    def retire_raw(self, *, target_kind: str, target_id: str, session_digest: str, created_at_utc: str) -> tuple[str, ...]:
        """Revoke access before touching files; failed disk cleanup remains closed."""
        with self._transaction():
            impact = self._deletion_impact_locked(target_kind=target_kind, target_id=target_id,
                session_digest=session_digest, preserve_documents=True)
            self._connection.execute("INSERT OR IGNORE INTO chat_raw_retirements VALUES(?,?,?,?)",
                (target_kind, target_id, session_digest, created_at_utc))
            self._detach_document_evidence(impact["retained_document_ids"])
            for job_id in impact["generation_job_ids"]:
                self._connection.execute("UPDATE chat_generation_jobs SET cancellation_requested=1 WHERE job_id=?", (job_id,))
            return tuple(impact["generation_job_ids"])

    def retired_export_temp_owners(self) -> tuple[dict[str, object], ...]:
        # Legacy v1 rows do not identify an approved owner and must be ignored.
        with self._lock:
            rows = self._connection.execute("""SELECT j.planned_batch_id AS batch_id,j.scope_json,j.created_at_utc,
                       NOT EXISTS (SELECT 1 FROM chat_export_batches b WHERE b.batch_id=j.planned_batch_id) AS unbound
                FROM chat_export_jobs j WHERE j.planned_batch_id IS NOT NULL AND j.session_digest IS NOT NULL
                  AND j.state IN ('complete','partial','failed','cancelled')""").fetchall()
            return tuple(dict(row) for row in rows)

    def has_active_source_jobs(self, source_id: str) -> bool:
        with self._lock:
            export_rows = self._connection.execute(
                """
                SELECT scope_json FROM chat_export_jobs
                WHERE state NOT IN ('complete', 'partial', 'failed', 'cancelled')
                """,
            ).fetchall()
            generation_rows = self._connection.execute(
                """
                SELECT scope_json FROM chat_generation_jobs
                WHERE state NOT IN ('complete', 'failed', 'cancelled')
                """
            ).fetchall()
        for row in (*export_rows, *generation_rows):
            try:
                scope = json.loads(str(row["scope_json"]))
            except (TypeError, ValueError):
                return True
            if isinstance(scope, dict) and scope.get("source_id") == source_id:
                return True
        return False

    def search_messages(
        self,
        *,
        session_digest: str,
        query: str,
        conversation_ids: tuple[str, ...],
        snapshot_id: str | None,
        start_time_utc: str,
        end_time_utc: str,
        cursor: tuple[str, str] | tuple[int, str] | None,
        limit: int,
        include_undated: bool = False,
    ) -> tuple[dict[str, object], ...]:
        """Return one row per stable identity from session-owned sealed snapshots.

        ``instr`` deliberately provides literal substring semantics, so SQL wildcard
        characters have no special meaning. Every external value is bound.
        """
        if not conversation_ids or not 1 <= limit <= 101:
            raise ValueError("invalid search bounds")
        placeholders = ",".join("?" for _ in conversation_ids)
        cursor_timestamp, cursor_message_id = cursor or (None, None)
        parameters: list[object] = [
            session_digest,
            session_digest,
            snapshot_id,
            snapshot_id,
            *conversation_ids,
            int(include_undated),
            start_time_utc,
            end_time_utc,
            query,
            query,
            cursor_timestamp,
            cursor_timestamp,
            cursor_timestamp,
            cursor_message_id,
            limit,
        ]
        time_condition = "((? = 1 AND message.sent_at_utc IS NULL) OR (message.sent_at_utc >= ? AND message.sent_at_utc < ?))"
        order_value = "COALESCE(source_order, 9223372036854775807)" if include_undated else "sent_at_utc"
        comparison = ">" if include_undated else "<"
        direction = "ASC" if include_undated else "DESC"
        with self._lock:
            rows = self._connection.execute(
                f"""
                WITH authorized_snapshots AS (
                    SELECT snapshot_id, batch_id
                    FROM chat_export_jobs
                    WHERE session_digest = ? AND searchable = 1 AND state = 'complete'
                      AND NOT EXISTS (SELECT 1 FROM chat_raw_retirements retired WHERE retired.target_kind='batch' AND retired.target_id=chat_export_jobs.batch_id)
                    UNION
                    SELECT snapshot_id, batch_id
                    FROM chat_manual_imports
                    WHERE session_digest = ? AND state = 'complete'
                      AND NOT EXISTS (SELECT 1 FROM chat_raw_retirements retired WHERE retired.target_kind='batch' AND retired.target_id=chat_manual_imports.batch_id)
                ), eligible AS (
                    SELECT
                        message.message_id,
                        message.conversation_id,
                        message.sent_at_utc,
                        message.source_order,
                        message.sender_id,
                        message.sender_name,
                        message.text,
                        snapshot.batch_id,
                        snapshot.snapshot_id,
                        ROW_NUMBER() OVER (
                            PARTITION BY message.message_id
                            ORDER BY snapshot.created_at_utc DESC,
                                     snapshot.snapshot_id DESC,
                                     snapshot.batch_id DESC
                        ) AS duplicate_rank
                    FROM authorized_snapshots AS authorized
                    JOIN chat_snapshots AS snapshot
                      ON snapshot.snapshot_id = authorized.snapshot_id
                     AND snapshot.batch_id = authorized.batch_id
                    JOIN chat_snapshot_messages AS membership
                      ON membership.snapshot_id = snapshot.snapshot_id
                    JOIN chat_messages AS message
                      ON message.message_id = membership.message_id
                    WHERE snapshot.sealed = 1
                      AND (? IS NULL OR snapshot.snapshot_id = ?)
                      AND message.conversation_id IN ({placeholders})
                      AND {time_condition}
                      AND (? = '' OR instr(lower(COALESCE(message.text, '')), lower(?)) > 0)
                )
                SELECT message_id, conversation_id, sent_at_utc, source_order,
                       sender_id, sender_name, text, batch_id, snapshot_id
                FROM eligible
                WHERE duplicate_rank = 1
                  AND (
                    ? IS NULL
                    OR {order_value} {comparison} ?
                    OR ({order_value} = ? AND message_id > ?)
                  )
                ORDER BY {order_value} {direction}, message_id ASC
                LIMIT ?
                """,
                tuple(parameters),
            ).fetchall()
        return tuple(dict(row) for row in rows)


def json_or_none(value: tuple[AttachmentPlaceholder, ...] | None) -> str | None:
    """Store nullable metadata as JSON without introducing synthetic values."""
    if value is None:
        return None
    items = [item.model_dump(mode="json") for item in value]
    return json.dumps(items, sort_keys=True, separators=(",", ":"))
