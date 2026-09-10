"""Contract tests for TraceMemo's strict read-only local API allowlist."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from chat_to_howto.domain.consent import ConsentRecord, SecretAccessScope, SecretOperation
from chat_to_howto.security.secrets import InMemorySecretStore
from chat_to_howto.sources.base import FetchCancelled
from chat_to_howto.sources.tracememo import TraceMemoAdapter


NOW = datetime(2026, 9, 10, tzinfo=timezone.utc)


class RecordingTransport:
    def __init__(self, responses: list[dict[str, object]]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str, dict[str, str], dict[str, object] | None, object | None]] = []

    def request(self, method: str, path: str, *, headers: dict[str, str], params=None, json=None):
        self.calls.append((method, path, headers, params, json))
        return self.responses.pop(0)


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
    return ConsentRecord.grant(access, expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc), now=NOW)


def adapter(responses: list[dict[str, object]]) -> tuple[TraceMemoAdapter, RecordingTransport]:
    store = InMemorySecretStore()
    write = token_access(SecretOperation.SET)
    store.set(access=write, consent=grant(write), value="tracememo-synthetic-token")
    transport = RecordingTransport(responses)
    return TraceMemoAdapter(
        transport=transport,
        secret_store=store,
        token_access=token_access(SecretOperation.GET),
        token_consent=grant(token_access(SecretOperation.GET)),
        account_id="account-1",
    ), transport


class TraceMemoContractTests(unittest.TestCase):
    def test_health_is_not_readiness_and_only_contacts_and_chatlog_are_allowed(self) -> None:
        """A live server without a ready database cannot promote the source to ready."""
        source, transport = adapter(
            [
                {"ok": True, "ready": False, "service": "TraceMemo Reader", "version": "1.0.0"},
                {"count": 1, "contacts": [{"m_nsUsrName": "wxid-1", "m_nsNickName": "synthetic", "md5": "stable-md5", "type": "user"}]},
                {
                    "count": 2,
                    "messages": [
                        {"id": "before", "createTime": 99, "content": "before"},
                        {"id": "in", "createTime": 100, "content": "in"},
                    ],
                },
            ]
        )
        self.assertFalse(source.health().ready)
        conversations = source.list_conversations()
        page = source.fetch_range("stable-md5", 100_000, 101_000)

        self.assertEqual(conversations[0].conversation_id, "stable-md5")
        self.assertEqual([message.message_id for message in page.messages], ["in"])
        self.assertEqual(page.completeness.status, "unknown")
        self.assertEqual(
            [(method, path) for method, path, *_ in transport.calls],
            [("GET", "/api/v1/health"), ("GET", "/api/v1/contact"), ("GET", "/api/v1/chatlog")],
        )
        self.assertEqual(transport.calls[0][2], {})
        self.assertEqual(transport.calls[1][2], {"Authorization": "Bearer tracememo-synthetic-token"})
        self.assertEqual(
            transport.calls[2][3], {"talker": "stable-md5", "startTime": 100, "endTime": 101}
        )

    def test_same_second_messages_are_preserved_and_no_pagination_claim_is_invented(self) -> None:
        """Truncating identical-second messages would fabricate a precise range result."""
        source, transport = adapter(
            [
                {
                    "count": 3,
                    "messages": [
                        {"id": "a", "createTime": 100, "content": "one"},
                        {"id": "b", "createTime": 100, "content": "two"},
                        {"id": "c", "createTime": 101, "content": "end-boundary"},
                    ],
                }
            ]
        )
        page = source.fetch_range("wxid-1", 100_000, 101_000)
        self.assertEqual([message.message_id for message in page.messages], ["a", "b"])
        self.assertEqual(page.completeness.status, "unknown")
        self.assertEqual(transport.calls[0][3], {"talker": "wxid-1", "startTime": 100, "endTime": 101})

    def test_cancellation_is_local_before_any_chatlog_request(self) -> None:
        """TraceMemo cancellation must not reach its report, agent, or write APIs."""
        source, transport = adapter([])
        source.cancel()
        with self.assertRaises(FetchCancelled):
            source.fetch_range("wxid-1", 100_000, 101_000)
        self.assertEqual(transport.calls, [])
