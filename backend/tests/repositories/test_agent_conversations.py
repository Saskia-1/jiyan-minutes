"""Synthetic conversation persistence, isolation, concurrency, and retention."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from chat_to_howto.repositories import agent_conversations
from chat_to_howto.repositories.chat import ChatRepository

NOW = datetime(2026, 10, 23, tzinfo=timezone.utc)
OWNER = "a" * 64
OTHER = "b" * 64


@pytest.fixture
def store(tmp_path: Path):
    chat = ChatRepository(tmp_path / "synthetic.sqlite3")
    try:
        yield agent_conversations.AgentConversationRepository(chat)
    finally:
        chat.close()

def test_crud_returns_detached_state_and_never_crosses_owner(store):
    state = {"title": "synthetic", "messages": [], "sources": [], "active_run": None}
    first = store.create(session_digest=OWNER, now_utc=NOW, state=state)
    state["title"] = "mutated"
    assert first["revision"] == 1
    assert first["created_at_utc"] == NOW.isoformat()
    assert store.get(first["conversation_id"], session_digest=OWNER)["state"]["title"] == "synthetic"
    assert store.list(session_digest=OTHER) == ()
    with pytest.raises(ValueError):
        store.get(first["conversation_id"], session_digest=OTHER)
    with pytest.raises(ValueError):
        store.update(first["conversation_id"], session_digest=OTHER, expected_revision=1,
                     state={"title": "stolen"}, now_utc=NOW)
    later = store.update(first["conversation_id"], session_digest=OWNER, expected_revision=1,
                         state={"title": "updated"}, now_utc=NOW + timedelta(hours=1))
    assert later["revision"] == 2
    assert later["created_at_utc"] == first["created_at_utc"]
    assert later["state"] == {"title": "updated"}
    assert store.list(session_digest=OWNER) == (later,)
    with pytest.raises(ValueError):
        store.update(first["conversation_id"], session_digest=OWNER, expected_revision=1,
                     state={}, now_utc=NOW)


def test_cas_is_atomic_across_connections(tmp_path: Path):
    path = tmp_path / "synthetic.sqlite3"
    chats = [ChatRepository(path), ChatRepository(path)]
    stores = [agent_conversations.AgentConversationRepository(chat) for chat in chats]
    try:
        row = stores[0].create(session_digest=OWNER, now_utc=NOW, state={})
        def write(index):
            try:
                return stores[index].update(row["conversation_id"], session_digest=OWNER,
                    expected_revision=1, state={"winner": index}, now_utc=NOW)
            except ValueError:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(write, [0, 1]))
        assert sum(result is not None for result in results) == 1
        assert stores[0].get(row["conversation_id"], session_digest=OWNER)["revision"] == 2
    finally:
        for chat in chats:
            chat.close()


@pytest.mark.parametrize("state", [{"value": "中" * 400_000}, {"value": float("nan")}, [], {"value": object()}])
def test_rejects_oversized_or_non_json_state_without_persisting(store, state):
    with pytest.raises(ValueError):
        store.create(session_digest=OWNER, now_utc=NOW, state=state)
    assert store.list(session_digest=OWNER) == ()


def test_sweep_scrubs_at_absolute_message_deadline_and_keeps_artifact_refs(store):
    old = NOW - timedelta(days=7)
    state = {
        "title": "synthetic copied title",
        "messages": [{"message_id": "old", "role": "user", "text": "synthetic expired message",
                      "created_at": old.isoformat(), "document_id": "doc", "version_id": "ver", "artifact_revision": 1},
                     {"message_id": "new", "role": "user", "text": "synthetic current message", "created_at": NOW.isoformat()}],
        "sources": [{"kind": "content_batch", "id": "expired", "expires_at": NOW.isoformat()},
                    {"kind": "content_batch", "id": "current", "expires_at": (NOW + timedelta(days=1)).isoformat()}],
        "active_run": {"run_id": "run", "state": "waiting_approval", "created_at": old.isoformat(),
                       "approval": {"approval_id": "approval"}, "document_id": "doc", "version_id": "ver", "artifact_revision": 1},
        "runtime": {"request": "synthetic expired context"},
    }
    row = store.create(session_digest=OWNER, now_utc=old, state=state)
    updated = store.update(row["conversation_id"], session_digest=OWNER, expected_revision=1, state=state, now_utc=NOW)
    assert store.sweep(now_utc=NOW - timedelta(microseconds=1)) == 0
    assert store.sweep(now_utc=NOW) == 1
    clean = store.get(row["conversation_id"], session_digest=OWNER)
    assert clean["revision"] == updated["revision"] + 1
    assert clean["state"]["messages"][0]["text"] != "synthetic expired message"
    assert clean["state"]["messages"][1]["text"] == "synthetic current message"
    assert clean["state"]["messages"][0]["document_id"] == "doc"
    assert clean["state"]["messages"][0]["version_id"] == "ver"
    assert clean["state"]["messages"][0]["artifact_revision"] == 1
    assert clean["state"]["sources"] == [state["sources"][1]]
    assert clean["state"]["active_run"]["state"] == "cancelled"
    assert clean["state"]["active_run"]["approval"] is None
    assert clean["state"]["active_run"]["document_id"] == "doc"
    assert "synthetic expired context" not in str(clean["state"])
    assert clean["state"]["title"] != "synthetic copied title"
    assert store.sweep(now_utc=NOW) == 0


def test_reopening_keeps_messages_and_document_metadata(tmp_path: Path):
    path = tmp_path / "synthetic.sqlite3"
    chat = ChatRepository(path)
    store = agent_conversations.AgentConversationRepository(chat)
    row = store.create(session_digest=OWNER, now_utc=NOW,
        state={"messages": [{"message_id": "m", "text": "synthetic", "created_at": NOW.isoformat(),
                             "document_id": "doc", "version_id": "version", "artifact_revision": 2}]})
    chat.close()
    chat = ChatRepository(path)
    try:
        assert agent_conversations.AgentConversationRepository(chat).get(row["conversation_id"], session_digest=OWNER) == row
    finally:
        chat.close()


def test_runtime_expires_without_messages_and_with_a_recent_save(store):
    old = NOW - timedelta(days=7)
    row = store.create(session_digest=OWNER, now_utc=old, state={
        "messages": [], "sources": [],
        "runtime": {"created_at": old.isoformat(), "requests": ["synthetic context"]},
        "active_run": {"created_at": NOW.isoformat(), "state": "running", "approval": None},
    })
    store.update(row["conversation_id"], session_digest=OWNER, expected_revision=1,
                 state=row["state"], now_utc=NOW)
    assert store.sweep(now_utc=NOW) == 1
    state = store.get(row["conversation_id"], session_digest=OWNER)["state"]
    assert state["runtime"] == {}
    assert state["active_run"]["state"] == "cancelled"
    assert store.sweep(now_utc=NOW) == 0


def test_sweep_uses_per_item_timestamps_and_handles_source_expiry_alias(store):
    old = NOW - timedelta(days=8)
    row = store.create(session_digest=OWNER, now_utc=old, state={
        "messages": [{"message_id": "fresh", "text": "synthetic current message", "created_at": NOW.isoformat()}],
        "runtime": {"created_at": NOW.isoformat(), "requests": ["synthetic current request"]},
        "sources": [{"id": "current", "expires_at_utc": (NOW + timedelta(days=1)).isoformat()}],
    })
    assert store.sweep(now_utc=NOW) == 0
    assert store.get(row["conversation_id"], session_digest=OWNER) == row
    assert store.sweep(now_utc=NOW + timedelta(days=1)) == 1
    state = store.get(row["conversation_id"], session_digest=OWNER)["state"]
    assert state["sources"] == []
    assert state["runtime"] == {}
    assert state["messages"][0]["text"] == "synthetic current message"


def test_json_limit_measures_utf8_bytes_and_failed_update_is_atomic(store):
    row = store.create(session_digest=OWNER, now_utc=NOW, state={"value": "x" * 1_048_564})
    with pytest.raises(ValueError):
        store.update(row["conversation_id"], session_digest=OWNER, expected_revision=1,
                     state={"value": "x" * 1_048_565}, now_utc=NOW)
    assert store.get(row["conversation_id"], session_digest=OWNER) == row


@pytest.mark.parametrize("owner", ["", "a" * 63, "g" * 64, "A" * 64, None])
def test_invalid_owner_is_rejected_before_writing(store, owner):
    with pytest.raises(ValueError):
        store.create(session_digest=owner, now_utc=NOW, state={})
    assert store.list(session_digest=OWNER) == ()
def test_conversation_id_stays_valid_when_random_token_starts_with_a_dash(tmp_path, monkeypatch):
    from chat_to_howto.repositories import agent_conversations
    from chat_to_howto.repositories.chat import ChatRepository
    from datetime import datetime, timezone
    import re
    monkeypatch.setattr(agent_conversations.secrets, "token_urlsafe", lambda _: "-edge_case")
    chat = ChatRepository(tmp_path / "opaque.sqlite3")
    try:
        row = agent_conversations.AgentConversationRepository(chat).create(
            session_digest="a" * 64, now_utc=datetime(2026, 10, 23, tzinfo=timezone.utc), state={})
        assert re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", row["conversation_id"])
    finally:
        chat.close()
