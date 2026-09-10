"""Bounded HTTP client for one literal loopback target."""
from __future__ import annotations
import ipaddress, json as json_module
from collections.abc import Callable, Mapping
from typing import Any
import httpx

class TransportError(RuntimeError): pass
class UnsafeLoopbackTarget(TransportError): pass
class RedirectRejected(TransportError): pass
class ResponseTooLarge(TransportError): pass
class UpstreamHttpError(TransportError): pass
class MalformedResponse(TransportError): pass

class LoopbackTransport:
    def __init__(self, base_url: str, *, expected_port: int, resolver: Callable[[str], tuple[str, ...]] | None = None, client: httpx.Client | None = None, timeout_seconds: float = 5, max_body_bytes: int = 1_048_576) -> None:
        if timeout_seconds <= 0 or max_body_bytes <= 0: raise ValueError("transport limits must be positive")
        try: url = httpx.URL(base_url)
        except httpx.InvalidURL: raise UnsafeLoopbackTarget("Configured source target is unsafe.") from None
        if url.scheme != "http" or url.host not in {"127.0.0.1", "::1"} or url.port != expected_port or url.userinfo or url.query or url.fragment or url.path not in ("", "/"):
            raise UnsafeLoopbackTarget("Configured source target is unsafe.")
        if resolver is not None:
            try: answers = resolver(url.host)
            except Exception: raise UnsafeLoopbackTarget("Configured source target is unsafe.") from None
            try: safe = bool(answers) and all(ipaddress.ip_address(item).is_loopback for item in answers)
            except ValueError: safe = False
            if not safe: raise UnsafeLoopbackTarget("Configured source target is unsafe.")
        self._url, self._cap = url, max_body_bytes
        self._client = client or httpx.Client(follow_redirects=False, trust_env=False, timeout=httpx.Timeout(timeout_seconds))
    def __repr__(self) -> str: return "<LoopbackTransport>"
    def request(self, method: str, path: str, *, headers: Mapping[str, str] | None = None, params: Mapping[str, object] | None = None, json: object | None = None) -> dict[str, Any]:
        if not path.startswith("/") or path.startswith("//") or "?" in path or "#" in path or (params and any(str(key).lower() in {"token", "access_token", "authorization"} for key in params)):
            raise UnsafeLoopbackTarget("Source request target is unsafe.")
        try:
            with self._client.stream(method, self._url.copy_with(path=path), headers=headers, params=params, json=json, follow_redirects=False) as response:
                if 300 <= response.status_code < 400: raise RedirectRejected("Source redirect rejected.")
                if response.status_code >= 400: raise UpstreamHttpError("Source request failed.")
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > self._cap: raise ResponseTooLarge("Source response exceeded the configured limit.")
        except TransportError: raise
        except httpx.HTTPError: raise TransportError("Source transport failed.") from None
        try: result = json_module.loads(body)
        except (TypeError, ValueError): raise MalformedResponse("Source response was not valid JSON.") from None
        if not isinstance(result, dict): raise MalformedResponse("Source response was not valid JSON.")
        return result
