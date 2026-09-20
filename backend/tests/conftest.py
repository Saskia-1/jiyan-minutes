"""Synthetic-only P0 fixtures. No real sockets, source data, or secrets are used."""

from __future__ import annotations

from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from http.cookies import SimpleCookie
from pathlib import Path
import os
import secrets
import socket
import sqlite3
import sys
import traceback

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from chat_to_howto.config import Settings
from chat_to_howto.domain.sources import SourceFingerprint
from chat_to_howto.security.secrets import InMemorySecretStore
from chat_to_howto.sources.base import (
    CompletenessEvidence, ConversationRef, FetchCancelled, FetchPage,
    SourceHealth, SourceMessage,
)


ORIGIN = "http://127.0.0.1:8765"
START = "2026-09-01T00:00:00+00:00"
END = "2026-09-01T00:00:02+00:00"
START_MS = 1788220800000
END_MS = 1788220802000
PUBLIC_JOB_REQUIRED_FIELDS = {
    "job_id", "source_id", "client_version", "tool_version", "account_version",
    "range_start", "range_end", "timezone", "count", "sha256", "hash_ok",
    "range_ok", "completeness", "state", "evidence_class",
}
PUBLIC_JOB_OPTIONAL_FIELDS = {"error_code", "schema_version", "system_version"}
PUBLIC_JOB_FIELDS = PUBLIC_JOB_REQUIRED_FIELDS | PUBLIC_JOB_OPTIONAL_FIELDS
# Explicit text paths inspected from git ls-files metadata for this task.
# This is a frozen input list, not filesystem discovery. New paths require review.
REVIEWED_TRACKED_TEXT = (
    "backend/pyproject.toml",
    "backend/src/chat_to_howto/config.py",
    "backend/src/chat_to_howto/db.py",
    "backend/src/chat_to_howto/environment.py",
    "backend/src/chat_to_howto/main.py",
    "backend/src/chat_to_howto/domain/consent.py",
    "backend/src/chat_to_howto/domain/sources.py",
    "backend/src/chat_to_howto/security/redaction.py",
    "backend/src/chat_to_howto/security/secrets.py",
    "backend/src/chat_to_howto/services/p0_verifier.py",
    "backend/src/chat_to_howto/sources/base.py",
    "backend/src/chat_to_howto/sources/factory.py",
    "backend/src/chat_to_howto/sources/qce.py",
    "backend/src/chat_to_howto/sources/tracememo.py",
    "backend/src/chat_to_howto/sources/transport.py",
)


class Clock:
    def __init__(self):
        self.now = datetime.now(timezone.utc)

    def __call__(self):
        return self.now

    def advance(self, **kwargs):
        self.now += timedelta(**kwargs)


class RecordingSecretStore(InMemorySecretStore):
    """Real memory-store authorization with body-free side-effect counters."""

    def __init__(self):
        super().__init__()
        self.mutations = 0
        self.references = set()

    def set(self, **kwargs):
        super().set(**kwargs)
        self.mutations += 1
        self.references.add(kwargs["access"].secret_reference)

    def delete(self, **kwargs):
        super().delete(**kwargs)
        self.mutations += 1


class SyntheticAdapter:
    """Implements the existing adapter boundary, with observable scoped reads."""

    def __init__(self, factory, source_id, account_id):
        self.factory = factory
        self.source_id = source_id
        self.account_id = account_id
        self.cancelled = False
        self.fingerprint = factory.fingerprint
        self.evidence_class = factory.evidence_class

    def health(self):
        return SourceHealth(available=True, ready=True)

    def list_accounts(self):
        return ("fixture-account", "fixture-account-2")

    def list_conversations(self):
        if self.factory.require_secret:
            access, consent = self.factory.secret_grant
            value = self.factory.secret_store.get(access=access, consent=consent)
            if value != self.factory.expected_token:
                raise AssertionError("synthetic token mismatch")
            self.factory.secret_reads += 1
        self.factory.enumerations += 1
        return [
            ConversationRef(self.source_id, self.account_id, ident, "同名合成群", "group")
            for ident in self.factory.conversation_ids
        ]

    def fetch_range(self, conversation_id, start_ms, end_ms):
        if self.cancelled:
            raise FetchCancelled("Synthetic cancellation")
        if self.factory.require_secret:
            access, consent = self.factory.secret_grant
            value = self.factory.secret_store.get(access=access, consent=consent)
            if value != self.factory.expected_token:
                raise AssertionError("synthetic token mismatch")
            self.factory.secret_reads += 1
        self.factory.reads.append((self.source_id, self.account_id, conversation_id, start_ms, end_ms))
        if self.factory.on_read:
            self.factory.on_read()
        if self.factory.failure:
            raise RuntimeError(self.factory.failure)
        if self.cancelled:
            raise FetchCancelled("Synthetic cancellation")
        messages = self.factory.messages(conversation_id)
        evidence = {
            "status": self.factory.completeness,
            "reason": "synthetic_fixture",
            "requested_start_ms": start_ms,
            "requested_end_ms": end_ms,
            "observed_start_ms": START_MS if messages else None,
            "observed_end_ms": END_MS - 1 if messages else None,
            "fetched_count": len(messages),
            "accepted_count": len(messages),
            "duplicate_count": 0,
            "unknown_time_count": 0,
            "missing_id_count": 0,
            "parse_error_count": 0,
            "termination": "synthetic_fixture_exhausted",
        }
        evidence.update(self.factory.evidence_changes)
        return FetchPage(messages, None, CompletenessEvidence(**evidence))

    def cancel(self):
        self.cancelled = True


class SyntheticFactory:
    def __init__(self, text):
        self.text = text
        self.fingerprint = SourceFingerprint(
            client_version="fixture-client-1", tool_version="fixture-tool-1",
            account_version="fixture-account-1",
        )
        self.conversation_ids = ("group:fixture-A", "group:fixture-B")
        self.instances = []
        self.reads = []
        self.enumerations = 0
        self.failure = None
        self.on_read = None
        self.completeness = "source_range_verified"
        self.evidence_changes = {}
        self.evidence_class = "synthetic"
        self.require_secret = False
        self.secret_reads = 0
        self.secret_grant = None
        self.secret_store = None
        self.expected_token = None

    def __call__(self, source_id, account_id, *, token_access=None, token_consent=None):
        if token_access is not None or token_consent is not None:
            self.secret_grant = (token_access, token_consent)
        adapter = SyntheticAdapter(self, source_id, account_id)
        self.instances.append(adapter)
        return adapter

    def messages(self, conversation_id):
        return (
            SourceMessage("fixture-m1", conversation_id, START_MS, self.text, 0),
            SourceMessage("fixture-m2", conversation_id, END_MS - 1, self.text, 1),
        )


class PrivacyScan:
    """Scan only owned synthetic runtime files and an injected text-file list.

    This helper never discovers repository paths. Its caller supplies any
    already-reviewed tracked text paths explicitly; ignored/untracked files
    are never enumerated or opened by a workspace scan.
    """

    def __init__(self, *, message_sentinels, secret_sentinels, caplog, capsys):
        self.message_sentinels = set(message_sentinels)
        self.secret_sentinels = set(secret_sentinels)
        self.public_only_sentinels = set()
        self.payloads = []
        self.allowed_artifact = None
        self.caplog = caplog
        self.capsys = capsys

    def inspect(self, payload, *, allow_message=False, public=True):
        encoded = payload if isinstance(payload, bytes) else str(payload).encode()
        forbidden = self.secret_sentinels | (set() if allow_message else self.message_sentinels)
        if public:
            forbidden |= self.public_only_sentinels
        if any(value.encode() in encoded for value in forbidden):
            # A plain fixed exception avoids pytest's value-rich assert rewrite.
            raise AssertionError("privacy_sentinel_leak")

    def allow_private_message_artifact(self, path):
        path = Path(path)
        if path.is_symlink() or not path.is_file() or path.suffix != ".jsonl":
            raise AssertionError("invalid_private_artifact_allowlist")
        if self.allowed_artifact is not None and path != self.allowed_artifact:
            raise AssertionError("multiple_private_artifact_allowlist")
        self.allowed_artifact = path.absolute()

    def scan_files(self, *, runtime_root, tracked_text_paths=(), reference_storage_paths=()):
        # runtime_root is created by the test itself, never the actual repo.
        for directory, directories, files in os.walk(runtime_root, followlinks=False):
            directories[:] = [name for name in directories if not (Path(directory) / name).is_symlink()]
            for name in files:
                path = Path(directory) / name
                if not path.is_symlink():
                    self.inspect(path.read_bytes(), allow_message=path.absolute() == self.allowed_artifact, public=path not in reference_storage_paths)
        for path in tracked_text_paths:
            path = Path(path)
            if path.suffix not in {".py", ".md", ".toml", ".txt", ".ts", ".tsx", ".json"}:
                raise AssertionError("unapproved_scan_file_type")
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(descriptor, "rb") as stream:
                self.inspect(stream.read())

    def verify(self, *, database_path, runtime_root, tracked_text_paths=()):
        capture = self.capsys.readouterr()
        for value in [*self.payloads, capture.out, capture.err, self.caplog.text]:
            self.inspect(value)
        # caplog.text at fixture finalization contains only the teardown stage.
        # Inspect the original LogRecords retained for every pytest phase.
        for phase in ("setup", "call", "teardown"):
            for record in self.caplog.get_records(phase):
                self.inspect(record.getMessage())
                if record.exc_info:
                    self.inspect("".join(traceback.format_exception(*record.exc_info)))
                if record.exc_text:
                    self.inspect(record.exc_text)
                if record.stack_info:
                    self.inspect(record.stack_info)
        if database_path.exists() and database_path.stat().st_size:
            with sqlite3.connect(database_path) as connection:
                names = connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                for (name,) in names:
                    quoted = '"' + name.replace('"', '""') + '"'
                    self.inspect(connection.execute(f"SELECT * FROM {quoted}").fetchall(), public=False)
        reference_storage_paths = {database_path, *(Path(str(database_path) + suffix) for suffix in ("-wal", "-shm", "-journal"))}
        self.scan_files(runtime_root=runtime_root, tracked_text_paths=tracked_text_paths, reference_storage_paths=reference_storage_paths)


class Harness:
    def __init__(self, *, tmp_path, caplog, capsys, monkeypatch):
        self.runtime_root = tmp_path
        self.workspace = tmp_path / "synthetic-workspace"
        self.workspace.mkdir()
        source_snapshot = Path(__file__).resolve().parents[2]
        self.tracked_text_paths = tuple(source_snapshot / path for path in REVIEWED_TRACKED_TEXT)
        self.output_root = tmp_path / "批准的合成导出 目录"
        self.output_root.mkdir(mode=0o700)
        self.second_output_root = tmp_path / "第二批准目录"
        self.second_output_root.mkdir(mode=0o700)
        self.database_path = tmp_path / "p0.sqlite3"
        self.database_path.touch()  # exercise a zero-byte database, not just a missing file
        self.clock = Clock()
        self.text = "fixture-chat-" + secrets.token_hex(24)
        self.token = "fixture-token-" + secrets.token_hex(24)
        self.privacy = PrivacyScan(message_sentinels=(self.text,), secret_sentinels=(self.token,), caplog=caplog, capsys=capsys)
        self.factory = SyntheticFactory(self.text)
        self.store = RecordingSecretStore()
        self.factory.secret_store = self.store
        self.factory.expected_token = self.token
        self.accounts = {"qq": "fixture-account", "wechat": "fixture-account"}
        self.disclosure_confirmations = {}
        self.stack = ExitStack()
        self.settings = Settings(
            host="127.0.0.1", timezone="Asia/Shanghai", data_dir=tmp_path,
            export_root=None, qce_base_url="http://127.0.0.1:40653",
            tracememo_base_url="http://127.0.0.1:6131", llm_provider="unconfigured",
        )
        def deny_network(*args, **kwargs):
            raise AssertionError("P0 synthetic test attempted socket access")
        monkeypatch.setattr(socket.socket, "connect", deny_network)
        monkeypatch.setattr(socket, "create_connection", deny_network)

    def start(
        self, *, run_jobs=False, frontend_dist=None, setup_options=None,
        import_request_max_bytes=91_000_000,
        session_recovery_ttl_seconds=7 * 24 * 60 * 60,
    ):
        # Deliberately deferred: missing P0 modules fail each relevant test,
        # while pytest can still collect the complete RED acceptance suite.
        from chat_to_howto.db import Database
        from chat_to_howto.main import create_app
        from fastapi.testclient import TestClient

        self.database = Database(self.database_path)
        self.app = create_app(
            settings=self.settings, database=self.database,
            adapter_factory=self.factory, secret_store=self.store, clock=self.clock,
            output_roots={"fixture-root": self.output_root, "fixture-root-2": self.second_output_root},
            source_root=self.workspace, origin=ORIGIN, run_jobs=run_jobs,
            session_ttl_seconds=1800, selection_ttl_seconds=300, consent_ttl_seconds=300,
            session_recovery_ttl_seconds=session_recovery_ttl_seconds,
            import_request_max_bytes=import_request_max_bytes,
            frontend_dist=frontend_dist, setup_options=setup_options,
        )
        self.client = self.stack.enter_context(TestClient(self.app, base_url=ORIGIN, raise_server_exceptions=False))
        self.csrf = None
        return self

    def request(self, method, path, *, csrf=True, **kwargs):
        headers = {"Origin": ORIGIN, **kwargs.pop("headers", {})}
        if csrf and self.csrf:
            headers.setdefault("X-CSRF-Token", self.csrf)
        response = self.client.request(method, path, headers=headers, **kwargs)
        if path == "/api/session" and response.status_code == 200:
            self._capture_bootstrap(response)
        else:
            self.privacy.payloads.extend((response.text, list(response.headers.multi_items())))
        return response

    def _capture_bootstrap(self, response):
        data = response.json()
        assert set(data) <= {"csrf_token", "expires_at"}
        cookie = SimpleCookie()
        cookie.load(response.headers["set-cookie"])
        assert len(cookie) == 1
        session = next(iter(cookie.values()))
        if any(value in data["csrf_token"] or value in session.value for value in (self.text, self.token)):
            raise AssertionError("privacy_sentinel_leak")
        self.privacy.secret_sentinels.update((data["csrf_token"], session.value))
        # Only the designated bootstrap fields are exempt from their own new
        # session/CSRF sentinel. All other fields and headers still get scanned.
        self.privacy.inspect({key: value for key, value in data.items() if key != "csrf_token"})
        self.privacy.inspect(list((key, value) for key, value in response.headers.multi_items() if key.lower() != "set-cookie"))
        sanitized_cookie = session.OutputString().replace(session.value, "<bootstrap-cookie>", 1)
        self.privacy.inspect(sanitized_cookie)

    def bootstrap(self):
        response = self.request("GET", "/api/session")
        assert response.status_code == 200
        self.csrf = response.json()["csrf_token"]
        return response

    def configure(self, source_id="qq", **changes):
        body = {
            "source_id": source_id, "account_id": "fixture-account",
            "base_url": "http://127.0.0.1:" + ("40653" if source_id == "qq" else "6131"),
            **self.factory.fingerprint.model_dump(), **changes,
        }
        response = self.request("POST", "/api/sources", json=body)
        assert response.status_code in (200, 201)
        self.accounts[source_id] = body["account_id"]
        return response

    def select(self, source_id="qq", index=0, account_id=None):
        account_id = account_id or self.accounts[source_id]
        response = self.request("GET", f"/api/sources/{source_id}/conversations", params={"account_id": account_id})
        assert response.status_code == 200
        return {
            "source_id": source_id, "account_id": account_id,
            "conversation_selection_id": response.json()["conversations"][index]["conversation_selection_id"],
            "range_start": START, "range_end": END, "output_root_id": "fixture-root",
        }

    def challenge(self, selection, timezone_name="Asia/Shanghai"):
        response = self.request("POST", f"/api/sources/{selection['source_id']}/verification-consents", json={
            "selection": selection, "timezone": timezone_name,
        })
        assert response.status_code == 200
        data = response.json()
        assert set(data) <= {"consent_challenge", "disclosure", "expires_at", "scope_fingerprint"}
        assert "configuration_nonce" not in response.text
        assert "secret_reference" not in response.text
        assert str(self.output_root) not in response.text
        assert not any(identifier in response.text for identifier in self.factory.conversation_ids)
        challenge = data["consent_challenge"]
        self.disclosure_confirmations[challenge] = {
            "disclosure_revision": data["disclosure"]["revision"],
            "disclosure_hash": data["disclosure"]["hash"],
        }
        return challenge

    def confirmation(self, challenge):
        return dict(self.disclosure_confirmations.get(challenge, {}))

    def submit(self, selection, challenge=None, **changes):
        challenge = challenge or self.challenge(selection)
        body = {
            "selection": selection, "timezone": "Asia/Shanghai",
            "consent_challenge": challenge, "confirmed": True,
            **self.confirmation(challenge),
            **changes,
        }
        return self.request("POST", f"/api/sources/{selection['source_id']}/verification-jobs", json=body)

    def secret_challenge(self, operation, source_id="qq"):
        response = self.request("POST", f"/api/sources/{source_id}/secret-consents", json={
            "operation": operation, "account_id": self.accounts[source_id],
        })
        assert response.status_code == 200
        data = response.json()
        assert set(data) <= {"consent_challenge", "disclosure", "expires_at"}
        assert "secret_reference" not in response.text
        challenge = data["consent_challenge"]
        self.disclosure_confirmations[challenge] = {
            "disclosure_revision": data["disclosure"]["revision"],
            "disclosure_hash": data["disclosure"]["hash"],
        }
        return challenge

    def secret_operation(self, operation, *, challenge=None, confirmed=True, source_id="qq", value=None, **changes):
        challenge = challenge if challenge is not None else self.secret_challenge(operation, source_id)
        body = {
            "operation": operation, "account_id": self.accounts[source_id],
            "consent_challenge": challenge,
            "confirmed": confirmed,
            **self.confirmation(challenge),
            **changes,
        }
        if value is not None:
            body["value"] = value
        response = self.request("POST", f"/api/sources/{source_id}/secret-operations", json=body)
        assert "secret_reference" not in response.text
        return response

    def new_job(self):
        self.bootstrap()
        self.configure()
        selection = self.select()
        response = self.submit(selection)
        assert response.status_code == 202
        return response.json()["job_id"]

    def run(self, job_id):
        self.app.state.verifier.run(job_id)
        response = self.request("GET", f"/api/sources/qq/verification-jobs/{job_id}")
        assert response.status_code == 200
        report = response.json()
        assert PUBLIC_JOB_REQUIRED_FIELDS <= set(report)
        assert set(report) <= PUBLIC_JOB_FIELDS
        assert report["source_id"] == "qq"
        assert report["client_version"] == "fixture-client-1"
        assert report["tool_version"] == "fixture-tool-1"
        assert report["account_version"] == "fixture-account-1"
        assert report["range_start"] == START and report["range_end"] == END
        assert report["timezone"] == "Asia/Shanghai"
        assert report["evidence_class"] in {"unknown", "synthetic", "real_device"}
        if "system_version" in report:
            assert set(report["system_version"]) <= {"system", "release", "version", "architecture"}
        return report

    def allow_successful_artifacts(self, artifact=None):
        if artifact is None:
            files = list(self.output_root.rglob("*.jsonl"))
            if len(files) != 1:
                raise AssertionError("expected_one_private_artifact")
            artifact = files[0]
        self.privacy.allow_private_message_artifact(artifact)

    def allow_single_private_artifact_if_present(self):
        """Register a produced fixture artifact before later verdict assertions."""
        files = [
            path
            for root in (self.output_root, self.second_output_root)
            for path in root.rglob("*.jsonl")
        ]
        if len(files) == 1:
            self.privacy.allow_private_message_artifact(files[0])


def pytest_configure(config):
    config.addinivalue_line("markers", "p0_privacy: synthetic P0 test with sentinel-safe pytest reports")


def register_p0_privacy_scan(node, scanner):
    """Keep dynamic sentinels local to this test; never use a global registry."""
    node.add_marker("p0_privacy")
    node._p0_privacy_scan = scanner


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_makereport(item, call):
    """Redact only registered P0 sentinels, preserving failures and diagnostics.

    The outer hookwrapper runs after pytest has formed all captured sections and
    rich exception output, but before the terminal reporter receives the report.
    Original LogRecords remain intact for the privacy gate to detect the leak.
    """
    outcome = yield
    report = outcome.get_result()
    scanner = getattr(item, "_p0_privacy_scan", None)
    if scanner is None or item.get_closest_marker("p0_privacy") is None:
        return
    sentinels = scanner.secret_sentinels | scanner.message_sentinels | scanner.public_only_sentinels

    def redact(value):
        for sentinel in sorted(sentinels, key=len, reverse=True):
            if sentinel:
                value = value.replace(sentinel, "<redacted-p0-sentinel>")
        return value

    report.sections = [(name, redact(content)) for name, content in report.sections]
    if report.longrepr is not None:
        rendered = str(report.longrepr)
        sanitized = redact(rendered)
        if sanitized != rendered:
            report.longrepr = sanitized
    if report.when == "teardown":
        del item._p0_privacy_scan


@pytest.fixture
def p0(tmp_path, caplog, capsys, monkeypatch, request):
    harness = Harness(tmp_path=tmp_path, caplog=caplog, capsys=capsys, monkeypatch=monkeypatch)
    # The same mutable set receives references immediately when the real store
    # records them, so even a call-phase failure can redact those references.
    harness.privacy.public_only_sentinels = harness.store.references
    register_p0_privacy_scan(request.node, harness.privacy)
    yield harness
    harness.stack.close()
    harness.privacy.public_only_sentinels.update(harness.store.references)
    harness.privacy.verify(database_path=harness.database_path, runtime_root=harness.runtime_root, tracked_text_paths=harness.tracked_text_paths)
