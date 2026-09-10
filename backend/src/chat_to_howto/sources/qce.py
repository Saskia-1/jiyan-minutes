"""Locked QCE v6.2.10 read-only adapter."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Protocol, TypeGuard

from chat_to_howto.domain.consent import ConsentRecord, SecretAccessScope
from chat_to_howto.security.secrets import SecretStore
from chat_to_howto.sources.base import (
    CompletenessEvidence,
    ConversationRef,
    FetchCancelled,
    FetchPage,
    FetchSliceEvidence,
    PaginationError,
    SourceAdapterError,
    SourceHealth,
    SourceMessage,
    UpstreamProtocolError,
)

_PROTOCOL_ERROR = "Source response did not match the locked contract."
_PAGINATION_ERROR = "Source pagination could not be safely continued."
_HISTORY_REASON = "QCE lazy fetch, cache, and gap repair do not prove complete history."
_PAGE_LIMIT = 2000
# P0 narrow-range guardrails count raw records before parsing/deduplication.
# Exhaustion of either budget raises without returning a partial success.
# Reserve the whole next page before requesting: changing a collection's limit
# mid-pagination would change upstream offsets. This can stop below the ceiling.
_MAX_MESSAGE_PAGES = 20
_MAX_MESSAGE_ITEMS = 20_000
_MAX_ENUMERATION_PAGES = 10
_MAX_ENUMERATION_ITEMS = 10_000
_BUDGET_ERROR = "Source reading exceeded the safe local budget."


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


class QceAdapter:
    def __init__(
        self,
        *,
        transport: _Transport,
        secret_store: SecretStore,
        token_access: SecretAccessScope,
        token_consent: ConsentRecord,
        account_id: str,
    ) -> None:
        self._transport = transport
        self._store = secret_store
        self._access = token_access
        self._consent = token_consent
        self._account = account_id
        self._cancelled = False

    def health(self) -> SourceHealth:
        data = self._call(
            lambda: self._transport.request("GET", "/health", headers={})
        )
        if (
            data.get("status") != "healthy"
            or not isinstance(data.get("mode"), str)
            or not data["mode"]
            or not isinstance(data.get("online"), bool)
        ):
            raise UpstreamProtocolError(_PROTOCOL_ERROR)
        return SourceHealth(available=True, ready=False)

    def list_accounts(self) -> tuple[str, ...]:
        return (self._account,)

    def list_conversations(self) -> list[ConversationRef]:
        result: list[ConversationRef] = []
        remaining = _MAX_ENUMERATION_ITEMS
        for path, key, field, kind, label in (
            ("/api/friends", "friends", "uid", "friend", "nick"),
            ("/api/groups", "groups", "groupCode", "group", "groupName"),
        ):
            values = self._page(path, key, remaining)
            remaining -= len(values)
            for item in values:
                result.append(self._conversation(item, field, kind, label))
        if remaining <= 0:
            raise PaginationError(_BUDGET_ERROR)
        recent_limit = min(100, remaining)
        contacts = self._get(
            "/api/recent-contacts", {"limit": recent_limit, "includeAll": True}
        ).get("contacts")
        if not isinstance(contacts, list):
            raise UpstreamProtocolError(_PROTOCOL_ERROR)
        if len(contacts) > recent_limit:
            raise PaginationError(_BUDGET_ERROR)
        for item in contacts:
            if not isinstance(item, dict):
                raise UpstreamProtocolError(_PROTOCOL_ERROR)
            chat_type = item.get("chatType")
            if not _integer(chat_type) or chat_type not in (1, 2):
                raise UpstreamProtocolError(_PROTOCOL_ERROR)
            kind = "group" if chat_type == 2 else "friend"
            result.append(self._conversation(item, "peerUid", kind, "peerName"))
        unique: dict[str, ConversationRef] = {}
        for conversation in result:
            unique.setdefault(conversation.conversation_id, conversation)
        self._check_cancelled()
        return list(unique.values())

    def fetch_range(
        self, conversation_id: str, start_ms: int, end_ms: int
    ) -> FetchPage:
        if not _integer(start_ms) or not _integer(end_ms) or end_ms <= start_ms:
            raise ValueError("range must use increasing integer milliseconds")
        peer_type, peer_uid = _peer(conversation_id)
        messages: list[SourceMessage] = []
        slices: list[FetchSliceEvidence] = []
        message_ids: set[str] = set()
        accepted_ids: set[str] = set()
        fetched = duplicates = unknown_time = missing_id = parse_errors = 0
        page = 1
        while True:
            requested = _PAGE_LIMIT
            if fetched + requested > _MAX_MESSAGE_ITEMS:
                raise PaginationError(_BUDGET_ERROR)
            data = self._post(
                "/api/messages/fetch",
                {
                    "peer": {"chatType": peer_type, "peerUid": peer_uid},
                    "filter": {"startTime": start_ms, "endTime": end_ms - 1},
                    "batchSize": 5000,
                    "page": page,
                    "limit": requested,
                    "forceRefresh": False,
                },
            )
            raw, has_next = _page_values(data, "messages", page, requested)
            accepted_before = len(messages)
            for item in raw:
                order = fetched
                fetched += 1
                if not isinstance(item, dict):
                    parse_errors += 1
                    continue
                identifier = _identifier(item.get("msgId"))
                stamp = _timestamp(item.get("msgTime"))
                missing_id += identifier is None
                unknown_time += stamp is None
                if identifier is not None:
                    if identifier in message_ids:
                        duplicates += 1
                    message_ids.add(identifier)
                try:
                    text = _message_text(item)
                except UpstreamProtocolError:
                    parse_errors += 1
                    continue
                if identifier is not None and identifier in accepted_ids:
                    continue
                if stamp is not None and start_ms <= stamp < end_ms:
                    if identifier is not None:
                        accepted_ids.add(identifier)
                    messages.append(
                        SourceMessage(identifier, conversation_id, stamp, text, order)
                    )
            slices.append(
                FetchSliceEvidence(
                    page=page,
                    cursor=None,
                    requested=requested,
                    returned=len(raw),
                    accepted=len(messages) - accepted_before,
                    has_next=has_next,
                )
            )
            if not has_next:
                break
            if page >= _MAX_MESSAGE_PAGES or fetched >= _MAX_MESSAGE_ITEMS:
                raise PaginationError(_BUDGET_ERROR)
            page += 1

        warnings = ["qce_history_unproven"]
        for count, warning in (
            (duplicates, "duplicate_ids"),
            (unknown_time, "unknown_timestamps"),
            (missing_id, "missing_identifiers"),
            (parse_errors, "malformed_records"),
        ):
            if count:
                warnings.append(warning)
        reason = _HISTORY_REASON
        if missing_id:
            reason += " At least one QCE message lacked a stable identifier."
        if unknown_time:
            reason += " At least one QCE message lacked a usable timestamp."
        if parse_errors:
            reason += " At least one QCE message could not be parsed."
        stamps = [
            message.sent_at_ms
            for message in messages
            if message.sent_at_ms is not None
        ]
        evidence = CompletenessEvidence(
            status="partial",
            reason=reason,
            requested_start_ms=start_ms,
            requested_end_ms=end_ms,
            observed_start_ms=min(stamps, default=None),
            observed_end_ms=max(stamps, default=None),
            slices=tuple(slices),
            fetched_count=fetched,
            accepted_count=len(messages),
            duplicate_count=duplicates,
            unknown_time_count=unknown_time,
            missing_id_count=missing_id,
            parse_error_count=parse_errors,
            warnings=tuple(warnings),
            termination="upstream_exhausted",
        )
        self._check_cancelled()
        return FetchPage(tuple(messages), None, evidence)

    def cancel(self) -> None:
        self._cancelled = True

    def _check_cancelled(self) -> None:
        if self._cancelled:
            raise FetchCancelled("Source fetch cancelled locally.")

    def _headers(self) -> dict[str, str]:
        self._check_cancelled()
        try:
            token = self._store.get(access=self._access, consent=self._consent)
            headers = {"Authorization": "Bearer " + token}
        except Exception:
            raise SourceAdapterError("Source request failed.") from None
        self._check_cancelled()
        return headers

    def _call(self, request: Callable[[], object]) -> dict[str, Any]:
        self._check_cancelled()
        try:
            response = request()
        except Exception:
            # Dependency errors, including already-typed errors, are untrusted.
            raise SourceAdapterError("Source request failed.") from None
        self._check_cancelled()
        return _data(response)

    def _get(self, path: str, params: Mapping[str, object]) -> dict[str, Any]:
        headers = self._headers()
        return self._call(
            lambda: self._transport.request(
                "GET", path, headers=headers, params=params
            )
        )

    def _post(self, path: str, body: object) -> dict[str, Any]:
        headers = self._headers()
        return self._call(
            lambda: self._transport.request(
                "POST", path, headers=headers, json=body
            )
        )

    def _page(
        self, path: str, key: str, remaining: int
    ) -> list[dict[str, Any]]:
        page = 1
        requested = min(_PAGE_LIMIT, remaining)
        result: list[dict[str, Any]] = []
        while True:
            if requested <= 0 or len(result) + requested > remaining:
                raise PaginationError(_BUDGET_ERROR)
            data = self._get(
                path, {"page": page, "limit": requested, "forceRefresh": False}
            )
            values, has_next = _page_values(data, key, page, requested)
            for value in values:
                if not isinstance(value, dict):
                    raise UpstreamProtocolError(_PROTOCOL_ERROR)
                result.append(value)
            if not has_next:
                return result
            if page >= _MAX_ENUMERATION_PAGES or len(result) >= remaining:
                raise PaginationError(_BUDGET_ERROR)
            page += 1

    def _conversation(
        self, item: Mapping[str, Any], field: str, kind: str, label: str
    ) -> ConversationRef:
        value = _identifier(item.get(field))
        if value is None:
            raise UpstreamProtocolError(_PROTOCOL_ERROR)
        name = item.get(label)
        if name is not None and not isinstance(name, str):
            raise UpstreamProtocolError(_PROTOCOL_ERROR)
        return ConversationRef(
            "qq", self._account, f"{kind}:{value}", name or value, kind
        )


def _data(response: object) -> dict[str, Any]:
    if not isinstance(response, dict):
        raise UpstreamProtocolError(_PROTOCOL_ERROR)
    data = response.get("data")
    if response.get("success") is not True or not isinstance(data, dict):
        raise UpstreamProtocolError(_PROTOCOL_ERROR)
    return data


def _integer(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _identifier(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value
    if _integer(value):
        return str(value)
    return None


def _timestamp(value: object) -> int | None:
    if not _integer(value):
        return None
    return value * 1000 if value < 100_000_000_000 else value


def _page_values(
    data: Mapping[str, Any], key: str, page: int, requested: int
) -> tuple[list[Any], bool]:
    current = data.get("currentPage")
    has_next = data.get("hasNext")
    if not _integer(current) or current != page or not isinstance(has_next, bool):
        raise PaginationError(_PAGINATION_ERROR)
    values = data.get(key)
    if not isinstance(values, list):
        raise UpstreamProtocolError(_PROTOCOL_ERROR)
    if len(values) > requested:
        raise PaginationError(_BUDGET_ERROR)
    return values, has_next


def _peer(value: str) -> tuple[int, str]:
    if not isinstance(value, str):
        raise ValueError("QCE conversation id must include a peer kind")
    kind, separator, uid = value.partition(":")
    if not separator or not uid.strip():
        raise ValueError("QCE conversation id must include a peer kind")
    if kind == "friend":
        return 1, uid
    if kind == "group":
        return 2, uid
    raise ValueError("QCE conversation id has an unsupported peer kind")


def _message_text(item: Mapping[str, Any]) -> str | None:
    elements = item.get("elements", [])
    if not isinstance(elements, list):
        raise UpstreamProtocolError(_PROTOCOL_ERROR)
    parts: list[str] = []
    for element in elements:
        if not isinstance(element, dict):
            raise UpstreamProtocolError(_PROTOCOL_ERROR)
        text = element.get("textElement")
        if text is None:
            continue
        if not isinstance(text, dict) or not isinstance(text.get("content"), str):
            raise UpstreamProtocolError(_PROTOCOL_ERROR)
        parts.append(text["content"])
    return "".join(parts) or None
