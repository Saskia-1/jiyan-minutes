"""Real local APIs with synthetic undated messages; no external provider calls."""

from io import BytesIO
from datetime import datetime, timezone
import json

from docx import Document
from fastapi.testclient import TestClient
import pytest

from chat_to_howto.config import Settings
from chat_to_howto.db import Database
from chat_to_howto.main import create_app
from chat_to_howto.providers.fake import FakeProvider
from chat_to_howto.security.secrets import InMemorySecretStore

ORIGIN = "http://127.0.0.1:8765"


@pytest.mark.parametrize("direction", ["ideas", "viewpoints", "how_to", "mixed"])
def test_undated_import_search_context_exclusion_versions_and_download(tmp_path, direction):
    output = tmp_path / "output"
    output.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    provider = FakeProvider()
    app = create_app(settings=Settings(
        host="127.0.0.1", timezone="Asia/Shanghai", data_dir=tmp_path,
        export_root=output, qce_base_url="http://127.0.0.1:40653",
        tracememo_base_url="http://127.0.0.1:6131", llm_provider="fake", llm_model="deterministic",
    ), database=Database(tmp_path / "p0.sqlite3"), secret_store=InMemorySecretStore(),
        source_root=workspace, output_roots={"approved-root": output}, origin=ORIGIN,
        generation_provider_factory=lambda _: provider,
        adapter_factory=lambda *_args, **_kwargs: None,
        clock=lambda: datetime(2026, 9, 20, tzinfo=timezone.utc))
    with TestClient(app, base_url=ORIGIN) as client:
        headers = {"Origin": ORIGIN, "X-CSRF-Token": client.get("/api/session").json()["csrf_token"]}
        preview = client.post("/api/imports/previews", headers=headers, json={
            "origin": "paste", "platform_hint": "generic",
            "pasted_text": "甲：\n收藏歌单用于跑步\n\n乙：\n再推荐相似歌曲\n\n甲：\n应排除的噪声\n",
            "options": {"default_conversation_name": "合成音乐讨论"},
        }).json()
        assert preview["ready_to_commit"] is True
        assert preview["analysis"]["parsed_count"] == 3
        assert preview["analysis"]["time_start"] is None
        assert all(item["timestamp"] is None for item in preview["analysis"]["samples"])
        imported = client.post(f"/api/imports/previews/{preview['preview_id']}/commit", headers=headers,
            json={"preview_fingerprint": preview["preview_fingerprint"], "confirmed": True})
        assert imported.status_code == 201, imported.json()
        scope = imported.json()
        base = {"source_id": "manual", "account_id": scope["account_id"],
                "conversation_selection_ids": [scope["conversations"][0]["conversation_selection_id"]],
                "start_time": scope["range_start"], "end_time": scope["range_end"], "include_undated": True}
        results, cursor = [], None
        while True:
            page = client.post("/api/search/from-selections", headers=headers,
                json={**base, "query": "", "limit": 1, "cursor": cursor})
            assert page.status_code == 200, page.json()
            results.extend(page.json()["results"])
            cursor = page.json()["next_cursor"]
            if cursor is None:
                break
        assert [item["text"] for item in results] == ["收藏歌单用于跑步", "再推荐相似歌曲", "应排除的噪声"]
        assert all(item["timestamp"] is None for item in results)
        assert [item["source_order"] for item in results] == [1, 2, 3]
        dated = client.post("/api/search/from-selections", headers=headers,
            json={**base, "query": "", "include_undated": False,
                  "start_time": "2026-09-01T00:00:00Z", "end_time": "2026-10-01T00:00:00Z"})
        assert dated.json()["results"] == []
        selection = {**base, "snapshot_id": scope["snapshot_id"], "topic": "音乐",
                     "mode": "complete", "summary_type": direction,
                     "included_message_ids": [results[0]["message_id"]], "excluded_message_ids": []}
        context = client.post("/api/generation/contexts/from-selections", headers=headers, json=selection)
        assert context.status_code == 200, context.json()
        assert [item["text"] for item in context.json()["messages"]] == [item["text"] for item in results]
        selection["included_message_ids"] = [item["message_id"] for item in results]
        selection["excluded_message_ids"] = [results[2]["message_id"]]
        generated_preview = client.post("/api/generation/previews/from-selections", headers=headers, json=selection)
        assert generated_preview.status_code == 201, generated_preview.json()
        value = generated_preview.json()
        assert value["message_count"] == 2
        assert "应排除的噪声" not in "".join(value["outbound_payloads"])
        consent = client.post("/api/generation/consents", headers=headers,
            json={"preview_id": value["preview_id"], "confirmed": True}).json()
        job = client.post("/api/generation/jobs", headers=headers,
            json={"consent_id": consent["consent_id"], "idempotency_key": "undated-quality"}).json()
        completed = client.get(f"/api/generation/jobs/{job['job_id']}").json()
        assert completed["state"] == "complete", completed["error_code"]
        assert all(item["timestamp"] is None for item in completed["result"]["evidence"])
        assert {item["quote"] for item in completed["result"]["evidence"]} == {"收藏歌单用于跑步", "再推荐相似歌曲"}
        document = client.post("/api/documents/from-generation", headers=headers,
            json={"generation_job_id": job["job_id"]}).json()
        content = document["current_version"]["content"]
        content["user_text"] = "用户完整文稿\n\n我补充的想法不需要伪造聊天依据。"
        edited = client.post(f"/api/documents/{document['document_id']}/versions", headers=headers, json={
            "parent_version_id": document["current_version"]["version_id"], "content": content,
        })
        assert edited.status_code == 200, edited.json()
        assert edited.json()["current_version"]["ordinal"] == 2
        assert "user_text" not in edited.json()["versions"][0]["content"]
        for fmt in ("markdown", "docx"):
            downloaded = client.post(f"/api/documents/{document['document_id']}/download", headers=headers,
                json={"version_id": edited.json()["current_version"]["version_id"],
                      "format": fmt, "source_mode": "with_sources"})
            assert downloaded.status_code == 200
            text = (downloaded.content.decode() if fmt == "markdown" else
                    "\n".join(p.text for p in Document(BytesIO(downloaded.content)).paragraphs))
            assert "我补充的想法" in text and "时间未知" in text
            assert "应排除的噪声" not in text
        recovery = client.post("/api/imports/recovery", headers=headers, json={}).json()
        assert recovery["imports"][0]["analysis"]["time_start"] is None
        download_url = f"/api/documents/{document['document_id']}/download"
        download_body = {"version_id": edited.json()["current_version"]["version_id"], "format": "docx"}
        assert client.post(download_url, headers={"Origin": ORIGIN}, json=download_body).status_code == 401
        client.cookies.clear()
        other_headers = {"Origin": ORIGIN, "X-CSRF-Token": client.get("/api/session").json()["csrf_token"]}
        assert client.post(download_url, headers=other_headers, json=download_body).status_code == 403
