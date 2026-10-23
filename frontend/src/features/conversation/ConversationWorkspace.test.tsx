import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ConversationWorkspace } from "./ConversationWorkspace";
import type { LocalApiClient } from "../../api/client";
import type { AgentConversation } from "../../conversation-types";
const time = "2026-10-23T00:00:00Z";
const initial = (id = "c1"): AgentConversation => ({ conversation_id: id, title: id === "c1" ? "第一段对话" : "第二段对话", revision: 1, created_at: time, updated_at: time, sources: [], messages: [], active_run: null });
const waiting = (base = initial()): AgentConversation => ({ ...base, revision: 2, messages: [{ message_id: "m1", role: "user", text: "整理重点", created_at: time, run_id: "r1", document_id: null, version_id: null, artifact_revision: null }], active_run: { run_id: "r1", revision: 1, state: "waiting_approval", stage: "agent", error_code: null, approval: { approval_id: "a1", kind: "agent", preview_fingerprint: "a".repeat(64), provider: "本地配置", model: "合成测试模型", outbound_payloads: ["完整脱敏请求正文"], provider_calls: 1, max_output_tokens: 1000, expires_at: "2099-01-01T00:00:00Z" }, document_id: null, version_id: null, artifact_revision: null } });
function apiFixture() {
 let value = initial();
 const api = {
   listAgentConversations: vi.fn(async () => ({ conversations: [initial(), initial("c2")] })),
   createAgentConversation: vi.fn(async () => initial("c3")),
   agentConversation: vi.fn(async (id: string) => id === "c1" ? value : initial(id)),
   setAgentConversationSources: vi.fn(async (_id, sources) => (value = { ...value, revision: value.revision + 1, sources })),
   sendAgentMessage: vi.fn(async () => (value = waiting(value))),
   approveAgentRun: vi.fn(async (): Promise<AgentConversation> => (value = { ...value, revision: 3, active_run: { ...value.active_run!, state: "running", approval: null } })),
   cancelAgentRun: vi.fn(async () => (value = { ...value, revision: 4, active_run: { ...value.active_run!, state: "cancelled", approval: null } })),
   previewImport: vi.fn(async () => ({ preview_id: "p1", preview_fingerprint: "b".repeat(64), ready_to_commit: true, expires_at_utc: "2099-01-01T00:00:00Z", source: { warnings: [] }, analysis: { total_count: 3, parsed_count: 2, unresolved_count: 1, duplicate_count: 0, samples: [{ row_number: 1, text: "合成记录" }], warnings: [], issues: [] } })),
   commitImport: vi.fn(async () => ({ snapshot_id: "s1", imported_count: 1, skipped_count: 2 })),
   previewContentImport: vi.fn(async () => ({ preview_id: "p2", preview_fingerprint: "c".repeat(64), status: "ready", suggested_kind: "note", units: [{ unit_id: "u1", text: "笔记内容", kind: "note_passage", group_id: "g1", sender_name: null, sent_at: null, order: 0, image_ref: null as string | null }], warnings: [], images: [], expires_at_utc: "2099-01-01T00:00:00Z" })),
   reviseContentPreview: vi.fn(async (_id, edits) => ({ preview_id: "p2", preview_fingerprint: "d".repeat(64), status: "ready", suggested_kind: "note", units: edits, warnings: [], images: [], expires_at_utc: "2099-01-01T00:00:00Z" })),
   commitContentPreview: vi.fn(async () => ({ batch_id: "b1", unit_count: 1 })),
   documentTextPair: vi.fn(async () => ({ document_id: "d1", version_id: "v1", body: { filename: "整理内容.txt", text: "正文预览" }, reference: { filename: "核查参考.txt", text: "原文参考" } })),
 };
 return { api: api as unknown as LocalApiClient, mocks: api, set: (next: AgentConversation) => { value = next; } };
}
afterEach(() => vi.restoreAllMocks());
async function start(api: LocalApiClient) { render(<ConversationWorkspace api={api} />); await screen.findByRole("button", { name: "第一段对话" }); await waitFor(() => expect(screen.getByRole("textbox", { name: "发送消息" })).toBeEnabled()); }
describe("ConversationWorkspace", () => {
 it("shows a continuous conversation and sends natural language without a form", async () => {
  const { api, mocks } = apiFixture(); await start(api);
  expect(screen.queryByText("选择整理方向")).not.toBeInTheDocument();
  fireEvent.change(screen.getByRole("textbox", { name: "发送消息" }), { target: { value: "整理重点" } }); fireEvent.click(screen.getByRole("button", { name: "发送" }));
  expect(await screen.findByText("完整脱敏请求正文")).toBeVisible(); expect(mocks.approveAgentRun).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: "授权并继续" })).toBeDisabled();
  fireEvent.click(screen.getByRole("checkbox", { name: "我已核对并授权本次外发" })); fireEvent.click(screen.getByRole("button", { name: "授权并继续" }));
  await screen.findByRole("button", { name: "停止" });
  expect(mocks.sendAgentMessage).toHaveBeenCalledWith("c1", "整理重点", expect.any(String));
 });
 it("requires separate acceptance of skipped records before attaching a source", async () => {
  const { api, mocks } = apiFixture(); await start(api);
  fireEvent.change(screen.getByLabelText("添加资料"), { target: { files: [new File(["synthetic"], "资料.txt")] } });
  const card = await screen.findByRole("region", { name: "导入确认" });
  fireEvent.click(await within(card).findByRole("checkbox", { name: "我已核对预览并确认导入" }));
  expect(within(card).getByRole("button", { name: "确认导入" })).toBeDisabled();
  fireEvent.change(screen.getByRole("textbox", { name: "发送消息" }), { target: { value: "整理重点" } });
  expect(screen.getByRole("button", { name: "发送" })).toBeDisabled();
  fireEvent.click(within(card).getByRole("checkbox", { name: /接受跳过/ })); fireEvent.click(within(card).getByRole("button", { name: "确认导入" }));
  await screen.findByText(/实际导入 1 条，跳过 2 条/);
  expect(mocks.commitImport).toHaveBeenCalledWith("p1", "b".repeat(64), true);
  expect(mocks.setAgentConversationSources).toHaveBeenCalledWith("c1", [{ kind: "chat_snapshot", id: "s1", label: "资料.txt" }]);
 });
 it("recovers exact artifact versions and two TXT files while retaining follow-up sources", async () => {
  const fixture = apiFixture(); const c = initial(); c.sources = [{ kind: "content_batch", id: "b1", label: "笔记" }];
  c.messages = [{ message_id: "result", role: "assistant", text: "已完成整理", created_at: time, run_id: "r1", document_id: "d1", version_id: "v1", artifact_revision: 1 }]; fixture.set(c);
  vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: vi.fn(() => "blob:synthetic"), revokeObjectURL: vi.fn() }));
  await start(fixture.api);
  await screen.findByRole("link", { name: "下载整理内容.txt" }); expect(screen.getByRole("link", { name: "下载核查参考.txt" })).toHaveAttribute("download", "核查参考.txt");
  expect(fixture.mocks.documentTextPair).toHaveBeenCalledWith("d1", "v1");
  fireEvent.change(screen.getByRole("textbox", { name: "发送消息" }), { target: { value: "再精炼一些" } }); fireEvent.click(screen.getByRole("button", { name: "发送" }));
  await screen.findByRole("region", { name: "外发授权" }); expect(screen.getByText("笔记")).toBeInTheDocument();
 });
 it("cancels the old server run on switch and ignores its late response", async () => {
  const fixture = apiFixture(); fixture.set(waiting()); await start(fixture.api);
  let resolve!: (v: AgentConversation) => void; fixture.mocks.approveAgentRun.mockImplementationOnce(() => new Promise(r => { resolve = r; }));
  fireEvent.click(await screen.findByRole("checkbox", { name: "我已核对并授权本次外发" })); fireEvent.click(screen.getByRole("button", { name: "授权并继续" }));
  fireEvent.click(screen.getByRole("button", { name: "第二段对话" })); await waitFor(() => expect(screen.getByRole("button", { name: "第二段对话" })).toHaveAttribute("aria-current", "page"));
  await act(async () => resolve({ ...waiting(), title: "过时会话" }));
  expect(fixture.mocks.cancelAgentRun).toHaveBeenCalledWith("c1", "r1"); expect(screen.queryByText("完整脱敏请求正文")).not.toBeInTheDocument();
 });
 it("polls a running task and asks again for the next distinct authorization", async () => {
  const fixture = apiFixture(); fixture.set(waiting()); await start(fixture.api);
  fireEvent.click(await screen.findByRole("checkbox", { name: "我已核对并授权本次外发" })); fireEvent.click(screen.getByRole("button", { name: "授权并继续" }));
  await screen.findByText("正在处理本次任务…");
  const second = waiting(); second.revision = 4; second.active_run!.approval = { ...second.active_run!.approval!, approval_id: "a2", outbound_payloads: ["第二次完整请求"] }; fixture.set(second);
  await screen.findByText("第二次完整请求", {}, { timeout: 2500 });
  expect(screen.getByRole("checkbox", { name: "我已核对并授权本次外发" })).not.toBeChecked(); expect(fixture.mocks.approveAgentRun).toHaveBeenCalledTimes(1);
 });
 it("clears pending raw previews and ignores a parsing response after retention cleanup", async () => {
  const fixture = apiFixture(); await start(fixture.api);
  let finish!: (value: unknown) => void;
  const original = await fixture.mocks.previewImport();
  fixture.mocks.previewImport.mockImplementationOnce(() => new Promise(resolve => { finish = resolve as typeof finish; }));
  fireEvent.change(screen.getByLabelText("添加资料"), { target: { files: [new File(["synthetic"], "将清理.txt")] } });
  await waitFor(() => expect(fixture.mocks.previewImport).toHaveBeenCalledTimes(2));
  act(() => window.dispatchEvent(new Event("minutes-raw-cleared")));
  await act(async () => finish(original));
  expect(screen.queryByRole("region", { name: "导入确认" })).not.toBeInTheDocument(); expect(screen.queryByText("合成记录")).not.toBeInTheDocument();
 });
 it("creates a clean new conversation after cancelling the old server task", async () => {
  const fixture = apiFixture(); const c = waiting(); c.sources = [{ kind: "content_batch", id: "b1", label: "旧来源" }]; fixture.set(c); await start(fixture.api);
  fireEvent.click(screen.getByRole("button", { name: "新建对话" }));
  await waitFor(() => expect(fixture.mocks.createAgentConversation).toHaveBeenCalled());
  expect(fixture.mocks.cancelAgentRun).toHaveBeenCalledWith("c1", "r1");
  await waitFor(() => expect(screen.getByRole("textbox", { name: "发送消息" })).toBeEnabled());
  expect(screen.queryByText("旧来源")).not.toBeInTheDocument(); expect(screen.queryByRole("region", { name: "外发授权" })).not.toBeInTheDocument();
 });
 it("stops a pending authorization and does not restore a late approval response", async () => {
  const fixture = apiFixture(); fixture.set(waiting()); await start(fixture.api);
  let finish!: (v: AgentConversation) => void; fixture.mocks.approveAgentRun.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  fireEvent.click(await screen.findByRole("checkbox", { name: "我已核对并授权本次外发" })); fireEvent.click(screen.getByRole("button", { name: "授权并继续" })); fireEvent.click(screen.getByRole("button", { name: "停止" }));
  await screen.findByText("任务已停止"); await act(async () => finish(waiting()));
  expect(screen.queryByRole("region", { name: "外发授权" })).not.toBeInTheDocument();
 });
 it("previews pasted notes locally and prevents sending while the note remains pending", async () => {
  const fixture = apiFixture(); await start(fixture.api);
  fireEvent.click(screen.getByRole("button", { name: "粘贴笔记" })); fireEvent.change(screen.getByRole("textbox", { name: "笔记正文" }), { target: { value: "笔记内容" } });
  fireEvent.change(screen.getByRole("textbox", { name: "发送消息" }), { target: { value: "整理" } });
  expect(screen.getByRole("button", { name: "发送" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "预览笔记" }));
  const card = await screen.findByRole("region", { name: "导入确认" }); fireEvent.click(await within(card).findByRole("checkbox", { name: "我已核对预览并确认导入" })); fireEvent.click(within(card).getByRole("button", { name: "确认导入" }));
  await screen.findByText(/实际导入 1 条/); expect(fixture.mocks.setAgentConversationSources).toHaveBeenCalledWith("c1", [{ kind: "content_batch", id: "b1", label: "粘贴笔记" }]);
 });
 it("keeps two attachment previews independent and replaces the full source list after each confirmation", async () => {
  const fixture = apiFixture(); await start(fixture.api);
  fireEvent.change(screen.getByLabelText("添加资料"), { target: { files: [new File(["synthetic"], "聊天.txt"), new File(["synthetic"], "截图.png", { type: "image/png" })] } });
  await waitFor(() => expect(screen.getAllByRole("checkbox", { name: "我已核对预览并确认导入" })).toHaveLength(2));
  const cards = screen.getAllByRole("region", { name: "导入确认" });
  fireEvent.click(within(cards[0]).getByRole("checkbox", { name: "我已核对预览并确认导入" })); fireEvent.click(within(cards[0]).getByRole("checkbox", { name: /接受跳过/ })); fireEvent.click(within(cards[0]).getByRole("button", { name: "确认导入" })); await screen.findByText(/实际导入 1 条，跳过 2 条/);
  fireEvent.click(within(cards[1]).getByRole("checkbox", { name: "我已核对预览并确认导入" })); fireEvent.click(within(cards[1]).getByRole("button", { name: "确认导入" }));
  await waitFor(() => expect(fixture.mocks.setAgentConversationSources).toHaveBeenLastCalledWith("c1", [{ kind: "chat_snapshot", id: "s1", label: "聊天.txt" }, { kind: "content_batch", id: "b1", label: "截图.png" }]));
 });
 it("does not associate a late import commit after switching to another conversation", async () => {
  const fixture = apiFixture(); await start(fixture.api);
  let finish!: (value: { snapshot_id: string; imported_count: number; skipped_count: number }) => void;
  fixture.mocks.commitImport.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  fireEvent.change(screen.getByLabelText("添加资料"), { target: { files: [new File(["synthetic"], "聊天.txt")] } });
  const card = await screen.findByRole("region", { name: "导入确认" }); fireEvent.click(await within(card).findByRole("checkbox", { name: "我已核对预览并确认导入" })); fireEvent.click(within(card).getByRole("checkbox", { name: /接受跳过/ })); fireEvent.click(within(card).getByRole("button", { name: "确认导入" }));
  await waitFor(() => expect(fixture.mocks.commitImport).toHaveBeenCalled()); fireEvent.click(screen.getByRole("button", { name: "第二段对话" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "第二段对话" })).toHaveAttribute("aria-current", "page")); await act(async () => finish({ snapshot_id: "late", imported_count: 2, skipped_count: 1 }));
  expect(fixture.mocks.setAgentConversationSources).not.toHaveBeenCalled();
 });

 it("retries association without importing the same attachment twice", async () => {
  const fixture = apiFixture(); fixture.mocks.setAgentConversationSources.mockRejectedValueOnce(new Error("temporary")); await start(fixture.api);
  fireEvent.change(screen.getByLabelText("添加资料"), { target: { files: [new File(["synthetic"], "资料.txt")] } });
  const card = await screen.findByRole("region", { name: "导入确认" }); fireEvent.click(await within(card).findByRole("checkbox", { name: "我已核对预览并确认导入" })); fireEvent.click(within(card).getByRole("checkbox", { name: /接受跳过/ })); fireEvent.click(within(card).getByRole("button", { name: "确认导入" }));
  await screen.findByRole("alert"); fireEvent.click(within(card).getByRole("button", { name: "确认导入" })); await screen.findByText(/实际导入 1 条，跳过 2 条/);
  expect(fixture.mocks.commitImport).toHaveBeenCalledTimes(1);
 });
 it("offers a separate explicit note preview for TXT with no parsed chat messages", async () => {
  const fixture = apiFixture(); const preview = await fixture.mocks.previewImport(); preview.analysis.parsed_count = 0; preview.ready_to_commit = false; fixture.mocks.previewImport.mockResolvedValue(preview); await start(fixture.api);
  fireEvent.change(screen.getByLabelText("添加资料"), { target: { files: [new File(["普通笔记正文"], "资料.txt")] } });
  fireEvent.click(await screen.findByRole("button", { name: "改为笔记预览" }));
  await screen.findByText("笔记内容"); expect(fixture.mocks.previewContentImport).toHaveBeenCalledWith({ pasted_note: "普通笔记正文" }); expect(fixture.mocks.commitContentPreview).not.toHaveBeenCalled();
 });

 it("requires local correction of uncertain OCR before confirming an image import", async () => {
  const fixture = apiFixture(); const preview = await fixture.mocks.previewContentImport(); fixture.mocks.previewContentImport.mockResolvedValue({ ...preview, status: "boundary_uncertain", units: [{ unit_id: "u1", text: "笔记内容", kind: "note_passage", group_id: "g1", sender_name: null, sent_at: null, order: 0, image_ref: "i1" }] } as typeof preview); await start(fixture.api);
  fireEvent.change(screen.getByLabelText("添加资料"), { target: { files: [new File(["synthetic"], "截图.png")] } });
  const card = await screen.findByRole("region", { name: "导入确认" }); await within(card).findByText(/正文与界面边界不确定/);
  expect(within(card).getByRole("button", { name: "确认导入" })).toBeDisabled();
  fireEvent.change(await within(card).findByRole("textbox", { name: "校对正文 1" }), { target: { value: "校对后的正文" } }); fireEvent.click(within(card).getByRole("button", { name: "保存校对" }));
  await waitFor(() => expect(within(card).queryByText(/正文与界面边界不确定/)).not.toBeInTheDocument());
  fireEvent.click(within(card).getByRole("checkbox", { name: "我已核对预览并确认导入" })); expect(within(card).getByRole("button", { name: "确认导入" })).toBeEnabled();
 });

 it("explains a message over 512 characters instead of truncating or sending it", async () => {
  const fixture = apiFixture(); await start(fixture.api); const long = "完整要求".repeat(129);
  fireEvent.change(screen.getByRole("textbox", { name: "发送消息" }), { target: { value: long } }); fireEvent.click(screen.getByRole("button", { name: "发送" }));
  await screen.findByText(/本次要求超过512字/); expect(fixture.mocks.sendAgentMessage).not.toHaveBeenCalled(); expect(screen.getByRole("textbox", { name: "发送消息" })).toHaveValue(long);
 });

 it("cancels a newly created server run when message submission finishes after switching", async () => {
  const fixture = apiFixture(); await start(fixture.api); let finish!: (value: AgentConversation) => void;
  fixture.mocks.sendAgentMessage.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  fireEvent.change(screen.getByRole("textbox", { name: "发送消息" }), { target: { value: "整理重点" } }); fireEvent.click(screen.getByRole("button", { name: "发送" }));
  fireEvent.click(screen.getByRole("button", { name: "第二段对话" })); await waitFor(() => expect(screen.getByRole("button", { name: "第二段对话" })).toHaveAttribute("aria-current", "page"));
  await act(async () => finish(waiting())); await waitFor(() => expect(fixture.mocks.cancelAgentRun).toHaveBeenCalledWith("c1", "r1"));
  expect(screen.queryByRole("region", { name: "外发授权" })).not.toBeInTheDocument();
 });

});
