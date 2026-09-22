"""Provider text must cross a strict extraction boundary before merge."""

from __future__ import annotations

import json

import pytest

from chat_to_howto.domain.generation import OutboundMessage
from chat_to_howto.providers.base import ProviderResponse
from chat_to_howto.services.generation_extraction import (
    ExtractionRejected,
    parse_structured_extraction,
)
from chat_to_howto.services.generation_planning import GenerationChunk
from chat_to_howto.services.generation_prompts import topic_prompt


SENTINEL = "synthetic-provider-private-body"


@pytest.mark.parametrize("direction", ["viewpoints", "ideas", "how_to", "mixed"])
def test_topic_schema_examples_are_valid_and_evidence_is_derived_locally(direction):
    prompt = topic_prompt(direction)
    content = prompt.split("不输出任何 JSON 外文本。\n", 1)[1].replace("INPUT_MESSAGE_ID", "m1")
    assert set(json.loads(content)) == {"result"}
    chunk = _chunk().model_copy(update={"payload": json.dumps({"summary_type": direction}).encode()})
    parsed = parse_structured_extraction(chunk, _response(content))
    assert len(parsed.evidence_quotes) == 1
    assert parsed.evidence_quotes[0].message_id == "m1"
    assert parsed.evidence_quotes[0].quote == SENTINEL
    assert parsed.result.professional_extensions[0].heading == "概念解释"


def test_model_cannot_hide_unvalidated_prose_in_user_text():
    result = _valid_result()
    result["user_text"] = "模型不能伪装为用户编辑内容"
    with pytest.raises(ExtractionRejected):
        parse_structured_extraction(_chunk(), _response(json.dumps({"result": result})))


@pytest.mark.parametrize("corruption", ["missing_root_brace", "misplaced_extensions"])
def test_topic_output_is_never_silently_repaired_or_relaxed(corruption):
    payload = {"result": {
        "status": "complete", "title": "合成音乐构思", "preparations": [], "steps": [],
        "checks": [], "missing_information": [], "conflicts": [],
        "professional_extensions": [{"heading": "概念解释", "text": "仅作为额外解释"}],
        "summary": {"summary_type": "ideas", "sections": [{"heading": "场景歌单",
            "text": "用收藏筛选跑步歌曲", "source_message_ids": ["m1"]}]},
    }}
    if corruption == "misplaced_extensions":
        payload["result"]["summary"]["professional_extensions"] = payload["result"].pop("professional_extensions")
    content = json.dumps(payload)
    if corruption == "missing_root_brace":
        content = content[:-1]
    chunk = _chunk().model_copy(update={"payload": b'{"summary_type":"ideas"}'})
    with pytest.raises(ExtractionRejected):
        parse_structured_extraction(chunk, _response(content))


def _chunk(*, index: int = 0) -> GenerationChunk:
    return GenerationChunk(
        chunk_id="chunk:v1:" + f"{index + 1:064x}",
        index=index,
        messages=(OutboundMessage(message_id=f"m{index + 1}", text=SENTINEL),),
        estimated_input_tokens=100,
        payload=b'{"synthetic":true}',
    )


def _valid_result(*, message_id: str = "m1", instruction: str = "Do the thing") -> dict[str, object]:
    return {
        "status": "complete",
        "title": "Synthetic guide",
        "preparations": [],
        "steps": [
            {
                "instruction": instruction,
                "location": None,
                "parameters": [],
                "conditions": [],
                "warnings": [],
                "expected_result": None,
                "source_message_ids": [message_id],
            }
        ],
        "checks": [],
        "missing_information": [],
        "conflicts": [],
    }


def _valid_output(*, message_id: str = "m1", quote: str = SENTINEL) -> dict[str, object]:
    return {
        "result": _valid_result(message_id=message_id),
        "evidence": [{"message_id": message_id, "quote": quote}],
    }


def _response(content: str, *, finish_reason: str = "stop") -> ProviderResponse:
    return ProviderResponse(
        provider="fake",
        model="deterministic",
        content=content,
        finish_reason=finish_reason,
    )


def test_valid_json_becomes_hashed_structured_extraction_without_raw_body() -> None:
    content = json.dumps(_valid_output())
    extraction = parse_structured_extraction(_chunk(), _response(content))

    assert extraction.chunk_index == 0
    assert extraction.chunk_id == _chunk().chunk_id
    assert extraction.result.steps[0].instruction == "Do the thing"
    assert extraction.raw_sha256 and len(extraction.raw_sha256) == 64
    assert SENTINEL not in repr(extraction)
    assert content not in repr(extraction)
    assert extraction.extraction_id.startswith("extraction:v1:")


@pytest.mark.parametrize(
    "content",
    [
        "{",
        "[]",
        "```json\n{}\n```",
        json.dumps({**_valid_output(), "summary": SENTINEL}),
        json.dumps({"result": _valid_result()}),
    ],
)
def test_malformed_or_schema_invalid_output_is_rejected_body_free(content: str) -> None:
    with pytest.raises(ExtractionRejected) as raised:
        parse_structured_extraction(_chunk(), _response(content))
    assert SENTINEL not in str(raised.value)
    assert content not in str(raised.value)


@pytest.mark.parametrize(
    "finish_reason",
    ["length", "content_filter", "tool_calls", "insufficient_system_resource"],
)
def test_non_stop_output_is_never_treated_as_complete(finish_reason: str) -> None:
    with pytest.raises(ExtractionRejected, match="incomplete"):
        parse_structured_extraction(
            _chunk(),
            _response(json.dumps(_valid_output()), finish_reason=finish_reason),
        )
