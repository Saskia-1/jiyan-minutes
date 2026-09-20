import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import type { LocalApiClient } from "../../api/client";
import type { Conversation } from "../../types";

const conversations: Conversation[] = [
  { conversation_selection_id: "opaque-a", display_name: "合成会话 A", account_id: "account-a", conversation_type: "group" },
  { conversation_selection_id: "opaque-b", display_name: "合成会话 B", account_id: "account-a", conversation_type: "group" },
];
const job = {
  job_id: "job:p1", state: "complete" as const, batch_id: "batch:p1", snapshot_id: "snapshot:p1",
  scope_fingerprint: "a".repeat(64), known_total: 2, completed_count: 2, fetched_count: 2,
  saved_count: 2, duplicate_count: 0, unknown_time_count: 0, parse_error_count: 0,
  manifest_sha256: "b".repeat(64), completeness: "source_range_verified" as const,
  error_code: null, searchable: true, cancellation_requested: false, progress_percent: 100, revision: 7,
};
const firstPage = {
  results: [{
    message_id: `message:v1:${"c".repeat(64)}`, conversation_id: "conversation-a",
    timestamp: "2026-09-01T00:00:01Z", sender_id: "sender-a", sender_name: "合成发送者",
    text: "第一条合成结果", batch_id: "batch:p1", snapshot_id: "snapshot:p1",
  }],
  next_cursor: "opaque-cursor",
};
const generationPreview = {
  preview_id: "preview:p2", scope_fingerprint: "a".repeat(64), provider: "fake", model: "deterministic",
  mode: "complete" as const, message_count: 0, character_count: 0, estimated_input_tokens: 0,
  max_output_tokens: 2000, provider_calls: 0, price_status: "unknown" as const, estimated_cost: null,
  cache_hit: false, will_send_external: false, outbound_payloads: [], expires_at: "2099-01-01T00:00:00Z",
};

function api(overrides: Record<string, unknown> = {}) {
  return {
    issueExportConsent: vi.fn().mockResolvedValue({ consent_id: "consent:p1", scope_fingerprint: "a".repeat(64), expires_at_utc: "2099-01-01T00:00:00Z" }),
    startExport: vi.fn().mockResolvedValue({ ...job, state: "pending_authorization", searchable: false }),
    exportJob: vi.fn().mockResolvedValue(job),
    cancelExport: vi.fn().mockResolvedValue({ ...job, state: "cancelled", searchable: false }),
    searchChats: vi.fn().mockResolvedValue(firstPage),
    previewGeneration: vi.fn().mockResolvedValue(generationPreview),
    ...overrides,
  } as unknown as LocalApiClient;
}

const props = {
  sourceId: "qq" as const,
  account: "account-a",
  conversations,
  rangeStart: "2026-09-01T00:00:00Z",
  rangeEnd: "2026-09-02T00:00:00Z",
  timezone: "Asia/Shanghai",
  outputRootId: "fixture-root",
  disabled: false,
  secretConfigured: false,
  secretGrantReady: true,
  onSecretGrantConsumed: vi.fn(),
};

it("会话选择达到后端上限后要求分批且不允许第 51 个", async () => {
  const { ChatSearchPanel } = await import("./ChatSearchPanel");
  const many = Array.from({ length: 51 }, (_, index) => ({
    conversation_selection_id: `opaque-${index}`,
    display_name: `合成会话 ${index + 1}`,
    account_id: "account-a",
    conversation_type: "group" as const,
  }));
  const user = userEvent.setup();
  render(<ChatSearchPanel {...props} sourceId="manual" conversations={many} api={api()} hideExport />);

  for (const checkbox of screen.getAllByRole("checkbox", { name: /^选择 合成会话/ }).slice(0, 50)) {
    await user.click(checkbox);
  }

  expect(screen.getByRole("checkbox", { name: "选择 合成会话 51" })).toBeDisabled();
  expect(screen.getByText(/最多选择 50 个会话/)).toBeVisible();
});

it("IANA 时区按墙上时间显示并把用户编辑严格换回 UTC", async () => {
  const { ChatSearchPanel } = await import("./ChatSearchPanel");
  const client = api();
  const user = userEvent.setup();
  render(<ChatSearchPanel {...props} sourceId="manual" api={client} hideExport
    timezone="America/New_York"
    rangeStart="2026-09-13T14:20:00Z" rangeEnd="2026-09-13T14:22:00Z" />);

  const start = screen.getByLabelText("搜索开始时间") as HTMLInputElement;
  expect(start.value).toBe("2026-09-13T10:20");
  fireEvent.change(start, { target: { value: "2026-09-13T10:21" } });
  await user.click(screen.getByRole("checkbox", { name: "选择 合成会话 A" }));
  await user.click(screen.getByRole("button", { name: "搜索本地聊天" }));

  await waitFor(() => expect(client.searchChats).toHaveBeenCalledTimes(1));
  expect((client.searchChats as any).mock.calls[0][0].start_time).toBe("2026-09-13T14:21:00.000Z");
});

it("IANA 夏令时重复墙上时间失败关闭而不是猜测偏移", async () => {
  const { ChatSearchPanel } = await import("./ChatSearchPanel");
  render(<ChatSearchPanel {...props} sourceId="manual" api={api()} hideExport
    timezone="America/New_York"
    rangeStart="2026-11-01T05:30:00Z" rangeEnd="2026-11-01T07:00:00Z" />);

  expect(screen.getByRole("alert")).toHaveTextContent("搜索时间范围无效");
  expect(screen.getByRole("button", { name: "搜索本地聊天" })).toBeDisabled();
});

it("多选会话后显式确认导出，完成后搜索并加载下一页", async () => {
  const { ChatSearchPanel } = await import("./ChatSearchPanel");
  const client = api({
    searchChats: vi.fn()
      .mockResolvedValueOnce(firstPage)
      .mockResolvedValueOnce({ results: [{ ...firstPage.results[0], message_id: `message:v1:${"d".repeat(64)}`, text: "第二页合成结果" }], next_cursor: null }),
  });
  const user = userEvent.setup();
  render(<ChatSearchPanel {...props} api={client} />);

  await user.click(screen.getByRole("checkbox", { name: "选择 合成会话 A" }));
  await user.click(screen.getByRole("checkbox", { name: "选择 合成会话 B" }));
  await user.click(screen.getByRole("checkbox", { name: /确认只导出所选会话和时间范围/ }));
  await user.click(screen.getByRole("button", { name: "导出并建立本地索引" }));

  expect(await screen.findByText(/已完成并可搜索/)).toBeVisible();
  expect((client.issueExportConsent as any).mock.calls[0][0].conversation_selection_ids).toEqual(["opaque-a", "opaque-b"]);
  await user.type(screen.getByRole("searchbox", { name: "搜索聊天内容" }), "合成");
  await user.click(screen.getByRole("button", { name: "搜索本地聊天" }));
  expect(await screen.findByText("第一条合成结果")).toBeVisible();
  const results = within(screen.getByRole("region", { name: "搜索结果" }));
  expect(results.getByText(/batch:p1.*snapshot:p1/)).toBeVisible();
  await user.click(screen.getByRole("button", { name: "加载更多结果" }));
  expect(await screen.findByText("第二页合成结果")).toBeVisible();
  expect((client.searchChats as any).mock.calls[1][0].cursor).toBe("opaque-cursor");
});

it("可从搜索结果排除消息，并把冻结后的排除范围用于生成预览", async () => {
  const { ChatSearchPanel } = await import("./ChatSearchPanel");
  const client = api();
  const user = userEvent.setup();
  render(<ChatSearchPanel {...props} api={client} />);

  await user.click(screen.getByRole("checkbox", { name: "选择 合成会话 A" }));
  await user.type(screen.getByRole("searchbox", { name: "搜索聊天内容" }), "合成");
  await user.click(screen.getByRole("button", { name: "搜索本地聊天" }));
  await user.click(await screen.findByRole("checkbox", { name: /不用于 AI 生成：第一条合成结果/ }));
  expect(screen.getByText("排除：1 条消息")).toBeVisible();

  await user.type(screen.getByRole("textbox", { name: "操作主题" }), "target");
  await user.click(screen.getByRole("button", { name: "预览 AI 外发" }));
  await waitFor(() => expect(client.previewGeneration).toHaveBeenCalledTimes(1));
  expect((client.previewGeneration as any).mock.calls[0][0].excluded_message_ids).toEqual([
    firstPage.results[0].message_id,
  ]);
});

it("重新导出产生新快照时清除旧搜索结果和排除项", async () => {
  const { ChatSearchPanel } = await import("./ChatSearchPanel");
  const replacementJob = { ...job, job_id: "job:p1-replacement", batch_id: "batch:p1-replacement",
    snapshot_id: "snapshot:p1-replacement" };
  const client = api({
    startExport: vi.fn().mockResolvedValue({ ...replacementJob, state: "pending_authorization", searchable: false }),
    exportJob: vi.fn().mockResolvedValue(replacementJob),
  });
  const user = userEvent.setup();
  render(<ChatSearchPanel {...props} api={client} />);

  await user.click(screen.getByRole("checkbox", { name: "选择 合成会话 A" }));
  await user.click(screen.getByRole("button", { name: "搜索本地聊天" }));
  await user.click(await screen.findByRole("checkbox", { name: /不用于 AI 生成：第一条合成结果/ }));
  expect(screen.getByText("排除：1 条消息")).toBeVisible();

  await user.click(screen.getByRole("checkbox", { name: /确认只导出所选会话和时间范围/ }));
  await user.click(screen.getByRole("button", { name: "导出并建立本地索引" }));
  await screen.findByText(/已完成并可搜索/);

  expect(screen.queryByRole("region", { name: "搜索结果" })).not.toBeInTheDocument();
  expect(screen.getByText("排除：0 条消息")).toBeVisible();
  await user.type(screen.getByRole("textbox", { name: "操作主题" }), "target");
  await user.click(screen.getByRole("button", { name: "预览 AI 外发" }));
  await waitFor(() => expect(client.previewGeneration).toHaveBeenCalledTimes(1));
  expect((client.previewGeneration as any).mock.calls[0][0]).toMatchObject({
    snapshot_id: "snapshot:p1-replacement", excluded_message_ids: [],
  });
});

it("建立生成预览期间锁定搜索范围和排除控件", async () => {
  const { ChatSearchPanel } = await import("./ChatSearchPanel");
  let finishPreview!: (value: typeof generationPreview) => void;
  const pendingPreview = new Promise<typeof generationPreview>(resolve => { finishPreview = resolve; });
  const client = api({ previewGeneration: vi.fn().mockReturnValue(pendingPreview) });
  const user = userEvent.setup();
  render(<ChatSearchPanel {...props} api={client} />);

  await user.click(screen.getByRole("checkbox", { name: "选择 合成会话 A" }));
  await user.click(screen.getByRole("button", { name: "搜索本地聊天" }));
  const exclusion = await screen.findByRole("checkbox", { name: /不用于 AI 生成：第一条合成结果/ });
  await user.type(screen.getByRole("textbox", { name: "操作主题" }), "target");
  await user.click(screen.getByRole("button", { name: "预览 AI 外发" }));

  await waitFor(() => expect(exclusion).toBeDisabled());
  expect(screen.getByRole("checkbox", { name: "选择 合成会话 A" })).toBeDisabled();
  await act(async () => finishPreview(generationPreview));
  await waitFor(() => expect(exclusion).toBeEnabled());
});

it("搜索展示 loading、empty、error 并且重试不改变范围", async () => {
  const { ChatSearchPanel } = await import("./ChatSearchPanel");
  let finish!: (value: typeof firstPage) => void;
  const delayed = new Promise<typeof firstPage>(resolve => { finish = resolve; });
  const searchChats = vi.fn()
    .mockReturnValueOnce(delayed)
    .mockRejectedValueOnce(new Error("synthetic failure"))
    .mockResolvedValueOnce({ results: [], next_cursor: null });
  const client = api({ searchChats });
  const user = userEvent.setup();
  render(<ChatSearchPanel {...props} api={client} />);
  await user.click(screen.getByRole("checkbox", { name: "选择 合成会话 A" }));
  await user.click(screen.getByRole("button", { name: "搜索本地聊天" }));
  expect(screen.getByRole("status")).toHaveTextContent("正在搜索");
  await act(async () => finish(firstPage));
  await screen.findByText("第一条合成结果");

  await user.click(screen.getByRole("button", { name: "搜索本地聊天" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("搜索失败");
  await user.click(screen.getByRole("button", { name: "重试上次搜索" }));
  expect(await screen.findByText(/没有匹配的本地聊天记录/)).toBeVisible();
  await waitFor(() => expect(searchChats).toHaveBeenCalledTimes(3));
  expect(searchChats.mock.calls[1][0]).toEqual(searchChats.mock.calls[2][0]);
});

it("可通过影响计划删除导出批次并清空已失效搜索状态", async () => {
  const counts = { documents: 0, versions: 0, document_artifacts: 0, generation_jobs: 0, generation_requests: 0,
    generation_consents: 0, generation_previews: 0, cache_entries: 0, export_batches: 1,
    export_jobs: 1, export_consents: 1, snapshots: 1, messages: 2, managed_files: 1 };
  const client = api({
    prepareDeletion: vi.fn().mockResolvedValue({ plan_id: "deletion-plan:batch", target_kind: "batch",
      target_id: "batch:p1", include_managed_files: false, fingerprint: "d".repeat(64), counts,
      effects: ["数据库：删除批次和快照"], expires_at: "2099-01-01T00:00:00Z" }),
    confirmDeletion: vi.fn().mockResolvedValue({ plan_id: "deletion-plan:batch", state: "complete",
      deleted_counts: counts, managed_files_retained: true }),
  });
  const user = userEvent.setup();
  const { ChatSearchPanel } = await import("./ChatSearchPanel");
  render(<ChatSearchPanel {...props} api={client} />);
  await user.click(screen.getByRole("checkbox", { name: "选择 合成会话 A" }));
  await user.click(screen.getByRole("checkbox", { name: /确认只导出所选会话和时间范围/ }));
  await user.click(screen.getByRole("button", { name: "导出并建立本地索引" }));
  await screen.findByText(/已完成并可搜索/);

  await user.click(screen.getByRole("button", { name: "查看删除影响" }));
  await user.click(screen.getByRole("checkbox", { name: /我已核对上述影响/ }));
  await user.click(screen.getByRole("button", { name: "确认删除" }));

  expect(client.confirmDeletion).toHaveBeenCalledWith("deletion-plan:batch", "d".repeat(64));
  await waitFor(() => expect(screen.queryByText(/已完成并可搜索/)).not.toBeInTheDocument());
});
