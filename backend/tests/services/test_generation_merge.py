"""Deterministic merge retains provenance while coalescing duplicate claims."""

from __future__ import annotations

import json

import pytest

from chat_to_howto.domain.generation import OutboundMessage
from chat_to_howto.providers.base import ProviderResponse
from chat_to_howto.services.generation_extraction import parse_structured_extraction
from chat_to_howto.services.generation_merge import MergeRejected, merge_extractions
from chat_to_howto.services.generation_planning import GenerationChunk


def _chunk(index: int, message_id: str) -> GenerationChunk:
    return GenerationChunk(
        chunk_id="chunk:v1:" + f"{index + 1:064x}",
        index=index,
        messages=(OutboundMessage(message_id=message_id, text=f"synthetic-{message_id}"),),
        estimated_input_tokens=20,
        payload=b'{"synthetic":true}',
    )


def _result(
    message_id: str,
    *,
    title: str = "Synthetic guide",
    instruction: str = "Perform shared action",
    missing: tuple[str, ...] = (),
) -> dict[str, object]:
    return {
        "status": "complete",
        "title": title,
        "preparations": [
            {"text": "Prepare shared input", "source_message_ids": [message_id]}
        ],
        "steps": [
            {
                "instruction": instruction,
                "location": None,
                "parameters": ["A"],
                "conditions": [],
                "warnings": [],
                "expected_result": "Done",
                "source_message_ids": [message_id],
            }
        ],
        "checks": [{"text": "Verify shared output", "source_message_ids": [message_id]}],
        "missing_information": list(missing),
        "conflicts": [],
    }


def _extraction(index: int, message_id: str, payload: dict[str, object]):
    return parse_structured_extraction(
        _chunk(index, message_id),
        ProviderResponse(
            provider="fake",
            model="deterministic",
            content=json.dumps(
                {
                    "result": payload,
                    "evidence": [
                        {"message_id": message_id, "quote": f"synthetic-{message_id}"}
                    ],
                }
            ),
            finish_reason="stop",
        ),
    )


def test_merge_orders_chunks_and_unions_evidence_for_duplicate_items() -> None:
    later = _extraction(1, "m2", _result("m2", missing=("Need version",)))
    earlier = _extraction(0, "m1", _result("m1", missing=("Need version", "Need path")))

    merged = merge_extractions((later, earlier))

    assert merged.source_extraction_ids == (earlier.extraction_id, later.extraction_id)
    assert merged.result.title == "Synthetic guide"
    assert len(merged.result.preparations) == 1
    assert merged.result.preparations[0].source_message_ids == ("m1", "m2")
    assert len(merged.result.steps) == 1
    assert merged.result.steps[0].source_message_ids == ("m1", "m2")
    assert merged.result.checks[0].source_message_ids == ("m1", "m2")
    assert merged.result.missing_information == ("Need version", "Need path")


def test_merge_keeps_distinct_actions_in_chronological_chunk_order() -> None:
    first = _extraction(0, "m1", _result("m1", instruction="First action"))
    second = _extraction(1, "m2", _result("m2", instruction="Second action"))

    merged = merge_extractions((second, first))
    assert [step.instruction for step in merged.result.steps] == ["First action", "Second action"]


def test_all_insufficient_extractions_merge_to_fixed_insufficient_result() -> None:
    payload = {
        "status": "insufficient_evidence",
        "title": "选定范围内未找到足够依据",
        "preparations": [],
        "steps": [],
        "checks": [],
        "missing_information": [],
        "conflicts": [],
    }
    merged = merge_extractions((_extraction(0, "m1", payload),))
    assert merged.result.status == "insufficient_evidence"


def test_merge_rejects_duplicate_chunk_index_or_extraction_identity() -> None:
    first = _extraction(0, "m1", _result("m1"))
    other_same_index = _extraction(0, "m2", _result("m2"))
    with pytest.raises(MergeRejected, match="duplicate chunk"):
        merge_extractions((first, other_same_index))
    with pytest.raises(MergeRejected, match="duplicate extraction"):
        merge_extractions((first, first))
