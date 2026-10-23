"""Versioned, concurrent-safe SQLite schema migrations for P1."""

from __future__ import annotations

from collections.abc import Callable
import errno
import os
from pathlib import Path
import sqlite3
import stat
import time


BUSY_TIMEOUT_MS = 5_000


def _prepare_database_file(path: Path) -> None:
    """Create or restrict the application-owned database without following a symlink."""
    if str(path) == ":memory:":
        return
    no_follow = os.O_NOFOLLOW if hasattr(os, "O_NOFOLLOW") else 0
    flags = os.O_CREAT | os.O_EXCL | os.O_RDWR | no_follow
    descriptor: int
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError:
        if path.is_symlink():
            raise ValueError("database path must be a regular file")
        existing_flags = os.O_RDONLY | no_follow
        if hasattr(os, "O_NONBLOCK"):
            existing_flags |= os.O_NONBLOCK
        try:
            descriptor = os.open(path, existing_flags)
        except OSError as error:
            if error.errno == errno.ELOOP:
                raise ValueError("database path must be a regular file") from None
            raise
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("database path must be a regular file")
        os.fchmod(descriptor, 0o600)
    finally:
        os.close(descriptor)


def open_connection(path: Path) -> sqlite3.Connection:
    """Open one local SQLite connection with P1's required safety settings."""
    database_path = Path(path)
    _prepare_database_file(database_path)
    connection = sqlite3.connect(
        database_path,
        timeout=BUSY_TIMEOUT_MS / 1000,
        check_same_thread=False,
    )
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    deadline = time.monotonic() + BUSY_TIMEOUT_MS / 1000
    while True:
        try:
            connection.execute("PRAGMA journal_mode = WAL")
            break
        except sqlite3.OperationalError as error:
            if "locked" not in str(error).lower() or time.monotonic() >= deadline:
                connection.close()
                raise
            time.sleep(0.01)
    _prepare_database_file(database_path)
    return connection


# The actual first P1 schema is intentionally immutable. Earlier local databases
# recorded version 1 with exactly this shape, so richer storage belongs in v2.
_MIGRATION_1 = (
    "CREATE TABLE chat_sources (source_id TEXT PRIMARY KEY)",
    """
    CREATE TABLE chat_accounts (
        source_id TEXT NOT NULL,
        account_id TEXT NOT NULL,
        PRIMARY KEY (source_id, account_id),
        FOREIGN KEY (source_id) REFERENCES chat_sources(source_id)
    )
    """,
    """
    CREATE TABLE chat_conversations (
        source_id TEXT NOT NULL,
        account_id TEXT NOT NULL,
        conversation_id TEXT NOT NULL,
        PRIMARY KEY (source_id, account_id, conversation_id),
        FOREIGN KEY (source_id, account_id)
            REFERENCES chat_accounts(source_id, account_id)
    )
    """,
    """
    CREATE TABLE chat_export_batches (
        batch_id TEXT PRIMARY KEY,
        source_id TEXT NOT NULL,
        account_id TEXT NOT NULL,
        created_at_utc TEXT NOT NULL,
        FOREIGN KEY (source_id, account_id)
            REFERENCES chat_accounts(source_id, account_id)
    )
    """,
    """
    CREATE TABLE chat_messages (
        message_id TEXT PRIMARY KEY,
        source_id TEXT NOT NULL,
        account_id TEXT NOT NULL,
        conversation_id TEXT NOT NULL,
        source_message_id TEXT,
        source_file_id TEXT,
        page_slice_id TEXT,
        source_order INTEGER,
        original_timestamp TEXT,
        text TEXT NOT NULL,
        created_at_utc TEXT NOT NULL,
        CHECK (source_order IS NULL OR typeof(source_order) = 'integer'),
        UNIQUE (source_id, account_id, conversation_id, source_message_id),
        FOREIGN KEY (source_id, account_id, conversation_id)
            REFERENCES chat_conversations(source_id, account_id, conversation_id)
    )
    """,
    """
    CREATE TABLE chat_batch_messages (
        batch_id TEXT NOT NULL,
        message_id TEXT NOT NULL,
        PRIMARY KEY (batch_id, message_id),
        FOREIGN KEY (batch_id) REFERENCES chat_export_batches(batch_id),
        FOREIGN KEY (message_id) REFERENCES chat_messages(message_id)
    )
    """,
    """
    CREATE TABLE chat_snapshots (
        snapshot_id TEXT PRIMARY KEY,
        batch_id TEXT NOT NULL,
        created_at_utc TEXT NOT NULL,
        sealed INTEGER NOT NULL DEFAULT 0 CHECK (sealed IN (0, 1)),
        FOREIGN KEY (batch_id) REFERENCES chat_export_batches(batch_id)
    )
    """,
    """
    CREATE TABLE chat_snapshot_messages (
        snapshot_id TEXT NOT NULL,
        message_id TEXT NOT NULL,
        PRIMARY KEY (snapshot_id, message_id),
        FOREIGN KEY (snapshot_id) REFERENCES chat_snapshots(snapshot_id),
        FOREIGN KEY (message_id) REFERENCES chat_messages(message_id)
    )
    """,
    """
    CREATE TABLE chat_export_jobs (
        job_id TEXT PRIMARY KEY,
        batch_id TEXT,
        state TEXT NOT NULL,
        idempotency_key TEXT NOT NULL UNIQUE,
        FOREIGN KEY (batch_id) REFERENCES chat_export_batches(batch_id)
    )
    """,
    """
    CREATE TABLE chat_search_runs (
        search_run_id TEXT PRIMARY KEY,
        snapshot_id TEXT NOT NULL,
        created_at_utc TEXT NOT NULL,
        FOREIGN KEY (snapshot_id) REFERENCES chat_snapshots(snapshot_id)
    )
    """,
    """
    CREATE TABLE chat_consent_records (
        consent_id TEXT PRIMARY KEY,
        scope_fingerprint TEXT NOT NULL,
        granted INTEGER NOT NULL CHECK (granted IN (0, 1)),
        recorded_at_utc TEXT NOT NULL,
        expires_at_utc TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE chat_audit_events (
        event_id TEXT PRIMARY KEY,
        event_kind TEXT NOT NULL,
        created_at_utc TEXT NOT NULL,
        batch_id TEXT,
        FOREIGN KEY (batch_id) REFERENCES chat_export_batches(batch_id)
    )
    """,
    "CREATE INDEX idx_chat_messages_conversation_time_order ON chat_messages(source_id, account_id, conversation_id, original_timestamp, source_order)",
    "CREATE INDEX idx_chat_snapshot_messages_snapshot ON chat_snapshot_messages(snapshot_id, message_id)",
    "CREATE INDEX idx_chat_batch_messages_batch ON chat_batch_messages(batch_id, message_id)",
    """
    CREATE TRIGGER chat_snapshots_immutable_update
    BEFORE UPDATE ON chat_snapshots
    WHEN OLD.sealed = 1
      OR NEW.batch_id != OLD.batch_id
      OR NEW.created_at_utc != OLD.created_at_utc
      OR NEW.sealed NOT IN (0, 1)
    BEGIN
        SELECT RAISE(ABORT, 'immutable snapshot');
    END
    """,
    """
    CREATE TRIGGER chat_snapshots_immutable_delete
    BEFORE DELETE ON chat_snapshots
    BEGIN
        SELECT RAISE(ABORT, 'immutable snapshot');
    END
    """,
    """
    CREATE TRIGGER chat_snapshot_messages_immutable_update
    BEFORE UPDATE ON chat_snapshot_messages
    BEGIN
        SELECT RAISE(ABORT, 'immutable snapshot');
    END
    """,
    """
    CREATE TRIGGER chat_snapshot_messages_immutable_insert
    BEFORE INSERT ON chat_snapshot_messages
    WHEN COALESCE((SELECT sealed FROM chat_snapshots WHERE snapshot_id = NEW.snapshot_id), 1) = 1
    BEGIN
        SELECT RAISE(ABORT, 'immutable snapshot');
    END
    """,
    """
    CREATE TRIGGER chat_snapshot_messages_immutable_delete
    BEFORE DELETE ON chat_snapshot_messages
    BEGIN
        SELECT RAISE(ABORT, 'immutable snapshot');
    END
    """,
)


_V2_TABLES = (
    "CREATE TABLE chat_sources_v2 (source_id TEXT PRIMARY KEY)",
    """
    CREATE TABLE chat_accounts_v2 (
        source_id TEXT NOT NULL,
        account_id TEXT NOT NULL,
        source_account_namespace TEXT,
        PRIMARY KEY (source_id, account_id),
        FOREIGN KEY (source_id) REFERENCES chat_sources_v2(source_id)
    )
    """,
    """
    CREATE TABLE chat_conversations_v2 (
        source_id TEXT NOT NULL,
        account_id TEXT NOT NULL,
        conversation_id TEXT NOT NULL,
        conversation_name TEXT,
        conversation_type TEXT,
        PRIMARY KEY (source_id, account_id, conversation_id),
        FOREIGN KEY (source_id, account_id)
            REFERENCES chat_accounts_v2(source_id, account_id)
    )
    """,
    """
    CREATE TABLE chat_export_batches_v2 (
        batch_id TEXT PRIMARY KEY,
        source_id TEXT NOT NULL,
        account_id TEXT NOT NULL,
        source_account_namespace TEXT,
        schema_version INTEGER NOT NULL DEFAULT 0 CHECK (schema_version >= 0),
        scope_fingerprint TEXT,
        scope_json TEXT,
        requested_start_utc TEXT,
        requested_end_utc TEXT,
        display_timezone TEXT,
        created_at_utc TEXT NOT NULL,
        UNIQUE (batch_id, source_id, account_id),
        CHECK (
            (
                schema_version = 0
                AND scope_fingerprint IS NULL
                AND scope_json IS NULL
                AND requested_start_utc IS NULL
                AND requested_end_utc IS NULL
                AND display_timezone IS NULL
            )
            OR (
                schema_version >= 1
                AND scope_fingerprint IS NOT NULL
                AND scope_json IS NOT NULL
                AND requested_start_utc IS NOT NULL
                AND requested_end_utc IS NOT NULL
                AND display_timezone IS NOT NULL
            )
        ),
        FOREIGN KEY (source_id, account_id)
            REFERENCES chat_accounts_v2(source_id, account_id)
    )
    """,
    """
    CREATE TABLE chat_batch_conversations_v2 (
        batch_id TEXT NOT NULL,
        source_id TEXT NOT NULL,
        account_id TEXT NOT NULL,
        conversation_id TEXT NOT NULL,
        PRIMARY KEY (batch_id, source_id, account_id, conversation_id),
        FOREIGN KEY (batch_id, source_id, account_id)
            REFERENCES chat_export_batches_v2(batch_id, source_id, account_id),
        FOREIGN KEY (source_id, account_id, conversation_id)
            REFERENCES chat_conversations_v2(source_id, account_id, conversation_id)
    )
    """,
    """
    CREATE TABLE chat_messages_v2 (
        message_id TEXT PRIMARY KEY,
        source_id TEXT NOT NULL,
        account_id TEXT NOT NULL,
        conversation_id TEXT NOT NULL,
        workspace_id TEXT,
        platform TEXT,
        source_account_namespace TEXT,
        conversation_name TEXT,
        conversation_type TEXT,
        source_message_id TEXT,
        sender_id TEXT,
        sender_name TEXT,
        sent_at_utc TEXT,
        original_timestamp TEXT,
        source_timezone TEXT,
        message_type TEXT,
        text TEXT,
        reply_to_message_id TEXT,
        source_file_id TEXT,
        page_slice_id TEXT,
        source_order INTEGER,
        attachments_json TEXT,
        payload_json TEXT NOT NULL,
        created_at_utc TEXT NOT NULL,
        CHECK (source_order IS NULL OR typeof(source_order) = 'integer'),
        UNIQUE (source_id, account_id, conversation_id, source_message_id),
        UNIQUE (message_id, source_id, account_id, conversation_id),
        FOREIGN KEY (source_id, account_id, conversation_id)
            REFERENCES chat_conversations_v2(source_id, account_id, conversation_id)
    )
    """,
    """
    CREATE TABLE chat_batch_messages_v2 (
        batch_id TEXT NOT NULL,
        message_id TEXT NOT NULL,
        source_id TEXT NOT NULL,
        account_id TEXT NOT NULL,
        conversation_id TEXT NOT NULL,
        PRIMARY KEY (batch_id, message_id),
        FOREIGN KEY (batch_id, source_id, account_id, conversation_id)
            REFERENCES chat_batch_conversations_v2(batch_id, source_id, account_id, conversation_id),
        FOREIGN KEY (message_id, source_id, account_id, conversation_id)
            REFERENCES chat_messages_v2(message_id, source_id, account_id, conversation_id)
    )
    """,
    """
    CREATE TABLE chat_snapshots_v2 (
        snapshot_id TEXT PRIMARY KEY,
        batch_id TEXT NOT NULL,
        created_at_utc TEXT NOT NULL,
        sealed INTEGER NOT NULL DEFAULT 0 CHECK (sealed IN (0, 1)),
        FOREIGN KEY (batch_id) REFERENCES chat_export_batches_v2(batch_id)
    )
    """,
    """
    CREATE TABLE chat_snapshot_messages_v2 (
        snapshot_id TEXT NOT NULL,
        message_id TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        PRIMARY KEY (snapshot_id, message_id),
        FOREIGN KEY (snapshot_id) REFERENCES chat_snapshots_v2(snapshot_id),
        FOREIGN KEY (message_id) REFERENCES chat_messages_v2(message_id)
    )
    """,
    """
    CREATE TABLE chat_export_jobs_v2 (
        job_id TEXT PRIMARY KEY,
        batch_id TEXT,
        state TEXT NOT NULL CHECK (state IN (
            'pending_authorization', 'connecting', 'reading', 'saving', 'verifying',
            'indexing', 'complete', 'partial', 'failed', 'cancelled'
        )),
        idempotency_key TEXT NOT NULL UNIQUE,
        updated_at_utc TEXT,
        known_total INTEGER CHECK (known_total IS NULL OR known_total >= 0),
        completed_count INTEGER DEFAULT 0 CHECK (completed_count IS NULL OR completed_count >= 0),
        fetched_count INTEGER DEFAULT 0 CHECK (fetched_count IS NULL OR fetched_count >= 0),
        saved_count INTEGER DEFAULT 0 CHECK (saved_count IS NULL OR saved_count >= 0),
        duplicate_count INTEGER DEFAULT 0 CHECK (duplicate_count IS NULL OR duplicate_count >= 0),
        unknown_time_count INTEGER DEFAULT 0 CHECK (unknown_time_count IS NULL OR unknown_time_count >= 0),
        parse_error_count INTEGER DEFAULT 0 CHECK (parse_error_count IS NULL OR parse_error_count >= 0),
        CHECK (known_total IS NULL OR completed_count IS NULL OR completed_count <= known_total),
        FOREIGN KEY (batch_id) REFERENCES chat_export_batches_v2(batch_id)
    )
    """,
    """
    CREATE TABLE chat_search_runs_v2 (
        search_run_id TEXT PRIMARY KEY,
        snapshot_id TEXT NOT NULL,
        created_at_utc TEXT NOT NULL,
        FOREIGN KEY (snapshot_id) REFERENCES chat_snapshots_v2(snapshot_id)
    )
    """,
    """
    CREATE TABLE chat_consent_records_v2 (
        consent_id TEXT PRIMARY KEY,
        scope_fingerprint TEXT NOT NULL,
        granted INTEGER NOT NULL CHECK (granted IN (0, 1)),
        recorded_at_utc TEXT NOT NULL,
        expires_at_utc TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE chat_audit_events_v2 (
        event_id TEXT PRIMARY KEY,
        event_kind TEXT NOT NULL,
        created_at_utc TEXT NOT NULL,
        batch_id TEXT,
        FOREIGN KEY (batch_id) REFERENCES chat_export_batches_v2(batch_id)
    )
    """,
)


_MIGRATION_2_COPY = (
    "INSERT INTO chat_sources_v2 SELECT source_id FROM chat_sources",
    """
    INSERT INTO chat_accounts_v2(source_id, account_id, source_account_namespace)
    SELECT source_id, account_id, NULL FROM chat_accounts
    """,
    """
    INSERT INTO chat_conversations_v2(
        source_id, account_id, conversation_id, conversation_name, conversation_type
    )
    SELECT source_id, account_id, conversation_id, NULL, NULL FROM chat_conversations
    """,
    """
    INSERT INTO chat_export_batches_v2(
        batch_id, source_id, account_id, source_account_namespace, schema_version,
        scope_fingerprint, scope_json, requested_start_utc, requested_end_utc,
        display_timezone, created_at_utc
    )
    SELECT batch_id, source_id, account_id, NULL, 0, NULL, NULL, NULL, NULL, NULL,
           created_at_utc
    FROM chat_export_batches
    """,
    """
    INSERT INTO chat_messages_v2(
        message_id, source_id, account_id, conversation_id, workspace_id, platform,
        source_account_namespace, conversation_name, conversation_type,
        source_message_id, sender_id, sender_name, sent_at_utc,
        original_timestamp, source_timezone, message_type, text,
        reply_to_message_id, source_file_id, page_slice_id, source_order,
        attachments_json, payload_json, created_at_utc
    )
    SELECT
        message_id, source_id, account_id, conversation_id, NULL, NULL, NULL,
        NULL, NULL, source_message_id, NULL, NULL, NULL, original_timestamp,
        NULL, NULL, text, NULL, source_file_id, page_slice_id, source_order,
        NULL,
        json_object(
            'source_id', source_id,
            'account_id', account_id,
            'conversation_id', conversation_id,
            'source_message_id', source_message_id,
            'source_file_id', source_file_id,
            'page_slice_id', page_slice_id,
            'source_order', source_order,
            'workspace_id', NULL,
            'platform', NULL,
            'source_account_namespace', NULL,
            'conversation_name', NULL,
            'conversation_type', NULL,
            'sent_at_utc', NULL,
            'original_timestamp', original_timestamp,
            'source_timezone', NULL,
            'sender_id', NULL,
            'sender_name', NULL,
            'message_type', NULL,
            'text', text,
            'reply_to_message_id', NULL,
            'export_batch_id', NULL,
            'attachments', NULL,
            'message_id', message_id
        ),
        created_at_utc
    FROM chat_messages
    """,
    """
    INSERT INTO chat_batch_conversations_v2(
        batch_id, source_id, account_id, conversation_id
    )
    SELECT DISTINCT membership.batch_id, message.source_id,
           message.account_id, message.conversation_id
    FROM chat_batch_messages AS membership
    JOIN chat_messages_v2 AS message ON message.message_id = membership.message_id
    """,
    """
    INSERT INTO chat_batch_messages_v2(
        batch_id, message_id, source_id, account_id, conversation_id
    )
    SELECT membership.batch_id, membership.message_id, message.source_id,
           message.account_id, message.conversation_id
    FROM chat_batch_messages AS membership
    JOIN chat_messages_v2 AS message ON message.message_id = membership.message_id
    """,
    """
    INSERT INTO chat_snapshots_v2(snapshot_id, batch_id, created_at_utc, sealed)
    SELECT snapshot_id, batch_id, created_at_utc, sealed FROM chat_snapshots
    """,
    """
    INSERT INTO chat_snapshot_messages_v2(snapshot_id, message_id, payload_json)
    SELECT snapshot.snapshot_id, snapshot.message_id, message.payload_json
    FROM chat_snapshot_messages AS snapshot
    JOIN chat_messages_v2 AS message ON message.message_id = snapshot.message_id
    """,
    """
    INSERT INTO chat_export_jobs_v2(
        job_id, batch_id, state, idempotency_key, updated_at_utc, known_total,
        completed_count, fetched_count, saved_count, duplicate_count,
        unknown_time_count, parse_error_count
    )
    SELECT
        job_id,
        batch_id,
        CASE state
            WHEN 'pending_consent' THEN 'pending_authorization'
            WHEN 'exporting' THEN 'reading'
            WHEN 'artifact_committed' THEN 'verifying'
            WHEN 'searchable' THEN 'complete'
            ELSE state
        END,
        idempotency_key,
        NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL
    FROM chat_export_jobs
    """,
    """
    INSERT INTO chat_search_runs_v2
    SELECT search_run_id, snapshot_id, created_at_utc FROM chat_search_runs
    """,
    """
    INSERT INTO chat_consent_records_v2
    SELECT consent_id, scope_fingerprint, granted, recorded_at_utc, expires_at_utc
    FROM chat_consent_records
    """,
    """
    INSERT INTO chat_audit_events_v2
    SELECT event_id, event_kind, created_at_utc, batch_id FROM chat_audit_events
    """,
)


_V2_POST_SCHEMA = (
    "CREATE INDEX idx_chat_messages_conversation_time_order ON chat_messages(source_id, account_id, conversation_id, sent_at_utc, source_order)",
    "CREATE INDEX idx_chat_snapshot_messages_snapshot ON chat_snapshot_messages(snapshot_id, message_id)",
    "CREATE INDEX idx_chat_batch_messages_batch ON chat_batch_messages(batch_id, message_id)",
    """
    CREATE TRIGGER chat_snapshots_immutable_update
    BEFORE UPDATE ON chat_snapshots
    WHEN OLD.sealed = 1
      OR NEW.batch_id != OLD.batch_id
      OR NEW.created_at_utc != OLD.created_at_utc
      OR NEW.sealed NOT IN (0, 1)
    BEGIN
        SELECT RAISE(ABORT, 'immutable snapshot');
    END
    """,
    """
    CREATE TRIGGER chat_snapshots_immutable_delete
    BEFORE DELETE ON chat_snapshots
    BEGIN
        SELECT RAISE(ABORT, 'immutable snapshot');
    END
    """,
    """
    CREATE TRIGGER chat_snapshot_messages_immutable_update
    BEFORE UPDATE ON chat_snapshot_messages
    BEGIN
        SELECT RAISE(ABORT, 'immutable snapshot');
    END
    """,
    """
    CREATE TRIGGER chat_snapshot_messages_immutable_insert
    BEFORE INSERT ON chat_snapshot_messages
    WHEN COALESCE((SELECT sealed FROM chat_snapshots WHERE snapshot_id = NEW.snapshot_id), 1) = 1
    BEGIN
        SELECT RAISE(ABORT, 'immutable snapshot');
    END
    """,
    """
    CREATE TRIGGER chat_snapshot_messages_immutable_delete
    BEFORE DELETE ON chat_snapshot_messages
    BEGIN
        SELECT RAISE(ABORT, 'immutable snapshot');
    END
    """,
)


def _upgrade_v1_to_v2(connection: sqlite3.Connection) -> None:
    """Rebuild v1 transactionally while representing unavailable metadata as null."""
    for statement in _V2_TABLES:
        connection.execute(statement)
    for statement in _MIGRATION_2_COPY:
        connection.execute(statement)

    for trigger in (
        "chat_snapshots_immutable_update",
        "chat_snapshots_immutable_delete",
        "chat_snapshot_messages_immutable_update",
        "chat_snapshot_messages_immutable_insert",
        "chat_snapshot_messages_immutable_delete",
    ):
        connection.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    for index in (
        "idx_chat_messages_conversation_time_order",
        "idx_chat_snapshot_messages_snapshot",
        "idx_chat_batch_messages_batch",
    ):
        connection.execute(f"DROP INDEX IF EXISTS {index}")

    for table in (
        "chat_snapshot_messages",
        "chat_search_runs",
        "chat_snapshots",
        "chat_batch_messages",
        "chat_export_jobs",
        "chat_messages",
        "chat_consent_records",
        "chat_audit_events",
        "chat_export_batches",
        "chat_conversations",
        "chat_accounts",
        "chat_sources",
    ):
        connection.execute(f"DROP TABLE {table}")

    for table in (
        "chat_sources",
        "chat_accounts",
        "chat_conversations",
        "chat_export_batches",
        "chat_batch_conversations",
        "chat_messages",
        "chat_batch_messages",
        "chat_snapshots",
        "chat_snapshot_messages",
        "chat_export_jobs",
        "chat_search_runs",
        "chat_consent_records",
        "chat_audit_events",
    ):
        connection.execute(f"ALTER TABLE {table}_v2 RENAME TO {table}")
    for statement in _V2_POST_SCHEMA:
        connection.execute(statement)


_MIGRATION_3 = (
    "ALTER TABLE chat_export_batches ADD COLUMN manifest_json TEXT",
    """
    ALTER TABLE chat_export_batches ADD COLUMN manifest_sha256 TEXT
    CHECK (
        manifest_sha256 IS NULL
        OR (
            length(manifest_sha256) = 64
            AND manifest_sha256 NOT GLOB '*[^0-9a-f]*'
        )
    )
    """,
    """
    ALTER TABLE chat_export_batches ADD COLUMN completeness TEXT
    CHECK (
        completeness IS NULL
        OR completeness IN ('source_range_verified', 'partial', 'unknown', 'empty')
    )
    """,
    "ALTER TABLE chat_export_batches ADD COLUMN limitations_accepted_at_utc TEXT",
    """
    ALTER TABLE chat_export_batches
    ADD COLUMN limitations_accepted_manifest_sha256 TEXT
    CHECK (
        limitations_accepted_manifest_sha256 IS NULL
        OR (
            length(limitations_accepted_manifest_sha256) = 64
            AND limitations_accepted_manifest_sha256 NOT GLOB '*[^0-9a-f]*'
        )
    )
    """,
    """
    CREATE TRIGGER chat_export_batches_manifest_bundle_insert
    BEFORE INSERT ON chat_export_batches
    WHEN NOT (
        (
            NEW.manifest_json IS NULL
            AND NEW.manifest_sha256 IS NULL
            AND NEW.completeness IS NULL
        )
        OR (
            NEW.manifest_json IS NOT NULL
            AND NEW.manifest_sha256 IS NOT NULL
            AND NEW.completeness IS NOT NULL
        )
    )
    BEGIN
        SELECT RAISE(ABORT, 'manifest evidence fields must be atomic');
    END
    """,
    """
    CREATE TRIGGER chat_export_batches_manifest_bundle_update
    BEFORE UPDATE ON chat_export_batches
    WHEN NOT (
        (
            NEW.manifest_json IS NULL
            AND NEW.manifest_sha256 IS NULL
            AND NEW.completeness IS NULL
        )
        OR (
            NEW.manifest_json IS NOT NULL
            AND NEW.manifest_sha256 IS NOT NULL
            AND NEW.completeness IS NOT NULL
        )
    )
    BEGIN
        SELECT RAISE(ABORT, 'manifest evidence fields must be atomic');
    END
    """,
    """
    CREATE TRIGGER chat_export_batches_acceptance_pair_insert
    BEFORE INSERT ON chat_export_batches
    WHEN NOT (
        (
            NEW.limitations_accepted_at_utc IS NULL
            AND NEW.limitations_accepted_manifest_sha256 IS NULL
        )
        OR (
            NEW.limitations_accepted_at_utc IS NOT NULL
            AND NEW.limitations_accepted_manifest_sha256 IS NOT NULL
            AND NEW.completeness IN ('partial', 'unknown')
        )
    )
    BEGIN
        SELECT RAISE(ABORT, 'limitation acceptance fields must be atomic and incomplete');
    END
    """,
    """
    CREATE TRIGGER chat_export_batches_acceptance_pair_update
    BEFORE UPDATE ON chat_export_batches
    WHEN NOT (
        (
            NEW.limitations_accepted_at_utc IS NULL
            AND NEW.limitations_accepted_manifest_sha256 IS NULL
        )
        OR (
            NEW.limitations_accepted_at_utc IS NOT NULL
            AND NEW.limitations_accepted_manifest_sha256 IS NOT NULL
            AND NEW.completeness IN ('partial', 'unknown')
        )
    )
    BEGIN
        SELECT RAISE(ABORT, 'limitation acceptance fields must be atomic and incomplete');
    END
    """,
    """
    CREATE TRIGGER chat_export_batches_acceptance_hash_insert
    BEFORE INSERT ON chat_export_batches
    WHEN NEW.limitations_accepted_manifest_sha256 IS NOT NULL
      AND NEW.limitations_accepted_manifest_sha256 IS NOT NEW.manifest_sha256
    BEGIN
        SELECT RAISE(ABORT, 'limitation acceptance manifest hash mismatch');
    END
    """,
    """
    CREATE TRIGGER chat_export_batches_acceptance_hash_update
    BEFORE UPDATE ON chat_export_batches
    WHEN NEW.limitations_accepted_manifest_sha256 IS NOT NULL
      AND NEW.limitations_accepted_manifest_sha256 IS NOT NEW.manifest_sha256
    BEGIN
        SELECT RAISE(ABORT, 'limitation acceptance manifest hash mismatch');
    END
    """,
)


_V4_JOB_STATES = (
    "'pending_authorization', 'connecting', 'reading', 'saving', 'verifying', "
    "'indexing', 'complete', 'partial', 'failed', 'cancelled'"
)


def _upgrade_v3_to_v4(connection: sqlite3.Connection) -> None:
    """Add scoped single-use consent and a CAS-capable durable job ledger."""
    connection.execute(
        """
        CREATE TABLE chat_export_consents (
            consent_id TEXT PRIMARY KEY,
            session_digest TEXT NOT NULL CHECK (
                length(session_digest) = 64
                AND session_digest NOT GLOB '*[^0-9a-f]*'
            ),
            scope_fingerprint TEXT NOT NULL CHECK (
                length(scope_fingerprint) = 64
                AND scope_fingerprint NOT GLOB '*[^0-9a-f]*'
            ),
            scope_json TEXT NOT NULL,
            recorded_at_utc TEXT NOT NULL,
            expires_at_utc TEXT NOT NULL,
            consumed_at_utc TEXT,
            consumed_job_id TEXT,
            CHECK (
                (consumed_at_utc IS NULL AND consumed_job_id IS NULL)
                OR (consumed_at_utc IS NOT NULL AND consumed_job_id IS NOT NULL)
            )
        )
        """
    )
    connection.execute(
        f"""
        CREATE TABLE chat_export_jobs_v4 (
            job_id TEXT PRIMARY KEY,
            batch_id TEXT,
            snapshot_id TEXT,
            planned_batch_id TEXT,
            planned_snapshot_id TEXT,
            state TEXT NOT NULL CHECK (state IN ({_V4_JOB_STATES})),
            idempotency_key TEXT NOT NULL,
            session_digest TEXT,
            scope_fingerprint TEXT,
            scope_json TEXT,
            consent_id TEXT,
            created_at_utc TEXT,
            updated_at_utc TEXT,
            revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0),
            cancellation_requested INTEGER NOT NULL DEFAULT 0
                CHECK (cancellation_requested IN (0, 1)),
            known_total INTEGER CHECK (known_total IS NULL OR known_total >= 0),
            completed_count INTEGER DEFAULT 0
                CHECK (completed_count IS NULL OR completed_count >= 0),
            fetched_count INTEGER DEFAULT 0
                CHECK (fetched_count IS NULL OR fetched_count >= 0),
            saved_count INTEGER DEFAULT 0
                CHECK (saved_count IS NULL OR saved_count >= 0),
            duplicate_count INTEGER DEFAULT 0
                CHECK (duplicate_count IS NULL OR duplicate_count >= 0),
            unknown_time_count INTEGER DEFAULT 0
                CHECK (unknown_time_count IS NULL OR unknown_time_count >= 0),
            parse_error_count INTEGER DEFAULT 0
                CHECK (parse_error_count IS NULL OR parse_error_count >= 0),
            error_code TEXT,
            manifest_sha256 TEXT CHECK (
                manifest_sha256 IS NULL
                OR (
                    length(manifest_sha256) = 64
                    AND manifest_sha256 NOT GLOB '*[^0-9a-f]*'
                )
            ),
            completeness TEXT CHECK (
                completeness IS NULL
                OR completeness IN (
                    'source_range_verified', 'partial', 'unknown', 'empty'
                )
            ),
            searchable INTEGER NOT NULL DEFAULT 0 CHECK (searchable IN (0, 1)),
            CHECK (
                known_total IS NULL
                OR completed_count IS NULL
                OR completed_count <= known_total
            ),
            CHECK (
                (
                    session_digest IS NULL
                    AND scope_fingerprint IS NULL
                    AND scope_json IS NULL
                    AND consent_id IS NULL
                    AND planned_batch_id IS NULL
                    AND planned_snapshot_id IS NULL
                )
                OR (
                    session_digest IS NOT NULL
                    AND scope_fingerprint IS NOT NULL
                    AND scope_json IS NOT NULL
                    AND consent_id IS NOT NULL
                    AND planned_batch_id IS NOT NULL
                    AND planned_snapshot_id IS NOT NULL
                )
            ),
            UNIQUE (session_digest, scope_fingerprint, idempotency_key),
            FOREIGN KEY (batch_id) REFERENCES chat_export_batches(batch_id),
            FOREIGN KEY (snapshot_id) REFERENCES chat_snapshots(snapshot_id),
            FOREIGN KEY (consent_id) REFERENCES chat_export_consents(consent_id)
        )
        """
    )
    connection.execute(
        """
        INSERT INTO chat_export_jobs_v4(
            job_id, batch_id, state, idempotency_key, created_at_utc,
            updated_at_utc, known_total, completed_count, fetched_count,
            saved_count, duplicate_count, unknown_time_count, parse_error_count,
            cancellation_requested
        )
        SELECT
            job_id, batch_id, state, idempotency_key, updated_at_utc,
            updated_at_utc, known_total, completed_count, fetched_count,
            saved_count, duplicate_count, unknown_time_count, parse_error_count,
            CASE WHEN state = 'cancelled' THEN 1 ELSE 0 END
        FROM chat_export_jobs
        """
    )
    connection.execute("DROP TABLE chat_export_jobs")
    connection.execute("ALTER TABLE chat_export_jobs_v4 RENAME TO chat_export_jobs")
    connection.execute(
        """
        CREATE UNIQUE INDEX idx_chat_export_jobs_idempotency_scope
        ON chat_export_jobs(session_digest, scope_fingerprint, idempotency_key)
        WHERE session_digest IS NOT NULL AND scope_fingerprint IS NOT NULL
        """
    )
    connection.execute(
        "CREATE INDEX idx_chat_export_jobs_state ON chat_export_jobs(state, updated_at_utc)"
    )
    connection.execute(
        f"""
        CREATE TABLE chat_export_job_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            state TEXT NOT NULL CHECK (state IN ({_V4_JOB_STATES})),
            revision INTEGER NOT NULL CHECK (revision >= 0),
            created_at_utc TEXT NOT NULL,
            error_code TEXT,
            UNIQUE (job_id, revision),
            FOREIGN KEY (job_id) REFERENCES chat_export_jobs(job_id) ON DELETE CASCADE
        )
        """
    )


_MIGRATION_5 = (
    """
    CREATE TABLE chat_generation_cache (
        cache_key TEXT NOT NULL CHECK (
            length(cache_key) = 64
            AND cache_key NOT GLOB '*[^0-9a-f]*'
        ),
        session_digest TEXT NOT NULL CHECK (
            length(session_digest) = 64
            AND session_digest NOT GLOB '*[^0-9a-f]*'
        ),
        snapshot_id TEXT NOT NULL,
        scope_fingerprint TEXT NOT NULL CHECK (
            length(scope_fingerprint) = 64
            AND scope_fingerprint NOT GLOB '*[^0-9a-f]*'
        ),
        identity_json TEXT NOT NULL,
        result_json TEXT NOT NULL,
        created_at_utc TEXT NOT NULL,
        PRIMARY KEY (cache_key, session_digest),
        FOREIGN KEY (snapshot_id) REFERENCES chat_snapshots(snapshot_id)
    )
    """,
    """
    CREATE INDEX idx_chat_generation_cache_snapshot
    ON chat_generation_cache(snapshot_id, session_digest)
    """,
)


_GENERATION_STATES = (
    "'pending', 'checking_cache', 'generating', 'extracting', 'merging', "
    "'validating', 'complete', 'failed', 'cancelled'"
)


_MIGRATION_6 = (
    """
    CREATE TABLE chat_generation_previews (
        preview_id TEXT PRIMARY KEY,
        session_digest TEXT NOT NULL,
        scope_fingerprint TEXT NOT NULL,
        payload_sha256 TEXT NOT NULL,
        scope_json TEXT NOT NULL,
        cache_identity_json TEXT NOT NULL,
        settings_json TEXT NOT NULL,
        message_count INTEGER NOT NULL CHECK (message_count >= 0),
        character_count INTEGER NOT NULL CHECK (character_count >= 0),
        estimated_input_tokens INTEGER NOT NULL CHECK (estimated_input_tokens >= 0),
        max_output_tokens INTEGER NOT NULL CHECK (max_output_tokens >= 0),
        provider_calls INTEGER NOT NULL CHECK (provider_calls >= 0),
        cache_hit INTEGER NOT NULL CHECK (cache_hit IN (0, 1)),
        will_send_external INTEGER NOT NULL CHECK (will_send_external IN (0, 1)),
        created_at_utc TEXT NOT NULL,
        expires_at_utc TEXT NOT NULL
    )
    """,
    """
    CREATE INDEX idx_chat_generation_previews_session
    ON chat_generation_previews(session_digest, expires_at_utc)
    """,
    """
    CREATE TABLE chat_generation_consents (
        consent_id TEXT PRIMARY KEY,
        preview_id TEXT NOT NULL UNIQUE,
        session_digest TEXT NOT NULL,
        scope_fingerprint TEXT NOT NULL,
        payload_sha256 TEXT NOT NULL,
        outbound_authorized INTEGER NOT NULL CHECK (outbound_authorized IN (0, 1)),
        recorded_at_utc TEXT NOT NULL,
        expires_at_utc TEXT NOT NULL,
        consumed_at_utc TEXT,
        consumed_job_id TEXT,
        CHECK (
            (consumed_at_utc IS NULL AND consumed_job_id IS NULL)
            OR (consumed_at_utc IS NOT NULL AND consumed_job_id IS NOT NULL)
        ),
        FOREIGN KEY (preview_id) REFERENCES chat_generation_previews(preview_id)
    )
    """,
    f"""
    CREATE TABLE chat_generation_jobs (
        job_id TEXT PRIMARY KEY,
        session_digest TEXT NOT NULL,
        consent_id TEXT NOT NULL,
        idempotency_key TEXT NOT NULL,
        state TEXT NOT NULL CHECK (state IN ({_GENERATION_STATES})),
        scope_fingerprint TEXT NOT NULL,
        scope_json TEXT NOT NULL,
        cache_identity_json TEXT NOT NULL,
        settings_json TEXT NOT NULL,
        outbound_authorized INTEGER NOT NULL CHECK (outbound_authorized IN (0, 1)),
        total_chunks INTEGER NOT NULL CHECK (total_chunks >= 0),
        completed_chunks INTEGER NOT NULL DEFAULT 0 CHECK (completed_chunks >= 0),
        cancellation_requested INTEGER NOT NULL DEFAULT 0
            CHECK (cancellation_requested IN (0, 1)),
        cache_hit INTEGER NOT NULL DEFAULT 0 CHECK (cache_hit IN (0, 1)),
        evidence_status TEXT NOT NULL DEFAULT 'unverified'
            CHECK (evidence_status IN ('unverified', 'verified')),
        result_json TEXT,
        error_code TEXT,
        created_at_utc TEXT NOT NULL,
        updated_at_utc TEXT NOT NULL,
        revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0),
        CHECK (completed_chunks <= total_chunks),
        CHECK (
            (state = 'complete' AND result_json IS NOT NULL AND evidence_status = 'verified')
            OR (state != 'complete' AND result_json IS NULL AND evidence_status = 'unverified')
        ),
        UNIQUE (session_digest, scope_fingerprint, idempotency_key),
        FOREIGN KEY (consent_id) REFERENCES chat_generation_consents(consent_id)
    )
    """,
    """
    CREATE INDEX idx_chat_generation_jobs_state
    ON chat_generation_jobs(state, updated_at_utc)
    """,
    f"""
    CREATE TABLE chat_generation_job_events (
        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id TEXT NOT NULL,
        state TEXT NOT NULL CHECK (state IN ({_GENERATION_STATES})),
        revision INTEGER NOT NULL CHECK (revision >= 0),
        created_at_utc TEXT NOT NULL,
        error_code TEXT,
        UNIQUE (job_id, revision),
        FOREIGN KEY (job_id) REFERENCES chat_generation_jobs(job_id) ON DELETE CASCADE
    )
    """,
)


_MIGRATION_7 = (
    """
    CREATE TABLE chat_documents (
        document_id TEXT PRIMARY KEY,
        session_digest TEXT NOT NULL CHECK (
            length(session_digest) = 64
            AND session_digest NOT GLOB '*[^0-9a-f]*'
        ),
        source_generation_job_id TEXT NOT NULL UNIQUE,
        current_version_id TEXT NOT NULL,
        source_result_json TEXT NOT NULL,
        created_at_utc TEXT NOT NULL,
        updated_at_utc TEXT NOT NULL,
        FOREIGN KEY (source_generation_job_id)
            REFERENCES chat_generation_jobs(job_id)
    )
    """,
    """
    CREATE TABLE chat_document_versions (
        version_id TEXT PRIMARY KEY,
        document_id TEXT NOT NULL,
        ordinal INTEGER NOT NULL CHECK (ordinal > 0),
        origin TEXT NOT NULL CHECK (
            origin IN ('ai_generated', 'user_edited', 'restored')
        ),
        parent_version_id TEXT,
        restored_from_version_id TEXT,
        content_json TEXT NOT NULL,
        change_summary TEXT NOT NULL DEFAULT '',
        created_at_utc TEXT NOT NULL,
        UNIQUE (document_id, ordinal),
        FOREIGN KEY (document_id) REFERENCES chat_documents(document_id)
            ON DELETE CASCADE,
        FOREIGN KEY (parent_version_id)
            REFERENCES chat_document_versions(version_id),
        FOREIGN KEY (restored_from_version_id)
            REFERENCES chat_document_versions(version_id),
        CHECK (
            (origin = 'ai_generated' AND parent_version_id IS NULL
                AND restored_from_version_id IS NULL AND ordinal = 1)
            OR (origin = 'user_edited' AND parent_version_id IS NOT NULL
                AND restored_from_version_id IS NULL AND ordinal > 1)
            OR (origin = 'restored' AND parent_version_id IS NOT NULL
                AND restored_from_version_id IS NOT NULL AND ordinal > 1)
        )
    )
    """,
    """
    CREATE INDEX idx_chat_documents_session
    ON chat_documents(session_digest, updated_at_utc)
    """,
    """
    CREATE INDEX idx_chat_document_versions_document
    ON chat_document_versions(document_id, ordinal)
    """,
    """
    CREATE TRIGGER chat_document_versions_immutable_update
    BEFORE UPDATE ON chat_document_versions
    BEGIN
        SELECT RAISE(ABORT, 'immutable document version');
    END
    """,
)


_MIGRATION_8 = (
    """
    CREATE TABLE chat_document_artifacts (
        artifact_id TEXT PRIMARY KEY,
        session_digest TEXT NOT NULL CHECK (
            length(session_digest) = 64
            AND session_digest NOT GLOB '*[^0-9a-f]*'
        ),
        document_id TEXT NOT NULL,
        version_id TEXT NOT NULL,
        output_root_id TEXT NOT NULL,
        filename TEXT NOT NULL,
        format TEXT NOT NULL CHECK (format IN ('markdown', 'docx')),
        source_mode TEXT NOT NULL CHECK (source_mode IN ('body_only', 'with_sources')),
        byte_size INTEGER NOT NULL CHECK (byte_size > 0),
        sha256 TEXT NOT NULL CHECK (
            length(sha256) = 64 AND sha256 NOT GLOB '*[^0-9a-f]*'
        ),
        created_at_utc TEXT NOT NULL,
        UNIQUE (output_root_id, filename),
        FOREIGN KEY (document_id) REFERENCES chat_documents(document_id)
            ON DELETE CASCADE,
        FOREIGN KEY (version_id) REFERENCES chat_document_versions(version_id)
            ON DELETE CASCADE
    )
    """,
    """
    CREATE INDEX idx_chat_document_artifacts_document
    ON chat_document_artifacts(document_id, created_at_utc)
    """,
)


_MIGRATION_9 = (
    """
    CREATE TABLE chat_deletion_plans (
        plan_id TEXT PRIMARY KEY,
        session_digest TEXT NOT NULL CHECK (
            length(session_digest) = 64
            AND session_digest NOT GLOB '*[^0-9a-f]*'
        ),
        target_kind TEXT NOT NULL CHECK (target_kind IN ('document', 'batch')),
        target_id TEXT NOT NULL,
        include_managed_files INTEGER NOT NULL CHECK (include_managed_files IN (0, 1)),
        impact_json TEXT NOT NULL,
        fingerprint TEXT NOT NULL CHECK (
            length(fingerprint) = 64 AND fingerprint NOT GLOB '*[^0-9a-f]*'
        ),
        created_at_utc TEXT NOT NULL,
        expires_at_utc TEXT NOT NULL,
        confirmed_at_utc TEXT,
        state TEXT NOT NULL CHECK (state IN ('planned', 'cleanup_pending', 'complete', 'failed')),
        staged_files_json TEXT,
        result_json TEXT
    )
    """,
    """
    CREATE INDEX idx_chat_deletion_plans_session
    ON chat_deletion_plans(session_digest, expires_at_utc)
    """,
)


_MIGRATION_10 = (
    """
    CREATE TABLE chat_generation_requests (
        request_record_id TEXT PRIMARY KEY,
        job_id TEXT NOT NULL,
        ordinal INTEGER NOT NULL CHECK (ordinal >= 1),
        provider TEXT NOT NULL,
        model TEXT NOT NULL,
        provider_request_id TEXT,
        status TEXT NOT NULL CHECK (
            status IN ('started', 'completed', 'failed', 'cancelled', 'unknown')
        ),
        prompt_tokens INTEGER CHECK (prompt_tokens IS NULL OR prompt_tokens >= 0),
        completion_tokens INTEGER CHECK (completion_tokens IS NULL OR completion_tokens >= 0),
        price_status TEXT NOT NULL DEFAULT 'unavailable'
            CHECK (price_status = 'unavailable'),
        price_source TEXT,
        price_version TEXT,
        price_observed_at_utc TEXT,
        currency TEXT,
        estimated_cost REAL,
        actual_cost REAL,
        created_at_utc TEXT NOT NULL,
        completed_at_utc TEXT,
        UNIQUE (job_id, ordinal),
        FOREIGN KEY (job_id) REFERENCES chat_generation_jobs(job_id) ON DELETE CASCADE
    )
    """,
    """
    CREATE INDEX idx_chat_generation_requests_job
    ON chat_generation_requests(job_id, ordinal)
    """,
)


_MIGRATION_11 = (
    """
    CREATE TABLE chat_manual_imports (
        import_id TEXT PRIMARY KEY,
        session_digest TEXT NOT NULL CHECK (
            length(session_digest) = 64
            AND session_digest NOT GLOB '*[^0-9a-f]*'
        ),
        batch_id TEXT NOT NULL UNIQUE,
        snapshot_id TEXT NOT NULL UNIQUE,
        source_kind TEXT NOT NULL CHECK (source_kind IN ('imported_file', 'paste')),
        platform_hint TEXT NOT NULL CHECK (platform_hint IN ('generic', 'qq', 'wechat')),
        source_fingerprint TEXT NOT NULL CHECK (
            length(source_fingerprint) = 64
            AND source_fingerprint NOT GLOB '*[^0-9a-f]*'
        ),
        filename TEXT NOT NULL,
        analysis_json TEXT NOT NULL,
        created_at_utc TEXT NOT NULL,
        state TEXT NOT NULL DEFAULT 'complete' CHECK (state = 'complete'),
        FOREIGN KEY (batch_id) REFERENCES chat_export_batches(batch_id),
        FOREIGN KEY (snapshot_id) REFERENCES chat_snapshots(snapshot_id)
    )
    """,
    """
    CREATE TABLE chat_manual_import_selections (
        handle_digest TEXT PRIMARY KEY CHECK (
            length(handle_digest) = 64
            AND handle_digest NOT GLOB '*[^0-9a-f]*'
        ),
        import_id TEXT NOT NULL,
        conversation_id TEXT NOT NULL,
        expires_at_utc TEXT NOT NULL,
        UNIQUE (import_id, conversation_id),
        FOREIGN KEY (import_id) REFERENCES chat_manual_imports(import_id) ON DELETE CASCADE
    )
    """,
    """
    CREATE INDEX idx_chat_manual_imports_session
    ON chat_manual_imports(session_digest, created_at_utc)
    """,
    """
    CREATE INDEX idx_chat_manual_import_selections_import
    ON chat_manual_import_selections(import_id, expires_at_utc)
    """,
)


_MIGRATION_12 = (
    """
    CREATE TABLE content_batches (
        batch_id TEXT PRIMARY KEY,
        session_digest TEXT NOT NULL CHECK (length(session_digest) = 64),
        created_at_utc TEXT NOT NULL,
        unit_count INTEGER NOT NULL CHECK (unit_count > 0)
    )
    """,
    """
    CREATE TABLE content_images (
        image_id TEXT PRIMARY KEY,
        batch_id TEXT NOT NULL,
        input_order INTEGER NOT NULL CHECK (input_order >= 0),
        sha256 TEXT NOT NULL CHECK (length(sha256) = 64),
        basename TEXT NOT NULL UNIQUE,
        mime_type TEXT NOT NULL CHECK (mime_type IN ('image/png', 'image/jpeg')),
        FOREIGN KEY (batch_id) REFERENCES content_batches(batch_id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE content_units (
        unit_id TEXT PRIMARY KEY,
        batch_id TEXT NOT NULL,
        image_id TEXT,
        kind TEXT NOT NULL CHECK (kind IN ('chat_message', 'note_passage')),
        group_id TEXT NOT NULL,
        source_order INTEGER NOT NULL CHECK (source_order >= 0),
        text TEXT NOT NULL CHECK (length(text) > 0),
        sender_name TEXT,
        sent_at TEXT,
        FOREIGN KEY (batch_id) REFERENCES content_batches(batch_id) ON DELETE CASCADE,
        FOREIGN KEY (image_id) REFERENCES content_images(image_id)
    )
    """,
    "CREATE INDEX idx_content_batches_session ON content_batches(session_digest, created_at_utc)",
    "CREATE INDEX idx_content_units_batch_order ON content_units(batch_id, source_order)",
    "CREATE INDEX idx_content_images_batch ON content_images(batch_id, input_order)",
)


_MIGRATION_13 = (
    """
    CREATE TABLE chat_generation_cache_v2 (
        cache_key TEXT NOT NULL CHECK (length(cache_key) = 64 AND cache_key NOT GLOB '*[^0-9a-f]*'),
        session_digest TEXT NOT NULL CHECK (length(session_digest) = 64 AND session_digest NOT GLOB '*[^0-9a-f]*'),
        snapshot_id TEXT NOT NULL CHECK (snapshot_id GLOB 'mixed:v2:*'),
        scope_fingerprint TEXT NOT NULL CHECK (length(scope_fingerprint) = 64 AND scope_fingerprint NOT GLOB '*[^0-9a-f]*'),
        identity_json TEXT NOT NULL,
        result_json TEXT NOT NULL,
        created_at_utc TEXT NOT NULL,
        PRIMARY KEY (cache_key, session_digest)
    )
    """,
    "CREATE INDEX idx_chat_generation_cache_v2_scope ON chat_generation_cache_v2(scope_fingerprint, session_digest)",
)


_MIGRATION_14 = (
    "ALTER TABLE chat_deletion_plans RENAME TO chat_deletion_plans_old",
    """
    CREATE TABLE chat_deletion_plans (
        plan_id TEXT PRIMARY KEY,
        session_digest TEXT NOT NULL CHECK (length(session_digest) = 64 AND session_digest NOT GLOB '*[^0-9a-f]*'),
        target_kind TEXT NOT NULL CHECK (target_kind IN ('document', 'batch', 'content_batch')),
        target_id TEXT NOT NULL,
        include_managed_files INTEGER NOT NULL CHECK (include_managed_files IN (0, 1)),
        impact_json TEXT NOT NULL,
        fingerprint TEXT NOT NULL CHECK (length(fingerprint) = 64 AND fingerprint NOT GLOB '*[^0-9a-f]*'),
        created_at_utc TEXT NOT NULL,
        expires_at_utc TEXT NOT NULL,
        confirmed_at_utc TEXT,
        state TEXT NOT NULL CHECK (state IN ('planned', 'cleanup_pending', 'complete', 'failed')),
        staged_files_json TEXT,
        result_json TEXT
    )
    """,
    "INSERT INTO chat_deletion_plans SELECT * FROM chat_deletion_plans_old",
    "DROP TABLE chat_deletion_plans_old",
    "CREATE INDEX idx_chat_deletion_plans_session ON chat_deletion_plans(session_digest, expires_at_utc)",
)


_MIGRATION_15 = (
    """CREATE TABLE chat_documents_retained (
        document_id TEXT PRIMARY KEY,
        session_digest TEXT NOT NULL CHECK (length(session_digest)=64 AND session_digest NOT GLOB '*[^0-9a-f]*'),
        source_generation_job_id TEXT NOT NULL UNIQUE,
        current_version_id TEXT NOT NULL, source_result_json TEXT NOT NULL,
        created_at_utc TEXT NOT NULL, updated_at_utc TEXT NOT NULL,
        source_deleted INTEGER NOT NULL DEFAULT 0 CHECK (source_deleted IN (0,1))
    )""",
    "INSERT INTO chat_documents_retained SELECT *, 0 FROM chat_documents",
    "DROP TABLE chat_documents",
    "ALTER TABLE chat_documents_retained RENAME TO chat_documents",
    "CREATE INDEX idx_chat_documents_session ON chat_documents(session_digest, updated_at_utc)",
    "ALTER TABLE chat_deletion_plans ADD COLUMN preserve_documents INTEGER NOT NULL DEFAULT 0 CHECK (preserve_documents IN (0,1))",
    """CREATE TABLE chat_raw_retirements (
        target_kind TEXT NOT NULL, target_id TEXT NOT NULL, session_digest TEXT NOT NULL,
        created_at_utc TEXT NOT NULL, PRIMARY KEY(target_kind, target_id)
    )""",
)


def _complete_retention_structures(connection: sqlite3.Connection) -> None:
    """Complete earlier local v15 shapes without replaying document migration."""
    columns = {row[1] for row in connection.execute("PRAGMA table_info(chat_deletion_plans)")}
    if "preserve_documents" not in columns:
        connection.execute(
            "ALTER TABLE chat_deletion_plans ADD COLUMN preserve_documents INTEGER NOT NULL DEFAULT 0 CHECK (preserve_documents IN (0,1))"
        )
    connection.execute("""CREATE TABLE IF NOT EXISTS chat_raw_retirements (
        target_kind TEXT NOT NULL, target_id TEXT NOT NULL, session_digest TEXT NOT NULL,
        created_at_utc TEXT NOT NULL, PRIMARY KEY(target_kind, target_id)
    )""")


_MIGRATION_17 = (
    """CREATE TABLE chat_agent_plan_requests (
        request_record_id TEXT PRIMARY KEY,
        provider TEXT NOT NULL,
        model TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('started', 'completed', 'failed', 'cancelled')),
        created_at_utc TEXT NOT NULL,
        completed_at_utc TEXT,
        prompt_tokens INTEGER CHECK (prompt_tokens IS NULL OR prompt_tokens >= 0),
        completion_tokens INTEGER CHECK (completion_tokens IS NULL OR completion_tokens >= 0)
    )""",
    "CREATE INDEX idx_chat_agent_plan_requests_created ON chat_agent_plan_requests(created_at_utc)",
)


MigrationAction = tuple[str, ...] | Callable[[sqlite3.Connection], None]
MIGRATIONS: tuple[tuple[int, MigrationAction], ...] = (
    (1, _MIGRATION_1),
    (2, _upgrade_v1_to_v2),
    (3, _MIGRATION_3),
    (4, _upgrade_v3_to_v4),
    (5, _MIGRATION_5),
    (6, _MIGRATION_6),
    (7, _MIGRATION_7),
    (8, _MIGRATION_8),
    (9, _MIGRATION_9),
    (10, _MIGRATION_10),
    (11, _MIGRATION_11),
    (12, _MIGRATION_12),
    (13, _MIGRATION_13),
    (14, _MIGRATION_14),
    (15, _MIGRATION_15),
    (16, _complete_retention_structures),
    (17, _MIGRATION_17),
)


def apply_migrations(path: Path) -> tuple[int, ...]:
    """Apply pending versions once under a write lock and validate rebuilt foreign keys."""
    connection = open_connection(path)
    applied: list[int] = []
    try:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS chat_schema_migrations (version INTEGER PRIMARY KEY)"
        )
        for version, action in MIGRATIONS:
            existing = connection.execute(
                "SELECT 1 FROM chat_schema_migrations WHERE version = ?", (version,)
            ).fetchone()
            if existing is not None:
                continue
            if callable(action):
                action(connection)
            else:
                for statement in action:
                    connection.execute(statement)
            connection.execute(
                "INSERT INTO chat_schema_migrations(version) VALUES (?)", (version,)
            )
            applied.append(version)
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise sqlite3.IntegrityError("foreign key violation after P1 migration")
        connection.commit()
        return tuple(applied)
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.close()
        _prepare_database_file(Path(path))
