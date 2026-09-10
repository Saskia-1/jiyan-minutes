"""Tests for narrowly-scoped consent records."""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from chat_to_howto.domain.consent import (
    ConsentRecord,
    ConsentScope,
    SecretAccessScope,
    SecretOperation,
)


NOW = datetime.now(timezone.utc)


class ConsentTests(unittest.TestCase):
    def test_granted_record_matches_only_the_same_scope_fingerprint(self) -> None:
        """Broader reuse of a consent record would authorize a new operation."""
        scope = ConsentScope(
            source_id="qq",
            account_id="account-1",
            conversation_id="conversation-1",
            range_start="2026-09-01T00:00:00+08:00",
            range_end="2026-09-01T01:00:00+08:00",
            output_path="/private/tmp/verification.json",
            operation="verification",
            configuration_nonce="configuration-1",
        )
        changed_scope = scope.model_copy(update={"conversation_id": "conversation-2"})
        record = ConsentRecord.grant(scope, expires_at=NOW + timedelta(minutes=5), now=NOW)

        self.assertTrue(record.matches(scope))
        self.assertFalse(record.matches(changed_scope))
        self.assertNotEqual(scope.fingerprint(), changed_scope.fingerprint())

    def test_declined_record_never_authorizes_its_scope(self) -> None:
        """A declined consent must not be treated as an implicit approval."""
        scope = ConsentScope(
            source_id="wechat",
            account_id="account-1",
            conversation_id="conversation-1",
            range_start="2026-09-01T00:00:00+08:00",
            range_end="2026-09-01T01:00:00+08:00",
            output_path="/private/tmp/verification.json",
            operation="verification",
            configuration_nonce="configuration-1",
        )

        self.assertFalse(
            ConsentRecord.decline(scope, expires_at=NOW + timedelta(minutes=5), now=NOW).matches(
                scope, now=NOW
            )
        )

    def test_secret_access_scope_requires_the_same_operation_reference_and_versions(self) -> None:
        """Changing secret operation or version must require a fresh consent."""
        scope = SecretAccessScope(
            operation=SecretOperation.SET,
            secret_reference="qq-token",
            secret_backend="keychain",
            source_id="qq",
            account_id="account-1",
            client_version="9.9.0",
            tool_version="1.2.3",
            account_version="account-v1",
            configuration_nonce="configuration-1",
        )
        record = ConsentRecord.grant(scope, expires_at=NOW + timedelta(minutes=5), now=NOW)

        self.assertTrue(record.matches(scope, now=NOW))
        self.assertFalse(
            record.matches(scope.model_copy(update={"operation": SecretOperation.GET}), now=NOW)
        )
        self.assertFalse(
            record.matches(scope.model_copy(update={"tool_version": "1.2.4"}), now=NOW)
        )

    def test_expired_secret_access_consent_never_matches(self) -> None:
        """Expired approval must not be reusable for a later secret operation."""
        scope = SecretAccessScope(
            operation=SecretOperation.GET,
            secret_reference="qq-token",
            secret_backend="memory",
            source_id="qq",
            account_id="account-1",
            client_version="9.9.0",
            tool_version="1.2.3",
            account_version="account-v1",
            configuration_nonce="configuration-1",
        )
        record = ConsentRecord.grant(
            scope,
            expires_at=NOW - timedelta(minutes=1),
            now=NOW - timedelta(minutes=2),
        )

        self.assertFalse(record.matches(scope, now=NOW))

    def test_naive_consent_timestamps_are_rejected_and_invalid_constructed_record_fails_closed(self) -> None:
        """Comparing naive and aware timestamps must never authorize access or crash."""
        scope = SecretAccessScope(
            operation=SecretOperation.GET,
            secret_reference="qq-token",
            secret_backend="memory",
            source_id="qq",
            account_id="account-1",
            client_version="9.9.0",
            tool_version="1.2.3",
            account_version="account-v1",
            configuration_nonce="configuration-1",
        )
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            ConsentRecord(
                scope_fingerprint=scope.fingerprint(),
                granted=True,
                recorded_at=datetime(2026, 9, 10),
                expires_at=NOW + timedelta(minutes=5),
            )
        invalid = ConsentRecord.model_construct(
            scope_fingerprint=scope.fingerprint(),
            granted=True,
            recorded_at=datetime(2026, 9, 10),
            expires_at=NOW + timedelta(minutes=5),
        )
        self.assertFalse(invalid.matches(scope, now=NOW))
