"""Focused content SQL through the existing chat repository transaction and lock."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from chat_to_howto.repositories.chat import ChatRepository


class ContentRepository:
    def __init__(self, chat_repository: ChatRepository) -> None:
        self._chat = chat_repository

    def save_batch(
        self, *, batch_id: str, session_digest: str, created_at_utc: str,
        units: Sequence[dict[str, Any]], images: Sequence[dict[str, Any]],
    ) -> None:
        with self._chat._transaction():
            connection = self._chat._connection
            connection.execute(
                "INSERT INTO content_batches(batch_id,session_digest,created_at_utc,unit_count) VALUES(?,?,?,?)",
                (batch_id, session_digest, created_at_utc, len(units)),
            )
            for image in images:
                connection.execute(
                    "INSERT INTO content_images(image_id,batch_id,input_order,sha256,basename,mime_type) VALUES(?,?,?,?,?,?)",
                    (image["image_id"], batch_id, image["input_order"], image["sha256"], image["basename"], image["mime_type"]),
                )
            for unit in units:
                connection.execute(
                    "INSERT INTO content_units(unit_id,batch_id,image_id,kind,group_id,source_order,text,sender_name,sent_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    (unit["unit_id"], batch_id, unit["image_id"], unit["kind"], unit["group_id"], unit["source_order"], unit["text"], unit["sender_name"], unit["sent_at"]),
                )

    def list_batches(self, *, session_digest: str) -> tuple[dict[str, Any], ...]:
        with self._chat._lock:
            rows = self._chat._connection.execute(
                "SELECT batch_id,created_at_utc,unit_count FROM content_batches WHERE session_digest=? AND NOT EXISTS (SELECT 1 FROM chat_raw_retirements r WHERE r.target_kind='content_batch' AND r.target_id=content_batches.batch_id) ORDER BY created_at_utc DESC,batch_id DESC",
                (session_digest,),
            ).fetchall()
            return tuple(dict(row) for row in rows)

    def list_images(self, batch_id: str, *, session_digest: str) -> tuple[dict[str, Any], ...]:
        with self._chat._lock:
            rows = self._chat._connection.execute(
                """SELECT i.image_id,i.input_order,i.sha256,i.basename,i.mime_type
                   FROM content_images i JOIN content_batches b ON b.batch_id=i.batch_id
                   WHERE i.batch_id=? AND b.session_digest=? AND NOT EXISTS (SELECT 1 FROM chat_raw_retirements r WHERE r.target_kind='content_batch' AND r.target_id=b.batch_id) ORDER BY i.input_order,i.image_id""",
                (batch_id, session_digest),
            ).fetchall()
            return tuple(dict(row) for row in rows)

    def list_units(self, batch_id: str, *, session_digest: str) -> tuple[dict[str, Any], ...]:
        with self._chat._lock:
            rows = self._chat._connection.execute(
                """SELECT u.unit_id,u.batch_id,u.image_id,u.kind,u.group_id,u.source_order,u.text,u.sender_name,u.sent_at
                   FROM content_units u JOIN content_batches b ON b.batch_id=u.batch_id
                   WHERE u.batch_id=? AND b.session_digest=? AND NOT EXISTS (SELECT 1 FROM chat_raw_retirements r WHERE r.target_kind='content_batch' AND r.target_id=b.batch_id) ORDER BY u.source_order,u.unit_id""",
                (batch_id, session_digest),
            ).fetchall()
            return tuple(dict(row) for row in rows)

    def owned_image(self, image_id: str, *, session_digest: str) -> dict[str, Any] | None:
        with self._chat._lock:
            row = self._chat._connection.execute(
                """SELECT i.basename,i.mime_type FROM content_images i
                   JOIN content_batches b ON b.batch_id=i.batch_id
                   WHERE i.image_id=? AND b.session_digest=? AND NOT EXISTS (SELECT 1 FROM chat_raw_retirements r WHERE r.target_kind='content_batch' AND r.target_id=b.batch_id)""",
                (image_id, session_digest),
            ).fetchone()
            return dict(row) if row is not None else None
