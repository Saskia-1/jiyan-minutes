"""Durable P2 job orchestration with exact consent, recovery, and cancellation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from threading import Thread
import time

import pytest

from chat_to_howto.api.session import digest
from chat_to_howto.domain.generation import GenerationBudget
from chat_to_howto.domain.jobs import ExportConversation, ExportScope
from chat_to_howto.domain.messages import MessageInput
from chat_to_howto.providers.fake import FakeProvider
from chat_to_howto.repositories.chat import ChatRepository
from chat_to_howto.services.generation_cache import GenerationCacheSettings
from chat_to_howto.services.generation_jobs import (
    GenerationAuthorizationError,
    GenerationJobConflict,
    GenerationJobOrchestrator,
)
from chat_to_howto.services.generation_planning import GenerationSelection


UTC = timezone.utc
NOW = datetime(2026, 9, 4, tzinfo=UTC)
CHAT_TEXT = "synthetic target instruction"
OUTSIDE_CHAT_TEXT = "synthetic instruction outside generation time"


@pytest.mark.parametrize("summary_type", ["viewpoints", "ideas", "how_to", "mixed"])
def test_topic_directions_share_chunk_synthesis_evidence_and_cache(tmp_path, summary_type):
    repository, _ = _repository_with_snapshot(tmp_path)
    provider = FakeProvider()
    settings = _cache_settings().model_copy(update={"max_chunk_input_tokens": 240})
    orchestrator = GenerationJobOrchestrator(
        repository=repository, provider_factory=lambda _: provider,
        cache_settings=settings, clock=lambda: NOW,
    )
    selection = GenerationSelection.model_validate({
        **_selection().model_dump(), "summary_type": summary_type,
        "end_time": datetime(2026, 9, 3, tzinfo=UTC),
    })
    try:
        preview = orchestrator.create_preview(selection, session_id="session-a")
        assert preview.synthesis_planned is True
        assert preview.provider_calls == 3
        consent = orchestrator.issue_consent(preview.preview_id, session_id="session-a", confirmed=True)
        job = orchestrator.start_job(consent.consent_id, session_id="session-a", idempotency_key="topic-test")
        completed = orchestrator.run_job(job.job_id)
        assert completed.state == "complete", completed.error_code
        assert provider.call_count == 3
        synthesis = json.loads(provider.request_payloads[-1])
        assert synthesis["operation"] == "synthesize"
        assert len(synthesis["drafts"]) == 2
        assert len(synthesis["messages"]) == 2
        assert len(completed.result.evidence) == 2
        if summary_type == "how_to":
            assert completed.result.result.summary is None
            assert completed.result.result.steps
        else:
            assert completed.result.result.summary.summary_type == summary_type
            assert completed.result.result.summary.sections[0].source_message_ids
        cached = orchestrator.create_preview(selection, session_id="session-a")
        assert cached.cache_hit is True
        other = selection.model_copy(update={"summary_type": "ideas" if summary_type != "ideas" else "viewpoints"})
        assert not orchestrator.create_preview(other, session_id="session-a").cache_hit
    finally:
        repository.close()


@pytest.mark.parametrize("attack,error_code", [("wrong_direction", "evidence_invalid"), ("unknown_id", "extraction_invalid"), ("rewritten_quote", "extraction_invalid")])
def test_topic_extraction_rejects_invalid_direction_or_model_written_evidence(tmp_path, attack, error_code):
    class AttackingFake(FakeProvider):
        async def generate(self, request, cancellation):
            response = await super().generate(request, cancellation)
            value = json.loads(response.content)
            if attack == "wrong_direction":
                value["result"]["summary"]["summary_type"] = "viewpoints"
            elif attack == "unknown_id":
                value["result"]["summary"]["sections"][0]["source_message_ids"] = ["unknown-message"]
            else:
                value["evidence"] = [{"message_id": "m1", "quote": "rewritten by the model"}]
            return response.model_copy(update={"content": json.dumps(value)})
    repository, _ = _repository_with_snapshot(tmp_path)
    provider = AttackingFake()
    orchestrator = _orchestrator(repository, provider)
    try:
        preview = orchestrator.create_preview(_selection().model_copy(update={"summary_type": "ideas"}), session_id="session-a")
        consent = orchestrator.issue_consent(preview.preview_id, session_id="session-a", confirmed=True)
        job = orchestrator.start_job(consent.consent_id, session_id="session-a", idempotency_key="invalid-topic")
        failed = orchestrator.run_job(job.job_id)
        assert failed.state == "failed"
        assert failed.error_code == error_code
        assert failed.result is None
        assert provider.call_count == 1
    finally:
        repository.close()


def _repository_with_snapshot(tmp_path) -> tuple[ChatRepository, str]:
    repository = ChatRepository(tmp_path / "chat.sqlite3")
    export_scope = ExportScope(
        source_id="synthetic",
        account_id="account-a",
        conversations=(ExportConversation(conversation_id="conversation-a"),),
        range_start=datetime(2026, 9, 1, tzinfo=UTC),
        range_end=datetime(2026, 9, 3, tzinfo=UTC),
        display_timezone="UTC",
        output_root_id="approved-root",
        configuration_nonce="nonce-a",
    )
    repository.create_batch("batch-a", export_scope)
    stored = repository.record_message(
        MessageInput(
            source_id="synthetic",
            account_id="account-a",
            conversation_id="conversation-a",
            source_message_id="source-m1",
            source_file_id=None,
            page_slice_id=None,
            source_order=1,
            sent_at_utc=datetime(2026, 9, 1, 1, tzinfo=UTC),
            original_timestamp="2026-09-01T01:00:00+00:00",
            sender_id="sender-a",
            sender_name="Synthetic Sender",
            text=CHAT_TEXT,
        )
    )
    repository.add_message_to_batch("batch-a", stored.message_id)
    outside = repository.record_message(
        MessageInput(
            source_id="synthetic",
            account_id="account-a",
            conversation_id="conversation-a",
            source_message_id="source-m2",
            source_file_id=None,
            page_slice_id=None,
            source_order=2,
            sent_at_utc=datetime(2026, 9, 2, 12, tzinfo=UTC),
            original_timestamp="2026-09-02T12:00:00+00:00",
            sender_id="sender-a",
            sender_name="Synthetic Sender",
            text=OUTSIDE_CHAT_TEXT,
        )
    )
    repository.add_message_to_batch("batch-a", outside.message_id)
    repository.create_snapshot("snapshot-a", "batch-a")
    repository.create_export_consent(
        consent_id="consent-export",
        session_digest=digest("session-a"),
        scope=export_scope,
        recorded_at_utc=NOW.isoformat(),
        expires_at_utc=(NOW + timedelta(minutes=5)).isoformat(),
    )
    row, _ = repository.start_export_job(
        job_id="job-export",
        planned_batch_id="batch-a",
        planned_snapshot_id="snapshot-a",
        session_digest=digest("session-a"),
        scope=export_scope,
        consent_id="consent-export",
        idempotency_key="export-idempotency",
        now_utc=NOW.isoformat(),
    )
    assert repository.cas_export_job(
        "job-export",
        expected_state="pending_authorization",
        expected_revision=int(row["revision"]),
        new_state="complete",
        batch_id="batch-a",
        snapshot_id="snapshot-a",
        searchable=1,
    )
    return repository, stored.message_id


def _selection() -> GenerationSelection:
    return GenerationSelection(
        snapshot_id="snapshot-a",
        conversation_ids=("conversation-a",),
        start_time=datetime(2026, 9, 1, tzinfo=UTC),
        end_time=datetime(2026, 9, 2, tzinfo=UTC),
        topic="target",
        mode="complete",
        provider="fake",
        model="deterministic",
        redaction_policy_revision="outbound-v1",
        excluded_message_ids=(),
        budget=GenerationBudget(
            max_messages=100,
            max_input_tokens=20_000,
            max_provider_calls=4,
            max_output_tokens=2_000,
        ),
    )


def _cache_settings() -> GenerationCacheSettings:
    return GenerationCacheSettings(
        prompt_version="howto-prompt-v1",
        output_schema_version="generation-envelope-v1",
        chunk_strategy_version="message-boundary-v1",
        max_chunk_input_tokens=4_000,
        provider_behavior_revision="fake-deterministic-v1",
    )


def _orchestrator(repository: ChatRepository, provider: FakeProvider) -> GenerationJobOrchestrator:
    return GenerationJobOrchestrator(
        repository=repository,
        provider_factory=lambda name: provider if name == "fake" else (_ for _ in ()).throw(ValueError()),
        cache_settings=_cache_settings(),
        clock=lambda: NOW,
    )


def _start(orchestrator: GenerationJobOrchestrator, *, key: str = "generation-key"):
    preview = orchestrator.create_preview(_selection(), session_id="session-a")
    consent = orchestrator.issue_consent(
        preview.preview_id,
        session_id="session-a",
        confirmed=True,
    )
    job = orchestrator.start_job(
        consent.consent_id,
        session_id="session-a",
        idempotency_key=key,
    )
    return preview, consent, job


def test_full_job_generates_extracts_merges_validates_persists_and_then_hits_cache(tmp_path) -> None:
    repository, message_id = _repository_with_snapshot(tmp_path)
    provider = FakeProvider()
    orchestrator = _orchestrator(repository, provider)
    try:
        preview, _consent, job = _start(orchestrator)
        assert preview.message_count == 1
        assert preview.provider_calls == 1
        assert preview.will_send_external is True
        assert preview.cache_hit is False
        assert len(preview.outbound_payloads) == 1

        completed = orchestrator.run_job(job.job_id)

        assert completed.state == "complete"
        assert completed.evidence_status == "verified"
        assert completed.result is not None
        assert completed.result.result.steps[0].source_message_ids == (message_id,)
        assert completed.completed_chunks == completed.total_chunks == 1
        assert completed.cache_hit is False
        assert provider.call_count == 1
        assert provider.request_payloads == tuple(
            item.encode("utf-8") for item in preview.outbound_payloads
        )
        assert completed.usage.request_count == 1
        assert completed.usage.may_have_incurred_cost is True
        assert completed.usage.price_status == "unavailable"
        assert completed.usage.estimated_cost is None
        assert completed.usage.actual_cost is None

        cached_preview, _cached_consent, cached_job = _start(
            orchestrator, key="generation-key-cache"
        )
        assert cached_preview.cache_hit is True
        assert cached_preview.will_send_external is False
        assert cached_preview.outbound_payloads == ()
        cached = orchestrator.run_job(cached_job.job_id)
        assert cached.state == "complete"
        assert cached.cache_hit is True
        assert cached.result == completed.result
        assert provider.call_count == 1
        assert cached.usage.request_count == 0
        assert cached.usage.may_have_incurred_cost is False
    finally:
        repository.close()


def test_preview_and_one_time_consent_are_exact_session_bound_and_nonreplayable(tmp_path) -> None:
    repository, _ = _repository_with_snapshot(tmp_path)
    orchestrator = _orchestrator(repository, FakeProvider())
    try:
        preview = orchestrator.create_preview(_selection(), session_id="session-a")
        with pytest.raises(GenerationAuthorizationError):
            orchestrator.issue_consent(preview.preview_id, session_id="session-b", confirmed=True)
        with pytest.raises(GenerationAuthorizationError):
            orchestrator.issue_consent(preview.preview_id, session_id="session-a", confirmed=False)
        consent = orchestrator.issue_consent(
            preview.preview_id, session_id="session-a", confirmed=True
        )
        first = orchestrator.start_job(
            consent.consent_id,
            session_id="session-a",
            idempotency_key="same-key",
        )
        same = orchestrator.start_job(
            consent.consent_id,
            session_id="session-a",
            idempotency_key="same-key",
        )
        assert same.job_id == first.job_id
        with pytest.raises(GenerationAuthorizationError):
            orchestrator.start_job(
                consent.consent_id,
                session_id="session-a",
                idempotency_key="different-key",
            )
        with pytest.raises(GenerationAuthorizationError):
            orchestrator.get_job(first.job_id, session_id="session-b")
    finally:
        repository.close()


@pytest.mark.parametrize(
    ("provider", "error_code"),
    [
        (FakeProvider(mode="malformed"), "extraction_invalid"),
        (FakeProvider(mode="partial"), "extraction_incomplete"),
        (FakeProvider(mode="timeout"), "provider_timeout"),
        (FakeProvider(mode="failure"), "provider_failed"),
    ],
)
def test_provider_failures_have_stable_recoverable_job_errors(tmp_path, provider, error_code) -> None:
    repository, _ = _repository_with_snapshot(tmp_path)
    orchestrator = _orchestrator(repository, provider)
    try:
        _preview, _consent, job = _start(orchestrator)
        failed = orchestrator.run_job(job.job_id)
        assert failed.state == "failed"
        assert failed.error_code == error_code
        assert failed.result is None
        assert failed.evidence_status == "unverified"
    finally:
        repository.close()


@pytest.mark.parametrize(
    ("attack_kind", "expected_error"),
    [
        ("nonexistent", "evidence_invalid"),
        ("out_of_scope", "evidence_invalid"),
        ("modified_quote", "evidence_invalid"),
        ("malformed", "extraction_invalid"),
    ],
)
def test_fake_provider_attacks_fail_closed_and_never_persist_a_result(
    tmp_path, attack_kind: str, expected_error: str
) -> None:
    repository, allowed_message_id = _repository_with_snapshot(tmp_path)
    outside_message_id = next(
        message.message_id
        for message in repository.snapshot_messages("snapshot-a")
        if message.message_id != allowed_message_id
    )
    if attack_kind == "malformed":
        provider = FakeProvider(mode="malformed")
        orchestrator = _orchestrator(repository, provider)
        attack = None
    else:
        attacked_message_id = {
            "nonexistent": "message:v1:" + "9" * 64,
            "out_of_scope": outside_message_id,
            "modified_quote": allowed_message_id,
        }[attack_kind]
        attacked_quote = {
            "nonexistent": "invented",
            "out_of_scope": OUTSIDE_CHAT_TEXT,
            "modified_quote": CHAT_TEXT + " modified",
        }[attack_kind]
        attack = {
            "result": {
                "status": "complete",
                "title": "Attack",
                "preparations": [],
                "steps": [
                    {
                        "instruction": "Invented",
                        "location": None,
                        "parameters": [],
                        "conditions": [],
                        "warnings": [],
                        "expected_result": None,
                        "source_message_ids": [attacked_message_id],
                    }
                ],
                "checks": [],
                "missing_information": [],
                "conflicts": [],
            },
            "evidence": [
                {"message_id": attacked_message_id, "quote": attacked_quote}
            ],
        }
        provider = FakeProvider(content=json.dumps(attack))
        orchestrator = _orchestrator(repository, provider)
    try:
        _preview, _consent, job = _start(orchestrator)
        failed = orchestrator.run_job(job.job_id)
        assert failed.state == "failed"
        assert failed.error_code == expected_error
        assert failed.result is None
        assert orchestrator.get_job(job.job_id, session_id="session-a").result is None
    finally:
        repository.close()


def test_inflight_provider_cancellation_reaches_terminal_cancelled_cross_thread(tmp_path) -> None:
    repository, _ = _repository_with_snapshot(tmp_path)
    provider = FakeProvider(mode="cancellation")
    orchestrator = _orchestrator(repository, provider)
    try:
        _preview, _consent, job = _start(orchestrator)
        worker = Thread(target=orchestrator.run_job, args=(job.job_id,))
        worker.start()
        deadline = time.monotonic() + 1
        while provider.call_count == 0 and time.monotonic() < deadline:
            time.sleep(0.005)
        cancelled = orchestrator.cancel_job(job.job_id, session_id="session-a")
        worker.join(timeout=1)

        assert not worker.is_alive()
        assert cancelled.state == "cancelled"
        persisted = orchestrator.get_job(job.job_id, session_id="session-a")
        assert persisted.state == "cancelled"
        assert persisted.usage.request_count == 1
        assert persisted.usage.may_have_incurred_cost is True
    finally:
        repository.close()


def test_failed_job_can_be_explicitly_retried_with_same_frozen_scope(tmp_path) -> None:
    repository, _ = _repository_with_snapshot(tmp_path)
    selected = {"provider": FakeProvider(mode="failure")}
    orchestrator = GenerationJobOrchestrator(
        repository=repository,
        provider_factory=lambda _name: selected["provider"],
        cache_settings=_cache_settings(),
        clock=lambda: NOW,
    )
    try:
        _preview, _consent, job = _start(orchestrator)
        assert orchestrator.run_job(job.job_id).state == "failed"
        selected["provider"] = FakeProvider()
        pending = orchestrator.retry_job(job.job_id, session_id="session-a")
        assert pending.state == "pending"
        completed = orchestrator.run_job(job.job_id)
        assert completed.state == "complete"
        assert completed.usage.request_count == 2
        records = repository.generation_request_records(job.job_id)
        assert [record["status"] for record in records] == ["failed", "completed"]
        assert all("payload" not in record and "prompt" not in record for record in records)
    finally:
        repository.close()


def test_daily_request_limit_blocks_retry_before_a_second_provider_call(tmp_path) -> None:
    repository, _ = _repository_with_snapshot(tmp_path)
    provider = FakeProvider(mode="failure")
    orchestrator = GenerationJobOrchestrator(
        repository=repository,
        provider_factory=lambda _name: provider,
        cache_settings=_cache_settings(),
        clock=lambda: NOW,
        max_daily_provider_requests=1,
    )
    try:
        _preview, _consent, job = _start(orchestrator)
        first = orchestrator.run_job(job.job_id)
        assert first.state == "failed"
        assert provider.call_count == 1

        pending = orchestrator.retry_job(job.job_id, session_id="session-a")
        assert pending.state == "pending"
        blocked = orchestrator.run_job(job.job_id)

        assert blocked.state == "failed"
        assert blocked.error_code == "daily_usage_limit"
        assert provider.call_count == 1
        assert blocked.usage.request_count == 1
    finally:
        repository.close()


def test_restart_reconciliation_leaves_no_nonterminal_generation_job(tmp_path) -> None:
    repository, _ = _repository_with_snapshot(tmp_path)
    orchestrator = _orchestrator(repository, FakeProvider())
    try:
        _preview, _consent, job = _start(orchestrator)
        assert job.state == "pending"
        reconciled = orchestrator.reconcile_interrupted()
        assert job.job_id in reconciled
        recovered = orchestrator.get_job(job.job_id, session_id="session-a")
        assert recovered.state == "failed"
        assert recovered.error_code == "restart_interrupted"
    finally:
        repository.close()


def test_retry_rejects_complete_or_cancelled_jobs(tmp_path) -> None:
    repository, _ = _repository_with_snapshot(tmp_path)
    orchestrator = _orchestrator(repository, FakeProvider())
    try:
        _preview, _consent, job = _start(orchestrator)
        orchestrator.run_job(job.job_id)
        with pytest.raises(GenerationJobConflict):
            orchestrator.retry_job(job.job_id, session_id="session-a")
    finally:
        repository.close()
