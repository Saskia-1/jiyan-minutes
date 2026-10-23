"""Public generation request bounds for message exclusions."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from chat_to_howto.api.generation import SelectionGenerationRequest


def _request(excluded_message_ids: list[str]) -> SelectionGenerationRequest:
    return SelectionGenerationRequest.model_validate(
        {
            "source_id": "qq",
            "account_id": "account-a",
            "conversation_selection_ids": ["selection-a"],
            "snapshot_id": "snapshot:p1",
            "start_time": "2026-09-01T00:00:00Z",
            "end_time": "2026-09-02T00:00:00Z",
            "topic": "target",
            "mode": "complete",
            "excluded_message_ids": excluded_message_ids,
        }
    )


def test_generation_request_accepts_exact_exclusion_limit() -> None:
    request = _request([f"message:{index}" for index in range(2_000)])

    assert len(request.excluded_message_ids) == 2_000


@pytest.mark.parametrize(
    "excluded_message_ids",
    [
        [f"message:{index}" for index in range(2_001)],
        ["message:a", "message:a"],
        ["../message:a"],
    ],
)
def test_generation_request_rejects_unbounded_duplicate_or_malformed_exclusions(
    excluded_message_ids: list[str],
) -> None:
    with pytest.raises(ValidationError):
        _request(excluded_message_ids)


def test_topic_expansion_falls_back_to_original_term_when_provider_unavailable(p0) -> None:
    p0.start()
    p0.bootstrap()

    preview = p0.request("POST", "/api/generation/topic-expansion/previews",
        json={"topic": "就业"})
    assert preview.status_code == 200
    approved = preview.json()
    response = p0.request("POST", "/api/generation/topic-expansion", json={
        "preview_id": approved["preview_id"],
        "preview_fingerprint": approved["preview_fingerprint"], "confirmed": True,
    })

    assert response.status_code == 200
    assert response.json() == {"topic": "就业", "terms": ["就业"], "degraded": True}


def test_topic_expansion_api_rejects_direct_dispatch_and_reused_authorization(p0) -> None:
    p0.start()
    p0.bootstrap()
    direct = p0.request("POST", "/api/generation/topic-expansion", json={"topic": "就业"})
    assert direct.status_code == 422
    preview = p0.request("POST", "/api/generation/topic-expansion/previews",
        json={"topic": "联系 13800138000 讨论就业"}).json()
    assert "13800138000" not in preview["outbound_text"]
    body = {"preview_id": preview["preview_id"],
        "preview_fingerprint": preview["preview_fingerprint"], "confirmed": False}
    assert p0.request("POST", "/api/generation/topic-expansion", json=body).status_code == 403
    body["confirmed"] = True
    assert p0.request("POST", "/api/generation/topic-expansion", json=body).status_code == 200
    assert p0.request("POST", "/api/generation/topic-expansion", json=body).status_code == 403
