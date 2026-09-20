"""Deterministic full API E2E; no real client, socket, account, or chat is used."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from chat_to_howto.config import Settings
from chat_to_howto.db import Database
from chat_to_howto.main import create_app
from chat_to_howto.providers.fake import FakeProvider
from chat_to_howto.security.secrets import InMemorySecretStore
from chat_to_howto.sources.base import (
    CompletenessEvidence,
    ConversationRef,
    FetchPage,
    SourceHealth,
    SourceMessage,
)


ORIGIN = "http://127.0.0.1:8765"
START = datetime(2026, 9, 1, tzinfo=timezone.utc)
END = datetime(2026, 9, 2, tzinfo=timezone.utc)
START_MS = int(START.timestamp() * 1000)
END_MS = int(END.timestamp() * 1000)


class DeterministicAdapter:
    evidence_class = "synthetic"

    def __init__(self, source_id: str, account_id: str) -> None:
        self.source_id = source_id
        self.account_id = account_id
        self.cancelled = False

    def health(self):
        return SourceHealth(available=True, ready=True)

    def list_accounts(self):
        return ("fixture-account",)

    def list_conversations(self):
        return tuple(
            ConversationRef(
                self.source_id,
                self.account_id,
                conversation_id,
                display_name,
                "group",
            )
            for conversation_id, display_name in (
                ("fixture-conversation-a", "合成会话 A"),
                ("fixture-conversation-b", "合成会话 B"),
            )
        )

    def fetch_range(self, conversation_id, start_ms, end_ms):
        messages = (
            SourceMessage(
                message_id=f"{conversation_id}-message",
                conversation_id=conversation_id,
                sent_at_ms=START_MS + (1_000 if conversation_id.endswith("a") else 2_000),
                text=f"可搜索的合成步骤 {conversation_id[-1].upper()}",
                source_order=1,
            ),
        )
        return FetchPage(
            messages=messages,
            next_cursor=None,
            completeness=CompletenessEvidence(
                status="source_range_verified",
                reason="deterministic_fixture",
                requested_start_ms=start_ms,
                requested_end_ms=end_ms,
                observed_start_ms=messages[0].sent_at_ms,
                observed_end_ms=messages[0].sent_at_ms,
                fetched_count=1,
                accepted_count=1,
                duplicate_count=0,
                unknown_time_count=0,
                missing_id_count=0,
                parse_error_count=0,
                termination="fixture_exhausted",
            ),
        )

    def cancel(self):
        self.cancelled = True


class DeterministicContractProvider(FakeProvider):
    """A non-DeepSeek provider name exercising the public provider contract."""

    @property
    def name(self) -> str:
        return "deterministic-contract"


def test_configure_select_export_import_and_search_through_real_app(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    output = tmp_path / "approved-output"
    output.mkdir(mode=0o700)
    database = Database(tmp_path / "p0.sqlite3")
    settings = Settings(
        host="127.0.0.1",
        timezone="Asia/Shanghai",
        data_dir=tmp_path,
        export_root=output,
        qce_base_url="http://127.0.0.1:40653",
        tracememo_base_url="http://127.0.0.1:6131",
        llm_provider="deterministic-contract",
        llm_model="deterministic",
    )

    def adapter_factory(source_id, account_id, **_grant):
        return DeterministicAdapter(source_id, account_id)

    provider = DeterministicContractProvider()

    def generation_provider_factory(provider_name):
        if provider_name != provider.name:
            raise ValueError("generation provider is not configured")
        return provider

    app = create_app(
        settings=settings,
        database=database,
        adapter_factory=adapter_factory,
        secret_store=InMemorySecretStore(),
        clock=lambda: datetime(2026, 9, 1, 12, tzinfo=timezone.utc),
        output_roots={"approved-root": output},
        source_root=workspace,
        origin=ORIGIN,
        generation_provider_factory=generation_provider_factory,
    )
    with TestClient(app, base_url=ORIGIN, raise_server_exceptions=False) as client:
        bootstrap = client.get("/api/session")
        csrf = bootstrap.json()["csrf_token"]
        headers = {"Origin": ORIGIN, "X-CSRF-Token": csrf}
        configured = client.post(
            "/api/sources",
            headers=headers,
            json={
                "source_id": "qq",
                "account_id": "fixture-account",
                "base_url": "http://127.0.0.1:40653",
                "client_version": "fixture-client",
                "tool_version": "fixture-tool",
                "account_version": "fixture-account-version",
            },
        )
        assert configured.status_code == 201

        listed = client.get(
            "/api/sources/qq/conversations",
            headers=headers,
            params={"account_id": "fixture-account"},
        )
        assert listed.status_code == 200
        conversations = listed.json()["conversations"]
        assert len(conversations) == 2
        assert all("conversation_id" not in item for item in conversations)

        consent = client.post(
            "/api/export-jobs/consents/from-selections",
            headers=headers,
            json={
                "selection": {
                    "source_id": "qq",
                    "account_id": "fixture-account",
                    "conversation_selection_ids": [
                        item["conversation_selection_id"] for item in conversations
                    ],
                    "range_start": START.isoformat(),
                    "range_end": END.isoformat(),
                    "output_root_id": "approved-root",
                    "display_timezone": "Asia/Shanghai",
                },
                "confirmed": True,
            },
        )
        assert consent.status_code == 201, consent.json()

        started = client.post(
            "/api/export-jobs",
            headers=headers,
            json={
                "consent_id": consent.json()["consent_id"],
                "idempotency_key": "synthetic-e2e",
            },
        )
        assert started.status_code == 202, started.json()
        status = client.get(
            f"/api/export-jobs/{started.json()['job_id']}", headers=headers
        )
        assert status.status_code == 200
        assert status.json()["state"] == "complete"
        assert status.json()["searchable"] is True

        searched = client.post(
            "/api/search/from-selections",
            headers=headers,
            json={
                "source_id": "qq",
                "account_id": "fixture-account",
                "query": "可搜索的合成步骤",
                "conversation_selection_ids": [
                    item["conversation_selection_id"] for item in conversations
                ],
                "start_time": START.isoformat(),
                "end_time": END.isoformat(),
                "limit": 25,
            },
        )
        assert searched.status_code == 200, searched.json()
        assert len(searched.json()["results"]) == 2
        assert {
            item["batch_id"] for item in searched.json()["results"]
        } == {status.json()["batch_id"]}
        assert {
            item["snapshot_id"] for item in searched.json()["results"]
        } == {status.json()["snapshot_id"]}

        baseline_preview = client.post(
            "/api/generation/previews/from-selections",
            headers=headers,
            json={
                "source_id": "qq",
                "account_id": "fixture-account",
                "conversation_selection_ids": [
                    item["conversation_selection_id"] for item in conversations
                ],
                "snapshot_id": status.json()["snapshot_id"],
                "start_time": START.isoformat(),
                "end_time": END.isoformat(),
                "topic": "合成步骤",
                "mode": "complete",
            },
        )
        assert baseline_preview.status_code == 201, baseline_preview.json()
        assert baseline_preview.json()["message_count"] == 2

        search_results = searched.json()["results"]
        excluded_message_id = search_results[0]["message_id"]
        retained_message_id = search_results[1]["message_id"]
        preview = client.post(
            "/api/generation/previews/from-selections",
            headers=headers,
            json={
                "source_id": "qq",
                "account_id": "fixture-account",
                "conversation_selection_ids": [
                    item["conversation_selection_id"] for item in conversations
                ],
                "snapshot_id": status.json()["snapshot_id"],
                "start_time": START.isoformat(),
                "end_time": END.isoformat(),
                "topic": "合成步骤",
                "mode": "complete",
                "excluded_message_ids": [excluded_message_id],
            },
        )
        assert preview.status_code == 201, preview.json()
        assert preview.json()["provider"] == "deterministic-contract"
        assert preview.json()["model"] == "deterministic"
        assert preview.json()["message_count"] == 1
        assert preview.json()["scope_fingerprint"] != baseline_preview.json()[
            "scope_fingerprint"
        ]
        assert preview.json()["will_send_external"] is True
        outbound_payload = "".join(preview.json()["outbound_payloads"])
        assert excluded_message_id not in outbound_payload
        assert retained_message_id in outbound_payload

        generation_consent = client.post(
            "/api/generation/consents",
            headers=headers,
            json={"preview_id": preview.json()["preview_id"], "confirmed": True},
        )
        assert generation_consent.status_code == 201, generation_consent.json()
        assert generation_consent.json()["scope_fingerprint"] == preview.json()[
            "scope_fingerprint"
        ]

        generation_started = client.post(
            "/api/generation/jobs",
            headers=headers,
            json={
                "consent_id": generation_consent.json()["consent_id"],
                "idempotency_key": "synthetic-generation-e2e",
            },
        )
        assert generation_started.status_code == 202, generation_started.json()
        generated = client.get(
            f"/api/generation/jobs/{generation_started.json()['job_id']}",
            headers=headers,
        )
        assert generated.status_code == 200, generated.json()
        assert generated.json()["state"] == "complete"
        assert generated.json()["evidence_status"] == "verified"
        assert generated.json()["result"]["result"]["title"] == "确定性测试操作指南"
        assert len(generated.json()["result"]["evidence"]) == 1
        assert generated.json()["result"]["evidence"][0]["message_id"] == (
            retained_message_id
        )
        assert generated.json()["result"]["evidence"][0]["message_id"] != (
            excluded_message_id
        )

        created_document = client.post(
            "/api/documents/from-generation",
            headers=headers,
            json={"generation_job_id": generation_started.json()["job_id"]},
        )
        assert created_document.status_code == 201, created_document.json()
        document = created_document.json()
        content = document["current_version"]["content"]
        content["title"] = "用户编辑的合成指南"
        content["steps"][0]["instruction"] = "用户确认后的合成步骤"
        edited_document = client.post(
            f"/api/documents/{document['document_id']}/versions",
            headers=headers,
            json={
                "parent_version_id": document["current_version"]["version_id"],
                "content": content,
                "change_summary": "合成端到端编辑",
            },
        )
        assert edited_document.status_code == 200, edited_document.json()
        edited = edited_document.json()
        assert edited["current_version"]["origin"] == "user_edited"
        assert edited["current_version"]["evidence_status"] == "unverified"
        assert len(edited["versions"]) == 2

        artifact_names = []
        for export_format in ("markdown", "docx"):
            exported_document = client.post(
                f"/api/documents/{document['document_id']}/exports",
                headers=headers,
                json={
                    "version_id": edited["current_version"]["version_id"],
                    "format": export_format,
                    "source_mode": "with_sources",
                    "output_root_id": "approved-root",
                },
            )
            assert exported_document.status_code == 201, exported_document.json()
            artifact = exported_document.json()
            artifact_names.append(artifact["filename"])
            path = output / "documents" / artifact["filename"]
            assert path.is_file()
            assert path.stat().st_size == artifact["byte_size"]

        document_plan = client.post(
            "/api/deletion-plans",
            headers=headers,
            json={
                "target_kind": "document",
                "target_id": document["document_id"],
                "include_managed_files": True,
            },
        )
        assert document_plan.status_code == 201, document_plan.json()
        assert document_plan.json()["counts"]["documents"] == 1
        assert document_plan.json()["counts"]["document_artifacts"] == 2
        deleted_document = client.post(
            f"/api/deletion-plans/{document_plan.json()['plan_id']}/confirm",
            headers=headers,
            json={"fingerprint": document_plan.json()["fingerprint"], "confirmed": True},
        )
        assert deleted_document.status_code == 200, deleted_document.json()
        assert deleted_document.json()["state"] == "complete"
        assert all(not (output / "documents" / name).exists() for name in artifact_names)

        batch_plan = client.post(
            "/api/deletion-plans",
            headers=headers,
            json={
                "target_kind": "batch",
                "target_id": status.json()["batch_id"],
                "include_managed_files": True,
            },
        )
        assert batch_plan.status_code == 201, batch_plan.json()
        deleted_batch = client.post(
            f"/api/deletion-plans/{batch_plan.json()['plan_id']}/confirm",
            headers=headers,
            json={"fingerprint": batch_plan.json()["fingerprint"], "confirmed": True},
        )
        assert deleted_batch.status_code == 200, deleted_batch.json()
        assert deleted_batch.json()["state"] == "complete"
        assert deleted_batch.json()["deleted_counts"]["messages"] == 2
        assert not (output / "exports" / status.json()["batch_id"]).exists()
