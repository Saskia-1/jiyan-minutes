"""Deleting one content batch removes only its dependent mixed document and private image."""

from datetime import datetime, timezone

from chat_to_howto.api.session import digest
from chat_to_howto.domain.generation import GenerationBudget
from chat_to_howto.domain.mixed_sources import SourceRef
from chat_to_howto.providers.fake import FakeProvider
from chat_to_howto.repositories.chat import ChatRepository
from chat_to_howto.repositories.content import ContentRepository
from chat_to_howto.services.artifacts import ApprovedOutputRoot
from chat_to_howto.services.deletion import DeletionService
from chat_to_howto.services.documents import DocumentService
from chat_to_howto.services.generation_cache import GenerationCacheSettings
from chat_to_howto.services.generation_jobs import GenerationJobOrchestrator
from chat_to_howto.services.generation_planning import MixedGenerationSelection
from chat_to_howto.services.mixed_search import MixedSearchService


def test_content_batch_plan_deletes_dependent_document_and_private_image_only(tmp_path):
    repo = ChatRepository(tmp_path / "chat.sqlite3")
    content = ContentRepository(repo)
    private = tmp_path / "private-images"
    private.mkdir(mode=0o700)
    image = private / ("a" * 32 + ".png")
    image.write_bytes(b"synthetic-image")
    first = "content:v1:note:" + "a" * 64
    other = "content:v1:note:" + "b" * 64
    for batch_id, unit_id, images in (
        ("batch-a", first, [{"image_id": "image-a", "input_order": 0, "sha256": "a" * 64, "basename": image.name, "mime_type": "image/png"}]),
        ("batch-b", other, []),
    ):
        content.save_batch(batch_id=batch_id, session_digest=digest("owner"), created_at_utc="2026-10-12T00:00:00+00:00", images=images, units=[
            {"unit_id": unit_id, "image_id": "image-a" if images else None, "kind": "note_passage", "group_id": "note-1", "source_order": 0, "text": "合成主题正文", "sender_name": None, "sent_at": None},
        ])
    settings = GenerationCacheSettings(prompt_version="synthetic", output_schema_version="synthetic", chunk_strategy_version="synthetic", max_chunk_input_tokens=8_000, provider_behavior_revision="synthetic")
    orchestrator = GenerationJobOrchestrator(repository=repo, provider_factory=lambda _: FakeProvider(), cache_settings=settings, clock=lambda: datetime(2026, 10, 12, tzinfo=timezone.utc), mixed_search=MixedSearchService(repo, content))
    selection = MixedGenerationSelection(source_refs=(SourceRef(kind="content_batch", id="batch-a"),), included_unit_ids=(first,), topic="合成主题", mode="complete", summary_type="ideas", include_undated=True,
        start_time=None, end_time=None, provider="fake", model="deterministic", thinking="disabled", reasoning_effort=None,
        redaction_policy_revision="text-only-outbound-v1", budget=GenerationBudget(max_messages=10, max_input_tokens=100_000, max_provider_calls=3))
    output = tmp_path / "output"; output.mkdir(mode=0o700)
    repository_root = tmp_path / "repository-root"; repository_root.mkdir()
    root = ApprovedOutputRoot.approve(output, root_id="approved-root", repository_root=repository_root)
    try:
        preview = orchestrator.create_mixed_preview(selection, session_id="owner")
        consent = orchestrator.issue_consent(preview.preview_id, session_id="owner", confirmed=True)
        job = orchestrator.start_job(consent.consent_id, session_id="owner", idempotency_key="mixed-a")
        assert orchestrator.run_job(job.job_id).state == "complete"
        document = DocumentService(repository=repo, clock=lambda: datetime(2026, 10, 12, tzinfo=timezone.utc)).create_from_generation(job.job_id, session_id="owner")
        deletion = DeletionService(repository=repo, approved_roots={"approved-root": root}, private_image_root=private,
            clock=lambda: datetime(2026, 10, 12, tzinfo=timezone.utc))
        plan = deletion.create_plan(target_kind="content_batch", target_id="batch-a", include_managed_files=False, session_id="owner")
        assert plan.counts["documents"] == 1
        assert plan.counts["content_images"] == 1
        assert plan.counts["content_units"] == 1
        result = deletion.confirm(plan.plan_id, fingerprint=plan.fingerprint, confirmed=True, session_id="owner")
        assert result.state == "complete"
        assert not image.exists()
        assert content.list_units("batch-a", session_digest=digest("owner")) == ()
        assert len(content.list_units("batch-b", session_digest=digest("owner"))) == 1
        assert all(item.document_id != document.document_id for item in DocumentService(repository=repo).list_documents(session_id="owner").documents)
    finally:
        repo.close()
