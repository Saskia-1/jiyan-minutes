"""Trusted construction of the two reviewed local source adapters."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import Any

from chat_to_howto.domain.consent import ConsentRecord, SecretAccessScope, SecretOperation
from chat_to_howto.domain.sources import SourceFingerprint
from chat_to_howto.security.secrets import SecretStore
from chat_to_howto.sources.base import SourceAdapterError
from chat_to_howto.sources.qce import QceAdapter
from chat_to_howto.sources.tracememo import TraceMemoAdapter
from chat_to_howto.sources.transport import LoopbackTransport


_SOURCES = {
    "qq": (40653, "qce", QceAdapter),
    "wechat": (6131, "tracememo", TraceMemoAdapter),
}


class LocalSourceAdapterFactory:
    """Build adapters from backend-owned versions, target and secret authority."""

    def __init__(
        self,
        *,
        secret_store: SecretStore,
        base_urls: Mapping[str, str],
        fingerprints: Mapping[str, SourceFingerprint],
        transport_factory: Callable[..., Any] = LoopbackTransport,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if set(base_urls) != set(_SOURCES) or set(fingerprints) != set(_SOURCES):
            raise ValueError("Both reviewed local sources require trusted configuration.")
        backend = getattr(secret_store, "backend", None)
        if not isinstance(backend, str) or not backend:
            raise ValueError("Secret store must identify its backend.")
        if not all(
            isinstance(fingerprints[source_id], SourceFingerprint)
            for source_id in _SOURCES
        ):
            raise TypeError("Source fingerprints must be trusted fingerprint values.")
        self._store = secret_store
        self._base_urls = dict(base_urls)
        self._fingerprints = dict(fingerprints)
        self._transport_factory = transport_factory
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def __call__(
        self,
        source_id: str,
        account_id: str,
        *,
        token_access: SecretAccessScope | None = None,
        token_consent: ConsentRecord | None = None,
    ):
        fingerprint = self._fingerprints.get(source_id)
        configuration = _SOURCES.get(source_id)
        if (
            fingerprint is None
            or configuration is None
            or not isinstance(account_id, str)
            or not account_id
            or not isinstance(token_access, SecretAccessScope)
            or not isinstance(token_consent, ConsentRecord)
            or token_access.operation is not SecretOperation.GET
            or token_access.source_id != source_id
            or token_access.account_id != account_id
            or token_access.secret_backend != self._store.backend
            or SourceFingerprint(
                client_version=token_access.client_version,
                tool_version=token_access.tool_version,
                account_version=token_access.account_version,
            )
            != fingerprint
            or not token_consent.matches(token_access, now=self._clock())
        ):
            raise SourceAdapterError("Source authorization is unavailable.")
        expected_port, transport_source, adapter_type = configuration
        transport = self._transport_factory(
            self._base_urls[source_id],
            expected_port=expected_port,
            source=transport_source,
        )
        return adapter_type(
            transport=transport,
            secret_store=self._store,
            token_access=token_access,
            token_consent=token_consent,
            account_id=account_id,
            fingerprint=fingerprint,
        )
