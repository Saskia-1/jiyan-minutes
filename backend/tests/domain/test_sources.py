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


def verification_scope(fingerprint: SourceFingerprint, configuration_nonce: str) -> ConsentScope:
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
        configuration_nonce=configuration_nonce,
    )


def approved_export_consent(
    fingerprint: SourceFingerprint, configuration_nonce: str
) -> tuple[ConsentScope, ConsentRecord]:
    scope = verification_scope(fingerprint, configuration_nonce)
    return scope, ConsentRecord.grant(scope, expires_at=NOW + timedelta(minutes=5), now=NOW)


class SourceCapabilityTests(unittest.TestCase):
    def test_document_review_cannot_set_ready(self) -> None:
        """Allowing a document review to mark a source ready would be unsafe."""
        capability = SourceCapability(source_id="qq").mark_document_reviewed()

        self.assertEqual(capability.status, SourceStatus.DOCUMENTED_NOT_TESTED)
        self.assertFalse(hasattr(capability, "transition_to"))

    def test_changed_client_tool_or_account_version_invalidates_a_ready_verification(self) -> None:
        """Keeping ready after a client, tool, or account change would be stale."""
        fingerprint = SourceFingerprint(
            client_version="9.9.0", tool_version="1.2.3", account_version="account-v1"
        )
        configuring = SourceCapability(source_id="wechat").start_configuration()
        scope, consent = approved_export_consent(fingerprint, configuring.configuration_nonce)
        capability = configuring.record_verification(
            fingerprint,
            current_fingerprint=fingerprint,
            export_scope=scope,
            export_consent=consent,
            evidence_class="real_device",
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
        capability = SourceCapability(source_id="wechat").start_configuration()
        scope, consent = approved_export_consent(fingerprint, capability.configuration_nonce)

        with self.assertRaisesRegex(ValueError, "current fingerprint"):
            capability.record_verification(
                fingerprint,
                current_fingerprint=fingerprint.model_copy(update={"tool_version": "1.2.4"}),
                export_scope=scope,
                export_consent=consent,
                evidence_class="real_device",
            )
        declined = ConsentRecord.decline(scope, expires_at=NOW + timedelta(minutes=5), now=NOW)
        with self.assertRaisesRegex(ValueError, "approved export consent"):
            capability.record_verification(
                fingerprint,
                current_fingerprint=fingerprint,
                export_scope=scope,
                export_consent=declined,
                evidence_class="real_device",
            )

    def test_blocked_or_unsupported_source_cannot_be_promoted_to_ready(self) -> None:
        """Later verification must not override an explicit safety stop."""
        fingerprint = SourceFingerprint(
            client_version="9.9.0", tool_version="1.2.3", account_version="account-v1"
        )
        configured = SourceCapability(source_id="wechat").start_configuration()
        scope, consent = approved_export_consent(fingerprint, configured.configuration_nonce)
        states = (
            configured.evaluate_requirements(
                user_approved=False, requirements_safe=True
            ),
            configured.evaluate_requirements(
                user_approved=True, requirements_safe=False
            ),
            configured.mark_unsupported(),
        )

        for capability in states:
            with self.subTest(status=capability.status):
                with self.assertRaisesRegex(ValueError, "cannot be verified"):
                    capability.record_verification(
                        fingerprint,
                        current_fingerprint=fingerprint,
                        export_scope=scope,
                        export_consent=consent,
                        evidence_class="real_device",
                    )

    def test_reconfiguration_invalidates_old_consent_after_blocked_or_unsupported(self) -> None:
        """A terminal safety state needs a fresh configuration session before ready."""
        fingerprint = SourceFingerprint(
            client_version="9.9.0", tool_version="1.2.3", account_version="account-v1"
        )
        configured = SourceCapability(source_id="wechat").start_configuration()
        old_scope, old_consent = approved_export_consent(
            fingerprint, configured.configuration_nonce
        )
        terminal_states = (
            configured.evaluate_requirements(user_approved=False, requirements_safe=True),
            configured.mark_unsupported(),
        )

        for terminal in terminal_states:
            with self.subTest(status=terminal.status):
                reconfigured = terminal.start_configuration()
                self.assertNotEqual(reconfigured.configuration_nonce, configured.configuration_nonce)
                with self.assertRaisesRegex(ValueError, "approved export consent"):
                    reconfigured.record_verification(
                        fingerprint,
                        current_fingerprint=fingerprint,
                        export_scope=old_scope,
                        export_consent=old_consent,
                        evidence_class="real_device",
                    )
                new_scope, new_consent = approved_export_consent(
                    fingerprint, reconfigured.configuration_nonce
                )
                ready = reconfigured.record_verification(
                    fingerprint,
                    current_fingerprint=fingerprint,
                    export_scope=new_scope,
                    export_consent=new_consent,
                    evidence_class="real_device",
                )
                self.assertEqual(ready.status, SourceStatus.READY)

    def test_mark_unsupported_rejects_a_blocked_source_but_accepts_unconfigured(self) -> None:
        """Changing one terminal state into another must require reconfiguration first."""
        blocked = SourceCapability(source_id="qq").evaluate_requirements(
            user_approved=False, requirements_safe=True
        )

        with self.assertRaisesRegex(ValueError, "fresh configuration session"):
            blocked.mark_unsupported()
        self.assertEqual(
            SourceCapability(source_id="qq").mark_unsupported().status,
            SourceStatus.UNSUPPORTED,
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
