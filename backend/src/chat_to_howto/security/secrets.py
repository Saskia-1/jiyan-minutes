"""Secret stores that keep values out of public and persistent metadata."""

from __future__ import annotations

import logging
import os
from pathlib import Path
import subprocess
from typing import Protocol

from pydantic import BaseModel, ConfigDict


LOGGER = logging.getLogger(__name__)


class SecretStoreError(RuntimeError):
    """A deliberately detail-free storage failure."""


class CommandRunner(Protocol):
    def run(self, args: list[str], **kwargs: object) -> object: ...


class SecretStore(Protocol):
    """Storage interface whose public methods never serialize secret values."""

    def get(self, secret_id: str) -> str: ...

    def set(self, secret_id: str, value: str) -> None: ...

    def delete(self, secret_id: str) -> None: ...


class SecretReference(BaseModel):
    """Persistable metadata pointing to a secret held by a dedicated backend."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    secret_id: str
    backend: str


class InMemorySecretStore:
    """Test-only store; values remain process-local and are omitted from repr."""

    backend = "memory"

    def __init__(self) -> None:
        self._values: dict[str, str] = {}

    def __repr__(self) -> str:
        return "<InMemorySecretStore backend='memory'>"

    def get(self, secret_id: str) -> str:
        try:
            return self._values[secret_id]
        except KeyError as exc:
            raise SecretStoreError("Secret is unavailable.") from exc

    def set(self, secret_id: str, value: str) -> None:
        self._values[secret_id] = value

    def delete(self, secret_id: str) -> None:
        self._values.pop(secret_id, None)

    def reference(self, secret_id: str) -> SecretReference:
        if secret_id not in self._values:
            raise SecretStoreError("Secret is unavailable.")
        return SecretReference(secret_id=secret_id, backend=self.backend)

    def record_reference(self, connection: object, reference: SecretReference) -> None:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS secret_references (secret_id TEXT PRIMARY KEY, backend TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT OR REPLACE INTO secret_references (secret_id, backend) VALUES (?, ?)",
            (reference.secret_id, reference.backend),
        )
        connection.commit()

    def log_reference(self, reference: SecretReference) -> None:
        LOGGER.info("Secret reference stored: backend=%s id=%s", reference.backend, reference.secret_id)


class KeychainSecretStore:
    """macOS Keychain adapter that writes values only on standard input."""

    backend = "keychain"

    def __init__(self, *, service: str, runner: CommandRunner | None = None) -> None:
        self._service = service
        self._runner = runner or subprocess

    def __repr__(self) -> str:
        return "<KeychainSecretStore backend='keychain'>"

    def get(self, secret_id: str) -> str:
        result = self._run(
            ["security", "find-generic-password", "-a", secret_id, "-s", self._service, "-w"]
        )
        value = getattr(result, "stdout", "")
        if not isinstance(value, str):
            raise SecretStoreError("Secret is unavailable.")
        return value.rstrip("\n")

    def set(self, secret_id: str, value: str) -> None:
        self._run(
            [
                "security",
                "add-generic-password",
                "-a",
                secret_id,
                "-s",
                self._service,
                "-U",
                "-w",
            ],
            input=value,
        )

    def delete(self, secret_id: str) -> None:
        self._run(["security", "delete-generic-password", "-a", secret_id, "-s", self._service])

    def _run(self, args: list[str], *, input: str | None = None) -> object:
        try:
            return self._runner.run(
                args,
                input=input,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
        except Exception as exc:
            del exc
            raise SecretStoreError("Secure storage operation failed.") from None


class FileSecretStore:
    """Explicit-only local fallback with strict modes outside the source tree."""

    backend = "file"

    def __init__(self, *, root: Path, source_root: Path, enabled: bool = False) -> None:
        if not enabled:
            raise ValueError("File secret fallback is disabled.")
        resolved_root = root.resolve()
        resolved_source = source_root.resolve()
        if resolved_root == resolved_source or resolved_source in resolved_root.parents:
            raise ValueError("File secret fallback must be outside the source tree.")
        self._root = resolved_root
        try:
            self._root.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(self._root, 0o700)
        except OSError as exc:
            raise SecretStoreError("Secure storage operation failed.") from exc

    def __repr__(self) -> str:
        return "<FileSecretStore backend='file'>"

    def get(self, secret_id: str) -> str:
        try:
            return self._path_for(secret_id).read_text(encoding="utf-8")
        except OSError as exc:
            raise SecretStoreError("Secret is unavailable.") from exc

    def set(self, secret_id: str, value: str) -> None:
        path = self._path_for(secret_id)
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(value)
            os.chmod(path, 0o600)
        except OSError as exc:
            raise SecretStoreError("Secure storage operation failed.") from exc

    def delete(self, secret_id: str) -> None:
        try:
            self._path_for(secret_id).unlink(missing_ok=True)
        except OSError as exc:
            raise SecretStoreError("Secure storage operation failed.") from exc

    def _path_for(self, secret_id: str) -> Path:
        encoded = secret_id.encode("utf-8").hex()
        return self._root / f"{encoded}.secret"
