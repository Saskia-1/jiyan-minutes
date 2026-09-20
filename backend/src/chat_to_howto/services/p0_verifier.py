"""Private, evidence-checked P0 verification over bound directory handles."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
from threading import RLock

from chat_to_howto.db import Database
from chat_to_howto.domain.consent import (
    ConsentRecord,
    ConsentScope,
    SecretAccessScope,
    SecretOperation,
)
from chat_to_howto.domain.sources import (
    SourceCapability,
    SourceFingerprint,
    SourceStatus,
)
from chat_to_howto.sources.base import FetchCancelled


_EVIDENCE_CLASSES = frozenset({"unknown", "synthetic", "real_device"})
_COMPLETENESS = frozenset({"source_range_verified", "partial", "unknown", "empty"})
_TERMINAL = frozenset({"complete", "partial", "failed", "cancelled"})
_JOB_ID = re.compile(r"[0-9a-f]{32}")
_TEMP_NAME = re.compile(r"messages-[0-9a-f]{32}\.tmp")
_OWNER_NAME = ".p0-owner"


class VerificationState(str, Enum):
    PENDING_AUTHORIZATION = "pending_authorization"
    CONNECTING = "connecting"
    READING = "reading"
    SAVING = "saving"
    VERIFYING = "verifying"
    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class _RootBinding:
    path: Path
    descriptor: int
    device: int
    inode: int


class P0Verifier:
    def __init__(
        self,
        *,
        database: Database,
        adapter_factory,
        output_roots: dict[str, Path],
        clock,
    ) -> None:
        self.database = database
        self.adapter_factory = adapter_factory
        self.output_roots = {
            key: Path(value).absolute() for key, value in output_roots.items()
        }
        self.clock = clock
        self._cancelled: set[str] = set()
        self._active: dict[str, object] = {}
        self._active_lock = RLock()
        self._closed = False
        bindings: dict[str, _RootBinding] = {}
        try:
            for root_id, root in self.output_roots.items():
                bindings[root_id] = self._bind_root(root)
        except Exception:
            for binding in bindings.values():
                os.close(binding.descriptor)
            raise
        self._root_bindings = bindings
        self._cleanup_interrupted_artifacts()

    def close(self) -> None:
        with self._active_lock:
            if self._closed:
                return
            self._closed = True
            descriptors = [binding.descriptor for binding in self._root_bindings.values()]
        for descriptor in descriptors:
            try:
                os.close(descriptor)
            except OSError:
                pass

    def cancel(self, job_id: str) -> str | None:
        state = self.database.cancel_job(job_id)
        if state != "cancelled":
            return state
        with self._active_lock:
            self._cancelled.add(job_id)
            adapter = self._active.get(job_id)
        if adapter is not None:
            try:
                adapter.cancel()
            except Exception:
                pass
        return state

    def run(self, job_id: str) -> None:
        job = self.database.one("SELECT * FROM jobs WHERE job_id=?", (job_id,))
        if job is None or job["state"] in _TERMINAL:
            return
        adapter = None
        try:
            self._check_cancelled(job_id)
            self._assert_job_root(job)
            if self.database.current_source_for_job(job) is None:
                self._fail(job_id, "source_configuration_changed")
                return
            self.database.transition_job(job_id, "connecting")
            grant = self._adapter_grant(job) if job["secret_required"] else {}
            if job["secret_required"] and not grant:
                self._fail(job_id, "secret_authorization_missing")
                return
            adapter = self.adapter_factory(job["source_id"], job["account_id"], **grant)
            self._activate(job_id, adapter)
            adapter_fingerprint = getattr(adapter, "fingerprint", None)
            if not isinstance(adapter_fingerprint, SourceFingerprint):
                self._fail(job_id, "adapter_identity_invalid")
                return
            evidence_class = getattr(adapter, "evidence_class", None)
            if type(evidence_class) is not str or evidence_class not in _EVIDENCE_CLASSES:
                self._fail(job_id, "adapter_evidence_invalid")
                return
            if adapter_fingerprint != self._fingerprint(job):
                self._fail(job_id, "version_mismatch")
                return
            if not self.database.set_job_evidence(job_id, evidence_class):
                self._fail(job_id, "source_configuration_changed")
                return
            self._check_cancelled(job_id)
            self.database.transition_job(job_id, "reading")
            self._check_cancelled(job_id)
            page = adapter.fetch_range(
                job["conversation_id"], job["start_ms"], job["end_ms"]
            )
            self._check_cancelled(job_id)
            evidence_error = self._validate_page_evidence(job, page)
            if evidence_error is not None:
                self._fail(job_id, evidence_error, range_ok=False)
                return
            self.database.transition_job(job_id, "saving")
            self._check_cancelled(job_id)
            _, final_path, digest, count, range_ok = self._write(
                self._root_bindings[job["output_root_id"]], job, page.messages
            )
            self._check_cancelled(job_id)
            self.database.transition_job(job_id, "verifying")
            self._check_cancelled(job_id)
            reopened_digest, reopened_count, reopened_range = self._reopen(
                final_path, job
            )
            if reopened_digest != digest:
                self._fail(job_id, "artifact_hash_mismatch", hash_ok=False, range_ok=False)
                self._cleanup_job(job_id)
                return
            if reopened_count != count:
                self._fail(
                    job_id,
                    "artifact_count_mismatch",
                    count=reopened_count,
                    hash_ok=True,
                    range_ok=False,
                )
                self._cleanup_job(job_id)
                return
            if not range_ok or not reopened_range:
                self._fail(
                    job_id,
                    "artifact_range_mismatch",
                    count=reopened_count,
                    hash_ok=True,
                    range_ok=False,
                )
                self._cleanup_job(job_id)
                return
            completeness = page.completeness.status
            state = "complete" if completeness == "source_range_verified" else "partial"
            if state == "complete" and evidence_class == "real_device":
                promotion = self._promote_ready(
                    job,
                    count=reopened_count,
                    digest=digest,
                    completeness=completeness,
                )
                if promotion != "promoted":
                    self._fail(job_id, promotion)
                    self._cleanup_job(job_id)
                return
            self.database.transition_job(
                job_id,
                state,
                count=reopened_count,
                sha256=digest,
                hash_ok=True,
                range_ok=True,
                completeness=completeness,
            )
        except FetchCancelled:
            self._cancel_and_cleanup(job_id)
        except _Cancelled:
            self._cancel_and_cleanup(job_id)
        except _OutputRootChanged:
            self._fail(job_id, "output_root_changed")
            self._cleanup_job(job_id)
        except _ArtifactCollision:
            self._fail(job_id, "artifact_collision")
            self._cleanup_job(job_id)
        except Exception:
            self._fail(job_id, "verification_failed")
            self._cleanup_job(job_id)
        finally:
            if adapter is not None:
                self._deactivate(job_id, adapter)

    def _activate(self, job_id: str, adapter: object) -> None:
        with self._active_lock:
            self._active[job_id] = adapter
            cancelled = job_id in self._cancelled
        if cancelled:
            try:
                adapter.cancel()
            finally:
                raise _Cancelled()

    def _deactivate(self, job_id: str, adapter: object) -> None:
        with self._active_lock:
            if self._active.get(job_id) is adapter:
                self._active.pop(job_id, None)

    def _adapter_grant(self, job: dict) -> dict:
        grant = self.database.claim_grant_access(job, now=self.clock())
        if not grant:
            return {}
        try:
            access = SecretAccessScope(
                operation=SecretOperation.GET,
                secret_reference=job["secret_ref"],
                secret_backend=job["secret_backend"],
                source_id=job["source_id"],
                account_id=job["account_id"],
                client_version=job["client_version"],
                tool_version=job["tool_version"],
                account_version=job["account_version"],
                configuration_nonce=job["nonce"],
            )
            consent = ConsentRecord(
                scope_fingerprint=grant["scope_fingerprint"],
                granted=True,
                recorded_at=datetime.fromisoformat(grant["recorded_at"]),
                expires_at=datetime.fromisoformat(grant["expires_at"]),
            )
        except (TypeError, ValueError):
            return {}
        if not consent.matches(access, now=self.clock()):
            return {}
        return {"token_access": access, "token_consent": consent}

    @staticmethod
    def _fingerprint(job):
        return SourceFingerprint(
            client_version=job["client_version"],
            tool_version=job["tool_version"],
            account_version=job["account_version"],
        )

    def _promote_ready(
        self, job: dict, *, count: int, digest: str, completeness: str
    ) -> str:
        source = self.database.current_source_for_job(job)
        if source is None:
            return "source_configuration_changed"
        try:
            status = SourceStatus(source["status"])
            if status in {SourceStatus.BLOCKED, SourceStatus.UNSUPPORTED}:
                return "readiness_evidence_invalid"
            fingerprint = self._fingerprint(job)
            scope = ConsentScope(
                source_id=job["source_id"],
                account_id=job["account_id"],
                conversation_id=job["conversation_id"],
                range_start=job["range_start"],
                range_end=job["range_end"],
                output_path=job["output_root_id"],
                operation="export_verification",
                client_version=job["client_version"],
                tool_version=job["tool_version"],
                account_version=job["account_version"],
                configuration_nonce=job["nonce"],
            )
            consent = ConsentRecord(
                scope_fingerprint=job["scope_fingerprint"],
                granted=True,
                recorded_at=datetime.fromisoformat(job["consent_recorded_at"]),
                expires_at=datetime.fromisoformat(job["consent_expires_at"]),
            )
            if not consent.matches(scope, now=self.clock()):
                return "readiness_evidence_invalid"
            capability = SourceCapability(
                source_id=job["source_id"],
                status=SourceStatus.NEEDS_USER_ACTION,
                configuration_nonce=job["nonce"],
            )
            capability.record_verification(
                fingerprint,
                current_fingerprint=SourceFingerprint(
                    client_version=source["client_version"],
                    tool_version=source["tool_version"],
                    account_version=source["account_version"],
                ),
                export_scope=scope,
                export_consent=consent,
                evidence_class="real_device",
            )
        except (TypeError, ValueError):
            return "readiness_evidence_invalid"
        if not self.database.complete_real_device_job(
            job,
            count=count,
            digest=digest,
            completeness=completeness,
            now=self.clock(),
        ):
            if self.database.current_source_for_job(job) is None:
                return "source_configuration_changed"
            return "readiness_evidence_invalid"
        return "promoted"

    @staticmethod
    def _validate_page_evidence(job: dict, page) -> str | None:
        evidence = getattr(page, "completeness", None)
        messages = getattr(page, "messages", None)
        if evidence is None or not isinstance(messages, tuple):
            return "adapter_evidence_invalid"
        if evidence.status not in _COMPLETENESS:
            return "adapter_evidence_invalid"
        if (
            type(evidence.requested_start_ms) is not int
            or type(evidence.requested_end_ms) is not int
            or evidence.requested_start_ms != job["start_ms"]
            or evidence.requested_end_ms != job["end_ms"]
        ):
            return "adapter_requested_bounds_mismatch"
        accepted = len(messages)
        if type(evidence.accepted_count) is not int or evidence.accepted_count != accepted:
            return "adapter_accepted_count_mismatch"
        if type(evidence.fetched_count) is not int or evidence.fetched_count < accepted:
            return "adapter_fetched_count_invalid"
        if evidence.slices:
            try:
                returned = sum(item.returned for item in evidence.slices)
                slice_accepted = sum(item.accepted for item in evidence.slices)
            except (AttributeError, TypeError):
                return "adapter_evidence_invalid"
            if returned != evidence.fetched_count:
                return "adapter_fetched_count_invalid"
            if slice_accepted != evidence.accepted_count:
                return "adapter_accepted_count_mismatch"
        stamps: list[int] = []
        for message in messages:
            stamp = getattr(message, "sent_at_ms", None)
            if (
                getattr(message, "conversation_id", None) != job["conversation_id"]
                or type(stamp) is not int
                or not job["start_ms"] <= stamp < job["end_ms"]
            ):
                return "adapter_range_evidence_invalid"
            stamps.append(stamp)
        observed_start = min(stamps, default=None)
        observed_end = max(stamps, default=None)
        if (
            evidence.observed_start_ms != observed_start
            or evidence.observed_end_ms != observed_end
        ):
            return "adapter_observed_range_mismatch"
        if (accepted and evidence.status == "empty") or (
            not accepted and evidence.status == "source_range_verified"
        ):
            return "adapter_empty_evidence_invalid"
        return None

    def _write(self, binding: _RootBinding, job: dict, messages):
        self._assert_root_binding(binding)
        artifact = self.database.prepare_artifact(job["job_id"])
        if artifact is None or not self._valid_artifact_names(artifact):
            raise ValueError("invalid artifact ownership metadata")
        root_fd = os.dup(binding.descriptor)
        job_fd: int | None = None
        job_created = owner_owned = temp_owned = final_owned = False
        directory_name = artifact["artifact_dir_name"]
        temporary_name = artifact["artifact_temp_name"]
        final_name = artifact["artifact_final_name"]
        owner_token = artifact["artifact_owner_token"]
        job_dir = binding.path / directory_name
        final_path = job_dir / final_name
        try:
            os.mkdir(directory_name, 0o700, dir_fd=root_fd)
            job_created = True
            job_fd = os.open(
                directory_name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=root_fd,
            )
            metadata = os.fstat(job_fd)
            if not stat.S_ISDIR(metadata.st_mode):
                raise ValueError("artifact directory is not a directory")
            owner_fd = os.open(
                _OWNER_NAME,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=job_fd,
            )
            owner_owned = True
            with os.fdopen(owner_fd, "wb") as owner_stream:
                owner_stream.write(owner_token.encode("ascii"))
                owner_stream.flush()
                os.fsync(owner_stream.fileno())
            if not self.database.mark_artifact_directory(
                job["job_id"], device=metadata.st_dev, inode=metadata.st_ino
            ):
                raise _Cancelled()
            descriptor = os.open(
                temporary_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=job_fd,
            )
            temp_owned = True
            if not self.database.mark_artifact_temp(job["job_id"], owned=True):
                raise _Cancelled()
            digest = sha256()
            count = 0
            with os.fdopen(descriptor, "wb") as stream:
                for message in messages:
                    self._check_record(job, message)
                    item = {
                        "message_id": message.message_id,
                        "conversation_id": message.conversation_id,
                        "sent_at_ms": message.sent_at_ms,
                        "text": message.text,
                        "source_order": message.source_order,
                    }
                    encoded = (
                        json.dumps(
                            item, ensure_ascii=False, separators=(",", ":")
                        ).encode()
                        + b"\n"
                    )
                    stream.write(encoded)
                    digest.update(encoded)
                    count += 1
                stream.flush()
                os.fsync(stream.fileno())
            self._assert_root_binding(binding)
            os.link(
                temporary_name,
                final_name,
                src_dir_fd=job_fd,
                dst_dir_fd=job_fd,
                follow_symlinks=False,
            )
            final_owned = True
            if not self.database.mark_artifact_final(job["job_id"], owned=True):
                raise _Cancelled()
            os.unlink(temporary_name, dir_fd=job_fd)
            temp_owned = False
            self.database.mark_artifact_temp(job["job_id"], owned=False)
            os.fsync(job_fd)
            os.fsync(root_fd)
            self._assert_root_binding(binding)
            return job_dir, final_path, digest.hexdigest(), count, True
        except FileExistsError:
            cleaned = self._cleanup_local_write(
                root_fd=root_fd,
                job_fd=job_fd,
                directory_name=directory_name,
                temporary_name=temporary_name,
                final_name=final_name,
                job_created=job_created,
                owner_owned=owner_owned,
                temp_owned=temp_owned,
                final_owned=final_owned,
            )
            if cleaned:
                self.database.mark_artifact_cleaned(job["job_id"])
            raise _ArtifactCollision() from None
        except BaseException:
            cleaned = self._cleanup_local_write(
                root_fd=root_fd,
                job_fd=job_fd,
                directory_name=directory_name,
                temporary_name=temporary_name,
                final_name=final_name,
                job_created=job_created,
                owner_owned=owner_owned,
                temp_owned=temp_owned,
                final_owned=final_owned,
            )
            if cleaned:
                self.database.mark_artifact_cleaned(job["job_id"])
            raise
        finally:
            if job_fd is not None:
                os.close(job_fd)
            os.close(root_fd)

    @staticmethod
    def _cleanup_local_write(
        *,
        root_fd: int,
        job_fd: int | None,
        directory_name: str,
        temporary_name: str,
        final_name: str,
        job_created: bool,
        owner_owned: bool,
        temp_owned: bool,
        final_owned: bool,
    ) -> bool:
        content_cleaned = True
        if job_fd is not None:
            for name, owned in (
                (temporary_name, temp_owned),
                (final_name, final_owned),
            ):
                if owned:
                    try:
                        os.unlink(name, dir_fd=job_fd)
                    except FileNotFoundError:
                        continue
                    except OSError:
                        content_cleaned = False
            # The marker is the proof needed for a later exact cleanup. Keep it
            # until every owned payload has either been removed or is absent.
            if owner_owned and content_cleaned:
                try:
                    os.unlink(_OWNER_NAME, dir_fd=job_fd)
                except FileNotFoundError:
                    pass
                except OSError:
                    content_cleaned = False
        if job_created and content_cleaned:
            try:
                os.rmdir(directory_name, dir_fd=root_fd)
            except OSError:
                pass
        return content_cleaned

    def _reopen(self, final_path: Path, job: dict):
        del final_path
        artifact = self.database.one(
            "SELECT * FROM jobs WHERE job_id=?", (job["job_id"],)
        )
        if artifact is None or not self._valid_artifact_names(artifact):
            raise ValueError("invalid artifact ownership metadata")
        binding = self._root_bindings[job["output_root_id"]]
        self._assert_root_binding(binding)
        root_fd = os.dup(binding.descriptor)
        directory_fd: int | None = None
        try:
            directory_fd = os.open(
                artifact["artifact_dir_name"],
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=root_fd,
            )
            metadata = os.fstat(directory_fd)
            if (
                metadata.st_dev != artifact["artifact_dir_dev"]
                or metadata.st_ino != artifact["artifact_dir_ino"]
            ):
                raise _OutputRootChanged()
            self._verify_owner(directory_fd, artifact["artifact_owner_token"])
            descriptor = os.open(
                artifact["artifact_final_name"],
                os.O_RDONLY | os.O_NOFOLLOW,
                dir_fd=directory_fd,
            )
            file_metadata = os.fstat(descriptor)
            if not stat.S_ISREG(file_metadata.st_mode):
                os.close(descriptor)
                raise ValueError("artifact is not a regular file")
            digest = sha256()
            count = 0
            range_ok = True
            with os.fdopen(descriptor, "rb") as stream:
                for line in stream:
                    digest.update(line)
                    record = json.loads(line)
                    stamp = record.get("sent_at_ms")
                    if (
                        record.get("conversation_id") != job["conversation_id"]
                        or type(stamp) is not int
                        or not job["start_ms"] <= stamp < job["end_ms"]
                    ):
                        range_ok = False
                    count += 1
            self._assert_root_binding(binding)
            return digest.hexdigest(), count, range_ok
        finally:
            if directory_fd is not None:
                os.close(directory_fd)
            os.close(root_fd)

    @staticmethod
    def _check_record(job, message) -> None:
        if (
            message.conversation_id != job["conversation_id"]
            or type(message.sent_at_ms) is not int
            or not job["start_ms"] <= message.sent_at_ms < job["end_ms"]
        ):
            raise ValueError("adapter_scope_mismatch")

    def _check_cancelled(self, job_id: str) -> None:
        row = self.database.one(
            "SELECT cancelled,state FROM jobs WHERE job_id=?", (job_id,)
        )
        if (
            job_id in self._cancelled
            or row is None
            or row["cancelled"]
            or row["state"] == "cancelled"
        ):
            raise _Cancelled()

    def _cancel_and_cleanup(self, job_id: str) -> None:
        self.database.cancel_job(job_id)
        self._cleanup_job(job_id)

    def _fail(self, job_id: str, code: str, **fields) -> None:
        self.database.fail_job(job_id, code, **fields)

    def _assert_job_root(self, job: dict) -> None:
        binding = self._root_bindings.get(job.get("output_root_id"))
        if binding is None:
            raise _OutputRootChanged()
        self._assert_root_binding(binding)

    @staticmethod
    def _bind_root(root: Path) -> _RootBinding:
        try:
            path_metadata = os.stat(root, follow_symlinks=False)
            if not stat.S_ISDIR(path_metadata.st_mode):
                raise ValueError("unsafe output root")
            descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            descriptor_metadata = os.fstat(descriptor)
            if (
                descriptor_metadata.st_dev != path_metadata.st_dev
                or descriptor_metadata.st_ino != path_metadata.st_ino
                or not stat.S_ISDIR(descriptor_metadata.st_mode)
            ):
                os.close(descriptor)
                raise ValueError("unsafe output root")
            return _RootBinding(
                path=root,
                descriptor=descriptor,
                device=descriptor_metadata.st_dev,
                inode=descriptor_metadata.st_ino,
            )
        except OSError as exc:
            raise ValueError("unsafe output root") from exc

    @staticmethod
    def _assert_root_binding(binding: _RootBinding) -> None:
        try:
            descriptor_metadata = os.fstat(binding.descriptor)
            path_metadata = os.stat(binding.path, follow_symlinks=False)
        except OSError:
            raise _OutputRootChanged() from None
        expected = (binding.device, binding.inode)
        if (
            (descriptor_metadata.st_dev, descriptor_metadata.st_ino) != expected
            or (path_metadata.st_dev, path_metadata.st_ino) != expected
            or not stat.S_ISDIR(path_metadata.st_mode)
        ):
            raise _OutputRootChanged()

    @staticmethod
    def _valid_artifact_names(job: dict) -> bool:
        job_id = job.get("job_id")
        token = job.get("artifact_owner_token")
        return bool(
            isinstance(job_id, str)
            and _JOB_ID.fullmatch(job_id)
            and job.get("artifact_dir_name") == "job-" + job_id
            and isinstance(job.get("artifact_temp_name"), str)
            and _TEMP_NAME.fullmatch(job["artifact_temp_name"])
            and job.get("artifact_final_name") == "messages.jsonl"
            and isinstance(token, str)
            and re.fullmatch(r"[0-9a-f]{64}", token)
        )

    @staticmethod
    def _verify_owner(directory_fd: int, expected: str) -> None:
        descriptor = os.open(
            _OWNER_NAME, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd
        )
        with os.fdopen(descriptor, "rb") as stream:
            observed = stream.read(65)
        if observed != expected.encode("ascii"):
            raise ValueError("artifact owner marker mismatch")

    def _cleanup_interrupted_artifacts(self) -> None:
        for job in self.database.artifact_cleanup_rows():
            self._cleanup_job(job["job_id"])

    def _cleanup_job(self, job_id: str) -> None:
        job = self.database.one("SELECT * FROM jobs WHERE job_id=?", (job_id,))
        if (
            job is None
            or not job.get("artifact_dir_owned")
            or not self._valid_artifact_names(job)
        ):
            return
        binding = self._root_bindings.get(job.get("output_root_id"))
        if binding is None:
            return
        try:
            root_metadata = os.fstat(binding.descriptor)
        except OSError:
            return
        if (root_metadata.st_dev, root_metadata.st_ino) != (
            binding.device,
            binding.inode,
        ):
            return
        root_fd = os.dup(binding.descriptor)
        directory_fd: int | None = None
        cleanup_verified = False
        try:
            directory_fd = os.open(
                job["artifact_dir_name"],
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=root_fd,
            )
            metadata = os.fstat(directory_fd)
            if (
                metadata.st_dev != job["artifact_dir_dev"]
                or metadata.st_ino != job["artifact_dir_ino"]
            ):
                return
            self._verify_owner(directory_fd, job["artifact_owner_token"])
            for field, owned in (
                ("artifact_temp_name", job["artifact_temp_owned"]),
                ("artifact_final_name", job["artifact_final_owned"]),
            ):
                if owned:
                    try:
                        os.unlink(job[field], dir_fd=directory_fd)
                    except FileNotFoundError:
                        pass
            os.unlink(_OWNER_NAME, dir_fd=directory_fd)
            os.fsync(directory_fd)
            cleanup_verified = True
        except (OSError, TypeError, ValueError):
            return
        finally:
            if directory_fd is not None:
                os.close(directory_fd)
            if cleanup_verified:
                try:
                    os.rmdir(job["artifact_dir_name"], dir_fd=root_fd)
                except OSError:
                    pass
                try:
                    os.fsync(root_fd)
                except OSError:
                    pass
            os.close(root_fd)
        if cleanup_verified:
            self.database.mark_artifact_cleaned(job_id)


class _Cancelled(Exception):
    pass


class _OutputRootChanged(Exception):
    pass


class _ArtifactCollision(Exception):
    pass
