"""Bounded HTTP client for a fixed, literal loopback source contract."""

from __future__ import annotations

import ipaddress
import json as json_module
import math
import re
from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import Any
from urllib.parse import unquote_plus

import httpx


class TransportError(RuntimeError):
    """A body-free error raised at the source transport boundary."""


class UnsafeLoopbackTarget(TransportError):
    """The configured source or a request violates its fixed contract."""


class RedirectRejected(TransportError):
    """The source attempted to redirect the request."""


class ResponseTooLarge(TransportError):
    """The source response exceeded the configured byte cap."""


class UpstreamHttpError(TransportError):
    """The source returned an HTTP error without exposing its body."""


class MalformedResponse(TransportError):
    """The source did not return a JSON object."""


_SOURCE_ALLOWLIST: Mapping[str, frozenset[tuple[str, str]]] = MappingProxyType(
    {
        "qce": frozenset(
            {
                ("GET", "/health"),
                ("GET", "/api/friends"),
                ("GET", "/api/groups"),
                ("GET", "/api/recent-contacts"),
                ("POST", "/api/messages/fetch"),
            }
        ),
        "tracememo": frozenset(
            {
                ("GET", "/api/v1/health"),
                ("GET", "/api/v1/contact"),
                ("GET", "/api/v1/chatlog"),
            }
        ),
    }
)
_FORBIDDEN_QUERY_KEYS = frozenset(
    {"token", "accesstoken", "authorization", "apikey", "key", "secret", "bearer"}
)


class LoopbackTransport:
    """Send only a source's immutable read-only request allowlist to loopback."""

    def __init__(
        self,
        base_url: str,
        *,
        expected_port: int,
        source: str | None = None,
        resolver: Callable[[str], tuple[str, ...]] | None = None,
        mock_transport: httpx.MockTransport | None = None,
        timeout_seconds: float = 5.0,
        max_body_bytes: int = 1_048_576,
    ) -> None:
        if (
            isinstance(expected_port, bool)
            or not isinstance(expected_port, int)
            or not 1 <= expected_port <= 65_535
            or not isinstance(timeout_seconds, (int, float))
            or isinstance(timeout_seconds, bool)
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
            or isinstance(max_body_bytes, bool)
            or not isinstance(max_body_bytes, int)
            or max_body_bytes <= 0
        ):
            raise ValueError("transport limits must be positive and finite")
        if not isinstance(base_url, str) or not re.fullmatch(
            rf"http://(?:127\.0\.0\.1|\[::1\]):{expected_port}/?", base_url
        ):
            raise UnsafeLoopbackTarget("Configured source target is unsafe.")
        try:
            url = httpx.URL(base_url)
        except httpx.InvalidURL:
            raise UnsafeLoopbackTarget("Configured source target is unsafe.") from None
        if (
            url.scheme != "http"
            or url.host not in {"127.0.0.1", "::1"}
            or url.port != expected_port
            or url.userinfo
            or url.query
            or url.fragment
            or url.path not in ("", "/")
        ):
            raise UnsafeLoopbackTarget("Configured source target is unsafe.")
        if resolver is not None and not self._resolver_is_safe(resolver, url.host):
            raise UnsafeLoopbackTarget("Configured source target is unsafe.")
        if mock_transport is not None and not isinstance(mock_transport, httpx.MockTransport):
            raise TypeError("mock_transport must be an httpx.MockTransport")

        self._url = url
        self._cap = max_body_bytes
        self._allowed_requests = _SOURCE_ALLOWLIST.get(source, frozenset())
        self._client = httpx.Client(
            transport=mock_transport,
            follow_redirects=False,
            trust_env=False,
            timeout=httpx.Timeout(timeout_seconds),
        )

    def __repr__(self) -> str:
        return "<LoopbackTransport>"

    def request(
        self,
        method: str,
        path: str,
        *,
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, object] | None = None,
        json: object | None = None,
    ) -> dict[str, Any]:
        self._validate_request(method, path, headers, params)
        try:
            with self._client.stream(
                method,
                self._url.copy_with(path=path),
                headers=headers,
                params=params,
                json=json,
                follow_redirects=False,
            ) as response:
                status_code = response.status_code
                body = bytearray()
                body_too_large = False
                if status_code < 300:
                    for chunk in response.iter_bytes():
                        if len(chunk) > self._cap - len(body):
                            body_too_large = True
                            break
                        body.extend(chunk)
        except Exception:
            raise TransportError("Source transport failed.") from None
        if 300 <= status_code < 400:
            raise RedirectRejected("Source redirect rejected.")
        if status_code >= 400:
            raise UpstreamHttpError("Source request failed.")
        if body_too_large:
            raise ResponseTooLarge("Source response exceeded the configured limit.")
        try:
            result = json_module.loads(bytes(body))
        except (MemoryError, RecursionError, TypeError, UnicodeDecodeError, ValueError):
            raise MalformedResponse("Source response was not valid JSON.") from None
        if not isinstance(result, dict):
            raise MalformedResponse("Source response was not valid JSON.")
        return result

    @staticmethod
    def _resolver_is_safe(
        resolver: Callable[[str], tuple[str, ...]], host: str
    ) -> bool:
        try:
            answers = resolver(host)
            return bool(answers) and all(
                ipaddress.ip_address(answer).is_loopback for answer in answers
            )
        except (TypeError, ValueError):
            return False
        except Exception:
            return False

    def _validate_request(
        self,
        method: str,
        path: str,
        headers: Mapping[str, str] | None,
        params: Mapping[str, object] | None,
    ) -> None:
        if (
            not isinstance(method, str)
            or not isinstance(path, str)
            or (method, path) not in self._allowed_requests
        ):
            raise UnsafeLoopbackTarget("Source request target is unsafe.")
        self._validate_headers(headers, health_request=path.endswith("/health"))
        self._validate_params(params)

    @staticmethod
    def _validate_headers(
        headers: Mapping[str, str] | None, *, health_request: bool
    ) -> None:
        if health_request:
            if headers is None or not headers:
                return
            raise UnsafeLoopbackTarget("Source request headers are unsafe.")
        if not isinstance(headers, Mapping) or len(headers) != 1:
            raise UnsafeLoopbackTarget("Source request headers are unsafe.")
        name, value = next(iter(headers.items()))
        if (
            not isinstance(name, str)
            or not isinstance(value, str)
            or name.casefold() != "authorization"
            or not re.fullmatch(r"Bearer [^\r\n ](?:[^\r\n]*[^\r\n ])?", value)
        ):
            raise UnsafeLoopbackTarget("Source request headers are unsafe.")

    @staticmethod
    def _validate_params(params: Mapping[str, object] | None) -> None:
        if params is None:
            return
        if not isinstance(params, Mapping):
            raise UnsafeLoopbackTarget("Source request target is unsafe.")
        for key in params:
            if not isinstance(key, str) or _normalise_query_key(key) in _FORBIDDEN_QUERY_KEYS:
                raise UnsafeLoopbackTarget("Source request target is unsafe.")


def _normalise_query_key(key: str) -> str:
    decoded = key
    for _ in range(8):
        next_value = unquote_plus(decoded)
        if next_value == decoded:
            break
        decoded = next_value
    return re.sub(r"[^a-z0-9]", "", decoded.casefold())
