"""Synthetic topic summary through real import, generation, evidence and document APIs."""

from datetime import datetime, timezone
import pytest

from docx import Document
from fastapi.testclient import TestClient

from chat_to_howto.config import Settings
from chat_to_howto.db import Database
from chat_to_howto.main import create_app
from chat_to_howto.providers.fake import FakeProvider
from chat_to_howto.security.secrets import InMemorySecretStore


ORIGIN = "http://127.0.0.1:8765"


class RecordingFakeProvider(FakeProvider):
    def __init__(self) -> None:
        super().__init__()
        self.prompts: list[str] = []

    async def generate(self, request, cancellation):
        self.prompts.append(request.system_prompt)
        return await super().generate(request, cancellation)


@pytest.mark.parametrize("direction", [None, "viewpoints", "ideas", "how_to", "mixed"])
def test_summary_uses_recalled_nonexcluded_messages_and_exports_original_evidence(tmp_path, direction) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    output = tmp_path / "output"
    output.mkdir(mode=0o700)
    provider = RecordingFakeProvider()
    app = create_app(
        settings=Settings(
            host="127.0.0.1", timezone="Asia/Shanghai", data_dir=tmp_path,
            export_root=output, qce_base_url="http://127.0.0.1:40653",
            tracememo_base_url="http://127.0.0.1:6131",
            llm_provider="fake", llm_model="deterministic",
        ),
        database=Database(tmp_path / "p0.sqlite3"),
        adapter_factory=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("summary must not access a chat adapter")
        ),
        secret_store=InMemorySecretStore(),
        clock=lambda: datetime(2026, 9, 13, 12, tzinfo=timezone.utc),
        output_roots={"approved-root": output}, source_root=workspace,
        origin=ORIGIN,
        generation_provider_factory=lambda name: provider if name == "fake" else None,
    )

    with TestClient(app, base_url=ORIGIN, raise_server_exceptions=False) as client:
        csrf = client.get("/api/session").json()["csrf_token"]
        headers = {"Origin": ORIGIN, "X-CSRF-Token": csrf}
        topic_preview = client.post("/api/generation/topic-expansion/previews",
            headers=headers, json={"topic": "就业"})
        assert topic_preview.status_code == 200, topic_preview.json()
        topic_authorization = topic_preview.json()
        expansion = client.post("/api/generation/topic-expansion", headers=headers, json={
            "preview_id": topic_authorization["preview_id"],
            "preview_fingerprint": topic_authorization["preview_fingerprint"], "confirmed": True,
        })
        assert expansion.status_code == 200, expansion.json()
        assert "求职" in expansion.json()["terms"]

        preview = client.post("/api/imports/previews", headers=headers, json={
            "origin": "paste", "platform_hint": "generic",
            "pasted_text": "[10:20] 甲：秋招求职安排\n[10:21] 乙：午饭地点\n[10:22] 丙：面试简历闲聊\n",
            "options": {"timezone": "Asia/Shanghai", "default_date": "2026-09-13",
                        "default_conversation_name": "合成主题群"},
        })
        assert preview.status_code == 201, preview.json()
        imported = client.post(
            f"/api/imports/previews/{preview.json()['preview_id']}/commit", headers=headers,
            json={"preview_fingerprint": preview.json()["preview_fingerprint"], "confirmed": True},
        )
        assert imported.status_code == 201, imported.json()
        scope = imported.json()
        handle = scope["conversations"][0]["conversation_selection_id"]
        search_base = {
            "source_id": "manual", "account_id": scope["account_id"],
            "conversation_selection_ids": [handle],
            "start_time": scope["range_start"], "end_time": scope["range_end"],
            "limit": 25,
        }
        search = lambda term: client.post(
            "/api/search/from-selections", headers=headers,
            json={**search_base, "query": term},
        ).json()["results"]
        first = search("秋招")[0]
        assert search("求职")[0]["message_id"] == first["message_id"]
        excluded = search("面试")[0]
        direction_fields = {"summary_type": direction} if direction else {}
        if direction:
            context = client.post("/api/generation/contexts/from-selections", headers=headers, json={
                **{key: search_base[key] for key in (
                    "source_id", "account_id", "conversation_selection_ids", "start_time", "end_time")},
                "snapshot_id": scope["snapshot_id"], "topic": "就业", "mode": "complete",
                "summary_type": direction, "included_message_ids": [first["message_id"]],
            })
            assert context.status_code == 200, context.json()
            assert context.json()["added_count"] == 2
            assert [item["text"] for item in context.json()["messages"]] == ["秋招求职安排", "午饭地点", "面试简历闲聊"]

        generation = client.post(
            "/api/generation/previews/from-selections", headers=headers,
            json={
                **{key: search_base[key] for key in (
                    "source_id", "account_id", "conversation_selection_ids", "start_time", "end_time")},
                "snapshot_id": scope["snapshot_id"], "topic": "就业", "mode": "complete",
                "generation_kind": "summary",
                **direction_fields,
                "included_message_ids": [first["message_id"], excluded["message_id"]],
                "excluded_message_ids": [excluded["message_id"]],
            },
        )
        assert generation.status_code == 201, generation.json()
        assert generation.json()["message_count"] == 1
        outbound = "".join(generation.json()["outbound_payloads"])
        if direction:
            assert first["message_id"] not in outbound
            assert '"message_id":"E001"' in outbound
        else:
            assert first["message_id"] in outbound
        assert excluded["message_id"] not in outbound
        assert "午饭地点" not in outbound
        assert "offer" not in outbound

        consent = client.post(
            "/api/generation/consents", headers=headers,
            json={"preview_id": generation.json()["preview_id"], "confirmed": True},
        )
        started = client.post(
            "/api/generation/jobs", headers=headers,
            json={"consent_id": consent.json()["consent_id"], "idempotency_key": "summary-e2e"},
        )
        assert started.status_code == 202, started.json()
        completed = client.get(
            f"/api/generation/jobs/{started.json()['job_id']}", headers=headers,
        )
        assert completed.status_code == 200, completed.json()
        assert completed.json()["state"] == "complete"
        if direction:
            mode_label = {
                "viewpoints": "【当前模式：观点整理】",
                "ideas": "【当前模式：Idea / 想法整理】",
                "how_to": "【当前模式：方法论 / 怎么做】",
                "mixed": "【当前模式：综合整理】",
            }[direction]
            assert any(mode_label in prompt for prompt in provider.prompts)
        else:
            assert any("Summarize the supplied messages" in prompt for prompt in provider.prompts)
        result = completed.json()["result"]
        if direction == "how_to":
            assert result["result"]["steps"][0]["source_message_ids"] == [first["message_id"]]
        else:
            key = "sections" if direction else "core_summary"
            assert result["result"]["summary"][key][0]["source_message_ids"] == [first["message_id"]]
        assert result["evidence"][0]["sender_name"] == "甲"
        assert result["evidence"][0]["quote"] == "秋招求职安排"
        assert result["evidence"][0]["timestamp"] == first["timestamp"]

        document = client.post(
            "/api/documents/from-generation", headers=headers,
            json={"generation_job_id": started.json()["job_id"]},
        )
        assert document.status_code == 201, document.json()
        assert bool(document.json()["current_version"]["content"].get("summary")) == (direction != "how_to")
        original = document.json()
        edited_content = original["current_version"]["content"]
        edited_content["title"] = "就业主题总结（人工修订）"
        edited = client.post(
            f"/api/documents/{original['document_id']}/versions", headers=headers,
            json={"parent_version_id": original["current_version"]["version_id"],
                  "content": edited_content, "change_summary": "synthetic edit"},
        )
        assert edited.status_code == 200, edited.json()
        assert edited.json()["current_version"]["ordinal"] == 2

        for format_name in ("markdown", "docx"):
            artifact = client.post(
                f"/api/documents/{original['document_id']}/exports", headers=headers,
                json={"version_id": edited.json()["current_version"]["version_id"],
                      "format": format_name, "source_mode": "with_sources",
                      "output_root_id": "approved-root"},
            )
            assert artifact.status_code == 201, artifact.json()
            path = output / "documents" / artifact.json()["filename"]
            assert path.is_file()
            if format_name == "markdown":
                content = path.read_text()
                assert "来源证据" in content
                if direction is None:
                    assert "核心摘要" in content
                assert "秋招求职安排" in content and "发送者：甲" in content
            else:
                text = "\n".join(paragraph.text for paragraph in Document(path).paragraphs)
                assert "秋招求职安排" in text
                if direction is None:
                    assert "核心摘要" in text
        restored = client.get(f"/api/documents/{original['document_id']}", headers=headers)
        assert restored.status_code == 200
        assert restored.json()["current_version"]["ordinal"] == 2
        assert restored.json()["source_evidence"][0]["quote"] == "秋招求职安排"
