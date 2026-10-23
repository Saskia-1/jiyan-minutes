"""The pair endpoint uses the existing session-owned retained document service."""

import pytest
from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient

from chat_to_howto.api.documents import create_documents_router
from chat_to_howto.services.artifacts import ApprovedOutputRoot
from chat_to_howto.services.deletion import DeletionService
from chat_to_howto.services.documents import DocumentService
from services.test_documents import NOW, _generated


@pytest.fixture
def pair_api(tmp_path):
    repository, _, job_id, _ = _generated(tmp_path)
    documents = DocumentService(repository=repository, clock=lambda: NOW)
    document = documents.create_from_generation(job_id, session_id="session-a")

    def require_session(x_session: str | None = Header(default=None)):
        if x_session is None:
            raise HTTPException(status_code=401)
        return x_session

    def require_csrf(x_csrf_token: str | None = Header(default=None)):
        if x_csrf_token != "synthetic-csrf":
            raise HTTPException(status_code=403)

    app = FastAPI()
    app.include_router(create_documents_router(get_service=lambda: documents,
        require_session=require_session, require_csrf=require_csrf))
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client, documents, document, repository
    finally:
        repository.close()


HEADERS = {"X-Session": "session-a", "X-CSRF-Token": "synthetic-csrf"}


def test_pair_endpoint_returns_one_immutable_version_without_managed_files(pair_api, tmp_path):
    client, documents, document, _ = pair_api
    original = document.current_version
    documents.create_version(document.document_id, parent_version_id=original.version_id,
        content=original.content.model_copy(update={"title": "新版本标题"}),
        change_summary="标题调整", session_id="session-a")
    before_files = set(tmp_path.rglob("*"))
    response = client.post(f"/api/documents/{document.document_id}/text-pair", headers=HEADERS,
        json={"version_id": original.version_id})
    assert response.status_code == 200
    pair = response.json()
    assert set(pair) == {"document_id", "version_id", "body", "reference"}
    assert pair["document_id"] == document.document_id and pair["version_id"] == original.version_id
    assert set(pair["body"]) == {"filename", "text"}
    assert set(pair["reference"]) == {"filename", "text"}
    assert pair["body"]["filename"].endswith("整理内容.txt")
    assert pair["reference"]["filename"].endswith("核查参考.txt")
    assert "新版本标题" not in pair["body"]["text"]
    assert original.version_id in pair["body"]["text"] and original.version_id in pair["reference"]["text"]
    assert "[证据1]" in pair["body"]["text"] and "[证据1]" in pair["reference"]["text"]
    assert set(tmp_path.rglob("*")) == before_files


@pytest.mark.parametrize("headers,status", [({}, 401), ({"X-Session": "session-a"}, 403),
    ({"X-Session": "session-b", "X-CSRF-Token": "synthetic-csrf"}, 403)])
def test_pair_rejects_missing_session_csrf_and_another_session(pair_api, headers, status):
    client, _, document, _ = pair_api
    response = client.post(f"/api/documents/{document.document_id}/text-pair", headers=headers,
        json={"version_id": document.current_version.version_id})
    assert response.status_code == status
    assert "synthetic instruction" not in response.text


@pytest.mark.parametrize("body", [{}, {"version_id": "../private"},
    {"version_id": "version:missing"}, {"version_id": 1}, {"version_id": "v", "extra": "private"}])
def test_pair_rejects_missing_invalid_or_unknown_version_with_safe_errors(pair_api, body):
    client, _, document, _ = pair_api
    response = client.post(f"/api/documents/{document.document_id}/text-pair", headers=HEADERS, json=body)
    assert response.status_code in {409, 422}
    assert "private" not in response.text and "synthetic instruction" not in response.text


def test_pair_endpoint_after_real_raw_cleanup_cannot_restore_evidence(pair_api, tmp_path):
    client, _, document, repository = pair_api
    output = tmp_path / "output"
    output.mkdir(mode=0o700)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    root = ApprovedOutputRoot.approve(output, root_id="approved-root", repository_root=workspace)
    deletion = DeletionService(repository=repository, approved_roots={"approved-root": root}, clock=lambda: NOW)
    plan = deletion.create_plan(target_kind="batch", target_id="batch-a", include_managed_files=True,
        preserve_documents=True, session_id="session-a")
    deletion.confirm(plan.plan_id, fingerprint=plan.fingerprint, confirmed=True, session_id="session-a")
    response = client.post(f"/api/documents/{document.document_id}/text-pair", headers=HEADERS,
        json={"version_id": document.current_version.version_id})
    assert response.status_code == 200
    assert "原始依据已清理，不可查看" in response.json()["reference"]["text"]
    assert "synthetic instruction" not in response.json()["reference"]["text"]
    assert document.current_version.content.title in response.json()["body"]["text"]


def test_real_app_pair_route_keeps_session_and_csrf_gate(p0):
    p0.start()
    path = "/api/documents/document:missing/text-pair"
    assert p0.request("POST", path, json={"version_id": "v"}).status_code == 401
    p0.bootstrap()
    assert p0.request("POST", path, csrf=False, json={"version_id": "v"}).status_code == 401
    assert p0.request("POST", path, json={"version_id": "v"}).status_code == 404
