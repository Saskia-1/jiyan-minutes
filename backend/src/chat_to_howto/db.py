"""Small, body-free SQLite persistence for P0 setup and verification jobs."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
from typing import Any, Mapping
from uuid import uuid4


_TERMINAL = {"complete", "partial", "failed", "cancelled"}
_NONTERMINAL = tuple(
    sorted({"pending_authorization", "connecting", "reading", "saving", "verifying"})
)
P0_SCHEMA_VERSION = 3
_NEXT = {
    "pending_authorization": {"connecting", "failed", "cancelled"},
    "connecting": {"reading", "failed", "cancelled"},
    "reading": {"saving", "failed", "cancelled"},
    "saving": {"verifying", "failed", "cancelled"},
    "verifying": {"complete", "partial", "failed", "cancelled"},
}
_SOURCE_SNAPSHOT_FIELDS = (
    "source_id",
    "account_id",
    "base_url",
    "client_version",
    "tool_version",
    "account_version",
    "nonce",
    "secret_ref",
)


class Database:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._prepare_private_file()
        self.migrate()
        self.reconcile_interrupted_jobs()

    def _prepare_private_file(self) -> None:
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(self.path, flags, 0o600)
            try:
                os.fchmod(descriptor, 0o600)
            finally:
                os.close(descriptor)
        except OSError as exc:
            raise RuntimeError("P0 database could not be opened safely.") from exc

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    def migrate(self) -> None:
        with self._connect() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if not isinstance(version, int) or version < 0 or version > P0_SCHEMA_VERSION:
                raise RuntimeError("Unsupported P0 database schema.")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    csrf_hash TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    recovery_expires_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sources (
                    source_id TEXT PRIMARY KEY,
                    account_id TEXT,
                    base_url TEXT,
                    client_version TEXT,
                    tool_version TEXT,
                    account_version TEXT,
                    nonce TEXT,
                    status TEXT NOT NULL,
                    secret_ref TEXT,
                    secret_configured INTEGER NOT NULL DEFAULT 0,
                    verified_client_version TEXT,
                    verified_tool_version TEXT,
                    verified_account_version TEXT,
                    verified_job_id TEXT
                );
                CREATE TABLE IF NOT EXISTS selections (
                    handle_hash TEXT PRIMARY KEY,
                    session_hash TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    nonce TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS challenges (
                    challenge_hash TEXT PRIMARY KEY,
                    session_hash TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    scope_fingerprint TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    used INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS grants (
                    source_id TEXT PRIMARY KEY,
                    session_hash TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    base_url TEXT NOT NULL,
                    client_version TEXT NOT NULL,
                    tool_version TEXT NOT NULL,
                    account_version TEXT NOT NULL,
                    nonce TEXT NOT NULL,
                    secret_ref TEXT NOT NULL,
                    secret_backend TEXT NOT NULL,
                    scope_fingerprint TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    consumed INTEGER NOT NULL DEFAULT 0,
                    job_id TEXT,
                    accessed INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    session_hash TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    base_url TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    output_root_id TEXT NOT NULL,
                    client_version TEXT NOT NULL,
                    tool_version TEXT NOT NULL,
                    account_version TEXT NOT NULL,
                    nonce TEXT NOT NULL,
                    secret_ref TEXT,
                    secret_backend TEXT NOT NULL,
                    secret_required INTEGER NOT NULL DEFAULT 0,
                    range_start TEXT NOT NULL,
                    range_end TEXT NOT NULL,
                    start_ms INTEGER NOT NULL,
                    end_ms INTEGER NOT NULL,
                    timezone TEXT NOT NULL,
                    scope_fingerprint TEXT NOT NULL,
                    consent_recorded_at TEXT,
                    consent_expires_at TEXT,
                    state TEXT NOT NULL,
                    evidence_class TEXT NOT NULL,
                    count INTEGER,
                    sha256 TEXT,
                    hash_ok INTEGER,
                    range_ok INTEGER,
                    completeness TEXT,
                    error_code TEXT,
                    cancelled INTEGER NOT NULL DEFAULT 0,
                    disclosure_revision TEXT NOT NULL DEFAULT '',
                    disclosure_hash TEXT NOT NULL DEFAULT '',
                    artifact_dir_name TEXT,
                    artifact_temp_name TEXT,
                    artifact_final_name TEXT,
                    artifact_owner_token TEXT,
                    artifact_dir_dev INTEGER,
                    artifact_dir_ino INTEGER,
                    artifact_temp_owned INTEGER NOT NULL DEFAULT 0,
                    artifact_final_owned INTEGER NOT NULL DEFAULT 0,
                    artifact_dir_owned INTEGER NOT NULL DEFAULT 0,
                    cleanup_pending INTEGER NOT NULL DEFAULT 0
                );
                """
            )
            # Preserve databases created by the initial P0 implementation. New
            # authority fields are additive; blank legacy values fail closed.
            self._ensure_columns(
                connection,
                "sessions",
                {"recovery_expires_at": "TEXT"},
            )
            # A legacy session gets no authority beyond its already-recorded
            # short expiry. Migration must never turn an old cookie into a new
            # recovery capability.
            connection.execute(
                "UPDATE sessions SET recovery_expires_at=expires_at "
                "WHERE recovery_expires_at IS NULL OR recovery_expires_at=''"
            )
            self._ensure_columns(
                connection,
                "sources",
                {
                    "verified_client_version": "TEXT",
                    "verified_tool_version": "TEXT",
                    "verified_account_version": "TEXT",
                    "verified_job_id": "TEXT",
                },
            )
            self._ensure_columns(
                connection,
                "grants",
                {
                    "session_hash": "TEXT NOT NULL DEFAULT ''",
                    "account_id": "TEXT NOT NULL DEFAULT ''",
                    "base_url": "TEXT NOT NULL DEFAULT ''",
                    "client_version": "TEXT NOT NULL DEFAULT ''",
                    "tool_version": "TEXT NOT NULL DEFAULT ''",
                    "account_version": "TEXT NOT NULL DEFAULT ''",
                    "nonce": "TEXT NOT NULL DEFAULT ''",
                    "secret_ref": "TEXT NOT NULL DEFAULT ''",
                    "secret_backend": "TEXT NOT NULL DEFAULT ''",
                    "job_id": "TEXT",
                    "accessed": "INTEGER NOT NULL DEFAULT 0",
                },
            )
            self._ensure_columns(
                connection,
                "jobs",
                {
                    "session_hash": "TEXT NOT NULL DEFAULT ''",
                    "base_url": "TEXT NOT NULL DEFAULT ''",
                    "secret_ref": "TEXT",
                    "secret_backend": "TEXT NOT NULL DEFAULT ''",
                    "secret_required": "INTEGER NOT NULL DEFAULT 0",
                    "consent_recorded_at": "TEXT",
                    "consent_expires_at": "TEXT",
                    "disclosure_revision": "TEXT NOT NULL DEFAULT ''",
                    "disclosure_hash": "TEXT NOT NULL DEFAULT ''",
                    "artifact_dir_name": "TEXT",
                    "artifact_temp_name": "TEXT",
                    "artifact_final_name": "TEXT",
                    "artifact_owner_token": "TEXT",
                    "artifact_dir_dev": "INTEGER",
                    "artifact_dir_ino": "INTEGER",
                    "artifact_temp_owned": "INTEGER NOT NULL DEFAULT 0",
                    "artifact_final_owned": "INTEGER NOT NULL DEFAULT 0",
                    "artifact_dir_owned": "INTEGER NOT NULL DEFAULT 0",
                    "cleanup_pending": "INTEGER NOT NULL DEFAULT 0",
                },
            )
            connection.execute(f"PRAGMA user_version={P0_SCHEMA_VERSION}")

    @staticmethod
    def _ensure_columns(
        connection: sqlite3.Connection,
        table: str,
        columns: Mapping[str, str],
    ) -> None:
        existing = {
            row[1] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        for name, declaration in columns.items():
            if name not in existing:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")

    def execute(self, query: str, values: tuple[Any, ...] = ()) -> None:
        with self._connect() as connection:
            connection.execute(query, values)

    def insert_selection(
        self,
        *,
        handle_hash: str,
        session_hash: str,
        source_id: str,
        account_id: str,
        conversation_id: str,
        expires_at: str,
        nonce: str,
    ) -> bool:
        """Insert one opaque selection without ever replacing an existing mapping."""
        with self._connect() as connection:
            changed = connection.execute(
                """
                INSERT OR IGNORE INTO selections(
                    handle_hash, session_hash, source_id, account_id,
                    conversation_id, expires_at, nonce
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    handle_hash,
                    session_hash,
                    source_id,
                    account_id,
                    conversation_id,
                    expires_at,
                    nonce,
                ),
            ).rowcount
        return changed == 1

    def one(self, query: str, values: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(query, values).fetchone()
        return dict(row) if row else None

    def rows(self, query: str, values: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self._connect() as connection:
            result = connection.execute(query, values).fetchall()
        return [dict(row) for row in result]

    def configure_source(self, source: Mapping[str, Any]) -> None:
        """Replace one source identity and revoke every authority tied to the old nonce."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO sources(
                    source_id,account_id,base_url,client_version,tool_version,
                    account_version,nonce,status,secret_ref,secret_configured,
                    verified_client_version,verified_tool_version,
                    verified_account_version,verified_job_id
                ) VALUES(?,?,?,?,?,?,?,?,?,0,NULL,NULL,NULL,NULL)
                ON CONFLICT(source_id) DO UPDATE SET
                    account_id=excluded.account_id,
                    base_url=excluded.base_url,
                    client_version=excluded.client_version,
                    tool_version=excluded.tool_version,
                    account_version=excluded.account_version,
                    nonce=excluded.nonce,
                    status=excluded.status,
                    secret_ref=excluded.secret_ref,
                    secret_configured=0,
                    verified_client_version=NULL,
                    verified_tool_version=NULL,
                    verified_account_version=NULL,
                    verified_job_id=NULL
                """,
                tuple(source[field] for field in (
                    "source_id", "account_id", "base_url", "client_version",
                    "tool_version", "account_version", "nonce", "status", "secret_ref",
                )),
            )
            source_id = source["source_id"]
            connection.execute("DELETE FROM selections WHERE source_id=?", (source_id,))
            connection.execute("DELETE FROM challenges WHERE source_id=?", (source_id,))
            connection.execute("DELETE FROM grants WHERE source_id=?", (source_id,))
            placeholders = ",".join("?" for _ in _NONTERMINAL)
            connection.execute(
                f"""
                UPDATE jobs
                   SET state='failed', error_code='source_reconfigured',
                       cleanup_pending=CASE
                           WHEN artifact_dir_owned=1 THEN 1 ELSE cleanup_pending END
                 WHERE source_id=? AND state IN ({placeholders})
                """,
                (source_id, *_NONTERMINAL),
            )

    def disconnect_source(self, source_id: str) -> str:
        """Forget local adapter authority without deleting prior exported data."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            source = connection.execute(
                "SELECT secret_configured FROM sources WHERE source_id=?", (source_id,)
            ).fetchone()
            if source is None:
                return "not_found"
            if bool(source["secret_configured"]):
                return "secret_configured"
            placeholders = ",".join("?" for _ in _NONTERMINAL)
            active = connection.execute(
                f"SELECT 1 FROM jobs WHERE source_id=? AND state IN ({placeholders}) LIMIT 1",
                (source_id, *_NONTERMINAL),
            ).fetchone()
            if active is not None:
                return "active_job"
            connection.execute("DELETE FROM selections WHERE source_id=?", (source_id,))
            connection.execute("DELETE FROM challenges WHERE source_id=?", (source_id,))
            connection.execute("DELETE FROM grants WHERE source_id=?", (source_id,))
            connection.execute("DELETE FROM sources WHERE source_id=?", (source_id,))
            return "disconnected"

    def claim_challenge(
        self,
        *,
        challenge_hash: str,
        session_hash: str,
        kind: str,
        source_id: str,
        account_id: str,
        scope_fingerprint: str,
        payload: str,
        now: datetime,
    ) -> dict[str, Any] | None:
        """Consume an exact challenge once while serializing competing callers."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM challenges WHERE challenge_hash=?", (challenge_hash,)
            ).fetchone()
            if row is None or not self._challenge_matches(
                row,
                session_hash=session_hash,
                kind=kind,
                source_id=source_id,
                account_id=account_id,
                scope_fingerprint=scope_fingerprint,
                payload=payload,
                now=now,
            ):
                return None
            changed = connection.execute(
                "UPDATE challenges SET used=1 WHERE challenge_hash=? AND used=0",
                (challenge_hash,),
            ).rowcount
            return dict(row) if changed == 1 else None

    def create_verification_job(
        self,
        *,
        job: Mapping[str, Any],
        challenge_hash: str,
        challenge_scope_fingerprint: str,
        challenge_payload: str,
        now: datetime,
    ) -> str:
        """Atomically consume export consent, bind one secret grant, and create one job."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            source = connection.execute(
                "SELECT * FROM sources WHERE source_id=?", (job["source_id"],)
            ).fetchone()
            if source is None or not self._source_matches(source, job):
                return "source_configuration_changed"
            challenge = connection.execute(
                "SELECT * FROM challenges WHERE challenge_hash=?", (challenge_hash,)
            ).fetchone()
            if challenge is None or not self._challenge_matches(
                challenge,
                session_hash=job["session_hash"],
                kind="verification",
                source_id=job["source_id"],
                account_id=job["account_id"],
                scope_fingerprint=challenge_scope_fingerprint,
                payload=challenge_payload,
                now=now,
            ):
                return "challenge_rejected"
            if connection.execute(
                "UPDATE challenges SET used=1 WHERE challenge_hash=? AND used=0",
                (challenge_hash,),
            ).rowcount != 1:
                return "challenge_rejected"

            secret_required = bool(source["secret_configured"])
            if secret_required:
                grant = connection.execute(
                    "SELECT * FROM grants WHERE source_id=?", (job["source_id"],)
                ).fetchone()
                if grant is None or not self._grant_matches(grant, job, now=now):
                    connection.rollback()
                    return "secret_authorization_missing"
                changed = connection.execute(
                    """
                    UPDATE grants
                       SET consumed=1, job_id=?
                     WHERE source_id=? AND consumed=0 AND job_id IS NULL
                    """,
                    (job["job_id"], job["source_id"]),
                ).rowcount
                if changed != 1:
                    connection.rollback()
                    return "secret_authorization_missing"

            connection.execute(
                """
                INSERT INTO jobs(
                    job_id,session_hash,source_id,account_id,base_url,
                    conversation_id,output_root_id,client_version,tool_version,
                    account_version,nonce,secret_ref,secret_backend,secret_required,
                    range_start,range_end,start_ms,end_ms,timezone,scope_fingerprint,
                    consent_recorded_at,consent_expires_at,state,evidence_class,
                    disclosure_revision,disclosure_hash
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    job["job_id"], job["session_hash"], job["source_id"],
                    job["account_id"], job["base_url"], job["conversation_id"],
                    job["output_root_id"], job["client_version"], job["tool_version"],
                    job["account_version"], job["nonce"], job["secret_ref"],
                    job["secret_backend"], int(secret_required), job["range_start"],
                    job["range_end"], job["start_ms"], job["end_ms"], job["timezone"],
                    job["scope_fingerprint"], self.now_text(now),
                    challenge["expires_at"], "pending_authorization", "unknown",
                    job.get("disclosure_revision", ""), job.get("disclosure_hash", ""),
                ),
            )
            return "created"

    def prepare_artifact(self, job_id: str) -> dict[str, Any] | None:
        """Persist exact unpredictable artifact names before any filesystem mutation."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM jobs WHERE job_id=?", (job_id,)
            ).fetchone()
            if row is None or row["state"] != "saving":
                return None
            if row["artifact_dir_name"] is None:
                directory = "job-" + job_id
                temporary = "messages-" + uuid4().hex + ".tmp"
                final = "messages.jsonl"
                owner = uuid4().hex + uuid4().hex
                connection.execute(
                    """
                    UPDATE jobs
                       SET artifact_dir_name=?,artifact_temp_name=?,
                           artifact_final_name=?,artifact_owner_token=?
                     WHERE job_id=? AND state='saving'
                       AND artifact_dir_name IS NULL
                    """,
                    (directory, temporary, final, owner, job_id),
                )
                row = connection.execute(
                    "SELECT * FROM jobs WHERE job_id=?", (job_id,)
                ).fetchone()
            return dict(row)

    def mark_artifact_directory(
        self, job_id: str, *, device: int, inode: int
    ) -> bool:
        with self._connect() as connection:
            changed = connection.execute(
                """
                UPDATE jobs
                   SET artifact_dir_owned=1,artifact_dir_dev=?,artifact_dir_ino=?
                 WHERE job_id=? AND state='saving' AND artifact_dir_owned=0
                """,
                (device, inode, job_id),
            ).rowcount
        return changed == 1

    def mark_artifact_temp(self, job_id: str, *, owned: bool) -> bool:
        with self._connect() as connection:
            changed = connection.execute(
                """
                UPDATE jobs SET artifact_temp_owned=?
                 WHERE job_id=? AND state IN ('saving','verifying')
                """,
                (int(owned), job_id),
            ).rowcount
        return changed == 1

    def mark_artifact_final(self, job_id: str, *, owned: bool) -> bool:
        with self._connect() as connection:
            changed = connection.execute(
                """
                UPDATE jobs SET artifact_final_owned=?
                 WHERE job_id=? AND state IN ('saving','verifying')
                """,
                (int(owned), job_id),
            ).rowcount
        return changed == 1

    def artifact_cleanup_rows(self) -> list[dict[str, Any]]:
        return self.rows(
            "SELECT * FROM jobs WHERE cleanup_pending=1 AND artifact_dir_owned=1"
        )

    def mark_artifact_cleaned(self, job_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE jobs
                   SET cleanup_pending=0,artifact_temp_owned=0,
                       artifact_final_owned=0,artifact_dir_owned=0
                 WHERE job_id=?
                """,
                (job_id,),
            )

    def store_secret_grant(
        self,
        *,
        source: Mapping[str, Any],
        session_hash: str,
        secret_backend: str,
        scope_fingerprint: str,
        recorded_at: datetime,
        expires_at: datetime,
    ) -> bool:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT * FROM sources WHERE source_id=?", (source["source_id"],)
            ).fetchone()
            if current is None or not self._source_matches(current, source):
                return False
            connection.execute(
                """
                INSERT INTO grants(
                    source_id,session_hash,account_id,base_url,client_version,
                    tool_version,account_version,nonce,secret_ref,secret_backend,
                    scope_fingerprint,recorded_at,expires_at,consumed,job_id,accessed
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,0,NULL,0)
                ON CONFLICT(source_id) DO UPDATE SET
                    session_hash=excluded.session_hash,
                    account_id=excluded.account_id,
                    base_url=excluded.base_url,
                    client_version=excluded.client_version,
                    tool_version=excluded.tool_version,
                    account_version=excluded.account_version,
                    nonce=excluded.nonce,
                    secret_ref=excluded.secret_ref,
                    secret_backend=excluded.secret_backend,
                    scope_fingerprint=excluded.scope_fingerprint,
                    recorded_at=excluded.recorded_at,
                    expires_at=excluded.expires_at,
                    consumed=0,
                    job_id=NULL,
                    accessed=0
                """,
                (
                    source["source_id"], session_hash, source["account_id"],
                    source["base_url"], source["client_version"], source["tool_version"],
                    source["account_version"], source["nonce"], source["secret_ref"],
                    secret_backend, scope_fingerprint, self.now_text(recorded_at),
                    self.now_text(expires_at),
                ),
            )
            return True

    def claim_secret_grant_for_access(
        self,
        *,
        source: Mapping[str, Any],
        session_hash: str,
        secret_backend: str,
        scope_fingerprint: str,
        now: datetime,
    ) -> dict[str, Any] | None:
        """Consume one live GET grant before a protected adapter access starts."""
        expected = {
            **source,
            "session_hash": session_hash,
            "secret_backend": secret_backend,
        }
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT * FROM sources WHERE source_id=?", (source["source_id"],)
            ).fetchone()
            grant = connection.execute(
                "SELECT * FROM grants WHERE source_id=?", (source["source_id"],)
            ).fetchone()
            if (
                current is None
                or not self._source_matches(current, source)
                or grant is None
                or grant["scope_fingerprint"] != scope_fingerprint
                or grant["accessed"]
                or not self._grant_matches(grant, expected, now=now)
            ):
                return None
            changed = connection.execute(
                """
                UPDATE grants
                   SET consumed=1, accessed=1
                 WHERE source_id=? AND consumed=0 AND accessed=0 AND job_id IS NULL
                """,
                (source["source_id"],),
            ).rowcount
            return dict(grant) if changed == 1 else None

    def set_secret_configured(
        self,
        source: Mapping[str, Any],
        *,
        configured: bool,
        clear_grants: bool = False,
    ) -> bool:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT * FROM sources WHERE source_id=?", (source["source_id"],)
            ).fetchone()
            if current is None or not self._source_matches(current, source):
                return False
            connection.execute(
                "UPDATE sources SET secret_configured=? WHERE source_id=?",
                (int(configured), source["source_id"]),
            )
            if clear_grants:
                connection.execute("DELETE FROM grants WHERE source_id=?", (source["source_id"],))
            return True

    def current_source_for_job(self, job: Mapping[str, Any]) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM sources WHERE source_id=?", (job["source_id"],)
            ).fetchone()
        if row is None or not self._source_matches(row, job):
            return None
        return dict(row)

    def claim_grant_access(
        self, job: Mapping[str, Any], *, now: datetime
    ) -> dict[str, Any] | None:
        """Allow the already-bound job to cross the adapter boundary exactly once."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            grant = connection.execute(
                "SELECT * FROM grants WHERE source_id=?", (job["source_id"],)
            ).fetchone()
            if (
                grant is None
                or grant["job_id"] != job["job_id"]
                or not grant["consumed"]
                or grant["accessed"]
                or not self._grant_matches(grant, job, now=now, require_unbound=False)
            ):
                return None
            changed = connection.execute(
                """
                UPDATE grants SET accessed=1
                 WHERE source_id=? AND job_id=? AND consumed=1 AND accessed=0
                """,
                (job["source_id"], job["job_id"]),
            ).rowcount
            return dict(grant) if changed == 1 else None

    def set_job_evidence(self, job_id: str, evidence_class: str) -> bool:
        with self._connect() as connection:
            changed = connection.execute(
                "UPDATE jobs SET evidence_class=? WHERE job_id=? AND state='connecting'",
                (evidence_class, job_id),
            ).rowcount
        return changed == 1

    def complete_real_device_job(
        self,
        job: Mapping[str, Any],
        *,
        count: int,
        digest: str,
        completeness: str,
        now: datetime,
    ) -> bool:
        """Atomically bind a successful real-device artifact to the current source."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            source = connection.execute(
                "SELECT * FROM sources WHERE source_id=?", (job["source_id"],)
            ).fetchone()
            persisted_job = connection.execute(
                "SELECT state,evidence_class,consent_expires_at FROM jobs WHERE job_id=?",
                (job["job_id"],),
            ).fetchone()
            try:
                consent_live = (
                    persisted_job is not None
                    and datetime.fromisoformat(persisted_job["consent_expires_at"]) > now
                )
            except (TypeError, ValueError):
                consent_live = False
            if (
                source is None
                or not self._source_matches(source, job)
                or source["status"] not in {
                    "needs_user_action", "documented_not_tested", "ready"
                }
                or persisted_job is None
                or persisted_job["state"] != "verifying"
                or persisted_job["evidence_class"] != "real_device"
                or not consent_live
                or completeness != "source_range_verified"
            ):
                return False
            connection.execute(
                """
                UPDATE sources
                   SET status='ready',
                       verified_client_version=?,
                       verified_tool_version=?,
                       verified_account_version=?,
                       verified_job_id=?
                 WHERE source_id=?
                """,
                (
                    job["client_version"], job["tool_version"],
                    job["account_version"], job["job_id"], job["source_id"],
                ),
            )
            connection.execute(
                """
                UPDATE jobs
                   SET state='complete',error_code=NULL,count=?,sha256=?,
                       hash_ok=1,range_ok=1,completeness=?
                 WHERE job_id=? AND state='verifying'
                """,
                (count, digest, completeness, job["job_id"]),
            )
            return True

    def transition_job(
        self, job_id: str, state: str, *, error_code: str | None = None, **fields: Any
    ) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT state FROM jobs WHERE job_id=?", (job_id,)
            ).fetchone()
            if current is None or state not in _NEXT.get(current["state"], set()):
                raise ValueError("illegal verification state transition")
            assignments = ["state=?", "error_code=?"]
            values: list[Any] = [state, error_code]
            for name, value in fields.items():
                if name not in {"count", "sha256", "hash_ok", "range_ok", "completeness"}:
                    raise ValueError("invalid job field")
                assignments.append(f"{name}=?")
                values.append(int(value) if isinstance(value, bool) else value)
            values.extend((job_id, current["state"]))
            changed = connection.execute(
                f"UPDATE jobs SET {', '.join(assignments)} WHERE job_id=? AND state=?",
                tuple(values),
            ).rowcount
            if changed != 1:
                raise ValueError("illegal verification state transition")

    def cancel_job(self, job_id: str) -> str | None:
        """Atomically cancel one nonterminal job and report its actual final state."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT state FROM jobs WHERE job_id=?", (job_id,)
            ).fetchone()
            if row is None:
                return None
            if row["state"] in _TERMINAL:
                return str(row["state"])
            placeholders = ",".join("?" for _ in _NONTERMINAL)
            changed = connection.execute(
                f"""
                UPDATE jobs
                   SET state='cancelled',error_code='cancelled',cancelled=1,
                       cleanup_pending=CASE
                           WHEN artifact_dir_owned=1 THEN 1 ELSE cleanup_pending END
                 WHERE job_id=? AND state IN ({placeholders})
                """,
                (job_id, *_NONTERMINAL),
            ).rowcount
            if changed == 1:
                return "cancelled"
            current = connection.execute(
                "SELECT state FROM jobs WHERE job_id=?", (job_id,)
            ).fetchone()
            return str(current["state"]) if current else None

    def fail_job(
        self, job_id: str, error_code: str, **fields: Any
    ) -> bool:
        assignments = [
            "state='failed'",
            "error_code=?",
            "cleanup_pending=CASE WHEN artifact_dir_owned=1 THEN 1 ELSE cleanup_pending END",
        ]
        values: list[Any] = [error_code]
        for name, value in fields.items():
            if name not in {"count", "sha256", "hash_ok", "range_ok", "completeness"}:
                raise ValueError("invalid job field")
            assignments.append(f"{name}=?")
            values.append(int(value) if isinstance(value, bool) else value)
        placeholders = ",".join("?" for _ in _NONTERMINAL)
        values.extend((job_id, *_NONTERMINAL))
        with self._connect() as connection:
            changed = connection.execute(
                f"UPDATE jobs SET {', '.join(assignments)} WHERE job_id=? AND state IN ({placeholders})",
                tuple(values),
            ).rowcount
        return changed == 1

    def reconcile_interrupted_jobs(self) -> None:
        placeholders = ",".join("?" for _ in _NONTERMINAL)
        self.execute(
            f"""
            UPDATE jobs
               SET state='failed',error_code='interrupted',
                   cleanup_pending=CASE
                       WHEN artifact_dir_owned=1 THEN 1 ELSE cleanup_pending END
             WHERE state IN ({placeholders})
            """,
            _NONTERMINAL,
        )

    @staticmethod
    def _challenge_matches(
        row: sqlite3.Row,
        *,
        session_hash: str,
        kind: str,
        source_id: str,
        account_id: str,
        scope_fingerprint: str,
        payload: str,
        now: datetime,
    ) -> bool:
        try:
            expires_at = datetime.fromisoformat(row["expires_at"])
        except (TypeError, ValueError):
            return False
        return (
            not row["used"]
            and row["session_hash"] == session_hash
            and row["kind"] == kind
            and row["source_id"] == source_id
            and row["account_id"] == account_id
            and row["scope_fingerprint"] == scope_fingerprint
            and row["payload"] == payload
            and expires_at > now
        )

    @staticmethod
    def _source_matches(source: Mapping[str, Any], snapshot: Mapping[str, Any]) -> bool:
        return all(source[field] == snapshot[field] for field in _SOURCE_SNAPSHOT_FIELDS)

    @staticmethod
    def _grant_matches(
        grant: sqlite3.Row,
        job: Mapping[str, Any],
        *,
        now: datetime,
        require_unbound: bool = True,
    ) -> bool:
        try:
            expires_at = datetime.fromisoformat(grant["expires_at"])
        except (TypeError, ValueError):
            return False
        fields_match = all(
            grant[field] == job[field]
            for field in (
                "session_hash", "source_id", "account_id", "base_url",
                "client_version", "tool_version", "account_version", "nonce",
                "secret_ref", "secret_backend",
            )
        )
        return (
            fields_match
            and expires_at > now
            and (not require_unbound or (not grant["consumed"] and grant["job_id"] is None))
        )

    @staticmethod
    def now_text(value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat()

    @staticmethod
    def dump(value: dict[str, Any]) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"))
