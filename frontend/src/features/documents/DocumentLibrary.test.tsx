import { render, screen } from "@testing-library/react";
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
