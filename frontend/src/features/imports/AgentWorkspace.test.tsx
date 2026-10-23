import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import type { LocalApiClient } from "../../api/client";
import { ImportedChatWorkspace } from "./ImportedChatWorkspace";
const committed = { import_id: "import:a", batch_id: "batch:a", snapshot_id: "snapshot:a", source_id: "manual" as const,
 account_id: "account:a", range_start: "2026-10-01T00:00:00Z", range_end: "2026-10-02T00:00:00Z", display_timezone: "UTC",
 conversations: [{ conversation_selection_id: "selection:a", display_name: "合成讨论", conversation_type: "group" }], warnings: [] };
const row = { message_id: "message:a", snapshot_id: committed.snapshot_id, batch_id: committed.batch_id,
 conversation_id: "conversation:a", timestamp: null, sender_id: null, sender_name: "甲", text: "创业建议" };
const preview = { preview_id: "preview:a", expires_at: "2099-01-01T00:00:00Z", scope_fingerprint: "a".repeat(64),
 will_send_external: true, outbound_payloads: ["完整脱敏要求和正文"], provider: "fake", model: "synthetic",
 message_count: 1, character_count: 10, provider_calls: 1, estimated_input_tokens: 10, max_output_tokens: 100 };
function setup(hits = true) {
 const api = { searchChats: vi.fn().mockResolvedValue({ results: hits ? [row] : [], next_cursor: null }),
  planAgent: vi.fn().mockResolvedValue({ instruction: "整理创业和就业观点", goal: "比较观点", keywords: ["创业", "就业"],
   summary_type: "viewpoints", all_content: false, needs_clarification: false, notices: [] }),
  previewGenerationContext: vi.fn().mockResolvedValue({ messages: [row], added_count: 0 }),
  previewGeneration: vi.fn().mockResolvedValue(preview), expandTopic: vi.fn(), authorizeGeneration: vi.fn(), startGeneration: vi.fn(),
  recoverDocuments: vi.fn().mockResolvedValue({ documents: [], next_cursor: null }),
 } as unknown as LocalApiClient;
 render(<ImportedChatWorkspace api={api} committed={committed} sourceControls={null} outputRootId="" importBusy={false} onScopeLockChange={() => undefined} />);
 return api;
}
it("本地理解展示可编辑任务，按提取词检索并展示完整预览，修改即撤销授权", async () => {
 const api = setup(); const user = userEvent.setup();
 await user.type(screen.getByLabelText("整理要求"), "整理创业和就业观点");
 await user.click(screen.getByRole("button", { name: "理解并整理" }));
 expect(await screen.findByLabelText("检索关键词")).toHaveValue("创业、就业");
 expect(screen.getByLabelText("任务整理方向")).toHaveValue("viewpoints");

 expect(await screen.findByText("完整脱敏要求和正文")).toBeVisible();
 expect(api.searchChats).toHaveBeenCalledWith(expect.objectContaining({ query: "创业" }));
 expect(api.searchChats).toHaveBeenCalledWith(expect.objectContaining({ query: "就业" }));
 expect(api.previewGeneration).toHaveBeenCalledWith(expect.objectContaining({ topic: "整理创业和就业观点", summary_type: "viewpoints" }));
 expect(api.expandTopic).not.toHaveBeenCalled(); expect(api.startGeneration).not.toHaveBeenCalled();
 await user.click(screen.getByRole("checkbox", { name: /授权本次向外部 AI/ }));
 await user.clear(screen.getByLabelText("检索关键词"));
 expect(screen.queryByRole("region", { name: "AI 外发预览" })).not.toBeInTheDocument();
 expect(api.authorizeGeneration).not.toHaveBeenCalled();
});
it("零命中时阻止生成；取消预览不授权不发送", async () => {
 const api = setup(false); const user = userEvent.setup();
 await user.type(screen.getByLabelText("整理要求"), "整理创业和就业观点"); await user.click(screen.getByRole("button", { name: "理解并整理" }));
 await screen.findByLabelText("检索关键词");
 await waitFor(() => expect(screen.getByText(/没有找到可整理/)).toBeVisible());
 expect(api.previewGeneration).not.toHaveBeenCalled();
 vi.mocked(api.searchChats).mockResolvedValue({ results: [row], next_cursor: null });
 await user.click(screen.getByRole("button", { name: "重新预览外发内容" }));
 await screen.findByText("完整脱敏要求和正文");
 await user.click(screen.getByRole("button", { name: "取消本次整理" }));
 expect(screen.queryByRole("region", { name: "AI 外发预览" })).not.toBeInTheDocument();
 expect(api.startGeneration).not.toHaveBeenCalled();
});
it("修改要求后忽略较早的任务理解响应", async () => {
 const api = setup(); let resolve!: (value: unknown) => void;
 vi.mocked(api.planAgent).mockReturnValue(new Promise(r => { resolve = r; }) as never);
 const user = userEvent.setup(); await user.type(screen.getByLabelText("整理要求"), "旧要求");
 await user.click(screen.getByRole("button", { name: "理解并整理" }));
 fireEvent.change(screen.getByLabelText("整理要求"), { target: { value: "新要求" } });
 resolve({ instruction: "旧要求", goal: "旧要求", keywords: ["旧"], summary_type: "mixed", all_content: false, needs_clarification: false, notices: [] });
 await waitFor(() => expect(screen.queryByText("正在理解要求…")).not.toBeInTheDocument());
 expect(screen.queryByLabelText("检索关键词")).not.toBeInTheDocument();
});
it("含糊规划必须人工核对，普通空关键词不能扩大到全文", async () => {
 const api = setup(); vi.mocked(api.planAgent).mockResolvedValue({ instruction: "整理一下", goal: "整理一下", keywords: [], summary_type: "mixed", all_content: false, needs_clarification: true, notices: ["请补充主题"] });
 const user = userEvent.setup(); await user.type(screen.getByLabelText("整理要求"), "整理一下"); await user.click(screen.getByRole("button", { name: "理解并整理" }));
 await screen.findByLabelText("检索关键词"); expect(screen.getByRole("button", { name: "重新预览外发内容" })).toBeDisabled();
 await user.click(screen.getByRole("button", { name: "已核对以上任务" })); expect(screen.getByRole("button", { name: "重新预览外发内容" })).toBeDisabled();
 await user.type(screen.getByLabelText("检索关键词"), "创业"); expect(screen.getByRole("button", { name: "重新预览外发内容" })).toBeEnabled();
 expect(api.previewGeneration).not.toHaveBeenCalled();
});
it("明确全部内容才容许空关键词在当前聊天范围本地检索", async () => {
 const api = setup(); vi.mocked(api.planAgent).mockResolvedValue({ instruction: "总结全部内容", goal: "总结全部内容", keywords: [], summary_type: "mixed", all_content: true, needs_clarification: false, notices: [] });
 const user = userEvent.setup(); await user.type(screen.getByLabelText("整理要求"), "总结全部内容"); await user.click(screen.getByRole("button", { name: "理解并整理" }));
 await screen.findByLabelText("检索关键词");
 await screen.findByText("完整脱敏要求和正文"); expect(api.searchChats).toHaveBeenCalledWith(expect.objectContaining({ query: "", conversation_selection_ids: ["selection:a"] }));
});
