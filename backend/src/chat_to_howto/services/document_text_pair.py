"""Render two in-memory UTF-8 text downloads from one retained document version."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from chat_to_howto.domain.documents import DocumentVersion, HowToDocument
from chat_to_howto.domain.generation import GenerationResult, summary_groups
from chat_to_howto.services.document_export import _edited_text_blocks, _origin_label, _safe_slug
from chat_to_howto.services.documents import DocumentConflict, _referenced_message_ids
from chat_to_howto.services.evidence_validation import ValidatedEvidence


class TextDownload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    filename: str
    text: str = Field(repr=False)


class DocumentTextPair(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    document_id: str
    version_id: str
    body: TextDownload
    reference: TextDownload


def render_document_text_pair(document: HowToDocument, *, version_id: str) -> DocumentTextPair:
    """Do not persist quotes or consult mutable generation/source state."""
    version = next((item for item in document.versions if item.version_id == version_id), None)
    if version is None:
        raise DocumentConflict("document version does not exist")
    user_content = _has_user_content(version, document.versions)
    cited_version = version
    warning = ""
    if user_content:
        original = next((item for item in document.versions if item.origin == "ai_generated"), None)
        if original is None:
            raise DocumentConflict("original document version is unavailable")
        cited_version = original
        warning = (f"结构化参考对应 AI 原始版本 {original.version_id}；"
                   "未自动核验用户新增或改写的文字。")

    # Keep evidence numbering in the separate reference file, including missing records.
    numbers = {identifier: index for index, identifier in
               enumerate(_referenced_message_ids(cited_version.content), 1)}
    title = version.content.title
    body_content: list[str]
    if version.content.user_text is not None:
        title, blocks = _edited_text_blocks(version.content.user_text, fallback_title=title)
        body_content = []
        for _kind, text in blocks:
            body_content.extend((text, ""))
    else:
        body_content = _content_lines(version.content)
    metadata = [f"版本：{version.ordinal}", f"版本标识：{version.version_id}",
                f"来源：{_origin_label(version.origin)}"]
    body_lines = [title, ""]
    body_lines.extend(body_content)

    reference_lines = [f"{title} · 核查参考", "", *metadata, ""]
    if warning:
        reference_lines.extend((warning, ""))
    reference_lines.extend((
        "参考编号用于对照导入依据；专业补充由 AI 提供，非原聊天内容，不作为原文证据。",
        "删除平台原文不会删除用户已经下载的摘录。", "",
    ))
    if document.source_deleted:
        reference_lines.extend(("原始依据已清理，不可查看；本文件不含原文摘录。", ""))
    by_id = {item.message_id: item for item in document.source_evidence}
    for identifier, number in numbers.items():
        reference_lines.append(f"[证据{number}]")
        if document.source_deleted:
            reference_lines.extend(("原始依据已清理，不可查看。", ""))
        elif identifier not in by_id:
            reference_lines.extend(("依据记录缺失，无法核查。", ""))
        else:
            reference_lines.extend(_evidence_lines(by_id[identifier], document))
    if not numbers:
        reference_lines.extend(("此版本没有结构化引用依据。", ""))

    slug = _safe_slug(title)
    return DocumentTextPair(
        document_id=document.document_id, version_id=version.version_id,
        body=TextDownload(filename=f"{slug}-整理内容.txt", text=_text(body_lines)),
        reference=TextDownload(filename=f"{slug}-核查参考.txt", text=_text(reference_lines)),
    )


def _has_user_content(version: DocumentVersion, versions: tuple[DocumentVersion, ...]) -> bool:
    by_id = {item.version_id: item for item in versions}
    seen: set[str] = set()
    while version.origin == "restored":
        if version.content.user_text is not None or version.version_id in seen:
            return True
        seen.add(version.version_id)
        restored = by_id.get(version.restored_from_version_id)
        if restored is None:
            return True
        version = restored
    return version.origin == "user_edited" or version.content.user_text is not None


def _content_lines(content: GenerationResult) -> list[str]:
    """Render substantive result fields without adding verification annotations."""
    lines: list[str] = []
    if content.summary is not None:
        for label, items in summary_groups(content.summary):
            if items:
                lines.extend((label, ""))
                for item in items:
                    lines.extend((item.text, ""))
    elif content.status == "complete":
        if content.preparations:
            lines.extend(("准备", ""))
            lines.extend(item.text for item in content.preparations)
            lines.append("")
        lines.extend(("步骤", ""))
        for index, step in enumerate(content.steps, 1):
            lines.append(f"{index}. {step.instruction}")
            for label, values in (("位置", (step.location,) if step.location else ()),
                                  ("参数", step.parameters), ("条件", step.conditions),
                                  ("注意", step.warnings),
                                  ("预期结果", (step.expected_result,) if step.expected_result else ())):
                if values:
                    lines.append(f"{label}：{'、'.join(values)}")
            lines.append("")
        if content.checks:
            lines.extend(("检查", ""))
            lines.extend(item.text for item in content.checks)
            lines.append("")
        if content.missing_information:
            lines.extend(("仍缺信息", "", *content.missing_information, ""))
        if content.conflicts:
            lines.extend(("冲突", ""))
            for item in content.conflicts:
                lines.extend((item.description, f"说法 A：{item.claim_a}",
                    f"说法 B：{item.claim_b}",
                    f"处理：{item.resolution}"
                    if item.resolution else "处理：尚未解析", ""))
    if content.professional_extensions:
        lines.extend(("专业补充（AI 提供，非原聊天内容）", ""))
        for item in content.professional_extensions:
            lines.extend((item.heading, "", item.text, ""))
    return lines


def _evidence_lines(item: ValidatedEvidence, document: HowToDocument) -> list[str]:
    lines = [f"发送者：{item.sender_name or item.sender_id or '未知发送者'}",
             f"时间：{item.timestamp.isoformat() if item.timestamp else '时间未知'}"]
    if item.source_batch_id:
        lines.append(f"来源批次：{item.source_batch_id}")
    elif document.source_snapshot_id:
        lines.append(f"快照标识：{document.source_snapshot_id}")
    if item.conversation_id:
        lines.append(f"会话：{item.conversation_id}")
    label = "段落标识" if item.source_kind == "note_passage" else "消息标识"
    lines.append(f"{label}：{item.message_id}")
    if item.image_id:
        lines.append(f"截图标识：{item.image_id}")
    lines.extend(("原文摘录：", item.quote, ""))
    return lines


def _text(lines: list[str]) -> str:
    return "\n".join(lines).rstrip() + "\n"
