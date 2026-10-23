"""Bounded, deterministic instruction hints. Never reads sources or calls a model."""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field

from chat_to_howto.domain.generation import SummaryType


class AgentPlan(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)

    instruction: str = Field(repr=False)
    goal: str = Field(repr=False)
    keywords: list[str] = Field(repr=False)
    summary_type: SummaryType
    all_content: bool
    needs_clarification: bool
    notices: list[str]


_DIRECTIONS = {
    "viewpoints": r"观点|看法|意见|分歧|共识|立场|利弊",
    "ideas": r"想法|灵感|创意|点子|构想",
    "how_to": r"操作步骤|操作指南|方法|步骤|流程|教程|如何|怎么做",
    "mixed": r"综合|全面归纳",
}
_DIRECTION_WORDS = "|".join(_DIRECTIONS.values())
_QUOTES = re.compile(r'“([^”\n]+)”|‘([^’\n]+)’|"([^"\n]+)"|\'([^\'\n]+)\'|「([^」\n]+)」')
_NEGATIVE = re.compile(r"除(?:了)?[^，,；;。！？\n]+?(?:以外|之外)|不是要|不想要|不希望|不要|不用|不需要|无需|不必|排除|忽略|不包括|不包含|不含|不涉及|不谈|别(?:发送|外发|上传|整理|总结|列|提)|不(?:发送|外发|上传|整理|总结|列|提取)")
_WHOLE = re.compile(r"(?:全部|所有)(?:的)?(?:聊天(?:内容|记录)?|内容|资料|记录)|全文|整段聊天|整份(?:资料|文件)")
_VAGUE = {"一下", "这份文件", "这个材料", "这个话题", "那个话题", "相关", "相关内容", "重要", "重要内容", "重点", "重点内容", "聊天", "聊天内容", "这段聊天", "内容", "资料", "主题", "全部内容", "所有内容", "全部", "所有"}
_BOUNDARY = rf"(?=的(?:各方|相关|主要)?(?:{_DIRECTION_WORDS}|内容|信息)|相关(?:{_DIRECTION_WORDS}|内容|信息)|[，,；;。！？\n]|$)"
_TOPIC = re.compile(rf"(?:关于|有关|围绕)\s*(.+?){_BOUNDARY}")
_OPINION_TOPIC = re.compile(r"对\s*(.+?)\s*的(?:看法|观点|意见)")
_KEYWORD_LIST = re.compile(r"关键词\s*[:：]\s*(.+?)(?=[；;。\n]|[，,](?=(?:请|只|再)?(?:整理|总结|归纳|提取|列出))|$)")
_ACTION_TOPIC = re.compile(rf"(?:整理|总结|归纳|提取)\s*([^，,；;。！？\n]+?)的(?=(?:各方|主要)?(?:{_DIRECTION_WORDS}))")
_SOURCE_SUBJECT = re.compile(r"(?:聊天(?:记录)?|文件|资料)(?:里|中)(?:的)?([^，,；;。！？\n]+?)(?=(?:整理|总结|归纳|提取)(?:成|为))")
_SUBJECT_PATTERNS = (_KEYWORD_LIST, _TOPIC, _OPINION_TOPIC, _ACTION_TOPIC, _SOURCE_SUBJECT)



def _positive_text(instruction: str) -> tuple[str, bool, bool]:
    """Do not turn a negated clause into a positive search or writing request."""
    positive = []
    has_negative = False
    scope_exclusion = False
    for clause in re.split(r"([，,；;。！？\n]|但是|而是|但|只(?=要|需|整理|列|提取|总结))", instruction):
        negative = _NEGATIVE.search(clause)
        if negative:
            has_negative = True
            remainder = clause[negative.end():]
            writing_only = bool(re.search(_DIRECTION_WORDS, remainder)) and not re.sub(
                rf"{_DIRECTION_WORDS}|整理|总结|列出|列|提取|任何|这些|一切|相关|的|或|和|与|及|、|\s", "", remainder,
            )
            scope_exclusion |= (negative.group().startswith("除")
                or bool(re.search(r"发送|外发|上传", negative.group())) or not writing_only)
            # Keep the affirmative prefix; the negative remainder requires user review.
            clause = clause[:negative.start()]
        if clause.strip():
            positive.append(clause)
    return "".join(positive), has_negative, scope_exclusion


def _keywords(text: str) -> list[str]:
    candidates: list[tuple[int, str, bool]] = []
    for match in _QUOTES.finditer(text):
        candidates.append((match.start(), next(value for value in match.groups() if value is not None), True))
    for pattern in _SUBJECT_PATTERNS:
        for match in pattern.finditer(text):
            value = match.group(1)
            if _QUOTES.search(value) or (pattern in (_ACTION_TOPIC, _SOURCE_SUBJECT) and re.search(r"关于|有关|围绕|^对", value)):
                continue
            candidates.append((match.start(), value, False))
    keywords: list[str] = []
    for _, candidate, quoted in sorted(candidates, key=lambda item: item[0]):
        # Lists are explicit OR terms; no synonym expansion or invented subjects.
        for value in ([candidate] if quoted else re.split(r"[、,，/]|以及|和|与|及", candidate)):
            value = value.strip(" \t:：的")
            value = re.sub(r"(?:相关|这个主题|这一主题)$", "", value).strip()
            if value and len(value) <= 64 and value not in _VAGUE and not re.fullmatch(r"[这那](?:个|些|一|份|段)?(?:问题|事情|主题|话题|内容|资料|材料|文件)", value) and value not in keywords:
                keywords.append(value)
    return keywords


def plan_instruction(instruction: str) -> AgentPlan:
    if not isinstance(instruction, str) or not instruction.strip() or len(instruction) > 512:
        raise ValueError("instruction must be nonblank text of at most 512 characters")
    positive, has_negative, scope_exclusion = _positive_text(instruction)
    keywords = _keywords(positive)
    # Remove quoted subjects so a topic named “方法” cannot choose a writing direction.
    direction_text = positive
    # Subjects and explicit keyword lists may themselves contain words like 方法.
    spans = [match.span(1) for pattern in _SUBJECT_PATTERNS for match in pattern.finditer(positive)]
    for start, end in sorted(spans, reverse=True):
        direction_text = direction_text[:start] + " " * (end - start) + direction_text[end:]
    direction_text = _QUOTES.sub("", direction_text)
    directions = [name for name, pattern in _DIRECTIONS.items() if re.search(pattern, direction_text)]
    summary_type = directions[0] if len(directions) == 1 else "mixed"
    topic_filter = any(pattern.search(positive) for pattern in (_TOPIC, _OPINION_TOPIC, _KEYWORD_LIST, _QUOTES))
    all_content = bool(_WHOLE.search(positive)) and not keywords and not topic_filter and not has_negative
    needs_clarification = scope_exclusion or (not keywords and not all_content)
    notices = ["本地规则只识别常见表达，不能理解任意复杂指令；请核对并修改关键词、整理方向与目标。"]
    if not directions:
        notices.append("未明确识别整理方向，暂用综合；可在任务卡修改。")
    if scope_exclusion:
        notices.append("检测到资料或外发范围的排除条件，本地尚未执行该条件；请手动调整来源、关键词或排除记录，并核对范围后继续。")
    elif needs_clarification:
        notices.append("未识别到明确主题，请填写关键词，或明确选择整理当前所选资料的全部内容。")
    if has_negative and not scope_exclusion:
        notices.append("已跳过识别到的否定要求；请核对范围，复杂排除条件需要手动调整。")
    return AgentPlan(
        instruction=instruction, goal=instruction, keywords=keywords,
        summary_type=summary_type, all_content=all_content,
        needs_clarification=needs_clarification, notices=notices,
    )
