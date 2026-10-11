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
    content = prompt.split("不输出任何 JSON 外文本。\n", 1)[1]
    assert set(json.loads(content)) == {"result"}
    chunk = _chunk().model_copy(update={
        "payload": json.dumps({"summary_type": direction}).encode(),
        "reference_ids": ("E001",),
    })
    parsed = parse_structured_extraction(chunk, _response(content))
    assert len(parsed.evidence_quotes) == 1
    assert parsed.evidence_quotes[0].message_id == "m1"
    assert parsed.evidence_quotes[0].quote == SENTINEL
    assert parsed.result.professional_extensions[0].heading == "概念解释"


@pytest.mark.parametrize(
    ("direction", "mode_label"),
    [
        ("viewpoints", "观点整理"),
        ("ideas", "Idea / 想法整理"),
        ("how_to", "方法论 / 怎么做"),
        ("mixed", "综合整理"),
    ],
)
def test_chunk_prompt_applies_mode_before_lossy_selection_and_keeps_legal_schema(direction, mode_label):
    prompt = topic_prompt(direction, draft_limit=2_000)

    assert f"【当前模式：{mode_label}】" in prompt
    assert "【分块草稿阶段】" in prompt
    assert "【最终表达规则】" not in prompt
    content = prompt.split("不输出任何 JSON 外文本。\n", 1)[1]
    result = json.loads(content)["result"]
    assert result["professional_extensions"] == []
    if direction == "how_to":
        assert result["steps"] and "summary" not in result
    else:
        assert result["summary"]["summary_type"] == direction
        assert result["summary"]["sections"]


def test_single_and_synthesis_prompts_both_apply_final_presentation_rules():
    single = topic_prompt("ideas")
    synthesis = topic_prompt("ideas", synthesis=True)

    assert "【单块最终阶段】" in single
    assert "【跨块最终整合阶段】" in synthesis
    assert "【最终表达规则】" in single
    assert "【最终表达规则】" in synthesis
    assert "【分块草稿阶段】" not in single
    assert "【分块草稿阶段】" not in synthesis


def test_production_prompt_is_domain_neutral_and_does_not_require_source_narration():
    prompt = topic_prompt("ideas", synthesis=True)

    for sample_specific_text in ("大部分人收藏", "作为子功能还算可以", "每次练习后反馈", "实时难度"):
        assert sample_specific_text not in prompt
    for required_narration in ("聊天中的建议材料提出", "该材料认为/声称", "讨论中猜测"):
        assert required_narration not in prompt
    assert "speaker/conversation 标签不作为默认正文主语" in prompt
    assert "每个 section 对应一个独立 Idea" in prompt
    assert "不能把候选按编号直接平铺" in prompt
    assert "顶层只有 result；" not in prompt
    assert "顶层只包含 result、idea_grouping、discarded_unit_ids；" in prompt
    example = json.loads(prompt.split("不输出任何 JSON 外文本。\n", 1)[1])
    assert set(example) == {"result", "idea_grouping", "discarded_unit_ids"}


def test_topic_prompt_preserves_actor_object_and_evaluation_target_without_sample_rules():
    prompt = topic_prompt("ideas", synthesis=True)
    assert "行为角色、动作对象和数据归属" in prompt
    assert "评价实际针对的对象" in prompt
    assert "个人体验不能扩大为普遍事实" in prompt
    assert "自然段" in prompt
    assert "好友推歌" not in prompt


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
            "text": "用收藏筛选跑步歌曲", "source_message_ids": ["E001"]}]},
    }}
    if corruption == "misplaced_extensions":
        payload["result"]["summary"]["professional_extensions"] = payload["result"].pop("professional_extensions")
    content = json.dumps(payload)
    if corruption == "missing_root_brace":
        content = content[:-1]
    chunk = _chunk().model_copy(update={
        "payload": b'{"summary_type":"ideas"}',
        "reference_ids": ("E001",),
    })
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


def test_short_reference_is_restored_to_real_message_id_before_merge_and_local_evidence():
    real_id = "message:v1:" + "a" * 64
    chunk = GenerationChunk(
        chunk_id="chunk:v1:" + "a" * 64,
        index=0,
        messages=(OutboundMessage(message_id=real_id, text=SENTINEL),),
        reference_ids=("E001",),
        estimated_input_tokens=100,
        payload=b'{"summary_type":"ideas"}',
    )
    result = {
        "status": "complete", "title": "Synthetic ideas",
        "preparations": [], "steps": [], "checks": [],
        "missing_information": [], "conflicts": [], "professional_extensions": [],
        "summary": {"summary_type": "ideas", "sections": [{
            "heading": "Independent idea", "text": "Direct synthesis",
            "source_message_ids": ["E001"],
        }]},
    }

    extraction = parse_structured_extraction(chunk, _response(json.dumps({"result": result})))

    assert extraction.result.summary.sections[0].source_message_ids == (real_id,)
    assert extraction.evidence_quotes[0].message_id == real_id
    assert extraction.evidence_quotes[0].quote == SENTINEL


@pytest.mark.parametrize("invalid_reference", ["E999", "message:v1:" + "a" * 64])
def test_unknown_or_unmapped_reference_is_rejected_without_guessing(invalid_reference: str):
    chunk = GenerationChunk(
        chunk_id="chunk:v1:" + "b" * 64,
        index=0,
        messages=(OutboundMessage(message_id="message:v1:" + "b" * 64, text=SENTINEL),),
        reference_ids=("E001",),
        estimated_input_tokens=100,
        payload=b'{"summary_type":"ideas"}',
    )
    result = {
        "status": "complete", "title": "Synthetic ideas",
        "preparations": [], "steps": [], "checks": [],
        "missing_information": [], "conflicts": [], "professional_extensions": [],
        "summary": {"summary_type": "ideas", "sections": [{
            "heading": "Independent idea", "text": "Direct synthesis",
            "source_message_ids": [invalid_reference],
        }]},
    }

    with pytest.raises(ExtractionRejected):
        parse_structured_extraction(chunk, _response(json.dumps({"result": result})))


def test_same_short_reference_is_task_local_and_cannot_reuse_another_chunks_mapping():
    first = GenerationChunk(
        chunk_id="chunk:v1:" + "c" * 64, index=0,
        messages=(OutboundMessage(message_id="message:v1:" + "1" * 64, text="first"),),
        reference_ids=("E001",), estimated_input_tokens=100,
        payload=b'{"summary_type":"ideas"}',
    )
    second = first.model_copy(update={
        "chunk_id": "chunk:v1:" + "d" * 64,
        "messages": (OutboundMessage(message_id="message:v1:" + "2" * 64, text="second"),),
    })
    result = {
        "status": "complete", "title": "Synthetic ideas",
        "preparations": [], "steps": [], "checks": [],
        "missing_information": [], "conflicts": [], "professional_extensions": [],
        "summary": {"summary_type": "ideas", "sections": [{
            "heading": "Independent idea", "text": "Direct synthesis", "source_message_ids": ["E001"],
        }]},
    }

    first_extraction = parse_structured_extraction(first, _response(json.dumps({"result": result})))
    second_extraction = parse_structured_extraction(second, _response(json.dumps({"result": result})))

    assert first_extraction.result.summary.sections[0].source_message_ids == ("message:v1:" + "1" * 64,)
    assert second_extraction.result.summary.sections[0].source_message_ids == ("message:v1:" + "2" * 64,)


def _ideas_synthesis_chunk(*, split_one_message: bool = False) -> GenerationChunk:
    first_id = "message:v1:" + "1" * 64
    second_id = first_id if split_one_message else "message:v1:" + "2" * 64
    messages = (OutboundMessage(message_id=first_id, text="A mechanism and its limit"),)
    references = ("E001",)
    if not split_one_message:
        messages += (OutboundMessage(message_id=second_id, text="A related constraint"),)
        references += ("E002",)
    return GenerationChunk(
        chunk_id="chunk:v1:" + "f" * 64, index=2,
        messages=messages, reference_ids=references, estimated_input_tokens=100,
        payload=json.dumps({"summary_type": "ideas", "operation": "synthesize", "idea_units": [
            {"unit_id": "D001", "heading": "A", "text": "An independent proposal", "source_message_ids": ["E001"]},
            {"unit_id": "D002", "heading": "B", "text": "Its limit" if not split_one_message else "A second proposal",
             "source_message_ids": ["E001" if split_one_message else "E002"]},
        ]}).encode(),
    )


def _ideas_synthesis_result(*, split: bool = False) -> dict[str, object]:
    sections = ([
        {"heading": "1. First proposal", "text": "First proposal with its mechanism and limit.",
         "source_message_ids": ["E001", "E002"]},
    ] if not split else [
        {"heading": "1. First proposal", "text": "First proposal works one way.", "source_message_ids": ["E001"]},
        {"heading": "2. Second proposal", "text": "Second proposal works another way.", "source_message_ids": ["E001"]},
    ])
    return {"status": "complete", "title": "Synthetic ideas", "preparations": [], "steps": [], "checks": [],
            "missing_information": [], "conflicts": [], "professional_extensions": [],
            "summary": {"summary_type": "ideas", "sections": sections}}


def test_ideas_synthesis_can_bind_mechanism_and_limit_from_different_messages_to_one_idea():
    chunk = _ideas_synthesis_chunk()
    content = {"result": _ideas_synthesis_result(),
               "idea_grouping": [{"unit_ids": ["D001", "D002"]}], "discarded_unit_ids": []}

    extracted = parse_structured_extraction(chunk, _response(json.dumps(content)))

    section = extracted.result.summary.sections[0]
    assert section.heading == "1. First proposal"
    assert "mechanism and limit" in section.text
    assert section.source_message_ids == tuple(message.message_id for message in chunk.messages)


def test_ideas_synthesis_can_split_two_independent_ideas_from_one_message():
    chunk = _ideas_synthesis_chunk(split_one_message=True)
    content = {"result": _ideas_synthesis_result(split=True),
               "idea_grouping": [{"unit_ids": ["D001"]}, {"unit_ids": ["D002"]}],
               "discarded_unit_ids": []}

    extracted = parse_structured_extraction(chunk, _response(json.dumps(content)))

    assert len(extracted.result.summary.sections) == 2
    assert all(section.source_message_ids == (chunk.messages[0].message_id,)
               for section in extracted.result.summary.sections)


@pytest.mark.parametrize("change", ["no_grouping", "unknown_unit", "hidden_unit", "cross_group_reference", "discarded_reference"])
def test_ideas_synthesis_rejects_missing_or_inconsistent_group_provenance(change: str):
    chunk = _ideas_synthesis_chunk()
    content = {"result": _ideas_synthesis_result(),
               "idea_grouping": [{"unit_ids": ["D001", "D002"]}], "discarded_unit_ids": []}
    if change == "no_grouping":
        del content["idea_grouping"]
    elif change == "unknown_unit":
        content["idea_grouping"] = [{"unit_ids": ["D001", "D999"]}]
    elif change == "hidden_unit":
        content["idea_grouping"] = [{"unit_ids": ["D001"]}]
    elif change == "cross_group_reference":
        content["idea_grouping"] = [{"unit_ids": ["D001"]}]
        content["discarded_unit_ids"] = ["D002"]
    else:
        content["discarded_unit_ids"] = ["D002"]

    with pytest.raises(ExtractionRejected):
        parse_structured_extraction(chunk, _response(json.dumps(content)))
