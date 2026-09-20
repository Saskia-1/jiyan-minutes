"""Real temporary SQLite/files; only the external adapter is synthetic."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
import stat
from threading import Event, Thread
import time

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


def test_run_jobs_true_schedules_and_completes_a_new_job_without_manual_runner_call(p0):
    p0.start(run_jobs=True)
    job_id = p0.new_job()
    for _ in range(100):
        report = p0.request("GET", f"/api/sources/qq/verification-jobs/{job_id}").json()
        if report["state"] in {"complete", "partial", "failed", "cancelled"}:
            break
        time.sleep(0.01)
    assert report["state"] == "complete"
    assert p0.factory.reads
    p0.allow_successful_artifacts()


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


@pytest.mark.parametrize(
    ("identity", "error_code"),
    [
        ("missing_fingerprint", "adapter_identity_invalid"),
        ("missing_evidence_class", "adapter_evidence_invalid"),
        ("unknown_evidence_class", "adapter_evidence_invalid"),
    ],
)
def test_adapter_must_supply_allowlisted_trusted_identity_before_any_read(p0, identity, error_code):
    """Missing or invented adapter identity must fail closed with a fixed body-free code."""
    p0.start()
    job_id = p0.new_job()
    if identity == "missing_fingerprint":
        p0.factory.fingerprint = None
    elif identity == "missing_evidence_class":
        p0.factory.evidence_class = None
    else:
        p0.factory.evidence_class = "browser_claimed_real_device"
    p0.app.state.verifier.run(job_id)
    report = p0.request("GET", f"/api/sources/qq/verification-jobs/{job_id}").json()
    p0.allow_single_private_artifact_if_present()
    assert report["state"] == "failed"
    assert report["error_code"] == error_code
    assert report["evidence_class"] == "unknown"
    assert p0.factory.reads == []


def test_job_rechecks_the_current_source_snapshot_before_constructing_an_adapter(p0):
    """A queued job cannot silently run under an account/configuration changed out of band."""
    p0.start()
    job_id = p0.new_job()
    instances_before = len(p0.factory.instances)
    p0.database.execute("UPDATE sources SET nonce=? WHERE source_id='qq'", ("changed-out-of-band",))
    p0.app.state.verifier.run(job_id)
    report = p0.request("GET", f"/api/sources/qq/verification-jobs/{job_id}").json()
    p0.allow_single_private_artifact_if_present()
    assert report["state"] == "failed"
    assert report["error_code"] == "source_configuration_changed"
    assert len(p0.factory.instances) == instances_before
    assert p0.factory.reads == []


@pytest.mark.parametrize("source_id", ["qq", "wechat"])
def test_complete_trusted_real_device_evidence_promotes_only_its_matching_source(p0, source_id):
    """Readiness is derived from adapter evidence plus the exact persisted export consent."""
    p0.factory.evidence_class = "real_device"
    p0.start()
    p0.bootstrap()
    p0.configure(source_id)
    selection = p0.select(source_id)
    created = p0.submit(selection)
    assert created.status_code == 202
    job_id = created.json()["job_id"]
    p0.app.state.verifier.run(job_id)
    report = p0.request("GET", f"/api/sources/{source_id}/verification-jobs/{job_id}").json()
    p0.allow_single_private_artifact_if_present()
    assert report["state"] == "complete"
    assert report["evidence_class"] == "real_device"
    sources = {item["source_id"]: item for item in p0.request("GET", "/api/sources").json()["sources"]}
    assert sources[source_id]["status"] == "ready"
    other = "wechat" if source_id == "qq" else "qq"
    assert sources[other]["status"] == "unconfigured"
    persisted = p0.database.one("SELECT * FROM sources WHERE source_id=?", (source_id,))
    assert persisted["verified_job_id"] == job_id
    assert persisted["verified_client_version"] == "fixture-client-1"


def test_browser_evidence_claim_cannot_upgrade_a_synthetic_adapter(p0):
    """Only the adapter object, never request JSON, supplies the evidence class."""
    p0.start()
    p0.bootstrap()
    p0.configure()
    created = p0.submit(p0.select(), evidence_class="real_device")
    assert created.status_code == 202
    report = p0.run(created.json()["job_id"])
    assert report["evidence_class"] == "synthetic"
    source = next(item for item in p0.request("GET", "/api/sources").json()["sources"] if item["source_id"] == "qq")
    assert source["status"] != "ready"
    p0.allow_successful_artifacts()


def test_trusted_adapter_unknown_evidence_is_reported_but_never_promotes_ready(p0):
    """Explicit unknown is a closed evidence value, not a missing identity or ready proof."""
    p0.factory.evidence_class = "unknown"
    p0.start()
    report = p0.run(p0.new_job())
    assert report["state"] == "complete"
    assert report["evidence_class"] == "unknown"
    source = next(item for item in p0.request("GET", "/api/sources").json()["sources"] if item["source_id"] == "qq")
    assert source["status"] != "ready"
    p0.allow_successful_artifacts()


def test_incomplete_real_device_evidence_never_promotes_ready(p0):
    """A real-device label cannot substitute for source-range completeness."""
    p0.factory.evidence_class = "real_device"
    p0.factory.completeness = "partial"
    p0.start()
    report = p0.run(p0.new_job())
    assert report["state"] == "partial"
    source = next(item for item in p0.request("GET", "/api/sources").json()["sources"] if item["source_id"] == "qq")
    assert source["status"] != "ready"
    p0.allow_successful_artifacts()


@pytest.mark.parametrize(
    ("corruption", "error_code"),
    [("count", "artifact_count_mismatch"), ("range", "artifact_range_mismatch")],
)
def test_real_device_readiness_requires_reopened_count_and_range_verification(p0, monkeypatch, corruption, error_code):
    """A matching streaming hash alone cannot establish a verified local artifact."""
    p0.factory.evidence_class = "real_device"
    p0.start()
    job_id = p0.new_job()
    original_reopen = p0.app.state.verifier._reopen

    def corrupt_verdict(path, job):
        digest, count, range_ok = original_reopen(path, job)
        if corruption == "count":
            count += 1
        else:
            range_ok = False
        return digest, count, range_ok

    monkeypatch.setattr(p0.app.state.verifier, "_reopen", corrupt_verdict)
    report = p0.run(job_id)
    p0.allow_single_private_artifact_if_present()
    assert report["state"] == "failed"
    assert report["error_code"] == error_code
    source = next(item for item in p0.request("GET", "/api/sources").json()["sources"] if item["source_id"] == "qq")
    assert source["status"] != "ready"
    assert not list(p0.output_root.rglob("*.jsonl"))


def test_source_snapshot_change_after_reopen_blocks_ready_and_success(p0, monkeypatch):
    """Final promotion must compare-and-set the same source configuration used by the job."""
    p0.factory.evidence_class = "real_device"
    p0.start()
    job_id = p0.new_job()
    original_reopen = p0.app.state.verifier._reopen

    def change_source_after_reopen(path, job):
        verdict = original_reopen(path, job)
        p0.database.execute(
            "UPDATE sources SET account_version=? WHERE source_id='qq'",
            ("changed-after-read",),
        )
        return verdict

    monkeypatch.setattr(p0.app.state.verifier, "_reopen", change_source_after_reopen)
    report = p0.run(job_id)
    p0.allow_single_private_artifact_if_present()
    assert report["state"] == "failed"
    assert report["error_code"] == "source_configuration_changed"
    source = next(item for item in p0.request("GET", "/api/sources").json()["sources"] if item["source_id"] == "qq")
    assert source["status"] != "ready"
    assert not list(p0.output_root.rglob("*.jsonl"))


def test_expired_export_consent_cannot_promote_real_device_evidence(p0):
    """The consent must still match at the final readiness decision, not only job creation."""
    p0.factory.evidence_class = "real_device"
    p0.start()
    job_id = p0.new_job()
    p0.clock.advance(minutes=6)
    report = p0.run(job_id)
    p0.allow_single_private_artifact_if_present()
    assert report["state"] == "failed"
    assert report["error_code"] == "readiness_evidence_invalid"
    source = next(item for item in p0.request("GET", "/api/sources").json()["sources"] if item["source_id"] == "qq")
    assert source["status"] != "ready"
    assert not list(p0.output_root.rglob("*.jsonl"))


def test_atomic_ready_commit_cannot_overwrite_a_concurrent_blocked_status(p0, monkeypatch):
    """The persistence CAS must preserve a safety stop applied after domain validation."""
    p0.factory.evidence_class = "real_device"
    p0.start()
    job_id = p0.new_job()
    original_complete = p0.database.complete_real_device_job

    def block_before_commit(job, **kwargs):
        p0.database.execute("UPDATE sources SET status='blocked' WHERE source_id='qq'")
        return original_complete(job, **kwargs)

    monkeypatch.setattr(p0.database, "complete_real_device_job", block_before_commit)
    report = p0.run(job_id)
    p0.allow_single_private_artifact_if_present()
    assert report["state"] == "failed"
    assert report["error_code"] == "readiness_evidence_invalid"
    source = next(item for item in p0.request("GET", "/api/sources").json()["sources"] if item["source_id"] == "qq")
    assert source["status"] == "blocked"
    assert not list(p0.output_root.rglob("*.jsonl"))


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
            files = [
                path
                for path in p0.output_root.rglob("*.jsonl")
                if path.is_file() and not path.is_symlink()
            ]
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


def test_output_root_real_directory_replacement_is_rejected_before_artifact_creation(p0):
    """Checking only is_dir at write time lets a different directory inherit approval."""
    p0.start()
    job_id = p0.new_job()
    approved_inode = p0.output_root.rename(p0.output_root.with_name("original-approved-root"))
    p0.output_root.mkdir(mode=0o700)

    report = p0.run(job_id)
    p0.allow_single_private_artifact_if_present()

    assert report["state"] == "failed"
    assert report["error_code"] == "output_root_changed"
    assert list(p0.output_root.iterdir()) == []
    assert list(approved_inode.iterdir()) == []


def test_final_artifact_publication_never_overwrites_a_racing_existing_file(p0, monkeypatch):
    """Replace-style publication can destroy a file created after the job directory."""
    p0.start()
    job_id = p0.new_job()
    marker = b"independent-existing-owner"
    real_mkdir = os.mkdir
    collision = []

    def mkdir_then_occupy_final(path, mode=0o777, *, dir_fd=None):
        real_mkdir(path, mode, dir_fd=dir_fd)
        if str(path).startswith("job-") and not collision:
            directory_fd = os.open(
                path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dir_fd
            )
            try:
                descriptor = os.open(
                    "messages.jsonl",
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=directory_fd,
                )
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(marker)
                collision.append(p0.output_root / str(path) / "messages.jsonl")
            finally:
                os.close(directory_fd)

    monkeypatch.setattr(os, "mkdir", mkdir_then_occupy_final)
    report = p0.run(job_id)
    preserved = bool(collision) and collision[0].read_bytes() == marker
    if collision and not preserved:
        p0.allow_single_private_artifact_if_present()

    assert report["state"] == "failed"
    assert report["error_code"] == "artifact_collision"
    assert preserved


def test_failure_cleanup_removes_only_exact_owned_names_and_preserves_unrelated_temp(p0, monkeypatch):
    """Glob cleanup can delete an unrelated file that merely shares a suffix."""
    p0.start()
    job_id = p0.new_job()
    reopen = p0.app.state.verifier._reopen
    decoys = []

    def corrupt_after_creating_unrelated(path, job):
        decoy = path.parent / "independent-owner.tmp"
        decoy.write_bytes(b"independent synthetic file")
        decoys.append(decoy)
        digest, count, range_ok = reopen(path, job)
        return "0" * 64, count, range_ok

    monkeypatch.setattr(p0.app.state.verifier, "_reopen", corrupt_after_creating_unrelated)
    report = p0.run(job_id)

    assert report["state"] == "failed"
    assert report["error_code"] == "artifact_hash_mismatch"
    assert decoys and decoys[0].read_bytes() == b"independent synthetic file"
    assert not (decoys[0].parent / "messages.jsonl").exists()


def test_restart_cleans_only_persisted_owned_artifact_names(p0, monkeypatch):
    """Interrupted jobs must not leave a formal chat artifact after process restart."""
    p0.start()
    job_id = p0.new_job()
    verifier = p0.app.state.verifier
    unrelated = []

    def interrupt_after_publication(path, _job):
        decoy = path.parent / "independent-owner.tmp"
        decoy.write_bytes(b"independent synthetic file")
        unrelated.append(decoy)
        raise SystemExit("synthetic process interruption")

    monkeypatch.setattr(verifier, "_reopen", interrupt_after_publication)
    with pytest.raises(SystemExit, match="synthetic process interruption"):
        verifier.run(job_id)
    p0.stack.close()

    p0.start()
    p0.bootstrap()
    formal = p0.output_root / f"job-{job_id}" / "messages.jsonl"
    if formal.exists():
        p0.allow_single_private_artifact_if_present()

    assert not formal.exists()
    assert unrelated and unrelated[0].read_bytes() == b"independent synthetic file"
    report = p0.request("GET", f"/api/sources/qq/verification-jobs/{job_id}").json()
    assert report["state"] == "failed"
    assert report["error_code"] == "interrupted"


def test_failed_exact_cleanup_retains_owner_proof_for_startup_retry(p0, monkeypatch):
    """A transient unlink failure must not orphan a formal artifact as unowned."""
    p0.start()
    job_id = p0.new_job()
    real_fsync = os.fsync
    real_unlink = os.unlink
    directory_failure = False
    blocked_final_unlinks = 0

    def fail_first_directory_sync(descriptor):
        nonlocal directory_failure
        if stat.S_ISDIR(os.fstat(descriptor).st_mode) and not directory_failure:
            directory_failure = True
            raise OSError("synthetic directory sync failure")
        return real_fsync(descriptor)

    def fail_first_two_final_cleanups(path, *args, **kwargs):
        nonlocal blocked_final_unlinks
        if path == "messages.jsonl" and blocked_final_unlinks < 2:
            blocked_final_unlinks += 1
            raise PermissionError("synthetic transient cleanup failure")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "fsync", fail_first_directory_sync)
    monkeypatch.setattr(os, "unlink", fail_first_two_final_cleanups)
    report = p0.run(job_id)
    formal = p0.output_root / f"job-{job_id}" / "messages.jsonl"

    assert report["state"] == "failed"
    assert formal.exists()
    ownership = p0.database.one(
        "SELECT cleanup_pending,artifact_dir_owned,artifact_final_owned "
        "FROM jobs WHERE job_id=?",
        (job_id,),
    )
    assert ownership == {
        "cleanup_pending": 1,
        "artifact_dir_owned": 1,
        "artifact_final_owned": 1,
    }

    p0.stack.close()
    p0.start()
    p0.bootstrap()
    assert not formal.exists()
    assert p0.database.one(
        "SELECT cleanup_pending FROM jobs WHERE job_id=?", (job_id,)
    )["cleanup_pending"] == 0


def test_database_enforces_private_mode_and_nonzero_p0_schema_version(tmp_path):
    """A group-readable or unversioned P0 database cannot safely retain authority."""
    from chat_to_howto.db import Database

    path = tmp_path / "mode-and-version.sqlite3"
    path.touch(mode=0o666)
    os.chmod(path, 0o666)
    Database(path)
    with sqlite3.connect(path) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert type(version) is int and version > 0


def test_database_rejects_unknown_future_schema_without_rewriting_it(tmp_path):
    """Opening a newer schema as the current shape risks destructive interpretation."""
    from chat_to_howto.db import Database

    path = tmp_path / "future.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version=999")

    with pytest.raises(RuntimeError, match="Unsupported P0 database schema"):
        Database(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 999


def test_cancel_completed_job_reports_completed_semantics_without_state_rewrite(p0):
    """A cancellation response must not claim success after the job already completed."""
    p0.start()
    job_id = p0.new_job()
    assert p0.run(job_id)["state"] == "complete"
    p0.allow_successful_artifacts()

    response = p0.request("POST", f"/api/sources/qq/verification-jobs/{job_id}/cancel")

    assert response.status_code == 200
    assert response.json() == {"job_id": job_id, "state": "complete"}
    assert p0.database.one("SELECT state FROM jobs WHERE job_id=?", (job_id,))["state"] == "complete"


def test_cancel_calls_the_active_adapter_before_unblocking_the_source_read(p0):
    """Persisting cancellation without adapter.cancel leaves upstream work running."""
    p0.start()
    job_id = p0.new_job()
    entered = Event()
    release = Event()

    def block_read():
        entered.set()
        release.wait(timeout=2)

    p0.factory.on_read = block_read
    runner = Thread(target=p0.app.state.verifier.run, args=(job_id,))
    runner.start()
    assert entered.wait(timeout=2)
    response = p0.request("POST", f"/api/sources/qq/verification-jobs/{job_id}/cancel")
    adapter_cancelled = p0.factory.instances[-1].cancelled
    release.set()
    runner.join(timeout=2)

    assert response.status_code == 200
    assert response.json()["state"] == "cancelled"
    assert adapter_cancelled
    assert not runner.is_alive()
    assert p0.request("GET", f"/api/sources/qq/verification-jobs/{job_id}").json()["state"] == "cancelled"
    assert not list(p0.output_root.rglob("*.jsonl"))


@pytest.mark.parametrize(
    ("changes", "error_code"),
    [
        ({"requested_start_ms": START_MS + 1}, "adapter_requested_bounds_mismatch"),
        ({"requested_end_ms": END_MS - 1}, "adapter_requested_bounds_mismatch"),
        ({"fetched_count": 1}, "adapter_fetched_count_invalid"),
        ({"accepted_count": 1}, "adapter_accepted_count_mismatch"),
        ({"observed_start_ms": START_MS + 1}, "adapter_observed_range_mismatch"),
        ({"observed_end_ms": END_MS - 2}, "adapter_observed_range_mismatch"),
        ({"status": "empty"}, "adapter_empty_evidence_invalid"),
    ],
)
def test_adapter_completeness_measurements_are_reconciled_before_success(
    p0, changes, error_code
):
    """A trusted label cannot override contradictory requested/fetched/observed measurements."""
    p0.factory.evidence_changes = changes
    p0.start()

    report = p0.run(p0.new_job())
    p0.allow_single_private_artifact_if_present()

    assert report["state"] == "failed"
    assert report["error_code"] == error_code
    assert report["range_ok"] is False
    assert not list(p0.output_root.rglob("*.jsonl"))


def test_zero_accepted_messages_require_empty_evidence_and_never_complete(p0):
    """A source-range label with no accepted record is not a successful export."""
    p0.factory.messages = lambda _conversation_id: ()
    p0.factory.evidence_changes = {
        "status": "source_range_verified",
        "fetched_count": 0,
        "accepted_count": 0,
        "observed_start_ms": None,
        "observed_end_ms": None,
    }
    p0.start()

    report = p0.run(p0.new_job())
    p0.allow_single_private_artifact_if_present()

    assert report["state"] == "failed"
    assert report["error_code"] == "adapter_empty_evidence_invalid"
    assert report["range_ok"] is False
    assert not list(p0.output_root.rglob("*.jsonl"))
