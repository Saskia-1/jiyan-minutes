"""P1 artifact tests use synthetic records and temporary output roots only."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import errno
from hashlib import sha256
import json
import os
from pathlib import Path

import pytest

from chat_to_howto.domain.jobs import ExportConversation, ExportScope
from chat_to_howto.domain.messages import AttachmentPlaceholder, MessageInput, StoredMessage
from chat_to_howto.sources.base import CompletenessEvidence, FetchSliceEvidence


def _scope(root_id: str = "approved-root") -> ExportScope:
    return ExportScope(
        source_id="synthetic-source",
        account_id="account-a",
        source_account_namespace="qq:opaque-account-a",
        conversations=(
            ExportConversation(
                conversation_id="conversation-a",
                display_name="同名合成群",
                conversation_type="group",
            ),
            ExportConversation(
                conversation_id="conversation-b",
                display_name="同名合成群",
                conversation_type="group",
            ),
        ),
        range_start=datetime(2026, 9, 1, tzinfo=timezone.utc),
        range_end=datetime(2026, 9, 2, tzinfo=timezone.utc),
        display_timezone="Asia/Shanghai",
        output_root_id=root_id,
        configuration_nonce="synthetic-config-v1",
        client_version="fixture-client-1",
        tool_version="fixture-tool-1",
        account_version="fixture-account-1",
    )


def _message(
    conversation_id: str,
    source_order: int,
    *,
    text: str = "合成正文",
    sent_at: datetime | None = None,
    source_message_id: str | None = None,
) -> StoredMessage:
    moment = sent_at or datetime(2026, 9, 1, 1, tzinfo=timezone.utc)
    value = MessageInput(
        workspace_id="workspace-synthetic",
        platform="qq",
        source_id="synthetic-source",
        account_id="account-a",
        source_account_namespace="qq:opaque-account-a",
        conversation_id=conversation_id,
        conversation_name="同名合成群",
        conversation_type="group",
        source_message_id=source_message_id or f"source-message-{conversation_id}-{source_order}",
        source_file_id=None,
        page_slice_id="slice-1",
        source_order=source_order,
        sent_at_utc=moment,
        original_timestamp=moment.isoformat(),
        source_timezone="UTC",
        sender_id="sender-a",
        sender_name="合成发送者",
        message_type="text",
        text=text,
        reply_to_message_id=None,
        attachments=(
            AttachmentPlaceholder(
                kind="image", availability="metadata_only", original_name="示意图.png"
            ),
        ),
    )
    return StoredMessage.from_input(value)


def _evidence(status: str = "source_range_verified") -> dict[str, CompletenessEvidence]:
    return {
        conversation_id: CompletenessEvidence(
            status=status,  # type: ignore[arg-type]
            reason="synthetic_fixture",
            duplicate_count=0,
            requested_start_ms=1788220800000,
            requested_end_ms=1788307200000,
            observed_start_ms=1788224400000,
            observed_end_ms=1788224400000,
            slices=(
                FetchSliceEvidence(
                    page=1,
                    cursor=None,
                    requested=100,
                    returned=1,
                    accepted=1,
                    has_next=False,
                ),
            ),
            fetched_count=1,
            accepted_count=1,
            unknown_time_count=0,
            missing_id_count=0,
            parse_error_count=0,
            warnings=(),
            termination="fixture_exhausted",
        )
        for conversation_id in ("conversation-a", "conversation-b")
    }


def _writer(tmp_path: Path, *, fault_hook=None):
    # Imported here so the first RED run fails specifically because production
    # code is absent, rather than during collection of unrelated tests.
    from chat_to_howto.services.artifacts import ApprovedOutputRoot, ArtifactWriter

    repository_root = tmp_path / "源码工作区"
    repository_root.mkdir()
    output_root = tmp_path / "本地 导出目录"
    output_root.mkdir(mode=0o700)
    approved = ApprovedOutputRoot.approve(
        output_root,
        root_id="approved-root",
        repository_root=repository_root,
    )
    return ArtifactWriter(approved, fault_hook=fault_hook), output_root, repository_root


def test_atomic_writer_creates_private_verified_jsonl_txt_and_manifest(tmp_path: Path) -> None:
    from chat_to_howto.domain.manifest import BatchManifest

    writer, output_root, _ = _writer(tmp_path)
    messages = (
        _message("conversation-b", 2, text="第二条合成正文"),
        _message("conversation-a", 1, text="第一条合成正文"),
    )

    result = writer.write_batch(
        batch_id="batch-001",
        snapshot_id="snapshot-001",
        scope=_scope(),
        messages=messages,
        conversation_evidence=_evidence(),
        source_type="qq",
        created_at=datetime(2026, 9, 1, 2, tzinfo=timezone.utc),
    )

    assert result.directory == output_root / "exports" / "batch-001"
    assert sorted(path.name for path in result.directory.iterdir()) == [
        "manifest.json",
        "messages.jsonl",
        "messages.txt",
    ]
    assert result.directory.stat().st_mode & 0o777 == 0o700
    for child in result.directory.iterdir():
        assert child.stat().st_mode & 0o777 == 0o600

    records = [json.loads(line) for line in result.jsonl_path.read_text().splitlines()]
    assert [item["conversation_id"] for item in records] == [
        "conversation-a",
        "conversation-b",
    ]
    assert all(item["export_batch_id"] == "batch-001" for item in records)
    assert all(item["snapshot_id"] == "snapshot-001" for item in records)
    assert "第一条合成正文" in result.txt_path.read_text()
    assert "第二条合成正文" in result.txt_path.read_text()

    manifest = BatchManifest.model_validate_json(result.manifest_path.read_text())
    assert manifest.export_batch_id == "batch-001"
    assert manifest.snapshot_id == "snapshot-001"
    assert manifest.scope_fingerprint == _scope().fingerprint()
    assert manifest.output_root_id == "approved-root"
    assert manifest.saved_count == 2
    assert manifest.fetched_count == 2
    assert manifest.completeness == "source_range_verified"
    assert manifest.source_tool_version == "fixture-tool-1"
    assert manifest.client_version == "fixture-client-1"
    assert manifest.account_version == "fixture-account-1"
    assert len(manifest.conversations) == 2
    assert {item.relative_path for item in manifest.artifacts} == {
        "messages.jsonl",
        "messages.txt",
    }
    assert "manifest.json" not in {item.relative_path for item in manifest.artifacts}
    for artifact in manifest.artifacts:
        payload = (result.directory / artifact.relative_path).read_bytes()
        assert artifact.byte_size == len(payload)
        assert artifact.sha256 == sha256(payload).hexdigest()


def test_verification_streams_artifacts_larger_than_manifest_read_limit(tmp_path: Path) -> None:
    """Large chat artifacts are hashed as streams, not loaded through a small-file helper."""
    writer, _, _ = _writer(tmp_path)
    large_synthetic_body = "合" * (3 * 1024 * 1024)

    result = writer.write_batch(
        batch_id="batch-large",
        snapshot_id="snapshot-large",
        scope=_scope(),
        messages=(_message("conversation-a", 1, text=large_synthetic_body),),
        conversation_evidence=_evidence(),
        source_type="synthetic",
    )

    assert result.jsonl_path.stat().st_size > 8 * 1024 * 1024
    assert result.manifest.saved_count == 1


@pytest.mark.parametrize("status,expected", [
    ("source_range_verified", "source_range_verified"),
    ("partial", "partial"),
    ("unknown", "unknown"),
])
def test_manifest_aggregates_completeness_conservatively(
    tmp_path: Path, status: str, expected: str
) -> None:
    writer, _, _ = _writer(tmp_path)
    evidence = _evidence()
    evidence["conversation-b"] = replace(
        evidence["conversation-b"], status=status  # type: ignore[arg-type]
    )

    result = writer.write_batch(
        batch_id=f"batch-{status}",
        snapshot_id=f"snapshot-{status}",
        scope=_scope(),
        messages=(
            _message("conversation-a", 1),
            _message("conversation-b", 1),
        ),
        conversation_evidence=evidence,
        source_type="qq",
    )

    manifest = json.loads(result.manifest_path.read_text())
    assert manifest["completeness"] == expected


def test_all_explicit_empty_conversations_produce_empty_manifest(tmp_path: Path) -> None:
    writer, _, _ = _writer(tmp_path)
    evidence = _evidence("empty")
    evidence = {
        key: replace(
            item,
            fetched_count=0,
            accepted_count=0,
            observed_start_ms=None,
            observed_end_ms=None,
            slices=(),
        )
        for key, item in evidence.items()
    }

    result = writer.write_batch(
        batch_id="batch-empty",
        snapshot_id="snapshot-empty",
        scope=_scope(),
        messages=(),
        conversation_evidence=evidence,
        source_type="wechat",
    )

    manifest = json.loads(result.manifest_path.read_text())
    assert manifest["completeness"] == "empty"
    assert manifest["earliest_saved_at_utc"] is None
    assert manifest["latest_saved_at_utc"] is None


@pytest.mark.parametrize(
    "message,match",
    [
        (_message("conversation-a", 1, sent_at=datetime(2026, 8, 31, tzinfo=timezone.utc)), "range"),
        (_message("conversation-a", 1, sent_at=datetime(2026, 9, 2, tzinfo=timezone.utc)), "range"),
        (_message("conversation-a", 1).model_copy(update={"sent_at_utc": None}), "timestamp"),
        (_message("not-selected", 1), "selected"),
        (_message("conversation-a", 1).model_copy(update={"account_id": "account-b"}), "scope"),
    ],
)
def test_writer_rejects_unknown_time_out_of_range_or_wrong_scope(
    tmp_path: Path, message: StoredMessage, match: str
) -> None:
    writer, output_root, _ = _writer(tmp_path)

    with pytest.raises(ValueError, match=match):
        writer.write_batch(
            batch_id="batch-invalid",
            snapshot_id="snapshot-invalid",
            scope=_scope(),
            messages=(message,),
            conversation_evidence=_evidence(),
            source_type="qq",
        )

    assert not (output_root / "exports" / "batch-invalid").exists()


@pytest.mark.parametrize("batch_id", ["../escape", "/absolute", "a/b", ".", "..", ""])
def test_writer_rejects_nonopaque_batch_ids(tmp_path: Path, batch_id: str) -> None:
    writer, _, _ = _writer(tmp_path)
    with pytest.raises(ValueError, match="opaque batch ID"):
        writer.write_batch(
            batch_id=batch_id,
            snapshot_id="snapshot-safe",
            scope=_scope(),
            messages=(),
            conversation_evidence=_evidence("empty"),
            source_type="qq",
        )


def test_writer_never_overwrites_an_existing_batch(tmp_path: Path) -> None:
    writer, output_root, _ = _writer(tmp_path)
    existing = output_root / "exports" / "batch-existing"
    existing.mkdir(parents=True)
    (output_root / "exports").chmod(0o700)
    marker = existing / "keep.txt"
    marker.write_text("preserve")

    with pytest.raises(FileExistsError):
        writer.write_batch(
            batch_id="batch-existing",
            snapshot_id="snapshot-new",
            scope=_scope(),
            messages=(),
            conversation_evidence=_evidence("empty"),
            source_type="qq",
        )

    assert marker.read_text() == "preserve"


def test_disk_full_failure_cleans_only_owned_temp_and_never_commits(tmp_path: Path) -> None:
    def fail(stage: str) -> None:
        if stage == "after_jsonl":
            raise OSError(errno.ENOSPC, "synthetic disk full")

    writer, output_root, _ = _writer(tmp_path, fault_hook=fail)
    exports = output_root / "exports"
    exports.mkdir(exist_ok=True)
    exports.chmod(0o700)
    unrelated = exports / ".unrelated.tmp"
    unrelated.mkdir()

    with pytest.raises(OSError) as raised:
        writer.write_batch(
            batch_id="batch-disk-full",
            snapshot_id="snapshot-disk-full",
            scope=_scope(),
            messages=(_message("conversation-a", 1),),
            conversation_evidence=_evidence(),
            source_type="qq",
        )

    assert raised.value.errno == errno.ENOSPC
    assert unrelated.is_dir()
    assert not (exports / "batch-disk-full").exists()
    assert not any(path.name.startswith(".batch-disk-full.tmp-") for path in exports.iterdir())


def test_verification_detects_tampering_before_commit_and_cleans_temp(tmp_path: Path) -> None:
    output_holder: dict[str, Path] = {}

    def tamper(stage: str) -> None:
        if stage != "after_txt":
            return
        temporary = next(
            path
            for path in (output_holder["root"] / "exports").iterdir()
            if path.name.startswith(".batch-tampered.tmp-")
        )
        with (temporary / "messages.jsonl").open("ab") as stream:
            stream.write(b"synthetic-corruption")

    writer, output_root, _ = _writer(tmp_path, fault_hook=tamper)
    output_holder["root"] = output_root

    with pytest.raises(OSError, match="verification failed"):
        writer.write_batch(
            batch_id="batch-tampered",
            snapshot_id="snapshot-tampered",
            scope=_scope(),
            messages=(_message("conversation-a", 1),),
            conversation_evidence=_evidence(),
            source_type="synthetic",
        )

    assert not (output_root / "exports" / "batch-tampered").exists()
    assert not any(path.name.startswith(".batch-tampered.tmp-") for path in (output_root / "exports").iterdir())


def test_writer_rejects_public_or_symlink_managed_exports_directory(tmp_path: Path) -> None:
    from chat_to_howto.services.artifacts import ApprovedOutputRoot, ArtifactWriter

    repository_root = tmp_path / "repo"
    repository_root.mkdir()

    public_root = tmp_path / "public-root"
    public_root.mkdir()
    (public_root / "exports").mkdir(mode=0o755)
    public_writer = ArtifactWriter(
        ApprovedOutputRoot.approve(
            public_root, root_id="approved-root", repository_root=repository_root
        )
    )
    with pytest.raises(PermissionError, match="not private"):
        public_writer.write_batch(
            batch_id="batch-public",
            snapshot_id="snapshot-public",
            scope=_scope(),
            messages=(),
            conversation_evidence=_evidence("empty"),
            source_type="qq",
        )

    symlink_root = tmp_path / "symlink-root"
    symlink_root.mkdir()
    escape_target = tmp_path / "escape-target"
    escape_target.mkdir()
    (symlink_root / "exports").symlink_to(escape_target, target_is_directory=True)
    symlink_writer = ArtifactWriter(
        ApprovedOutputRoot.approve(
            symlink_root, root_id="approved-root", repository_root=repository_root
        )
    )
    with pytest.raises(OSError):
        symlink_writer.write_batch(
            batch_id="batch-symlink",
            snapshot_id="snapshot-symlink",
            scope=_scope(),
            messages=(),
            conversation_evidence=_evidence("empty"),
            source_type="qq",
        )
    assert list(escape_target.iterdir()) == []


def test_writer_requires_root_bound_scope_and_exact_evidence_set(tmp_path: Path) -> None:
    writer, _, _ = _writer(tmp_path)
    with pytest.raises(PermissionError, match="approved output root"):
        writer.write_batch(
            batch_id="batch-wrong-root",
            snapshot_id="snapshot-wrong-root",
            scope=_scope("another-root"),
            messages=(),
            conversation_evidence=_evidence("empty"),
            source_type="qq",
        )
    incomplete = _evidence("empty")
    del incomplete["conversation-b"]
    with pytest.raises(ValueError, match="every selected conversation"):
        writer.write_batch(
            batch_id="batch-missing-evidence",
            snapshot_id="snapshot-missing-evidence",
            scope=_scope(),
            messages=(),
            conversation_evidence=incomplete,
            source_type="qq",
        )


def test_writer_requires_evidence_for_the_exact_requested_range(tmp_path: Path) -> None:
    writer, _, _ = _writer(tmp_path)
    evidence = _evidence("empty")
    evidence["conversation-a"] = replace(
        evidence["conversation-a"], requested_end_ms=1788307200001
    )
    with pytest.raises(ValueError, match="requested range"):
        writer.write_batch(
            batch_id="batch-evidence-range",
            snapshot_id="snapshot-evidence-range",
            scope=_scope(),
            messages=(),
            conversation_evidence=evidence,
            source_type="qq",
        )


def test_approval_rejects_repository_descendant_and_symlink_root(tmp_path: Path) -> None:
    from chat_to_howto.services.artifacts import ApprovedOutputRoot

    repository_root = tmp_path / "repo"
    repository_root.mkdir()
    inside = repository_root / "exports"
    inside.mkdir()
    with pytest.raises(ValueError, match="outside the repository"):
        ApprovedOutputRoot.approve(
            inside, root_id="approved-root", repository_root=repository_root
        )

    real = tmp_path / "real-output"
    real.mkdir()
    alias = tmp_path / "output-alias"
    alias.symlink_to(real, target_is_directory=True)
    with pytest.raises(ValueError, match="symbolic link"):
        ApprovedOutputRoot.approve(
            alias, root_id="approved-root", repository_root=repository_root
        )


def test_replaced_approved_root_is_rejected_without_writing_to_replacement(tmp_path: Path) -> None:
    from chat_to_howto.services.artifacts import ApprovedOutputRoot, ArtifactWriter

    repository_root = tmp_path / "repo"
    repository_root.mkdir()
    output_root = tmp_path / "approved"
    output_root.mkdir()
    approved = ApprovedOutputRoot.approve(
        output_root, root_id="approved-root", repository_root=repository_root
    )
    moved = tmp_path / "approved-original"
    output_root.rename(moved)
    output_root.mkdir()

    with pytest.raises(PermissionError, match="identity changed"):
        ArtifactWriter(approved).write_batch(
            batch_id="batch-root-swap",
            snapshot_id="snapshot-root-swap",
            scope=_scope(),
            messages=(),
            conversation_evidence=_evidence("empty"),
            source_type="qq",
        )

    assert list(output_root.iterdir()) == []
    assert list(moved.iterdir()) == []
