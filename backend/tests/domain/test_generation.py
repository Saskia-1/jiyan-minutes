"""P2 structured how-to schema tests use synthetic local message IDs only."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from chat_to_howto.domain.generation import (
    Check,
    Conflict,
    GenerationResult,
    Preparation,
    Step,
)


def _valid_result() -> dict[str, object]:
    return {
        "status": "complete",
        "title": "合成交付操作指南",
        "preparations": [
            {"text": "将画面比例设为16:9", "source_message_ids": ["m1"]}
        ],
        "steps": [
            {
                "instruction": "导出第3到10页",
                "location": None,
                "parameters": ["PNG", "第3到10页"],
                "conditions": [],
                "warnings": [],
                "expected_result": "文件位于交付目录",
                "source_message_ids": ["m2", "m3", "m4"],
            }
        ],
        "checks": [
            {"text": "确认页码止于第10页", "source_message_ids": ["m3"]}
        ],
        "missing_information": ["软件与具体菜单未提供"],
        "conflicts": [],
    }


def test_generation_result_preserves_required_arrays_nulls_and_evidence() -> None:
    result = GenerationResult.model_validate(_valid_result())
    encoded = result.model_dump(mode="json")

    assert encoded["preparations"][0]["source_message_ids"] == ["m1"]
    assert encoded["steps"][0]["location"] is None
    assert encoded["conflicts"] == []
    assert set(encoded) == {
        "status",
        "title",
        "preparations",
        "steps",
        "checks",
        "missing_information",
        "conflicts",
    }


@pytest.mark.parametrize(
    "field",
    ["title", "preparations", "steps", "checks", "missing_information", "conflicts"],
)
def test_every_top_level_schema_field_is_required(field: str) -> None:
    payload = _valid_result()
    del payload[field]
    with pytest.raises(ValidationError):
        GenerationResult.model_validate(payload)


def test_forbidden_summary_background_or_reason_sections_are_rejected() -> None:
    for field in ("summary", "background", "reasons", "conclusion"):
        payload = _valid_result()
        payload[field] = "不应存在"
        with pytest.raises(ValidationError):
            GenerationResult.model_validate(payload)


@pytest.mark.parametrize("model,values", [
    (Preparation, {"text": "准备", "source_message_ids": []}),
    (
        Step,
        {
            "instruction": "操作",
            "location": None,
            "parameters": [],
            "conditions": [],
            "warnings": [],
            "expected_result": None,
            "source_message_ids": [],
        },
    ),
    (Check, {"text": "检查", "source_message_ids": []}),
])
def test_every_factual_item_requires_real_nonempty_unique_evidence_ids(model, values) -> None:
    with pytest.raises(ValidationError, match="source_message_ids|evidence"):
        model.model_validate(values)
    values["source_message_ids"] = ["m1", "m1"]
    with pytest.raises(ValidationError, match="duplicate"):
        model.model_validate(values)
    values["source_message_ids"] = [""]
    with pytest.raises(ValidationError):
        model.model_validate(values)


def test_insufficient_evidence_result_has_exact_message_and_no_factual_items() -> None:
    result = GenerationResult.insufficient()
    assert result.status == "insufficient_evidence"
    assert result.title == "选定范围内未找到足够依据"
    assert result.preparations == ()
    assert result.steps == ()
    assert result.checks == ()
    assert result.conflicts == ()

    invalid = result.model_dump()
    invalid["steps"] = [
        {
            "instruction": "凭空操作",
            "location": None,
            "parameters": [],
            "conditions": [],
            "warnings": [],
            "expected_result": None,
            "source_message_ids": ["m1"],
        }
    ]
    with pytest.raises(ValidationError, match="insufficient"):
        GenerationResult.model_validate(invalid)


def test_complete_result_requires_at_least_one_action_step() -> None:
    payload = _valid_result()
    payload["steps"] = []
    with pytest.raises(ValidationError, match="step"):
        GenerationResult.model_validate(payload)


def test_structured_summary_can_omit_empty_sections_but_every_item_has_evidence() -> None:
    payload = {
        "status": "complete", "title": "就业聊天总结",
        "preparations": [], "steps": [], "checks": [],
        "missing_information": [], "conflicts": [],
        "summary": {
            "core_summary": [{"text": "讨论了求职安排", "source_message_ids": ["m1"]}],
            "discussions": [], "decisions": [], "action_items": [], "open_questions": [],
        },
    }

    result = GenerationResult.model_validate(payload)

    assert result.summary.core_summary[0].source_message_ids == ("m1",)
    assert result.model_dump(mode="json")["summary"] == payload["summary"]
    payload["summary"]["core_summary"][0]["source_message_ids"] = []
    with pytest.raises(ValidationError):
        GenerationResult.model_validate(payload)


def test_summary_cannot_smuggle_guide_steps_or_empty_unsupported_claims() -> None:
    payload = _valid_result()
    payload["summary"] = {
        "core_summary": [{"text": "核心", "source_message_ids": ["m1"]}],
        "discussions": [], "decisions": [], "action_items": [], "open_questions": [],
    }
    with pytest.raises(ValidationError):
        GenerationResult.model_validate(payload)


def test_unresolved_conflict_retains_both_claims_and_evidence() -> None:
    conflict = Conflict(
        description="导出范围不一致",
        claim_a="第3到8页",
        claim_a_source_message_ids=("m2",),
        claim_b="第3到10页",
        claim_b_source_message_ids=("m3",),
        resolution=None,
        resolution_source_message_ids=(),
    )
    assert conflict.resolution is None

    with pytest.raises(ValidationError, match="resolution evidence"):
        conflict.model_copy(
            update={"resolution": "采用第3到10页", "resolution_source_message_ids": ()},
        ).model_validate(
            conflict.model_copy(update={"resolution": "采用第3到10页"}).model_dump()
        )
