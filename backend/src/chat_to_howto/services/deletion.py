"""Fingerprint-confirmed staged deletion for local documents and export batches."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
from typing import Literal
from threading import RLock
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from chat_to_howto.api.session import digest
from chat_to_howto.repositories.chat import ChatRepository
from chat_to_howto.services.artifacts import ApprovedOutputRoot, _open_or_create_private_directory, _rename_noreplace


class DeletionConflict(ValueError):
    pass


class DeletionPlanView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    plan_id: str
    target_kind: Literal["document", "batch", "content_batch"]
    target_id: str
    include_managed_files: bool
    preserve_documents: bool = False
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    counts: dict[str, int]
    effects: tuple[str, ...]
    expires_at: datetime


class DeletionResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    plan_id: str
    state: Literal["complete", "cleanup_pending"]
    deleted_counts: dict[str, int]
    managed_files_retained: bool


class DeletionService:
    def __init__(self, *, repository: ChatRepository, approved_roots: dict[str, ApprovedOutputRoot],
                 clock=None, ttl: timedelta = timedelta(minutes=5), private_image_root: Path | None = None, cancel_generation_jobs=None) -> None:
        self._repository = repository
        self._roots = dict(approved_roots)
        if private_image_root is not None:
            if "private-content-images" in self._roots:
                raise ValueError("private image root identifier is reserved")
            candidate = Path(private_image_root).absolute()
            metadata = candidate.lstat()
            if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) & 0o077:
                raise ValueError("private image directory is unsafe")
            self._roots["private-content-images"] = ApprovedOutputRoot(
                path=candidate, root_id="private-content-images", device=metadata.st_dev, inode=metadata.st_ino,
            )
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = RLock()
        self._ttl = ttl
        self._cancel_generation_jobs = cancel_generation_jobs or (lambda _ids: None)

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("deletion clock must be timezone-aware")
        return value.astimezone(timezone.utc)

    def create_plan(self, *, target_kind: Literal["document", "batch", "content_batch"], target_id: str,
                    include_managed_files: bool, session_id: str, preserve_documents: bool = False) -> DeletionPlanView:
        if target_kind not in {"document", "batch", "content_batch"} or not target_id:
            raise ValueError("invalid deletion target")
        if preserve_documents and (target_kind == "document" or not include_managed_files):
            raise ValueError("raw cleanup must include managed raw files")
        return self._create_plan(target_kind=target_kind, target_id=target_id,
            include_managed_files=include_managed_files, preserve_documents=preserve_documents,
            session_hash=digest(session_id))

    def _create_plan(self, *, target_kind, target_id, include_managed_files, preserve_documents, session_hash):
        impact = self._repository.deletion_impact(
            target_kind=target_kind, target_id=target_id, session_digest=session_hash, preserve_documents=preserve_documents
        )
        impact_json = _canonical(impact)
        fingerprint = _fingerprint(impact, include_managed_files)
        now = self._now(); expires = now + self._ttl
        plan_id = f"deletion-plan:{uuid4().hex}"
        self._repository.create_deletion_plan(
            plan_id=plan_id, session_digest=session_hash, target_kind=target_kind,
            target_id=target_id, include_managed_files=include_managed_files,
            impact_json=impact_json, fingerprint=fingerprint, created_at_utc=now.isoformat(),
            expires_at_utc=expires.isoformat(), preserve_documents=preserve_documents,
        )
        return _plan_view(plan_id, target_kind, target_id, include_managed_files,
                          fingerprint, impact, expires, preserve_documents=preserve_documents)

    def confirm(self, plan_id: str, *, fingerprint: str, confirmed: bool,
                session_id: str) -> DeletionResult:
        return self._confirm(plan_id, fingerprint=fingerprint, confirmed=confirmed, session_hash=digest(session_id))

    def expire_raw(self, *, target_kind: str, target_id: str, session_hash: str) -> DeletionResult:
        jobs = self._repository.retire_raw(target_kind=target_kind, target_id=target_id,
            session_digest=session_hash, created_at_utc=self._now().isoformat())
        self._cancel_generation_jobs(jobs)
        plan = self._create_plan(target_kind=target_kind, target_id=target_id,
            include_managed_files=True, preserve_documents=True, session_hash=session_hash)
        return self._confirm(plan.plan_id, fingerprint=plan.fingerprint, confirmed=True, session_hash=session_hash)

    def _confirm(self, plan_id: str, *, fingerprint: str, confirmed: bool, session_hash: str) -> DeletionResult:
        with self._lock:
            return self._confirm_locked(plan_id, fingerprint=fingerprint, confirmed=confirmed, session_hash=session_hash)

    def _confirm_locked(self, plan_id: str, *, fingerprint: str, confirmed: bool, session_hash: str) -> DeletionResult:
        if confirmed is not True:
            raise PermissionError("explicit deletion confirmation is required")
        try:
            plan = self._repository.get_deletion_plan(plan_id)
        except ValueError:
            raise PermissionError("deletion plan is unavailable") from None
        if plan["session_digest"] != session_hash or plan["state"] != "planned":
            raise PermissionError("deletion plan is unavailable")
        if plan["fingerprint"] != fingerprint:
            raise PermissionError("deletion fingerprint does not match")
        if datetime.fromisoformat(str(plan["expires_at_utc"])) <= self._now():
            raise PermissionError("deletion plan expired")
        impact = self._repository.deletion_impact(
            target_kind=str(plan["target_kind"]), target_id=str(plan["target_id"]),
            session_digest=session_hash, preserve_documents=bool(plan["preserve_documents"]),
        )
        if _fingerprint(impact, bool(plan["include_managed_files"])) != fingerprint:
            raise DeletionConflict("deletion target changed")
        impact_json = _canonical(impact)
        self._cancel_generation_jobs(tuple(impact["generation_job_ids"]))
        staged: list[dict[str, str]] = []
        files_to_stage = list(impact["private_images"]) + (list(impact["managed_files"]) if bool(plan["include_managed_files"]) else [])
        if files_to_stage:
            try:
                staged = self._stage_files(plan_id, files_to_stage)
            except BaseException:
                self._restore_files(staged)
                raise
        try:
            result = self._repository.delete_planned_impact(
                plan_id=plan_id, session_digest=session_hash, fingerprint=fingerprint,
                expected_impact_json=impact_json, confirmed_at_utc=self._now().isoformat(),
                staged_files_json=_canonical(staged) if staged else None,
            )
        except ValueError as error:
            self._restore_files(staged)
            raise DeletionConflict("deletion target changed") from error
        except BaseException:
            self._restore_files(staged)
            raise
        cleanup_ok = True
        if staged:
            try:
                self._remove_staged(staged)
            except OSError:
                cleanup_ok = False
        self._repository.complete_deletion_cleanup(plan_id, success=cleanup_ok)
        return DeletionResult(
            plan_id=plan_id, state="complete" if cleanup_ok else "cleanup_pending",
            deleted_counts=result["deleted"],
            managed_files_retained=not bool(plan["include_managed_files"]) and bool(impact["managed_files"]),
        )

    def reconcile_cleanup(self) -> tuple[str, ...]:
        """Finish file cleanup after a crash that occurred after the DB commit."""
        with self._lock:
            return self._reconcile_cleanup_locked()

    def _reconcile_cleanup_locked(self) -> tuple[str, ...]:
        completed: list[str] = []
        for row in self._repository.interrupted_deletion_staging():
            try:
                self._restore_files(_parse_staged_files(row["staged_files_json"]))
                self._repository.record_deletion_staging(str(row["plan_id"]), None)
            except (OSError, TypeError, ValueError):
                continue
        for row in self._repository.pending_deletion_cleanups():
            plan_id = str(row["plan_id"])
            try:
                staged = _parse_staged_files(row["staged_files_json"])
                self._remove_staged(staged)
            except (OSError, PermissionError, TypeError, ValueError, json.JSONDecodeError):
                self._repository.complete_deletion_cleanup(plan_id, success=False)
                continue
            self._repository.complete_deletion_cleanup(plan_id, success=True)
            completed.append(plan_id)
        return tuple(completed)

    def reconcile_export_temporaries(self) -> int:
        pending = 0
        for owner in self._repository.retired_export_temp_owners():
            try:
                scope = json.loads(str(owner["scope_json"]))
                root_id = scope["output_root_id"]
                root = self._roots[root_id]
                root_fd = root.open_bound(); parent_fd = None
                try:
                    parent_fd = _open_or_create_private_directory(root_fd, "exports")
                    prefix = "." + str(owner["batch_id"]) + ".tmp-"
                    names = [name for name in os.listdir(parent_fd) if name.startswith(prefix)
                        and re.fullmatch(r"[0-9a-f]{32}", name[len(prefix):])]
                    if bool(owner["unbound"]) and datetime.fromisoformat(str(owner["created_at_utc"])) + timedelta(days=7) <= self._now():
                        if str(owner["batch_id"]) in os.listdir(parent_fd):
                            names.append(str(owner["batch_id"]))
                finally:
                    if parent_fd is not None: os.close(parent_fd)
                    os.close(root_fd)
                for name in names:
                    # The terminal job cannot publish this temporary. Keep its
                    # original discoverable name until removal completes, so a
                    # crash or an unexpected entry can be retried on startup.
                    self._remove_staged([{"root_id": root_id, "parent": "exports",
                        "name": name, "staged_name": name, "kind": "directory"}])
            except (KeyError, TypeError, ValueError, OSError):
                pending += 1
        return pending

    def _stage_files(self, plan_id: str, files: list[dict[str, str]]) -> list[dict[str, str]]:
        staged: list[dict[str, str]] = []
        try:
            expanded = []
            for item in files:
                _validate_file_target(item)
                if item["parent"] == "exports" and item["kind"] == "directory":
                    root = self._roots.get(item["root_id"])
                    if root is None:
                        raise PermissionError("managed output root is unavailable")
                    root_fd = root.open_bound(); parent_fd = None
                    try:
                        parent_fd = _open_or_create_private_directory(root_fd, "exports")
                        prefix = "." + item["name"] + ".tmp-"
                        for name in sorted(os.listdir(parent_fd)):
                            if name.startswith(prefix) and re.fullmatch(r"[0-9a-f]{32}", name[len(prefix):]):
                                expanded.append({**item, "name": name})
                    finally:
                        if parent_fd is not None: os.close(parent_fd)
                        os.close(root_fd)
                expanded.append(item)
            for index, item in enumerate(expanded):
                root = self._roots.get(item["root_id"])
                if root is None:
                    raise PermissionError("managed output root is unavailable")
                if item["root_id"] == "private-content-images" and (
                    item["parent"] != "." or item["kind"] != "file"
                    or re.fullmatch(r"[0-9a-f]{32}\.(?:png|jpg)", item["name"]) is None
                ):
                    raise PermissionError("private screenshot target is invalid")
                root_fd = root.open_bound(); parent_fd = None
                staged_name = f".delete-{sha256(plan_id.encode()).hexdigest()[:16]}-{index}"
                try:
                    parent_fd = _open_or_create_private_directory(root_fd, item["parent"])
                    try:
                        metadata = os.stat(item["name"], dir_fd=parent_fd, follow_symlinks=False)
                    except FileNotFoundError:
                        if item["parent"] == "exports" and item["kind"] == "directory":
                            continue
                        raise
                    expected = stat.S_ISDIR(metadata.st_mode) if item["kind"] == "directory" else stat.S_ISREG(metadata.st_mode)
                    if not expected or stat.S_ISLNK(metadata.st_mode):
                        raise PermissionError("managed artifact type changed")
                    candidate = {**item, "staged_name": staged_name}
                    self._repository.record_deletion_staging(plan_id, _canonical([*staged, candidate]))
                    _rename_noreplace(parent_fd, item["name"], staged_name)
                    os.fsync(parent_fd)
                    staged.append(candidate)
                finally:
                    if parent_fd is not None: os.close(parent_fd)
                    os.close(root_fd)
        except BaseException:
            self._restore_files(staged)
            self._repository.record_deletion_staging(plan_id, None)
            raise
        return staged

    def _restore_files(self, staged: list[dict[str, str]]) -> None:
        for item in reversed(staged):
            root = self._roots.get(item["root_id"])
            if root is None: continue
            root_fd = root.open_bound(); parent_fd = None
            try:
                parent_fd = _open_or_create_private_directory(root_fd, item["parent"])
                try:
                    _rename_noreplace(parent_fd, item["staged_name"], item["name"])
                except FileNotFoundError:
                    # A write-ahead entry may precede the actual rename.
                    pass
                os.fsync(parent_fd)
            finally:
                if parent_fd is not None: os.close(parent_fd)
                os.close(root_fd)

    def _remove_staged(self, staged: list[dict[str, str]]) -> None:
        for item in staged:
            _validate_file_target(item)
            root = self._roots[item["root_id"]]
            root_fd = root.open_bound(); parent_fd = None
            try:
                parent_fd = _open_or_create_private_directory(root_fd, item["parent"])
                if item["kind"] == "file":
                    try:
                        os.unlink(item["staged_name"], dir_fd=parent_fd)
                    except FileNotFoundError:
                        pass
                else:
                    try:
                        directory_fd = os.open(item["staged_name"], os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0), dir_fd=parent_fd)
                    except FileNotFoundError:
                        continue
                    try:
                        for name in os.listdir(directory_fd):
                            if name not in {"messages.jsonl", "messages.txt", "manifest.json"}:
                                raise PermissionError("managed export contains an unexpected entry")
                            metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                            if not stat.S_ISREG(metadata.st_mode):
                                raise PermissionError("managed export entry type changed")
                            os.unlink(name, dir_fd=directory_fd)
                        os.fsync(directory_fd)
                    finally:
                        os.close(directory_fd)
                    os.rmdir(item["staged_name"], dir_fd=parent_fd)
                os.fsync(parent_fd)
            finally:
                if parent_fd is not None: os.close(parent_fd)
                os.close(root_fd)


def _validate_file_target(item: dict[str, str]) -> None:
    if item["parent"] not in {"exports", "documents", "."} or item["kind"] not in {"directory", "file"}:
        raise PermissionError("managed artifact target is invalid")
    for key in ("name", "staged_name"):
        if key in item and (item[key] in {".", ".."} or any(char in item[key] for char in ("/", "\\", "\0"))):
            raise PermissionError("managed artifact target is invalid")


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _parse_staged_files(raw: object) -> list[dict[str, str]]:
    if raw is None:
        return []
    parsed = json.loads(str(raw))
    if not isinstance(parsed, list):
        raise ValueError("invalid staged deletion record")
    result: list[dict[str, str]] = []
    keys = {"root_id", "parent", "name", "kind", "staged_name"}
    for item in parsed:
        if not isinstance(item, dict) or set(item) != keys:
            raise ValueError("invalid staged deletion record")
        if not all(isinstance(item[key], str) and item[key] for key in keys):
            raise ValueError("invalid staged deletion record")
        if item["kind"] not in {"file", "directory"}:
            raise ValueError("invalid staged deletion record")
        _validate_file_target(item)
        result.append({key: item[key] for key in keys})
    return result


def _fingerprint(impact: dict[str, object], include_managed_files: bool) -> str:
    return sha256(_canonical({"impact": impact, "include_managed_files": include_managed_files}).encode()).hexdigest()


def _plan_view(plan_id: str, target_kind: str, target_id: str, include: bool,
               fingerprint: str, impact: dict[str, object], expires: datetime, *, preserve_documents: bool = False) -> DeletionPlanView:
    counts = {
        "documents": len(impact["document_ids"]),
        "versions": len(impact["version_ids"]),
        "document_artifacts": len([item for item in impact["managed_files"] if item["kind"] == "file"]),
        "generation_jobs": len(impact["generation_job_ids"]),
        "generation_requests": len(impact["generation_request_ids"]),
        "generation_consents": len(impact["generation_consent_ids"]),
        "generation_previews": len(impact["generation_preview_ids"]),
        "cache_entries": len(impact["cache_keys"]),
        "export_batches": len(impact["batch_ids"]),
        "export_jobs": len(impact["export_job_ids"]),
        "export_consents": len(impact["export_consent_ids"]),
        "snapshots": len(impact["snapshot_ids"]),
        "messages": len(impact["message_ids"]),
        "managed_files": len(impact["managed_files"]),
        "content_batches": len(impact["content_batch_ids"]),
        "content_units": len(impact["content_unit_ids"]),
        "content_images": len(impact["private_images"]),
    }
    effects = ("数据库：删除本地生成结果、不可变版本历史与衍生文档记录", "缓存：撤销相关生成缓存、授权预览与任务记录") if target_kind == "document" else (
        "数据库：删除导出批次、已导入快照与无其他引用的消息", "衍生数据：删除依赖该快照的生成结果、版本、文档和缓存",
    ) if target_kind == "batch" else (
        "数据库：删除选定的截图／笔记批次及正文", "衍生数据：删除依赖该批次的生成结果、版本、文档和缓存",
        "私有原图：随该导入批次一同删除，不受导出文件选项影响",
    )
    if preserve_documents:
        effects = (
            "原文：删除批次、快照、索引、预览、过程记录与内部依据副本",
            "文档：保留正文、全部版本与已保存的文档文件；来源依据不再可查看",
        )
    if target_kind == "batch" and int(impact.get("shared_message_count", 0)):
        effects += (
            f'共享消息：{impact["shared_message_count"]} 条仍被其他批次或快照引用，将保留',
        )
    effects += (("本地文件：删除计划所列的受管文件",) if include else ("本地文件：保留受管导出文件；这不是取证级擦除",))
    effects += ("上游：不会删除 QQ/微信原始数据、第三方工具缓存或 macOS 权限",)
    return DeletionPlanView(plan_id=plan_id, target_kind=target_kind, target_id=target_id,
        include_managed_files=include, preserve_documents=preserve_documents, fingerprint=fingerprint, counts=counts, effects=effects, expires_at=expires)
