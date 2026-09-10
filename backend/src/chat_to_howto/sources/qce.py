"""Locked QCE v6.2.10 read-only adapter."""
from __future__ import annotations
from collections.abc import Mapping
from typing import Any, Protocol
from chat_to_howto.domain.consent import ConsentRecord, SecretAccessScope
from chat_to_howto.security.secrets import SecretStore
from chat_to_howto.sources.base import CompletenessEvidence, ConversationRef, FetchCancelled, FetchPage, PaginationError, SourceAdapterError, SourceHealth, SourceMessage, UpstreamProtocolError

class _Transport(Protocol):
    def request(self, method: str, path: str, *, headers: dict[str, str], params: Mapping[str, object] | None = None, json: object | None = None) -> dict[str, Any]: ...

class QceAdapter:
    def __init__(self, *, transport: _Transport, secret_store: SecretStore, token_access: SecretAccessScope, token_consent: ConsentRecord, account_id: str) -> None:
        self._transport, self._store, self._access, self._consent, self._account, self._cancelled = transport, secret_store, token_access, token_consent, account_id, False
    def health(self) -> SourceHealth:
        data = _data(self._transport.request("GET", "/health", headers={}))
        if data.get("status") != "healthy" or not isinstance(data.get("mode"), str) or not isinstance(data.get("online"), bool): raise UpstreamProtocolError("Source response did not match the locked contract.")
        return SourceHealth(True, False)
    def list_accounts(self) -> tuple[str, ...]: return (self._account,)
    def list_conversations(self) -> list[ConversationRef]:
        result: list[ConversationRef] = []
        for path, key, field, kind, label in (("/api/friends", "friends", "uid", "friend", "nick"), ("/api/groups", "groups", "groupCode", "group", "groupName")):
            for item in self._page(path, key):
                if isinstance(item.get(field), (str, int)):
                    value = str(item[field]); result.append(ConversationRef("qq", self._account, f"{kind}:{value}", str(item.get(label) or value), kind))
        contacts = self._get("/api/recent-contacts", {"limit": 100, "includeAll": True}).get("contacts")
        if isinstance(contacts, list):
            for item in contacts:
                if isinstance(item, dict) and isinstance(item.get("peerUid"), (str, int)):
                    kind = "group" if item.get("chatType") == 2 else "friend"; value = str(item["peerUid"])
                    result.append(ConversationRef("qq", self._account, f"{kind}:{value}", str(item.get("peerName") or value), kind))
        seen: set[str] = set(); return [item for item in result if not (item.conversation_id in seen or seen.add(item.conversation_id))]
    def fetch_range(self, conversation_id: str, start_ms: int, end_ms: int) -> FetchPage:
        if any(not isinstance(x, int) or isinstance(x, bool) for x in (start_ms, end_ms)) or end_ms <= start_ms: raise ValueError("range must use increasing integer milliseconds")
        if self._cancelled: raise FetchCancelled("Source fetch cancelled locally.")
        peer_type, peer_uid = _peer(conversation_id); messages: list[SourceMessage] = []; page = 1; seen: set[int] = set(); reason = "QCE lazy fetch, cache, and gap repair do not prove complete history."
        while True:
            if self._cancelled: raise FetchCancelled("Source fetch cancelled locally.")
            data = self._post("/api/messages/fetch", {"peer": {"chatType": peer_type, "peerUid": peer_uid}, "filter": {"startTime": start_ms, "endTime": end_ms - 1}, "batchSize": 5000, "page": page, "limit": 2000, "forceRefresh": False})
            current, raw, has_next = data.get("currentPage"), data.get("messages"), data.get("hasNext")
            if isinstance(current, bool) or not isinstance(current, int) or current != page or current in seen or not isinstance(has_next, bool): raise PaginationError("Source pagination could not be safely continued.")
            if not isinstance(raw, list): raise UpstreamProtocolError("Source response did not match the locked contract.")
            seen.add(current)
            for item in raw:
                if isinstance(item, dict):
                    msg = _message(item, conversation_id, len(messages))
                    if msg.sent_at_ms is None:
                        reason = "At least one QCE message lacked a usable timestamp."
                    else:
                        if msg.message_id is None:
                            reason = "At least one QCE message lacked a stable identifier."
                        if start_ms <= msg.sent_at_ms < end_ms:
                            messages.append(msg)
            if not has_next: break
            if page >= 10000: raise PaginationError("Source pagination exceeded the safe limit.")
            page += 1
        return FetchPage(tuple(messages), None, CompletenessEvidence("partial", reason))
    def cancel(self) -> None: self._cancelled = True
    def _headers(self) -> dict[str, str]: return {"Authorization": "Bearer " + self._store.get(access=self._access, consent=self._consent)}
    def _get(self, path: str, params: Mapping[str, object]) -> dict[str, Any]: return _call(lambda: self._transport.request("GET", path, headers=self._headers(), params=params))
    def _post(self, path: str, body: object) -> dict[str, Any]: return _call(lambda: self._transport.request("POST", path, headers=self._headers(), json=body))
    def _page(self, path: str, key: str) -> list[dict[str, Any]]:
        data = self._get(path, {"page": 1, "limit": 2000, "forceRefresh": False}); values = data.get(key)
        if not isinstance(values, list) or data.get("hasNext") is not False: raise UpstreamProtocolError("Source response did not match the locked contract.")
        return [value for value in values if isinstance(value, dict)]

def _call(call: Any) -> dict[str, Any]:
    try: return _data(call())
    except SourceAdapterError: raise
    except Exception: raise SourceAdapterError("Source request failed.") from None
def _data(response: Mapping[str, Any]) -> dict[str, Any]:
    data = response.get("data")
    if response.get("success") is not True or not isinstance(data, dict): raise UpstreamProtocolError("Source response did not match the locked contract.")
    return data
def _peer(value: str) -> tuple[int, str]:
    kind, separator, uid = value.partition(":")
    if not separator or not uid: raise ValueError("QCE conversation id must include a peer kind")
    if kind == "friend": return 1, uid
    if kind == "group": return 2, uid
    raise ValueError("QCE conversation id has an unsupported peer kind")
def _message(item: Mapping[str, Any], conversation: str, order: int) -> SourceMessage:
    stamp = item.get("msgTime") if isinstance(item.get("msgTime"), int) and not isinstance(item.get("msgTime"), bool) else None
    if stamp is not None and stamp < 100000000000: stamp *= 1000
    identifier = item.get("msgId"); elements = item.get("elements", [])
    text = "".join(part["textElement"]["content"] for part in elements if isinstance(part, dict) and isinstance(part.get("textElement"), dict) and isinstance(part["textElement"].get("content"), str)) if isinstance(elements, list) else ""
    return SourceMessage(str(identifier) if isinstance(identifier, (str, int)) else None, conversation, stamp, text or None, order)
