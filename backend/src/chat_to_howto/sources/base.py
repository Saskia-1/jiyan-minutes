"""Normalized, immutable contracts for read-only source adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol


class SourceAdapterError(RuntimeError):
    """Public, body-free source failure."""


class FetchCancelled(SourceAdapterError):
    """The caller cancelled local source work."""


class PaginationError(SourceAdapterError):
    """Pagination cannot safely continue."""


class UpstreamProtocolError(SourceAdapterError):
    """The response does not match the locked source contract."""


@dataclass(frozen=True, slots=True)
class SourceHealth:
    available: bool
    ready: bool


@dataclass(frozen=True, slots=True)
class ConversationRef:
    source_id: str
    account_id: str
    conversation_id: str
    display_name: str
    conversation_type: str


@dataclass(frozen=True, slots=True)
class SourceMessage:
    message_id: str | None
    conversation_id: str
    sent_at_ms: int | None
    text: str | None
    source_order: int


@dataclass(frozen=True, slots=True)
class FetchSliceEvidence:
    """One response: requested limit, raw returned count, and accepted count.

    Page/cursor are the values sent, not an inferred next request. None means
    the upstream contract has no corresponding field or limit.
    """

    page: int | None
    cursor: str | None
    requested: int | None
    returned: int
    accepted: int
    has_next: bool | None


@dataclass(frozen=True, slots=True)
class CompletenessEvidence:
    """Auditable fetch evidence; absent measurements are unknown, never zero.

    Requested bounds use [start, end). Observed bounds are the minimum and
    maximum accepted message timestamps (both inclusive), or None if empty.
    Fetched counts include all raw entries, including malformed ones. Duplicate
    count measures repeated usable IDs among all received records; a rejected
    occurrence does not prevent accepting a later valid occurrence. Error
    counters may overlap: a record can lack both time and ID.
    Warnings are fixed codes and must contain no upstream body data.
    """

    status: Literal["complete", "partial", "unknown", "empty"]
    reason: str
    duplicate_count: int | None = None
    requested_start_ms: int | None = None
    requested_end_ms: int | None = None
    observed_start_ms: int | None = None
    observed_end_ms: int | None = None
    slices: tuple[FetchSliceEvidence, ...] = ()
    fetched_count: int | None = None
    accepted_count: int | None = None
    unknown_time_count: int | None = None
    missing_id_count: int | None = None
    parse_error_count: int | None = None
    warnings: tuple[str, ...] = ()
    termination: str | None = None


@dataclass(frozen=True, slots=True)
class FetchPage:
    messages: tuple[SourceMessage, ...]
    next_cursor: str | None
    completeness: CompletenessEvidence


class SourceAdapter(Protocol):
    def health(self) -> SourceHealth: ...

    def list_accounts(self) -> tuple[str, ...]: ...

    def list_conversations(self) -> list[ConversationRef]: ...

    def fetch_range(
        self, conversation_id: str, start_ms: int, end_ms: int
    ) -> FetchPage: ...

    def cancel(self) -> None: ...
