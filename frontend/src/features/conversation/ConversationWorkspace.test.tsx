import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ConversationWorkspace } from "./ConversationWorkspace";
import type { LocalApiClient } from "../../api/client";
import type { AgentConversation, AgentSource } from "../../conversation-types";
import type { ContentPreview, ContentUnitEdit, ImportPreview } from "../../types";
const time = "2026-10-23T00:00:00Z";
const initial = (id = "c1"): AgentConversation => ({ conversation_id: id, title: id === "c1" ? "第一段对话" : "第二段对话", revision: 1, created_at: time, updated_at: time, sources: [], messages: [], active_run: null });
function running(base = initial(), text = "整理重点"): AgentConversation {
 return { ...base, revision: base.revision + 1, messages: [...base.messages, { message_id: `m${base.messages.length + 1}`, role: "user", text, created_at: time, run_id: "r1", document_id: null, version_id: null, artifact_revision: null }], active_run: { run_id: "r1", revision: 1, state: "running", stage: "agent_running", error_code: null, approval: null, document_id: null, version_id: null, artifact_revision: null } };
}
function waiting(): AgentConversation {
 const value = running(); value.active_run = { ...value.active_run!, state: "waiting_approval", approval: { approval_id: "a1", kind: "agent", preview_fingerprint: "a".repeat(64), provider: "本地配置", model: "合成测试模型", outbound_payloads: ["旧任务完整脱敏请求正文"], provider_calls: 1, max_output_tokens: 1000, expires_at: "2099-01-01T00:00:00Z" } }; return value;
}
function chatPreview(): ImportPreview {
 return { preview_id: "p1", preview_fingerprint: "b".repeat(64), ready_to_commit: true, expires_at_utc: "2099-01-01T00:00:00Z", source: { format: "txt", encoding: "utf-8", container: "plain", member_name: null, row_count: 3, columns: [], sha256: "a".repeat(64), byte_size: 10, warnings: [] }, applied_mapping: {}, analysis: { total_count: 3, parsed_count: 2, unresolved_count: 1, duplicate_count: 0, sender_count: 2, time_start: null, time_end: null, message_types: { text: 2, image: 0, file: 0, other: 0, unknown: 0 }, conversations: [], samples: [{ row_number: 1, conversation_name: null, sender_name: null, timestamp: null, message_type: "text", text: "合成记录" }], warnings: [], issues: [] } };
}
function contentPreview(): ContentPreview {
 return { preview_id: "p2", preview_fingerprint: "c".repeat(64), status: "ready", suggested_kind: "note", units: [{ unit_id: "u1", text: "笔记内容", kind: "note_passage", group_id: "g1", sender_name: null, sent_at: null, order: 0, image_ref: null }], warnings: [], images: [], expires_at_utc: "2099-01-01T00:00:00Z" };
}
function apiFixture() {
 let value = initial();
 const api = {
  listAgentConversations: vi.fn(async () => ({ conversations: [initial(), initial("c2")] })),
  createAgentConversation: vi.fn(async () => initial("c3")),
  agentConversation: vi.fn(async (id: string): Promise<AgentConversation> => id === "c1" ? value : initial(id)),
  setAgentConversationSources: vi.fn(async (_id: string, sources: AgentSource[]): Promise<AgentConversation> => (value = { ...value, revision: value.revision + 1, sources })),
  sendAgentMessage: vi.fn(async (_id: string, text: string, _client: string, _automatic?: boolean): Promise<AgentConversation> => (value = running(value, text))),
  approveAgentRun: vi.fn(async (): Promise<AgentConversation> => (value = { ...value, revision: value.revision + 1, active_run: { ...value.active_run!, state: "running", approval: null } })),
  cancelAgentRun: vi.fn(async (): Promise<AgentConversation> => (value = { ...value, revision: value.revision + 1, active_run: { ...value.active_run!, state: "cancelled", approval: null } })),
  previewImport: vi.fn(async (): Promise<ImportPreview> => chatPreview()),
  commitImport: vi.fn(async () => ({ snapshot_id: "s1", imported_count: 1, skipped_count: 2 })),
  previewContentImport: vi.fn(async (): Promise<ContentPreview> => contentPreview()),
  reviseContentPreview: vi.fn(async (_id: string, edits: ContentUnitEdit[]): Promise<ContentPreview> => ({ ...contentPreview(), preview_fingerprint: "d".repeat(64), units: edits.map(unit => ({ ...unit, image_ref: null })) })),
  commitContentPreview: vi.fn(async () => ({ batch_id: "b1", unit_count: 1 })),
  documentTextPair: vi.fn(async (id: string, version: string) => ({ document_id: id, version_id: version, body: { filename: "整理内容.txt", text: "最终整理正文" }, reference: { filename: "核查参考.txt", text: "原文参考" } })),
 };
 return { api: api as unknown as LocalApiClient, mocks: api, set: (next: AgentConversation) => { value = next; } };
}
afterEach(() => vi.restoreAllMocks());
async function start(api: LocalApiClient) { const view = render(<ConversationWorkspace api={api} />); await screen.findByRole("button", { name: "第一段对话" }); await waitFor(() => expect(screen.getByRole("textbox", { name: "发送消息" })).toBeEnabled()); return view; }
function upload(name = "资料.txt") { fireEvent.change(screen.getByLabelText("添加资料"), { target: { files: [new File(["普通笔记正文"], name)] } }); }
function send(text: string) { fireEvent.change(screen.getByRole("textbox", { name: "发送消息" }), { target: { value: text } }); fireEvent.click(screen.getByRole("button", { name: "发送" })); }
async function switchToSecond() { fireEvent.click(screen.getByRole("button", { name: "第二段对话" })); await waitFor(() => expect(screen.getByRole("button", { name: "第二段对话" })).toHaveAttribute("aria-current", "page")); }
describe("ConversationWorkspace automatic chat", () => {
 it("sends a natural-language task in automatic mode without author labels or approval controls", async () => {
  const fixture = apiFixture(); const view = await start(fixture.api); send("整理重点"); await screen.findByRole("button", { name: "停止" });
  expect(fixture.mocks.sendAgentMessage).toHaveBeenCalledWith("c1", "整理重点", expect.any(String), true);
  expect(screen.queryByRole("region", { name: "外发授权" })).not.toBeInTheDocument(); expect(fixture.mocks.approveAgentRun).not.toHaveBeenCalled();
  expect(view.container.querySelector(".chat-avatar, .chat-message-author")).toBeNull();
 });
 it("automatically imports valid chat rows and attaches a filename chip without a confirmation or receipt card", async () => {
  const fixture = apiFixture(); await start(fixture.api); upload();
  const sources = await screen.findByRole("region", { name: "本会话资料" }); expect(within(sources).getByText("资料.txt")).toBeVisible();
  expect(fixture.mocks.commitImport).toHaveBeenCalledWith("p1", "b".repeat(64), true);
  expect(fixture.mocks.setAgentConversationSources).toHaveBeenCalledWith("c1", [{ kind: "chat_snapshot", id: "s1", label: "资料.txt" }]);
  expect(screen.queryByRole("region", { name: "导入确认" })).not.toBeInTheDocument(); expect(screen.queryByRole("region", { name: "已导入资料" })).not.toBeInTheDocument(); expect(screen.queryByText(/实际导入/)).not.toBeInTheDocument();
 });
 it("automatically treats zero-message TXT as a note and never commits an empty chat import", async () => {
  const fixture = apiFixture(); const preview = chatPreview(); preview.analysis.parsed_count = 0; preview.ready_to_commit = false; fixture.mocks.previewImport.mockResolvedValue(preview); await start(fixture.api); upload();
  await screen.findByRole("region", { name: "本会话资料" });
  expect(fixture.mocks.previewContentImport).toHaveBeenCalledWith({ pasted_note: "普通笔记正文", retain_pending: true }); expect(fixture.mocks.commitImport).not.toHaveBeenCalled();
  expect(fixture.mocks.commitContentPreview).toHaveBeenCalledWith("p2", "c".repeat(64)); expect(fixture.mocks.setAgentConversationSources).toHaveBeenCalledWith("c1", [{ kind: "content_batch", id: "b1", label: "资料.txt" }]);
 });
 it("automatically imports ready image OCR as a conversation source", async () => {
  const fixture = apiFixture(); await start(fixture.api); upload("截图.png"); await screen.findByRole("region", { name: "本会话资料" });
  expect(fixture.mocks.previewContentImport).toHaveBeenCalledWith(expect.objectContaining({ images: [expect.objectContaining({ filename: "截图.png", input_order: 0 })], retain_pending: true }));
  expect(fixture.mocks.commitContentPreview).toHaveBeenCalledWith("p2", "c".repeat(64)); expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
 });
 it("requires correction for uncertain OCR and saves then imports in one action", async () => {
  const fixture = apiFixture(); fixture.mocks.previewContentImport.mockResolvedValue({ ...contentPreview(), status: "boundary_uncertain" }); await start(fixture.api); upload("截图.png");
  const editor = await screen.findByRole("textbox", { name: "校对正文 1" }); expect(fixture.mocks.commitContentPreview).not.toHaveBeenCalled();
  expect(screen.queryByRole("checkbox")).not.toBeInTheDocument(); fireEvent.change(editor, { target: { value: "校对后的正文" } }); fireEvent.click(screen.getByRole("button", { name: "保存校对并导入" }));
  await screen.findByRole("region", { name: "本会话资料" }); expect(fixture.mocks.reviseContentPreview).toHaveBeenCalledWith("p2", [expect.objectContaining({ text: "校对后的正文" })]); expect(fixture.mocks.commitContentPreview).toHaveBeenCalledWith("p2", "d".repeat(64));
 });
 it("retries only conversation association after an import was already committed", async () => {
  const fixture = apiFixture(); fixture.mocks.setAgentConversationSources.mockRejectedValueOnce(new Error("temporary")); await start(fixture.api); upload();
  fireEvent.click(await screen.findByRole("button", { name: "重试添加" })); await screen.findByRole("region", { name: "本会话资料" }); expect(fixture.mocks.commitImport).toHaveBeenCalledTimes(1); expect(fixture.mocks.setAgentConversationSources).toHaveBeenCalledTimes(2);
 });
 it("automatically imports pasted chats while preserving the user's separate task draft", async () => {
  const fixture = apiFixture(); await start(fixture.api); const composer = screen.getByRole("textbox", { name: "发送消息" }); fireEvent.change(composer, { target: { value: "整理讨论中的重点" } });
  const pasted = "甲：\n先验证创业需求\n\n乙：\n再研究商业模式"; fireEvent.paste(composer, { clipboardData: { getData: () => pasted } }); await screen.findByRole("region", { name: "本会话资料" });
  expect(fixture.mocks.previewImport).toHaveBeenCalledWith(expect.objectContaining({ origin: "paste", pasted_text: pasted, retain_pending: true })); expect(fixture.mocks.sendAgentMessage).not.toHaveBeenCalled(); expect(composer).toHaveValue("整理讨论中的重点");
 });
 it("routes a long chat already entered into the composer to local import before applying the task limit", async () => {
  const fixture = apiFixture(); await start(fixture.api); const text = `甲：\n${"完整聊天正文".repeat(110)}\n乙：\n后续讨论`; send(text);
  await screen.findByRole("region", { name: "本会话资料" }); expect(fixture.mocks.sendAgentMessage).not.toHaveBeenCalled(); expect(screen.queryByText(/本次要求超过512字/)).not.toBeInTheDocument(); expect(screen.getByRole("textbox", { name: "发送消息" })).toHaveValue("");
 });
 it("does not classify ordinary multiline requirements as source chat", async () => {
  const fixture = apiFixture(); await start(fixture.api); const composer = screen.getByRole("textbox", { name: "发送消息" }); const text = "整理要求：归纳观点\n输出格式：简洁列表";
  expect(fireEvent.paste(composer, { clipboardData: { getData: () => text } })).toBe(true); send(text); await screen.findByRole("button", { name: "停止" });
  expect(fixture.mocks.sendAgentMessage).toHaveBeenCalledWith("c1", text, expect.any(String), true); expect(fixture.mocks.previewImport).not.toHaveBeenCalled();
 });
 it("keeps ordinary requirements over 512 characters intact and asks for simplification", async () => {
  const fixture = apiFixture(); await start(fixture.api); const long = "完整要求".repeat(129); send(long); await screen.findByText(/本次要求超过512字/);
  expect(fixture.mocks.sendAgentMessage).not.toHaveBeenCalled(); expect(screen.getByRole("textbox", { name: "发送消息" })).toHaveValue(long);
 });
 it("serially imports multiple attachments and replaces the complete authoritative source list", async () => {
  const fixture = apiFixture(); await start(fixture.api); fireEvent.change(screen.getByLabelText("添加资料"), { target: { files: [new File(["synthetic"], "聊天.txt"), new File(["synthetic"], "截图.png")] } });
  await waitFor(() => expect(fixture.mocks.setAgentConversationSources).toHaveBeenLastCalledWith("c1", [{ kind: "chat_snapshot", id: "s1", label: "聊天.txt" }, { kind: "content_batch", id: "b1", label: "截图.png" }]));
  const sources = screen.getByRole("region", { name: "本会话资料" }); expect(within(sources).getByText("聊天.txt")).toBeVisible(); expect(within(sources).getByText("截图.png")).toBeVisible();
 });
 it("never commits a late parser response after switching conversations", async () => {
  const fixture = apiFixture(); let finish!: (value: ImportPreview) => void; fixture.mocks.previewImport.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; })); await start(fixture.api); upload(); await waitFor(() => expect(fixture.mocks.previewImport).toHaveBeenCalled());
  await switchToSecond(); await act(async () => finish(chatPreview())); expect(fixture.mocks.commitImport).not.toHaveBeenCalled(); expect(fixture.mocks.setAgentConversationSources).not.toHaveBeenCalled(); expect(screen.queryByText("合成记录")).not.toBeInTheDocument();
 });
 it("never associates a late import commit with a switched conversation", async () => {
  const fixture = apiFixture(); let finish!: (value: { snapshot_id: string; imported_count: number; skipped_count: number }) => void; fixture.mocks.commitImport.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; })); await start(fixture.api); upload(); await waitFor(() => expect(fixture.mocks.commitImport).toHaveBeenCalled());
  await switchToSecond(); await act(async () => finish({ snapshot_id: "late", imported_count: 1, skipped_count: 2 })); expect(fixture.mocks.setAgentConversationSources).not.toHaveBeenCalled();
 });
 it("cancels a newly created run when its message response arrives after switching", async () => {
  const fixture = apiFixture(); let finish!: (value: AgentConversation) => void; fixture.mocks.sendAgentMessage.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; })); await start(fixture.api); send("整理重点"); await switchToSecond(); await act(async () => finish(running()));
  await waitFor(() => expect(fixture.mocks.cancelAgentRun).toHaveBeenCalledWith("c1", "r1")); expect(screen.queryByRole("button", { name: "停止" })).not.toBeInTheDocument();
 });
 it("clears source context and pending raw previews on retention cleanup", async () => {
  const fixture = apiFixture(); const c = initial(); c.sources = [{ kind: "content_batch", id: "old", label: "已到期资料" }]; fixture.set(c); let finish!: (value: ImportPreview) => void; fixture.mocks.previewImport.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; })); await start(fixture.api); upload(); await waitFor(() => expect(fixture.mocks.previewImport).toHaveBeenCalled());
  fixture.set(initial()); act(() => window.dispatchEvent(new Event("minutes-raw-cleared"))); await act(async () => finish(chatPreview())); await waitFor(() => expect(screen.queryByText("已到期资料")).not.toBeInTheDocument()); expect(fixture.mocks.commitImport).not.toHaveBeenCalled(); expect(screen.queryByText("合成记录")).not.toBeInTheDocument();
 });
 it("polls to a final artifact, hides completion boilerplate, and reuses sources for a follow-up", async () => {
  const fixture = apiFixture(); const c = initial(); c.sources = [{ kind: "content_batch", id: "b1", label: "笔记" }]; fixture.set(running(c)); await start(fixture.api);
  const completed = running(c); completed.revision = 5; completed.active_run = { ...completed.active_run!, state: "complete" }; completed.messages.push({ message_id: "result", role: "assistant", text: "已完成整理，请下载这两份成果", created_at: time, run_id: "r1", document_id: "d1", version_id: "v1", artifact_revision: 1 }); fixture.set(completed);
  await screen.findByText("最终整理正文", {}, { timeout: 2500 }); expect(screen.queryByText("已完成整理，请下载这两份成果")).not.toBeInTheDocument(); expect(screen.queryByText("本次整理已完成")).not.toBeInTheDocument();
  expect(fixture.mocks.documentTextPair).toHaveBeenCalledWith("d1", "v1"); expect(screen.getByRole("link", { name: "下载核查参考.txt" })).toBeVisible(); send("再精炼一些"); await screen.findByRole("button", { name: "停止" });
  expect(fixture.mocks.sendAgentMessage).toHaveBeenCalledWith("c1", "再精炼一些", expect.any(String), true); expect(screen.getByText("笔记")).toBeVisible(); expect(screen.getByRole("link", { name: "下载整理内容.txt" })).toBeVisible(); expect(fixture.mocks.approveAgentRun).not.toHaveBeenCalled();
 });
 it("does not auto-approve recovered legacy manual work and allows stopping before a clean new session", async () => {
  const fixture = apiFixture(); const legacy = waiting(); legacy.sources = [{ kind: "content_batch", id: "old", label: "旧来源" }]; fixture.set(legacy); await start(fixture.api);
  await screen.findByText(/上次任务已暂停/); expect(screen.queryByRole("region", { name: "外发授权" })).not.toBeInTheDocument(); expect(screen.queryByText("旧任务完整脱敏请求正文")).not.toBeInTheDocument(); expect(fixture.mocks.approveAgentRun).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "停止" })); await waitFor(() => expect(fixture.mocks.cancelAgentRun).toHaveBeenCalledWith("c1", "r1")); fireEvent.click(screen.getByRole("button", { name: "新建对话" })); await waitFor(() => expect(screen.getByRole("textbox", { name: "发送消息" })).toBeEnabled()); expect(screen.queryByText("旧来源")).not.toBeInTheDocument();
 });
});
