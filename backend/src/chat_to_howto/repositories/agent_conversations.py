"""Bounded server-owned conversation state with owner isolation and atomic CAS.

Only the conversation service supplies this private JSON. Consent previews and
source excerpts belong in memory, never in this persistence layer.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import re
import secrets
import sqlite3
from typing import Any

from chat_to_howto.repositories.chat import ChatRepository

_MAX_STATE_BYTES = 1_048_576
_RETENTION = timedelta(days=7)
_EXPIRED_TEXT = "[消息正文已过期清理]"


def _owner(session_digest: str) -> None:
    if not isinstance(session_digest, str) or re.fullmatch(r"[0-9a-f]{64}", session_digest) is None:
        raise ValueError("invalid conversation owner")


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("invalid conversation timestamp")
    return value.astimezone(timezone.utc)


def _timestamp(value: Any, fallback: datetime) -> datetime:
    if isinstance(value, str):
        try:
            return _utc(datetime.fromisoformat(value))
        except ValueError:
            pass
    return fallback


def _encode(state: dict[str, Any]) -> str:
    if not isinstance(state, dict):
        raise ValueError("invalid conversation state")
    try:
        encoded = json.dumps(state, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > _MAX_STATE_BYTES:
            raise ValueError
    except (TypeError, ValueError, OverflowError, RecursionError, UnicodeError):
        raise ValueError("invalid conversation state") from None
    return encoded


def _row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "conversation_id": row["conversation_id"],
        "revision": row["revision"],
        "created_at_utc": row["created_at_utc"],
        "updated_at_utc": row["updated_at_utc"],
        "state": json.loads(row["state_json"]),
    }


def _scrub(state: dict[str, Any], *, created: datetime, now: datetime) -> None:
    """Use each original item timestamp, never the conversation's last save."""
    context_expired = False
    for message in state.get("messages", []):
        if not isinstance(message, dict):
            continue
        timestamp = _timestamp(message.get("created_at", message.get("created_at_utc")), created)
        if timestamp + _RETENTION <= now:
            if message.get("text") != _EXPIRED_TEXT:
                context_expired = True
            # Keep only safe identifiers and artifact metadata when the body dies.
            for key in tuple(message):
                if key not in {
                    "message_id", "role", "text", "created_at", "created_at_utc",
                    "run_id", "document_id", "version_id", "artifact_revision", "client_message_id",
                }:
                    del message[key]
            message["text"] = _EXPIRED_TEXT
    if context_expired and "title" in state:
        state["title"] = "历史会话"

    sources = state.get("sources", [])
    kept_sources = [source for source in sources if isinstance(source, dict) and
        _timestamp(source.get("expires_at", source.get("expires_at_utc")), created + _RETENTION) > now]
    if kept_sources != sources:
        state["sources"] = kept_sources
        context_expired = True

    runtime = state.get("runtime")
    if isinstance(runtime, dict) and runtime:
        timestamp = _timestamp(runtime.get("created_at", runtime.get("created_at_utc")), created)
        context_expired = context_expired or timestamp + _RETENTION <= now
    run = state.get("active_run")
    if isinstance(run, dict):
        timestamp = _timestamp(run.get("created_at", run.get("created_at_utc")), created)
        context_expired = context_expired or timestamp + _RETENTION <= now
        if context_expired and run.get("state") in {"waiting_approval", "running"}:
            run["state"] = "cancelled"
            run["stage"] = "cancelled"
            run["error_code"] = "conversation_context_expired"
            run["approval"] = None
    if context_expired and "runtime" in state:
        state["runtime"] = {}


class AgentConversationRepository:
    """Share the chat connection, lock and write transaction across all methods."""

    def __init__(self, chat_repository: ChatRepository) -> None:
        self._chat = chat_repository

    def create(self, *, session_digest: str, now_utc: datetime, state: dict[str, Any]) -> dict[str, Any]:
        _owner(session_digest)
        now = _utc(now_utc).isoformat()
        encoded = _encode(state)
        conversation_id = f"conversation:{secrets.token_urlsafe(24)}"
        with self._chat._transaction():
            self._chat._connection.execute(
                """INSERT INTO chat_agent_conversations(
                    conversation_id,session_digest,revision,created_at_utc,updated_at_utc,state_json
                ) VALUES(?,?,1,?,?,?)""",
                (conversation_id, session_digest, now, now, encoded),
            )
            return self.get(conversation_id, session_digest=session_digest)

    def list(self, *, session_digest: str) -> tuple[dict[str, Any], ...]:
        _owner(session_digest)
        with self._chat._lock:
            rows = self._chat._connection.execute(
                """SELECT * FROM chat_agent_conversations WHERE session_digest=?
                   ORDER BY updated_at_utc DESC,conversation_id DESC""", (session_digest,),
            ).fetchall()
            return tuple(_row(row) for row in rows)

    def get(self, conversation_id: str, *, session_digest: str) -> dict[str, Any]:
        _owner(session_digest)
        with self._chat._lock:
            row = self._chat._connection.execute(
                "SELECT * FROM chat_agent_conversations WHERE conversation_id=? AND session_digest=?",
                (conversation_id, session_digest),
            ).fetchone()
            if row is None:
                raise ValueError("conversation not found")
            return _row(row)

    def update(self, conversation_id: str, *, session_digest: str, expected_revision: int,
               state: dict[str, Any], now_utc: datetime) -> dict[str, Any]:
        _owner(session_digest)
        if type(expected_revision) is not int or expected_revision < 1:
            raise ValueError("invalid conversation revision")
        encoded = _encode(state)
        now = _utc(now_utc).isoformat()
        with self._chat._transaction():
            result = self._chat._connection.execute(
                """UPDATE chat_agent_conversations SET state_json=?,updated_at_utc=?,revision=revision+1
                   WHERE conversation_id=? AND session_digest=? AND revision=?""",
                (encoded, now, conversation_id, session_digest, expected_revision),
            )
            if result.rowcount != 1:
                raise ValueError("conversation revision conflict")
            return self.get(conversation_id, session_digest=session_digest)

    def sweep(self, *, now_utc: datetime) -> int:
        now = _utc(now_utc)
        changed = 0
        with self._chat._transaction():
            rows = self._chat._connection.execute("SELECT * FROM chat_agent_conversations").fetchall()
            for row in rows:
                state = json.loads(row["state_json"])
                _scrub(state, created=_timestamp(row["created_at_utc"], now - _RETENTION), now=now)
                encoded = _encode(state)
                if encoded == row["state_json"]:
                    continue
                self._chat._connection.execute(
                    """UPDATE chat_agent_conversations SET state_json=?,revision=revision+1,updated_at_utc=?
                       WHERE conversation_id=? AND session_digest=? AND revision=?""",
                    (encoded, now.isoformat(), row["conversation_id"], row["session_digest"], row["revision"]),
                )
                changed += 1
        return changed
