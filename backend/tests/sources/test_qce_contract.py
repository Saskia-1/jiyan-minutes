"""Contract tests for the fixed, read-only QCE v6.2.10 surface."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from chat_to_howto.domain.consent import ConsentRecord, SecretAccessScope, SecretOperation
from chat_to_howto.security.secrets import InMemorySecretStore
from chat_to_howto.sources.base import FetchCancelled, PaginationError, SourceAdapterError
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
