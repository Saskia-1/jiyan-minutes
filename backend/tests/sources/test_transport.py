"""Loopback transport boundaries are enforced before requests are sent."""

from __future__ import annotations

import sys
import unittest
from collections.abc import Callable, Iterable
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from chat_to_howto.sources.transport import (
    LoopbackTransport,
    MalformedResponse,
    RedirectRejected,
    ResponseTooLarge,
    TransportError,
    UnsafeLoopbackTarget,
    UpstreamHttpError,
)


SENTINEL = "synthetic-sensitive-value"


class ChunkedStream(httpx.SyncByteStream):
    """A synthetic stream whose chunks are distinct from Content-Length."""

    def __init__(self, chunks: Iterable[bytes]) -> None:
        self._chunks = tuple(chunks)

    def __iter__(self) -> Iterable[bytes]:
        return iter(self._chunks)

    def close(self) -> None:
        pass


def mock_client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(
        transport=httpx.MockTransport(handler),
        follow_redirects=False,
        trust_env=False,
        timeout=httpx.Timeout(5.0),
    )


def qce_transport(client: httpx.Client, *, cap: int = 1_048_576) -> LoopbackTransport:
    return LoopbackTransport(
        "http://127.0.0.1:40653",
        expected_port=40653,
        source="qce",
        resolver=lambda _host: ("127.0.0.1",),
        client=client,
        max_body_bytes=cap,
    )


class LoopbackTransportTests(unittest.TestCase):
    def test_rejects_empty_raw_url_delimiters_before_httpx_normalizes_them(self) -> None:
        """Parser-normalized empty userinfo/query/fragment must not be accepted."""
        for target in (
            "http://@127.0.0.1:40653",
            "http://127.0.0.1:40653?",
            "http://127.0.0.1:40653#",
        ):
            with self.subTest(target=target), self.assertRaises(UnsafeLoopbackTarget):
                LoopbackTransport(target, expected_port=40653)

    def test_default_source_contract_fails_closed_and_only_allows_locked_routes(self) -> None:
        """An unbound or broadened transport could send or administrate upstream data."""
        client = mock_client(lambda _request: httpx.Response(200, json={"success": True}))
        unbound = LoopbackTransport(
            "http://127.0.0.1:40653",
            expected_port=40653,
            resolver=lambda _host: ("127.0.0.1",),
            client=client,
        )
        with self.assertRaises(UnsafeLoopbackTarget):
            unbound.request("GET", "/health", headers={})

        transport = qce_transport(client)
        for method, path in (
            ("get", "/health"),
            ("GET", "/api%2Ffriends"),
            ("GET", "//api/friends"),
            ("GET", "/api/./friends"),
            ("GET", "/api/messages/send"),
            ("POST", "/api/messages/fetch/"),
        ):
            with self.subTest(method=method, path=path), self.assertRaises(UnsafeLoopbackTarget):
                transport.request(method, path, headers={})

        self.assertEqual(transport.request("GET", "/health", headers={}), {"success": True})

    def test_qce_allows_only_its_five_locked_request_shapes(self) -> None:
        """Removing one endpoint or adding a generic route breaks the locked QCE contract."""
        transport = qce_transport(
            mock_client(lambda _request: httpx.Response(200, json={"success": True}))
        )
        bearer = {"Authorization": "Bearer fixture-credential"}
        allowed = (
            ("GET", "/health", {}, None, None),
            ("GET", "/api/friends", bearer, {"page": 1}, None),
            ("GET", "/api/groups", bearer, {"page": 1}, None),
            ("GET", "/api/recent-contacts", bearer, {"limit": 1}, None),
            ("POST", "/api/messages/fetch", bearer, None, {"page": 1}),
        )
        for method, path, headers, params, body in allowed:
            with self.subTest(method=method, path=path):
                self.assertEqual(
                    transport.request(method, path, headers=headers, params=params, json=body),
                    {"success": True},
                )

    def test_tracememo_contract_rejects_write_and_administrative_routes(self) -> None:
        """A source label must not turn the transport into a generic local proxy."""
        transport = LoopbackTransport(
            "http://[::1]:6131",
            expected_port=6131,
            source="tracememo",
            resolver=lambda _host: ("::1",),
            client=mock_client(lambda _request: httpx.Response(200, json={"ok": True})),
        )
        self.assertEqual(transport.request("GET", "/api/v1/health", headers={}), {"ok": True})
        for path in (
            "/api/v1/send",
            "/api/v1/report",
            "/api/v1/agent",
            "/api/v1/sql",
            "/api/v1/monitor",
            "/api/v1/anti-recall",
        ):
            with self.subTest(path=path), self.assertRaises(UnsafeLoopbackTarget):
                transport.request("POST", path, headers={})

        bearer = {"Authorization": "Bearer fixture-credential"}
        for path in ("/api/v1/contact", "/api/v1/chatlog"):
            with self.subTest(path=path):
                self.assertEqual(transport.request("GET", path, headers=bearer), {"ok": True})

    def test_rejects_query_secrets_and_unapproved_authentication_headers(self) -> None:
        """Credentials outside a Bearer header must never become request metadata."""
        transport = qce_transport(mock_client(lambda _request: httpx.Response(200, json={})))
        for params in (
            {"token": SENTINEL},
            {"ACCESS%5FTOKEN": SENTINEL},
            {"Api-Key": SENTINEL},
            {"api key": SENTINEL},
            {"Bearer": SENTINEL},
        ):
            with self.subTest(params=params), self.assertRaises(UnsafeLoopbackTarget) as raised:
                transport.request("GET", "/api/friends", headers={}, params=params)
            self.assertNotIn(SENTINEL, str(raised.exception))

        for headers in (
            {},
            {"X-API-Key": SENTINEL},
            {"Cookie": "session=" + SENTINEL},
            {"Authorization": "Basic " + SENTINEL},
            {"Authorization": "Bearer "},
            {"Authorization": "Bearer " + SENTINEL, "X-Extra": "value"},
        ):
            with self.subTest(headers=headers), self.assertRaises(UnsafeLoopbackTarget) as raised:
                transport.request("GET", "/api/friends", headers=headers)
            self.assertNotIn(SENTINEL, str(raised.exception))

    def test_rejects_redirects_without_following_them(self) -> None:
        """A redirect could otherwise escape the fixed loopback allowlist."""
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(302, headers={"location": "http://example.test/private"})

        transport = qce_transport(mock_client(handler))
        with self.assertRaises(RedirectRejected):
            transport.request("GET", "/health", headers={})
        self.assertEqual(len(seen), 1)

    def test_caps_single_and_multiple_chunks_before_caching_past_cap(self) -> None:
        """A chunk that crosses the cap must fail even without honest length metadata."""
        cases = (
            (None, (b"abcdef",)),
            ("1", (b"abc", b"def")),
        )
        for content_length, chunks in cases:
            with self.subTest(content_length=content_length, chunks=chunks):
                headers = {} if content_length is None else {"content-length": content_length}
                transport = qce_transport(
                    mock_client(
                        lambda _request, headers=headers, chunks=chunks: httpx.Response(
                            200,
                            headers=headers,
                            stream=ChunkedStream(chunks),
                        )
                    ),
                    cap=5,
                )
                with self.assertRaises(ResponseTooLarge):
                    transport.request("GET", "/health", headers={})

    def test_maps_malformed_http_and_transport_failures_to_body_free_errors(self) -> None:
        """No upstream body or exception text may cross this boundary."""
        cases: tuple[
            tuple[Callable[[httpx.Request], httpx.Response], type[TransportError]], ...
        ] = (
            (
                lambda _request: httpx.Response(
                    200, content=("not-json-" + SENTINEL).encode()
                ),
                MalformedResponse,
            ),
            (lambda _request: httpx.Response(200, json=[SENTINEL]), MalformedResponse),
            (
                lambda _request: httpx.Response(
                    503, content=("error-" + SENTINEL).encode()
                ),
                UpstreamHttpError,
            ),
            (
                lambda _request: (_ for _ in ()).throw(
                    httpx.ReadError("transport-" + SENTINEL)
                ),
                TransportError,
            ),
        )
        for handler, expected in cases:
            with self.subTest(expected=expected.__name__):
                transport = qce_transport(mock_client(handler))
                with self.assertRaises(expected) as raised:
                    transport.request("GET", "/health", headers={})
                self.assertNotIn(SENTINEL, str(raised.exception))

    def test_rejects_injected_clients_that_enable_proxy_redirect_or_unbounded_timeout(self) -> None:
        """Dependency injection must not bypass production network invariants."""
        unsafe_clients = (
            httpx.Client(follow_redirects=True, trust_env=False, timeout=httpx.Timeout(5.0)),
            httpx.Client(follow_redirects=False, trust_env=True, timeout=httpx.Timeout(5.0)),
            httpx.Client(follow_redirects=False, trust_env=False, timeout=httpx.Timeout(None)),
            httpx.Client(
                proxy="http://127.0.0.1:9999",
                follow_redirects=False,
                trust_env=False,
                timeout=httpx.Timeout(5.0),
            ),
        )
        try:
            for client in unsafe_clients:
                with self.subTest(client=client), self.assertRaises(TransportError):
                    qce_transport(client)
        finally:
            for client in unsafe_clients:
                client.close()

    def test_rejects_resolver_answers_outside_loopback(self) -> None:
        """A resolver hook returning a public address must not weaken loopback-only use."""
        with self.assertRaises(UnsafeLoopbackTarget):
            LoopbackTransport(
                "http://127.0.0.1:40653",
                expected_port=40653,
                source="qce",
                resolver=lambda _host: ("127.0.0.1", "203.0.113.7"),
            )
