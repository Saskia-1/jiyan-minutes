"""Bounded local instruction interpretation, without sources or AI."""

import pytest


@pytest.mark.parametrize(("instruction", "keywords", "direction"), [
    ("整理这段聊天里有关创业的想法，列出各方观点和分歧", ["创业"], "mixed"),
    ("归纳关于职业发展的观点", ["职业发展"], "viewpoints"),
    ("提取围绕产品设计的灵感", ["产品设计"], "ideas"),
    ("整理对远程工作的看法", ["远程工作"], "viewpoints"),
    ("总结关于烘焙的操作步骤", ["烘焙"], "how_to"),
    ("整理“创业”和“职业规划”的想法", ["创业", "职业规划"], "ideas"),
    ("关键词：创业、融资、创业；整理方法", ["创业", "融资"], "how_to"),
    ("整理关于创业和融资的观点", ["创业", "融资"], "viewpoints"),
    ("整理关于创业的综合内容", ["创业"], "mixed"),
])
def test_extracts_explicit_topics_and_directions_without_losing_instruction(instruction, keywords, direction):
    from chat_to_howto.services.agent_planning import plan_instruction

    plan = plan_instruction(instruction)
    assert plan.instruction == instruction
    assert plan.goal == instruction
    assert plan.keywords == keywords
    assert plan.summary_type == direction
    assert not plan.all_content
    assert not plan.needs_clarification
    assert plan.notices  # Always disclose editable, limited local interpretation.


@pytest.mark.parametrize(("instruction", "keywords", "direction"), [
    ("不要整理方法，只列关于创业的观点", ["创业"], "viewpoints"),
    ("不要关于工作的方法，只整理关于创业的观点", ["创业"], "viewpoints"),
    ("不要‘工作’，只整理‘创业’的想法", ["创业"], "ideas"),
    ("不用观点，整理关于创业的想法", ["创业"], "ideas"),
])
def test_negated_topics_and_directions_do_not_become_positive_selections(instruction, keywords, direction):
    from chat_to_howto.services.agent_planning import plan_instruction

    plan = plan_instruction(instruction)
    assert plan.keywords == keywords
    assert plan.summary_type == direction
    assert not plan.all_content


@pytest.mark.parametrize("instruction", ["帮我整理一下", "整理重要内容", "整理关于这个话题的观点", "不要整理全部内容", "不要方法"])
def test_ambiguous_request_never_silently_selects_all(instruction):
    from chat_to_howto.services.agent_planning import plan_instruction

    plan = plan_instruction(instruction)
    assert plan.needs_clarification
    assert plan.keywords == []
    assert not plan.all_content


@pytest.mark.parametrize("instruction", ["整理全部内容的观点", "总结所有聊天内容", "总结全文"])
def test_only_explicit_whole_source_request_allows_empty_keywords(instruction):
    from chat_to_howto.services.agent_planning import plan_instruction

    plan = plan_instruction(instruction)
    assert plan.all_content
    assert plan.keywords == []
    assert not plan.needs_clarification


def test_whole_source_phrase_with_topic_stays_topic_filtered():
    from chat_to_howto.services.agent_planning import plan_instruction

    plan = plan_instruction("整理全部内容中关于创业的观点")
    assert plan.keywords == ["创业"]
    assert not plan.all_content


@pytest.mark.parametrize("instruction", ["", " \n\t", "字" * 513, None, 123])
def test_rejects_blank_nontext_and_overlong_instruction(instruction):
    from chat_to_howto.services.agent_planning import plan_instruction

    with pytest.raises(ValueError):
        plan_instruction(instruction)


@pytest.mark.parametrize(("instruction", "keywords", "direction"), [
    ("关键词：创业,融资；整理观点", ["创业", "融资"], "viewpoints"),
    ("关键词：创业，融资，整理观点", ["创业", "融资"], "viewpoints"),
    ("整理关于学习方法的观点", ["学习方法"], "viewpoints"),
    ("整理关于方法论的观点", ["方法论"], "viewpoints"),
    ("关于创业，只要想法，不要方法或观点", ["创业"], "ideas"),
    ("整理关于创业的观点，不需要任何方法", ["创业"], "viewpoints"),
])
def test_topic_terms_and_clause_punctuation_do_not_confuse_direction(instruction, keywords, direction):
    from chat_to_howto.services.agent_planning import plan_instruction

    plan = plan_instruction(instruction)
    assert plan.keywords == keywords
    assert plan.summary_type == direction


@pytest.mark.parametrize("instruction", [
    "整理所有内容中关于这个问题的观点", "整理全部资料里有关重要内容的想法",
    "总结全部内容，但排除关于工作的记录", "不是要方法，只整理关于创业的观点",
    "不想要方法，只整理关于创业的观点",
])
def test_vague_filters_and_negated_preferences_never_expand_scope_or_direction(instruction):
    from chat_to_howto.services.agent_planning import plan_instruction

    plan = plan_instruction(instruction)
    if "关于创业" in instruction:
        assert plan.summary_type == "viewpoints"
        assert plan.keywords == ["创业"]
    else:
        assert plan.needs_clarification
        assert not plan.all_content


def test_quoted_compound_is_one_explicit_keyword():
    from chat_to_howto.services.agent_planning import plan_instruction

    plan = plan_instruction("整理‘和平与发展’的观点")
    assert plan.keywords == ["和平与发展"]
    assert plan.summary_type == "viewpoints"


@pytest.mark.parametrize(("instruction", "keywords", "direction"), [
    ("整理创业的想法", ["创业"], "ideas"),
    ("帮我把聊天里的面试经验整理成可执行步骤", ["面试经验"], "how_to"),
    ("总结远程办公的利弊", ["远程办公"], "viewpoints"),
])
def test_common_explicit_subject_sentences_need_no_manual_topic_rewrite(instruction, keywords, direction):
    from chat_to_howto.services.agent_planning import plan_instruction

    plan = plan_instruction(instruction)
    assert plan.keywords == keywords
    assert plan.summary_type == direction
    assert plan.goal == instruction
    assert not plan.needs_clarification
    assert not plan.all_content


@pytest.mark.parametrize("instruction", [
    "整理这份文件的观点", "整理一下的想法", "总结重要内容的利弊",
    "帮我把聊天里的重要内容整理成可执行步骤", "整理这个材料的想法",
])
def test_common_subject_rules_reject_generic_source_and_filler_words(instruction):
    from chat_to_howto.services.agent_planning import plan_instruction

    plan = plan_instruction(instruction)
    assert plan.keywords == []
    assert plan.needs_clarification
    assert not plan.all_content


@pytest.mark.parametrize(("instruction", "keywords"), [
    ("整理关于创业的观点，不要发送涉及客户的记录", ["创业"]),
    ("整理关于工作的观点，排除包含薪资的记录", ["工作"]),
    ("总结全部内容，除了薪资以外", []),
    ("总结全部内容，除客户信息之外", []),
    ("整理关于创业的观点，不要包含个人信息", ["创业"]),
    ("整理关于工作的观点，不要外发身份证号", ["工作"]),
    ("整理关于创业的观点，不发送客户记录", ["创业"]),
    ("整理关于工作的观点，别上传个人信息", ["工作"]),
    ("整理关于创业的内容，不发送方法", ["创业"]),
    ("整理关于工作的内容，不上传观点", ["工作"]),
])
def test_source_or_outbound_exclusion_needs_explicit_scope_review(instruction, keywords):
    from chat_to_howto.services.agent_planning import plan_instruction

    plan = plan_instruction(instruction)
    assert plan.goal == instruction
    assert plan.instruction == instruction
    assert plan.keywords == keywords
    assert plan.needs_clarification
    assert not plan.all_content
    assert any("范围" in notice and "调整" in notice for notice in plan.notices)
    if keywords:
        assert not any("未识别到明确主题" in notice for notice in plan.notices)


def test_negative_writing_direction_does_not_require_scope_review():
    from chat_to_howto.services.agent_planning import plan_instruction

    plan = plan_instruction("整理关于创业的内容，不要方法，只要观点")
    assert plan.keywords == ["创业"]
    assert plan.summary_type == "viewpoints"
    assert not plan.needs_clarification
