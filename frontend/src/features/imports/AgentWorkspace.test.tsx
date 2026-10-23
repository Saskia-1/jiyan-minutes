import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { ApiError, type LocalApiClient } from "../../api/client";
import { ImportedChatWorkspace } from "./ImportedChatWorkspace";
const committed = { import_id: "import:a", batch_id: "batch:a", snapshot_id: "snapshot:a", source_id: "manual" as const,
 account_id: "account:a", range_start: "2026-10-01T00:00:00Z", range_end: "2026-10-02T00:00:00Z", display_timezone: "UTC",
 conversations: [{ conversation_selection_id: "selection:a", display_name: "合成讨论", conversation_type: "group" }], warnings: [] };
const row = { message_id: "message:a", snapshot_id: committed.snapshot_id, batch_id: committed.batch_id,
 conversation_id: "conversation:a", timestamp: null, sender_id: null, sender_name: "甲", text: "创业建议" };
const preview = { preview_id: "preview:a", expires_at: "2099-01-01T00:00:00Z", scope_fingerprint: "a".repeat(64),
 will_send_external: true, outbound_payloads: ["完整脱敏要求和正文"], provider: "fake", model: "synthetic",
 message_count: 1, character_count: 10, provider_calls: 1, estimated_input_tokens: 10, max_output_tokens: 100 };
const planPreview = { preview_id: "a".repeat(32), preview_fingerprint: "b".repeat(64), instruction: "脱敏整理要求",
 outbound_text: '{"operation":"plan_agent","instruction":"脱敏整理要求"}', provider: "deepseek", model: "deepseek-chat",
 expires_at_utc: "2099-01-01T00:00:00Z", max_output_tokens: 1200 };
const aiPlan = { instruction: "脱敏整理要求", goal: "提取创业方向", keywords: ["创业"], summary_type: "ideas" as const,
 all_content: false, needs_clarification: false, notices: [] };
function setup(hits = true, local = true) {
 const api = { searchChats: vi.fn().mockResolvedValue({ results: hits ? [row] : [], next_cursor: null }),
  previewAgentPlan: vi.fn().mockResolvedValue(planPreview), executeAgentPlan: vi.fn().mockResolvedValue(aiPlan), cancelAgentPlan: vi.fn().mockResolvedValue(undefined),
  planAgent: vi.fn().mockResolvedValue({ instruction: "整理创业和就业观点", goal: "比较观点", keywords: ["创业", "就业"],
   summary_type: "viewpoints", all_content: false, needs_clarification: false, notices: [] }),
  previewGenerationContext: vi.fn().mockResolvedValue({ messages: [row], added_count: 0 }),
  previewGeneration: vi.fn().mockResolvedValue(preview), expandTopic: vi.fn(), authorizeGeneration: vi.fn(), startGeneration: vi.fn(),
  recoverDocuments: vi.fn().mockResolvedValue({ documents: [], next_cursor: null }),
 } as unknown as LocalApiClient;
 render(<ImportedChatWorkspace api={api} committed={committed} sourceControls={null} outputRootId="" importBusy={false} onScopeLockChange={() => undefined} />);
 if (local) fireEvent.click(screen.getByRole("radio", { name: "本地规则（不联网）" }));
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

it("默认AI理解展示完整要求与预算；规划授权和正文授权独立且原始约束保留", async () => {
 const api = setup(true, false); const user = userEvent.setup(); const original = "把大家聊的创业点子捋一捋，不要遗漏保留的限制";
 await user.type(screen.getByLabelText("整理要求"), original); await user.click(screen.getByRole("button", { name: "理解并整理" }));
 const panel = await screen.findByRole("region", { name: "AI 任务理解预览" });
 expect(panel).toHaveTextContent(planPreview.outbound_text); expect(panel).toHaveTextContent("deepseek-chat");
 expect(panel).toHaveTextContent("最多 1 次调用"); expect(panel).toHaveTextContent("1200"); expect(panel).toHaveTextContent("费用：未知");
 expect(api.planAgent).not.toHaveBeenCalled(); expect(api.executeAgentPlan).not.toHaveBeenCalled();
 expect(screen.getByRole("button", { name: "授权并理解要求" })).toBeDisabled();
 await user.click(screen.getByRole("checkbox", { name: "授权本次向外部 AI 发送上述整理要求" }));
 await user.click(screen.getByRole("button", { name: "授权并理解要求" }));
 expect(await screen.findByLabelText("AI 提取的目标")).toHaveValue("提取创业方向");
 expect(screen.getByLabelText("AI 提取的目标")).toHaveAttribute("readonly");
 expect(screen.getByText("需调整目标时，请修改下方完整要求。")).toBeVisible();
 expect(screen.getByLabelText("整理目标与完整要求")).toHaveValue(original);
 await screen.findByText("完整脱敏要求和正文");
 expect(api.executeAgentPlan).toHaveBeenCalledExactlyOnceWith(planPreview.preview_id, planPreview.preview_fingerprint);
 expect(api.previewGeneration).toHaveBeenCalledWith(expect.objectContaining({ topic: original }));
 expect(api.authorizeGeneration).not.toHaveBeenCalled(); expect(api.startGeneration).not.toHaveBeenCalled();
 expect(screen.getByRole("checkbox", { name: "授权本次向外部 AI 发送上述冻结范围纯文本" })).not.toBeChecked();
});
it.each(["cancel", "instruction", "mode", "source", "raw"])("AI预览在%s后失效，禁止复用", async action => {
 const api = setup(true, false); const user = userEvent.setup();
 await user.type(screen.getByLabelText("整理要求"), "整理创业点子"); await user.click(screen.getByRole("button", { name: "理解并整理" }));
 await screen.findByRole("region", { name: "AI 任务理解预览" });
 await user.click(screen.getByRole("checkbox", { name: "授权本次向外部 AI 发送上述整理要求" }));
 if (action === "cancel") await user.click(screen.getByRole("button", { name: "取消理解要求" }));
 if (action === "instruction") fireEvent.change(screen.getByLabelText("整理要求"), { target: { value: "新要求" } });
 if (action === "mode") await user.click(screen.getByRole("radio", { name: "本地规则（不联网）" }));
 if (action === "source") await user.click(screen.getByRole("checkbox", { name: "本次整理包含当前聊天" }));
 if (action === "raw") act(() => window.dispatchEvent(new Event("minutes-raw-cleared")));
 expect(screen.queryByRole("region", { name: "AI 任务理解预览" })).not.toBeInTheDocument();
 expect(api.cancelAgentPlan).toHaveBeenCalledWith(planPreview.preview_id); expect(api.executeAgentPlan).not.toHaveBeenCalled();
 expect(api.previewGeneration).not.toHaveBeenCalled();
});
it.each(["cancel", "instruction", "raw"])("AI运行中%s立即停后续，迟到结果不能筛选正文", async action => {
 const api = setup(true, false); let resolve!: (value: typeof aiPlan) => void;
 vi.mocked(api.executeAgentPlan).mockReturnValue(new Promise(r => { resolve = r; })); const user = userEvent.setup();
 await user.type(screen.getByLabelText("整理要求"), "整理创业点子"); await user.click(screen.getByRole("button", { name: "理解并整理" }));
 await screen.findByRole("region", { name: "AI 任务理解预览" });
 await user.click(screen.getByRole("checkbox", { name: "授权本次向外部 AI 发送上述整理要求" })); await user.click(screen.getByRole("button", { name: "授权并理解要求" }));
 expect(screen.getByRole("checkbox", { name: "本次整理包含当前聊天" })).toBeDisabled();
 if (action === "cancel") await user.click(screen.getByRole("button", { name: "取消理解要求" }));
 if (action === "instruction") fireEvent.change(screen.getByLabelText("整理要求"), { target: { value: "新要求" } });
 if (action === "raw") act(() => window.dispatchEvent(new Event("minutes-raw-cleared")));
 expect(screen.queryByRole("region", { name: "AI 任务理解预览" })).not.toBeInTheDocument();
 await act(async () => resolve(aiPlan));
 expect(screen.queryByLabelText("检索关键词")).not.toBeInTheDocument(); expect(api.previewGeneration).not.toHaveBeenCalled();
 expect(api.cancelAgentPlan).toHaveBeenCalledWith(planPreview.preview_id);
});
it("已取消的建立预览请求迟到时只清理，不复活任务", async () => {
 const api = setup(true, false); let resolve!: (value: typeof planPreview) => void;
 vi.mocked(api.previewAgentPlan).mockReturnValue(new Promise(r => { resolve = r; })); const user = userEvent.setup();
 await user.type(screen.getByLabelText("整理要求"), "旧要求"); await user.click(screen.getByRole("button", { name: "理解并整理" }));
 await user.click(screen.getByRole("button", { name: "取消理解要求" }));
 await act(async () => resolve(planPreview));
 expect(screen.queryByRole("region", { name: "AI 任务理解预览" })).not.toBeInTheDocument();
 expect(api.cancelAgentPlan).toHaveBeenCalledWith(planPreview.preview_id); expect(api.executeAgentPlan).not.toHaveBeenCalled();
});
it.each([new ApiError(429, "daily_limit_exceeded"), new ApiError(503, "provider_unconfigured"), new ApiError(0)])("AI失败不自动降级；重试必须重新预览并独立授权 %j", async failure => {
 const api = setup(true, false); const user = userEvent.setup(); vi.mocked(api.executeAgentPlan).mockRejectedValue(failure);
 await user.type(screen.getByLabelText("整理要求"), "整理创业点子"); await user.click(screen.getByRole("button", { name: "理解并整理" }));
 await screen.findByRole("region", { name: "AI 任务理解预览" });
 await user.click(screen.getByRole("checkbox", { name: "授权本次向外部 AI 发送上述整理要求" })); await user.click(screen.getByRole("button", { name: "授权并理解要求" }));
 await screen.findByText(/AI 任务理解未完成/); expect(api.planAgent).not.toHaveBeenCalled(); expect(api.previewGeneration).not.toHaveBeenCalled();
 expect(screen.queryByRole("region", { name: "AI 任务理解预览" })).not.toBeInTheDocument();
 await user.click(screen.getByRole("button", { name: "理解并整理" }));
 await screen.findByRole("region", { name: "AI 任务理解预览" }); expect(api.previewAgentPlan).toHaveBeenCalledTimes(2);
 expect(screen.getByRole("button", { name: "授权并理解要求" })).toBeDisabled(); expect(api.executeAgentPlan).toHaveBeenCalledTimes(1);
});

it("要求预览过期时必须重建，不能使用已选授权继续", async () => {
 const api = setup(true, false); const user = userEvent.setup();
 await user.type(screen.getByLabelText("整理要求"), "整理创业点子"); await user.click(screen.getByRole("button", { name: "理解并整理" }));
 await screen.findByRole("region", { name: "AI 任务理解预览" });
 await user.click(screen.getByRole("checkbox", { name: "授权本次向外部 AI 发送上述整理要求" }));
 const date = vi.spyOn(Date, "now").mockReturnValue(Date.parse("2100-01-01T00:00:00Z"));
 try { await user.click(screen.getByRole("button", { name: "授权并理解要求" })); } finally { date.mockRestore(); }
 expect(await screen.findByText(/AI 任务理解预览已过期/)).toBeVisible(); expect(api.executeAgentPlan).not.toHaveBeenCalled();
 expect(api.cancelAgentPlan).toHaveBeenCalledWith(planPreview.preview_id);
});
it.each(["preview", "execute"])("卸载%s中任务清理冻结要求，迟到结果不继续", async stage => {
 const api = setup(true, false); const user = userEvent.setup(); let resolve!: (value: never) => void;
 const pending = new Promise<never>(r => { resolve = r; });
 if (stage === "preview") vi.mocked(api.previewAgentPlan).mockReturnValue(pending);
 else vi.mocked(api.executeAgentPlan).mockReturnValue(pending);
 await user.type(screen.getByLabelText("整理要求"), "整理创业点子"); await user.click(screen.getByRole("button", { name: "理解并整理" }));
 if (stage === "execute") {
  await screen.findByRole("region", { name: "AI 任务理解预览" });
  await user.click(screen.getByRole("checkbox", { name: "授权本次向外部 AI 发送上述整理要求" })); await user.click(screen.getByRole("button", { name: "授权并理解要求" }));
 }
 cleanup(); await act(async () => resolve((stage === "preview" ? planPreview : aiPlan) as never));
 expect(api.cancelAgentPlan).toHaveBeenCalledWith(planPreview.preview_id); expect(api.previewGeneration).not.toHaveBeenCalled();
});
it("来源在AI运行时外部改变使旧响应失效，解除冲突锁后可重新开始", async () => {
 const api = setup(true, false); cleanup(); const lock = vi.fn();
 const view = render(<ImportedChatWorkspace api={api} committed={committed} sourceControls={null} outputRootId="" importBusy={false} onScopeLockChange={lock} />);
 let resolve!: (value: typeof aiPlan) => void; vi.mocked(api.executeAgentPlan).mockReturnValue(new Promise(r => { resolve = r; })); const user = userEvent.setup();
 await user.type(screen.getByLabelText("整理要求"), "整理创业点子"); await user.click(screen.getByRole("button", { name: "理解并整理" }));
 await screen.findByRole("region", { name: "AI 任务理解预览" });
 await user.click(screen.getByRole("checkbox", { name: "授权本次向外部 AI 发送上述整理要求" })); await user.click(screen.getByRole("button", { name: "授权并理解要求" }));
 expect(lock).toHaveBeenLastCalledWith(true);
 view.rerender(<ImportedChatWorkspace api={api} committed={{ ...committed, snapshot_id: "snapshot:new" }} sourceControls={null} outputRootId="" importBusy={false} onScopeLockChange={lock} />);
 await act(async () => resolve(aiPlan));
 expect(api.cancelAgentPlan).toHaveBeenCalledWith(planPreview.preview_id); expect(api.previewGeneration).not.toHaveBeenCalled();
 expect(screen.queryByLabelText("检索关键词")).not.toBeInTheDocument(); expect(lock).toHaveBeenLastCalledWith(false);
 expect(screen.getByRole("button", { name: "理解并整理" })).toBeEnabled();
});
it("结果若不对应已授权脱敏要求则失败，不能把不匹配任务交给正文流程", async () => {
 const api = setup(true, false); const user = userEvent.setup(); vi.mocked(api.executeAgentPlan).mockResolvedValue({ ...aiPlan, instruction: "别的要求" });
 await user.type(screen.getByLabelText("整理要求"), "整理创业点子"); await user.click(screen.getByRole("button", { name: "理解并整理" }));
 await screen.findByRole("region", { name: "AI 任务理解预览" });
 await user.click(screen.getByRole("checkbox", { name: "授权本次向外部 AI 发送上述整理要求" })); await user.click(screen.getByRole("button", { name: "授权并理解要求" }));
 expect(await screen.findByText(/AI 任务理解未完成/)).toBeVisible(); expect(api.previewGeneration).not.toHaveBeenCalled();
 expect(screen.queryByLabelText("检索关键词")).not.toBeInTheDocument();
});
