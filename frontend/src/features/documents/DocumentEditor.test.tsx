import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import type { LocalApiClient } from "../../api/client";
import type { HowToDocument } from "../../types";

const content = {
  status: "complete" as const, title: "AI 原始标题", preparations: [],
  steps: [{ instruction: "AI 原始步骤", location: null, parameters: [], conditions: [], warnings: [],
    expected_result: null, source_message_ids: [`message:v1:${"c".repeat(64)}`] }],
  checks: [], missing_information: [], conflicts: [],
};
const initial: HowToDocument = {
  document_id: "document:p3", source_generation_job_id: "generation-job:p2", source_snapshot_id: "snapshot:p1",
  created_at: "2026-09-05T00:00:00Z", updated_at: "2026-09-05T00:00:00Z",
  current_version: { document_id: "document:p3", version_id: "document-version:1", ordinal: 1, origin: "ai_generated",
    evidence_status: "verified", parent_version_id: null, restored_from_version_id: null,
    source_generation_job_id: "generation-job:p2", change_summary: "", created_at: "2026-09-05T00:00:00Z", content },
  versions: [],
  source_evidence: [{ message_id: `message:v1:${"c".repeat(64)}`, conversation_id: "conversation-a",
    timestamp: "2026-09-01T00:00:01Z", sender_id: "sender-a", sender_name: "合成发送者", quote: "合成证据" }],
};
initial.versions = [initial.current_version];

it("刷新后可直接打开已恢复的最新版本并继续编辑导出", async () => {
  const version2 = { ...initial.current_version, version_id: "document-version:2", ordinal: 2,
    origin: "user_edited" as const, evidence_status: "unverified" as const,
    parent_version_id: "document-version:1", content: { ...content, title: "指南 version 2" } };
  const recovered = { ...initial, current_version: version2, versions: [initial.current_version, version2] };
  const version3 = { ...version2, version_id: "document-version:3", ordinal: 3,
    parent_version_id: "document-version:2", content: { ...content, title: "刷新后继续编辑" } };
  const saved = { ...recovered, current_version: version3, versions: [...recovered.versions, version3] };
  const api = { createDocument: vi.fn(), createDocumentVersion: vi.fn().mockResolvedValue(saved),
    restoreDocumentVersion: vi.fn(), exportDocument: vi.fn().mockResolvedValue({
      artifact_id: "document-artifact:recovered", document_id: "document:p3", version_id: "document-version:3",
      format: "markdown", source_mode: "body_only", filename: "recovered.md", byte_size: 123,
      sha256: "a".repeat(64), created_at: "2026-09-05T00:00:00Z",
    }) } as unknown as LocalApiClient;
  const user = userEvent.setup();
  const { DocumentEditor } = await import("./DocumentEditor");
  render(<DocumentEditor api={api} initialDocument={recovered} outputRootId="approved-root" />);

  expect(await screen.findByDisplayValue("指南 version 2")).toBeVisible();
  expect(api.createDocument).not.toHaveBeenCalled();
  await user.clear(screen.getByRole("textbox", { name: "文档标题" }));
  await user.type(screen.getByRole("textbox", { name: "文档标题" }), "刷新后继续编辑");
  await user.click(screen.getByRole("button", { name: "保存为新版本" }));
  await user.click(screen.getByRole("button", { name: "导出当前版本" }));

  expect(api.createDocumentVersion).toHaveBeenCalledWith(
    "document:p3", "document-version:2", expect.objectContaining({ title: "刷新后继续编辑" }), "",
  );
  expect(api.exportDocument).toHaveBeenCalledWith(
    "document:p3", "document-version:3", "markdown", "body_only", "approved-root",
  );
});

it("创建本地文档、保存用户编辑且不把编辑版标成 AI 原文", async () => {
  const edited: HowToDocument = { ...initial, current_version: { ...initial.current_version,
    version_id: "document-version:2", ordinal: 2, origin: "user_edited", evidence_status: "unverified",
    parent_version_id: "document-version:1", content: { ...content, title: "用户标题", steps: [{ ...content.steps[0], instruction: "用户步骤" }] } },
    versions: [initial.current_version] };
  edited.versions = [...edited.versions, edited.current_version];
  const api = { createDocument: vi.fn().mockResolvedValue(initial), createDocumentVersion: vi.fn().mockResolvedValue(edited),
    restoreDocumentVersion: vi.fn() } as unknown as LocalApiClient;
  const user = userEvent.setup();
  const { DocumentEditor } = await import("./DocumentEditor");
  render(<DocumentEditor api={api} generationJobId="generation-job:p2" />);

  await user.click(screen.getByRole("button", { name: "创建可编辑文档" }));
  expect(await screen.findByText(/AI 原始版本.*证据已验证/)).toBeVisible();
  await user.clear(screen.getByRole("textbox", { name: "文档标题" }));
  await user.type(screen.getByRole("textbox", { name: "文档标题" }), "用户标题");
  await user.clear(screen.getByRole("textbox", { name: "步骤 1" }));
  await user.type(screen.getByRole("textbox", { name: "步骤 1" }), "用户步骤");
  await user.type(screen.getByRole("textbox", { name: "修改说明" }), "本地调整");
  await user.click(screen.getByRole("button", { name: "保存为新版本" }));

  expect(await screen.findByText(/用户编辑版本.*不再视为原始模型输出/)).toBeVisible();
  expect(api.createDocumentVersion).toHaveBeenCalledWith("document:p3", "document-version:1", expect.objectContaining({ title: "用户标题" }), "本地调整");
  expect(screen.getByText("版本 1")).toBeVisible();
  expect(screen.getByText("版本 2")).toBeVisible();
});

it("恢复历史会创建新版本并可展开查看来源证据", async () => {
  const current = { ...initial.current_version, version_id: "document-version:2", ordinal: 2, origin: "user_edited" as const,
    evidence_status: "unverified" as const, parent_version_id: "document-version:1", content: { ...content, title: "Changed" } };
  const edited = { ...initial, current_version: current, versions: [initial.current_version, current] };
  const restoredVersion = { ...initial.current_version, version_id: "document-version:3", ordinal: 3, origin: "restored" as const,
    evidence_status: "unverified" as const, parent_version_id: "document-version:2", restored_from_version_id: "document-version:1" };
  const restored = { ...edited, current_version: restoredVersion, versions: [...edited.versions, restoredVersion] };
  const api = { createDocument: vi.fn().mockResolvedValue(edited), createDocumentVersion: vi.fn(),
    restoreDocumentVersion: vi.fn().mockResolvedValue(restored) } as unknown as LocalApiClient;
  const user = userEvent.setup();
  const { DocumentEditor } = await import("./DocumentEditor");
  render(<DocumentEditor api={api} generationJobId="generation-job:p2" />);
  await user.click(screen.getByRole("button", { name: "创建可编辑文档" }));
  await user.click(await screen.findByRole("button", { name: "从版本 1 创建恢复版本" }));
  await waitFor(() => expect(api.restoreDocumentVersion).toHaveBeenCalledWith("document:p3", "document-version:1"));
  expect(await screen.findByText(/恢复版本.*不覆盖历史/)).toBeVisible();
  await user.click(screen.getByText(/查看来源证据/));
  expect(screen.getByRole("region", { name: "文档来源证据" })).toHaveTextContent("合成证据");
});

it("可将当前版本导出到已批准的本地位置", async () => {
  const api = { createDocument: vi.fn().mockResolvedValue(initial), createDocumentVersion: vi.fn(),
    restoreDocumentVersion: vi.fn(), exportDocument: vi.fn().mockResolvedValue({
      artifact_id: "document-artifact:p3", document_id: "document:p3", version_id: "document-version:1",
      format: "docx", source_mode: "with_sources", filename: "safe-name.docx", byte_size: 1234,
      sha256: "a".repeat(64), created_at: "2026-09-05T00:00:00Z",
    }) } as unknown as LocalApiClient;
  const user = userEvent.setup();
  const { DocumentEditor } = await import("./DocumentEditor");
  render(<DocumentEditor api={api} generationJobId="generation-job:p2" outputRootId="approved-root" />);
  await user.click(screen.getByRole("button", { name: "创建可编辑文档" }));
  await user.selectOptions(await screen.findByRole("combobox", { name: "导出格式" }), "docx");
  await user.selectOptions(screen.getByRole("combobox", { name: "来源内容" }), "with_sources");
  await user.click(screen.getByRole("button", { name: "导出当前版本" }));

  expect(await screen.findByRole("status")).toHaveTextContent("safe-name.docx");
  expect(api.exportDocument).toHaveBeenCalledWith(
    "document:p3", "document-version:1", "docx", "with_sources", "approved-root",
  );
});

it("删除文档前必须先查看影响并二次确认", async () => {
  const counts = { documents: 1, versions: 1, document_artifacts: 0, generation_jobs: 1, generation_requests: 0,
    generation_consents: 1, generation_previews: 1, cache_entries: 1, export_batches: 0,
    export_jobs: 0, export_consents: 0, snapshots: 0, messages: 0, managed_files: 0 };
  const api = { createDocument: vi.fn().mockResolvedValue(initial), createDocumentVersion: vi.fn(),
    restoreDocumentVersion: vi.fn(), prepareDeletion: vi.fn().mockResolvedValue({
      plan_id: "deletion-plan:p3", target_kind: "document", target_id: "document:p3",
      include_managed_files: false, fingerprint: "d".repeat(64), counts,
      effects: ["数据库：删除文档"], expires_at: "2099-01-01T00:00:00Z",
    }), confirmDeletion: vi.fn().mockResolvedValue({ plan_id: "deletion-plan:p3", state: "complete",
      deleted_counts: counts, managed_files_retained: false }) } as unknown as LocalApiClient;
  const user = userEvent.setup();
  const { DocumentEditor } = await import("./DocumentEditor");
  render(<DocumentEditor api={api} generationJobId="generation-job:p2" />);
  await user.click(screen.getByRole("button", { name: "创建可编辑文档" }));

  await user.click(await screen.findByRole("button", { name: "查看删除影响" }));
  await user.click(screen.getByRole("checkbox", { name: /我已核对上述影响/ }));
  await user.click(screen.getByRole("button", { name: "确认删除" }));

  expect(api.confirmDeletion).toHaveBeenCalledWith("deletion-plan:p3", "d".repeat(64));
  expect(await screen.findByRole("button", { name: "创建可编辑文档" })).toBeVisible();
});
