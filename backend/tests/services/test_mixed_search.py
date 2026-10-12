"""Mixed retrieval is explicit, typed, deduplicated, and session-owned."""

from datetime import datetime, timezone

import pytest

from chat_to_howto.api.session import digest
from chat_to_howto.domain.messages import MessageInput, StoredMessage
from chat_to_howto.repositories.chat import ChatRepository
from chat_to_howto.repositories.content import ContentRepository


def _message(source_id: str, order: int, text: str) -> StoredMessage:
    return StoredMessage.from_input(MessageInput(
        source_id="manual", account_id="owner", conversation_id="chat",
        source_message_id=source_id, source_file_id=None, page_slice_id=None,
        source_order=order, sent_at_utc=datetime(2026, 10, 12, 10, order, tzinfo=timezone.utc),
        text=text,
    ))


class _OwnedChat:
    def __init__(self, messages): self.messages = messages

    def generation_snapshot_messages(self, *, session_digest, snapshot_id):
        if session_digest != digest("owner") or snapshot_id != "snapshot-1":
            raise PermissionError("not owned")
        return "chat-batch-1", self.messages


@pytest.fixture
def fixture(tmp_path):
    from chat_to_howto.services.mixed_search import MixedSearchService

    chat = ChatRepository(tmp_path / "chat.sqlite3")
    content = ContentRepository(chat)
    content.save_batch(batch_id="note-batch-1", session_digest=digest("owner"), created_at_utc="2026-10-12T00:00:00+00:00",
        images=[], units=[
            {"unit_id": "content:v1:note:" + "a" * 64, "image_id": None, "kind": "note_passage", "group_id": "note-1", "source_order": 1, "text": "音乐方案", "sender_name": None, "sent_at": None},
            {"unit_id": "content:v1:note:" + "b" * 64, "image_id": None, "kind": "note_passage", "group_id": "note-1", "source_order": 2, "text": "另一个想法", "sender_name": None, "sent_at": None},
        ])
    service = MixedSearchService(_OwnedChat((_message("first", 1, "音乐方案"), _message("second", 2, "音乐执行"))), content)
    yield service
    chat.close()


def test_multi_term_hits_are_deduplicated_but_sources_remain_distinct(fixture):
    from chat_to_howto.domain.mixed_sources import MixedSearchRequest, SourceRef

    request = MixedSearchRequest(source_refs=(SourceRef(kind="chat_snapshot", id="snapshot-1"), SourceRef(kind="content_batch", id="note-batch-1")), include_undated=True, terms=("音乐", "方案"))
    hits = fixture.search(request, session_id="owner")
    assert len(hits) == 3
    assert len({hit.unit_id for hit in hits}) == 3
    assert [hit.source_kind for hit in hits] == ["chat_message", "chat_message", "note_passage"]
    assert [hit.text for hit in hits] == ["音乐方案", "音乐执行", "音乐方案"]


def test_unknown_time_requires_opt_in_and_time_range_filters_known_chat(fixture):
    from chat_to_howto.domain.mixed_sources import MixedSearchRequest, SourceRef

    sources = (SourceRef(kind="chat_snapshot", id="snapshot-1"), SourceRef(kind="content_batch", id="note-batch-1"))
    without_undated = fixture.search(MixedSearchRequest(source_refs=sources, include_undated=False, terms=("音乐",)), session_id="owner")
    assert all(hit.source_kind == "chat_message" for hit in without_undated)
    range_only = fixture.search(MixedSearchRequest(source_refs=sources, include_undated=True, terms=("音乐",), start_time=datetime(2026, 10, 12, 10, 1, tzinfo=timezone.utc), end_time=datetime(2026, 10, 12, 10, 2, tzinfo=timezone.utc)), session_id="owner")
    assert [hit.text for hit in range_only] == ["音乐方案", "音乐方案"]


def test_unowned_source_and_oversized_source_fail_closed(fixture):
    from chat_to_howto.domain.mixed_sources import MixedSearchRequest, SourceRef

    with pytest.raises(PermissionError):
        fixture.search(MixedSearchRequest(source_refs=(SourceRef(kind="content_batch", id="note-batch-1"),), include_undated=True, terms=("音乐",)), session_id="other")
    with pytest.raises(PermissionError):
        fixture.search(MixedSearchRequest(source_refs=(SourceRef(kind="chat_snapshot", id="unknown"),), include_undated=True, terms=("音乐",)), session_id="owner")
    fixture._chat.messages = tuple(_message(f"many-{index}", 1, "音乐") for index in range(2_001))
    with pytest.raises(ValueError, match="limit"):
        fixture.search(MixedSearchRequest(source_refs=(SourceRef(kind="chat_snapshot", id="snapshot-1"),), include_undated=True, terms=("音乐",)), session_id="owner")


def test_screenshot_chat_with_confirmed_time_obeys_time_filter(fixture):
    from chat_to_howto.domain.mixed_sources import MixedSearchRequest, SourceRef

    fixture._content.save_batch(batch_id="screenshot-chat", session_digest=digest("owner"), created_at_utc="2026-10-12T00:00:00+00:00", images=[], units=[
        {"unit_id": "content:v1:chat:" + "c" * 64, "image_id": None, "kind": "chat_message", "group_id": "chat-1", "source_order": 0, "text": "音乐记录", "sender_name": "甲", "sent_at": "2026-10-12T10:05:00+00:00"},
    ])
    source = (SourceRef(kind="content_batch", id="screenshot-chat"),)
    included = fixture.search(MixedSearchRequest(source_refs=source, include_undated=False, terms=("音乐",), start_time=datetime(2026, 10, 12, 10, 0, tzinfo=timezone.utc), end_time=datetime(2026, 10, 12, 10, 10, tzinfo=timezone.utc)), session_id="owner")
    assert len(included) == 1 and included[0].sender_name == "甲"
    assert fixture.search(MixedSearchRequest(source_refs=source, include_undated=True, terms=("音乐",), start_time=datetime(2026, 10, 12, 11, 0, tzinfo=timezone.utc), end_time=datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)), session_id="owner") == ()
