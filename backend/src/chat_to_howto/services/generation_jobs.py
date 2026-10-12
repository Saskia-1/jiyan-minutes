"""Durable P2 generation pipeline from exact preview consent to verified result."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
import re
from threading import RLock
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from chat_to_howto.api.session import digest
from chat_to_howto.domain.generation import GenerationResult
from chat_to_howto.domain.search import SearchResult
from chat_to_howto.providers.base import (
    CancellationToken,
    LLMProvider,
    ProviderCancelled,
    ProviderFailure,
    ProviderMalformedOutput,
    ProviderRequest,
    ProviderTimeout,
)
from chat_to_howto.repositories.chat import ChatRepository
from chat_to_howto.repositories.content import ContentRepository
from chat_to_howto.services.evidence_validation import (
    EvidenceValidationRejected,
    EvidenceValidator,
    ValidatedGeneration,
    ValidatedMixedGeneration,
    AnyValidatedGeneration,
    parse_validated_generation,
)
from chat_to_howto.services.generation_cache import (
    CacheCorrupt,
    GenerationCache,
    GenerationCacheIdentity,
    GenerationCacheSettings,
    build_cache_identity,
)
from chat_to_howto.services.generation_extraction import (
    ExtractionRejected,
    StructuredExtraction,
    parse_structured_extraction,
)
from chat_to_howto.services.generation_merge import MergeRejected, merge_extractions
from chat_to_howto.services.generation_planning import (
    GenerationBudgetExceeded,
    GenerationPlan,
    GenerationScopeBuilder,
    GenerationSelection,
    MixedGenerationSelection,
    MixedGenerationScopeBuilder,
    build_generation_plan,
    build_synthesis_chunk,
)
from chat_to_howto.services.generation_scope import AnyGenerationScope, GenerationScope, MixedGenerationScope
from chat_to_howto.services.mixed_search import MixedSearchService
from chat_to_howto.services.generation_prompts import topic_prompt


GenerationState = Literal[
    "pending",
    "checking_cache",
    "generating",
    "extracting",
    "merging",
    "validating",
    "complete",
    "failed",
    "cancelled",
]
EvidenceStatus = Literal["unverified", "verified"]
_TERMINAL = frozenset({"complete", "failed", "cancelled"})
_OPAQUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

SYSTEM_PROMPT = """Return JSON only. Build an executable how-to solely from the supplied messages.
The top-level JSON object must contain exactly `result` and `evidence`.
`result` must contain exactly: status, title, preparations, steps, checks,
missing_information, conflicts. Every factual item must cite source_message_ids.
`evidence` must contain exactly one object for every cited message id, with exactly
message_id and quote; quote must be the complete, exact supplied message text.
Never invent an id or alter a quote. If evidence is insufficient, use status
`insufficient_evidence`, title `选定范围内未找到足够依据`, empty factual arrays,
and an empty evidence array. Do not add summary, background, reasons, or markdown."""

SUMMARY_SYSTEM_PROMPT = """Return JSON only. Summarize the supplied messages, and no other source.
The top-level JSON object must contain exactly `result` and `evidence`.
`result` must contain status, title, preparations, steps, checks,
missing_information, conflicts, and summary. For a complete summary,
preparations, steps, checks, missing_information, and conflicts must be empty arrays.
`summary` must contain exactly core_summary, discussions, decisions, action_items,
open_questions. Each is an array of {text, source_message_ids}; core_summary must
have at least one item. Include decisions, action items and open questions only
when explicitly supported by the supplied messages; otherwise leave arrays empty.
Every summary item must cite actual supplied message IDs. Never infer unsupported
facts or treat topic-expansion search terms as chat facts. `evidence` must contain
exactly one {message_id, quote} for every cited ID, where quote is the complete,
exact supplied message text. Never invent IDs or alter quotes. If evidence is
insufficient, use status `insufficient_evidence`, title
`选定范围内未找到足够依据`, empty factual arrays, no summary, and empty evidence.
Do not add markdown or prose outside the JSON object."""


class GenerationAuthorizationError(PermissionError):
    pass


class GenerationJobNotFound(LookupError):
    pass


class GenerationJobConflict(RuntimeError):
    pass


class GenerationDailyUsageExceeded(RuntimeError):
    pass


class GenerationPreviewView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    preview_id: str
    scope_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider: str
    model: str
    thinking: Literal["disabled", "enabled"] = "disabled"
    reasoning_effort: Literal["low", "high", "max"] | None = None
    mode: Literal["fast", "complete"]
    message_count: int = Field(ge=0)
    character_count: int = Field(ge=0)
    estimated_input_tokens: int = Field(ge=0)
    max_output_tokens: int = Field(ge=0)
    provider_calls: int = Field(ge=0)
    synthesis_planned: bool = Field(default=False, exclude_if=lambda value: not value)
    price_status: Literal["unknown"] = "unknown"
    estimated_cost: None = None
    cache_hit: bool
    will_send_external: bool
    outbound_payloads: tuple[str, ...] = Field(repr=False)
    expires_at: datetime


class GenerationContextView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)
    messages: tuple[SearchResult, ...]
    added_count: int = Field(ge=0)


class GenerationConsentGrant(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    consent_id: str
    scope_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    outbound_authorized: bool
    expires_at: datetime


class GenerationUsage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    request_count: int = Field(ge=0)
    completed_requests: int = Field(ge=0)
    failed_requests: int = Field(ge=0)
    cancelled_requests: int = Field(ge=0)
    unknown_usage_requests: int = Field(ge=0)
    known_prompt_tokens: int = Field(ge=0)
    known_completion_tokens: int = Field(ge=0)
    models: tuple[str, ...]
    price_status: Literal["unavailable"] = "unavailable"
    price_source: None = None
    price_version: None = None
    price_observed_at: None = None
    currency: None = None
    estimated_cost: None = None
    actual_cost: None = None
    may_have_incurred_cost: bool


class GenerationJobView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    job_id: str
    state: GenerationState
    scope_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    completed_chunks: int = Field(ge=0)
    total_chunks: int = Field(ge=0)
    cancellation_requested: bool
    cache_hit: bool
    evidence_status: EvidenceStatus
    error_code: str | None
    revision: int = Field(ge=0)
    usage: GenerationUsage
    result: AnyValidatedGeneration | None = Field(default=None, repr=False)


class GenerationJobOrchestrator:
    def __init__(
        self,
        *,
        repository: ChatRepository,
        provider_factory: Callable[[str], LLMProvider],
        cache_settings: GenerationCacheSettings,
        clock: Callable[[], datetime] | None = None,
        preview_ttl: timedelta = timedelta(minutes=5),
        consent_ttl: timedelta = timedelta(minutes=5),
        max_daily_provider_requests: int = 100,
        mixed_search: MixedSearchService | None = None,
    ) -> None:
        if not timedelta(0) < preview_ttl <= timedelta(minutes=15):
            raise ValueError("preview TTL must be between zero and fifteen minutes")
        if not timedelta(0) < consent_ttl <= timedelta(minutes=15):
            raise ValueError("consent TTL must be between zero and fifteen minutes")
        if not isinstance(max_daily_provider_requests, int) or isinstance(max_daily_provider_requests, bool) or not 1 <= max_daily_provider_requests <= 10_000:
            raise ValueError("daily provider request limit is invalid")
        self._repository = repository
        self._provider_factory = provider_factory
        self._cache_settings = cache_settings
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._preview_ttl = preview_ttl
        self._consent_ttl = consent_ttl
        self._max_daily_provider_requests = max_daily_provider_requests
        self._scope_builder = GenerationScopeBuilder(repository)
        self._mixed_scope_builder = MixedGenerationScopeBuilder(mixed_search) if mixed_search is not None else None
        self._cache = GenerationCache(repository)
        self._evidence_validator = EvidenceValidator(repository, ContentRepository(repository))
        self._active_lock = RLock()
        self._active_tokens: dict[str, CancellationToken] = {}

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("generation clock must be timezone-aware")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _require_opaque(value: str, label: str) -> str:
        if not isinstance(value, str) or not _OPAQUE.fullmatch(value) or value in {".", ".."}:
            raise ValueError(f"opaque {label} is required")
        return value

    def create_preview(
        self,
        selection: GenerationSelection,
        *,
        session_id: str,
    ) -> GenerationPreviewView:
        scope = self._scope_builder.build(selection, session_id=session_id)
        return self._create_preview_from_scope(scope, session_id=session_id)

    def create_mixed_preview(
        self, selection: MixedGenerationSelection, *, session_id: str,
    ) -> GenerationPreviewView:
        if self._mixed_scope_builder is None:
            raise GenerationAuthorizationError("mixed generation is unavailable")
        scope = self._mixed_scope_builder.build(selection, session_id=session_id)
        return self._create_preview_from_scope(scope, session_id=session_id)

    def _create_preview_from_scope(
        self, scope: AnyGenerationScope, *, session_id: str,
    ) -> GenerationPreviewView:
        plan = build_generation_plan(
            scope,
            max_chunk_input_tokens=self._cache_settings.max_chunk_input_tokens,
        )
        identity = build_cache_identity(scope, self._cache_settings)
        cached = self._cache.get(identity, session_id=session_id)
        now = self._now()
        expires = now + self._preview_ttl
        preview_id = f"preview:{uuid4().hex}"
        character_count = sum(len(message.text) for message in scope.messages)
        will_send = cached is None and bool(plan.chunks)
        self._repository.create_generation_preview(
            preview_id=preview_id,
            session_digest=digest(session_id),
            scope_fingerprint=scope.fingerprint(),
            payload_sha256=scope.payload_sha256(),
            scope_json=scope.model_dump_json(),
            cache_identity_json=identity.model_dump_json(),
            settings_json=self._cache_settings.model_dump_json(),
            message_count=len(scope.messages),
            character_count=character_count,
            estimated_input_tokens=plan.total_estimated_input_tokens,
            max_output_tokens=plan.total_max_output_tokens,
            provider_calls=plan.provider_calls,
            cache_hit=cached is not None,
            will_send_external=will_send,
            created_at_utc=now.isoformat(),
            expires_at_utc=expires.isoformat(),
        )
        return GenerationPreviewView(
            preview_id=preview_id,
            scope_fingerprint=scope.fingerprint(),
            provider=scope.provider,
            model=scope.model,
            thinking=scope.thinking,
            reasoning_effort=scope.reasoning_effort,
            mode=scope.mode,
            message_count=len(scope.messages),
            character_count=character_count,
            estimated_input_tokens=plan.total_estimated_input_tokens,
            max_output_tokens=plan.total_max_output_tokens,
            provider_calls=plan.provider_calls,
            synthesis_planned=plan.synthesis_input_tokens > 0,
            cache_hit=cached is not None,
            will_send_external=will_send,
            outbound_payloads=tuple(
                chunk.payload.decode("utf-8") for chunk in plan.chunks
            ) if will_send else (),
            expires_at=expires,
        )

    def preview_context(self, selection: GenerationSelection, *, session_id: str) -> GenerationContextView:
        batch_id, messages, added = self._scope_builder.context_messages(selection, session_id=session_id)
        return GenerationContextView(
            messages=tuple(SearchResult(
                message_id=message.message_id, conversation_id=message.conversation_id,
                timestamp=message.sent_at_utc, sender_id=message.sender_id,
                source_order=message.source_order,
                sender_name=message.sender_name, text=message.text,
                batch_id=batch_id, snapshot_id=selection.snapshot_id,
            ) for message in messages), added_count=added,
        )

    def issue_consent(
        self,
        preview_id: str,
        *,
        session_id: str,
        confirmed: bool,
    ) -> GenerationConsentGrant:
        if confirmed is not True:
            raise GenerationAuthorizationError("explicit generation confirmation is required")
        self._require_opaque(preview_id, "preview ID")
        now = self._now()
        expires = now + self._consent_ttl
        try:
            row = self._repository.create_generation_consent(
                consent_id=f"generation-consent:{uuid4().hex}",
                preview_id=preview_id,
                session_digest=digest(session_id),
                recorded_at_utc=now.isoformat(),
                expires_at_utc=expires.isoformat(),
            )
        except PermissionError as error:
            raise GenerationAuthorizationError(str(error)) from None
        return GenerationConsentGrant(
            consent_id=str(row["consent_id"]),
            scope_fingerprint=str(row["scope_fingerprint"]),
            outbound_authorized=bool(row["outbound_authorized"]),
            expires_at=expires,
        )

    def start_job(
        self,
        consent_id: str,
        *,
        session_id: str,
        idempotency_key: str,
    ) -> GenerationJobView:
        self._require_opaque(consent_id, "consent ID")
        self._require_opaque(idempotency_key, "idempotency key")
        try:
            row, _created = self._repository.start_generation_job(
                job_id=f"generation-job:{uuid4().hex}",
                consent_id=consent_id,
                session_digest=digest(session_id),
                idempotency_key=idempotency_key,
                now_utc=self._now().isoformat(),
            )
        except PermissionError as error:
            raise GenerationAuthorizationError(str(error)) from None
        return self._view(row)

    def get_job(self, job_id: str, *, session_id: str) -> GenerationJobView:
        return self._view(self._job_for_session(job_id, session_id=session_id))

    def _job_for_session(self, job_id: str, *, session_id: str) -> dict[str, object]:
        self._require_opaque(job_id, "generation job ID")
        try:
            row = self._repository.get_generation_job(job_id)
        except ValueError:
            raise GenerationJobNotFound("generation job was not found") from None
        if row["session_digest"] != digest(session_id):
            raise GenerationAuthorizationError("generation job belongs to another session")
        return row

    @staticmethod
    def _scope_from_row(row: dict[str, object]) -> AnyGenerationScope:
        try:
            raw = str(row["scope_json"])
            if json.loads(raw).get("schema_version") == 2:
                return MixedGenerationScope.model_validate_json(raw)
            return GenerationScope.model_validate_json(raw)
        except (KeyError, TypeError, ValueError):
            raise GenerationJobConflict("generation job scope is invalid") from None

    @staticmethod
    def _settings_from_row(row: dict[str, object]) -> GenerationCacheSettings:
        try:
            return GenerationCacheSettings.model_validate_json(str(row["settings_json"]))
        except (KeyError, TypeError, ValueError):
            raise GenerationJobConflict("generation job settings are invalid") from None

    @staticmethod
    def _identity_from_row(row: dict[str, object]) -> GenerationCacheIdentity:
        try:
            return GenerationCacheIdentity.model_validate_json(
                str(row["cache_identity_json"])
            )
        except (KeyError, TypeError, ValueError):
            raise GenerationJobConflict("generation cache identity is invalid") from None

    def _view(self, row: dict[str, object]) -> GenerationJobView:
        result_json = row.get("result_json")
        try:
            result = (
                parse_validated_generation(str(result_json))
                if result_json is not None
                else None
            )
            return GenerationJobView(
                job_id=str(row["job_id"]),
                state=str(row["state"]),
                scope_fingerprint=str(row["scope_fingerprint"]),
                completed_chunks=int(row["completed_chunks"]),
                total_chunks=int(row["total_chunks"]),
                cancellation_requested=bool(row["cancellation_requested"]),
                cache_hit=bool(row["cache_hit"]),
                evidence_status=str(row["evidence_status"]),
                error_code=(
                    str(row["error_code"]) if row.get("error_code") is not None else None
                ),
                revision=int(row["revision"]),
                usage=self._usage(str(row["job_id"])),
                result=result,
            )
        except (KeyError, TypeError, ValueError):
            raise GenerationJobConflict("stored generation job is invalid") from None

    def _usage(self, job_id: str) -> GenerationUsage:
        records = self._repository.generation_request_records(job_id)
        statuses = [str(record["status"]) for record in records]
        return GenerationUsage(
            request_count=len(records),
            completed_requests=statuses.count("completed"),
            failed_requests=statuses.count("failed") + statuses.count("unknown"),
            cancelled_requests=statuses.count("cancelled"),
            unknown_usage_requests=sum(
                record["prompt_tokens"] is None or record["completion_tokens"] is None
                for record in records
            ),
            known_prompt_tokens=sum(int(record["prompt_tokens"] or 0) for record in records),
            known_completion_tokens=sum(int(record["completion_tokens"] or 0) for record in records),
            models=tuple(dict.fromkeys(str(record["model"]) for record in records)),
            may_have_incurred_cost=bool(records),
        )

    def run_job(self, job_id: str) -> GenerationJobView:
        self._require_opaque(job_id, "generation job ID")
        try:
            row = self._repository.get_generation_job(job_id)
        except ValueError:
            raise GenerationJobNotFound("generation job was not found") from None
        if row["state"] != "pending":
            return self._view(row)
        if not self._transition(row, "checking_cache"):
            return self._current(job_id)
        row = self._repository.get_generation_job(job_id)
        session_id_digest = str(row["session_digest"])
        try:
            scope = self._scope_from_row(row)
            settings = self._settings_from_row(row)
            identity = self._identity_from_row(row)
            if settings != self._cache_settings:
                raise GenerationJobConflict("generation contract changed")
            plan = build_generation_plan(
                scope, max_chunk_input_tokens=settings.max_chunk_input_tokens
            )
            if plan.provider_calls != int(row["total_chunks"]):
                raise GenerationJobConflict("generation plan changed")
            cached = self._cache_get_digest(identity, session_id_digest)
            if cached is not None:
                return self._complete(
                    self._repository.get_generation_job(job_id),
                    cached,
                    cache_hit=True,
                )
            if plan.chunks and not bool(row["outbound_authorized"]):
                return self._fail_current(job_id, "outbound_not_authorized")
            cancelled = self._cancel_boundary(self._repository.get_generation_job(job_id))
            if cancelled is not None:
                return cancelled
            if not plan.chunks:
                verified = (
                    ValidatedMixedGeneration(
                        source_refs=scope.source_refs, snapshot_id=scope.snapshot_id,
                        scope_fingerprint=scope.fingerprint(), source_extraction_ids=(),
                        result=GenerationResult.insufficient(), evidence=(),
                    ) if isinstance(scope, MixedGenerationScope) else
                    ValidatedGeneration(
                        batch_id=scope.batch_id, snapshot_id=scope.snapshot_id,
                        scope_fingerprint=scope.fingerprint(), source_extraction_ids=(),
                        result=GenerationResult.insufficient(), evidence=(),
                    )
                )
                self._cache_put_digest(identity, verified, session_id_digest)
                return self._complete(
                    self._repository.get_generation_job(job_id), verified, cache_hit=False
                )

            provider = self._provider_factory(scope.provider)
            if not isinstance(provider, LLMProvider) or provider.name != scope.provider:
                return self._fail_current(job_id, "provider_unavailable")
            row = self._repository.get_generation_job(job_id)
            if not self._transition(row, "generating", total_chunks=plan.provider_calls):
                return self._current(job_id)
            extractions: list[StructuredExtraction] = []
            for index in range(plan.provider_calls):
                cancelled = self._cancel_boundary(self._repository.get_generation_job(job_id))
                if cancelled is not None:
                    return cancelled
                is_synthesis = index == len(plan.chunks)
                chunk = (build_synthesis_chunk(scope, plan, tuple(item.result for item in extractions))
                         if is_synthesis else plan.chunks[index])
                token = CancellationToken()
                request_record_id = f"generation-request:{uuid4().hex}"
                request_time = self._now()
                day_start = request_time.replace(hour=0, minute=0, second=0, microsecond=0)
                try:
                    self._repository.create_generation_request(
                        request_record_id=request_record_id,
                        job_id=job_id,
                        provider=scope.provider,
                        model=scope.model,
                        created_at_utc=request_time.isoformat(),
                        day_start_utc=day_start.isoformat(),
                        day_end_utc=(day_start + timedelta(days=1)).isoformat(),
                        daily_request_limit=self._max_daily_provider_requests,
                    )
                except PermissionError:
                    raise GenerationDailyUsageExceeded from None
                with self._active_lock:
                    self._active_tokens[job_id] = token
                try:
                    try:
                        response = asyncio.run(
                            provider.generate(
                                ProviderRequest(
                                    model=scope.model,
                                    system_prompt=(
                                        topic_prompt(scope.summary_type, synthesis=is_synthesis,
                                                     draft_limit=plan.max_draft_chars if not is_synthesis else 0)
                                        if scope.summary_type is not None else
                                        SUMMARY_SYSTEM_PROMPT if scope.generation_kind == "summary" else SYSTEM_PROMPT
                                    ),
                                    payload=chunk.payload,
                                    max_output_tokens=scope.budget.max_output_tokens,
                                    thinking=scope.thinking,
                                    reasoning_effort=scope.reasoning_effort,
                                ),
                                token,
                            )
                        )
                    except ProviderCancelled:
                        self._finish_request(request_record_id, status="cancelled")
                        raise
                    except (ProviderTimeout, ProviderMalformedOutput, ProviderFailure):
                        self._finish_request(request_record_id, status="failed")
                        raise
                    except BaseException:
                        self._finish_request(request_record_id, status="unknown")
                        raise
                    self._finish_request(
                        request_record_id,
                        status="completed",
                        provider_request_id=response.request_id,
                        prompt_tokens=response.prompt_tokens,
                        completion_tokens=response.completion_tokens,
                    )
                finally:
                    with self._active_lock:
                        self._active_tokens.pop(job_id, None)
                row = self._repository.get_generation_job(job_id)
                if row["state"] != "generating":
                    return self._view(row)
                if not self._transition(row, "extracting"):
                    return self._current(job_id)
                if response.finish_reason != "stop":
                    return self._fail_current(job_id, "extraction_incomplete")
                extraction = parse_structured_extraction(chunk, response)
                if scope.summary_type is not None:
                    chunk_scope = (scope.model_copy(update={
                        "messages": chunk.messages,
                        "included_unit_ids": tuple(message.message_id for message in chunk.messages),
                        "excluded_unit_ids": (),
                    }) if isinstance(scope, MixedGenerationScope) else
                        scope.model_copy(update={"messages": chunk.messages}))
                    self._validate_digest(
                        merge_extractions((extraction,)),
                        scope=chunk_scope,
                        session_digest=session_id_digest,
                    )
                extractions.append(extraction)
                row = self._repository.get_generation_job(job_id)
                if index + 1 < plan.provider_calls:
                    if not self._transition(
                        row, "generating", completed_chunks=index + 1
                    ):
                        return self._current(job_id)
                else:
                    if not self._transition(
                        row, "merging", completed_chunks=index + 1
                    ):
                        return self._current(job_id)
            merged = merge_extractions(tuple(extractions[-1:] if plan.synthesis_input_tokens else extractions))
            if plan.synthesis_input_tokens:
                merged = merged.model_copy(update={"source_extraction_ids": tuple(item.extraction_id for item in extractions)})
            row = self._repository.get_generation_job(job_id)
            if not self._transition(row, "validating"):
                return self._current(job_id)
            verified = self._validate_digest(
                merged,
                scope=scope,
                session_digest=session_id_digest,
            )
            self._cache_put_digest(identity, verified, session_id_digest)
            return self._complete(
                self._repository.get_generation_job(job_id), verified, cache_hit=False
            )
        except ProviderCancelled:
            return self._cancel_current(job_id)
        except ProviderTimeout:
            return self._fail_current(job_id, "provider_timeout")
        except ProviderMalformedOutput:
            return self._fail_current(job_id, "provider_malformed")
        except ProviderFailure:
            return self._fail_current(job_id, "provider_failed")
        except ExtractionRejected:
            return self._fail_current(job_id, "extraction_invalid")
        except MergeRejected:
            return self._fail_current(job_id, "merge_invalid")
        except EvidenceValidationRejected:
            return self._fail_current(job_id, "evidence_invalid")
        except CacheCorrupt:
            return self._fail_current(job_id, "cache_invalid")
        except GenerationBudgetExceeded:
            return self._fail_current(job_id, "budget_exceeded")
        except GenerationDailyUsageExceeded:
            return self._fail_current(job_id, "daily_usage_limit")
        except GenerationJobConflict:
            return self._fail_current(job_id, "job_invalid")
        except Exception:
            return self._fail_current(job_id, "generation_failed")

    def _finish_request(
        self, request_record_id: str, *, status: str,
        provider_request_id: str | None = None, prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
    ) -> None:
        if not self._repository.finish_generation_request(
            request_record_id,
            status=status,
            provider_request_id=provider_request_id,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            completed_at_utc=self._now().isoformat(),
        ):
            raise GenerationJobConflict("provider usage record changed")

    def _cache_get_digest(
        self, identity: GenerationCacheIdentity, session_digest: str
    ) -> AnyValidatedGeneration | None:
        row = self._repository.get_generation_cache(
            cache_key=identity.cache_key, session_digest=session_digest
        )
        if row is None:
            return None
        try:
            persisted = GenerationCacheIdentity.model_validate_json(str(row["identity_json"]))
            result = parse_validated_generation(str(row["result_json"]))
            if (
                persisted != identity
                or result.snapshot_id != identity.snapshot_id
                or result.scope_fingerprint != identity.scope_fingerprint
            ):
                raise ValueError
            return result
        except (KeyError, TypeError, ValueError):
            raise CacheCorrupt("Stored generation cache is invalid.") from None

    def _cache_put_digest(
        self,
        identity: GenerationCacheIdentity,
        result: AnyValidatedGeneration,
        session_digest: str,
    ) -> None:
        self._repository.put_generation_cache(
            cache_key=identity.cache_key,
            session_digest=session_digest,
            snapshot_id=identity.snapshot_id,
            scope_fingerprint=identity.scope_fingerprint,
            identity_json=identity.model_dump_json(),
            result_json=result.model_dump_json(),
        )

    def _validate_digest(self, merged, *, scope: AnyGenerationScope, session_digest: str):
        return self._evidence_validator.validate_with_digest(
            merged,
            scope=scope,
            session_digest=session_digest,
        )

    def _transition(
        self, row: dict[str, object], state: GenerationState, **changes: object
    ) -> bool:
        return self._repository.cas_generation_job(
            str(row["job_id"]),
            expected_state=str(row["state"]),
            expected_revision=int(row["revision"]),
            new_state=state,
            updated_at_utc=self._now().isoformat(),
            error_code=None,
            **changes,
        )

    def _current(self, job_id: str) -> GenerationJobView:
        return self._view(self._repository.get_generation_job(job_id))

    def _complete(
        self,
        row: dict[str, object],
        result: AnyValidatedGeneration,
        *,
        cache_hit: bool,
    ) -> GenerationJobView:
        cancelled = self._cancel_boundary(row)
        if cancelled is not None:
            return cancelled
        if self._repository.cas_generation_job(
            str(row["job_id"]),
            expected_state=str(row["state"]),
            expected_revision=int(row["revision"]),
            new_state="complete",
            updated_at_utc=self._now().isoformat(),
            completed_chunks=int(row["total_chunks"]),
            cache_hit=int(cache_hit),
            evidence_status="verified",
            result_json=result.model_dump_json(),
            error_code=None,
        ):
            return self._current(str(row["job_id"]))
        return self._current(str(row["job_id"]))

    def _cancel_boundary(self, row: dict[str, object]) -> GenerationJobView | None:
        if not bool(row.get("cancellation_requested")):
            return None
        return self._cancel_current(str(row["job_id"]))

    def _cancel_current(self, job_id: str) -> GenerationJobView:
        for _attempt in range(16):
            row = self._repository.get_generation_job(job_id)
            if row["state"] in _TERMINAL:
                return self._view(row)
            if self._repository.cas_generation_job(
                job_id,
                expected_state=str(row["state"]),
                expected_revision=int(row["revision"]),
                new_state="cancelled",
                updated_at_utc=self._now().isoformat(),
                cancellation_requested=1,
                error_code="cancelled",
            ):
                return self._current(job_id)
        raise GenerationJobConflict("generation job changed repeatedly during cancellation")

    def _fail_current(self, job_id: str, error_code: str) -> GenerationJobView:
        for _attempt in range(16):
            row = self._repository.get_generation_job(job_id)
            if row["state"] in _TERMINAL:
                return self._view(row)
            if self._repository.cas_generation_job(
                job_id,
                expected_state=str(row["state"]),
                expected_revision=int(row["revision"]),
                new_state="failed",
                updated_at_utc=self._now().isoformat(),
                error_code=error_code,
                result_json=None,
                evidence_status="unverified",
            ):
                return self._current(job_id)
        raise GenerationJobConflict("generation job changed repeatedly while failing")

    def cancel_job(self, job_id: str, *, session_id: str) -> GenerationJobView:
        row = self._job_for_session(job_id, session_id=session_id)
        if row["state"] in _TERMINAL:
            return self._view(row)
        with self._active_lock:
            token = self._active_tokens.get(job_id)
        if token is not None:
            token.cancel()
        return self._cancel_current(job_id)

    def retry_job(self, job_id: str, *, session_id: str) -> GenerationJobView:
        row = self._job_for_session(job_id, session_id=session_id)
        if row["state"] != "failed":
            raise GenerationJobConflict("only failed generation jobs may be retried")
        if not self._repository.cas_generation_job(
            job_id,
            expected_state="failed",
            expected_revision=int(row["revision"]),
            new_state="pending",
            updated_at_utc=self._now().isoformat(),
            completed_chunks=0,
            cancellation_requested=0,
            cache_hit=0,
            evidence_status="unverified",
            result_json=None,
            error_code=None,
        ):
            raise GenerationJobConflict("generation job changed before retry")
        return self._current(job_id)

    def reconcile_interrupted(self) -> tuple[str, ...]:
        reconciled: list[str] = []
        for row in self._repository.nonterminal_generation_jobs():
            job_id = str(row["job_id"])
            if self._repository.cas_generation_job(
                job_id,
                expected_state=str(row["state"]),
                expected_revision=int(row["revision"]),
                new_state="failed",
                updated_at_utc=self._now().isoformat(),
                error_code="restart_interrupted",
                result_json=None,
                evidence_status="unverified",
            ):
                reconciled.append(job_id)
        return tuple(reconciled)
