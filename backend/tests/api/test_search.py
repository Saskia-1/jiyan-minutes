"""HTTP contract for private local search."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from chat_to_howto.api.search import create_search_router
from chat_to_howto.domain.search import SearchPage, SearchRequest, SearchResult


class StubSearch:
    def __init__(self) -> None:
        self.calls = []

    def search(self, request, *, session_id):
        self.calls.append((request, session_id))
        return SearchPage(
            results=(
                SearchResult(
                    message_id="message:v1:" + "a" * 64,
                    conversation_id="conversation-a",
                    timestamp=datetime(2026, 9, 1, tzinfo=timezone.utc),
                    sender_id="sender-a",
                    sender_name="Synthetic Sender",
                    text="safe result",
                    batch_id="batch-a",
                    snapshot_id="snapshot-a",
                ),
            ),
            next_cursor=None,
        )


def _client(*, resolve_search=None):
    service = StubSearch()
    guards = []

    def require_session():
        guards.append("session")
        return "session-a"

    def require_csrf():
        guards.append("csrf")

    app = FastAPI()
    app.include_router(
        create_search_router(
            get_search=lambda: service,
            require_session=require_session,
            require_csrf=require_csrf,
            resolve_search=resolve_search,
        )
    )
    return TestClient(app), service, guards


def test_search_can_resolve_opaque_conversation_handles_server_side() -> None:
    resolved = []

    def resolve_search(request, session_id):
        resolved.append((request, session_id))
        return SearchRequest(
            query=request.query,
            conversation_ids=("internal-conversation-a",),
            start_time=request.start_time,
            end_time=request.end_time,
            limit=request.limit,
            cursor=request.cursor,
        )

    client, service, guards = _client(resolve_search=resolve_search)
    response = client.post(
        "/api/search/from-selections",
        json={
            "source_id": "qq",
            "account_id": "account-a",
            "query": "safe",
            "conversation_selection_ids": ["opaque-a"],
            "start_time": "2026-09-01T00:00:00Z",
            "end_time": "2026-09-02T00:00:00Z",
            "limit": 25,
        },
    )

    assert response.status_code == 200, response.json()
    assert resolved[0][1] == "session-a"
    assert service.calls[0][0].conversation_ids == ("internal-conversation-a",)
    assert guards == ["session", "csrf"]


def test_search_post_uses_injected_session_and_csrf() -> None:
    client, service, guards = _client()
    response = client.post(
        "/api/search",
        json={
            "query": "safe",
            "conversation_ids": ["conversation-a"],
            "start_time": "2026-09-01T00:00:00Z",
            "end_time": "2026-09-02T00:00:00Z",
            "limit": 25,
        },
    )

    assert response.status_code == 200
    assert response.json()["results"][0]["batch_id"] == "batch-a"
    assert len(service.calls) == 1
    assert service.calls[0][1] == "session-a"
    assert guards == ["session", "csrf"]


def test_search_post_bounds_inputs_before_calling_service() -> None:
    client, service, _ = _client()
    response = client.post(
        "/api/search",
        json={
            "query": "x" * 513,
            "conversation_ids": ["conversation-a"],
            "start_time": "2026-09-01T00:00:00Z",
            "end_time": "2026-09-02T00:00:00Z",
            "limit": 10_000,
        },
    )

    assert response.status_code == 422
    assert service.calls == []


def test_main_application_mounts_search_with_real_local_guards(p0) -> None:
    p0.start()
    p0.bootstrap()
    body = {
        "query": "synthetic",
        "conversation_ids": ["conversation-a"],
        "start_time": "2026-09-01T00:00:00Z",
        "end_time": "2026-09-02T00:00:00Z",
        "limit": 25,
    }

    assert p0.request("POST", "/api/search", csrf=False, json=body).status_code == 401
    assert p0.request(
        "POST",
        "/api/search",
        headers={"Origin": "https://evil.example"},
        json=body,
    ).status_code == 403
    response = p0.request("POST", "/api/search", json=body)

    assert response.status_code == 200
    assert response.json() == {"results": [], "next_cursor": None}
    assert p0.app.state.chat_search is not None
