"""Scope-complete local cache for evidence-validated generation results only."""

from __future__ import annotations

from hashlib import sha256
import json
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from chat_to_howto.api.session import digest
from chat_to_howto.services.evidence_validation import AnyValidatedGeneration, parse_validated_generation
from chat_to_howto.services.generation_scope import AnyGenerationScope


class CacheCorrupt(RuntimeError):
    """Stored cache data failed its strict identity or result contract."""


class GenerationCacheSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)

    prompt_version: str = Field(min_length=1, max_length=128)
    output_schema_version: str = Field(min_length=1, max_length=128)
    chunk_strategy_version: str = Field(min_length=1, max_length=128)
    max_chunk_input_tokens: int = Field(gt=0, le=10_000_000)
    provider_behavior_revision: str = Field(min_length=1, max_length=128)

    def fingerprint(self) -> str:
        return sha256(
            json.dumps(
                self.model_dump(mode="json"),
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()


class GenerationCacheIdentity(BaseModel):
    """Hashed scope identity; it deliberately stores no topic or message content."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)

    cache_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    snapshot_id: str
    scope_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider: str
    model: str
    settings_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    prompt_version: str
    output_schema_version: str
    chunk_strategy_version: str


def build_cache_identity(
    scope: AnyGenerationScope,
    settings: GenerationCacheSettings,
) -> GenerationCacheIdentity:
    scope_fingerprint = scope.fingerprint()
    settings_fingerprint = settings.fingerprint()
    canonical = json.dumps(
        {
            "version": scope.schema_version,
            "snapshot_id": scope.snapshot_id,
            "scope_fingerprint": scope_fingerprint,
            "provider": scope.provider,
            "model": scope.model,
            "settings": settings.model_dump(mode="json"),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return GenerationCacheIdentity(
        cache_key=sha256(canonical).hexdigest(),
        snapshot_id=scope.snapshot_id,
        scope_fingerprint=scope_fingerprint,
        provider=scope.provider,
        model=scope.model,
        settings_fingerprint=settings_fingerprint,
        prompt_version=settings.prompt_version,
        output_schema_version=settings.output_schema_version,
        chunk_strategy_version=settings.chunk_strategy_version,
    )


class _CacheRepository(Protocol):
    def get_generation_cache(
        self, *, cache_key: str, session_digest: str
    ) -> dict[str, object] | None:
        ...

    def put_generation_cache(self, **row: object) -> None:
        ...


class GenerationCache:
    def __init__(self, repository: _CacheRepository) -> None:
        self._repository = repository

    def get(
        self,
        identity: GenerationCacheIdentity,
        *,
        session_id: str,
    ) -> AnyValidatedGeneration | None:
        row = self._repository.get_generation_cache(
            cache_key=identity.cache_key,
            session_digest=digest(session_id),
        )
        if row is None:
            return None
        try:
            persisted_identity = GenerationCacheIdentity.model_validate_json(
                str(row["identity_json"])
            )
            result = parse_validated_generation(str(row["result_json"]))
            if (
                persisted_identity != identity
                or result.snapshot_id != identity.snapshot_id
                or result.scope_fingerprint != identity.scope_fingerprint
            ):
                raise ValueError
            return result
        except (KeyError, TypeError, ValueError):
            raise CacheCorrupt("Stored generation cache is invalid.") from None

    def put(
        self,
        identity: GenerationCacheIdentity,
        result: AnyValidatedGeneration,
        *,
        session_id: str,
    ) -> None:
        if (
            result.evidence_status != "verified"
            or result.snapshot_id != identity.snapshot_id
            or result.scope_fingerprint != identity.scope_fingerprint
        ):
            raise ValueError("only identity-matched verified results may be cached")
        self._repository.put_generation_cache(
            cache_key=identity.cache_key,
            session_digest=digest(session_id),
            snapshot_id=identity.snapshot_id,
            scope_fingerprint=identity.scope_fingerprint,
            identity_json=identity.model_dump_json(),
            result_json=result.model_dump_json(),
        )
