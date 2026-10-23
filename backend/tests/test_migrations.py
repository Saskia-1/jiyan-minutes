"""SQLite migration invariants for P1's local-only repository."""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import stat

import pytest

from chat_to_howto.domain.jobs import ExportJobState
from chat_to_howto.migrations import apply_migrations, open_connection
from chat_to_howto.repositories.chat import ChatRepository


_LEGACY_V1_SCHEMA = """
CREATE TABLE chat_schema_migrations (version INTEGER PRIMARY KEY);
INSERT INTO chat_schema_migrations(version) VALUES (1);
CREATE TABLE chat_sources (source_id TEXT PRIMARY KEY);
CREATE TABLE chat_accounts (
    source_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    PRIMARY KEY (source_id, account_id),
    FOREIGN KEY (source_id) REFERENCES chat_sources(source_id)
);
CREATE TABLE chat_conversations (
    source_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    PRIMARY KEY (source_id, account_id, conversation_id),
    FOREIGN KEY (source_id, account_id)
        REFERENCES chat_accounts(source_id, account_id)
);
CREATE TABLE chat_export_batches (
    batch_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    FOREIGN KEY (source_id, account_id)
        REFERENCES chat_accounts(source_id, account_id)
);
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
    UNIQUE (source_id, account_id, conversation_id, source_message_id),
    FOREIGN KEY (source_id, account_id, conversation_id)
        REFERENCES chat_conversations(source_id, account_id, conversation_id)
);
CREATE TABLE chat_batch_messages (
    batch_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    PRIMARY KEY (batch_id, message_id),
    FOREIGN KEY (batch_id) REFERENCES chat_export_batches(batch_id),
    FOREIGN KEY (message_id) REFERENCES chat_messages(message_id)
);
CREATE TABLE chat_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    sealed INTEGER NOT NULL DEFAULT 0 CHECK (sealed IN (0, 1)),
    FOREIGN KEY (batch_id) REFERENCES chat_export_batches(batch_id)
);
CREATE TABLE chat_snapshot_messages (
    snapshot_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    PRIMARY KEY (snapshot_id, message_id),
    FOREIGN KEY (snapshot_id) REFERENCES chat_snapshots(snapshot_id),
    FOREIGN KEY (message_id) REFERENCES chat_messages(message_id)
);
CREATE TABLE chat_export_jobs (
    job_id TEXT PRIMARY KEY,
    batch_id TEXT,
    state TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    FOREIGN KEY (batch_id) REFERENCES chat_export_batches(batch_id)
);
CREATE TABLE chat_search_runs (
    search_run_id TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    FOREIGN KEY (snapshot_id) REFERENCES chat_snapshots(snapshot_id)
);
CREATE TABLE chat_consent_records (
    consent_id TEXT PRIMARY KEY,
    scope_fingerprint TEXT NOT NULL,
    granted INTEGER NOT NULL CHECK (granted IN (0, 1)),
    recorded_at_utc TEXT NOT NULL,
    expires_at_utc TEXT NOT NULL
);
CREATE TABLE chat_audit_events (
    event_id TEXT PRIMARY KEY,
    event_kind TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    batch_id TEXT,
    FOREIGN KEY (batch_id) REFERENCES chat_export_batches(batch_id)
);
"""


def _create_legacy_v1_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(_LEGACY_V1_SCHEMA)
        connection.executescript(
            """
            INSERT INTO chat_sources(source_id) VALUES ('legacy-source');
            INSERT INTO chat_accounts(source_id, account_id)
                VALUES ('legacy-source', 'legacy-account');
            INSERT INTO chat_conversations(source_id, account_id, conversation_id)
                VALUES ('legacy-source', 'legacy-account', 'legacy-conversation');
            INSERT INTO chat_export_batches(batch_id, source_id, account_id, created_at_utc)
                VALUES ('legacy-batch', 'legacy-source', 'legacy-account', '2026-09-01T00:00:00+00:00');
            INSERT INTO chat_messages(
                message_id, source_id, account_id, conversation_id, source_message_id,
                source_file_id, page_slice_id, source_order, original_timestamp, text,
                created_at_utc
            ) VALUES (
                'message:v1:legacy', 'legacy-source', 'legacy-account',
                'legacy-conversation', 'legacy-message', NULL, NULL, 1,
                '2026-09-01T00:00:00+00:00', 'synthetic legacy body',
                '2026-09-01T00:00:00+00:00'
            );
            INSERT INTO chat_batch_messages(batch_id, message_id)
                VALUES ('legacy-batch', 'message:v1:legacy');
            INSERT INTO chat_snapshots(snapshot_id, batch_id, created_at_utc, sealed)
                VALUES ('legacy-snapshot', 'legacy-batch', '2026-09-01T00:00:00+00:00', 1);
            INSERT INTO chat_snapshot_messages(snapshot_id, message_id)
                VALUES ('legacy-snapshot', 'message:v1:legacy');
            INSERT INTO chat_export_jobs(job_id, batch_id, state, idempotency_key)
                VALUES ('legacy-job', 'legacy-batch', 'searchable', 'legacy-key');
            INSERT INTO chat_search_runs(search_run_id, snapshot_id, created_at_utc)
                VALUES ('legacy-search', 'legacy-snapshot', '2026-09-01T00:00:00+00:00');
            INSERT INTO chat_consent_records(
                consent_id, scope_fingerprint, granted, recorded_at_utc, expires_at_utc
            ) VALUES (
                'legacy-consent', 'legacy-fingerprint', 1,
                '2026-09-01T00:00:00+00:00', '2026-09-02T00:00:00+00:00'
            );
            INSERT INTO chat_audit_events(event_id, event_kind, created_at_utc, batch_id)
                VALUES ('legacy-event', 'legacy', '2026-09-01T00:00:00+00:00', 'legacy-batch');
            """
        )
        connection.commit()
    finally:
        connection.close()


def test_migrations_are_versioned_idempotent_and_configure_sqlite(tmp_path: Path) -> None:
    """A restart keeps one migration history and safe SQLite connection settings."""
    path = tmp_path / "synthetic.sqlite3"

    assert apply_migrations(path) == (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17)
    assert apply_migrations(path) == ()

    connection = open_connection(path)
    try:
        versions = connection.execute(
            "SELECT version FROM chat_schema_migrations ORDER BY version"
        ).fetchall()
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()[0]
        busy_timeout = connection.execute("PRAGMA busy_timeout").fetchone()[0]

        assert versions == [(1,), (2,), (3,), (4,), (5,), (6,), (7,), (8,), (9,), (10,), (11,), (12,), (13,), (14,), (15,), (16,), (17,)]
        assert {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")} >= {"content_batches", "content_images", "content_units"}
        assert journal_mode == "wal"
        assert foreign_keys == 1
        assert busy_timeout >= 5000
    finally:
        connection.close()


def test_v16_database_adds_body_free_agent_request_metadata_without_existing_changes(tmp_path, monkeypatch):
    from chat_to_howto import migrations
    path = tmp_path / "v16.sqlite3"
    with monkeypatch.context() as patch:
        patch.setattr(migrations, "MIGRATIONS", tuple(item for item in migrations.MIGRATIONS if item[0] <= 16))
        apply_migrations(path)
    assert apply_migrations(path) == (17,)
    assert apply_migrations(path) == ()
    with open_connection(path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(chat_agent_plan_requests)")}
        assert columns == {"request_record_id", "provider", "model", "status", "created_at_utc",
                           "completed_at_utc", "prompt_tokens", "completion_tokens"}
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_earlier_retention_v15_shape_upgrades_without_losing_documents(tmp_path: Path, monkeypatch) -> None:
    """An existing v15 marker can predate the completed cleanup structures."""
    from chat_to_howto import migrations
    path = tmp_path / "earlier-retention.sqlite3"
    with monkeypatch.context() as patch:
        patch.setattr(migrations, "MIGRATIONS", tuple(item for item in migrations.MIGRATIONS if item[0] <= 15))
        apply_migrations(path)
    connection = open_connection(path)
    try:
        connection.execute("DROP TABLE chat_raw_retirements")
        connection.execute("ALTER TABLE chat_deletion_plans DROP COLUMN preserve_documents")
        connection.execute("INSERT INTO chat_documents VALUES(?,?,?,?,?,?,?,?)",
            ("synthetic-doc", "a" * 64, "synthetic-generation", "synthetic-version", "{}", "2026-10-23", "2026-10-23", 1))
        connection.commit()
    finally:
        connection.close()
    assert apply_migrations(path) == (16, 17)
    assert apply_migrations(path) == ()
    connection = open_connection(path)
    try:
        assert connection.execute("SELECT source_deleted FROM chat_documents WHERE document_id='synthetic-doc'").fetchone() == (1,)
        assert connection.execute("SELECT count(*) FROM chat_raw_retirements").fetchone() == (0,)
        assert "preserve_documents" in {row[1] for row in connection.execute("PRAGMA table_info(chat_deletion_plans)")}
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()


def test_foreign_keys_and_snapshot_indexes_are_enforced(tmp_path: Path) -> None:
    """Invalid membership fails locally and query-critical indexes are present."""
    path = tmp_path / "synthetic.sqlite3"
    apply_migrations(path)
    connection = open_connection(path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO chat_batch_messages(batch_id, message_id) VALUES (?, ?)",
                ("missing-batch", "missing-message"),
            )
        indexes = {
            row[1]
            for table in ("chat_snapshot_messages", "chat_messages")
            for row in connection.execute(f"PRAGMA index_list({table})")
        }
        assert {"idx_chat_snapshot_messages_snapshot", "idx_chat_messages_conversation_time_order"} <= indexes
    finally:
        connection.close()


def test_eight_simultaneous_migrators_apply_one_version_without_a_race(tmp_path: Path) -> None:
    """A pending migration is rechecked inside BEGIN IMMEDIATE, not trusted from a stale read."""
    path = tmp_path / "synthetic.sqlite3"

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: apply_migrations(path), range(8)))

    assert results.count((1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17)) == 1
    assert results.count(()) == 7
    connection = open_connection(path)
    try:
        assert connection.execute(
            "SELECT version FROM chat_schema_migrations ORDER BY version"
            ).fetchall() == [(1,), (2,), (3,), (4,), (5,), (6,), (7,), (8,), (9,), (10,), (11,), (12,), (13,), (14,), (15,), (16,), (17,)]
    finally:
        connection.close()


def test_legacy_v1_upgrade_is_idempotent_preserves_data_and_marks_unknown_scope(
    tmp_path: Path,
) -> None:
    """The real previous v1 shape upgrades without fabricating consent scope or losing rows."""
    path = tmp_path / "synthetic.sqlite3"
    _create_legacy_v1_database(path)

    assert apply_migrations(path) == (2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17)
    assert apply_migrations(path) == ()

    connection = open_connection(path)
    try:
        assert connection.execute(
            "SELECT version FROM chat_schema_migrations ORDER BY version"
            ).fetchall() == [(1,), (2,), (3,), (4,), (5,), (6,), (7,), (8,), (9,), (10,), (11,), (12,), (13,), (14,), (15,), (16,), (17,)]
        assert connection.execute(
            "SELECT text FROM chat_messages WHERE message_id = 'message:v1:legacy'"
        ).fetchone() == ("synthetic legacy body",)
        assert connection.execute(
            "SELECT source_id, account_id, conversation_id FROM chat_batch_messages"
        ).fetchone() == ("legacy-source", "legacy-account", "legacy-conversation")
        batch = connection.execute(
            """
            SELECT schema_version, source_account_namespace, scope_json,
                   manifest_json, manifest_sha256, completeness,
                   limitations_accepted_at_utc,
                   limitations_accepted_manifest_sha256
            FROM chat_export_batches WHERE batch_id = 'legacy-batch'
            """
        ).fetchone()
        assert batch == (0, None, None, None, None, None, None, None)
        job = connection.execute(
            "SELECT state, updated_at_utc FROM chat_export_jobs WHERE job_id = 'legacy-job'"
        ).fetchone()
        assert job == ("complete", None)
        assert connection.execute("SELECT COUNT(*) FROM chat_search_runs").fetchone() == (1,)
        assert connection.execute("SELECT COUNT(*) FROM chat_consent_records").fetchone() == (1,)
        assert connection.execute("SELECT COUNT(*) FROM chat_audit_events").fetchone() == (1,)
    finally:
        connection.close()

    repository = ChatRepository(path)
    try:
        assert repository.snapshot_messages("legacy-snapshot")[0].text == "synthetic legacy body"
        with pytest.raises(ValueError, match="legacy batch has no export scope"):
            repository.batch_scope("legacy-batch")
    finally:
        repository.close()


def test_eight_simultaneous_migrators_upgrade_legacy_v1_once(tmp_path: Path) -> None:
    """An existing v1 database is upgraded once even when app instances start together."""
    path = tmp_path / "synthetic.sqlite3"
    _create_legacy_v1_database(path)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: apply_migrations(path), range(8)))

    assert results.count((2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17)) == 1
    assert results.count(()) == 7
    connection = open_connection(path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM chat_messages").fetchone() == (1,)
        assert connection.execute(
            "SELECT version FROM chat_schema_migrations ORDER BY version"
            ).fetchall() == [(1,), (2,), (3,), (4,), (5,), (6,), (7,), (8,), (9,), (10,), (11,), (12,), (13,), (14,), (15,), (16,), (17,)]
    finally:
        connection.close()


def test_database_file_permissions_are_private_for_new_and_existing_files(tmp_path: Path) -> None:
    """The application database is always restricted to its current OS user."""
    path = tmp_path / "synthetic.sqlite3"
    path.touch()
    os.chmod(path, 0o666)

    apply_migrations(path)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_database_path_rejects_a_directory_without_changing_its_permissions(
    tmp_path: Path,
) -> None:
    """A mistaken database path cannot strip traversal permission from a directory."""
    path = tmp_path / "not-a-database"
    path.mkdir(mode=0o700)

    with pytest.raises(ValueError, match="regular file"):
        open_connection(path)

    assert stat.S_IMODE(path.stat().st_mode) == 0o700


def test_export_job_schema_uses_exact_v3_states_and_checked_progress(tmp_path: Path) -> None:
    """Unknown totals remain nullable and invalid lifecycle aliases cannot persist."""
    assert tuple(state.value for state in ExportJobState) == (
        "pending_authorization", "connecting", "reading", "saving", "verifying",
        "indexing", "complete", "partial", "failed", "cancelled",
    )
    path = tmp_path / "synthetic.sqlite3"
    apply_migrations(path)
    connection = open_connection(path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO chat_export_jobs(
                    job_id, state, idempotency_key, updated_at_utc, completed_count
                ) VALUES (?, ?, ?, ?, ?)
                """,
                ("job-1", "searchable", "key-1", "2026-09-01T00:00:00+00:00", 0),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO chat_export_jobs(
                    job_id, state, idempotency_key, updated_at_utc, known_total, completed_count
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                ("job-2", "reading", "key-2", "2026-09-01T00:00:00+00:00", 1, 2),
            )
    finally:
        connection.close()


def test_v3_manifest_and_limitation_acceptance_columns_are_hash_bound(
    tmp_path: Path,
) -> None:
    """Acceptance cannot be partial, stale, or attached to complete/empty evidence."""
    path = tmp_path / "synthetic.sqlite3"
    apply_migrations(path)
    connection = open_connection(path)
    try:
        connection.execute("INSERT INTO chat_sources(source_id) VALUES ('synthetic-source')")
        connection.execute(
            "INSERT INTO chat_accounts(source_id, account_id) VALUES (?, ?)",
            ("synthetic-source", "synthetic-account"),
        )
        connection.execute(
            """
            INSERT INTO chat_export_batches(
                batch_id, source_id, account_id, schema_version, created_at_utc
            ) VALUES (?, ?, ?, 0, ?)
            """,
            (
                "synthetic-batch",
                "synthetic-source",
                "synthetic-account",
                "2026-09-01T00:00:00+00:00",
            ),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE chat_export_batches SET completeness = 'complete' WHERE batch_id = ?",
                ("synthetic-batch",),
            )

        connection.execute(
            """
            UPDATE chat_export_batches
            SET manifest_json = ?, manifest_sha256 = ?, completeness = 'partial'
            WHERE batch_id = ?
            """,
            ('{"schema_version":1}', "a" * 64, "synthetic-batch"),
        )
        with pytest.raises(sqlite3.IntegrityError, match="acceptance"):
            connection.execute(
                """
                UPDATE chat_export_batches
                SET limitations_accepted_at_utc = ?
                WHERE batch_id = ?
                """,
                ("2026-09-01T01:00:00+00:00", "synthetic-batch"),
            )
        with pytest.raises(sqlite3.IntegrityError, match="manifest hash"):
            connection.execute(
                """
                UPDATE chat_export_batches
                SET limitations_accepted_at_utc = ?,
                    limitations_accepted_manifest_sha256 = ?
                WHERE batch_id = ?
                """,
                (
                    "2026-09-01T01:00:00+00:00",
                    "b" * 64,
                    "synthetic-batch",
                ),
            )
        connection.execute(
            """
            UPDATE chat_export_batches
            SET limitations_accepted_at_utc = ?,
                limitations_accepted_manifest_sha256 = manifest_sha256
            WHERE batch_id = ?
            """,
            ("2026-09-01T01:00:00+00:00", "synthetic-batch"),
        )
        assert connection.execute(
            """
            SELECT completeness, limitations_accepted_manifest_sha256
            FROM chat_export_batches WHERE batch_id = ?
            """,
            ("synthetic-batch",),
        ).fetchone() == ("partial", "a" * 64)
    finally:
        connection.close()
