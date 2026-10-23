"""Synthetic text-pair fixtures verify rendering, never real model quality."""

from datetime import datetime, timezone
import re

import pytest

from chat_to_howto.domain.documents import DocumentVersion, HowToDocument
from chat_to_howto.domain.generation import (
    Check, Conflict, GenerationResult, Preparation, ProfessionalExtension, Step,
    SummaryContent, SummaryItem, TopicSection, TopicSummary,
)
from chat_to_howto.domain.mixed_sources import SourceRef
from chat_to_howto.services.documents import DocumentConflict
from chat_to_howto.services.evidence_validation import ValidatedEvidence


NOW = datetime(2026, 10, 23, tzinfo=timezone.utc)


def _document(direction="how_to"):
    content = GenerationResult(
        status="complete", title="中文 / 双文件 .. 核查",
        preparations=(Preparation(text="准备资料", source_message_ids=("chat:2",)),),
        steps=(Step(instruction="核查段落", location="本地", parameters=("参数甲",),
                    conditions=("条件乙",), warnings=("注意丙",), expected_result="结果丁",
                    source_message_ids=("note:1", "chat:2")),),
        checks=(Check(text="检查截图", source_message_ids=("ocr:1",)),),
        missing_information=("仍需确认",),
        conflicts=(Conflict(description="版本分歧", claim_a="说法甲",
                            claim_a_source_message_ids=("chat:2",), claim_b="说法乙",
                            claim_b_source_message_ids=("note:1",), resolution="暂用截图",
                            resolution_source_message_ids=("ocr:1",)),),
        professional_extensions=(ProfessionalExtension(heading="扩展建议", text="合成 AI 补充"),),
    )
    if direction != "how_to":
        content = content.model_copy(update={
            "preparations": (), "steps": (), "checks": (), "conflicts": (), "missing_information": (),
            "summary": TopicSummary(summary_type=direction, sections=(
                TopicSection(heading="原文主题", text="合成整理正文", source_message_ids=("chat:2", "note:1")),
                TopicSection(heading="截图主题", text="合成截图正文", source_message_ids=("ocr:1", "chat:2")),
            )),
        })
    version = DocumentVersion(document_id="document:pair", version_id="version:original", ordinal=1,
        origin="ai_generated", evidence_status="verified", parent_version_id=None,
        restored_from_version_id=None, source_generation_job_id="job:pair", change_summary="",
        created_at=NOW, content=content)
    # Deliberately shuffled; numbering must follow actual use, not this array.
    evidence = (
        ValidatedEvidence(message_id="unused:1", conversation_id=None, timestamp=None,
            sender_id=None, sender_name=None, quote="未使用的摘录不得输出"),
        ValidatedEvidence(message_id="ocr:1", conversation_id=None, timestamp=None,
            sender_id=None, sender_name=None, quote="截图合成摘录", source_kind="note_passage",
            source_batch_id="batch:images", image_id="image:1"),
        ValidatedEvidence(message_id="note:1", conversation_id=None, timestamp=None,
            sender_id=None, sender_name="笔记作者", quote="笔记合成摘录", source_kind="note_passage",
            source_batch_id="batch:notes"),
        ValidatedEvidence(message_id="chat:2", conversation_id="conversation:1", timestamp=NOW,
            sender_id="sender:1", sender_name="发言者甲", quote="聊天合成摘录", source_kind="chat_message",
            source_batch_id="batch:chat"),
    )
    return HowToDocument(document_id="document:pair", source_generation_job_id="job:pair",
        source_snapshot_id=None, source_refs=(SourceRef(kind="content_batch", id="batch:notes"),
            SourceRef(kind="chat_snapshot", id="snapshot:chat")), created_at=NOW, updated_at=NOW,
        current_version=version, versions=(version,), source_evidence=evidence)


def _render(document, version_id="version:original"):
    # Import inside tests lets RED fail at the missing feature, not collection.
    from chat_to_howto.services.document_text_pair import render_document_text_pair
    return render_document_text_pair(document, version_id=version_id)


@pytest.mark.parametrize("direction", ["viewpoints", "ideas", "how_to", "mixed"])
def test_pair_has_utf8_plain_body_and_one_numbering_for_all_directions(direction):
    pair = _render(_document(direction))
    body, reference = pair.body.text, pair.reference.text
    assert pair.document_id == "document:pair" and pair.version_id == "version:original"
    assert "版本：1" not in body and "version:original" not in body
    assert "来源：AI 原始版本" not in body
    assert "版本：1" in reference and "version:original" in reference
    for part, suffix in ((pair.body, "整理内容.txt"), (pair.reference, "核查参考.txt")):
        assert part.filename.endswith(suffix) and "/" not in part.filename and ".." not in part.filename
        assert part.text.encode("utf-8").decode("utf-8") == part.text
    assert re.findall(r"\[证据\d+\]", body) == []
    assert re.findall(r"\[证据\d+\]", reference) == ["[证据1]", "[证据2]", "[证据3]"]
    assert "聊天合成摘录" not in body and "笔记合成摘录" not in body
    assert "未使用的摘录" not in reference
    assert "AI 提供，非原聊天内容" in body and "合成 AI 补充" in body
    assert "非原聊天内容" in reference and "内部推理" not in reference
    assert "删除平台原文不会删除" in reference and "已经下载" in reference
    if direction == "how_to":
        for value in ("准备资料", "核查段落", "检查截图",
                      "参数甲", "条件乙", "注意丙", "结果丁", "仍需确认", "说法甲",
                      "说法乙", "暂用截图"):
            assert value in body
    else:
        assert "合成整理正文" in body
        assert "合成截图正文" in body


def test_reference_locates_mixed_chat_note_and_image_without_guessing_unknown_time():
    reference = _render(_document()).reference.text
    for value in ("发送者：发言者甲", "2026-10-23T00:00:00+00:00", "会话：conversation:1",
                  "来源批次：batch:chat", "消息标识：chat:2", "来源批次：batch:notes",
                  "段落标识：note:1", "截图标识：image:1", "来源批次：batch:images",
                  "段落标识：ocr:1", "时间未知", "未知发送者"):
        assert value in reference
    sections = reference.split("[证据")
    assert "聊天合成摘录" in sections[1]
    assert "笔记合成摘录" in sections[2]
    assert "截图合成摘录" in sections[3]


def test_missing_reference_keeps_matching_number_and_does_not_fabricate_quote():
    document = _document().model_copy(update={"source_evidence": ()})
    pair = _render(document)
    assert "[证据1]" not in pair.body.text and "[证据1]" in pair.reference.text
    assert "依据记录缺失，无法核查" in pair.reference.text
    assert "聊天合成摘录" not in pair.reference.text


def test_deleted_sources_ignore_even_stale_evidence_and_keep_body_readable():
    document = _document()
    before = _render(document)
    deleted = _render(document.model_copy(update={"source_deleted": True}))
    assert deleted.body.text == before.body.text
    assert "原始依据已清理，不可查看" in deleted.reference.text
    assert {"[证据1]", "[证据2]", "[证据3]"} <= set(re.findall(r"\[证据\d+\]", deleted.reference.text))
    for private in ("聊天合成摘录", "笔记合成摘录", "截图合成摘录", "发言者甲", "batch:chat"):
        assert private not in deleted.reference.text


def test_old_version_stays_fixed_and_unknown_version_is_rejected():
    document = _document()
    original = document.current_version
    edited = original.model_copy(update={"version_id": "version:edit", "ordinal": 2,
        "origin": "user_edited", "evidence_status": "unverified", "parent_version_id": original.version_id,
        "content": original.content.model_copy(update={"title": "新的标题", "user_text": "新的用户文字"})})
    changed = document.model_copy(update={"current_version": edited, "versions": (original, edited)})
    assert _render(changed).model_dump() == _render(document).model_dump()
    with pytest.raises(DocumentConflict):
        _render(changed, "version:missing")


@pytest.mark.parametrize("restore", [False, True])
def test_user_edits_and_restored_user_edits_never_claim_original_refs_verify_rewrites(restore):
    document = _document()
    original = document.current_version
    edited = original.model_copy(update={"version_id": "version:edit", "ordinal": 2,
        "origin": "user_edited", "evidence_status": "unverified", "parent_version_id": original.version_id,
        "content": original.content.model_copy(update={"title": "改写标题",
            "user_text": "# 改写标题\n\n## 我的章节\n\n用户自行补充，没有核验。",
            "steps": (original.content.steps[0].model_copy(update={"source_message_ids": ("note:1",)}),)})})
    versions = (original, edited)
    selected = edited
    if restore:
        selected = edited.model_copy(update={"version_id": "version:restore", "ordinal": 3,
            "origin": "restored", "parent_version_id": edited.version_id,
            "restored_from_version_id": edited.version_id})
        versions += (selected,)
    document = document.model_copy(update={"current_version": selected, "versions": versions})
    pair = _render(document, selected.version_id)
    assert "用户自行补充，没有核验。" in pair.body.text
    assert "# 改写标题" not in pair.body.text
    assert "核查段落" not in pair.body.text
    assert "用户自行补充，没有核验。 [证据" not in pair.body.text
    assert "结构化参考对应 AI 原始版本" not in pair.body.text
    assert "未自动核验用户" not in pair.body.text
    assert "AI 原始版本结构化参考索引" not in pair.body.text
    assert "[证据" not in pair.body.text
    assert "结构化参考对应 AI 原始版本 version:original" in pair.reference.text
    assert "未自动核验用户新增或改写的文字" in pair.reference.text
    assert "聊天合成摘录" in pair.reference.text


def test_plain_structured_edit_uses_warning_and_original_reference_index():
    document = _document()
    original = document.current_version
    edited = original.model_copy(update={"version_id": "version:edit", "ordinal": 2,
        "origin": "user_edited", "evidence_status": "unverified", "parent_version_id": original.version_id,
        "content": original.content.model_copy(update={
            "steps": (original.content.steps[0].model_copy(update={"instruction": "完全改写的步骤"}),)})})
    pair = _render(document.model_copy(update={"current_version": edited, "versions": (original, edited)}),
        edited.version_id)
    assert "完全改写的步骤" in pair.body.text
    assert "完全改写的步骤 [证据" not in pair.body.text
    assert "AI 原始版本结构化参考索引" not in pair.body.text
    assert "未自动核验用户" not in pair.body.text
    assert "未自动核验用户新增或改写的文字" in pair.reference.text


def test_legacy_chat_reference_uses_retained_snapshot_locator():
    document = _document().model_copy(update={"source_snapshot_id": "snapshot:legacy", "source_refs": (),
        "source_evidence": tuple(item.model_copy(update={"source_kind": None, "source_batch_id": None})
                                 for item in _document().source_evidence)})
    assert "快照标识：snapshot:legacy" in _render(document).reference.text


def test_restoring_ai_original_preserves_citations_without_treating_it_as_a_user_rewrite():
    document = _document()
    original = document.current_version
    restored = original.model_copy(update={"version_id": "version:restored-ai", "ordinal": 2,
        "origin": "restored", "evidence_status": "unverified", "parent_version_id": original.version_id,
        "restored_from_version_id": original.version_id})
    pair = _render(document.model_copy(update={"current_version": restored, "versions": (original, restored)}),
        restored.version_id)
    assert "核查段落" in pair.body.text and "[证据" not in pair.body.text
    assert "未自动核验用户" not in pair.body.text
    assert "version:restored-ai" in pair.reference.text


def test_insufficient_result_never_creates_evidence():
    document = _document()
    version = document.current_version.model_copy(update={"content": GenerationResult.insufficient()})
    pair = _render(document.model_copy(update={"current_version": version, "versions": (version,)}))
    assert "选定范围内未找到足够依据" in pair.body.text
    assert "此版本没有结构化引用依据" in pair.reference.text
    assert "[证据" not in pair.body.text and "聊天合成摘录" not in pair.reference.text


def test_legacy_summary_all_groups_keep_corresponding_citations():
    document = _document()
    summary = SummaryContent(core_summary=(SummaryItem(text="核心合成摘要", source_message_ids=("chat:2",)),),
        discussions=(), decisions=(SummaryItem(text="合成决定", source_message_ids=("note:1",)),),
        action_items=(), open_questions=(SummaryItem(text="待解决问题", source_message_ids=("ocr:1",)),))
    content = document.current_version.content.model_copy(update={"summary": summary,
        "preparations": (), "steps": (), "checks": (), "conflicts": (), "missing_information": ()})
    version = document.current_version.model_copy(update={"content": content})
    pair = _render(document.model_copy(update={"current_version": version, "versions": (version,)}))
    for value in ("核心合成摘要", "合成决定", "待解决问题"):
        assert value in pair.body.text
    assert "[证据" not in pair.body.text
    assert re.findall(r"\[证据\d+\]", pair.reference.text) == ["[证据1]", "[证据2]", "[证据3]"]


def test_result_body_keeps_substantive_evidence_words_and_does_not_mutate_document():
    document = _document()
    original = document.current_version
    content = original.content.model_copy(update={
        "steps": (original.content.steps[0].model_copy(update={
            "instruction": "收集证据材料并核查版本，不要删除证据文件",
        }),),
    })
    version = original.model_copy(update={"content": content})
    document = document.model_copy(update={"current_version": version, "versions": (version,)})
    before = document.model_dump_json()
    pair = _render(document)
    assert "收集证据材料并核查版本，不要删除证据文件" in pair.body.text
    assert "[证据" not in pair.body.text
    assert "消息标识：chat:2" in pair.reference.text
    assert document.model_dump_json() == before


def test_result_body_preserves_literal_user_text_instead_of_removing_verification_words():
    document = _document()
    original = document.current_version
    user_text = "# 我的证据笔记\n\n用户自己的证据附录说明：[待核查]是工作标签，[证据1]是原文中的编号。"
    edited = original.model_copy(update={"version_id": "version:edit", "ordinal": 2,
        "origin": "user_edited", "evidence_status": "unverified", "parent_version_id": original.version_id,
        "content": original.content.model_copy(update={"user_text": user_text})})
    pair = _render(document.model_copy(update={"current_version": edited, "versions": (original, edited)}), edited.version_id)
    assert "用户自己的证据附录说明：[待核查]是工作标签，[证据1]是原文中的编号。" in pair.body.text
    assert "AI 原始版本结构化参考索引" not in pair.body.text
    assert "未自动核验用户" not in pair.body.text
    assert "未自动核验用户新增或改写的文字" in pair.reference.text
