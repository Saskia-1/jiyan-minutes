"""Loopback transport boundaries are enforced before a request is sent."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from chat_to_howto.sources.transport import (
    LoopbackTransport,
    RedirectRejected,
    ResponseTooLarge,
    UnsafeLoopbackTarget,
)


TOKEN = "synthetic-token-that-must-not-escape"


class LoopbackTransportTests(unittest.TestCase):
    def test_rejects_non_exact_loopback_targets_and_resolved_public_addresses(self) -> None:
        """A changed host, scheme, port, credential, or DNS answer must stop SSRF."""
        invalid_targets = (
            "http://example.test:40653",
            "http://user:pass@127.0.0.1:40653",
            "https://127.0.0.1:40653",
            "http://127.0.0.1:6131",
            "http://127.0.0.1:40653?token=" + TOKEN,
            "http://127.0.0.1:40653#fragment",
        )
        for target in invalid_targets:
            with self.subTest(target=target), self.assertRaises(UnsafeLoopbackTarget) as raised:
                LoopbackTransport(target, expected_port=40653, resolver=lambda _host: ("127.0.0.1",))
            self.assertNotIn(TOKEN, str(raised.exception))

        with self.assertRaises(UnsafeLoopbackTarget):
            LoopbackTransport(
                "http://localhost:40653",
                expected_port=40653,
                resolver=lambda _host: ("127.0.0.1", "203.0.113.7"),
            )

    def test_rejects_redirects_without_following_them(self) -> None:
        """A redirect could otherwise escape the fixed loopback allowlist."""
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(302, headers={"location": "http://example.test/private"})

        client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
        transport = LoopbackTransport(
            "http://127.0.0.1:40653",
            expected_port=40653,
            resolver=lambda _host: ("127.0.0.1",),
            client=client,
        )
        with self.assertRaises(RedirectRejected):
            transport.request("GET", "/health")
        self.assertEqual(len(seen), 1)

    def test_caps_streamed_response_and_never_puts_token_in_errors_or_repr(self) -> None:
        """Oversized or failed responses must be body-free, bounded public errors."""
        client = httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(200, headers={"content-length": "1"}, content=b"abcdef")
            ),
            follow_redirects=False,
        )
        transport = LoopbackTransport(
            "http://127.0.0.1:40653",
            expected_port=40653,
            resolver=lambda _host: ("127.0.0.1",),
            client=client,
            max_body_bytes=5,
        )
        with self.assertRaises(ResponseTooLarge) as raised:
            transport.request("GET", "/health", headers={"Authorization": f"Bearer {TOKEN}"})
        self.assertNotIn(TOKEN, str(raised.exception))
        self.assertNotIn(TOKEN, repr(transport))

    def test_rejects_paths_that_carry_query_or_fragment_secrets(self) -> None:
        """Callers cannot turn this client into a query-token proxy."""
        transport = LoopbackTransport(
            "http://127.0.0.1:40653",
            expected_port=40653,
            resolver=lambda _host: ("127.0.0.1",),
            client=httpx.Client(transport=httpx.MockTransport(lambda _request: httpx.Response(200))),
        )
        for path in ("/api/friends?token=" + TOKEN, "/api/friends#secret"):
            with self.subTest(path=path), self.assertRaises(UnsafeLoopbackTarget) as raised:
                transport.request("GET", path)
            self.assertNotIn(TOKEN, str(raised.exception))
