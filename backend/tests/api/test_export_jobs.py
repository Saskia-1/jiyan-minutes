"""P1 router factories remain independent from P0 main application wiring."""

from __future__ import annotations

from datetime import datetime, timezone
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from chat_to_howto.api.export_batches import create_export_batches_router
from chat_to_howto.api.export_jobs import create_export_jobs_router
from chat_to_howto.domain.jobs import ExportJobState
from chat_to_howto.domain.jobs import ExportScope
from chat_to_howto.services.export_jobs import ConsentGrant, ExportJobView


SCOPE = {
    "schema_version": 1,
    "source_id": "synthetic-source",
    "account_id": "account-a",
    "source_account_namespace": None,
    "conversations": [
        {
            "conversation_id": "conversation-a",
            "display_name": "合成会话",
            "conversation_type": "group",
        }
    ],
    "range_start": "2026-09-01T00:00:00Z",
    "range_end": "2026-09-02T00:00:00Z",
    "output_root_id": "approved-root",
    "configuration_nonce": "configuration-a",
    "operation": "export",
    "display_timezone": "Asia/Shanghai",
    "client_version": "client-1",
    "tool_version": "tool-1",
    "account_version": "account-1",
}


class StubOrchestrator:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []
        self.job = ExportJobView(
            job_id="job-a",
            state=ExportJobState.PENDING_AUTHORIZATION,
            batch_id=None,
            snapshot_id=None,
            scope_fingerprint="a" * 64,
            known_total=None,
            completed_count=0,
            fetched_count=0,
            saved_count=0,
            duplicate_count=0,
            unknown_time_count=0,
            parse_error_count=0,
            manifest_sha256=None,
            completeness=None,
            error_code=None,
            searchable=False,
            progress_percent=None,
            revision=0,
        )

    def issue_consent(self, *, session_id, scope, confirmed):
        self.calls.append(("consent", session_id, scope, confirmed))
        return ConsentGrant(
            consent_id="consent-a",
            scope_fingerprint=scope.fingerprint(),
            expires_at_utc=datetime(2026, 9, 1, 12, 5, tzinfo=timezone.utc),
        )

    def start_export(self, *, session_id, scope, consent_id, idempotency_key):
        self.calls.append(
            ("start", session_id, scope, consent_id, idempotency_key)
        )
        return self.job

    def start_export_from_consent(
        self, *, session_id, consent_id, idempotency_key
    ):
        self.calls.append(
            ("start-from-consent", session_id, consent_id, idempotency_key)
        )
        return self.job

    def run_job(self, job_id):
        self.calls.append(("run", job_id))
        return self.job

    def get_job(self, job_id, *, session_id):
        self.calls.append(("get", session_id, job_id))
        return self.job

    def cancel_job(self, job_id, *, session_id):
        self.calls.append(("cancel", session_id, job_id))
        return self.job

    def delete_job(self, job_id, *, session_id):
        self.calls.append(("delete", session_id, job_id))

    def accept_limitations(self, job_id, *, session_id, manifest_sha256):
        self.calls.append(("accept", session_id, job_id, manifest_sha256))
        return self.job

    def retry_index(self, job_id, *, session_id):
        self.calls.append(("retry", session_id, job_id))
        return self.job

    def retry_import(self, job_id, *, session_id):
        self.calls.append(("retry-import", session_id, job_id))
        return self.job

    def get_batch(self, batch_id, *, session_id):
        self.calls.append(("batch", session_id, batch_id))
        return {
            "batch_id": batch_id,
            "manifest_sha256": "b" * 64,
            "completeness": "partial",
            "limitations_accepted_manifest_sha256": None,
            "searchable": False,
            "generation_eligible": True,
        }


def _client(*, resolve_scope=None):
    service = StubOrchestrator()
    guards: list[str] = []

    def require_session() -> str:
        guards.append("session")
        return "session-a"

    def require_csrf() -> None:
        guards.append("csrf")

    app = FastAPI()
    app.include_router(
        create_export_jobs_router(
            get_orchestrator=lambda: service,
            require_session=require_session,
            require_csrf=require_csrf,
            resolve_scope=resolve_scope,
        )
    )
    app.include_router(
        create_export_batches_router(
            get_orchestrator=lambda: service,
            require_session=require_session,
        )
    )
    return TestClient(app), service, guards


def test_public_selection_consent_resolves_opaque_handles_server_side() -> None:
    resolutions = []

    def resolve_scope(selection, session_id):
        resolutions.append((selection, session_id))
        return ExportScope.model_validate_json(json.dumps(SCOPE))

    client, service, guards = _client(resolve_scope=resolve_scope)
    response = client.post(
        "/api/export-jobs/consents/from-selections",
        json={
            "selection": {
                "source_id": "synthetic-source",
                "account_id": "account-a",
                "conversation_selection_ids": ["opaque-a", "opaque-b"],
                "range_start": "2026-09-01T00:00:00Z",
                "range_end": "2026-09-02T00:00:00Z",
                "output_root_id": "approved-root",
                "display_timezone": "Asia/Shanghai",
            },
            "confirmed": True,
        },
    )

    assert response.status_code == 201, response.json()
    assert resolutions[0][1] == "session-a"
    assert resolutions[0][0].conversation_selection_ids == (
        "opaque-a",
        "opaque-b",
    )
    assert service.calls[0][0] == "consent"
    assert guards == ["session", "csrf"]


def test_public_start_can_use_exact_persisted_consent_scope() -> None:
    client, service, guards = _client()

    response = client.post(
        "/api/export-jobs",
        json={
            "consent_id": "consent-a",
            "idempotency_key": "idempotency-a",
        },
    )

    assert response.status_code == 202, response.json()
    assert (
        "start-from-consent",
        "session-a",
        "consent-a",
        "idempotency-a",
    ) in service.calls
    assert guards == ["session", "csrf"]


def test_router_uses_injected_session_and_csrf_and_schedules_one_click_run() -> None:
    client, service, guards = _client()

    consent = client.post(
        "/api/export-jobs/consents",
        json={"scope": SCOPE, "confirmed": True},
        headers={"X-CSRF-Token": "synthetic"},
    )
    started = client.post(
        "/api/export-jobs",
        json={
            "scope": SCOPE,
            "consent_id": "consent-a",
            "idempotency_key": "idempotency-a",
        },
        headers={"X-CSRF-Token": "synthetic"},
    )

    assert consent.status_code == 201, consent.json()
    assert consent.json()["consent_id"] == "consent-a"
    assert started.status_code == 202
    assert started.json()["job_id"] == "job-a"
    assert ("run", "job-a") in service.calls
    assert guards.count("session") == 2
    assert guards.count("csrf") == 2


def test_router_validates_strict_bounded_requests_and_fixed_operations() -> None:
    client, service, _ = _client()
    oversized_scope = {**SCOPE, "source_id": "x" * 513}
    sentinel = "SECRET-VALIDATION-INPUT-MUST-NOT-ECHO"

    assert client.post(
        "/api/export-jobs/consents",
        json={"scope": SCOPE, "confirmed": False},
    ).status_code == 422
    assert client.post(
        "/api/export-jobs",
        json={
            "scope": SCOPE,
            "consent_id": "consent-a",
            "idempotency_key": "x" * 129,
        },
    ).status_code == 422
    unsafe = client.post(
        "/api/export-jobs",
        json={
            "scope": SCOPE,
            "consent_id": "consent-a",
            "idempotency_key": "idempotency-a",
            "unexpected": sentinel,
        },
    )
    assert unsafe.status_code == 422
    assert sentinel not in unsafe.text
    assert all("input" not in item for item in unsafe.json()["detail"])
    unsafe_key = client.post(
        "/api/export-jobs",
        json={
            "scope": SCOPE,
            "consent_id": "consent-a",
            "idempotency_key": "idempotency-a",
            sentinel: "untrusted",
        },
    )
    assert unsafe_key.status_code == 422
    assert sentinel not in unsafe_key.text
    assert client.post(
        "/api/export-jobs/job-a/accept-limitations",
        json={"manifest_sha256": "not-a-hash"},
    ).status_code == 422
    assert client.post(
        "/api/export-jobs/job-a/cancel",
        json={"unexpected": "rejected"},
    ).status_code == 422
    unsafe_delete = client.request(
        "DELETE",
        "/api/export-jobs/job-a",
        json={"unexpected": sentinel},
    )
    assert unsafe_delete.status_code == 422
    assert sentinel not in unsafe_delete.text
    assert client.post(
        "/api/export-jobs/consents",
        json={"scope": oversized_scope, "confirmed": True},
    ).status_code == 422
    assert not service.calls


def test_job_and_batch_routes_delegate_only_after_injected_authority() -> None:
    client, service, guards = _client()

    first = client.get("/api/export-jobs/job-a")
    assert first.status_code == 200, first.json()
    assert client.post("/api/export-jobs/job-a/cancel", json={}).status_code == 200
    assert client.post(
        "/api/export-jobs/job-a/accept-limitations",
        json={"manifest_sha256": "a" * 64},
    ).status_code == 200
    assert client.post("/api/export-jobs/job-a/retry-index", json={}).status_code == 200
    assert client.post("/api/export-jobs/job-a/retry-import", json={}).status_code == 200
    assert client.delete("/api/export-jobs/job-a").status_code == 204
    batch = client.get("/api/export-batches/batch-a")

    assert batch.status_code == 200
    assert batch.json()["batch_id"] == "batch-a"
    assert {call[0] for call in service.calls} == {
        "get", "cancel", "accept", "retry", "retry-import", "delete", "batch"
    }
    assert guards.count("csrf") == 5
    assert guards.count("session") == 7


def test_main_application_mounts_p1_routes_with_its_real_session_dependencies(p0) -> None:
    """Removing the production include_router wiring must break this public contract."""
    p0.start()
    assert p0.app.state.export_orchestrator is not None
    assert p0.app.state.chat_repository is not None

    p0.bootstrap()
    p0.configure()
    source = p0.database.one("SELECT * FROM sources WHERE source_id='qq'")
    scope = {
        **SCOPE,
        "source_id": "qq",
        "account_id": "fixture-account",
        "conversations": [
            {
                "conversation_id": "group:fixture-A",
                "display_name": "合成会话",
                "conversation_type": "group",
            }
        ],
        "range_start": "2026-09-01T00:00:00Z",
        "range_end": "2026-09-01T00:00:02Z",
        "output_root_id": "fixture-root",
        "configuration_nonce": source["nonce"],
        "client_version": source["client_version"],
        "tool_version": source["tool_version"],
        "account_version": source["account_version"],
    }
    request_body = {"scope": scope, "confirmed": True}
    assert p0.request(
        "POST",
        "/api/export-jobs/consents",
        csrf=False,
        json=request_body,
    ).status_code == 401
    assert p0.request(
        "POST",
        "/api/export-jobs/consents",
        headers={"Origin": "https://evil.example"},
        json=request_body,
    ).status_code == 403
    response = p0.request(
        "POST",
        "/api/export-jobs/consents",
        json=request_body,
    )
    assert response.status_code == 201
    assert set(response.json()) == {
        "consent_id", "scope_fingerprint", "expires_at_utc"
    }
    missing_batch = p0.request("GET", "/api/export-batches/missing-batch")
    assert missing_batch.status_code == 404
    assert missing_batch.json()["detail"] == "export batch was not found"
