"""Session-bound search orchestration and opaque cursor validation."""

from __future__ import annotations

from base64 import urlsafe_b64decode, urlsafe_b64encode
from datetime import datetime
from hashlib import sha256
import json

from pydantic import ValidationError

from chat_to_howto.api.session import digest
from chat_to_howto.domain.search import SearchPage, SearchRequest, SearchResult
from chat_to_howto.repositories.chat import ChatRepository


class ChatSearchService:
    """Search only completed snapshots owned by the requesting local session."""

    def __init__(self, repository: ChatRepository) -> None:
        self._repository = repository

    @staticmethod
    def _criteria_fingerprint(request: SearchRequest) -> str:
        canonical = json.dumps(
            {
                "version": 1,
                "query": request.query,
                "conversation_ids": sorted(request.conversation_ids),
                "snapshot_id": request.snapshot_id,
                "start_time": request.start_time.isoformat(),
                "end_time": request.end_time.isoformat(),
                "include_undated": request.include_undated,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return sha256(canonical.encode("utf-8")).hexdigest()

    @classmethod
    def _decode_cursor(
        cls, request: SearchRequest
    ) -> tuple[str, str] | tuple[int, str] | None:
        if request.cursor is None:
            return None
        try:
            padding = "=" * (-len(request.cursor) % 4)
            payload = json.loads(
                urlsafe_b64decode((request.cursor + padding).encode("ascii"))
            )
            if (
                not isinstance(payload, dict)
                or payload.get("v") != 1
                or payload.get("f") != cls._criteria_fingerprint(request)
                or not isinstance(payload.get("m"), str)
            ):
                raise ValueError
            message_id = payload["m"]
            if not message_id or len(message_id) > 128:
                raise ValueError
            if request.include_undated:
                order = payload.get("o")
                if type(order) is not int or order < 0:
                    raise ValueError
                return order, message_id
            timestamp = datetime.fromisoformat(payload["t"])
            if timestamp.tzinfo is None or timestamp.utcoffset() is None:
                raise ValueError
            return timestamp.isoformat(), message_id
        except (UnicodeError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            raise ValueError("search cursor does not match this request") from None

    @classmethod
    def _encode_cursor(cls, request: SearchRequest, result: SearchResult) -> str:
        payload = json.dumps(
            {
                "v": 1,
                "f": cls._criteria_fingerprint(request),
                "t": result.timestamp.isoformat() if result.timestamp else None,
                "o": result.source_order if result.source_order is not None else 9223372036854775807,
                "m": result.message_id,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return urlsafe_b64encode(payload).decode("ascii").rstrip("=")

    def search(self, request: SearchRequest, *, session_id: str) -> SearchPage:
        if not isinstance(session_id, str) or not session_id:
            raise PermissionError("local session is unavailable")
        cursor = self._decode_cursor(request)
        rows = self._repositohanwery.search_messages(
            session_digest=digest(session_id),
            query=request.query,
            conversation_ids=request.conversation_ids,
            snapshot_id=request.snapshot_id,
            start_time_utc=request.start_time.isoformat(),
            end_time_utc=request.end_time.isoformat(),
            cursor=cursor,
            limit=request.limit + 1,
            include_undated=request.include_undated,
        )
        try:
            results = tuple(
                SearchResult(
                    message_id=str(row["message_id"]),
                    conversation_id=str(row["conversation_id"]),
                    timestamp=datetime.fromisoformat(str(row["sent_at_utc"])) if row["sent_at_utc"] is not None else None,
                    source_order=row["source_order"],
                    sender_id=row["sender_id"],
                    sender_name=row["sender_name"],
                    text=row["text"],
                    batch_id=str(row["batch_id"]),
                    snapshot_id=str(row["snapshot_id"]),
                )
                for row in rows[: request.limit]
            )
        except (KeyError, TypeError, ValueError, ValidationError):
            raise RuntimeError("stored search index is invalid") from None
        next_cursor = (
            self._encode_cursor(request, results[-1])
            if len(rows) > request.limit and results
            else None
        )
        return SearchPage(results=results, next_cursor=next_cursor)
