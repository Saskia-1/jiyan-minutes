"""Verify a committed P1 artifact set before one transactional local import."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timezone
from hashlib import sha256
import json
import os
import re
import stat
from typing import BinaryIO

from pydantic import ValidationError

from chat_to_howto.domain.jobs import ExportScope
from chat_to_howto.domain.manifest import ArtifactDigest, BatchManifest
from chat_to_howto.domain.messages import MessageInput, StoredMessage
from chat_to_howto.repositories.chat import ChatRepository, SnapshotRef
from chat_to_howto.services.artifacts import ApprovedOutputRoot


_OPAQUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_DIRECTORY_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


@dataclass(frozen=True, slots=True)
class ImportLimits:
    max_manifest_bytes: int = 4 * 1024 * 1024
    max_jsonl_bytes: int = 512 * 1024 * 1024
    max_txt_bytes: int = 512 * 1024 * 1024
    max_line_bytes: int = 8 * 1024 * 1024
    max_messages: int = 2_000_000

    def __post_init__(self) -> None:
        if min(
            self.max_manifest_bytes,
            self.max_jsonl_bytes,
            self.max_txt_bytes,
            self.max_line_bytes,
            self.max_messages,
        ) <= 0:
            raise ValueError("import limits must be positive")


class BatchImporter:
    """Reads only a named batch below a still-bound approved output root."""

    def __init__(
        self,
        repository: ChatRepository,
        approved_root: ApprovedOutputRoot,
        *,
        limits: ImportLimits | None = None,
    ) -> None:
        self._repository = repository
        self._root = approved_root
        self._limits = limits or ImportLimits()

    def import_verified(
        self,
        *,
        batch_id: str,
        expected_scope: ExportScope,
    ) -> SnapshotRef:
        if not _OPAQUE_ID.fullmatch(batch_id) or batch_id in {".", ".."}:
            raise ValueError("opaque batch ID is required")
        if expected_scope.output_root_id != self._root.root_id:
            raise PermissionError("expected scope does not match the approved output root")

        root_fd = self._root.open_bound()
        exports_fd: int | None = None
        batch_fd: int | None = None
        try:
            exports_fd = os.open("exports", _DIRECTORY_FLAGS, dir_fd=root_fd)
            batch_fd = os.open(batch_id, _DIRECTORY_FLAGS, dir_fd=exports_fd)
            batch_stat = os.fstat(batch_fd)
            if not stat.S_ISDIR(batch_stat.st_mode) or stat.S_IMODE(batch_stat.st_mode) != 0o700:
                raise PermissionError("committed batch directory is not private")

            manifest_payload = _read_bounded_regular(
                batch_fd,
                "manifest.json",
                maximum=self._limits.max_manifest_bytes,
            )
            try:
                manifest = BatchManifest.model_validate_json(manifest_payload)
            except ValidationError as error:
                raise ValueError("manifest schema validation failed") from error
            if manifest.export_batch_id != batch_id:
                raise ValueError("manifest batch identity does not match the requested batch")
            if manifest.output_root_id != self._root.root_id:
                raise PermissionError("manifest output root does not match the approved output root")
            if manifest.scope_fingerprint != expected_scope.fingerprint():
                raise ValueError("manifest scope fingerprint does not match the expected scope")
            _validate_manifest_scope(manifest, expected_scope)

            digests = {item.relative_path: item for item in manifest.artifacts}
            _verify_digest(
                batch_fd,
                digests["messages.txt"],
                maximum=self._limits.max_txt_bytes,
            )
            messages = _read_jsonl(
                batch_fd,
                digests["messages.jsonl"],
                manifest=manifest,
                scope=expected_scope,
                limits=self._limits,
            )
            if len(messages) != manifest.saved_count:
                raise ValueError("manifest saved count does not match JSONL records")
        finally:
            if batch_fd is not None:
                os.close(batch_fd)
            if exports_fd is not None:
                os.close(exports_fd)
            os.close(root_fd)

        # Parsing and filesystem verification complete before this single
        # repository transaction; no corrupt prefix can be committed.
        return self._repository.import_snapshot_atomic(
            batch_id=batch_id,
            snapshot_id=manifest.snapshot_id,
            scope=expected_scope,
            messages=messages,
            manifest=manifest,
        )


def _open_regular(directory_fd: int, name: str, maximum: int) -> tuple[int, os.stat_result]:
    descriptor = os.open(name, os.O_RDONLY | _NOFOLLOW, dir_fd=directory_fd)
    metadata = os.fstat(descriptor)
    if not stat.S_ISREG(metadata.st_mode):
        os.close(descriptor)
        raise ValueError("artifact must be a regular file")
    if stat.S_IMODE(metadata.st_mode) != 0o600:
        os.close(descriptor)
        raise PermissionError("artifact file is not private")
    if metadata.st_size > maximum:
        os.close(descriptor)
        raise ValueError("artifact exceeds the configured byte limit")
    return descriptor, metadata


def _read_bounded_regular(directory_fd: int, name: str, *, maximum: int) -> bytes:
    descriptor, metadata = _open_regular(directory_fd, name, maximum)
    try:
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            payload = stream.read(maximum + 1)
        if len(payload) != metadata.st_size or len(payload) > maximum:
            raise ValueError("artifact changed or exceeded the configured byte limit")
        return payload
    finally:
        os.close(descriptor)


def _verify_stream(stream: BinaryIO, expected: ArtifactDigest, maximum: int) -> None:
    digest = sha256()
    size = 0
    while chunk := stream.read(1024 * 1024):
        size += len(chunk)
        if size > maximum:
            raise ValueError("artifact exceeds the configured byte limit")
        digest.update(chunk)
    if size != expected.byte_size:
        raise ValueError("artifact size does not match the manifest")
    if digest.hexdigest() != expected.sha256:
        raise ValueError("artifact hash does not match the manifest")


def _verify_digest(directory_fd: int, expected: ArtifactDigest, *, maximum: int) -> None:
    descriptor, _ = _open_regular(directory_fd, expected.relative_path, maximum)
    try:
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            _verify_stream(stream, expected, maximum)
    finally:
        os.close(descriptor)


def _read_jsonl(
    directory_fd: int,
    expected: ArtifactDigest,
    *,
    manifest: BatchManifest,
    scope: ExportScope,
    limits: ImportLimits,
) -> tuple[StoredMessage, ...]:
    descriptor, _ = _open_regular(directory_fd, expected.relative_path, limits.max_jsonl_bytes)
    try:
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            # Hash this exact open inode before parsing it. The private batch
            # directory and retained fd prevent path traversal and symlink swaps.
            _verify_stream(stream, expected, limits.max_jsonl_bytes)
            stream.seek(0)
            messages: list[StoredMessage] = []
            seen_ids: set[str] = set()
            line_number = 0
            while True:
                raw_line = stream.readline(limits.max_line_bytes + 1)
                if not raw_line:
                    break
                line_number += 1
                if len(raw_line) > limits.max_line_bytes:
                    raise ValueError(f"JSONL line {line_number} exceeds the configured limit")
                if not raw_line.endswith(b"\n"):
                    raise ValueError(f"JSONL line {line_number} is not newline terminated")
                if not raw_line.strip():
                    raise ValueError(f"JSONL line {line_number} is empty")
                if len(messages) >= limits.max_messages:
                    raise ValueError("JSONL record count exceeds the configured limit")
                message = _parse_record(
                    raw_line,
                    line_number=line_number,
                    manifest=manifest,
                    scope=scope,
                )
                if message.message_id in seen_ids:
                    raise ValueError("JSONL contains a duplicate stable message record")
                seen_ids.add(message.message_id)
                messages.append(message)
            return tuple(messages)
    finally:
        os.close(descriptor)


def _parse_record(
    raw_line: bytes,
    *,
    line_number: int,
    manifest: BatchManifest,
    scope: ExportScope,
) -> StoredMessage:
    try:
        decoded = raw_line.decode("utf-8")
        record = json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"JSONL record {line_number} is not valid UTF-8 JSON") from error
    if not isinstance(record, dict):
        raise ValueError(f"JSONL record {line_number} must be an object")
    if record.pop("export_batch_id", None) != manifest.export_batch_id:
        raise ValueError(f"JSONL record {line_number} has the wrong batch identity")
    if record.pop("snapshot_id", None) != manifest.snapshot_id:
        raise ValueError(f"JSONL record {line_number} has the wrong snapshot identity")
    try:
        # Strict models intentionally reject Python string-to-datetime coercion;
        # JSON validation still accepts the schema's RFC 3339 representation.
        message = StoredMessage.model_validate_json(
            json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        )
    except ValidationError as error:
        raise ValueError(f"JSONL record {line_number} failed schema validation") from error

    if (message.source_id, message.account_id) != (scope.source_id, scope.account_id):
        raise ValueError(f"JSONL record {line_number} is outside the expected scope")
    if message.source_account_namespace != scope.source_account_namespace:
        raise ValueError(f"JSONL record {line_number} is outside the expected account namespace")
    selected = {item.conversation_id for item in scope.conversations}
    if message.conversation_id not in selected:
        raise ValueError(f"JSONL record {line_number} conversation was not selected")

    payload = message.model_dump()
    message_id = payload.pop("message_id")
    # Older in-development payloads may have carried membership. It is never
    # accepted as part of stable identity, even when the model still knows it.
    payload.pop("export_batch_id", None)
    try:
        normalized = MessageInput.model_validate(payload)
    except ValidationError as error:
        raise ValueError(f"JSONL record {line_number} failed stable identity validation") from error
    if normalized.derive_message_id() != message_id:
        raise ValueError(f"JSONL record {line_number} has an invalid stable message ID")
    if message.sent_at_utc is None:
        raise ValueError(f"JSONL record {line_number} has no precise timestamp")
    moment = message.sent_at_utc.astimezone(scope.range_start.tzinfo)
    if moment < scope.range_start or moment >= scope.range_end:
        raise ValueError(f"JSONL record {line_number} is outside the expected range")
    return message


def _validate_manifest_scope(manifest: BatchManifest, scope: ExportScope) -> None:
    """Cross-check every redundant manifest scope field, not only its hash label."""
    expected_conversations = tuple(
        (item.conversation_id, item.display_name, item.conversation_type)
        for item in scope.conversations
    )
    actual_conversations = tuple(
        (item.conversation_id, item.display_name, item.conversation_type)
        for item in manifest.conversations
    )
    scalar_values = (
        (manifest.source_id, scope.source_id),
        (manifest.account_id, scope.account_id),
        (manifest.source_account_namespace, scope.source_account_namespace),
        (manifest.client_version, scope.client_version),
        (manifest.source_tool_version, scope.tool_version),
        (manifest.account_version, scope.account_version),
        (manifest.display_timezone, scope.display_timezone),
        (
            manifest.requested_start_utc,
            scope.range_start.astimezone(timezone.utc),
        ),
        (
            manifest.requested_end_utc,
            scope.range_end.astimezone(timezone.utc),
        ),
    )
    if any(actual != expected for actual, expected in scalar_values):
        raise ValueError("manifest fields do not match the expected scope")
    if actual_conversations != expected_conversations:
        raise ValueError("manifest conversations do not match the expected scope")
