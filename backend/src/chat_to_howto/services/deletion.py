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
                 clock=None, ttl: timedelta = timedelta(minutes=5), private_image_root: Path | None = None) -> None:
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
        self._ttl = ttl

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("deletion clock must be timezone-aware")
        return value.astimezone(timezone.utc)

    def create_plan(self, *, target_kind: Literal["document", "batch", "content_batch"], target_id: str,
                    include_managed_files: bool, session_id: str) -> DeletionPlanView:
        if target_kind not in {"document", "batch", "content_batch"} or not target_id:
            raise ValueError("invalid deletion target")
        session_hash = digest(session_id)
        impact = self._repository.deletion_impact(
            target_kind=target_kind, target_id=target_id, session_digest=session_hash
        )
        impact_json = _canonical(impact)
        fingerprint = _fingerprint(impact, include_managed_files)
        now = self._now(); expires = now + self._ttl
        plan_id = f"deletion-plan:{uuid4().hex}"
        self._repository.create_deletion_plan(
            plan_id=plan_id, session_digest=session_hash, target_kind=target_kind,
            target_id=target_id, include_managed_files=include_managed_files,
            impact_json=impact_json, fingerprint=fingerprint, created_at_utc=now.isoformat(),
            expires_at_utc=expires.isoformat(),
        )
        return _plan_view(plan_id, target_kind, target_id, include_managed_files,
                          fingerprint, impact, expires)

    def confirm(self, plan_id: str, *, fingerprint: str, confirmed: bool,
                session_id: str) -> DeletionResult:
        if confirmed is not True:
            raise PermissionError("explicit deletion confirmation is required")
        try:
            plan = self._repository.get_deletion_plan(plan_id)
        except ValueError:
            raise PermissionError("deletion plan is unavailable") from None
        session_hash = digest(session_id)
        if plan["session_digest"] != session_hash or plan["state"] != "planned":
            raise PermissionError("deletion plan is unavailable")
        if plan["fingerprint"] != fingerprint:
            raise PermissionError("deletion fingerprint does not match")
        if datetime.fromisoformat(str(plan["expires_at_utc"])) <= self._now():
            raise PermissionError("deletion plan expired")
        impact = self._repository.deletion_impact(
            target_kind=str(plan["target_kind"]), target_id=str(plan["target_id"]),
            session_digest=session_hash,
        )
        if _fingerprint(impact, bool(plan["include_managed_files"])) != fingerprint:
            raise DeletionConflict("deletion target changed")
        impact_json = _canonical(impact)
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
        completed: list[str] = []
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

    def _stage_files(self, plan_id: str, files: list[dict[str, str]]) -> list[dict[str, str]]:
        staged: list[dict[str, str]] = []
        try:
            for index, item in enumerate(files):
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
                    metadata = os.stat(item["name"], dir_fd=parent_fd, follow_symlinks=False)
                    expected = stat.S_ISDIR(metadata.st_mode) if item["kind"] == "directory" else stat.S_ISREG(metadata.st_mode)
                    if not expected or stat.S_ISLNK(metadata.st_mode):
                        raise PermissionError("managed artifact type changed")
                    _rename_noreplace(parent_fd, item["name"], staged_name)
                    os.fsync(parent_fd)
                    staged.append({**item, "staged_name": staged_name})
                finally:
                    if parent_fd is not None: os.close(parent_fd)
                    os.close(root_fd)
        except BaseException:
            self._restore_files(staged)
            raise
        return staged

    def _restore_files(self, staged: list[dict[str, str]]) -> None:
        for item in reversed(staged):
            root = self._roots.get(item["root_id"])
            if root is None: continue
            root_fd = root.open_bound(); parent_fd = None
            try:
                parent_fd = _open_or_create_private_directory(root_fd, item["parent"])
                _rename_noreplace(parent_fd, item["staged_name"], item["name"])
                os.fsync(parent_fd)
            finally:
                if parent_fd is not None: os.close(parent_fd)
                os.close(root_fd)

    def _remove_staged(self, staged: list[dict[str, str]]) -> None:
        for item in staged:
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
        result.append({key: item[key] for key in keys})
    return result


def _fingerprint(impact: dict[str, object], include_managed_files: bool) -> str:
    return sha256(_canonical({"impact": impact, "include_managed_files": include_managed_files}).encode()).hexdigest()


def _plan_view(plan_id: str, target_kind: str, target_id: str, include: bool,
               fingerprint: str, impact: dict[str, object], expires: datetime) -> DeletionPlanView:
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
    if target_kind == "batch" and int(impact.get("shared_message_count", 0)):
        effects += (
            f'共享消息：{impact["shared_message_count"]} 条仍被其他批次或快照引用，将保留',
        )
    effects += (("本地文件：删除计划所列的受管文件",) if include else ("本地文件：保留受管导出文件；这不是取证级擦除",))
    effects += ("上游：不会删除 QQ/微信原始数据、第三方工具缓存或 macOS 权限",)
    return DeletionPlanView(plan_id=plan_id, target_kind=target_kind, target_id=target_id,
        include_managed_files=include, fingerprint=fingerprint, counts=counts, effects=effects, expires_at=expires)
