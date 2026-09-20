import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import type { LocalApiClient } from "../../api/client";

const preview = {
  preview_id: "preview:p2", scope_fingerprint: "a".repeat(64), provider: "fake", model: "deterministic",
  mode: "complete" as const, message_count: 1, character_count: 22, estimated_input_tokens: 40,
  max_output_tokens: 2000, provider_calls: 1, price_status: "unknown" as const, estimated_cost: null,
  cache_hit: false, will_send_external: true,
  outbound_payloads: ['{"messages":[{"message_id":"opaque-message","text":"完整脱敏正文"}],"schema_version":1,"topic":"target"}'],
  expires_at: "2099-01-01T00:00:00Z",
};
const usage = { request_count: 1, completed_requests: 1, failed_requests: 0, cancelled_requests: 0,
  unknown_usage_requests: 0, known_prompt_tokens: 30, known_completion_tokens: 20, models: ["deterministic"],
  price_status: "unavailable" as const, price_source: null, price_version: null, price_observed_at: null,
  currency: null, estimated_cost: null, actual_cost: null, may_have_incurred_cost: true };
const completeJob = {
  job_id: "generation-job:p2", state: "complete" as const, scope_fingerprint: "a".repeat(64),
  completed_chunks: 1, total_chunks: 1, cancellation_requested: false, cache_hit: false,
  evidence_status: "verified" as const, error_code: null, revision: 5, usage,
  result: {
    evidence_status: "verified" as const, batch_id: "batch:p1", snapshot_id: "snapshot:p1",
    scope_fingerprint: "a".repeat(64), source_extraction_ids: [`extraction:v1:${"b".repeat(64)}`],
    result: {
      status: "complete" as const, title: "合成操作指南", preparations: [],
      steps: [{ instruction: "执行合成步骤", location: null, parameters: ["A"], conditions: [], warnings: [],
        expected_result: "完成", source_message_ids: [`message:v1:${"c".repeat(64)}`] }],
      checks: [], missing_information: [], conflicts: [],
    },
    evidence: [{ message_id: `message:v1:${"c".repeat(64)}`, conversation_id: "conversation-a",
      timestamp: "2026-09-01T00:00:01Z", sender_id: "sender-a", sender_name: "合成发送者", quote: "合成证据原文" }],
  },
};

function api(overrides: Record<string, unknown> = {}) {
  return {
    previewGeneration: vi.fn().mockResolvedValue(preview),
    authorizeGeneration: vi.fn().mockResolvedValue({ consent_id: "generation-consent:p2", scope_fingerprint: "a".repeat(64), outbound_authorized: true, expires_at: "2099-01-01T00:00:00Z" }),
    startGeneration: vi.fn().mockResolvedValue({ ...completeJob, state: "pending", result: null, evidence_status: "unverified", revision: 0 }),
    generationJob: vi.fn().mockResolvedValue(completeJob),
    cancelGeneration: vi.fn().mockResolvedValue({ ...completeJob, state: "cancelled", result: null, evidence_status: "unverified" }),
    retryGeneration: vi.fn().mockResolvedValue({ ...completeJob, state: "pending", result: null, evidence_status: "unverified" }),
    ...overrides,
  } as unknown as LocalApiClient;
}

const props = {
  api: api(), sourceId: "qq" as const, account: "account-a",
  conversationSelectionIds: ["opaque-a"], conversationLabels: ["合成会话 A"],
  snapshotId: "snapshot:p1", rangeStart: "2026-09-01T00:00:00Z", rangeEnd: "2026-09-02T00:00:00Z",
  excludedMessageIds: [],
  disabled: false,
};

it("先展示外发范围和预算，再授权生成并检查已验证证据", async () => {
  const { GenerationPanel } = await import("./GenerationPanel");
  const client = api();
  const user = userEvent.setup();
  render(<GenerationPanel {...props} api={client} />);

  await user.type(screen.getByRole("textbox", { name: "操作主题" }), "target");
  await user.click(screen.getByRole("button", { name: "预览 AI 外发" }));

  expect(await screen.findByText(/将向 fake.*发送 1 条/)).toBeVisible();
  expect(screen.getByText(/预计输入不超过 40 token/)).toBeVisible();
  await user.click(screen.getByText(/查看将要外发的完整脱敏文本/));
  expect(screen.getByRole("region", { name: "外发分块 1" })).toHaveTextContent("完整脱敏正文");
  await user.click(screen.getByRole("checkbox", { name: /授权本次向外部 AI/ }));
  await user.click(screen.getByRole("button", { name: "确认并生成" }));

  expect(await screen.findByText("合成操作指南")).toBeVisible();
  expect(screen.getByText(/AI 生成.*本地证据已验证/)).toBeVisible();
  expect(screen.getByText("执行合成步骤")).toBeVisible();
  expect(screen.getByText(/AI 用量：1 次请求/)).toBeVisible();
  await user.click(screen.getByText(/查看已验证证据/));
  const evidence = within(screen.getByRole("region", { name: "已验证证据" }));
  expect(evidence.getByText("合成证据原文")).toBeVisible();
  expect((client.previewGeneration as any).mock.calls[0][0].conversation_selection_ids).toEqual(["opaque-a"]);
  expect((client.previewGeneration as any).mock.calls[0][0].excluded_message_ids).toEqual([]);
});

it("排除消息变化后清除旧预览和授权，并按新范围重新预览", async () => {
  const { GenerationPanel } = await import("./GenerationPanel");
  const client = api();
  const user = userEvent.setup();
  const excludedMessageId = `message:v1:${"d".repeat(64)}`;
  const view = render(<GenerationPanel {...props} api={client} />);

  await user.type(screen.getByRole("textbox", { name: "操作主题" }), "target");
  await user.click(screen.getByRole("button", { name: "预览 AI 外发" }));
  await user.click(await screen.findByRole("checkbox", { name: /授权本次向外部 AI/ }));
  expect(screen.getByRole("button", { name: "确认并生成" })).toBeEnabled();

  view.rerender(<GenerationPanel {...props} api={client} excludedMessageIds={[excludedMessageId]} />);

  await waitFor(() => expect(screen.queryByRole("region", { name: "AI 外发预览" })).not.toBeInTheDocument());
  expect(screen.queryByRole("checkbox", { name: /授权本次向外部 AI/ })).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "预览 AI 外发" }));
  await screen.findByRole("region", { name: "AI 外发预览" });
  expect((client.previewGeneration as any).mock.calls[1][0].excluded_message_ids).toEqual([excludedMessageId]);
});

it("丢弃排除范围变化后才返回的旧预览", async () => {
  const { GenerationPanel } = await import("./GenerationPanel");
  let finishPreview!: (value: typeof preview) => void;
  const pendingPreview = new Promise<typeof preview>(resolve => { finishPreview = resolve; });
  const client = api({ previewGeneration: vi.fn().mockReturnValue(pendingPreview) });
  const user = userEvent.setup();
  const excludedMessageId = `message:v1:${"e".repeat(64)}`;
  const view = render(<GenerationPanel {...props} api={client} />);

  await user.type(screen.getByRole("textbox", { name: "操作主题" }), "target");
  await user.click(screen.getByRole("button", { name: "预览 AI 外发" }));
  view.rerender(<GenerationPanel {...props} api={client} excludedMessageIds={[excludedMessageId]} />);
  await act(async () => finishPreview(preview));

  await waitFor(() => expect(screen.getByRole("button", { name: "预览 AI 外发" })).toBeEnabled());
  expect(screen.queryByRole("region", { name: "AI 外发预览" })).not.toBeInTheDocument();
});

it("授权请求期间排除范围变化时不启动旧范围生成", async () => {
  const { GenerationPanel } = await import("./GenerationPanel");
  const consent = { consent_id: "generation-consent:p2", scope_fingerprint: "a".repeat(64),
    outbound_authorized: true, expires_at: "2099-01-01T00:00:00Z" };
  let finishConsent!: (value: typeof consent) => void;
  const pendingConsent = new Promise<typeof consent>(resolve => { finishConsent = resolve; });
  const client = api({ authorizeGeneration: vi.fn().mockReturnValue(pendingConsent) });
  const user = userEvent.setup();
  const excludedMessageId = `message:v1:${"f".repeat(64)}`;
  const view = render(<GenerationPanel {...props} api={client} />);

  await user.type(screen.getByRole("textbox", { name: "操作主题" }), "target");
  await user.click(screen.getByRole("button", { name: "预览 AI 外发" }));
  await user.click(await screen.findByRole("checkbox", { name: /授权本次向外部 AI/ }));
  await user.click(screen.getByRole("button", { name: "确认并生成" }));
  await waitFor(() => expect(client.authorizeGeneration).toHaveBeenCalledTimes(1));
  view.rerender(<GenerationPanel {...props} api={client} excludedMessageIds={[excludedMessageId]} />);
  await act(async () => finishConsent(consent));

  await waitFor(() => expect(screen.getByRole("button", { name: "预览 AI 外发" })).toBeEnabled());
  expect(client.startGeneration).not.toHaveBeenCalled();
});

it("失败结果明确标为未验证并允许按原冻结范围重试", async () => {
  const { GenerationPanel } = await import("./GenerationPanel");
  const failed = { ...completeJob, state: "failed" as const, evidence_status: "unverified" as const,
    error_code: "evidence_invalid", result: null };
  const client = api({ startGeneration: vi.fn().mockResolvedValue(failed), retryGeneration: vi.fn().mockResolvedValue(completeJob) });
  const user = userEvent.setup();
  render(<GenerationPanel {...props} api={client} />);
  await user.type(screen.getByRole("textbox", { name: "操作主题" }), "target");
  await user.click(screen.getByRole("button", { name: "预览 AI 外发" }));
  await user.click(await screen.findByRole("checkbox", { name: /授权本次向外部 AI/ }));
  await user.click(screen.getByRole("button", { name: "确认并生成" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("未通过本地证据验证");
  await user.click(screen.getByRole("button", { name: "重试生成" }));
  await waitFor(() => expect(client.retryGeneration).toHaveBeenCalledWith("generation-job:p2"));
});

it("没有已导入 snapshot 时不允许进入生成授权", async () => {
  const { GenerationPanel } = await import("./GenerationPanel");
  render(<GenerationPanel {...props} snapshotId={null} />);
  expect(screen.getByText(/先完成导出与本地导入/)).toBeVisible();
  expect(screen.getByRole("button", { name: "预览 AI 外发" })).toBeDisabled();
});
