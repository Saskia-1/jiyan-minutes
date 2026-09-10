"""Tests that secret values cannot cross storage or reporting boundaries."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from chat_to_howto.security.secrets import (
    InMemorySecretStore,
    KeychainSecretStore,
    FileSecretStore,
    SecretStoreError,
)
from chat_to_howto.domain.consent import ConsentRecord, SecretAccessScope, SecretOperation


SENTINEL = "never-expose-secret-value"
NOW = datetime.now(timezone.utc)


def access(operation: SecretOperation, backend: str = "memory", **changes: str) -> SecretAccessScope:
    values: dict[str, object] = {
        "operation": operation,
        "secret_reference": "qq-token",
        "secret_backend": backend,
        "source_id": "qq",
        "account_id": "account-1",
        "client_version": "9.9.0",
        "tool_version": "1.2.3",
        "account_version": "account-v1",
    }
    values.update(changes)
    return SecretAccessScope(**values)


def grant(scope: SecretAccessScope) -> ConsentRecord:
    return ConsentRecord.grant(scope, expires_at=NOW + timedelta(minutes=5), now=NOW)


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
        write_access = access(SecretOperation.SET)
        store.set(access=write_access, consent=grant(write_access), value=SENTINEL)
        reference = store.reference_for(access=write_access, consent=grant(write_access))
        database = sqlite3.connect(":memory:")
        self.addCleanup(database.close)

        store.record_reference(database, reference, access=write_access, consent=grant(write_access))
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
        read_access = access(SecretOperation.GET)
        self.assertEqual(store.get(access=read_access, consent=grant(read_access)), SENTINEL)
        delete_access = access(SecretOperation.DELETE)
        store.delete(access=delete_access, consent=grant(delete_access))
        with self.assertRaises(SecretStoreError):
            store.get(access=read_access, consent=grant(read_access))

    def test_every_secret_operation_rejects_missing_mismatched_declined_or_expired_consent(self) -> None:
        """No store entry point may work without authorization for its exact scope."""
        store = InMemorySecretStore()
        write_access = access(SecretOperation.SET)
        with self.assertRaisesRegex(SecretStoreError, "denied"):
            store.set(access=write_access, consent=None, value=SENTINEL)
        with self.assertRaisesRegex(SecretStoreError, "denied"):
            store.set(
                access=write_access,
                consent=grant(access(SecretOperation.SET, tool_version="1.2.4")),
                value=SENTINEL,
            )
        declined = ConsentRecord.decline(
            write_access, expires_at=NOW + timedelta(minutes=5), now=NOW
        )
        with self.assertRaisesRegex(SecretStoreError, "denied"):
            store.set(access=write_access, consent=declined, value=SENTINEL)
        expired = ConsentRecord.grant(
            write_access,
            expires_at=NOW - timedelta(minutes=1),
            now=NOW - timedelta(minutes=2),
        )
        with self.assertRaisesRegex(SecretStoreError, "denied"):
            store.set(access=write_access, consent=expired, value=SENTINEL)

        store.set(access=write_access, consent=grant(write_access), value=SENTINEL)
        for operation in (SecretOperation.GET, SecretOperation.DELETE):
            with self.subTest(operation=operation):
                operation_access = access(operation)
                with self.assertRaisesRegex(SecretStoreError, "denied"):
                    if operation is SecretOperation.GET:
                        store.get(access=operation_access, consent=grant(write_access))
                    else:
                        store.delete(access=operation_access, consent=grant(write_access))

    def test_keychain_value_is_written_only_through_stdin_and_errors_are_redacted(self) -> None:
        """Putting the value in argv or a thrown tool error would disclose it."""
        runner = CapturingRunner()
        store = KeychainSecretStore(service="chat-to-howto", runner=runner)

        write_access = access(SecretOperation.SET, backend="keychain")
        store.set(access=write_access, consent=grant(write_access), value=SENTINEL)

        self.assertIsNotNone(runner.args)
        self.assertNotIn(SENTINEL, runner.args or ())
        self.assertEqual((runner.args or (None,))[-1], "-w")
        self.assertEqual(runner.input, SENTINEL)
        with self.assertRaises(SecretStoreError) as raised:
            KeychainSecretStore(service="chat-to-howto", runner=ExplodingRunner()).set(
                access=write_access, consent=grant(write_access), value=SENTINEL
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
            write_access = access(SecretOperation.SET, backend="file")
            store.set(access=write_access, consent=grant(write_access), value=SENTINEL)

            read_access = access(SecretOperation.GET, backend="file")
            self.assertEqual(store.get(access=read_access, consent=grant(read_access)), SENTINEL)
            self.assertEqual(os.stat(fallback_root).st_mode & 0o777, 0o700)
            self.assertEqual(os.stat(next(fallback_root.iterdir())).st_mode & 0o777, 0o600)


@unittest.skipUnless(
    sys.platform == "darwin" and os.getenv("RUN_KEYCHAIN_INTEGRATION") == "1",
    "set RUN_KEYCHAIN_INTEGRATION=1 on macOS to run the isolated Keychain integration",
)
class KeychainIntegrationTests(unittest.TestCase):
    def test_temporary_keychain_set_get_delete_never_emits_the_synthetic_value(self) -> None:
        """The password prompt path must work without the default Keychain or argv secrets."""
        integration_secret = "synthetic-" + "keychain-" + "secret"
        captured_stdout = StringIO()
        captured_stderr = StringIO()
        with tempfile.TemporaryDirectory(dir="/private/tmp") as temporary:
            keychain_path = Path(temporary) / "chat-to-howto-test.keychain-db"
            subprocess.run(
                ["security", "create-keychain", "-p", "", str(keychain_path)],
                check=True,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            subprocess.run(
                ["security", "unlock-keychain", "-p", "", str(keychain_path)],
                check=True,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.addCleanup(
                lambda: subprocess.run(
                    ["security", "delete-keychain", str(keychain_path)],
                    check=False,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
            )
            store = KeychainSecretStore(
                service="chat-to-howto-integration", keychain_path=keychain_path
            )
            write_access = access(SecretOperation.SET, backend="keychain")
            read_access = access(SecretOperation.GET, backend="keychain")
            delete_access = access(SecretOperation.DELETE, backend="keychain")

            with redirect_stdout(captured_stdout), redirect_stderr(captured_stderr):
                store.set(access=write_access, consent=grant(write_access), value=integration_secret)
                self.assertEqual(
                    store.get(access=read_access, consent=grant(read_access)), integration_secret
                )
                store.delete(access=delete_access, consent=grant(delete_access))
                with self.assertRaises(SecretStoreError):
                    store.get(access=read_access, consent=grant(read_access))
        self.assertNotIn(integration_secret, captured_stdout.getvalue())
        self.assertNotIn(integration_secret, captured_stderr.getvalue())
