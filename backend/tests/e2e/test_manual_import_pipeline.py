"""Synthetic manual import through the real preview/search/generation/document API flow."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from chat_to_howto.config import Settings
from chat_to_howto.db import Database
from chat_to_howto.main import create_app
from chat_to_howto.providers.fake import FakeProvider
from chat_to_howto.security.secrets import InMemorySecretStore


ORIGIN = "http://127.0.0.1:8765"


def test_preview_commit_search_exclude_generate_document_and_delete(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    output = tmp_path / "output"
    output.mkdir(mode=0o700)
    provider = FakeProvider()
    app = create_app(
        settings=Settings(
            host="127.0.0.1", timezone="Asia/Shanghai", data_dir=tmp_path,
            export_root=output, qce_base_url="http://127.0.0.1:40653",
            tracememo_base_url="http://127.0.0.1:6131",
            llm_provider="fake", llm_model="deterministic",
        ),
        database=Database(tmp_path / "p0.sqlite3"),
        adapter_factory=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("manual import must not access a chat adapter")
        ),
        secret_store=InMemorySecretStore(),
        clock=lambda: datetime(2026, 9, 13, 12, tzinfo=timezone.utc),
        output_roots={"approved-root": output},
        source_root=workspace,
        origin=ORIGIN,
        generation_provider_factory=lambda name: provider if name == "fake" else None,
    )

    with TestClient(app, base_url=ORIGIN, raise_server_exceptions=False) as client:
        csrf = client.get("/api/session").json()["csrf_token"]
        headers = {"Origin": ORIGIN, "X-CSRF-Token": csrf}
        preview = client.post(
            "/api/imports/previews", headers=headers,
            json={
                "origin": "paste", "platform_hint": "generic",
                "pasted_text": "[10:20] A：保留的操作步骤\n[10:21] B：应被排除的步骤\n",
                "options": {
                    "timezone": "Asia/Shanghai", "default_date": "2026-09-13",
                    "default_conversation_name": "手动导入项目群",
                },
            },
        )
        assert preview.status_code == 201, preview.json()
        assert preview.json()["analysis"]["parsed_count"] == 2
        assert app.state.chat_repository.message_count() == 0

        imported = client.post(
            f"/api/imports/previews/{preview.json()['preview_id']}/commit",
            headers=headers,
            json={"preview_fingerprint": preview.json()["preview_fingerprint"], "confirmed": True},
        )
        assert imported.status_code == 201, imported.json()
        scope = imported.json()
        handle = scope["conversations"][0]["conversation_selection_id"]

        searched = client.post(
            "/api/search/from-selections", headers=headers,
            json={
                "source_id": "manual", "account_id": scope["account_id"],
                "conversation_selection_ids": [handle], "query": "步骤",
                "start_time": scope["range_start"], "end_time": scope["range_end"],
                "limit": 25,
            },
        )
        assert searched.status_code == 200, searched.json()
        results = searched.json()["results"]
        assert len(results) == 2
        excluded_id = next(item["message_id"] for item in results if "排除" in item["text"])
        retained_id = next(item["message_id"] for item in results if "保留" in item["text"])

        generation_preview = client.post(
            "/api/generation/previews/from-selections", headers=headers,
            json={
                "source_id": "manual", "account_id": scope["account_id"],
                "conversation_selection_ids": [handle], "snapshot_id": scope["snapshot_id"],
                "start_time": scope["range_start"], "end_time": scope["range_end"],
                "topic": "整理保留步骤", "mode": "complete",
                "excluded_message_ids": [excluded_id],
            },
        )
        assert generation_preview.status_code == 201, generation_preview.json()
        assert generation_preview.json()["message_count"] == 1
        outbound = "".join(generation_preview.json()["outbound_payloads"])
        assert retained_id in outbound
        assert excluded_id not in outbound

        consent = client.post(
            "/api/generation/consents", headers=headers,
            json={"preview_id": generation_preview.json()["preview_id"], "confirmed": True},
        )
        started = client.post(
            "/api/generation/jobs", headers=headers,
            json={"consent_id": consent.json()["consent_id"], "idempotency_key": "manual-import-e2e"},
        )
        assert started.status_code == 202, started.json()
        generated = client.get(f"/api/generation/jobs/{started.json()['job_id']}", headers=headers)
        assert generated.status_code == 200, generated.json()
        assert generated.json()["state"] == "complete"
        assert [item["message_id"] for item in generated.json()["result"]["evidence"]] == [retained_id]

        document = client.post(
            "/api/documents/from-generation", headers=headers,
            json={"generation_job_id": started.json()["job_id"]},
        )
        assert document.status_code == 201, document.json()
        assert document.json()["source_snapshot_id"] == scope["snapshot_id"]

        original_document = document.json()
        edited_content = original_document["current_version"]["content"]
        edited_content["title"] = "手动导入指南 version 2"
        edited = client.post(
            f"/api/documents/{original_document['document_id']}/versions",
            headers=headers,
            json={
                "parent_version_id": original_document["current_version"]["version_id"],
                "content": edited_content,
                "change_summary": "P0 refresh recovery",
            },
        )
        assert edited.status_code == 200, edited.json()
        assert edited.json()["current_version"]["ordinal"] == 2

        recovered = client.post(
            "/api/documents/recovery", headers=headers,
            json={"cursor": None, "limit": 20},
        )
        assert recovered.status_code == 200, recovered.json()
        assert recovered.json()["documents"][0]["document_id"] == original_document["document_id"]
        assert recovered.json()["documents"][0]["current_version"]["content"]["title"] == "手动导入指南 version 2"

        for export_format in ("markdown", "docx"):
            exported = client.post(
                f"/api/documents/{original_document['document_id']}/exports",
                headers=headers,
                json={
                    "version_id": edited.json()["current_version"]["version_id"],
                    "format": export_format,
                    "source_mode": "body_only",
                    "output_root_id": "approved-root",
                },
            )
            assert exported.status_code == 201, exported.json()
            assert (output / "documents" / exported.json()["filename"]).is_file()

        plan = client.post(
            "/api/deletion-plans", headers=headers,
            json={"target_kind": "batch", "target_id": scope["batch_id"], "include_managed_files": False},
        )
        assert plan.status_code == 201, plan.json()
        assert plan.json()["counts"]["documents"] == 1
        deleted = client.post(
            f"/api/deletion-plans/{plan.json()['plan_id']}/confirm", headers=headers,
            json={"fingerprint": plan.json()["fingerprint"], "confirmed": True},
        )
        assert deleted.status_code == 200, deleted.json()
        assert deleted.json()["deleted_counts"]["messages"] == 2
