"""Real temporary SQLite/files; only the external adapter is synthetic."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import stat

import pytest

from chat_to_howto.domain.consent import ConsentRecord, ConsentScope
from chat_to_howto.domain.sources import SourceCapability, SourceFingerprint
from chat_to_howto.sources.base import SourceMessage
from conftest import END, END_MS, START, START_MS
from ._writer_probe import ArtifactProbe, inode


STAGES = ["pending_authorization", "connecting", "reading", "saving", "verifying"]


def domain_verification_inputs():
    source = SourceCapability(source_id="qq").start_configuration()
    versions = SourceFingerprint(client_version="c1", tool_version="t1", account_version="a1")
    # Compatibility only: the existing domain still calls the opaque root-ID
    # slot output_path. No real path is used; migrate to output_root_id when
    # that domain field lands.
    output_field = "output_root_id" if "output_root_id" in ConsentScope.model_fields else "output_path"
    scope = ConsentScope(
        source_id="qq", account_id="fixture-account", conversation_id="fixture-conversation",
        range_start=START, range_end=END, **{output_field: "fixture-root"},
        operation="export_verification", configuration_nonce=source.configuration_nonce,
        **versions.model_dump(),
    )
    grant = ConsentRecord.grant(scope, expires_at=datetime.now(timezone.utc) + timedelta(minutes=5))
    return source, dict(fingerprint=versions, current_fingerprint=versions, export_scope=scope, export_consent=grant)


def test_domain_requires_explicit_evidence_class_before_readiness():
    source, kwargs = domain_verification_inputs()
    with pytest.raises(TypeError):
        source.record_verification(**kwargs)


@pytest.mark.parametrize("evidence", ["synthetic", "mock", "health", "documentation"])
def test_domain_cannot_promote_non_device_evidence_to_ready(evidence):
    source, kwargs = domain_verification_inputs()
    try:
        result = source.record_verification(**kwargs, evidence_class=evidence)
    except ValueError:
        return
    assert result.status.value != "ready"


def test_domain_accepts_real_device_evidence_only_with_existing_matching_consent():
    source, kwargs = domain_verification_inputs()
    ready = source.record_verification(**kwargs, evidence_class="real_device")
    assert ready.status.value == "ready"
    assert SourceCapability(source_id="wechat").status.value == "unconfigured"
    changed = ready.with_current_versions(SourceFingerprint(client_version="c2", tool_version="t1", account_version="a1"))
    assert changed.status.value != "ready"


def test_p0_states_are_exact_and_illegal_shortcuts_cannot_report_success(p0):
    from chat_to_howto.services.p0_verifier import VerificationState

    assert {state.value for state in VerificationState} == {
        *STAGES, "complete", "partial", "failed", "cancelled",
    }
    p0.start()
    job_id = p0.new_job()
    with pytest.raises(ValueError):
        p0.database.transition_job(job_id, "complete")
    with pytest.raises(ValueError):
        p0.database.transition_job(job_id, "indexing")
    for state in STAGES[1:]:
        p0.database.transition_job(job_id, state)
    p0.database.transition_job(job_id, "partial")
    with pytest.raises(ValueError):
        p0.database.transition_job(job_id, "reading")
    response = p0.request("GET", f"/api/sources/qq/verification-jobs/{job_id}")
    assert response.json()["state"] == "partial"
    assert "percent" not in response.text and "percentage" not in response.text


@pytest.mark.parametrize("state", STAGES)
def test_restart_marks_every_nonterminal_job_interrupted_without_rereading(p0, state):
    p0.start()
    job_id = p0.new_job()
    for next_state in STAGES[1:STAGES.index(state) + 1]:
        p0.database.transition_job(job_id, next_state)
    p0.stack.close()
    p0.start()
    p0.bootstrap()
    response = p0.request("GET", f"/api/sources/qq/verification-jobs/{job_id}")
    assert response.status_code == 200
    assert response.json()["state"] == "failed"
    assert response.json()["error_code"] == "interrupted"
    assert p0.factory.reads == []


def test_synthetic_success_creates_private_parseable_artifact_with_verified_count_hash_and_range(p0):
    p0.start()
    job_id = p0.new_job()
    report = p0.run(job_id)
    assert report["state"] == "complete"
    assert report["evidence_class"] == "synthetic"
    assert report["completeness"] == "source_range_verified"
    assert report["count"] == 2
    assert report["hash_ok"] is True and report["range_ok"] is True
    files = list(p0.output_root.rglob("*.jsonl"))
    assert len(files) == 1
    artifact = files[0]
    raw = artifact.read_bytes()
    records = [json.loads(line) for line in raw.splitlines()]
    assert len(records) == 2
    assert [record["sent_at_ms"] for record in records] == [START_MS, END_MS - 1]
    assert {record["conversation_id"] for record in records} == {"group:fixture-A"}
    if not all(record["text"] == p0.text for record in records):
        raise AssertionError("synthetic_artifact_body_mismatch")
    assert sha256(raw).hexdigest() == report["sha256"]
    assert stat.S_IMODE(artifact.stat().st_mode) == 0o600
    assert stat.S_IMODE(artifact.parent.stat().st_mode) == 0o700
    assert all(source["status"] != "ready" for source in p0.request("GET", "/api/sources").json()["sources"])
    assert not list(p0.output_root.rglob("*.tmp"))
    p0.allow_successful_artifacts(artifact)


@pytest.mark.parametrize("completeness", ["partial", "unknown", "empty"])
def test_incomplete_or_empty_evidence_is_not_complete_success(p0, completeness):
    p0.factory.completeness = completeness
    if completeness == "empty":
        p0.factory.messages = lambda conversation_id: ()
    p0.start()
    report = p0.run(p0.new_job())
    assert report["completeness"] == completeness
    assert report["state"] == "partial"
    assert report["evidence_class"] == "synthetic"
    p0.allow_successful_artifacts()


@pytest.mark.parametrize("bad_record", ["other_conversation", "before_start", "at_end", "unknown_time"])
def test_verifier_rechecks_adapter_scope_before_any_success_artifact(p0, bad_record):
    records = {
        "other_conversation": SourceMessage("m", "group:not-selected", START_MS, p0.text, 0),
        "before_start": SourceMessage("m", "group:fixture-A", START_MS - 1, p0.text, 0),
        "at_end": SourceMessage("m", "group:fixture-A", END_MS, p0.text, 0),
        "unknown_time": SourceMessage("m", "group:fixture-A", None, p0.text, 0),
    }
    p0.factory.messages = lambda conversation_id: (records[bad_record],)
    p0.start()
    report = p0.run(p0.new_job())
    assert report["state"] == "failed"
    assert report["error_code"]
    assert not list(p0.output_root.rglob("*.jsonl"))
    assert not list(p0.output_root.rglob("*.tmp"))


def test_adapter_version_mismatch_invalidates_approved_job_before_read(p0):
    p0.start()
    job_id = p0.new_job()
    p0.factory.fingerprint = SourceFingerprint(client_version="unexpected", tool_version="fixture-tool-1", account_version="fixture-account-1")
    report = p0.run(job_id)
    assert report["state"] == "failed"
    assert report["error_code"] == "version_mismatch"
    assert p0.factory.reads == []


def test_upstream_exception_cannot_leak_token_or_chat_into_any_public_or_persistent_surface(p0):
    p0.start()
    job_id = p0.new_job()
    p0.factory.failure = p0.token + p0.text
    report = p0.run(job_id)
    assert report["state"] == "failed"
    assert report["error_code"]
    assert not list(p0.output_root.rglob("*.jsonl"))
    assert not list(p0.output_root.rglob("*.tmp"))


def test_cancelling_one_job_does_not_cancel_a_later_adapter_instance(p0):
    p0.start()
    first = p0.new_job()
    response = p0.request("POST", f"/api/sources/qq/verification-jobs/{first}/cancel")
    assert response.status_code in (200, 202)
    assert p0.run(first)["state"] == "cancelled"
    assert p0.factory.reads == []
    second_response = p0.submit(p0.select())
    assert second_response.status_code == 202
    assert p0.run(second_response.json()["job_id"])["state"] == "complete"
    assert len(p0.factory.reads) == 1
    p0.allow_successful_artifacts()


@pytest.mark.parametrize("stage", ["reading", "saving", "verifying"])
def test_cancellation_at_each_io_stage_cleans_private_partial_artifacts(p0, monkeypatch, stage):
    p0.start()
    job_id = p0.new_job()
    transition = p0.database.transition_job

    def transition_and_cancel(identifier, state, *args, **kwargs):
        result = transition(identifier, state, *args, **kwargs)
        value = getattr(state, "value", state)
        if identifier == job_id and value == stage:
            p0.app.state.verifier.cancel(job_id)
        return result

    monkeypatch.setattr(p0.database, "transition_job", transition_and_cancel)
    report = p0.run(job_id)
    assert report["state"] == "cancelled"
    assert not list(p0.output_root.rglob("*.jsonl"))
    assert not list(p0.output_root.rglob("*.tmp"))
    if stage == "reading":
        assert p0.factory.reads == []


def test_cancel_during_upstream_read_prevents_first_local_write(p0):
    p0.start()
    job_id = p0.new_job()
    p0.factory.on_read = lambda: p0.app.state.verifier.cancel(job_id)
    report = p0.run(job_id)
    assert report["state"] == "cancelled"
    assert not list(p0.output_root.rglob("*"))


def test_symlink_output_root_is_refused_without_reading(p0, tmp_path):
    actual = p0.output_root
    link = tmp_path / "alias-root"
    link.symlink_to(actual, target_is_directory=True)
    p0.output_root = link
    with pytest.raises(ValueError):
        p0.start()
    assert p0.factory.reads == []


def test_repository_output_root_is_refused_without_creating_files(p0):
    p0.output_root = p0.workspace
    with pytest.raises(ValueError):
        p0.start()
    assert p0.factory.reads == []


def test_writer_uses_exclusive_nofollow_creation_and_file_commit_directory_fsync_order(p0, monkeypatch):
    p0.start()
    job_id = p0.new_job()
    probe = ArtifactProbe(p0.output_root, monkeypatch)
    report = p0.run(job_id)
    assert report["state"] == "complete"
    files = list(p0.output_root.rglob("*.jsonl"))
    assert len(files) == 1
    artifact = files[0]
    file_inode = inode(artifact.stat())
    directory_inode = inode(artifact.parent.stat())
    create = next(event for event in probe.events if event[0] == "create" and event[1] == file_inode)
    assert create[3] & os.O_EXCL and create[3] & os.O_NOFOLLOW
    file_sync = next(index for index, event in enumerate(probe.events) if event[:2] == ("file_fsync", file_inode))
    commit = next(index for index, event in enumerate(probe.events) if event[:2] == ("commit", file_inode))
    directory_sync = next(index for index, event in enumerate(probe.events) if event[:2] == ("directory_fsync", directory_inode) and index > commit)
    assert file_sync < commit < directory_sync
    assert any(event[:2] == ("reopen", file_inode) for event in probe.events)
    p0.allow_successful_artifacts(artifact)


@pytest.mark.parametrize("failure", ["file_fsync", "replace", "directory_fsync"])
def test_atomic_writer_failure_never_leaves_a_formal_success_artifact(p0, monkeypatch, failure):
    p0.start()
    job_id = p0.new_job()
    probe = ArtifactProbe(p0.output_root, monkeypatch, failure=failure)
    report = p0.run(job_id)
    expected_event = "replace_attempt" if failure == "replace" else failure
    assert any(event[0] == expected_event for event in probe.events)
    assert report["state"] == "failed"
    assert report["error_code"]
    assert not list(p0.output_root.rglob("*.jsonl"))
    assert not list(p0.output_root.rglob("*.tmp"))


def test_existing_job_directory_collision_is_never_overwritten(p0, monkeypatch):
    p0.start()
    job_id = p0.new_job()
    probe = ArtifactProbe(p0.output_root, monkeypatch)
    collisions = []

    def occupy_first_job_directory(path, mode):
        if not collisions and path.parent == p0.output_root:
            probe.real_mkdir(path, mode)
            marker = path / "existing-owner.txt"
            marker.write_text("existing file must survive")
            collisions.append(marker)
    probe.before_mkdir = occupy_first_job_directory
    report = p0.run(job_id)
    assert collisions, "test must reach the directory collision boundary"
    assert collisions[0].read_text() == "existing file must survive"
    assert list(collisions[0].parent.iterdir()) == collisions
    assert report["state"] in ("complete", "failed")
    if report["state"] == "complete":
        p0.allow_successful_artifacts()


@pytest.mark.parametrize("collision", ["file", "symlink"])
def test_temp_file_collisions_and_symlinks_cannot_overwrite_an_existing_file(p0, monkeypatch, tmp_path, collision):
    p0.start()
    job_id = p0.new_job()
    unrelated = tmp_path / "unrelated-existing.txt"
    unrelated.write_text("existing synthetic owner")
    probe = ArtifactProbe(p0.output_root, monkeypatch)
    collisions = []

    def occupy_first_created_file(path, flags):
        if not collisions:
            if collision == "symlink":
                path.symlink_to(unrelated)
            else:
                descriptor = probe.real_open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                os.write(descriptor, b"existing synthetic owner")
                os.close(descriptor)
            collisions.append(path)
    probe.before_create = occupy_first_created_file
    report = p0.run(job_id)
    assert collisions, "test must reach exclusive temporary file creation"
    assert unrelated.read_text() == "existing synthetic owner"
    # An unrelated collision belongs to its original owner, including after failure.
    assert collisions[0].read_text() == "existing synthetic owner"
    assert report["state"] in ("complete", "failed")
    if report["state"] == "complete":
        p0.allow_successful_artifacts()


def test_approved_root_replaced_by_symlink_is_refused_at_write_time(p0, tmp_path):
    p0.start()
    job_id = p0.new_job()
    escaped = tmp_path / "unapproved-target"
    escaped.mkdir()
    original = p0.output_root.with_name("original-approved-root")
    p0.output_root.rename(original)
    p0.output_root.symlink_to(escaped, target_is_directory=True)
    report = p0.run(job_id)
    assert report["state"] == "failed"
    assert list(escaped.iterdir()) == []


def test_reopen_detects_tampering_instead_of_trusting_streaming_hash(p0, monkeypatch):
    p0.start()
    job_id = p0.new_job()
    transition = p0.database.transition_job
    tampered = []

    def tamper_after_save_before_verify(identifier, state, *args, **kwargs):
        result = transition(identifier, state, *args, **kwargs)
        if identifier == job_id and getattr(state, "value", state) == "verifying":
            files = [path for path in p0.output_root.rglob("*") if path.is_file() and not path.is_symlink()]
            assert len(files) == 1, "expected one closed artifact before verification"
            path = files[0]
            records = [json.loads(line) for line in path.read_bytes().splitlines()]
            assert len(records) == 2
            before_bounds = [record["sent_at_ms"] for record in records]
            records[0]["text"] = "changed but valid synthetic body"
            encoded = b"".join(json.dumps(record, ensure_ascii=False).encode() + b"\n" for record in records)
            descriptor = os.open(path, os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(encoded)
            # Count, JSON validity, conversation and time scope remain intact.
            reparsed = [json.loads(line) for line in path.read_bytes().splitlines()]
            assert len(reparsed) == 2
            assert [record["sent_at_ms"] for record in reparsed] == before_bounds
            tampered.append(inode(path.stat()))
        return result

    monkeypatch.setattr(p0.database, "transition_job", tamper_after_save_before_verify)
    report = p0.run(job_id)
    assert tampered, "verification must reopen the artifact"
    assert report["state"] == "failed"
    assert report["hash_ok"] is False
    assert report["error_code"] == "artifact_hash_mismatch"
    assert not list(p0.output_root.rglob("*.jsonl"))
    assert not list(p0.output_root.rglob("*.tmp"))
