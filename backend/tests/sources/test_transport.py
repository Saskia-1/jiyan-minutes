"""Loopback transport boundaries are enforced before requests are sent."""

from __future__ import annotations

import sys
import unittest
from collections.abc import Callable, Iterable
from pathlib import Path
from unittest import mock

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


class TrackingStream(ChunkedStream):
    """Synthetic response stream that records closure by the HTTP client."""

    def __init__(self, chunks: Iterable[bytes]) -> None:
        super().__init__(chunks)
        self.closed = False

    def close(self) -> None:
        self.closed = True


class TrackingMockTransport(httpx.MockTransport):
    """Synthetic network transport that records client lifecycle closure."""

    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        super().__init__(handler)
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1
        super().close()


def mock_transport(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


def qce_transport(
    test_transport: httpx.MockTransport, *, cap: int = 1_048_576
) -> LoopbackTransport:
    return LoopbackTransport(
        "http://127.0.0.1:40653",
        expected_port=40653,
        source="qce",
        resolver=lambda _host: ("127.0.0.1",),
        mock_transport=test_transport,
        max_body_bytes=cap,
    )


class LoopbackTransportTests(unittest.TestCase):
    def test_exposes_a_public_close_method(self) -> None:
        """Callers need an explicit lifecycle boundary for the owned client."""
        transport = qce_transport(
            mock_transport(lambda _request: httpx.Response(200, json={"success": True}))
        )
        self.assertTrue(callable(getattr(transport, "close", None)))

    def test_context_manager_closes_response_and_client_and_blocks_later_requests(self) -> None:
        """Close must free owned resources and make later reads fail without details."""
        stream = TrackingStream((b'{"success": true}',))
        test_transport = TrackingMockTransport(
            lambda _request: httpx.Response(200, stream=stream)
        )
        with qce_transport(test_transport) as transport:
            self.assertEqual(transport.request("GET", "/health", headers={}), {"success": True})
            self.assertTrue(stream.closed)
            self.assertEqual(test_transport.close_calls, 0)

        self.assertEqual(test_transport.close_calls, 1)
        transport.close()
        self.assertEqual(test_transport.close_calls, 1)
        with self.assertRaises(TransportError) as raised:
            transport.request("GET", "/health", headers={})
        self.assertIs(type(raised.exception), TransportError)
        self.assertEqual(str(raised.exception), "Source transport is closed.")
        self.assertNotIn(SENTINEL, repr(raised.exception))
        self.assertIsNone(raised.exception.__cause__)

    def test_rejects_complete_http_clients_as_injection(self) -> None:
        """A configured client could add proxy, auth, cookies, or hooks after validation."""
        client = httpx.Client(follow_redirects=False, trust_env=False, timeout=httpx.Timeout(5.0))
        try:
            with self.assertRaises(TypeError):
                LoopbackTransport(
                    "http://127.0.0.1:40653",
                    expected_port=40653,
                    source="qce",
                    client=client,
                )
        finally:
            client.close()

    def test_rejects_empty_raw_url_delimiters_before_httpx_normalizes_them(self) -> None:
        """Parser-normalized empty userinfo/query/fragment must not be accepted."""
        for target in (
            "http://@127.0.0.1:40653",
            "http://127.0.0.1:40653?",
            "http://127.0.0.1:40653#",
        ):
            with self.subTest(target=target), self.assertRaises(UnsafeLoopbackTarget):
                LoopbackTransport(target, expected_port=40653)

    def test_mock_transport_observes_the_fixed_origin_path_query_and_headers(self) -> None:
        """Test injection cannot hide a changed origin or credential-bearing request."""
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            self.assertEqual(request.url.scheme, "http")
            self.assertEqual(request.url.host, "127.0.0.1")
            self.assertEqual(request.url.port, 40653)
            self.assertNotIn("cookie", request.headers)
            self.assertNotIn("x-api-key", request.headers)
            if request.url.path == "/health":
                self.assertEqual(str(request.url), "http://127.0.0.1:40653/health")
                self.assertEqual(request.url.raw_path, b"/health")
                self.assertNotIn("authorization", request.headers)
            else:
                self.assertEqual(str(request.url), "http://127.0.0.1:40653/api/friends?page=1")
                self.assertEqual(request.url.raw_path, b"/api/friends?page=1")
                self.assertEqual(request.headers["authorization"], "Bearer fixture-credential")
            return httpx.Response(200, json={"success": True})

        transport = qce_transport(mock_transport(handler))
        self.assertEqual(transport.request("GET", "/health", headers={}), {"success": True})
        self.assertEqual(
            transport.request(
                "GET",
                "/api/friends",
                headers={"Authorization": "Bearer fixture-credential"},
                params={"page": 1},
            ),
            {"success": True},
        )
        self.assertEqual(len(seen), 2)

    def test_response_cookies_are_never_replayed_on_the_next_request(self) -> None:
        """A source-controlled Set-Cookie must not become ambient authentication."""
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            self.assertNotIn("cookie", request.headers)
            if len(seen) == 1:
                return httpx.Response(
                    200,
                    headers={"set-cookie": "session=" + SENTINEL + "; Path=/"},
                    json={"success": True},
                )
            return httpx.Response(200, json={"success": True})

        transport = qce_transport(mock_transport(handler))
        self.assertEqual(transport.request("GET", "/health", headers={}), {"success": True})
        self.assertEqual(transport.request("GET", "/health", headers={}), {"success": True})
        self.assertEqual(len(seen), 2)
        self.assertNotIn(SENTINEL, repr(transport))

        failing_transport = qce_transport(
            mock_transport(
                lambda _request: httpx.Response(
                    503,
                    headers={"set-cookie": "session=" + SENTINEL + "; Path=/"},
                    content=b"upstream failure",
                )
            )
        )
        with self.assertRaises(UpstreamHttpError) as raised:
            failing_transport.request("GET", "/health", headers={})
        self.assertNotIn(SENTINEL, str(raised.exception))
        self.assertNotIn(SENTINEL, repr(raised.exception))

    def test_default_source_contract_fails_closed_and_only_allows_locked_routes(self) -> None:
        """An unbound or broadened transport could send or administrate upstream data."""
        test_transport = mock_transport(
            lambda _request: httpx.Response(200, json={"success": True})
        )
        unbound = LoopbackTransport(
            "http://127.0.0.1:40653",
            expected_port=40653,
            resolver=lambda _host: ("127.0.0.1",),
            mock_transport=test_transport,
        )
        with self.assertRaises(UnsafeLoopbackTarget):
            unbound.request("GET", "/health", headers={})

        transport = qce_transport(test_transport)
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
            mock_transport(lambda _request: httpx.Response(200, json={"success": True}))
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
            mock_transport=mock_transport(
                lambda _request: httpx.Response(200, json={"ok": True})
            ),
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
        transport = qce_transport(mock_transport(lambda _request: httpx.Response(200, json={})))
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

        transport = qce_transport(mock_transport(handler))
        with self.assertRaises(RedirectRejected) as raised:
            transport.request("GET", "/health", headers={})
        self.assertIs(type(raised.exception), RedirectRejected)
        self.assertIsNone(raised.exception.__cause__)
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
                    mock_transport(
                        lambda _request, headers=headers, chunks=chunks: httpx.Response(
                            200,
                            headers=headers,
                            stream=ChunkedStream(chunks),
                        )
                    ),
                    cap=5,
                )
                with self.assertRaises(ResponseTooLarge) as raised:
                    transport.request("GET", "/health", headers={})
                self.assertIs(type(raised.exception), ResponseTooLarge)
                self.assertIsNone(raised.exception.__cause__)

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
                transport = qce_transport(mock_transport(handler))
                with self.assertRaises(expected) as raised:
                    transport.request("GET", "/health", headers={})
                self.assertIs(type(raised.exception), expected)
                self.assertNotIn(SENTINEL, str(raised.exception))
                self.assertIsNone(raised.exception.__cause__)

    def test_sanitizes_dependency_transport_errors_even_when_they_are_typed(self) -> None:
        """A dependency may forge a public error type with sensitive detail."""
        transport = qce_transport(
            mock_transport(
                lambda _request: (_ for _ in ()).throw(TransportError(SENTINEL))
            )
        )
        with self.assertRaises(TransportError) as raised:
            transport.request("GET", "/health", headers={})
        self.assertIs(type(raised.exception), TransportError)
        self.assertEqual(str(raised.exception), "Source transport failed.")
        self.assertNotIn(SENTINEL, repr(raised.exception))
        self.assertIsNone(raised.exception.__cause__)

    def test_rejects_deep_json_with_fixed_malformed_error(self) -> None:
        """Unsafe JSON parser recursion must not escape this response boundary."""
        nested_json = b"[" * 2_000 + b"0" + b"]" * 2_000
        self.assertLess(len(nested_json), 4_096)
        transport = qce_transport(
            mock_transport(lambda _request: httpx.Response(200, content=nested_json))
        )
        with self.assertRaises(MalformedResponse) as raised:
            transport.request("GET", "/health", headers={})
        self.assertIs(type(raised.exception), MalformedResponse)
        self.assertIsNone(raised.exception.__cause__)

    def test_maps_json_memory_errors_to_a_fixed_malformed_error(self) -> None:
        """A parser allocation failure must not reveal parser or source details."""
        transport = qce_transport(
            mock_transport(lambda _request: httpx.Response(200, content=b"{}"))
        )
        with mock.patch(
            "chat_to_howto.sources.transport.json_module.loads",
            side_effect=MemoryError(SENTINEL),
        ), self.assertRaises(MalformedResponse) as raised:
            transport.request("GET", "/health", headers={})
        self.assertIs(type(raised.exception), MalformedResponse)
        self.assertNotIn(SENTINEL, repr(raised.exception))
        self.assertIsNone(raised.exception.__cause__)

    def test_rejects_non_mock_network_transports_as_injection(self) -> None:
        """Only a test double, never a real HTTP transport, is injectable."""
        with self.assertRaises(TypeError):
            LoopbackTransport(
                "http://127.0.0.1:40653",
                expected_port=40653,
                source="qce",
                mock_transport=httpx.HTTPTransport(),
            )

    def test_rejects_resolver_answers_outside_loopback(self) -> None:
        """A resolver hook returning a public address must not weaken loopback-only use."""
        with self.assertRaises(UnsafeLoopbackTarget):
            LoopbackTransport(
                "http://127.0.0.1:40653",
                expected_port=40653,
                source="qce",
                resolver=lambda _host: ("127.0.0.1", "203.0.113.7"),
            )
