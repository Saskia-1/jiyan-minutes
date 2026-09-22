import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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

it("无文件选择 API 时发起真实浏览器下载，不声称能控制本地目录", async () => {
  const api = { downloadDocument: vi.fn().mockResolvedValue(new Blob(["document bytes"])) } as unknown as LocalApiClient;
  vi.stubGlobal("showSaveFilePicker", undefined);
  const createObjectURL = vi.fn().mockReturnValue("blob:local-document");
  class DownloadURL extends URL {
    static createObjectURL = createObjectURL;
    static revokeObjectURL = vi.fn();
  }
  vi.stubGlobal("URL", DownloadURL);
  let filename = "";
  const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) { filename = this.download; });
  const { DocumentEditor } = await import("./DocumentEditor");
  const user = userEvent.setup();
  render(<DocumentEditor compact api={api} initialDocument={initial} />);
  await user.click(screen.getByRole("button", { name: "下载文档" }));
  expect(await screen.findByText(/已发起浏览器下载/)).toBeVisible();
  expect(filename).toBe("AI 原始标题.docx");
  expect(createObjectURL).toHaveBeenCalledOnce();
  click.mockRestore(); vi.unstubAllGlobals();
});

it("简洁编辑默认关闭，完整文本确认后追加版本并下载最新正文", async () => {
  const text = "用户确认标题\n\n自由补充的完整内容";
  const saved: HowToDocument = { ...initial, current_version: { ...initial.current_version,
    version_id: "document-version:2", ordinal: 2, origin: "user_edited", evidence_status: "unverified",
    content: { ...content, title: "用户确认标题", user_text: text } }, versions: initial.versions };
  const api = { createDocumentVersion: vi.fn().mockResolvedValue(saved),
    downloadDocument: vi.fn().mockResolvedValue(new Blob([text])) } as unknown as LocalApiClient;
  const write = vi.fn().mockResolvedValue(undefined), close = vi.fn().mockResolvedValue(undefined);
  const picker = vi.fn().mockResolvedValue({ createWritable: async () => ({ write, close }) });
  vi.stubGlobal("showSaveFilePicker", picker);
  const { DocumentEditor } = await import("./DocumentEditor");
  const user = userEvent.setup();
  render(<DocumentEditor compact api={api} initialDocument={initial} />);
  expect(screen.getByRole("radio", { name: "否" })).toBeChecked();
  expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "预览结果" }));
  expect(screen.queryByText("AI 原始步骤")).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "预览结果" }));
  expect(screen.getByText("AI 原始步骤")).toBeVisible();
  await user.click(screen.getByRole("radio", { name: "是" }));
  expect(screen.getAllByRole("textbox")).toHaveLength(1);
  expect(screen.getByRole("textbox", { name: "完整文档" })).toHaveValue("AI 原始标题\n\n1. AI 原始步骤");
  fireEvent.change(screen.getByRole("textbox", { name: "完整文档" }), { target: { value: text } });
  expect(screen.getByText("AI 原始步骤")).toBeVisible();
  await user.click(screen.getByRole("button", { name: "确认修改" }));
  expect(await screen.findByText(/自由补充的完整内容/)).toBeVisible();
  expect(screen.queryByText(/AI 生成 · 本地证据已验证/)).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "下载文档" }));
  await waitFor(() => expect(write).toHaveBeenCalledOnce());
  expect(api.downloadDocument).toHaveBeenCalledWith(initial.document_id, "document-version:2", "docx", "body_only");
  expect(api.createDocumentVersion).toHaveBeenCalledWith(initial.document_id, initial.current_version.version_id,
    expect.objectContaining({ user_text: text }), "用户确认完整文本");
  expect(initial.current_version.content.user_text).toBeUndefined();
  vi.unstubAllGlobals();
});

it("自然章节可编辑且保留原消息引用，证据默认折叠并保留未知时间", async () => {
  const topicContent = { ...content, steps: [], summary: { summary_type: "ideas" as const,
    sections: [{ heading: "音乐身份", text: "将音乐标签与分享整合为个人音乐画像。",
      source_message_ids: [initial.source_evidence[0].message_id] }] } };
  const doc: HowToDocument = { ...initial, current_version: { ...initial.current_version, content: topicContent },
    versions: [{ ...initial.current_version, content: topicContent }],
    source_evidence: [{ ...initial.source_evidence[0], timestamp: null }] };
  const api = { createDocumentVersion: vi.fn().mockResolvedValue(doc) } as unknown as LocalApiClient;
  const { DocumentEditor } = await import("./DocumentEditor");
  const user = userEvent.setup();
  render(<DocumentEditor api={api} initialDocument={doc} />);
  const evidenceToggle = screen.getAllByText(/查看原始依据/)[0];
  expect(evidenceToggle.closest("details")).not.toHaveAttribute("open");
  await user.click(evidenceToggle);
  expect(screen.getAllByText("时间未知")[0]).toBeVisible();
  expect(screen.getAllByText("合成证据")[0]).toBeVisible();
  await user.clear(screen.getByRole("textbox", { name: "章节正文 1" }));
  await user.type(screen.getByRole("textbox", { name: "章节正文 1" }), "用户修改的构思");
  await user.click(screen.getByRole("button", { name: "保存为新版本" }));
  expect(api.createDocumentVersion).toHaveBeenCalledWith("document:p3", "document-version:1",
    expect.objectContaining({ summary: { summary_type: "ideas", sections: [{ heading: "音乐身份",
      text: "用户修改的构思", source_message_ids: [initial.source_evidence[0].message_id] }] } }), "");
});

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

it("聊天总结可以修改已有结论文字并保留原消息证据引用", async () => {
  const summaryContent = { ...content, title: "就业总结", steps: [], summary: {
    core_summary: [{ text: "讨论了秋招", source_message_ids: [initial.source_evidence[0].message_id] }],
    discussions: [], decisions: [], action_items: [], open_questions: [],
  } };
  const summaryDocument: HowToDocument = { ...initial,
    current_version: { ...initial.current_version, content: summaryContent },
    versions: [{ ...initial.current_version, content: summaryContent }],
  };
  const saved = { ...summaryDocument, current_version: { ...summaryDocument.current_version,
    version_id: "document-version:2", ordinal: 2, content: { ...summaryContent, summary: {
      ...summaryContent.summary, core_summary: [{ ...summaryContent.summary.core_summary[0], text: "秋招需要提前准备简历" }],
    } },
  } };
  const api = { createDocumentVersion: vi.fn().mockResolvedValue(saved),
    restoreDocumentVersion: vi.fn() } as unknown as LocalApiClient;
  const { DocumentEditor } = await import("./DocumentEditor");
  const user = userEvent.setup();
  render(<DocumentEditor api={api} initialDocument={summaryDocument} />);

  await user.clear(screen.getByRole("textbox", { name: "核心摘要 1" }));
  await user.type(screen.getByRole("textbox", { name: "核心摘要 1" }), "秋招需要提前准备简历");
  await user.click(screen.getByRole("button", { name: "保存为新版本" }));

  expect(api.createDocumentVersion).toHaveBeenCalledWith(
    "document:p3", "document-version:1", expect.objectContaining({
      summary: expect.objectContaining({
        core_summary: [{ text: "秋招需要提前准备简历", source_message_ids: [initial.source_evidence[0].message_id] }],
      }),
    }), "",
  );
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
