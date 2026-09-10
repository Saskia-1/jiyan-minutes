"""Tests that secret values cannot cross storage or reporting boundaries."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from chat_to_howto.security.secrets import (
    InMemorySecretStore,
    KeychainSecretStore,
    FileSecretStore,
    SecretStoreError,
)


SENTINEL = "never-expose-secret-value"


class ExplodingRunner:
    def run(self, *args: object, **kwargs: object) -> object:
        raise RuntimeError(f"tool output: {SENTINEL}")


class CapturingRunner:
    def __init__(self) -> None:
        self.args: tuple[str, ...] | None = None
        self.input: str | None = None

    def run(self, args: list[str], **kwargs: object) -> object:
        self.args = tuple(args)
        self.input = kwargs.get("input") if isinstance(kwargs.get("input"), str) else None
        return object()


class SecretStoreTests(unittest.TestCase):
    def test_in_memory_value_is_absent_from_repr_api_logs_and_sqlite_rows(self) -> None:
        """Exposing this sentinel from any public boundary would leak a token."""
        store = InMemorySecretStore()
        store.set("qq-token", SENTINEL)
        reference = store.reference("qq-token")
        database = sqlite3.connect(":memory:")
        self.addCleanup(database.close)

        store.record_reference(database, reference)
        rows = database.execute("SELECT secret_id, backend FROM secret_references").fetchall()
        with self.assertLogs("chat_to_howto.security.secrets", level=logging.INFO) as captured:
            store.log_reference(reference)

        public_boundaries = "\n".join(
            [
                repr(store),
                repr(reference),
                json.dumps(reference.model_dump(mode="json"), sort_keys=True),
                repr(rows),
                "\n".join(captured.output),
            ]
        )
        self.assertNotIn(SENTINEL, public_boundaries)
        self.assertEqual(store.get("qq-token"), SENTINEL)
        store.delete("qq-token")
        with self.assertRaises(SecretStoreError):
            store.get("qq-token")

    def test_keychain_value_is_written_only_through_stdin_and_errors_are_redacted(self) -> None:
        """Putting the value in argv or a thrown tool error would disclose it."""
        runner = CapturingRunner()
        store = KeychainSecretStore(service="chat-to-howto", runner=runner)

        store.set("qq-token", SENTINEL)

        self.assertIsNotNone(runner.args)
        self.assertNotIn(SENTINEL, runner.args or ())
        self.assertEqual(runner.input, SENTINEL)
        with self.assertRaises(SecretStoreError) as raised:
            KeychainSecretStore(service="chat-to-howto", runner=ExplodingRunner()).set(
                "qq-token", SENTINEL
            )
        self.assertNotIn(SENTINEL, str(raised.exception))
        self.assertIsNone(raised.exception.__cause__)

    def test_file_fallback_requires_opt_in_and_uses_private_modes_outside_source_tree(self) -> None:
        """An implicit or source-tree fallback could persist a token unsafely."""
        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            source_root = temporary_path / "workspace"
            fallback_root = temporary_path / "private-secrets"
            with self.assertRaisesRegex(ValueError, "disabled"):
                FileSecretStore(root=fallback_root, source_root=source_root)
            with self.assertRaisesRegex(ValueError, "outside the source tree"):
                FileSecretStore(
                    root=source_root / "secrets", source_root=source_root, enabled=True
                )

            store = FileSecretStore(
                root=fallback_root, source_root=source_root, enabled=True
            )
            store.set("qq-token", SENTINEL)

            self.assertEqual(store.get("qq-token"), SENTINEL)
            self.assertEqual(os.stat(fallback_root).st_mode & 0o777, 0o700)
            self.assertEqual(os.stat(next(fallback_root.iterdir())).st_mode & 0o777, 0o600)
