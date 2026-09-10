"""Safe, explicit source readiness transitions."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from chat_to_howto.domain.consent import ConsentRecord, ConsentScope


class SourceStatus(str, Enum):
    UNCONFIGURED = "unconfigured"
    NEEDS_USER_ACTION = "needs_user_action"
    DOCUMENTED_NOT_TESTED = "documented_not_tested"
    READY = "ready"
    BLOCKED = "blocked"
    UNSUPPORTED = "unsupported"
    ERROR = "error"


class SourceFingerprint(BaseModel):
    """The exact versions for which a source verification was performed."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    client_version: str
    tool_version: str
    account_version: str


class SourceCapability(BaseModel):
    """Independent readiness state for one source, never inferred from docs."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str
    status: SourceStatus = SourceStatus.UNCONFIGURED
    document_reviewed: bool = False
    verified_fingerprint: SourceFingerprint | None = Field(default=None, repr=False)
    blocked_reason: str | None = None
    fallback_source_id: None = None

    @model_validator(mode="after")
    def _validate_evidence_boundaries(self) -> "SourceCapability":
        if self.status is SourceStatus.READY and self.verified_fingerprint is None:
            raise ValueError("ready status requires a verified fingerprint")
        if self.status is SourceStatus.BLOCKED and self.blocked_reason is None:
            raise ValueError("blocked status requires a reason")
        if self.status is not SourceStatus.BLOCKED and self.blocked_reason is not None:
            raise ValueError("only blocked status may carry a blocked reason")
        return self

    def mark_document_reviewed(self) -> "SourceCapability":
        """Record documentation only; it is never operational verification."""
        return self.model_copy(
            update={
                "document_reviewed": True,
                "status": SourceStatus.DOCUMENTED_NOT_TESTED,
                "verified_fingerprint": None,
                "blocked_reason": None,
            }
        )

    def transition_to(self, status: SourceStatus) -> "SourceCapability":
        """Permit non-ready informational transitions without inventing evidence."""
        if status is SourceStatus.READY:
            raise ValueError("ready status requires a verified fingerprint")
        if status is SourceStatus.BLOCKED:
            raise ValueError("blocked status requires explicit requirements evaluation")
        return self.model_copy(
            update={"status": status, "verified_fingerprint": None, "blocked_reason": None}
        )

    def record_verification(
        self,
        fingerprint: SourceFingerprint,
        *,
        current_fingerprint: SourceFingerprint,
        export_scope: ConsentScope,
        export_consent: ConsentRecord,
    ) -> "SourceCapability":
        """Only a successful, versioned verification can make a source ready."""
        if self.status in {SourceStatus.BLOCKED, SourceStatus.UNSUPPORTED}:
            raise ValueError("blocked or unsupported source cannot be verified")
        if fingerprint != current_fingerprint:
            raise ValueError("verification fingerprint must match the current fingerprint")
        if (
            export_scope.operation != "export_verification"
            or export_scope.source_id != self.source_id
            or export_scope.client_version != fingerprint.client_version
            or export_scope.tool_version != fingerprint.tool_version
            or export_scope.account_version != fingerprint.account_version
            or not export_consent.matches(export_scope)
        ):
            raise ValueError("ready status requires approved export consent")
        return self.model_copy(
            update={
                "status": SourceStatus.READY,
                "verified_fingerprint": fingerprint,
                "blocked_reason": None,
            }
        )

    def with_current_versions(self, fingerprint: SourceFingerprint) -> "SourceCapability":
        """Invalidate an old verification instead of extending it to new versions."""
        if self.verified_fingerprint is None or self.verified_fingerprint == fingerprint:
            return self
        return self.model_copy(
            update={
                "status": SourceStatus.NEEDS_USER_ACTION,
                "verified_fingerprint": None,
                "blocked_reason": None,
            }
        )

    def evaluate_requirements(
        self, *, user_approved: bool, requirements_safe: bool
    ) -> "SourceCapability":
        """Block on a declined or unsafe prerequisite; do not choose an alternative."""
        if not user_approved:
            reason = "user_declined"
        elif not requirements_safe:
            reason = "unsafe_requirement"
        else:
            return self
        return self.model_copy(
            update={
                "status": SourceStatus.BLOCKED,
                "verified_fingerprint": None,
                "blocked_reason": reason,
                "fallback_source_id": None,
            }
        )
