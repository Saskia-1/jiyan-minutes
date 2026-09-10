"""Secret stores that keep values out of public and persistent metadata."""

from __future__ import annotations

import logging
import os
from pathlib import Path
import pty
import subprocess
import tty
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from chat_to_howto.domain.consent import ConsentRecord, SecretAccessScope, SecretOperation


LOGGER = logging.getLogger(__name__)


class SecretStoreError(RuntimeError):
    """A deliberately detail-free storage failure."""


class CommandRunner(Protocol):
    def run(self, args: list[str], **kwargs: object) -> object: ...


class SecretStore(Protocol):
    """Storage interface whose public methods never serialize secret values."""

    def get(self, *, access: SecretAccessScope, consent: ConsentRecord) -> str: ...

    def set(self, *, access: SecretAccessScope, consent: ConsentRecord, value: str) -> None: ...

    def delete(self, *, access: SecretAccessScope, consent: ConsentRecord) -> None: ...


class SecretReference(BaseModel):
    """Persistable metadata pointing to a secret held by a dedicated backend."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    secret_id: str
    backend: str


def _require_authorized_access(
    *,
    access: SecretAccessScope,
    consent: ConsentRecord | None,
    operation: SecretOperation,
    backend: str,
) -> None:
    """Reject absent, stale, declined, or differently-scoped authorization."""
    if (
        consent is None
        or access.operation is not operation
        or access.secret_backend != backend
        or not consent.matches(access)
    ):
        raise SecretStoreError("Secret access denied.")


class InMemorySecretStore:
    """Test-only store; values remain process-local and are omitted from repr."""

    backend = "memory"

    def __init__(self) -> None:
        self._values: dict[str, str] = {}

    def __repr__(self) -> str:
        return "<InMemorySecretStore backend='memory'>"

    def get(self, *, access: SecretAccessScope, consent: ConsentRecord) -> str:
        _require_authorized_access(
            access=access, consent=consent, operation=SecretOperation.GET, backend=self.backend
        )
        try:
            return self._values[access.secret_reference]
        except KeyError as exc:
            raise SecretStoreError("Secret is unavailable.") from None

    def set(self, *, access: SecretAccessScope, consent: ConsentRecord | None, value: str) -> None:
        _require_authorized_access(
            access=access, consent=consent, operation=SecretOperation.SET, backend=self.backend
        )
        self._values[access.secret_reference] = value

    def delete(self, *, access: SecretAccessScope, consent: ConsentRecord) -> None:
        _require_authorized_access(
            access=access, consent=consent, operation=SecretOperation.DELETE, backend=self.backend
        )
        self._values.pop(access.secret_reference, None)

    def reference_for(self, *, access: SecretAccessScope, consent: ConsentRecord) -> SecretReference:
        _require_authorized_access(
            access=access, consent=consent, operation=SecretOperation.SET, backend=self.backend
        )
        if access.secret_reference not in self._values:
            raise SecretStoreError("Secret is unavailable.")
        return SecretReference(secret_id=access.secret_reference, backend=self.backend)

    def record_reference(
        self,
        connection: object,
        reference: SecretReference,
        *,
        access: SecretAccessScope,
        consent: ConsentRecord,
    ) -> None:
        _require_authorized_access(
            access=access, consent=consent, operation=SecretOperation.SET, backend=self.backend
        )
        if reference.secret_id != access.secret_reference or reference.backend != self.backend:
            raise SecretStoreError("Secret access denied.")
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

    def __init__(
        self,
        *,
        service: str,
        keychain_path: Path | None = None,
        runner: CommandRunner | None = None,
    ) -> None:
        self._service = service
        self._keychain_path = keychain_path
        self._runner = runner or subprocess
        self._uses_system_runner = runner is None

    def __repr__(self) -> str:
        return "<KeychainSecretStore backend='keychain'>"

    def get(self, *, access: SecretAccessScope, consent: ConsentRecord) -> str:
        _require_authorized_access(
            access=access, consent=consent, operation=SecretOperation.GET, backend=self.backend
        )
        result = self._run(
            self._command(
                ["find-generic-password", "-a", access.secret_reference, "-s", self._service],
                prompt_password=False,
            )
        )
        value = getattr(result, "stdout", "")
        if not isinstance(value, str):
            raise SecretStoreError("Secret is unavailable.")
        return value.rstrip("\n")

    def set(self, *, access: SecretAccessScope, consent: ConsentRecord, value: str) -> None:
        _require_authorized_access(
            access=access, consent=consent, operation=SecretOperation.SET, backend=self.backend
        )
        self._run(
            self._command(
                ["add-generic-password", "-a", access.secret_reference, "-s", self._service, "-U"],
                prompt_password=True,
            ),
            input=value,
        )

    def delete(self, *, access: SecretAccessScope, consent: ConsentRecord) -> None:
        _require_authorized_access(
            access=access, consent=consent, operation=SecretOperation.DELETE, backend=self.backend
        )
        self._run(
            self._command(
                ["delete-generic-password", "-a", access.secret_reference, "-s", self._service],
                prompt_password=False,
            )
        )

    def _command(self, arguments: list[str], *, prompt_password: bool) -> list[str]:
        command = ["security", *arguments]
        if self._keychain_path is not None:
            command.append(str(self._keychain_path))
        if prompt_password:
            command.append("-w")
        return command

    def _run(self, args: list[str], *, input: str | None = None) -> object:
        try:
            if input is not None and self._uses_system_runner:
                return self._run_password_prompt(args, input)
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

    @staticmethod
    def _run_password_prompt(args: list[str], value: str) -> subprocess.CompletedProcess[str]:
        """Feed the `security -w` prompt through a TTY, never an argv element."""
        child_pid, master_fd = pty.fork()
        if child_pid == 0:
            os.execvp(args[0], args)
        try:
            tty.setraw(master_fd)
            os.write(master_fd, f"{value}\n".encode("utf-8"))
            output = bytearray()
            while True:
                try:
                    chunk = os.read(master_fd, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                output.extend(chunk)
            _, status = os.waitpid(child_pid, 0)
            return_code = os.waitstatus_to_exitcode(status)
            if return_code:
                raise subprocess.CalledProcessError(
                    return_code, args, output=bytes(output), stderr=b""
                )
            return subprocess.CompletedProcess(args, return_code, "", "")
        finally:
            os.close(master_fd)


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

    def get(self, *, access: SecretAccessScope, consent: ConsentRecord) -> str:
        _require_authorized_access(
            access=access, consent=consent, operation=SecretOperation.GET, backend=self.backend
        )
        try:
            return self._path_for(access.secret_reference).read_text(encoding="utf-8")
        except OSError as exc:
            raise SecretStoreError("Secret is unavailable.") from exc

    def set(self, *, access: SecretAccessScope, consent: ConsentRecord, value: str) -> None:
        _require_authorized_access(
            access=access, consent=consent, operation=SecretOperation.SET, backend=self.backend
        )
        path = self._path_for(access.secret_reference)
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(value)
            os.chmod(path, 0o600)
        except OSError as exc:
            raise SecretStoreError("Secure storage operation failed.") from exc

    def delete(self, *, access: SecretAccessScope, consent: ConsentRecord) -> None:
        _require_authorized_access(
            access=access, consent=consent, operation=SecretOperation.DELETE, backend=self.backend
        )
        try:
            self._path_for(access.secret_reference).unlink(missing_ok=True)
        except OSError as exc:
            raise SecretStoreError("Secure storage operation failed.") from exc

    def _path_for(self, secret_id: str) -> Path:
        encoded = secret_id.encode("utf-8").hex()
        return self._root / f"{encoded}.secret"
