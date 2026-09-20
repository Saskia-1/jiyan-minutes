"""Verified generation cache is scope-complete, session-bound, and secret-free."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from chat_to_howto.domain.generation import GenerationBudget, GenerationResult, OutboundMessage
from chat_to_howto.services.evidence_validation import ValidatedGeneration
from chat_to_howto.services.generation_cache import (
    CacheCorrupt,
    GenerationCache,
    GenerationCacheSettings,
    build_cache_identity,
)
from chat_to_howto.services.generation_scope import GenerationScope


UTC = timezone.utc


def _scope(**changes: object) -> GenerationScope:
    values: dict[str, object] = {
        "batch_id": "batch-a",
        "snapshot_id": "snapshot-a",
        "search_run_id": None,
        "mode": "complete",
        "topic": "synthetic private topic",
        "provider": "deepseek",
        "model": "deepseek-v4-flash",
        "redaction_policy_revision": "outbound-v1",
        "conversation_ids": ("conversation-a",),
        "range_start": datetime(2026, 9, 1, tzinfo=UTC),
        "range_end": datetime(2026, 9, 2, tzinfo=UTC),
        "messages": (OutboundMessage(message_id="m1", text="synthetic private chat"),),
        "excluded_message_ids": (),
        "budget": GenerationBudget(
            max_messages=100,
            max_input_tokens=20_000,
            max_provider_calls=4,
            max_output_tokens=2_000,
        ),
    }
    values.update(changes)
    return GenerationScope(**values)


def _settings(**changes: object) -> GenerationCacheSettings:
    values: dict[str, object] = {
        "prompt_version": "howto-prompt-v1",
        "output_schema_version": "generation-envelope-v1",
        "chunk_strategy_version": "message-boundary-v1",
        "max_chunk_input_tokens": 4_000,
        "provider_behavior_revision": "deepseek-json-defaults-v1",
    }
    values.update(changes)
    return GenerationCacheSettings(**values)


def _verified(scope: GenerationScope) -> ValidatedGeneration:
    return ValidatedGeneration(
        batch_id=scope.batch_id,
        snapshot_id=scope.snapshot_id,
        scope_fingerprint=scope.fingerprint(),
        source_extraction_ids=("extraction:v1:" + "1" * 64,),
        result=GenerationResult.insufficient(),
        evidence=(),
    )


class CacheRepositoryStub:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], dict[str, object]] = {}

    def get_generation_cache(self, *, cache_key: str, session_digest: str):
        return self.rows.get((cache_key, session_digest))

    def put_generation_cache(self, **row: object) -> None:
        key = (str(row["cache_key"]), str(row["session_digest"]))
        existing = self.rows.get(key)
        if existing is not None and existing != row:
            raise ValueError("generation cache identity conflict")
        self.rows[key] = dict(row)


def test_cache_key_invalidates_every_required_identity_dimension() -> None:
    scope = _scope()
    base = build_cache_identity(scope, _settings()).cache_key
    scope_changes = (
        {"snapshot_id": "snapshot-b"},
        {"conversation_ids": ("conversation-b",)},
        {"range_end": datetime(2026, 9, 3, tzinfo=UTC)},
        {"topic": "another topic"},
        {"provider": "another-provider"},
        {"model": "another-model"},
        {"redaction_policy_revision": "outbound-v2"},
        {"messages": (OutboundMessage(message_id="m1", text="changed chat"),)},
        {
            "budget": GenerationBudget(
                max_messages=100,
                max_input_tokens=21_000,
                max_provider_calls=4,
                max_output_tokens=2_000,
            )
        },
    )
    for change in scope_changes:
        assert build_cache_identity(_scope(**change), _settings()).cache_key != base
    for field, value in (
        ("prompt_version", "howto-prompt-v2"),
        ("output_schema_version", "generation-envelope-v2"),
        ("chunk_strategy_version", "message-boundary-v2"),
        ("max_chunk_input_tokens", 4_001),
        ("provider_behavior_revision", "deepseek-json-defaults-v2"),
    ):
        assert build_cache_identity(scope, _settings(**{field: value})).cache_key != base


def test_only_exact_same_session_verified_result_is_a_cache_hit() -> None:
    repository = CacheRepositoryStub()
    cache = GenerationCache(repository)
    scope = _scope()
    identity = build_cache_identity(scope, _settings())
    verified = _verified(scope)

    assert cache.get(identity, session_id="session-a") is None
    cache.put(identity, verified, session_id="session-a")

    assert cache.get(identity, session_id="session-a") == verified
    assert cache.get(identity, session_id="session-b") is None
    assert cache.get(build_cache_identity(_scope(topic="changed"), _settings()), session_id="session-a") is None


def test_corrupt_or_identity_mismatched_cache_is_never_returned() -> None:
    repository = CacheRepositoryStub()
    cache = GenerationCache(repository)
    scope = _scope()
    identity = build_cache_identity(scope, _settings())
    cache.put(identity, _verified(scope), session_id="session-a")
    row = next(iter(repository.rows.values()))
    row["result_json"] = "{}"

    with pytest.raises(CacheCorrupt):
        cache.get(identity, session_id="session-a")


def test_cache_identity_and_repr_do_not_retain_topic_chat_or_api_key() -> None:
    identity = build_cache_identity(_scope(), _settings())
    rendered = repr(identity) + identity.model_dump_json()
    assert "synthetic private topic" not in rendered
    assert "synthetic private chat" not in rendered
    assert "DEEPSEEK_API_KEY" not in rendered
