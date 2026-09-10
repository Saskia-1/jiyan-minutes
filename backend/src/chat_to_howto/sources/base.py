"""Small normalized contracts for read-only source adapters."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Literal, Protocol

class SourceAdapterError(RuntimeError): pass
class FetchCancelled(SourceAdapterError): pass
class PaginationError(SourceAdapterError): pass
class UpstreamProtocolError(SourceAdapterError): pass
@dataclass(frozen=True, slots=True)
class SourceHealth: available: bool; ready: bool
@dataclass(frozen=True, slots=True)
class ConversationRef: source_id: str; account_id: str; conversation_id: str; display_name: str; conversation_type: str
@dataclass(frozen=True, slots=True)
class SourceMessage: message_id: str | None; conversation_id: str; sent_at_ms: int | None; text: str | None; source_order: int
@dataclass(frozen=True, slots=True)
class CompletenessEvidence: status: Literal["complete", "partial", "unknown", "empty"]; reason: str
@dataclass(frozen=True, slots=True)
class FetchPage: messages: tuple[SourceMessage, ...]; next_cursor: str | None; completeness: CompletenessEvidence
class SourceAdapter(Protocol):
    def health(self) -> SourceHealth: ...
    def list_accounts(self) -> tuple[str, ...]: ...
    def list_conversations(self) -> list[ConversationRef]: ...
    def fetch_range(self, conversation_id: str, start_ms: int, end_ms: int) -> FetchPage: ...
    def cancel(self) -> None: ...
