"""Synthetic tests for the trusted production adapter factory."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from chat_to_howto.domain.consent import (
    ConsentRecord,
    SecretAccessScope,
    SecretOperation,
)
from chat_to_howto.domain.sources import SourceFingerprint
from chat_to_howto.security.secrets import InMemorySecretStore
from chat_to_howto.sources.base import SourceAdapterError


NOW = datetime(2026, 9, 11, tzinfo=timezone.utc)


class RecordingTransportFactory:
    def __init__(self) -> None:
        self.calls = []

    def __call__(self, base_url, *, expected_port, source):
        transport = object()
        self.calls.append((base_url, expected_port, source, transport))
        return transport


def _fingerprint(source_id: str) -> SourceFingerprint:
    return SourceFingerprint(
        client_version=f"{source_id}-client-reviewed",
        tool_version=f"{source_id}-tool-reviewed",
        account_version=f"{source_id}-account-reviewed",
    )


def _access(source_id: str, fingerprint: SourceFingerprint) -> SecretAccessScope:
    return SecretAccessScope(
        operation=SecretOperation.GET,
        secret_reference=f"{source_id}-opaque-reference",
        secret_backend="memory",
        source_id=source_id,
        account_id="approved-account",
        configuration_nonce="approved-configuration",
        **fingerprint.model_dump(),
    )


def _factory(store, transports):
    try:
        from chat_to_howto.sources.factory import LocalSourceAdapterFactory
    except ImportError:
        pytest.fail("trusted local source adapter factory is missing")
    return LocalSourceAdapterFactory(
        secret_store=store,
        base_urls={
            "qq": "http://127.0.0.1:40653",
            "wechat": "http://127.0.0.1:6131",
        },
        fingerprints={source_id: _fingerprint(source_id) for source_id in ("qq", "wechat")},
        transport_factory=transports,
        clock=lambda: NOW,
    )


@pytest.mark.parametrize(
    ("source_id", "port", "transport_name", "adapter_name"),
    [
        ("qq", 40653, "qce", "QceAdapter"),
        ("wechat", 6131, "tracememo", "TraceMemoAdapter"),
    ],
)
def test_factory_supplies_trusted_real_device_identity_and_backend_secret_grant(
    source_id, port, transport_name, adapter_name
):
    """Removing factory-owned identity or forwarding a browser secret would cross trust boundaries."""
    store = InMemorySecretStore()
    transports = RecordingTransportFactory()
    factory = _factory(store, transports)
    fingerprint = _fingerprint(source_id)
    access = _access(source_id, fingerprint)
    consent = ConsentRecord.grant(
        access,
        now=NOW,
        expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
    )

    adapter = factory(
        source_id,
        "approved-account",
        token_access=access,
        token_consent=consent,
    )

    assert type(adapter).__name__ == adapter_name
    assert adapter.fingerprint == fingerprint
    assert adapter.evidence_class == "real_device"
    assert adapter._store is store
    assert adapter._access is access
    assert adapter._consent is consent
    assert transports.calls == [
        (
            "http://127.0.0.1:" + str(port),
            port,
            transport_name,
            adapter._transport,
        )
    ]


@pytest.mark.parametrize("source_id", ["qq", "wechat"])
def test_factory_rejects_missing_or_cross_source_secret_authority_before_transport(source_id):
    """Constructing a production adapter without the exact server grant would bypass consent."""
    store = InMemorySecretStore()
    transports = RecordingTransportFactory()
    factory = _factory(store, transports)
    other = "wechat" if source_id == "qq" else "qq"
    wrong_access = _access(other, _fingerprint(other))
    wrong_consent = ConsentRecord.grant(
        wrong_access,
        now=NOW,
        expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
    )

    with pytest.raises(SourceAdapterError, match="Source authorization is unavailable"):
        factory(source_id, "approved-account")
    with pytest.raises(SourceAdapterError, match="Source authorization is unavailable"):
        factory(
            source_id,
            "approved-account",
            token_access=wrong_access,
            token_consent=wrong_consent,
        )

    assert transports.calls == []
