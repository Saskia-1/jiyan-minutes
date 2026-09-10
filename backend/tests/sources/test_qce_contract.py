"""Contract tests for the fixed, read-only QCE v6.2.10 surface."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from dataclasses import FrozenInstanceError
import sys
import unittest
from pathlib import Path
from typing import get_args, get_type_hints

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from chat_to_howto.domain.consent import ConsentRecord, SecretAccessScope, SecretOperation
from chat_to_howto.security.secrets import InMemorySecretStore
from chat_to_howto.sources.base import CompletenessEvidence, FetchCancelled, PaginationError, SourceAdapterError, UpstreamProtocolError
from chat_to_howto.sources.qce import QceAdapter


NOW = datetime(2026, 9, 10, tzinfo=timezone.utc)


class RecordingTransport:
    def __init__(self, responses: list[dict[str, object]]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str, dict[str, str], dict[str, object] | None, object | None]] = []

    def request(self, method: str, path: str, *, headers: dict[str, str], params=None, json=None):
        self.calls.append((method, path, headers, params, json))
        if not self.responses:
            raise RuntimeError("synthetic final-page failure")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def token_access(operation: SecretOperation) -> SecretAccessScope:
    return SecretAccessScope(
        operation=operation,
        secret_reference="qce-test-token",
        secret_backend="memory",
        source_id="qq",
        account_id="account-1",
        client_version="qq-client",
        tool_version="6.2.10",
        account_version="account-version",
        configuration_nonce="configuration-1",
    )


def grant(access: SecretAccessScope) -> ConsentRecord:
    return ConsentRecord.grant(access, expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc), now=NOW)


def adapter(responses: list[dict[str, object]]) -> tuple[QceAdapter, RecordingTransport]:
    store = InMemorySecretStore()
    write = token_access(SecretOperation.SET)
    store.set(access=write, consent=grant(write), value="qce-synthetic-token")
    transport = RecordingTransport(responses)
    return QceAdapter(
        transport=transport,
        secret_store=store,
        token_access=token_access(SecretOperation.GET),
        token_consent=grant(token_access(SecretOperation.GET)),
        account_id="account-1",
    ), transport


class QceContractTests(unittest.TestCase):
    def test_verified_status_uses_the_shared_source_range_vocabulary(self) -> None:
        statuses = get_args(get_type_hints(CompletenessEvidence)["status"])
        self.assertEqual(set(statuses), {"source_range_verified", "partial", "unknown", "empty"})

    def test_cancel_during_secret_read_prevents_the_transport_call(self) -> None:
        for operation in ("list_conversations", "fetch_range"):
            with self.subTest(operation=operation):
                source, transport = adapter([])
                original = source._store.get
                def cancelling_secret_read(**kwargs):
                    token = original(**kwargs)
                    source.cancel()
                    return token
                source._store.get = cancelling_secret_read
                with self.assertRaises(FetchCancelled):
                    if operation == "fetch_range":
                        source.fetch_range("friend:peer", 0, 10)
                    else:
                        source.list_conversations()
                self.assertEqual(transport.calls, [])

    def test_rejects_responses_exceeding_the_requested_page_limit(self) -> None:
        for key, entry in (("messages", {"msgId": "a", "msgTime": 0}), ("friends", {"uid": "a"}), ("groups", {"groupCode": "a"})):
            with self.subTest(key=key):
                responses = []
                if key == "groups":
                    responses.append({"success": True, "data": {"friends": [], "currentPage": 1, "hasNext": False}})
                responses.append({"success": True, "data": {key: [entry] * 2001, "currentPage": 1, "hasNext": False}})
                source, transport = adapter(responses)
                with self.assertRaises(PaginationError):
                    if key == "messages":
                        source.fetch_range("friend:peer", 0, 10)
                    else:
                        source.list_conversations()
                self.assertEqual(len(transport.calls), len(responses))

    def test_raw_message_budget_counts_duplicates_and_stops_before_next_page(self) -> None:
        responses = [
            {"success": True, "data": {"messages": [{"msgId": "same", "msgTime": 0}] * 2000, "currentPage": page, "hasNext": True}}
            for page in range(1, 11)
        ]
        source, transport = adapter(responses)
        with self.assertRaises(PaginationError):
            source.fetch_range("friend:peer", 0, 10)
        self.assertEqual(len(transport.calls), 10)

    def test_budget_never_changes_page_size_and_therefore_page_offsets(self) -> None:
        source, transport = adapter([
            {"success": True, "data": {"messages": [{"msgId": "same", "msgTime": 0}] * (1999 if page == 1 else 2000), "currentPage": page, "hasNext": True}}
            for page in range(1, 11)
        ])
        with self.assertRaises(PaginationError):
            source.fetch_range("friend:peer", 0, 10)
        self.assertEqual(len(transport.calls), 10)
        self.assertEqual([call[4]["limit"] for call in transport.calls], [2000] * 10)

    def test_enumeration_raw_budget_is_shared_across_friends_and_groups(self) -> None:
        responses = [
            {"success": True, "data": {"friends": [{"uid": "same"}] * 2000, "currentPage": page, "hasNext": page < 3}}
            for page in range(1, 4)
        ]
        responses.extend(
            {"success": True, "data": {"groups": [{"groupCode": "same"}] * 2000, "currentPage": page, "hasNext": True}}
            for page in range(1, 3)
        )
        source, transport = adapter(responses)
        with self.assertRaises(PaginationError):
            source.list_conversations()
        self.assertEqual(len(transport.calls), 5)

    def test_empty_continuation_pages_are_bounded(self) -> None:
        for key, max_pages in (("messages", 20), ("friends", 10)):
            with self.subTest(key=key):
                source, transport = adapter([
                    {"success": True, "data": {key: [], "currentPage": page, "hasNext": True}}
                    for page in range(1, max_pages + 1)
                ])
                with self.assertRaises(PaginationError):
                    if key == "messages":
                        source.fetch_range("friend:peer", 0, 10)
                    else:
                        source.list_conversations()
                self.assertEqual(len(transport.calls), max_pages)

    def test_recent_contacts_obey_the_explicit_response_limit(self) -> None:
        source, transport = adapter([
            {"success": True, "data": {"friends": [], "currentPage": 1, "hasNext": False}},
            {"success": True, "data": {"groups": [], "currentPage": 1, "hasNext": False}},
            {"success": True, "data": {"contacts": [{"peerUid": "a", "chatType": 1}] * 101}},
        ])
        with self.assertRaises(PaginationError):
            source.list_conversations()
        self.assertEqual(len(transport.calls), 3)

    def test_rejected_id_occurrence_cannot_hide_later_usable_record(self) -> None:
        source, _ = adapter([
            {"success": True, "data": {"currentPage": 1, "hasNext": True, "messages": [
                {"msgId": "a", "msgTime": None},
                {"msgId": "b", "msgTime": 1_700_000_002_000},
            ]}},
            {"success": True, "data": {"currentPage": 2, "hasNext": False, "messages": [
                {"msgId": "a", "msgTime": 1_700_000_000_000},
                {"msgId": "b", "msgTime": 1_700_000_000_100},
                {"msgId": "b", "msgTime": 1_700_000_000_100},
            ]}},
        ])
        page = source.fetch_range("friend:peer", 1_700_000_000_000, 1_700_000_001_000)
        self.assertEqual([item.message_id for item in page.messages], ["a", "b"])
        self.assertEqual(page.completeness.duplicate_count, 3)
        self.assertEqual(page.completeness.fetched_count, 5)
        self.assertEqual(page.completeness.accepted_count, 2)

    def test_dependency_errors_are_body_free_even_when_already_typed(self) -> None:
        for operation in ("health", "list_conversations", "fetch_range"):
            for error_type in (RuntimeError, SourceAdapterError, UpstreamProtocolError):
                with self.subTest(operation=operation, error_type=error_type):
                    source, _ = adapter([error_type("synthetic-private-sentinel")])
                    with self.assertRaises(SourceAdapterError) as caught:
                        if operation == "fetch_range":
                            source.fetch_range("friend:peer", 0, 10)
                        else:
                            getattr(source, operation)()
                    error = caught.exception
                    self.assertIs(type(error), SourceAdapterError)
                    self.assertNotIn("synthetic-private-sentinel", str(error))
                    self.assertNotIn("synthetic-private-sentinel", repr(error))
                    self.assertIsNone(error.__cause__)
                    self.assertTrue(error.__suppress_context__)

    def test_secret_store_errors_are_sanitized_before_transport(self) -> None:
        for operation in ("list_conversations", "fetch_range"):
            with self.subTest(operation=operation):
                source, transport = adapter([])
                def denied(**_kwargs):
                    raise SourceAdapterError("synthetic-private-secret")
                source._store.get = denied
                with self.assertRaises(SourceAdapterError) as caught:
                    if operation == "fetch_range":
                        source.fetch_range("friend:peer", 0, 10)
                    else:
                        source.list_conversations()
                self.assertIs(type(caught.exception), SourceAdapterError)
                self.assertNotIn("synthetic-private-secret", repr(caught.exception))
                self.assertIsNone(caught.exception.__cause__)
                self.assertTrue(caught.exception.__suppress_context__)
                self.assertEqual(transport.calls, [])

    def test_health_and_each_enumeration_response_observe_cancellation(self) -> None:
        responses = [
            {"success": True, "data": {"friends": [], "currentPage": 1, "hasNext": False}},
            {"success": True, "data": {"groups": [], "currentPage": 1, "hasNext": False}},
            {"success": True, "data": {"contacts": []}},
        ]
        for cancel_at in range(3):
            with self.subTest(cancel_at=cancel_at):
                source, transport = adapter(responses)
                original = transport.request
                def request(*args, **kwargs):
                    response = original(*args, **kwargs)
                    if len(transport.calls) == cancel_at + 1:
                        source.cancel()
                    return response
                transport.request = request
                with self.assertRaises(FetchCancelled):
                    source.list_conversations()
                self.assertEqual(len(transport.calls), cancel_at + 1)
        source, transport = adapter([])
        def health_response(*args, **kwargs):
            source.cancel()
            return {"success": True, "data": {"status": "healthy", "mode": "full", "online": True}}
        transport.request = health_response
        with self.assertRaises(FetchCancelled):
            source.health()

    def test_enumeration_rejects_invalid_ids_types_and_pages(self) -> None:
        friend_page = {"friends": [], "currentPage": 1, "hasNext": False}
        group_page = {"groups": [], "currentPage": 1, "hasNext": False}
        for invalid in (True, False, 1.0, "1", None):
            with self.subTest(page=invalid):
                source, _ = adapter([{"success": True, "data": {**friend_page, "currentPage": invalid}}])
                with self.assertRaises(PaginationError):
                    source.list_conversations()
        for item in ({"uid": True}, {"uid": ""}, None):
            with self.subTest(friend=item):
                source, _ = adapter([{"success": True, "data": {**friend_page, "friends": [item]}}])
                with self.assertRaises(UpstreamProtocolError):
                    source.list_conversations()
        for item in ({"peerUid": True, "chatType": 1}, {"peerUid": "p", "chatType": True}, {"peerUid": "p", "chatType": 3}, {"peerUid": "p", "chatType": 1.0}, {"peerUid": "p"}, None):
            with self.subTest(recent=item):
                source, _ = adapter([{"success": True, "data": friend_page}, {"success": True, "data": group_page}, {"success": True, "data": {"contacts": [item]}}])
                with self.assertRaises(UpstreamProtocolError):
                    source.list_conversations()

    def test_evidence_accounts_for_range_slices_and_every_rejected_record(self) -> None:
        source, _ = adapter([
            {"success": True, "data": {"currentPage": 1, "hasNext": True, "messages": [
                {"msgId": "a", "msgTime": 1_700_000_000, "elements": []},
                {"msgId": "outside", "msgTime": 1_700_000_002_000, "elements": []},
                {"msgId": "unknown", "msgTime": True, "elements": []},
                "malformed",
            ]}},
            {"success": True, "data": {"currentPage": 2, "hasNext": False, "messages": [
                {"msgId": "a", "msgTime": 1_700_000_000_000, "elements": []},
                {"msgId": True, "msgTime": 1_700_000_000_500, "elements": []},
                {"msgId": "broken", "msgTime": 1_700_000_000_600, "elements": "bad"},
            ]}},
        ])
        page = source.fetch_range("friend:peer", 1_700_000_000_000, 1_700_000_001_000)
        self.assertEqual([item.message_id for item in page.messages], ["a", None])
        evidence = page.completeness
        self.assertEqual(evidence.status, "partial")
        self.assertEqual(evidence.requested_start_ms, 1_700_000_000_000)
        self.assertEqual(evidence.requested_end_ms, 1_700_000_001_000)
        self.assertEqual(evidence.observed_start_ms, 1_700_000_000_000)
        self.assertEqual(evidence.observed_end_ms, 1_700_000_000_500)
        self.assertEqual((evidence.fetched_count, evidence.accepted_count, evidence.duplicate_count), (7, 2, 1))
        self.assertEqual((evidence.unknown_time_count, evidence.missing_id_count, evidence.parse_error_count), (1, 1, 2))
        self.assertEqual(evidence.termination, "upstream_exhausted")
        self.assertEqual(evidence.warnings, ("qce_history_unproven", "duplicate_ids", "unknown_timestamps", "missing_identifiers", "malformed_records"))
        self.assertEqual([(item.page, item.cursor, item.requested, item.returned, item.accepted, item.has_next) for item in evidence.slices], [(1, None, 2000, 4, 1, True), (2, None, 2000, 3, 1, False)])
        with self.assertRaises(FrozenInstanceError):
            evidence.accepted_count = 99
        with self.assertRaises(FrozenInstanceError):
            evidence.slices[0].accepted = 99

    def test_empty_and_unknown_time_only_pages_never_claim_complete(self) -> None:
        for raw, unknown_count in (([], 0), ([{"msgId": "a", "msgTime": False}], 1)):
            with self.subTest(raw=raw):
                source, _ = adapter([{"success": True, "data": {"currentPage": 1, "hasNext": False, "messages": raw}}])
                evidence = source.fetch_range("friend:peer", 0, 10).completeness
                self.assertEqual(evidence.status, "partial")
                self.assertIsNone(evidence.observed_start_ms)
                self.assertIsNone(evidence.observed_end_ms)
                self.assertEqual(evidence.fetched_count, len(raw))
                self.assertEqual(evidence.accepted_count, 0)
                self.assertEqual(evidence.unknown_time_count, unknown_count)

    def test_response_time_cancel_never_returns_a_partial_page(self) -> None:
        """Cancellation racing with a response must win before normalized data escapes."""
        source, transport = adapter([])
        def response_after_cancel(*_args, **_kwargs):
            source.cancel()
            return {"success": True, "data": {"messages": [], "hasNext": False, "currentPage": 1}}
        transport.request = response_after_cancel
        with self.assertRaises(FetchCancelled):
            source.fetch_range("friend:peer", 0, 1)
    def test_enumerates_friend_and_group_pages_without_skipping_second_page(self) -> None:
        """Both QCE collections are paged; one page is not a complete enumeration."""
        source, transport = adapter([
            {"success": True, "data": {"friends": [{"uid": "f1"}], "currentPage": 1, "hasNext": True}},
            {"success": True, "data": {"friends": [{"uid": "f2"}], "currentPage": 2, "hasNext": False}},
            {"success": True, "data": {"groups": [{"groupCode": "g1"}], "currentPage": 1, "hasNext": True}},
            {"success": True, "data": {"groups": [{"groupCode": "g2"}], "currentPage": 2, "hasNext": False}},
            {"success": True, "data": {"contacts": []}},
        ])
        self.assertEqual([item.conversation_id for item in source.list_conversations()], ["friend:f1", "friend:f2", "group:g1", "group:g2"])
        self.assertEqual([call[3]["page"] for call in transport.calls[:4]], [1, 2, 1, 2])
    def test_cross_page_stable_ids_dedupe_but_same_text_and_time_different_ids_survive(self) -> None:
        """A page overlap must not erase distinct messages sharing text/timestamp."""
        source, _ = adapter([
            {"success": True, "data": {"messages": [{"msgId": "a", "msgTime": 1_700_000_000_000, "elements": []}, {"msgId": "b", "msgTime": 1_700_000_000_000, "elements": []}], "hasNext": True, "currentPage": 1}},
            {"success": True, "data": {"messages": [{"msgId": "a", "msgTime": 1_700_000_000_000, "elements": []}, {"msgId": "c", "msgTime": 1_700_000_000_000, "elements": []}], "hasNext": False, "currentPage": 2}},
        ])
        page = source.fetch_range("friend:peer", 1_700_000_000_000, 1_700_000_001_000)
        self.assertEqual([item.message_id for item in page.messages], ["a", "b", "c"])
        self.assertEqual(page.completeness.duplicate_count, 1)
    def test_uses_only_locked_read_endpoints_and_exact_request_shapes(self) -> None:
        """Changing method, token placement, or range units would violate QCE v6.2.10."""
        source, transport = adapter(
            [
                {"success": True, "data": {"status": "healthy", "mode": "full", "online": True}},
                {"success": True, "data": {"friends": [{"uid": "friend-1", "nick": "friend"}], "hasNext": False, "currentPage": 1}},
                {"success": True, "data": {"groups": [{"groupCode": "group-1", "groupName": "group"}], "hasNext": False, "currentPage": 1}},
                {"success": True, "data": {"contacts": [{"peerUid": "recent-1", "peerName": "recent", "chatType": 1}]}},
                {
                    "success": True,
                    "data": {
                        "messages": [{"msgId": "m1", "msgTime": 1_700_000_000_000, "elements": [{"textElement": {"content": "synthetic"}}]}],
                        "hasNext": False,
                        "currentPage": 1,
                    },
                },
            ]
        )

        self.assertFalse(source.health().ready)
        self.assertEqual(
            [(item.conversation_id, item.conversation_type) for item in source.list_conversations()],
            [("friend:friend-1", "friend"), ("group:group-1", "group"), ("friend:recent-1", "friend")],
        )
        page = source.fetch_range("friend:friend-1", 1_700_000_000_000, 1_700_000_001_000)

        self.assertEqual([message.message_id for message in page.messages], ["m1"])
        self.assertEqual(page.messages[0].text, "synthetic")
        self.assertEqual(page.completeness.status, "partial")
        self.assertEqual(
            [(method, path) for method, path, *_ in transport.calls],
            [
                ("GET", "/health"),
                ("GET", "/api/friends"),
                ("GET", "/api/groups"),
                ("GET", "/api/recent-contacts"),
                ("POST", "/api/messages/fetch"),
            ],
        )
        self.assertEqual(transport.calls[0][2], {})
        for _, _, headers, _, _ in transport.calls[1:]:
            self.assertEqual(headers, {"Authorization": "Bearer qce-synthetic-token"})
        self.assertEqual(transport.calls[1][3], {"page": 1, "limit": 2000, "forceRefresh": False})
        self.assertEqual(transport.calls[2][3], {"page": 1, "limit": 2000, "forceRefresh": False})
        self.assertEqual(transport.calls[3][3], {"limit": 100, "includeAll": True})
        self.assertEqual(
            transport.calls[4][4],
            {
                "peer": {"chatType": 1, "peerUid": "friend-1"},
                "filter": {"startTime": 1_700_000_000_000, "endTime": 1_700_000_000_999},
                "batchSize": 5000,
                "page": 1,
                "limit": 2000,
                "forceRefresh": False,
            },
        )

    def test_paginates_all_pages_but_marks_lazy_history_limits_partial(self) -> None:
        """Stopping after one successful page would silently omit QCE history."""
        source, transport = adapter(
            [
                {"success": True, "data": {"messages": [{"msgId": "m1", "msgTime": 1_700_000_000_000, "elements": []}], "hasNext": True, "currentPage": 1}},
                {"success": True, "data": {"messages": [{"msgId": "m2", "msgTime": 1_700_000_000_000, "elements": []}], "hasNext": False, "currentPage": 2}},
            ]
        )
        page = source.fetch_range("friend:peer-1", 1_700_000_000_000, 1_700_000_001_000)
        self.assertEqual([message.message_id for message in page.messages], ["m1", "m2"])
        self.assertEqual([call[4]["page"] for call in transport.calls], [1, 2])
        self.assertEqual(page.completeness.status, "partial")

    def test_rejects_duplicate_pages_and_last_page_failure_without_false_completion(self) -> None:
        """A repeated cursor or failed final request must not be reported as complete."""
        repeated, _ = adapter(
            [
                {"success": True, "data": {"messages": [], "hasNext": True, "currentPage": 1}},
                {"success": True, "data": {"messages": [], "hasNext": True, "currentPage": 1}},
            ]
        )
        with self.assertRaises(PaginationError):
            repeated.fetch_range("friend:peer-1", 0, 20)

        failing, _ = adapter(
            [
                {"success": True, "data": {"messages": [], "hasNext": True, "currentPage": 1}},
                RuntimeError("synthetic final-page failure"),
            ]
        )
        with self.assertRaises(SourceAdapterError) as raised:
            failing.fetch_range("friend:peer-1", 0, 20)
        self.assertNotIn("synthetic final-page failure", str(raised.exception))

    def test_rejects_unsuccessful_envelopes_without_exposing_the_upstream_body(self) -> None:
        """A QCE error body can contain sensitive diagnostics and is never public."""
        source, _ = adapter([{"success": False, "message": "synthetic-sensitive-body"}])
        with self.assertRaises(SourceAdapterError) as raised:
            source.fetch_range("friend:peer-1", 0, 20)
        self.assertNotIn("synthetic-sensitive-body", str(raised.exception))

    def test_cancellation_is_local_and_stops_before_the_next_request(self) -> None:
        """Calling cancel must never use QCE task or deletion endpoints."""
        source, transport = adapter(
            [{"success": True, "data": {"messages": [], "hasNext": True, "currentPage": 1}}]
        )
        source.cancel()
        with self.assertRaises(FetchCancelled):
            source.fetch_range("friend:peer-1", 0, 20)
        self.assertEqual(transport.calls, [])

    def test_uses_numeric_group_chat_type_and_marks_missing_identifiers_uncertain(self) -> None:
        """QCE requires numeric peer kinds; a missing stable id cannot prove a complete page."""
        source, transport = adapter(
            [{"success": True, "data": {"messages": [{"msgTime": 1_700_000_000_000, "elements": [{"textElement": {"content": "same"}}]}], "hasNext": False, "currentPage": 1}}]
        )
        page = source.fetch_range("group:group-1", 1_700_000_000_000, 1_700_000_001_000)
        self.assertEqual(transport.calls[0][4]["peer"], {"chatType": 2, "peerUid": "group-1"})
        self.assertIsNone(page.messages[0].message_id)
        self.assertIn("identifier", page.completeness.reason)
