"""The public mixed preview uses owned units, existing one-use consent and FakeProvider."""

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from chat_to_howto.config import Settings
from chat_to_howto.db import Database
from chat_to_howto.main import create_app
from chat_to_howto.providers.fake import FakeProvider
from chat_to_howto.security.secrets import InMemorySecretStore


ORIGIN = "http://127.0.0.1:8765"


def test_mixed_note_generation_from_public_import_endpoint(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    output = tmp_path / "output"
    output.mkdir()
    provider = FakeProvider()
    app = create_app(
        settings=Settings(host="127.0.0.1", timezone="Asia/Shanghai", data_dir=tmp_path,
            export_root=output, qce_base_url="http://127.0.0.1:40653",
            tracememo_base_url="http://127.0.0.1:6131", llm_provider="fake", llm_model="deterministic"),
        database=Database(tmp_path / "p0.sqlite3"),
        adapter_factory=lambda *_args, **_kwargs: None,
        secret_store=InMemorySecretStore(),
        clock=lambda: datetime(2026, 10, 12, tzinfo=timezone.utc),
        output_roots={"approved-root": output}, source_root=workspace, origin=ORIGIN,
        generation_provider_factory=lambda _: provider,
    )
    with TestClient(app, base_url=ORIGIN) as client:
        headers = {"Origin": ORIGIN, "X-CSRF-Token": client.get("/api/session").json()["csrf_token"]}
        preview = client.post("/api/content-imports/previews", headers=headers, json={"pasted_note": "合成主题的笔记正文"}).json()
        batch = client.post(f"/api/content-imports/previews/{preview['preview_id']}/commit", headers=headers,
            json={"preview_fingerprint": preview["preview_fingerprint"], "confirmed": True}).json()
        search = client.post("/api/mixed-search", headers=headers, json={
            "source_refs": [{"kind": "content_batch", "id": batch["batch_id"]}],
            "include_undated": True, "terms": ["主题"],
        })
        assert search.status_code == 200, search.text
        unit_id = search.json()["hits"][0]["unit_id"]
        request = {"source_refs": [{"kind": "content_batch", "id": batch["batch_id"]}],
            "included_unit_ids": [unit_id], "excluded_unit_ids": [], "topic": "合成主题",
            "mode": "complete", "summary_type": "ideas", "include_undated": True}
        denied = client.post("/api/generation/mixed/previews", json=request)
        assert denied.status_code in {401, 403}
        generation = client.post("/api/generation/mixed/previews", headers=headers, json=request)
        assert generation.status_code == 201, generation.text
        outbound = generation.json()
        assert outbound["provider_calls"] == 1 and outbound["message_count"] == 1
        assert unit_id not in outbound["outbound_payloads"][0]
        grant = client.post("/api/generation/consents", headers=headers, json={"preview_id": outbound["preview_id"], "confirmed": True})
        assert grant.status_code == 201, grant.text
        assert client.post("/api/generation/consents", headers=headers, json={"preview_id": outbound["preview_id"], "confirmed": True}).status_code == 403
        started = client.post("/api/generation/jobs", headers=headers, json={"consent_id": grant.json()["consent_id"], "idempotency_key": "synthetic-mixed"})
        assert started.status_code == 202, started.text
        final = client.get(f"/api/generation/jobs/{started.json()['job_id']}", headers=headers)
        assert final.status_code == 200 and final.json()["state"] == "complete", final.text
        assert final.json()["result"]["schema_version"] == 2
        assert final.json()["result"]["evidence"][0]["quote"] == "合成主题的笔记正文"
        assert provider.call_count == 1
