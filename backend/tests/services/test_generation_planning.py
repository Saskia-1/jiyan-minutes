"""P2 scope selection, deterministic chunking, and hard-budget tests."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from chat_to_howto.domain.generation import GenerationBudget, OutboundMessage
from chat_to_howto.domain.messages import MessageInput, StoredMessage
from chat_to_howto.services.generation_planning import (
    GenerationBudgetExceeded,
    GenerationScopeBuilder,
    GenerationSelection,
    build_generation_plan,
    estimate_chunk_tokens,
)
from chat_to_howto.services.generation_scope import GenerationScope


UTC = timezone.utc


def _stored(
    source_message_id: str,
    *,
    conversation_id: str = "conversation-a",
    sent_at: datetime,
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
            original_timestamp=sent_at.isoformat(),
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
