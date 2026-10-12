"""Bounded, session-owned local recall across chat snapshots and note batches."""

from __future__ import annotations

from datetime import datetime, timezone

from chat_to_howto.api.session import digest
from chat_to_howto.domain.mixed_sources import MixedSearchHit, MixedSearchRequest
from chat_to_howto.repositories.chat import ChatRepository
from chat_to_howto.repositories.content import ContentRepository


_MAX_SOURCE_UNITS = 2_000


class MixedSearchService:
    def __init__(self, chat_repository: ChatRepository, content_repository: ContentRepository) -> None:
        self._chat = chat_repository
        self._content = content_repository

    def search(self, request: MixedSearchRequest, *, session_id: str) -> tuple[MixedSearchHit, ...]:
        if not session_id:
            raise PermissionError("local session unavailable")
        owner = digest(session_id)
        # Resolve every source before evaluating a term. An unowned source cannot
        # produce a partial result or be silently ignored.
        resolved: list[tuple[object, tuple[object, ...]]] = []
        count = 0
        for source in request.source_refs:
            if source.kind == "chat_snapshot":
                _, units = self._chat.generation_snapshot_messages(session_digest=owner, snapshot_id=source.id)
            else:
                units = self._content.list_units(source.id, session_digest=owner)
                if not units:
                    raise PermissionError("content source unavailable")
            count += len(units)
            if count > _MAX_SOURCE_UNITS:
                raise ValueError("mixed search source limit exceeded")
            resolved.append((source, units))

        terms = tuple(dict.fromkeys(term.casefold() for term in request.terms if term))
        seen: set[str] = set()
        hits: list[MixedSearchHit] = []
        for source, units in resolved:
            if source.kind == "chat_snapshot":
                ordered = sorted(units, key=lambda item: (
                    item.sent_at_utc is None,
                    item.sent_at_utc or datetime.max.replace(tzinfo=timezone.utc),
                    item.source_order if item.source_order is not None else 2**63 - 1,
                    item.message_id,
                ))
                for message in ordered:
                    if not message.text:
                        continue
                    timestamp = message.sent_at_utc
                    if timestamp is None and not request.include_undated:
                        continue
                    if timestamp is not None and request.start_time is not None and not (request.start_time <= timestamp < request.end_time):
                        continue
                    if terms and not any(term in message.text.casefold() for term in terms):
                        continue
                    if message.message_id in seen:
                        continue
                    seen.add(message.message_id)
                    hits.append(MixedSearchHit(
                        unit_id=message.message_id, source_kind="chat_message",
                        source_ref=source, text=message.text, sender_name=message.sender_name,
                        timestamp=timestamp, source_order=message.source_order,
                    ))
            else:
                for unit in units:
                    timestamp = datetime.fromisoformat(unit["sent_at"]) if unit["sent_at"] is not None else None
                    if timestamp is None and not request.include_undated:
                        continue
                    if timestamp is not None and request.start_time is not None and not (request.start_time <= timestamp < request.end_time):
                        continue
                    if terms and not any(term in unit["text"].casefold() for term in terms):
                        continue
                    if unit["unit_id"] in seen:
                        continue
                    seen.add(unit["unit_id"])
                    hits.append(MixedSearchHit(
                        unit_id=unit["unit_id"], source_kind=unit["kind"],
                        source_ref=source, text=unit["text"], sender_name=unit["sender_name"],
                        timestamp=timestamp,
                        source_order=unit["source_order"], image_id=unit["image_id"],
                    ))
        return tuple(hits)
