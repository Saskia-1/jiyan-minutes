"""Deletion is impact-planned, fingerprint-confirmed, local, and narrowly staged."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from chat_to_howto.api.session import digest
from chat_to_howto.services.artifacts import ApprovedOutputRoot
from chat_to_howto.services.deletion import DeletionConflict, DeletionService
from chat_to_howto.services.documents import DocumentNotFound, DocumentService
from .test_documents import NOW, _generated


def _service(tmp_path: Path):
    repository, provider, job_id, _message_id = _generated(tmp_path)
    document_service = DocumentService(repository=repository, clock=lambda: NOW)
    document = document_service.create_from_generation(job_id, session_id="session-a")
    repository_root = tmp_path / "repo"; repository_root.mkdir()
    output = tmp_path / "approved"; output.mkdir(mode=0o700)
    root = ApprovedOutputRoot.approve(output, root_id="approved-root", repository_root=repository_root)
    service = DeletionService(repository=repository, approved_roots={"approved-root": root}, clock=lambda: NOW)
    return repository, provider, document_service, document, service, output


def test_document_deletion_plan_is_exact_stale_safe_and_deletes_managed_files(tmp_path: Path) -> None:
    repository, provider, documents, document, service, output = _service(tmp_path)
    try:
        artifacts = output / "documents"; artifacts.mkdir(mode=0o700)
        filename = "synthetic.docx"; (artifacts / filename).write_bytes(b"synthetic-docx")
        repository.record_document_artifact(
            artifact_id="document-artifact:p3", session_digest=digest("session-a"),
            document_id=document.document_id, version_id=document.current_version.version_id,
            output_root_id="approved-root", filename=filename, format="docx", source_mode="body_only",
            byte_size=14, sha256_digest="a" * 64, created_at_utc=NOW.isoformat(),
        )
        stale = service.create_plan(target_kind="document", target_id=document.document_id,
            include_managed_files=True, session_id="session-a")
        assert stale.counts == {"documents": 1, "versions": 1, "document_artifacts": 1,
            "generation_jobs": 1, "generation_requests": 1, "generation_consents": 1, "generation_previews": 1,
            "cache_entries": 1, "export_batches": 0, "export_jobs": 0,
            "export_consents": 0, "snapshots": 0, "messages": 0, "managed_files": 1}
        documents.create_version(document.document_id, parent_version_id=document.current_version.version_id,
            content=document.current_version.content.model_copy(update={"title": "new"}),
            change_summary="changed after plan", session_id="session-a")
        with pytest.raises(DeletionConflict, match="changed"):
            service.confirm(stale.plan_id, fingerprint=stale.fingerprint, confirmed=True, session_id="session-a")

        plan = service.create_plan(target_kind="document", target_id=document.document_id,
            include_managed_files=True, session_id="session-a")
        result = service.confirm(plan.plan_id, fingerprint=plan.fingerprint, confirmed=True, session_id="session-a")
        assert result.state == "complete"
        assert result.deleted_counts["versions"] == 2
        assert not (artifacts / filename).exists()
        with pytest.raises(DocumentNotFound):
            documents.get_document(document.document_id, session_id="session-a")
        with pytest.raises(ValueError):
            repository.get_generation_job(document.source_generation_job_id)
        assert repository.snapshot_messages("snapshot-a")
        assert provider.call_count == 1
    finally:
        repository.close()


def test_batch_plan_deletes_snapshot_and_index_but_retains_files_when_not_selected(tmp_path: Path) -> None:
    repository, _provider, _documents, document, service, output = _service(tmp_path)
    try:
        exports = output / "exports" / "batch-a"; exports.mkdir(parents=True, mode=0o700)
        retained = exports / "messages.jsonl"; retained.write_bytes(b"synthetic")
        plan = service.create_plan(target_kind="batch", target_id="batch-a",
            include_managed_files=False, session_id="session-a")
        assert plan.counts["snapshots"] == 1 and plan.counts["messages"] == 1
        assert plan.counts["documents"] == 1
        result = service.confirm(plan.plan_id, fingerprint=plan.fingerprint, confirmed=True, session_id="session-a")
        assert result.state == "complete"
        assert result.managed_files_retained is True
        assert retained.exists()
        assert repository.snapshot_messages("snapshot-a") == ()
        assert repository.message_count() == 0
        with pytest.raises(DocumentNotFound):
            _documents.get_document(document.document_id, session_id="session-a")
    finally:
        repository.close()


def test_deletion_plan_is_session_bound_expiring_and_requires_explicit_confirmation(tmp_path: Path) -> None:
    repository, _provider, _documents, document, service, _output = _service(tmp_path)
    try:
        plan = service.create_plan(target_kind="document", target_id=document.document_id,
            include_managed_files=False, session_id="session-a")
        with pytest.raises(PermissionError):
            service.confirm(plan.plan_id, fingerprint=plan.fingerprint, confirmed=True, session_id="session-b")
        with pytest.raises(PermissionError):
            service.confirm(plan.plan_id, fingerprint=plan.fingerprint, confirmed=False, session_id="session-a")
        expired = DeletionService(repository=repository, approved_roots={}, clock=lambda: NOW + timedelta(minutes=6))
        with pytest.raises(PermissionError, match="expired"):
            expired.confirm(plan.plan_id, fingerprint=plan.fingerprint, confirmed=True, session_id="session-a")
    finally:
        repository.close()


def test_partial_file_staging_is_rolled_back_before_database_deletion(tmp_path: Path) -> None:
    repository, _provider, _documents, document, service, output = _service(tmp_path)
    try:
        artifacts = output / "documents"; artifacts.mkdir(mode=0o700)
        first = artifacts / "first.md"; first.write_text("safe", encoding="utf-8")
        for artifact_id, filename in (("artifact:first", "first.md"), ("artifact:missing", "missing.md")):
            repository.record_document_artifact(
                artifact_id=artifact_id, session_digest=digest("session-a"),
                document_id=document.document_id, version_id=document.current_version.version_id,
                output_root_id="approved-root", filename=filename, format="markdown",
                source_mode="body_only", byte_size=4, sha256_digest="b" * 64,
                created_at_utc=NOW.isoformat(),
            )
        plan = service.create_plan(target_kind="document", target_id=document.document_id,
            include_managed_files=True, session_id="session-a")

        with pytest.raises(FileNotFoundError):
            service.confirm(plan.plan_id, fingerprint=plan.fingerprint, confirmed=True, session_id="session-a")

        assert first.read_text(encoding="utf-8") == "safe"
        assert not tuple(artifacts.glob(".delete-*"))
        assert repository.get_deletion_plan(plan.plan_id)["state"] == "planned"
    finally:
        repository.close()


def test_startup_reconciliation_finishes_committed_file_cleanup(tmp_path: Path) -> None:
    repository, _provider, _documents, document, service, output = _service(tmp_path)
    try:
        artifacts = output / "documents"; artifacts.mkdir(mode=0o700)
        filename = "interrupted.md"; (artifacts / filename).write_text("safe", encoding="utf-8")
        repository.record_document_artifact(
            artifact_id="artifact:interrupted", session_digest=digest("session-a"),
            document_id=document.document_id, version_id=document.current_version.version_id,
            output_root_id="approved-root", filename=filename, format="markdown",
            source_mode="body_only", byte_size=4, sha256_digest="c" * 64,
            created_at_utc=NOW.isoformat(),
        )
        plan = service.create_plan(target_kind="document", target_id=document.document_id,
            include_managed_files=True, session_id="session-a")
        impact = repository.deletion_impact(target_kind="document", target_id=document.document_id,
            session_digest=digest("session-a"))
        staged = service._stage_files(plan.plan_id, impact["managed_files"])
        repository.delete_planned_impact(
            plan_id=plan.plan_id, session_digest=digest("session-a"), fingerprint=plan.fingerprint,
            expected_impact_json=repository.get_deletion_plan(plan.plan_id)["impact_json"],
            confirmed_at_utc=NOW.isoformat(), staged_files_json=__import__("json").dumps(
                staged, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ),
        )

        assert service.reconcile_cleanup() == (plan.plan_id,)
        assert repository.get_deletion_plan(plan.plan_id)["state"] == "complete"
        assert not (artifacts / filename).exists()
    finally:
        repository.close()
