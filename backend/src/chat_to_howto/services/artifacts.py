"""Private, atomic, no-overwrite P1 JSONL/TXT/manifest artifact writer."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import ctypes
import errno
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Callable, Iterable, Mapping
from uuid import uuid4

from chat_to_howto.domain.jobs import ExportScope
from chat_to_howto.domain.manifest import (
    ArtifactDigest,
    BatchManifest,
    CompletenessStatus,
    ConversationEvidenceManifest,
    ConversationManifest,
    SliceManifest,
)
from chat_to_howto.domain.messages import StoredMessage
from chat_to_howto.sources.base import CompletenessEvidence


_OPAQUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_FILE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL
_DIRECTORY_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


@dataclass(frozen=True, slots=True)
class ApprovedOutputRoot:
    """A user-approved canonical directory bound to its filesystem identity."""

    path: Path
    root_id: str
    device: int
    inode: int

    @classmethod
    def approve(
        cls,
        path: Path,
        *,
        root_id: str,
        repository_root: Path,
    ) -> "ApprovedOutputRoot":
        if not _is_opaque(root_id):
            raise ValueError("opaque output root ID is required")
        candidate = Path(path).expanduser().absolute()
        try:
            leaf = candidate.lstat()
        except FileNotFoundError as error:
            raise ValueError("approved output root must already exist") from error
        if stat.S_ISLNK(leaf.st_mode):
            raise ValueError("approved output root cannot be a symbolic link")
        if not stat.S_ISDIR(leaf.st_mode):
            raise ValueError("approved output root must be a directory")
        canonical = candidate.resolve(strict=True)
        repository = Path(repository_root).expanduser().resolve(strict=True)
        if canonical == repository or canonical.is_relative_to(repository):
            raise ValueError("approved output root must remain outside the repository")
        return cls(
            path=canonical,
            root_id=root_id,
            device=leaf.st_dev,
            inode=leaf.st_ino,
        )

    def open_bound(self) -> int:
        """Open only the still-present approved inode, never a replacement path."""
        current = self.path.lstat()
        if stat.S_ISLNK(current.st_mode):
            raise PermissionError("approved output root identity changed")
        if (current.st_dev, current.st_ino) != (self.device, self.inode):
            raise PermissionError("approved output root identity changed")
        descriptor = os.open(self.path, _DIRECTORY_FLAGS)
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (self.device, self.inode):
            os.close(descriptor)
            raise PermissionError("approved output root identity changed")
        return descriptor


@dataclass(frozen=True, slots=True)
class BatchArtifacts:
    directory: Path
    jsonl_path: Path
    txt_path: Path
    manifest_path: Path
    manifest: BatchManifest


class ArtifactWriter:
    """Write a complete batch behind an atomic, exclusive directory commit."""

    def __init__(
        self,
        approved_root: ApprovedOutputRoot,
        *,
        fault_hook: Callable[[str], None] | None = None,
    ) -> None:
        self._root = approved_root
        self._fault_hook = fault_hook or (lambda _stage: None)

    def write_batch(
        self,
        *,
        batch_id: str,
        snapshot_id: str,
        scope: ExportScope,
        messages: Iterable[StoredMessage],
        conversation_evidence: Mapping[str, CompletenessEvidence],
        source_type: str,
        created_at: datetime | None = None,
    ) -> BatchArtifacts:
        if not _is_opaque(batch_id):
            raise ValueError("opaque batch ID is required")
        if not _is_opaque(snapshot_id):
            raise ValueError("opaque snapshot ID is required")
        if scope.output_root_id != self._root.root_id:
            raise PermissionError("export scope does not match the approved output root")
        if source_type not in {"qq", "wechat", "synthetic", "manual"}:
            raise ValueError("unsupported source type")

        ordered = self._validate_and_order(scope, tuple(messages))
        selected_ids = tuple(item.conversation_id for item in scope.conversations)
        if set(conversation_evidence) != set(selected_ids):
            raise ValueError("evidence must match every selected conversation exactly")
        evidence = tuple(conversation_evidence[item] for item in selected_ids)
        self._validate_evidence(scope, evidence)

        root_fd = self._root.open_bound()
        exports_fd: int | None = None
        temporary_fd: int | None = None
        temporary_name = f".{batch_id}.tmp-{uuid4().hex}"
        temporary_inode: tuple[int, int] | None = None
        committed = False
        try:
            exports_fd = _open_or_create_private_directory(root_fd, "exports")
            try:
                os.stat(batch_id, dir_fd=exports_fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise FileExistsError(errno.EEXIST, "export batch already exists", batch_id)

            os.mkdir(temporary_name, mode=0o700, dir_fd=exports_fd)
            temporary_fd = os.open(temporary_name, _DIRECTORY_FLAGS, dir_fd=exports_fd)
            opened_temp = os.fstat(temporary_fd)
            temporary_inode = (opened_temp.st_dev, opened_temp.st_ino)
            os.fchmod(temporary_fd, 0o700)

            jsonl_digest = self._write_jsonl(
                temporary_fd,
                batch_id=batch_id,
                snapshot_id=snapshot_id,
                messages=ordered,
            )
            self._fault_hook("after_jsonl")
            txt_digest = self._write_txt(temporary_fd, scope=scope, messages=ordered)
            self._fault_hook("after_txt")

            manifest = _build_manifest(
                batch_id=batch_id,
                snapshot_id=snapshot_id,
                scope=scope,
                source_type=source_type,
                messages=ordered,
                evidence=evidence,
                artifacts=(jsonl_digest, txt_digest),
                created_at=created_at or datetime.now(timezone.utc),
            )
            manifest_payload = json.dumps(
                manifest.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8") + b"\n"
            _write_untracked_private_file(temporary_fd, "manifest.json", (manifest_payload,))
            self._fault_hook("after_manifest")

            _verify_artifact(temporary_fd, jsonl_digest)
            _verify_artifact(temporary_fd, txt_digest)
            BatchManifest.model_validate_json(_read_small_file(temporary_fd, "manifest.json"))
            os.fsync(temporary_fd)
            self._fault_hook("before_commit")
            _rename_noreplace(exports_fd, temporary_name, batch_id)
            committed = True
            os.fsync(exports_fd)
        finally:
            if temporary_fd is not None:
                os.close(temporary_fd)
            if not committed and exports_fd is not None and temporary_inode is not None:
                _remove_owned_temp(exports_fd, temporary_name, temporary_inode)
            if exports_fd is not None:
                os.close(exports_fd)
            os.close(root_fd)

        # Refuse to report a path as successful if the approved path was swapped
        # during the operation. All actual writes above remained bound to the fd.
        check_fd = self._root.open_bound()
        os.close(check_fd)
        directory = self._root.path / "exports" / batch_id
        return BatchArtifacts(
            directory=directory,
            jsonl_path=directory / "messages.jsonl",
            txt_path=directory / "messages.txt",
            manifest_path=directory / "manifest.json",
            manifest=manifest,
        )

    @staticmethod
    def _validate_and_order(
        scope: ExportScope, messages: tuple[StoredMessage, ...]
    ) -> tuple[StoredMessage, ...]:
        selected_order = {
            item.conversation_id: position for position, item in enumerate(scope.conversations)
        }
        for message in messages:
            if (message.source_id, message.account_id) != (scope.source_id, scope.account_id):
                raise ValueError("message is outside the authorized source/account scope")
            if message.source_account_namespace != scope.source_account_namespace:
                raise ValueError("message account namespace is outside the authorized scope")
            if message.conversation_id not in selected_order:
                raise ValueError("message conversation was not selected")
            if message.sent_at_utc is None:
                raise ValueError("a precise export cannot save a message without a timestamp")
            timestamp = message.sent_at_utc.astimezone(timezone.utc)
            if timestamp < scope.range_start.astimezone(timezone.utc) or timestamp >= scope.range_end.astimezone(timezone.utc):
                raise ValueError("message is outside the authorized range")
        return tuple(
            sorted(
                messages,
                key=lambda item: (
                    selected_order[item.conversation_id],
                    item.sent_at_utc.astimezone(timezone.utc) if item.sent_at_utc else datetime.max.replace(tzinfo=timezone.utc),
                    item.source_order if item.source_order is not None else 2**63 - 1,
                    item.message_id,
                ),
            )
        )

    @staticmethod
    def _validate_evidence(
        scope: ExportScope, evidence: tuple[CompletenessEvidence, ...]
    ) -> None:
        start_ms = int(scope.range_start.astimezone(timezone.utc).timestamp() * 1000)
        end_ms = int(scope.range_end.astimezone(timezone.utc).timestamp() * 1000)
        for item in evidence:
            if item.requested_start_ms is not None and item.requested_start_ms != start_ms:
                raise ValueError("evidence requested range does not match export scope")
            if item.requested_end_ms is not None and item.requested_end_ms != end_ms:
                raise ValueError("evidence requested range does not match export scope")

    @staticmethod
    def _write_jsonl(
        directory_fd: int,
        *,
        batch_id: str,
        snapshot_id: str,
        messages: tuple[StoredMessage, ...],
    ) -> ArtifactDigest:
        def chunks() -> Iterable[bytes]:
            for message in messages:
                record = message.model_dump(mode="json")
                # Batch membership is intentionally artifact metadata, not part
                # of the stable message identity stored by MessageInput.
                record["export_batch_id"] = batch_id
                record["snapshot_id"] = snapshot_id
                yield json.dumps(
                    record,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8") + b"\n"

        return _write_private_file(directory_fd, "messages.jsonl", chunks())

    @staticmethod
    def _write_txt(
        directory_fd: int,
        *,
        scope: ExportScope,
        messages: tuple[StoredMessage, ...],
    ) -> ArtifactDigest:
        names = {item.conversation_id: item.display_name for item in scope.conversations}

        def chunks() -> Iterable[bytes]:
            for message in messages:
                timestamp = message.sent_at_utc.astimezone(timezone.utc).isoformat()  # validated above
                conversation = names.get(message.conversation_id) or message.conversation_id
                sender = message.sender_name or message.sender_id or "未知发送者"
                body = message.text if message.text is not None else "[非文本消息]"
                yield f"[{timestamp}] [{conversation}] {sender}: {body}\n".encode("utf-8")
                for attachment in message.attachments or ():
                    kind = attachment.kind or "unknown"
                    availability = attachment.availability or "unknown"
                    yield f"  [附件: {kind}; {availability}]\n".encode("utf-8")

        return _write_private_file(directory_fd, "messages.txt", chunks())


def _is_opaque(value: str) -> bool:
    return bool(value and value not in {".", ".."} and _OPAQUE_ID.fullmatch(value))


def _open_or_create_private_directory(parent_fd: int, name: str) -> int:
    created = False
    try:
        os.mkdir(name, mode=0o700, dir_fd=parent_fd)
        created = True
    except FileExistsError:
        pass
    descriptor = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent_fd)
    metadata = os.fstat(descriptor)
    if not stat.S_ISDIR(metadata.st_mode):
        os.close(descriptor)
        raise NotADirectoryError(name)
    if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) & 0o077:
        os.close(descriptor)
        raise PermissionError("managed artifact directory is not private")
    if created:
        os.fchmod(descriptor, 0o700)
        os.fsync(parent_fd)
    return descriptor


def _write_private_file(
    directory_fd: int, name: str, chunks: Iterable[bytes]
) -> ArtifactDigest:
    size, digest = _write_untracked_private_file(directory_fd, name, chunks)
    return ArtifactDigest(relative_path=name, byte_size=size, sha256=digest)


def _write_untracked_private_file(
    directory_fd: int, name: str, chunks: Iterable[bytes]
) -> tuple[int, str]:
    descriptor = os.open(name, _FILE_FLAGS | _NOFOLLOW, 0o600, dir_fd=directory_fd)
    digest = sha256()
    size = 0
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            for chunk in chunks:
                stream.write(chunk)
                digest.update(chunk)
                size += len(chunk)
            stream.flush()
            os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return size, digest.hexdigest()


def _read_small_file(directory_fd: int, name: str, maximum: int = 8 * 1024 * 1024) -> bytes:
    descriptor = os.open(name, os.O_RDONLY | _NOFOLLOW, dir_fd=directory_fd)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > maximum:
            raise ValueError("artifact file is not a bounded regular file")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            return stream.read(maximum + 1)
    finally:
        os.close(descriptor)


def _verify_artifact(directory_fd: int, expected: ArtifactDigest) -> None:
    descriptor = os.open(expected.relative_path, os.O_RDONLY | _NOFOLLOW, dir_fd=directory_fd)
    digest = sha256()
    size = 0
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o600:
            raise PermissionError("artifact permissions are not private")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
    finally:
        os.close(descriptor)
    if size != expected.byte_size or digest.hexdigest() != expected.sha256:
        raise OSError(errno.EIO, "artifact verification failed")


def _nullable_sum(values: Iterable[int | None]) -> int | None:
    collected = tuple(values)
    if any(value is None for value in collected):
        return None
    return sum(value for value in collected if value is not None)


def _ms_to_utc(value: int | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc)


def _aggregate_status(evidence: tuple[CompletenessEvidence, ...]) -> CompletenessStatus:
    statuses = {item.status for item in evidence}
    if statuses == {"empty"}:
        return "empty"
    if "unknown" in statuses or "empty" in statuses:
        return "unknown"
    if "partial" in statuses:
        return "partial"
    return "source_range_verified"


def _build_manifest(
    *,
    batch_id: str,
    snapshot_id: str,
    scope: ExportScope,
    source_type: str,
    messages: tuple[StoredMessage, ...],
    evidence: tuple[CompletenessEvidence, ...],
    artifacts: tuple[ArtifactDigest, ArtifactDigest],
    created_at: datetime,
) -> BatchManifest:
    status = _aggregate_status(evidence)
    timestamps = tuple(
        message.sent_at_utc.astimezone(timezone.utc)
        for message in messages
        if message.sent_at_utc is not None
    )
    observed_starts = tuple(
        value for value in (_ms_to_utc(item.observed_start_ms) for item in evidence) if value is not None
    )
    observed_ends = tuple(
        value for value in (_ms_to_utc(item.observed_end_ms) for item in evidence) if value is not None
    )
    evidence_models = tuple(
        ConversationEvidenceManifest(
            conversation_id=conversation.conversation_id,
            status=item.status,
            reason=item.reason,
            requested_start_ms=item.requested_start_ms,
            requested_end_ms=item.requested_end_ms,
            observed_start_ms=item.observed_start_ms,
            observed_end_ms=item.observed_end_ms,
            slices=tuple(SliceManifest(**asdict(slice_item)) for slice_item in item.slices),
            fetched_count=item.fetched_count,
            accepted_count=item.accepted_count,
            duplicate_count=item.duplicate_count,
            unknown_time_count=item.unknown_time_count,
            missing_id_count=item.missing_id_count,
            parse_error_count=item.parse_error_count,
            warnings=item.warnings,
            termination=item.termination,
        )
        for conversation, item in zip(scope.conversations, evidence, strict=True)
    )
    warnings = tuple(dict.fromkeys(code for item in evidence for code in item.warnings))
    limitations = tuple(
        dict.fromkeys(
            item.reason for item in evidence if item.status in {"partial", "unknown"}
        )
    )
    return BatchManifest(
        export_batch_id=batch_id,
        snapshot_id=snapshot_id,
        artifact_set_id=f"artifacts:{batch_id}",
        scope_fingerprint=scope.fingerprint(),
        output_root_id=scope.output_root_id,
        created_at_utc=created_at,
        source_type=source_type,  # type: ignore[arg-type]
        source_id=scope.source_id,
        source_tool_version=scope.tool_version,
        client_version=scope.client_version,
        account_id=scope.account_id,
        source_account_namespace=scope.source_account_namespace,
        account_version=scope.account_version,
        conversations=tuple(
            ConversationManifest(
                conversation_id=item.conversation_id,
                display_name=item.display_name,
                conversation_type=item.conversation_type,
            )
            for item in scope.conversations
        ),
        requested_start_utc=scope.range_start,
        requested_end_utc=scope.range_end,
        observed_start_utc=min(observed_starts) if observed_starts else None,
        observed_end_utc=max(observed_ends) if observed_ends else None,
        display_timezone=scope.display_timezone,
        fetched_count=_nullable_sum(item.fetched_count for item in evidence),
        saved_count=len(messages),
        duplicate_count=_nullable_sum(item.duplicate_count for item in evidence),
        unknown_time_count=_nullable_sum(item.unknown_time_count for item in evidence),
        parse_error_count=_nullable_sum(item.parse_error_count for item in evidence),
        earliest_saved_at_utc=min(timestamps) if timestamps else None,
        latest_saved_at_utc=max(timestamps) if timestamps else None,
        completeness=status,
        completeness_reason="all_conversations_explicitly_empty" if status == "empty" else "conservative_per_conversation_aggregation",
        limitations=limitations,
        warnings=warnings,
        conversation_evidence=evidence_models,
        artifacts=artifacts,
    )


def _rename_noreplace(parent_fd: int, source: str, destination: str) -> None:
    """Atomically rename a directory while refusing an existing destination."""
    libc = ctypes.CDLL(None, use_errno=True)
    source_bytes = os.fsencode(source)
    destination_bytes = os.fsencode(destination)
    if sys.platform == "darwin" and hasattr(libc, "renameatx_np"):
        function = libc.renameatx_np
        function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        function.restype = ctypes.c_int
        result = function(parent_fd, source_bytes, parent_fd, destination_bytes, 0x00000004)
    elif hasattr(libc, "renameat2"):
        function = libc.renameat2
        function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        function.restype = ctypes.c_int
        result = function(parent_fd, source_bytes, parent_fd, destination_bytes, 0x00000001)
    else:
        raise OSError(errno.ENOTSUP, "atomic no-overwrite rename is unavailable")
    if result != 0:
        code = ctypes.get_errno()
        if code == errno.EEXIST:
            raise FileExistsError(code, "export batch already exists", destination)
        raise OSError(code, os.strerror(code), destination)


def _remove_owned_temp(
    parent_fd: int, name: str, expected_identity: tuple[int, int]
) -> None:
    """Remove only this writer's known files from its still-matching temp inode."""
    try:
        descriptor = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent_fd)
    except FileNotFoundError:
        return
    try:
        metadata = os.fstat(descriptor)
        if (metadata.st_dev, metadata.st_ino) != expected_identity:
            return
        for child in ("messages.jsonl", "messages.txt", "manifest.json"):
            try:
                os.unlink(child, dir_fd=descriptor)
            except FileNotFoundError:
                pass
    finally:
        os.close(descriptor)
    try:
        current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if (current.st_dev, current.st_ino) == expected_identity and stat.S_ISDIR(current.st_mode):
            os.rmdir(name, dir_fd=parent_fd)
    except FileNotFoundError:
        pass
