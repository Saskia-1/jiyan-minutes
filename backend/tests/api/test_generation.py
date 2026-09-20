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
