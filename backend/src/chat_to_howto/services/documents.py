"""Session-owned append-only editing and restoration for verified generation results."""

from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Iterable
from uuid import uuid4

from chat_to_howto.api.session import digest
from chat_to_howto.domain.documents import DocumentRecoveryPage, DocumentVersion, HowToDocument
from chat_to_howto.domain.generation import GenerationResult
from chat_to_howto.repositories.chat import ChatRepository
from chat_to_howto.services.evidence_validation import ValidatedGeneration


_OPAQUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_MAX_CONTENT_BYTES = 256_000


class DocumentAuthorizationError(PermissionError):
    pass


class DocumentNotFound(LookupError):
    pass


class DocumentConflict(ValueError):
    pass


class DocumentService:
    def __init__(self, *, repository: ChatRepository, clock=None) -> None:
        self._repository = repository
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("document clock must be timezone-aware")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _opaque(value: str, label: str) -> str:
        if not isinstance(value, str) or not _OPAQUE.fullmatch(value) or value in {".", ".."}:
            raise ValueError(f"opaque {label} is required")
        return value

    def create_from_generation(
        self, generation_job_id: str, *, session_id: str
    ) -> HowToDocument:
        self._opaque(generation_job_id, "generation job ID")
        session_hash = digest(session_id)
        try:
            job = self._repository.get_generation_job(generation_job_id)
        except ValueError:
            raise DocumentNotFound("generation job was not found") from None
        if job["session_digest"] != session_hash:
            raise DocumentAuthorizationError("generation job belongs to another session")
        if (
            job["state"] != "complete"
            or job["evidence_status"] != "verified"
            or job["result_json"] is None
        ):
            raise DocumentConflict("generation job has no verified result")
        try:
            source = ValidatedGeneration.model_validate_json(str(job["result_json"]))
        except (TypeError, ValueError):
            raise DocumentConflict("verified generation result is invalid") from None
        now = self._now().isoformat()
        try:
            row = self._repository.create_document_from_generation(
                document_id=f"document:{uuid4().hex}",
                version_id=f"document-version:{uuid4().hex}",
                session_digest=session_hash,
                source_generation_job_id=generation_job_id,
                source_result_json=source.model_dump_json(),
                content_json=source.result.model_dump_json(),
                created_at_utc=now,
            )
        except PermissionError as error:
            raise DocumentAuthorizationError(str(error)) from None
        return self._view(row)

    def get_document(self, document_id: str, *, session_id: str) -> HowToDocument:
        self._opaque(document_id, "document ID")
        try:
            row = self._repository.get_document(
                document_id, session_digest=digest(session_id)
            )
        except PermissionError as error:
            raise DocumentAuthorizationError(str(error)) from None
        except ValueError:
            raise DocumentNotFound("document was not found") from None
        return self._view(row)

    def list_documents(
        self, *, session_id: str, cursor: str | None = None, limit: int = 20
    ) -> DocumentRecoveryPage:
        if not session_id:
            raise DocumentAuthorizationError("local session is unavailable")
        rows, next_cursor = self._repository.list_documents(
            session_digest=digest(session_id), cursor=cursor, limit=limit
        )
        return DocumentRecoveryPage(
            documents=tuple(self._view(row) for row in rows),
            next_cursor=next_cursor,
        )

    def create_version(
        self,
        document_id: str,
        *,
        parent_version_id: str,
        content: GenerationResult,
        change_summary: str,
        session_id: str,
    ) -> HowToDocument:
        self._opaque(document_id, "document ID")
        self._opaque(parent_version_id, "parent version ID")
        content = GenerationResult.model_validate(content)
        self._validate_content(content)
        summary = self._plain_summary(change_summary)
        current = self.get_document(document_id, session_id=session_id)
        if current.current_version.version_id != parent_version_id:
            raise DocumentConflict("parent version is not current")
        allowed = {item.message_id for item in current.source_evidence}
        if not set(_referenced_message_ids(content)) <= allowed:
            raise DocumentConflict("user edit contains unavailable evidence")
        try:
            row = self._repository.append_document_version(
                document_id=document_id,
                version_id=f"document-version:{uuid4().hex}",
                session_digest=digest(session_id),
                parent_version_id=parent_version_id,
                restored_from_version_id=None,
                origin="user_edited",
                content_json=content.model_dump_json(),
                change_summary=summary,
                created_at_utc=self._now().isoformat(),
            )
        except PermissionError as error:
            raise DocumentAuthorizationError(str(error)) from None
        except ValueError as error:
            raise DocumentConflict(str(error)) from None
        return self._view(row)

    def restore_version(
        self, document_id: str, *, version_id: str, session_id: str
    ) -> HowToDocument:
        self._opaque(document_id, "document ID")
        self._opaque(version_id, "version ID")
        try:
            row = self._repository.restore_document_version(
                document_id=document_id,
                version_id=f"document-version:{uuid4().hex}",
                restored_from_version_id=version_id,
                session_digest=digest(session_id),
                created_at_utc=self._now().isoformat(),
            )
        except PermissionError as error:
            raise DocumentAuthorizationError(str(error)) from None
        except ValueError as error:
            raise DocumentConflict(str(error)) from None
        return self._view(row)

    @staticmethod
    def _plain_summary(value: str) -> str:
        if not isinstance(value, str) or len(value) > 500:
            raise DocumentConflict("change summary is invalid")
        _reject_controls((value,))
        return value.strip()

    @staticmethod
    def _validate_content(content: GenerationResult) -> None:
        payload = content.model_dump_json()
        if len(payload.encode("utf-8")) > _MAX_CONTENT_BYTES:
            raise DocumentConflict("document content is too large")
        _reject_controls(_strings(content.model_dump(mode="json")))

    @staticmethod
    def _view(row: dict[str, object]) -> HowToDocument:
        try:
            source = ValidatedGeneration.model_validate_json(
                str(row["source_result_json"])
            )
            versions = tuple(
                DocumentVersion(
                    document_id=str(item["document_id"]),
                    version_id=str(item["version_id"]),
                    ordinal=int(item["ordinal"]),
                    origin=str(item["origin"]),
                    evidence_status=(
                        "verified" if item["origin"] == "ai_generated" else "unverified"
                    ),
                    parent_version_id=(
                        str(item["parent_version_id"])
                        if item["parent_version_id"] is not None
                        else None
                    ),
                    restored_from_version_id=(
                        str(item["restored_from_version_id"])
                        if item["restored_from_version_id"] is not None
                        else None
                    ),
                    source_generation_job_id=str(row["source_generation_job_id"]),
                    change_summary=str(item["change_summary"]),
                    created_at=datetime.fromisoformat(str(item["created_at_utc"])),
                    content=GenerationResult.model_validate_json(str(item["content_json"])),
                )
                for item in row["versions"]
            )
            by_id = {item.version_id: item for item in versions}
            return HowToDocument(
                document_id=str(row["document_id"]),
                source_generation_job_id=str(row["source_generation_job_id"]),
                source_snapshot_id=source.snapshot_id,
                created_at=datetime.fromisoformat(str(row["created_at_utc"])),
                updated_at=datetime.fromisoformat(str(row["updated_at_utc"])),
                current_version=by_id[str(row["current_version_id"])],
                versions=versions,
                source_evidence=source.evidence,
            )
        except (KeyError, TypeError, ValueError):
            raise DocumentConflict("stored document is invalid") from None


def _strings(value: object) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings(item)


def _reject_controls(values: Iterable[str]) -> None:
    for value in values:
        if any(ord(character) < 32 and character not in "\n\t" for character in value):
            raise DocumentConflict("document text contains unsupported control characters")


def _referenced_message_ids(result: GenerationResult) -> tuple[str, ...]:
    ordered: list[str] = []
    for item in (*result.preparations, *result.steps, *result.checks):
        ordered.extend(item.source_message_ids)
    for conflict in result.conflicts:
        ordered.extend(conflict.claim_a_source_message_ids)
        ordered.extend(conflict.claim_b_source_message_ids)
        ordered.extend(conflict.resolution_source_message_ids)
    return tuple(dict.fromkeys(ordered))
