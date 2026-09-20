"""Scoped read-only TraceMemo adapter; release/device verification is separate."""

from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any, Protocol

from chat_to_howto.domain.consent import ConsentRecord, SecretAccessScope
from chat_to_howto.domain.sources import SourceFingerprint
from chat_to_howto.security.secrets import SecretStore
from chat_to_howto.sources.base import (
    CompletenessEvidence,
    ConversationRef,
    FetchCancelled,
    FetchPage,
    FetchSliceEvidence,
    SourceAdapterError,
    SourceHealth,
    SourceMessage,
    UpstreamProtocolError,
)


_PROTOCOL_ERROR = "Source response did not match the reviewed contract."
_HISTORY_REASON = "TraceMemo chatlog has no HTTP pagination or completeness signal."
_MD5 = re.compile(r"[0-9a-fA-F]{32}")


class _Transport(Protocol):
    def request(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str],
        params: Mapping[str, object] | None = None,
        json: object | None = None,
    ) -> dict[str, Any]: ...


class TraceMemoAdapter:
    def __init__(
        self,
        *,
        transport: _Transport,
        secret_store: SecretStore,
        token_access: SecretAccessScope,
        token_consent: ConsentRecord,
        account_id: str,
        fingerprint: SourceFingerprint,
    ) -> None:
        self._transport = transport
        self._store = secret_store
        self._access = token_access
        self._consent = token_consent
        self._account = account_id
        self._cancelled = False
        self._conversation_ids: frozenset[str] = frozenset()
        if not isinstance(fingerprint, SourceFingerprint):
            raise TypeError("fingerprint must be a SourceFingerprint")
        self.fingerprint = fingerprint
        self.evidence_class = "real_device"

    def health(self) -> SourceHealth:
        data = self._get("/api/v1/health")
        if data.get("ok") is not True or not isinstance(data.get("ready"), bool):
            raise UpstreamProtocolError(_PROTOCOL_ERROR) from None
        self._check_cancelled()
        return SourceHealth(available=True, ready=False)

    def list_accounts(self) -> tuple[str, ...]:
        self._check_cancelled()
        return (self._account,)

    def list_conversations(self) -> list[ConversationRef]:
        # Only a fully parsed, successfully delivered enumeration authorizes IDs.
        self._conversation_ids = frozenset()
        contacts = self._get("/api/v1/contact", {}).get("contacts")
        if not isinstance(contacts, list):
            raise UpstreamProtocolError(_PROTOCOL_ERROR) from None
        result: list[ConversationRef] = []
        identifiers: set[str] = set()
        for item in contacts:
            if not isinstance(item, dict):
                raise UpstreamProtocolError(_PROTOCOL_ERROR) from None
            identifier = _md5(item.get("md5"))
            kind = item.get("type")
            name = item.get("m_nsNickName")
            if (
                identifier is None
                or kind not in ("user", "group")
                or (name is not None and not isinstance(name, str))
            ):
                raise UpstreamProtocolError(_PROTOCOL_ERROR) from None
            if identifier not in identifiers:
                result.append(
                    ConversationRef(
                        "wechat", self._account, identifier, name or identifier, kind
                    )
                )
                identifiers.add(identifier)
        self._check_cancelled()
        self._conversation_ids = frozenset(identifiers)
        return result

    def fetch_range(
        self, conversation_id: str, start_ms: int, end_ms: int
    ) -> FetchPage:
        self._check_cancelled()
        if not _integer(start_ms) or not _integer(end_ms) or end_ms <= start_ms:
            raise ValueError("range must use increasing integer milliseconds") from None
        identifier = _md5(conversation_id)
        if identifier is None or identifier not in self._conversation_ids:
            raise ValueError(
                "TraceMemo conversation must be an enumerated md5"
            ) from None
        data = self._get(
            "/api/v1/chatlog",
            {
                "talker": identifier,
                "startTime": start_ms // 1000,
                "endTime": (end_ms + 999) // 1000,
            },
        )
        raw = data.get("messages")
        if not isinstance(raw, list):
            raise UpstreamProtocolError(_PROTOCOL_ERROR) from None
        declared_count = data.get("count")
        if "count" in data and (
            not _integer(declared_count) or declared_count < 0
        ):
            raise UpstreamProtocolError(_PROTOCOL_ERROR) from None

        result: list[SourceMessage] = []
        seen_ids: set[str] = set()
        accepted_ids: set[str] = set()
        duplicates = unknown_time = missing_id = parse_errors = 0
        for order, item in enumerate(raw):
            if not isinstance(item, dict):
                parse_errors += 1
                continue
            message_id = _identifier(item.get("id"))
            value = item.get("createTime", item.get("datetime"))
            # The reviewed response contract is Unix seconds. Do not guess
            # milliseconds from digit count or manufacture subsecond precision.
            stamp = value * 1000 if _integer(value) else None
            unknown_time += stamp is None
            missing_id += message_id is None
            if message_id is not None:
                if message_id in seen_ids:
                    duplicates += 1
                seen_ids.add(message_id)
            content = item.get("content")
            if content is not None and not isinstance(content, str):
                parse_errors += 1
                continue
            if message_id is not None and message_id in accepted_ids:
                continue
            if stamp is not None and start_ms <= stamp < end_ms:
                if message_id is not None:
                    accepted_ids.add(message_id)
                result.append(
                    SourceMessage(message_id, identifier, stamp, content, order)
                )

        warnings = ["tracememo_history_unproven"]
        # The declaration is a consistency check, never measured coverage.
        if declared_count is not None and declared_count != len(raw):
            warnings.append("upstream_count_mismatch")
        uncertain_boundary = start_ms % 1000 or end_ms % 1000
        if uncertain_boundary:
            warnings.append("second_precision_boundaries")
        for count, warning in (
            (duplicates, "duplicate_ids"),
            (unknown_time, "unknown_timestamps"),
            (missing_id, "missing_identifiers"),
            (parse_errors, "malformed_records"),
        ):
            if count:
                warnings.append(warning)
        reason = _HISTORY_REASON
        if uncertain_boundary:
            reason += (
                " Second-resolution timestamps cannot establish exact"
                " millisecond boundaries."
            )
        stamps = [
            message.sent_at_ms for message in result if message.sent_at_ms is not None
        ]
        evidence = CompletenessEvidence(
            status="unknown",
            reason=reason,
            requested_start_ms=start_ms,
            requested_end_ms=end_ms,
            observed_start_ms=min(stamps, default=None),
            observed_end_ms=max(stamps, default=None),
            slices=(
                FetchSliceEvidence(
                    page=None,
                    cursor=None,
                    requested=None,
                    returned=len(raw),
                    accepted=len(result),
                    has_next=None,
                ),
            ),
            fetched_count=len(raw),
            accepted_count=len(result),
            duplicate_count=duplicates,
            unknown_time_count=unknown_time,
            missing_id_count=missing_id,
            parse_error_count=parse_errors,
            warnings=tuple(warnings),
            termination="no_upstream_pagination_or_completeness",
        )
        self._check_cancelled()
        return FetchPage(tuple(result), None, evidence)

    def cancel(self) -> None:
        self._cancelled = True

    def _check_cancelled(self) -> None:
        if self._cancelled:
            raise FetchCancelled("Source fetch cancelled locally.") from None

    def _get(
        self, path: str, params: Mapping[str, object] | None = None
    ) -> dict[str, Any]:
        self._check_cancelled()
        try:
            headers: dict[str, str] = {}
            if path != "/api/v1/health":
                token = self._store.get(access=self._access, consent=self._consent)
                headers = {"Authorization": "Bearer " + token}
        except Exception:
            raise SourceAdapterError("Source request failed.") from None
        self._check_cancelled()
        try:
            response = self._transport.request(
                "GET", path, headers=headers, params=params
            )
        except Exception:
            raise SourceAdapterError("Source request failed.") from None
        self._check_cancelled()
        if not isinstance(response, dict):
            raise UpstreamProtocolError(_PROTOCOL_ERROR) from None
        return response


def _md5(value: object) -> str | None:
    """Require exactly 32 hex digits; enumerate and send lowercase, without trimming."""
    if isinstance(value, str) and _MD5.fullmatch(value):
        return value.lower()
    return None


def _integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _identifier(value: object) -> str | None:
    if _integer(value):
        return str(value)
    if isinstance(value, str) and value.strip():
        return value
    return None
