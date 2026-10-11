"""Explicit, private real-data evaluation of the existing generation pipeline.

This is an evaluation fixture loader, not a product import endpoint. It preserves
missing timestamps and never adds real chats or generated results to test fixtures.
Run with backend/.venv/bin/python; results default to the ignored generated/ tree.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from chat_to_howto.api.session import digest
from chat_to_howto.config import load_deepseek_environment
from chat_to_howto.domain.generation import GenerationBudget
from chat_to_howto.domain.jobs import ExportConversation, ExportScope
from chat_to_howto.domain.messages import MessageInput
from chat_to_howto.providers.deepseek import DeepSeekProvider
from chat_to_howto.repositories.chat import ChatRepository
from chat_to_howto.services.documents import DocumentService
from chat_to_howto.services.document_export import render_docx, render_markdown
from chat_to_howto.services.generation_cache import GenerationCacheSettings
from chat_to_howto.services.generation_jobs import GenerationJobOrchestrator, SYSTEM_PROMPT, SUMMARY_SYSTEM_PROMPT
from chat_to_howto.services.generation_planning import GenerationBudgetExceeded, GenerationScopeBuilder, GenerationSelection, build_generation_plan
from chat_to_howto.services.generation_prompts import topic_prompt


def write_private(path: Path, content: str | bytes) -> None:
    with path.open("xb") as stream:
        os.chmod(path, 0o600)
        stream.write(content.encode("utf-8") if isinstance(content, str) else content)


def write_json(path: Path, value) -> None:
    write_private(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


class EvaluationProvider(DeepSeekProvider):
    """Keep final model output privately for comparison, never credentials/reasoning."""

    def _parse_response(self, raw: bytes, requested_model: str):
        self.response_index += 1
        prefix = self.result_root / f"response-{self.response_index:03d}"
        try:
            envelope = json.loads(raw)
            choice = envelope.get("choices", [{}])[0]
            message = choice.get("message", {})
            content = message.get("content")
            finish = choice.get("finish_reason")
            write_json(prefix.with_suffix(".diagnostics.json"), {
                "finish_reason": finish if finish in {"stop", "length", "content_filter", "tool_calls", "insufficient_system_resource"} else "unknown",
                "content_chars": len(content) if isinstance(content, str) else None,
                "reasoning_chars": len(message.get("reasoning_content") or ""),
            })
            if isinstance(content, str) and content:
                write_private(prefix.with_suffix(".content.txt"), content)
        except (ValueError, TypeError, KeyError, IndexError, AttributeError):
            pass
        return super()._parse_response(raw, requested_model)


def prepare(args, root: Path):
    payload = args.input.read_bytes()
    fingerprint = sha256(payload).hexdigest()
    manifest_path = root / "scope.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        if (manifest["input_sha256"] != fingerprint or manifest["topic"] != args.topic
            or manifest["speakers"] != args.speakers):
            raise ValueError("evaluation input differs from the frozen scope")
        return manifest
    text = payload.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    pattern = "(" + "|".join(re.escape(speaker) for speaker in args.speakers) + r"):[\u00a0 \t]*"
    parts = re.split(pattern, text)
    if parts[0].strip() or len(parts) < 3:
        raise ValueError("transcript does not match the explicitly named speakers")
    records = [(parts[index], parts[index + 1].strip()) for index in range(1, len(parts), 2)]
    if any(not body for _sender, body in records):
        raise ValueError("transcript contains an empty message")
    now = datetime.now(timezone.utc)
    session = f"evaluation-session:{uuid4().hex}"
    batch_id, snapshot_id = f"eval-batch:{uuid4().hex}", f"eval-snapshot:{uuid4().hex}"
    account = f"evaluation:{fingerprint[:24]}"
    conversation = "evaluation-conversation"
    administrative_scope = ExportScope(
        source_id="manual", account_id=account,
        conversations=(ExportConversation(conversation_id=conversation),),
        range_start=now, range_end=now + timedelta(days=1), display_timezone="Asia/Shanghai",
        output_root_id="evaluation-only", configuration_nonce="evaluation-only",
    )
    repository = ChatRepository(root / "evaluation.sqlite3")
    try:
        repository.create_batch(batch_id, administrative_scope)
        message_ids, skipped, stored_records = [], [], []
        for index, (sender, body) in enumerate(records, 1):
            message = repository.record_message(MessageInput(
                source_id="manual", account_id=account, conversation_id=conversation,
                source_message_id=None, source_file_id=f"evaluation:{fingerprint}",
                page_slice_id=None, source_order=index, sent_at_utc=None, original_timestamp=None,
                sender_name=sender, text=body,
            ))
            repository.add_message_to_batch(batch_id, message.message_id)
            stored_records.append(message.model_dump(mode="json"))
            if re.fullmatch(r"\[(?:语音|图片|文件|聊天记录)\](?:\s*\d{2}:\d{2})?", body):
                skipped.append(message.message_id)
            else:
                message_ids.append(message.message_id)
        repository.create_snapshot(snapshot_id, batch_id)
        repository.create_export_consent(
            consent_id="eval-export-consent", session_digest=digest(session), scope=administrative_scope,
            recorded_at_utc=now.isoformat(), expires_at_utc=(now + timedelta(minutes=5)).isoformat(),
        )
        row, _ = repository.start_export_job(
            job_id="eval-source-job", planned_batch_id=batch_id, planned_snapshot_id=snapshot_id,
            session_digest=digest(session), scope=administrative_scope, consent_id="eval-export-consent",
            idempotency_key="eval-source", now_utc=now.isoformat(),
        )
        if not repository.cas_export_job(
            "eval-source-job", expected_state="pending_authorization", expected_revision=int(row["revision"]),
            new_state="complete", batch_id=batch_id, snapshot_id=snapshot_id, searchable=1,
        ):
            raise RuntimeError("could not freeze evaluation scope")
        if args.smoke:
            message_ids = message_ids[-8:]
        manifest = {
            "input_sha256": fingerprint, "topic": args.topic, "speakers": args.speakers,
            "session_id": session, "snapshot_id": snapshot_id, "conversation_id": conversation,
            "range_start": now.isoformat(), "range_end": (now + timedelta(days=1)).isoformat(),
            "range_note": "Administrative fixture range only; every original message timestamp remains null.",
            "message_ids": message_ids, "skipped_placeholder_ids": skipped,
            "source_message_count": len(records), "selected_message_count": len(message_ids),
            "message_scope_sha256": sha256(json.dumps(message_ids).encode()).hexdigest(),
            "import_ui_tested": False,
            "smoke_test": args.smoke,
        }
        write_json(root / "original_messages.json", stored_records)
        write_json(manifest_path, manifest)
        return manifest
    finally:
        repository.close()


def selection(manifest, mode: str, model: str) -> GenerationSelection:
    return GenerationSelection(
        snapshot_id=manifest["snapshot_id"], conversation_ids=(manifest["conversation_id"],),
        start_time=manifest["range_start"], end_time=manifest["range_end"], topic=manifest["topic"],
        mode="complete", generation_kind="summary" if mode == "legacy-summary" else "guide",
        summary_type=None if mode == "legacy-summary" else mode,
        included_message_ids=manifest["message_ids"], excluded_message_ids=(), allow_undated=True,
        provider="deepseek", model=model, redaction_policy_revision="text-only-outbound-v1",
        budget=GenerationBudget(max_messages=2_000, max_input_tokens=100_000, max_provider_calls=20, max_output_tokens=12_000),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--speakers", nargs="+", required=True)
    parser.add_argument("--topic", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default=os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash"))
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--smoke", action="store_true", help="Use only the last eight readable messages for one minimal ideas generation.")
    parser.add_argument("--chunk-tokens", type=int, default=8_000, help="Evaluation-only per-chunk input estimate; applied equally to all compared modes.")
    parser.add_argument("--modes", nargs="+", choices=["legacy-summary", "viewpoints", "ideas", "how_to", "mixed"],
                        default=None)
    args = parser.parse_args()
    args.modes = args.modes or (["ideas"] if args.smoke else ["legacy-summary", "viewpoints", "ideas", "how_to", "mixed"])
    root = args.output.resolve()
    if args.smoke:
        root = root / "smoke"
    project = Path(__file__).resolve().parents[2]
    if project in root.parents and project / "generated" not in root.parents:
        parser.error("private real-data results inside the repository must be under ignored generated/")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    manifest = prepare(args, root)
    repository = ChatRepository(root / "evaluation.sqlite3")
    settings = GenerationCacheSettings(
        prompt_version="topic-editorial-v3", output_schema_version="generation-envelope-v3",
        chunk_strategy_version="message-boundary-synthesis-v2", max_chunk_input_tokens=args.chunk_tokens,
        provider_behavior_revision="deepseek-json-nonthinking-v2",
    )
    try:
        plans = {}
        for mode in args.modes:
            scope = GenerationScopeBuilder(repository).build(selection(manifest, mode, args.model), session_id=manifest["session_id"])
            plan = build_generation_plan(scope, max_chunk_input_tokens=settings.max_chunk_input_tokens)
            plans[mode] = {"messages": len(scope.messages), "calls": plan.provider_calls,
                           "input_token_upper_estimate": plan.total_estimated_input_tokens,
                           "message_scope_sha256": manifest["message_scope_sha256"]}
        print(json.dumps({"prepared": True, "source_messages": manifest["source_message_count"], "plans": plans}, ensure_ascii=False))
        if args.prepare_only:
            return 0
        load_deepseek_environment(project)
        provider = EvaluationProvider.from_environment(timeout_seconds=180)
        orchestrator = GenerationJobOrchestrator(repository=repository, provider_factory=lambda _: provider,
                                                cache_settings=settings)
        orchestrator.reconcile_interrupted()
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
        run_dir = root / run_id
        run_dir.mkdir(mode=0o700)
        provider.result_root = run_dir
        provider.response_index = 0
        write_json(run_dir / "comparison_scope.json", {
            "topic": args.topic, "input_sha256": manifest["input_sha256"], "plans": plans, "model": args.model,
            "max_chunk_input_tokens": args.chunk_tokens,
            "baseline_note": "Legacy Summary had no summary_type. Its unchanged generic prompt is the baseline; no direction is retroactively invented.",
        })
        failed = False
        for mode in args.modes:
            preview = orchestrator.create_preview(selection(manifest, mode, args.model), session_id=manifest["session_id"])
            write_private(run_dir / f"{mode}.preview.json", preview.model_dump_json(indent=2))
            consent = orchestrator.issue_consent(preview.preview_id, session_id=manifest["session_id"], confirmed=True)
            job = orchestrator.start_job(consent.consent_id, session_id=manifest["session_id"], idempotency_key=f"eval:{run_id}:{mode}")
            print(json.dumps({"mode": mode, "state": "running", "max_calls": preview.provider_calls}), flush=True)
            completed = orchestrator.run_job(job.job_id)
            write_private(run_dir / f"{mode}.json", completed.model_dump_json(indent=2))
            if completed.state != "complete":
                failed = True
                print(json.dumps({"mode": mode, "state": completed.state, "error": completed.error_code}), flush=True)
                break  # Inspect the first failure before spending on further modes.
            document = DocumentService(repository=repository).create_from_generation(job.job_id, session_id=manifest["session_id"])
            write_private(run_dir / f"{mode}.md", render_markdown(document.current_version, document.source_evidence, source_mode="body_only"))
            write_private(run_dir / f"{mode}.with-sources.md", render_markdown(document.current_version, document.source_evidence, source_mode="with_sources"))
            write_private(run_dir / f"{mode}.docx", render_docx(document.current_version, document.source_evidence, source_mode="with_sources"))
            print(json.dumps({"mode": mode, "state": "complete", "evidence_count": len(document.source_evidence),
                              "calls": completed.usage.request_count}), flush=True)
        print(json.dumps({"results": str(run_dir), "all_complete": not failed}), flush=True)
        return int(failed)
    finally:
        repository.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        # Never echo provider envelopes, credentials, or real chat bodies on failure.
        print(json.dumps({"evaluation_error": type(error).__name__}), file=sys.stderr)
        raise SystemExit(1)
