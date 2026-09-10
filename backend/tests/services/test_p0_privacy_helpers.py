"""Scanner self-tests with wholly synthetic roots and fixed failure text."""

from pathlib import Path
from types import SimpleNamespace
import builtins
import io
import logging
import os
import secrets
import shutil
import subprocess
import sys

import pytest

from conftest import PrivacyScan, register_p0_privacy_scan
from ._writer_probe import ArtifactProbe, inode


def scanner():
    return PrivacyScan(
        message_sentinels=("synthetic-body-marker",),
        secret_sentinels=("synthetic-token-marker", "synthetic-session-marker", "synthetic-csrf-marker"),
        caplog=SimpleNamespace(text="", get_records=lambda phase: []),
        capsys=SimpleNamespace(readouterr=lambda: SimpleNamespace(out="", err="")),
    )


def test_scanner_never_opens_unlisted_workspace_files(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    listed = workspace / "reviewed.py"
    listed.write_text("# safe synthetic source")
    unlisted = workspace / "private-data.json"
    unlisted.write_text("synthetic-token-marker")
    seen = []
    real_open = os.open
    real_io_open, real_builtin_open = io.open, builtins.open

    def guard_stream(original):
        def opened(path, *args, **kwargs):
            if not isinstance(path, int) and Path(path).absolute() == unlisted.absolute():
                raise AssertionError("unlisted_file_was_opened")
            return original(path, *args, **kwargs)
        return opened

    def guard(path, *args, **kwargs):
        if Path(path).absolute() == unlisted.absolute():
            raise AssertionError("unlisted_file_was_opened")
        seen.append(Path(path))
        return real_open(path, *args, **kwargs)

    def forbid_discovery(*args, **kwargs):
        raise AssertionError("workspace_discovery_forbidden")

    monkeypatch.setattr(os, "open", guard)
    monkeypatch.setattr(io, "open", guard_stream(real_io_open))
    monkeypatch.setattr(builtins, "open", guard_stream(real_builtin_open))
    monkeypatch.setattr(Path, "rglob", forbid_discovery)
    scanner().scan_files(runtime_root=runtime, tracked_text_paths=(listed,))
    assert seen == [listed]


def test_only_registered_private_message_artifact_may_contain_body(tmp_path):
    artifact = tmp_path / "messages.jsonl"
    artifact.write_text('{"text":"synthetic-body-marker"}\n')
    check = scanner()
    check.allow_private_message_artifact(artifact)
    check.scan_files(runtime_root=tmp_path)


@pytest.mark.parametrize("secret", ["synthetic-token-marker", "synthetic-session-marker", "synthetic-csrf-marker"], ids=["token", "session", "csrf"])
def test_private_artifact_never_exempts_secret_material(tmp_path, secret):
    artifact = tmp_path / "messages.jsonl"
    artifact.write_text(secret)
    check = scanner()
    check.allow_private_message_artifact(artifact)
    with pytest.raises(AssertionError, match="^privacy_sentinel_leak$"):
        check.scan_files(runtime_root=tmp_path)


def test_extra_jsonl_cannot_inherit_successful_artifact_exemption(tmp_path):
    artifact = tmp_path / "messages.jsonl"
    artifact.write_text("synthetic-body-marker")
    extra = tmp_path / "unexpected.jsonl"
    extra.write_text("synthetic-body-marker")
    check = scanner()
    check.allow_private_message_artifact(artifact)
    with pytest.raises(AssertionError, match="^privacy_sentinel_leak$"):
        check.scan_files(runtime_root=tmp_path)
    with pytest.raises(AssertionError, match="^multiple_private_artifact_allowlist$"):
        check.allow_private_message_artifact(extra)


def test_response_headers_are_part_of_scanner_boundary(tmp_path):
    check = scanner()
    check.payloads.append([("X-Debug", "synthetic-token-marker")])
    with pytest.raises(AssertionError, match="^privacy_sentinel_leak$"):
        check.verify(database_path=tmp_path / "absent.sqlite", runtime_root=tmp_path)


@pytest.mark.parametrize("phase", ["setup", "call", "teardown"])
def test_scanner_checks_original_records_from_every_pytest_phase(tmp_path, phase):
    check = scanner()
    record = logging.LogRecord("synthetic-fixture", logging.WARNING, "fixture.py", 1, "payload=%s", ("synthetic-token-marker",), None)
    check.caplog = SimpleNamespace(text="", get_records=lambda requested: [record] if requested == phase else [])
    with pytest.raises(AssertionError, match="^privacy_sentinel_leak$"):
        check.verify(database_path=tmp_path / "absent.sqlite", runtime_root=tmp_path)


def test_artifact_probe_tracks_dirfd_fdopen_writer_and_ignores_unrelated_io(tmp_path, monkeypatch):
    root = tmp_path / "private-output"
    root.mkdir()
    probe = ArtifactProbe(root, monkeypatch)
    unrelated = os.open(tmp_path / "unrelated", os.O_CREAT | os.O_WRONLY, 0o600)
    os.fsync(unrelated)
    os.close(unrelated)
    assert probe.events == []
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    os.mkdir("job", 0o700, dir_fd=root_fd)
    job_fd = os.open("job", os.O_RDONLY | os.O_DIRECTORY, dir_fd=root_fd)
    descriptor = os.open("private.tmp", os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_WRONLY, 0o600, dir_fd=job_fd)
    key = inode(os.fstat(descriptor))
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(b"synthetic fixture")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace("private.tmp", "messages.jsonl", src_dir_fd=job_fd, dst_dir_fd=job_fd)
    os.fsync(job_fd)
    descriptor = os.open("messages.jsonl", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=job_fd)
    with os.fdopen(descriptor, "rb") as stream:
        assert stream.read() == b"synthetic fixture"
    os.close(job_fd)
    os.close(root_fd)
    assert [event[0] for event in probe.events] == ["create", "file_fsync", "replace_attempt", "commit", "directory_fsync", "reopen"]
    assert all(event[1] == key for event in probe.events if event[0] != "directory_fsync")


@pytest.mark.parametrize("case", ["log", "exception", "richdiff", "clean"])
def test_nested_pytest_catches_call_logs_and_redacts_failure_reports(tmp_path, request, caplog, capsys, case):
    """Run real pytest, with random input confined to a private fixture file."""
    token = "nested-synthetic-" + secrets.token_hex(24)
    outer_scan = PrivacyScan(message_sentinels=(), secret_sentinels=(token,), caplog=caplog, capsys=capsys)
    register_p0_privacy_scan(request.node, outer_scan)
    nested = tmp_path / "nested-pytest"
    tests = nested / "backend" / "tests"
    tests.mkdir(parents=True)
    source_root = Path(__file__).resolve().parents[3]
    # Copy only the known test plugin and its explicitly named import closure.
    # No recursive workspace copy, runtime secrets, env files or repository scan.
    relative_files = (
        "backend/tests/conftest.py",
        "backend/src/chat_to_howto/__init__.py",
        "backend/src/chat_to_howto/config.py",
        "backend/src/chat_to_howto/domain/__init__.py",
        "backend/src/chat_to_howto/domain/consent.py",
        "backend/src/chat_to_howto/domain/sources.py",
        "backend/src/chat_to_howto/security/__init__.py",
        "backend/src/chat_to_howto/security/secrets.py",
        "backend/src/chat_to_howto/sources/__init__.py",
        "backend/src/chat_to_howto/sources/base.py",
    )
    for relative in relative_files:
        destination = nested / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_root / relative, destination)
    private_input = tests / "synthetic-input.txt"
    descriptor = os.open(private_input, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        stream.write(token)
    test_source = '''from pathlib import Path
import logging
import pytest
from conftest import PrivacyScan, register_p0_privacy_scan

@pytest.fixture
def guard(request, caplog, capsys, tmp_path):
    token = Path(__file__).with_name("synthetic-input.txt").read_text()
    check = PrivacyScan(message_sentinels=(), secret_sentinels=(token,), caplog=caplog, capsys=capsys)
    register_p0_privacy_scan(request.node, check)
    caplog.set_level(logging.INFO)
    yield token
    check.verify(database_path=tmp_path / "absent.sqlite", runtime_root=tmp_path)

def test_nested_p0(guard):
    case = CASE_VALUE
    logger = logging.getLogger("synthetic-nested-test")
    if case == "log":
        logger.warning("ordinary-context payload=%s", guard)
    elif case == "exception":
        try:
            raise RuntimeError(guard)
        except RuntimeError:
            logger.exception("ordinary-exception-context")
    elif case == "richdiff":
        assert {"payload": guard} == {"payload": "expected-public-value"}
    else:
        logger.warning("ordinary-safe-diagnostic")
'''.replace("CASE_VALUE", repr(case))
    (tests / "test_nested_p0.py").write_text(test_source)
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "backend/tests/test_nested_p0.py", "-q", "--tb=short", "--showlocals", "-rA", "-o", "log_cli=false"],
        cwd=nested,
        env={"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTHONDONTWRITEBYTECODE": "1", "PATH": str(Path(sys.executable).parent) + ":/usr/bin:/bin"},
        capture_output=True, text=True, timeout=30, check=False,
    )
    output = completed.stdout + completed.stderr
    if token in output:
        # Never include child output or the token in a failing assertion diff.
        raise AssertionError("nested_pytest_output_leak")
    expected = 0 if case == "clean" else 1
    if completed.returncode != expected:
        raise AssertionError("nested_pytest_wrong_exit_code")
    if case in ("log", "exception"):
        assert "privacy_sentinel_leak" in output
        assert "<redacted-p0-sentinel>" in output
        assert "ordinary-" in output
    elif case == "richdiff":
        assert "AssertionError" in output
        assert "expected-public-value" in output
        assert "<redacted-p0-sentinel>" in output
    else:
        assert "1 passed" in output
        assert "ordinary-safe-diagnostic" in output
        assert "privacy_sentinel_leak" not in output
