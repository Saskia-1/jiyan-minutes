"""Tests for narrowly-scoped consent records."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from chat_to_howto.domain.consent import ConsentRecord, ConsentScope


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
        )
        changed_scope = scope.model_copy(update={"conversation_id": "conversation-2"})
        record = ConsentRecord.grant(scope)

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
        )

        self.assertFalse(ConsentRecord.decline(scope).matches(scope))
