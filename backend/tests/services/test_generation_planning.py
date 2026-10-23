"""P2 scope selection, deterministic chunking, and hard-budget tests."""

from __future__ import annotations

from datetime import datetime, timezone
import json

import pytest

from chat_to_howto.domain.generation import GenerationBudget, GenerationResult, OutboundMessage, TopicSummary, TopicSection
from chat_to_howto.domain.messages import MessageInput, StoredMessage
from chat_to_howto.services.generation_planning import (
    GenerationBudgetExceeded,
    GenerationScopeBuilder,
    GenerationSelection,
    build_generation_plan,
    build_synthesis_chunk,
    estimate_chunk_tokens,
)
from chat_to_howto.services.generation_scope import GenerationScope


UTC = timezone.utc


def _stored(
    source_message_id: str,
    *,
    conversation_id: str = "conversation-a",
    sent_at: datetime | None,
    text: str | None,
    source_order: int = 0,
) -> StoredMessage:
    return StoredMessage.from_input(
        MessageInput(
            source_id="synthetic",
            account_id="account-a",
            conversation_id=conversation_id,
            source_message_id=source_message_id,
            source_file_id=None,
            page_slice_id=None,
            source_order=source_order,
            sent_at_utc=sent_at,
            original_timestamp=sent_at.isoformat() if sent_at else None,
            text=text,
        )
    )


class SnapshotRepositoryStub:
    def __init__(self, messages: tuple[StoredMessage, ...]) -> None:
        self.messages = messages
        self.seen_digest: str | None = None
        self.seen_snapshot: str | None = None

    def generation_snapshot_messages(self, *, session_digest: str, snapshot_id: str):
        self.seen_digest = session_digest
        self.seen_snapshot = snapshot_id
        return "batch-a", self.messages


def _selection(**changes: object) -> GenerationSelection:
    values: dict[str, object] = {
        "snapshot_id": "snapshot-a",
        "conversation_ids": ("conversation-a",),
        "start_time": datetime(2026, 9, 1, tzinfo=UTC),
        "end_time": datetime(2026, 9, 3, tzinfo=UTC),
        "topic": "target",
        "mode": "complete",
        "provider": "deepseek",
        "model": "deepseek-v4-flash",
        "redaction_policy_revision": "outbound-v1",
        "excluded_message_ids": (),
        "budget": GenerationBudget(
            max_messages=20,
            max_input_tokens=20_000,
            max_provider_calls=5,
            max_output_tokens=2_000,
        ),
    }
    values.update(changes)
    return GenerationSelection(**values)


def _scope(messages: tuple[OutboundMessage, ...], **changes: object) -> GenerationScope:
    values: dict[str, object] = {
        "batch_id": "batch-a",
        "snapshot_id": "snapshot-a",
        "search_run_id": None,
        "mode": "complete",
        "topic": "target",
        "provider": "deepseek",
        "model": "deepseek-v4-flash",
        "redaction_policy_revision": "outbound-v1",
        "conversation_ids": ("conversation-a",),
        "range_start": datetime(2026, 9, 1, tzinfo=UTC),
        "range_end": datetime(2026, 9, 3, tzinfo=UTC),
        "messages": messages,
        "excluded_message_ids": (),
        "budget": GenerationBudget(
            max_messages=20,
            max_input_tokens=20_000,
            max_provider_calls=5,
            max_output_tokens=2_000,
        ),
    }
    values.update(changes)
    return GenerationScope(**values)


def test_summary_scope_uses_only_recalled_ids_and_keeps_source_order_after_exclusion() -> None:
    early = _stored("summary-early", sent_at=datetime(2026, 9, 1, 1, tzinfo=UTC), text="秋招开始")
    late = _stored("summary-late", sent_at=datetime(2026, 9, 1, 2, tzinfo=UTC), text="面试安排")
    irrelevant = _stored("summary-other", sent_at=datetime(2026, 9, 1, 3, tzinfo=UTC), text="无关")
    excluded = _stored("summary-excluded", sent_at=datetime(2026, 9, 1, 4, tzinfo=UTC), text="实习闲聊")
    repository = SnapshotRepositoryStub((late, irrelevant, excluded, early))

    scope = GenerationScopeBuilder(repository).build(
        _selection(
            topic="就业", generation_kind="summary",
            included_message_ids=(late.message_id, excluded.message_id, early.message_id),
            excluded_message_ids=(excluded.message_id,),
        ),
        session_id="local-session",
    )

    assert scope.generation_kind == "summary"
    assert tuple(item.message_id for item in scope.messages) == (early.message_id, late.message_id)
    assert all("就业" not in item.text for item in scope.messages)


def test_summary_rejects_recalled_ids_outside_selected_scope() -> None:
    message = _stored("summary-in-scope", sent_at=datetime(2026, 9, 1, 1, tzinfo=UTC), text="求职")
    repository = SnapshotRepositoryStub((message,))

    with pytest.raises(ValueError, match="recalled"):
        GenerationScopeBuilder(repository).build(
            _selection(generation_kind="summary", included_message_ids=("unknown-id",)),
            session_id="local-session",
        )


def test_scope_builder_intersects_owned_snapshot_conversation_time_and_exclusions() -> None:
    selected_early = _stored(
        "m-selected-early",
        sent_at=datetime(2026, 9, 1, 3, tzinfo=UTC),
        text="target early",
        source_order=2,
    )
    selected_late = _stored(
        "m-selected-late",
        sent_at=datetime(2026, 9, 2, 3, tzinfo=UTC),
        text="target late",
        source_order=1,
    )
    excluded = _stored(
        "m-excluded",
        sent_at=datetime(2026, 9, 2, 4, tzinfo=UTC),
        text="target excluded",
    )
    repository = SnapshotRepositoryStub(
        (
            selected_late,
            _stored(
                "m-other-conversation",
                conversation_id="conversation-b",
                sent_at=datetime(2026, 9, 1, 4, tzinfo=UTC),
                text="target hidden",
            ),
            _stored(
                "m-outside-time",
                sent_at=datetime(2026, 9, 3, tzinfo=UTC),
                text="target boundary",
            ),
            _stored(
                "m-no-text",
                sent_at=datetime(2026, 9, 2, 5, tzinfo=UTC),
                text=None,
            ),
            excluded,
            selected_early,
        )
    )
    selection = _selection(excluded_message_ids=(excluded.message_id,))

    scope = GenerationScopeBuilder(repository).build(selection, session_id="session-a")

    assert scope.batch_id == "batch-a"
    assert scope.snapshot_id == "snapshot-a"
    assert [item.message_id for item in scope.messages] == [
        selected_early.message_id,
        selected_late.message_id,
    ]
    assert scope.excluded_message_ids == (excluded.message_id,)
    assert repository.seen_snapshot == "snapshot-a"
    assert repository.seen_digest is not None and len(repository.seen_digest) == 64
    assert all("hidden" not in item.text and "boundary" not in item.text for item in scope.messages)


def test_fast_mode_applies_literal_topic_before_outbound_and_binds_search_identity() -> None:
    literal = _stored(
        "m-literal",
        sent_at=datetime(2026, 9, 1, 2, tzinfo=UTC),
        text="use 100%_target literally",
    )
    near_match = _stored(
        "m-near",
        sent_at=datetime(2026, 9, 1, 1, tzinfo=UTC),
        text="use 100XXtarget approximately",
    )
    repository = SnapshotRepositoryStub((near_match, literal))

    first = GenerationScopeBuilder(repository).build(
        _selection(mode="fast", topic="100%_target"),
        session_id="session-a",
    )
    second = GenerationScopeBuilder(repository).build(
        _selection(mode="fast", topic="100%_target"),
        session_id="session-a",
    )

    assert [item.message_id for item in first.messages] == [literal.message_id]
    assert first.search_run_id == second.search_run_id
    assert first.search_run_id is not None and first.search_run_id.startswith("search:v1:")


def test_scope_builder_rejects_exclusion_outside_selected_local_scope() -> None:
    repository = SnapshotRepositoryStub(
        (
            _stored(
                "m-one",
                sent_at=datetime(2026, 9, 1, 1, tzinfo=UTC),
                text="target",
            ),
        )
    )
    with pytest.raises(ValueError, match="excluded message"):
        GenerationScopeBuilder(repository).build(
            _selection(excluded_message_ids=("message:v1:" + "a" * 64,)),
            session_id="session-a",
        )


def test_generation_selection_bounds_excluded_message_count() -> None:
    with pytest.raises(ValueError):
        _selection(excluded_message_ids=tuple(f"message-{index}" for index in range(2_001)))


def test_chunking_preserves_message_boundaries_chronology_and_has_stable_ids() -> None:
    messages = tuple(
        OutboundMessage(message_id=f"m{index}", text=f"message-{index}-" + "x" * 30)
        for index in range(1, 5)
    )
    scope = _scope(messages)
    two_message_limit = estimate_chunk_tokens(scope.topic, messages[:2])

    first = build_generation_plan(scope, max_chunk_input_tokens=two_message_limit)
    second = build_generation_plan(scope, max_chunk_input_tokens=two_message_limit)

    assert [[item.message_id for item in chunk.messages] for chunk in first.chunks] == [
        ["m1", "m2"],
        ["m3", "m4"],
    ]
    assert [chunk.chunk_id for chunk in first.chunks] == [chunk.chunk_id for chunk in second.chunks]
    assert len({item.message_id for chunk in first.chunks for item in chunk.messages}) == 4
    assert all(chunk.estimated_input_tokens <= two_message_limit for chunk in first.chunks)
    assert first.total_max_output_tokens == 4_000


def test_chunking_refuses_single_message_call_count_and_total_input_overflow() -> None:
    one = OutboundMessage(message_id="m1", text="x" * 500)
    with pytest.raises(GenerationBudgetExceeded, match="single message"):
        build_generation_plan(_scope((one,)), max_chunk_input_tokens=50)

    many = tuple(
        OutboundMessage(message_id=f"m{index}", text="x" * 80)
        for index in range(4)
    )
    one_message_limit = estimate_chunk_tokens("target", many[:1])
    with pytest.raises(GenerationBudgetExceeded, match="provider call"):
        build_generation_plan(
            _scope(
                many,
                budget=GenerationBudget(
                    max_messages=20,
                    max_input_tokens=20_000,
                    max_provider_calls=2,
                    max_output_tokens=2_000,
                ),
            ),
            max_chunk_input_tokens=one_message_limit,
        )

    per_chunk = estimate_chunk_tokens("target", many[:2])
    plan = build_generation_plan(_scope(many), max_chunk_input_tokens=per_chunk)
    with pytest.raises(GenerationBudgetExceeded, match="total input"):
        build_generation_plan(
            _scope(
                many,
                budget=GenerationBudget(
                    max_messages=20,
                    max_input_tokens=plan.total_estimated_input_tokens - 1,
                    max_provider_calls=5,
                    max_output_tokens=2_000,
                ),
            ),
            max_chunk_input_tokens=per_chunk,
        )


def test_empty_scope_needs_no_provider_call() -> None:
    plan = build_generation_plan(_scope(()), max_chunk_input_tokens=500)
    assert plan.chunks == ()
    assert plan.total_estimated_input_tokens == 0
    assert plan.total_max_output_tokens == 0


def test_context_completes_short_gaps_without_expanding_unbounded_conversation():
    messages = tuple(_stored(f"gap-{i}", sent_at=None, text=f"part {i}", source_order=i) for i in range(50))
    builder = GenerationScopeBuilder(SnapshotRepositoryStub(messages))
    selection = _selection(summary_type="ideas", allow_undated=True, included_message_ids=(messages[0].message_id, messages[24].message_id))
    _, expanded, _ = builder.context_messages(selection, session_id="session-a")
    assert [item.message_id for item in expanded] == [item.message_id for item in messages[:31]]
    selection = selection.model_copy(update={"included_message_ids": (messages[0].message_id, messages[40].message_id)})
    _, expanded, _ = builder.context_messages(selection, session_id="session-a")
    assert messages[20].message_id not in {item.message_id for item in expanded}


def test_context_is_bounded_to_neighbours_conversation_and_time_then_can_be_excluded():
    messages = tuple(_stored(f"context-{i}", sent_at=datetime(2026, 9, 1, i, tzinfo=UTC),
                             text=f"context {i}", source_order=i) for i in range(8))
    other = _stored("other", conversation_id="conversation-b", sent_at=datetime(2026, 9, 1, 4, tzinfo=UTC), text="private other")
    boundary = _stored("boundary", sent_at=datetime(2026, 9, 3, tzinfo=UTC), text="outside time")
    builder = GenerationScopeBuilder(SnapshotRepositoryStub((*reversed(messages), other, boundary)))
    selection = _selection(summary_type="ideas", included_message_ids=(messages[4].message_id,))
    _, expanded, added = builder.context_messages(selection, session_id="session-a")
    assert expanded == messages
    assert added == 7
    confirmed = selection.model_copy(update={
        "included_message_ids": tuple(item.message_id for item in expanded),
        "excluded_message_ids": (messages[3].message_id,),
    })
    scope = builder.build(confirmed, session_id="session-a")
    assert [item.message_id for item in scope.messages] == [messages[i].message_id for i in (0, 1, 2, 4, 5, 6, 7)]
    assert all(item.speaker and item.conversation for item in scope.messages)
    with pytest.raises(ValueError, match="context seeds"):
        builder.context_messages(selection.model_copy(update={"included_message_ids": (other.message_id,)}), session_id="session-a")


def test_undated_messages_require_explicit_internal_opt_in_and_keep_source_order():
    first = _stored("first", sent_at=datetime(2026, 9, 1, tzinfo=UTC), text="first", source_order=1).model_copy(update={"sent_at_utc": None, "original_timestamp": None})
    second = first.model_copy(update={"message_id": "undated-second", "text": "second", "source_order": 2})
    builder = GenerationScopeBuilder(SnapshotRepositoryStub((second, first)))
    selected = _selection(summary_type="ideas", included_message_ids=(second.message_id, first.message_id))
    with pytest.raises(ValueError, match="recalled"):
        builder.build(selected, session_id="session-a")
    scope = builder.build(selected.model_copy(update={"allow_undated": True}), session_id="session-a")
    assert [item.message_id for item in scope.messages] == [first.message_id, second.message_id]
    with pytest.raises(ValueError, match="explicit message IDs"):
        _selection(allow_undated=True)


def test_new_direction_reserves_synthesis_call_and_rejects_budget_overflow():
    messages = tuple(OutboundMessage(message_id=f"m{i}", text="x" * 80) for i in range(2))
    scope = _scope(
        messages, summary_type="ideas",
        budget=GenerationBudget(max_messages=20, max_input_tokens=100_000,
                                max_provider_calls=5, max_output_tokens=4_000),
    )
    limit = max(estimate_chunk_tokens(scope.topic, (message,), summary_type="ideas") for message in messages)
    plan = build_generation_plan(scope, max_chunk_input_tokens=limit)
    assert plan.provider_calls == 3
    assert plan.synthesis_input_tokens > 0
    assert plan.total_estimated_input_tokens > sum(chunk.estimated_input_tokens for chunk in plan.chunks)
    smaller = scope.model_copy(update={"budget": scope.budget.model_copy(update={"max_provider_calls": 2})})
    with pytest.raises(GenerationBudgetExceeded, match="provider call"):
        build_generation_plan(smaller, max_chunk_input_tokens=limit)


def test_synthesis_reserves_room_for_citation_rich_drafts_without_truncating():
    messages = tuple(OutboundMessage(message_id=f"m{i}", text="x" * 80) for i in range(2))
    scope = _scope(messages, summary_type="ideas", budget=GenerationBudget(
        max_messages=20, max_input_tokens=100_000, max_provider_calls=5, max_output_tokens=4_000))
    plan = build_generation_plan(scope, max_chunk_input_tokens=estimate_chunk_tokens("target", messages[:1], summary_type="ideas"))
    first_draft = GenerationResult(status="complete", title="synthetic", preparations=(), steps=(), checks=(),
        missing_information=(), conflicts=(), summary=TopicSummary(summary_type="ideas", sections=(
            TopicSection(heading="完整构思", text="x" * 7_000, source_message_ids=("m0",)),)))
    second_draft = first_draft.model_copy(update={"summary": TopicSummary(
        summary_type="ideas", sections=(
            TopicSection(heading="补充条件", text="x" * 7_000, source_message_ids=("m1",)),
        ),
    )})
    synthesis = build_synthesis_chunk(scope, plan, (first_draft, second_draft))
    assert synthesis.estimated_input_tokens <= plan.synthesis_input_tokens
    oversized = first_draft.model_copy(update={"summary": TopicSummary(summary_type="ideas", sections=(
        TopicSection(heading="oversized", text="x" * 13_000, source_message_ids=("m0",)),))})
    with pytest.raises(GenerationBudgetExceeded, match="intermediate draft"):
        build_synthesis_chunk(scope, plan, (oversized, second_draft))


def test_topic_chunks_use_one_task_global_short_reference_map_without_exposing_real_ids():
    messages = tuple(
        OutboundMessage(message_id=f"message:v1:{index:064x}", text=f"detail-{index}-" + "x" * 60)
        for index in range(1, 4)
    )
    scope = _scope(
        messages,
        summary_type="ideas",
        budget=GenerationBudget(
            max_messages=20,
            max_input_tokens=100_000,
            max_provider_calls=5,
            max_output_tokens=4_000,
        ),
    )
    limit = max(estimate_chunk_tokens(scope.topic, (message,), summary_type="ideas") for message in messages)

    plan = build_generation_plan(scope, max_chunk_input_tokens=limit)

    payloads = [json.loads(chunk.payload) for chunk in plan.chunks]
    assert [item["messages"][0]["message_id"] for item in payloads] == ["E001", "E002", "E003"]
    assert [chunk.reference_ids for chunk in plan.chunks] == [("E001",), ("E002",), ("E003",)]
    serialized = "".join(chunk.payload.decode("utf-8") for chunk in plan.chunks)
    assert all(message.message_id not in serialized for message in messages)
    assert [[message.message_id for message in chunk.messages] for chunk in plan.chunks] == [
        [messages[0].message_id], [messages[1].message_id], [messages[2].message_id]
    ]


def test_synthesis_uses_validated_drafts_and_cited_evidence_context_not_full_chronological_messages():
    messages = tuple(
        OutboundMessage(message_id=f"message:v1:{index:064x}", text=text, speaker=f"speaker-{index}")
        for index, text in enumerate(("shared mechanism", "different condition", "uncited noise"), 1)
    )
    scope = _scope(
        messages,
        summary_type="ideas",
        budget=GenerationBudget(max_messages=20, max_input_tokens=100_000,
                                max_provider_calls=5, max_output_tokens=4_000),
    )
    limit = max(
        estimate_chunk_tokens(scope.topic, (message,), summary_type="ideas")
        for message in messages
    )
    plan = build_generation_plan(scope, max_chunk_input_tokens=limit)
    drafts = (
        GenerationResult(status="complete", title="draft-a", preparations=(), steps=(), checks=(),
            missing_information=(), conflicts=(), summary=TopicSummary(summary_type="ideas", sections=(
                TopicSection(heading="shared", text="same idea with mechanism", source_message_ids=(messages[0].message_id,)),))),
        GenerationResult(status="complete", title="draft-b", preparations=(), steps=(), checks=(),
            missing_information=(), conflicts=(), summary=TopicSummary(summary_type="ideas", sections=(
                TopicSection(heading="conditional", text="independent condition", source_message_ids=(messages[1].message_id,)),))),
        GenerationResult(status="insufficient_evidence", title="选定范围内未找到足够依据",
            preparations=(), steps=(), checks=(), missing_information=(), conflicts=()),
    )

    synthesis = build_synthesis_chunk(scope, plan, drafts)

    payload = json.loads(synthesis.payload)
    assert "messages" not in payload
    assert payload["operation"] == "synthesize"
    assert [item["reference_id"] for item in payload["evidence_context"]] == ["E001", "E002"]
    assert [item["text"] for item in payload["evidence_context"]] == ["shared mechanism", "different condition"]
    assert "drafts" not in payload
    assert [(item["unit_id"], item["source_message_ids"]) for item in payload["idea_units"]] == [
        ("D001", ["E001"]), ("D002", ["E002"]),
    ]
    assert [item["text"] for item in payload["idea_units"]] == [
        "same idea with mechanism", "independent condition",
    ]
    assert "uncited noise" not in synthesis.payload.decode("utf-8")
    assert all(message.message_id not in synthesis.payload.decode("utf-8") for message in messages)
    assert tuple(message.message_id for message in synthesis.messages) == (
        messages[0].message_id, messages[1].message_id
    )
    assert synthesis.reference_ids == ("E001", "E002")


def test_full_instruction_topic_is_redacted_before_frozen_preview_and_payload():
    from chat_to_howto.services.generation_scope import build_outbound_preview

    message = _stored("instruction-pii", sent_at=datetime(2026, 9, 1, 1, tzinfo=UTC), text="创业建议")
    scope = GenerationScopeBuilder(SnapshotRepositoryStub((message,))).build(
        _selection(topic="整理与 user@example.com 和 13800138000 有关的创业观点"), session_id="owner",
    )
    preview = build_outbound_preview(scope)
    plan = build_generation_plan(scope, max_chunk_input_tokens=8_000)
    for payload in (preview.payload, plan.chunks[0].payload):
        assert b"user@example.com" not in payload
        assert b"13800138000" not in payload
        assert "创业观点" in payload.decode()
    assert scope.topic == "整理与 [已脱敏邮箱] 和 [已脱敏手机号] 有关的创业观点"
