import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import type { LocalApiClient } from "../../api/client";
import type { HowToDocument } from "../../types";

const content = {
  status: "complete" as const, title: "指南 version 2", preparations: [],
  steps: [{ instruction: "已编辑步骤", location: null, parameters: [], conditions: [], warnings: [],
    expected_result: null, source_message_ids: [`message:v1:${"c".repeat(64)}`] }],
  checks: [], missing_information: [], conflicts: [],
};
const version = { document_id: "document:p3", version_id: "document-version:2", ordinal: 2,
  origin: "user_edited" as const, evidence_status: "unverified" as const,
  parent_version_id: "document-version:1", restored_from_version_id: null,
  source_generation_job_id: "generation-job:p2", change_summary: "P0",
  created_at: "2026-09-05T00:00:00Z", content };
const document: HowToDocument = { document_id: "document:p3", source_generation_job_id: "generation-job:p2",
  source_snapshot_id: "snapshot:p1", created_at: "2026-09-05T00:00:00Z", updated_at: "2026-09-05T00:00:01Z",
  current_version: version, versions: [version], source_evidence: [{ message_id: `message:v1:${"c".repeat(64)}`,
    conversation_id: "conversation-a", timestamp: "2026-09-01T00:00:01Z", sender_id: null,
    sender_name: "测试", quote: "证据" }] };

it("页面初始化时恢复文档列表并可打开最新版本", async () => {
  const api = { recoverDocuments: vi.fn().mockResolvedValue({ documents: [document], next_cursor: null }),
    document: vi.fn().mockResolvedValue(document),
    createDocumentVersion: vi.fn(), restoreDocumentVersion: vi.fn(), exportDocument: vi.fn(),
    prepareDeletion: vi.fn(), confirmDeletion: vi.fn() } as unknown as LocalApiClient;
  const user = userEvent.setup();
  const { DocumentLibrary } = await import("./DocumentLibrary");
  render(<DocumentLibrary api={api} outputRootId="approved-root" />);

  expect(await screen.findByText("指南 version 2")).toBeVisible();
  await user.click(screen.getByRole("button", { name: "打开最新版本" }));
  expect(await screen.findByRole("textbox", { name: "文档标题" })).toHaveValue("指南 version 2");
  expect(api.recoverDocuments).toHaveBeenCalledWith(null);
});

it("简洁工作区重新打开同一文档时从服务取最新版本，不预览缓存版本", async () => {
  let latest = document;
  const api = { recoverDocuments: vi.fn().mockResolvedValue({ documents: [document], next_cursor: null }),
    document: vi.fn(async () => latest) } as unknown as LocalApiClient;
  const user = userEvent.setup();
  const { ImportedChatWorkspace } = await import("../imports/ImportedChatWorkspace");
  render(<ImportedChatWorkspace api={api} committed={null} sourceControls={null} outputRootId=""
    importBusy={false} onScopeLockChange={() => undefined} />);
  await user.click(screen.getByText("已保存的文档"));
  await user.click(await screen.findByRole("button", { name: "打开最新版本" }));
  expect(await screen.findByRole("heading", { name: "指南 version 2" })).toBeVisible();
  latest = { ...document, current_version: { ...version, ordinal: 3, version_id: "document-version:3",
    content: { ...content, title: "已确认的新标题", user_text: "已确认的新标题\n\n最新正文" } } };
  await user.click(screen.getByRole("button", { name: "打开最新版本" }));
  expect(await screen.findByRole("heading", { name: "已确认的新标题" })).toBeVisible();
  expect(screen.getByText(/最新正文/)).toBeVisible();
  expect(screen.queryByRole("heading", { name: "指南 version 2" })).not.toBeInTheDocument();
});

it("读取最新版本失败时报告错误，不把列表缓存当作最新文档", async () => {
  const api = { recoverDocuments: vi.fn().mockResolvedValue({ documents: [document], next_cursor: null }),
    document: vi.fn().mockRejectedValue(new Error("offline")) } as unknown as LocalApiClient;
  const { DocumentLibrary } = await import("./DocumentLibrary");
  render(<DocumentLibrary api={api} outputRootId="" />);
  await userEvent.setup().click(await screen.findByRole("button", { name: "打开最新版本" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("最新版本");
  expect(screen.queryByRole("textbox", { name: "文档标题" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "打开最新版本" })).toBeEnabled();
});

it("清理期间返回的旧文档响应保留版本但不能复活原始依据", async () => {
  let resolveOpen!: (value: HowToDocument) => void;
  const api = { recoverDocuments: vi.fn().mockResolvedValue({ documents: [document], next_cursor: null }),
    document: vi.fn().mockReturnValue(new Promise<HowToDocument>(resolve => { resolveOpen = resolve; })),
  } as unknown as LocalApiClient;
  const { DocumentLibrary } = await import("./DocumentLibrary");
  const user = userEvent.setup();
  render(<DocumentLibrary api={api} outputRootId="" />);
  await user.click(await screen.findByRole("button", { name: "打开最新版本" }));
  await act(async () => {
    window.dispatchEvent(new Event("minutes-raw-cleared"));
    resolveOpen(document);
  });
  await waitFor(() => expect(screen.getByRole("textbox", { name: "文档标题" })).toHaveValue("指南 version 2"));
  expect(screen.queryByText("证据", { exact: true })).not.toBeInTheDocument();
});
