"""Synthetic contracts for TraceMemo's read-only, scope-bound adapter."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from chat_to_howto.domain.consent import (
    ConsentRecord,
    SecretAccessScope,
    SecretOperation,
)
from chat_to_howto.domain.sources import SourceFingerprint
from chat_to_howto.security.secrets import InMemorySecretStore
from chat_to_howto.sources.base import (
    FetchCancelled,
    SourceAdapterError,
    UpstreamProtocolError,
)
from chat_to_howto.sources.tracememo import TraceMemoAdapter


NOW = datetime(2026, 9, 10, tzinfo=timezone.utc)
TALKER = "abcdef0123456789abcdef0123456789"
OTHER = "0123456789abcdef0123456789abcdef"
SENTINEL = "synthetic-token-body-path-sentinel"


class RecordingTransport:
    def __init__(self, responses: list[object]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple] = []
        self.on_response: Callable[[], None] | None = None

    def request(self, method, path, *, headers, params=None, json=None):
        self.calls.append((method, path, headers, params, json))
        response = self.responses.pop(0)
        if self.on_response is not None:
            self.on_response()
        if isinstance(response, Exception):
            raise response
        return response


def token_access(operation: SecretOperation) -> SecretAccessScope:
    return SecretAccessScope(
        operation=operation,
        secret_reference="tracememo-test-token",
        secret_backend="memory",
        source_id="wechat",
        account_id="account-1",
        client_version="wechat-4.1.13",
        tool_version="2.3.0",
        account_version="account-version",
        configuration_nonce="configuration-1",
    )


def grant(access: SecretAccessScope) -> ConsentRecord:
    return ConsentRecord.grant(
        access, expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc), now=NOW
    )


def adapter(responses: list[object], *, store=None):
    if store is None:
        store = InMemorySecretStore()
        write = token_access(SecretOperation.SET)
        store.set(access=write, consent=grant(write), value="tracememo-synthetic-token")
    transport = RecordingTransport(responses)
    source = TraceMemoAdapter(
        transport=transport,
        secret_store=store,
        token_access=token_access(SecretOperation.GET),
        token_consent=grant(token_access(SecretOperation.GET)),
        account_id="account-1",
        fingerprint=SourceFingerprint(
            client_version="wechat-4.1.13",
            tool_version="2.3.0",
            account_version="account-version",
        ),
    )
    return source, transport


def contacts(*identifiers):
    return {
        "contacts": [
            {"md5": identifier, "m_nsNickName": "same name", "type": "user"}
            for identifier in identifiers
        ]
    }


def selected(responses):
    source, transport = adapter([contacts(TALKER), *responses])
    source.list_conversations()
    return source, transport


def assert_safe_error(error, expected_type):
    assert type(error) is expected_type
    assert SENTINEL not in str(error)
    assert SENTINEL not in repr(error)
    assert error.__cause__ is None
    assert error.__suppress_context__ is True


@pytest.mark.parametrize("ready", [False, True])
def test_health_is_liveness_only_and_never_reads_a_secret(ready):
    class FailingStore:
        def get(self, **kwargs):
            raise AssertionError(SENTINEL)

    source, transport = adapter([{"ok": True, "ready": ready}], store=FailingStore())
    health = source.health()
    assert health.available is True
    assert health.ready is False
    assert transport.calls == [("GET", "/api/v1/health", {}, None, None)]


@pytest.mark.parametrize("operation", ["health", "list_conversations", "fetch_range"])
@pytest.mark.parametrize(
    "failure", [RuntimeError, SourceAdapterError, UpstreamProtocolError]
)
def test_all_transport_failures_have_fresh_body_free_public_errors(operation, failure):
    response = failure(SENTINEL)
    factory = selected if operation == "fetch_range" else adapter
    source, _ = factory([response])
    with pytest.raises(SourceAdapterError) as raised:
        if operation == "fetch_range":
            source.fetch_range(TALKER, 100_000, 101_000)
        else:
            getattr(source, operation)()
    assert_safe_error(raised.value, SourceAdapterError)


@pytest.mark.parametrize("operation", ["list_conversations", "fetch_range"])
def test_secret_store_failures_are_sanitized_before_transport(operation):
    class FailingStore:
        def get(self, **kwargs):
            raise RuntimeError(SENTINEL)

    source, transport = selected([])
    source._store = FailingStore()
    with pytest.raises(SourceAdapterError) as raised:
        if operation == "fetch_range":
            source.fetch_range(TALKER, 100_000, 101_000)
        else:
            source.list_conversations()
    assert_safe_error(raised.value, SourceAdapterError)
    assert len(transport.calls) == 1


@pytest.mark.parametrize("operation", ["health", "list_conversations", "fetch_range"])
@pytest.mark.parametrize("response", [None, [SENTINEL], {"body": SENTINEL}])
def test_malformed_response_shapes_raise_body_free_protocol_errors(operation, response):
    factory = selected if operation == "fetch_range" else adapter
    source, _ = factory([response])
    with pytest.raises(UpstreamProtocolError) as raised:
        if operation == "fetch_range":
            source.fetch_range(TALKER, 100_000, 101_000)
        else:
            getattr(source, operation)()
    assert_safe_error(raised.value, UpstreamProtocolError)


def test_duplicate_names_use_enumerated_lowercase_md5_and_exact_read_only_request():
    source, transport = adapter([
        contacts(TALKER.upper(), OTHER),
        {"messages": [{"id": "a", "createTime": 100, "content": "one"}]},
    ])
    conversations = source.list_conversations()
    assert [item.conversation_id for item in conversations] == [TALKER, OTHER]
    assert [item.display_name for item in conversations] == ["same name", "same name"]
    page = source.fetch_range(TALKER.upper(), 100_000, 101_000)
    assert page.messages[0].conversation_id == TALKER
    assert transport.calls == [
        (
            "GET", "/api/v1/contact",
            {"Authorization": "Bearer tracememo-synthetic-token"}, {}, None,
        ),
        (
            "GET", "/api/v1/chatlog",
            {"Authorization": "Bearer tracememo-synthetic-token"},
            {"talker": TALKER, "startTime": 100, "endTime": 101}, None,
        ),
    ]


@pytest.mark.parametrize(
    "value",
    ["same name", "", "arbitrary", OTHER, True, 1, "a" * 31, "a" * 33, "g" * 32],
)
def test_unlisted_and_non_md5_talkers_cannot_reach_chatlog(value):
    source, transport = selected([{"messages": []}])
    with pytest.raises(ValueError):
        source.fetch_range(value, 100_000, 101_000)
    assert len(transport.calls) == 1


def test_even_valid_md5_requires_enumeration_by_this_adapter():
    source, transport = adapter([{"messages": []}])
    with pytest.raises(ValueError):
        source.fetch_range(TALKER, 100_000, 101_000)
    assert transport.calls == []


@pytest.mark.parametrize("bad", [
    None, {"md5": True}, {"md5": 1}, {"md5": "a" * 31},
    {"md5": "g" * 32}, {"md5": TALKER, "type": True},
    {"md5": TALKER, "type": 1}, {"md5": TALKER, "type": "unknown"},
    {"md5": TALKER, "type": "user", "m_nsNickName": True},
])
def test_malformed_contacts_fail_closed_without_enabling_earlier_contacts(bad):
    response = contacts(OTHER)
    response["contacts"].append(bad)
    source, transport = adapter([response])
    with pytest.raises(UpstreamProtocolError):
        source.list_conversations()
    with pytest.raises(ValueError):
        source.fetch_range(OTHER, 100_000, 101_000)
    assert len(transport.calls) == 1


def test_nonsecond_boundaries_overread_filter_and_report_precision_uncertainty():
    source, transport = selected([{"messages": [
        {"id": "before", "createTime": 100, "content": "same"},
        {"id": "a", "createTime": 101, "content": "same"},
        {"id": "b", "createTime": 101, "content": "same"},
        {"id": "end", "createTime": 102, "content": "same"},
    ]}])
    page = source.fetch_range(TALKER, 100_500, 101_500)
    assert [item.message_id for item in page.messages] == ["a", "b"]
    assert [item.source_order for item in page.messages] == [1, 2]
    assert transport.calls[-1][3] == {
        "talker": TALKER, "startTime": 100, "endTime": 102
    }
    evidence = page.completeness
    assert evidence.status == "unknown"
    assert evidence.requested_start_ms == 100_500
    assert evidence.requested_end_ms == 101_500
    assert evidence.observed_start_ms == evidence.observed_end_ms == 101_000
    assert (evidence.fetched_count, evidence.accepted_count) == (4, 2)
    assert evidence.duplicate_count == evidence.unknown_time_count == 0
    assert evidence.missing_id_count == evidence.parse_error_count == 0
    assert evidence.warnings == (
        "tracememo_history_unproven", "second_precision_boundaries"
    )
    assert evidence.termination == "no_upstream_pagination_or_completeness"
    assert page.next_cursor is None
    assert len(evidence.slices) == 1
    part = evidence.slices[0]
    assert part.page is part.cursor is part.requested is part.has_next is None
    assert (part.returned, part.accepted) == (4, 2)


def test_malformed_records_and_bool_fields_are_measured_without_false_ids_or_times():
    source, _ = selected([{"messages": [
        None,
        {"id": "a", "createTime": 99, "content": "outside"},
        {"id": "a", "createTime": 100, "content": "inside"},
        {"id": "a", "createTime": 100, "content": "duplicate"},
        {"id": True, "createTime": 100, "content": "missing id"},
        {"id": "time-bool", "createTime": True, "content": "bad time"},
        {"id": "bad-content", "createTime": 100, "content": {"body": SENTINEL}},
        {"id": "", "createTime": 100, "content": None},
        {"id": "end", "createTime": 101, "content": "end"},
    ]}])
    page = source.fetch_range(TALKER, 100_000, 101_000)
    assert [item.message_id for item in page.messages] == ["a", None, None]
    assert [item.source_order for item in page.messages] == [2, 4, 7]
    evidence = page.completeness
    assert (
        evidence.fetched_count, evidence.accepted_count, evidence.duplicate_count
    ) == (9, 3, 2)
    assert (
        evidence.unknown_time_count, evidence.missing_id_count, evidence.parse_error_count
    ) == (1, 2, 2)
    assert evidence.warnings == (
        "tracememo_history_unproven", "duplicate_ids", "unknown_timestamps",
        "missing_identifiers", "malformed_records",
    )
    assert SENTINEL not in repr(evidence)


def test_empty_response_is_unknown_with_zero_counts_and_no_observed_range():
    source, _ = selected([{"messages": []}])
    evidence = source.fetch_range(TALKER, 100_000, 101_000).completeness
    assert evidence.status == "unknown"
    assert evidence.observed_start_ms is evidence.observed_end_ms is None
    assert (
        evidence.fetched_count,
        evidence.accepted_count,
        evidence.duplicate_count,
        evidence.unknown_time_count,
        evidence.missing_id_count,
        evidence.parse_error_count,
    ) == (0, 0, 0, 0, 0, 0)
    assert evidence.slices[0].returned == 0
    assert evidence.warnings == ("tracememo_history_unproven",)


def test_fractional_empty_range_still_discloses_upstream_second_precision():
    source, _ = selected([{"messages": []}])
    evidence = source.fetch_range(TALKER, 100_100, 100_900).completeness
    assert evidence.status == "unknown"
    assert "second_precision_boundaries" in evidence.warnings


def test_declared_count_mismatch_does_not_hide_an_empty_unproven_response():
    source, _ = selected([{"count": 10, "messages": []}])
    page = source.fetch_range(TALKER, 100_000, 101_000)
    evidence = page.completeness
    assert page.messages == ()
    assert evidence.status == "unknown"
    assert evidence.fetched_count == evidence.accepted_count == 0
    assert evidence.slices[0].returned == 0
    assert evidence.warnings == (
        "tracememo_history_unproven", "upstream_count_mismatch"
    )


@pytest.mark.parametrize("count", [True, False, -1, "10", None, 1.0])
def test_invalid_declared_counts_raise_body_free_protocol_errors(count):
    source, _ = selected([{"count": count, "messages": [], "body": SENTINEL}])
    with pytest.raises(UpstreamProtocolError) as raised:
        source.fetch_range(TALKER, 100_000, 101_000)
    assert_safe_error(raised.value, UpstreamProtocolError)


@pytest.mark.parametrize("count", [0, 2])
def test_matching_declared_count_compares_raw_records_not_accepted_messages(count):
    raw = [
        {"id": "before", "createTime": 99},
        {"id": "inside", "createTime": 100},
    ] if count else []
    source, _ = selected([{"count": count, "messages": raw}])
    evidence = source.fetch_range(TALKER, 100_000, 101_000).completeness
    assert evidence.status == "unknown"
    assert evidence.fetched_count == count
    assert evidence.accepted_count == (1 if count else 0)
    assert evidence.warnings == ("tracememo_history_unproven",)


@pytest.mark.parametrize(
    "operation", ["health", "list_accounts", "list_conversations", "fetch_range"]
)
def test_cancelled_adapter_never_starts_another_request(operation):
    source, transport = adapter([])
    source.cancel()
    with pytest.raises(FetchCancelled):
        if operation == "fetch_range":
            source.fetch_range(TALKER, 100_000, 101_000)
        else:
            getattr(source, operation)()
    assert transport.calls == []


@pytest.mark.parametrize("operation,response", [
    ("health", {"ok": True, "ready": True}),
    ("list_conversations", contacts(TALKER)),
    ("fetch_range", {"messages": [{"id": "a", "createTime": 100}]}),
])
def test_response_cancellation_prevents_delivery_and_further_requests(operation, response):
    factory = selected if operation == "fetch_range" else adapter
    source, transport = factory([response])
    transport.on_response = source.cancel
    with pytest.raises(FetchCancelled):
        if operation == "fetch_range":
            source.fetch_range(TALKER, 100_000, 101_000)
        else:
            getattr(source, operation)()
    calls = len(transport.calls)
    with pytest.raises(FetchCancelled):
        source.health()
    assert len(transport.calls) == calls


@pytest.mark.parametrize(
    "bounds", [(True, 2000), (1000, False), (1.5, 2000), (1000, 1000), (2000, 1000)]
)
def test_range_requires_increasing_integer_milliseconds(bounds):
    source, transport = selected([])
    with pytest.raises(ValueError):
        source.fetch_range(TALKER, *bounds)
    assert len(transport.calls) == 1
