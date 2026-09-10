"""State-boundary tests for independent chat sources."""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from chat_to_howto.domain.sources import (
    SourceCapability,
    SourceFingerprint,
    SourceStatus,
)
from chat_to_howto.domain.consent import ConsentRecord, ConsentScope


NOW = datetime.now(timezone.utc)


def verification_scope(fingerprint: SourceFingerprint) -> ConsentScope:
    return ConsentScope(
        source_id="wechat",
        account_id="account-1",
        conversation_id="conversation-1",
        range_start="2026-09-01T00:00:00+08:00",
        range_end="2026-09-01T01:00:00+08:00",
        output_path="/private/tmp/verification.json",
        operation="export_verification",
        client_version=fingerprint.client_version,
        tool_version=fingerprint.tool_version,
        account_version=fingerprint.account_version,
    )


def approved_export_consent(fingerprint: SourceFingerprint) -> tuple[ConsentScope, ConsentRecord]:
    scope = verification_scope(fingerprint)
    return scope, ConsentRecord.grant(scope, expires_at=NOW + timedelta(minutes=5), now=NOW)


class SourceCapabilityTests(unittest.TestCase):
    def test_document_review_cannot_set_ready(self) -> None:
        """Allowing a document review to mark a source ready would be unsafe."""
        capability = SourceCapability(source_id="qq").mark_document_reviewed()

        self.assertEqual(capability.status, SourceStatus.DOCUMENTED_NOT_TESTED)
        with self.assertRaisesRegex(ValueError, "verified fingerprint"):
            capability.transition_to(SourceStatus.READY)

    def test_changed_client_tool_or_account_version_invalidates_a_ready_verification(self) -> None:
        """Keeping ready after a client, tool, or account change would be stale."""
        fingerprint = SourceFingerprint(
            client_version="9.9.0", tool_version="1.2.3", account_version="account-v1"
        )
        scope, consent = approved_export_consent(fingerprint)
        capability = SourceCapability(source_id="wechat").record_verification(
            fingerprint,
            current_fingerprint=fingerprint,
            export_scope=scope,
            export_consent=consent,
        )

        changed_versions = (
            fingerprint.model_copy(update={"client_version": "9.9.1"}),
            fingerprint.model_copy(update={"tool_version": "1.2.4"}),
            fingerprint.model_copy(update={"account_version": "account-v2"}),
        )

        for current in changed_versions:
            with self.subTest(current=current):
                invalidated = capability.with_current_versions(current)
                self.assertEqual(invalidated.status, SourceStatus.NEEDS_USER_ACTION)
                self.assertIsNone(invalidated.verified_fingerprint)

    def test_verification_requires_current_fingerprint_and_approved_real_export_scope(self) -> None:
        """Stale or unapproved evidence cannot make a source ready."""
        fingerprint = SourceFingerprint(
            client_version="9.9.0", tool_version="1.2.3", account_version="account-v1"
        )
        scope, consent = approved_export_consent(fingerprint)
        capability = SourceCapability(source_id="wechat")

        with self.assertRaisesRegex(ValueError, "current fingerprint"):
            capability.record_verification(
                fingerprint,
                current_fingerprint=fingerprint.model_copy(update={"tool_version": "1.2.4"}),
                export_scope=scope,
                export_consent=consent,
            )
        declined = ConsentRecord.decline(scope, expires_at=NOW + timedelta(minutes=5), now=NOW)
        with self.assertRaisesRegex(ValueError, "approved export consent"):
            capability.record_verification(
                fingerprint,
                current_fingerprint=fingerprint,
                export_scope=scope,
                export_consent=declined,
            )

    def test_blocked_or_unsupported_source_cannot_be_promoted_to_ready(self) -> None:
        """Later verification must not override an explicit safety stop."""
        fingerprint = SourceFingerprint(
            client_version="9.9.0", tool_version="1.2.3", account_version="account-v1"
        )
        scope, consent = approved_export_consent(fingerprint)
        states = (
            SourceCapability(source_id="wechat").evaluate_requirements(
                user_approved=False, requirements_safe=True
            ),
            SourceCapability(source_id="wechat").evaluate_requirements(
                user_approved=True, requirements_safe=False
            ),
            SourceCapability(source_id="wechat").transition_to(SourceStatus.UNSUPPORTED),
        )

        for capability in states:
            with self.subTest(status=capability.status):
                with self.assertRaisesRegex(ValueError, "cannot be verified"):
                    capability.record_verification(
                        fingerprint,
                        current_fingerprint=fingerprint,
                        export_scope=scope,
                        export_consent=consent,
                    )

    def test_declined_requirement_blocks_without_a_fallback(self) -> None:
        """Falling back after a declined operation could bypass user choice."""
        blocked = SourceCapability(source_id="qq").evaluate_requirements(
            user_approved=False,
            requirements_safe=True,
        )

        self.assertEqual(blocked.status, SourceStatus.BLOCKED)
        self.assertEqual(blocked.blocked_reason, "user_declined")
        self.assertIsNone(blocked.fallback_source_id)

    def test_unsafe_requirement_blocks_without_a_fallback(self) -> None:
        """Switching paths after an unsafe requirement would weaken the boundary."""
        blocked = SourceCapability(source_id="wechat").evaluate_requirements(
            user_approved=True,
            requirements_safe=False,
        )

        self.assertEqual(blocked.status, SourceStatus.BLOCKED)
        self.assertEqual(blocked.blocked_reason, "unsafe_requirement")
        self.assertIsNone(blocked.fallback_source_id)
