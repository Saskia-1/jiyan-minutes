import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import type { LocalApiClient } from "../../api/client";
import { ApiError } from "../../api/client";

const preview = {
  preview_id: "import-preview:a", preview_fingerprint: "a".repeat(64),
  expires_at_utc: "2099-01-01T00:00:00Z", ready_to_commit: true,
  source: { format: "txt" as const, encoding: "utf-8" as const, container: "plain" as const,
    member_name: null, row_count: 1, columns: ["sent_at", "sender_name", "text"],
    sha256: "b".repeat(64), byte_size: 42,
    warnings: ["manual_backup_is_secondary", "completeness_is_not_inferred"] },
  applied_mapping: { sent_at: "sent_at", sender_name: "sender_name", text: "text" },
  analysis: { total_count: 1, parsed_count: 1, unresolved_count: 0, duplicate_count: 0,
    sender_count: 1, time_start: "2026-09-13T02:20:00Z", time_end: "2026-09-13T02:20:00Z",
    message_types: { text: 1, image: 0, file: 0, other: 0, unknown: 0 },
    conversations: [{ conversation_id: "opaque-internal", conversation_name: "项目群", message_count: 1 }],
    warnings: ["manual_backup_is_secondary"], issues: [],
    samples: [{ row_number: 1, conversation_name: "项目群", sender_name: "张三",
      timestamp: "2026-09-13T02:20:00Z", message_type: "text", text: "今天开会" }] },
};
const committed = {
  import_id: "import:a", batch_id: "batch:manual:a", snapshot_id: "snapshot:manual:a",
  source_id: "manual" as const, account_id: "import:account",
  range_start: "2026-09-13T02:20:00Z", range_end: "2026-09-13T02:20:01Z",
  display_timezone: "UTC",
  conversations: [{ conversation_selection_id: "selection:manual:a", display_name: "项目群", conversation_type: "group" }],
  warnings: ["manual_backup_is_secondary"],
};
const emptyRecovery = { imports: [], next_cursor: null };

it("现有聊天入口保留，并可展开本机截图／笔记导入", async () => {
  const api = {
    recoverImports: vi.fn().mockResolvedValue(emptyRecovery),
    recoverContentBatches: vi.fn().mockResolvedValue({ items: [], next_cursor: null }),
  } as unknown as LocalApiClient;
  const { ChatImportPanel } = await import("./ChatImportPanel");
  const user = userEvent.setup();
  render(<ChatImportPanel api={api} outputRootId="fixture-root" />);
  expect(screen.getByLabelText("聊天记录文件")).toBeVisible();
  await user.click(screen.getByText("截图 OCR / 笔记文字导入"));
  expect(await screen.findByLabelText("聊天或笔记截图")).toBeVisible();
  expect(screen.getByLabelText("聊天记录文件")).toBeVisible();
});

it("混合检索只发送明确勾选的来源，时间未知内容需单独选择", async () => {
  const contentBatch = { batch_id: "c".repeat(32), unit_count: 1, image_count: 0,
    images: [], created_at_utc: "2026-10-12T00:00:00Z" };
  const api = {
    recoverImports: vi.fn().mockResolvedValue({ imports: [committed], next_cursor: null }),
    recoverContentBatches: vi.fn().mockResolvedValue({ items: [contentBatch], next_cursor: null }),
    searchChats: vi.fn().mockResolvedValue({ results: [], next_cursor: null }),
    searchMixed: vi.fn().mockResolvedValue({ hits: [{ unit_id: "content:v1:note:" + "a".repeat(64), source_kind: "note_passage",
      source_ref: { kind: "content_batch", id: contentBatch.batch_id }, text: "音乐笔记", sender_name: null,
      timestamp: null, source_order: 0, image_id: null }] }),
  } as unknown as LocalApiClient;
  const { ChatImportPanel } = await import("./ChatImportPanel");
  const user = userEvent.setup();
  render(<ChatImportPanel compact api={api} outputRootId="fixture-root" />);
  await screen.findByText(/当前聊天/);
  await user.type(screen.getByRole("textbox", { name: "关键词" }), "音乐");
  await user.click(screen.getByText("选择截图／笔记与聊天来源，预览混合检索"));
  await user.click(await screen.findByRole("checkbox", { name: /截图／笔记批次 1/ }));
  await user.click(screen.getByRole("checkbox", { name: "包含时间未知的聊天与笔记" }));
  await user.click(screen.getByRole("button", { name: "预览勾选来源的相关正文" }));
  await waitFor(() => expect(api.searchMixed).toHaveBeenCalledWith({
    source_refs: [{ kind: "content_batch", id: contentBatch.batch_id }], include_undated: true, terms: ["音乐"],
  }));
  await user.click(screen.getByText("相关正文 1 条"));
  expect(await screen.findByText("音乐笔记")).toBeVisible();
});

it("简洁导入无时间聊天默认折叠，展开排除后只整理剩余消息", async () => {
  const rows = ["收藏歌单", "无关消息"].map((text, index) => ({
    message_id: `message:${index}`, conversation_id: "c", timestamp: null, source_order: index + 1,
    sender_name: "甲", sender_id: null, text, batch_id: committed.batch_id, snapshot_id: committed.snapshot_id,
  }));
  const api = { recoverImports: vi.fn().mockResolvedValue({ imports: [{ ...committed, analysis: { ...preview.analysis, time_start: null, time_end: null } }], next_cursor: null }),
    searchChats: vi.fn().mockResolvedValue({ results: rows, next_cursor: null }),
    expandTopic: vi.fn().mockRejectedValue(new Error("offline")),
    previewGenerationContext: vi.fn().mockResolvedValue({ messages: rows, added_count: 0 }),
    previewGeneration: vi.fn().mockRejectedValue(new Error("stop before external generation")),
  } as unknown as LocalApiClient;
  const user = userEvent.setup();
  const { ChatImportPanel } = await import("./ChatImportPanel");
  render(<ChatImportPanel compact api={api} outputRootId="" />);
  const toggle = await screen.findByText(/展开聊天内容（2 条/);
  expect(toggle.closest("details")).not.toHaveAttribute("open");
  await user.click(toggle);
  expect(screen.getByText("收藏歌单")).toBeVisible();
  expect(screen.getAllByText("时间未知")).toHaveLength(2);
  await user.click(screen.getAllByRole("checkbox", { name: "排除此消息" })[1]);
  await user.type(screen.getByRole("textbox", { name: "关键词" }), "音乐");
  expect(screen.getByRole("checkbox", { name: /自动扩展相关关键词/ })).not.toBeChecked();
  await user.click(screen.getByRole("button", { name: "开始整理" }));
  await waitFor(() => expect(api.previewGeneration).toHaveBeenCalledWith(expect.objectContaining({
    summary_type: "ideas", include_undated: true, excluded_message_ids: ["message:1"], included_message_ids: ["message:0", "message:1"],
  })));
  expect(api.expandTopic).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: "重新整理" })).toBeEnabled();
});

it("原始 MM-DD TXT 会将用户明确选择的年份传入预览", async () => {
  const api = {
    recoverImports: vi.fn().mockResolvedValue(emptyRecovery),
    previewImport: vi.fn().mockResolvedValue(preview),
  } as unknown as LocalApiClient;
  const user = userEvent.setup();
  const { ChatImportPanel } = await import("./ChatImportPanel");
  render(<ChatImportPanel api={api} outputRootId="fixture-root" />);
  const file = new File(["Alice: 09-09 21:59:54\nhello\n"], "chat.txt", { type: "text/plain" });
  Object.defineProperty(file, "arrayBuffer", {
    value: async () => new TextEncoder().encode("Alice: 09-09 21:59:54\nhello\n").buffer,
  });

  fireEvent.change(screen.getByLabelText("聊天记录文件"), { target: { files: [file] } });
  await user.type(screen.getByLabelText("默认年份"), "2026");
  await user.type(screen.getByLabelText("默认会话名称"), "cs发展路径");
  await user.click(screen.getByRole("button", { name: "生成导入预览" }));

  await waitFor(() => expect(api.previewImport).toHaveBeenCalledWith(expect.objectContaining({
    options: expect.objectContaining({ default_year: 2026 }),
  })));
});

it("粘贴内容必须先预览并确认，提交后进入现有搜索与生成工作区", async () => {
  const api = {
    recoverImports: vi.fn().mockResolvedValue(emptyRecovery),
    previewImport: vi.fn().mockResolvedValue(preview),
    commitImport: vi.fn().mockResolvedValue(committed),
    searchChats: vi.fn().mockResolvedValue({ results: [], next_cursor: null }),
    previewGeneration: vi.fn(),
    prepareDeletion: vi.fn(), confirmDeletion: vi.fn(),
  } as unknown as LocalApiClient;
  const { ChatImportPanel } = await import("./ChatImportPanel");
  const user = userEvent.setup();
  render(<ChatImportPanel api={api} outputRootId="fixture-root" />);

  await user.click(screen.getByRole("radio", { name: "粘贴文本" }));
  fireEvent.change(screen.getByRole("textbox", { name: "聊天文本" }), { target: { value: "[10:20] 张三：今天开会" } });
  await user.type(screen.getByLabelText("默认日期"), "2026-09-13");
  await user.type(screen.getByLabelText("默认会话名称"), "项目群");
  await user.click(screen.getByRole("button", { name: "生成导入预览" }));

  expect(await screen.findByText(/可导入 1 条/)).toBeVisible();
  expect(screen.getByText(/文本 1 · 图片 0 · 文件 0 · 其他 0 · 未知 0/)).toBeVisible();
  expect(screen.getByText("今天开会")).toBeVisible();
  expect(api.commitImport).not.toHaveBeenCalled();
  await user.click(screen.getByRole("checkbox", { name: /确认按此预览导入/ }));
  await user.click(screen.getByRole("button", { name: "确认导入本地索引" }));

  expect(await screen.findByText(/导入完成/)).toBeVisible();
  expect(screen.queryByRole("region", { name: "导入预览" })).not.toBeInTheDocument();
  expect(screen.getByRole("region", { name: "导入聊天的搜索与生成" })).toBeVisible();
  expect(screen.getByRole("checkbox", { name: "选择 项目群" })).toBeVisible();
  await waitFor(() => expect(api.commitImport).toHaveBeenCalledWith("import-preview:a", "a".repeat(64)));
});

it("更换导入来源时不会把旧文件的字段映射带到新预览", async () => {
  const api = {
    recoverImports: vi.fn().mockResolvedValue(emptyRecovery),
    previewImport: vi.fn().mockResolvedValue(preview),
  } as unknown as LocalApiClient;
  const { ChatImportPanel } = await import("./ChatImportPanel");
  const user = userEvent.setup();
  render(<ChatImportPanel api={api} outputRootId="fixture-root" />);

  await user.click(screen.getByRole("radio", { name: "粘贴文本" }));
  fireEvent.change(screen.getByRole("textbox", { name: "聊天文本" }), {
    target: { value: "[10:20] A：step" },
  });
  await user.click(screen.getByRole("button", { name: "生成导入预览" }));
  await screen.findByRole("region", { name: "导入预览" });

  await user.click(screen.getByRole("radio", { name: "导入文件" }));
  await user.click(screen.getByRole("radio", { name: "粘贴文本" }));
  await user.click(screen.getByRole("button", { name: "生成导入预览" }));

  expect(api.previewImport).toHaveBeenLastCalledWith(expect.objectContaining({
    options: expect.objectContaining({ mapping: {} }),
  }));
});

it.each(["chat.txt", "chat.json", "chat.csv", "chat.xlsx"])(
  "%s 文件由用户选择后才读取并编码为预览请求",
  async filename => {
    const api = {
      recoverImports: vi.fn().mockResolvedValue(emptyRecovery),
      previewImport: vi.fn().mockResolvedValue(preview),
    } as unknown as LocalApiClient;
    const { ChatImportPanel } = await import("./ChatImportPanel");
    const user = userEvent.setup();
    render(<ChatImportPanel api={api} outputRootId="fixture-root" />);
    const bytes = new Uint8Array([0, 1, 2, 253, 254, 255]);
    const file = new File([bytes], filename, { type: "application/octet-stream" });
    Object.defineProperty(file, "arrayBuffer", {
      value: async () => bytes.buffer,
    });

    fireEvent.change(screen.getByLabelText("聊天记录文件"), {
      target: { files: [file] },
    });
    await user.click(screen.getByRole("button", { name: "生成导入预览" }));

    await waitFor(() => expect(api.previewImport).toHaveBeenCalledWith(expect.objectContaining({
      origin: "imported_file",
      filename,
      payload_base64: "AAEC/f7/",
    })));
  },
);

it("超过 64 MiB 的文件在浏览器读取前被拒绝", async () => {
  const api = { recoverImports: vi.fn().mockResolvedValue(emptyRecovery), previewImport: vi.fn() } as unknown as LocalApiClient;
  const { ChatImportPanel } = await import("./ChatImportPanel");
  const user = userEvent.setup();
  render(<ChatImportPanel api={api} outputRootId="fixture-root" />);
  const file = new File(["x"], "too-large.zip");
  const arrayBuffer = vi.fn();
  Object.defineProperties(file, {
    size: { value: 64 * 1024 * 1024 + 1 },
    arrayBuffer: { value: arrayBuffer },
  });

  fireEvent.change(screen.getByLabelText("聊天记录文件"), {
    target: { files: [file] },
  });
  await user.click(screen.getByRole("button", { name: "生成导入预览" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("64 MiB");
  expect(arrayBuffer).not.toHaveBeenCalled();
  expect(api.previewImport).not.toHaveBeenCalled();
});

it("预览请求运行期间锁定导入来源避免旧响应串到新来源", async () => {
  let resolvePreview!: (value: typeof preview) => void;
  const pending = new Promise<typeof preview>(resolve => { resolvePreview = resolve; });
  const api = {
    recoverImports: vi.fn().mockResolvedValue(emptyRecovery),
    previewImport: vi.fn().mockReturnValue(pending),
  } as unknown as LocalApiClient;
  const { ChatImportPanel } = await import("./ChatImportPanel");
  const user = userEvent.setup();
  render(<ChatImportPanel api={api} outputRootId="fixture-root" />);

  await user.click(screen.getByRole("radio", { name: "粘贴文本" }));
  fireEvent.change(screen.getByRole("textbox", { name: "聊天文本" }), {
    target: { value: "[10:20] A：step" },
  });
  await user.click(screen.getByRole("button", { name: "生成导入预览" }));

  expect(screen.getByRole("radio", { name: "导入文件" })).toBeDisabled();
  expect(screen.getByRole("radio", { name: "粘贴文本" })).toBeDisabled();
  resolvePreview(preview);
  await screen.findByRole("region", { name: "导入预览" });
  expect(screen.getByRole("radio", { name: "导入文件" })).toBeEnabled();
});

it("AI 预览或生成期间锁定手动导入批次删除", async () => {
  const pendingGenerationPreview = new Promise(() => undefined);
  const api = {
    recoverImports: vi.fn().mockResolvedValue(emptyRecovery),
    previewImport: vi.fn().mockResolvedValue(preview),
    commitImport: vi.fn().mockResolvedValue(committed),
    searchChats: vi.fn().mockResolvedValue({
      results: [{
        message_id: `message:v1:${"1".repeat(64)}`,
        conversation_id: "opaque-internal",
        timestamp: "2026-09-13T02:20:00Z",
        sender_id: null,
        sender_name: "张三",
        text: "今天开会",
        batch_id: committed.batch_id,
        snapshot_id: committed.snapshot_id,
      }],
      next_cursor: null,
    }),
    previewGeneration: vi.fn().mockReturnValue(pendingGenerationPreview),
    prepareDeletion: vi.fn(), confirmDeletion: vi.fn(),
  } as unknown as LocalApiClient;
  const { ChatImportPanel } = await import("./ChatImportPanel");
  const user = userEvent.setup();
  render(<ChatImportPanel api={api} outputRootId="fixture-root" />);

  await user.click(screen.getByRole("radio", { name: "粘贴文本" }));
  fireEvent.change(screen.getByRole("textbox", { name: "聊天文本" }), {
    target: { value: "[10:20] 张三：今天开会" },
  });
  await user.click(screen.getByRole("button", { name: "生成导入预览" }));
  await user.click(await screen.findByRole("checkbox", { name: /确认按此预览导入/ }));
  await user.click(screen.getByRole("button", { name: "确认导入本地索引" }));
  await user.click(await screen.findByRole("checkbox", { name: "选择 项目群" }));
  await user.click(screen.getByRole("button", { name: "搜索本地聊天" }));
  await screen.findByRole("region", { name: "搜索结果" });
  await user.type(screen.getByLabelText("操作主题"), "开会");
  await user.click(screen.getByRole("button", { name: "预览 AI 外发" }));

  const deletion = screen.getByRole("region", { name: "删除这次导入及本地索引" });
  await waitFor(() => expect(
    within(deletion).getByRole("button", { name: "查看删除影响" }),
  ).toBeDisabled());
  expect(screen.getByRole("radio", { name: "导入文件" })).toBeDisabled();
  expect(screen.getByRole("radio", { name: "粘贴文本" })).toBeDisabled();
  expect(screen.getByRole("textbox", { name: "聊天文本" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "生成导入预览" })).toBeDisabled();
});

it("展示不含原始内容的可恢复导入错误建议", async () => {
  const api = {
    recoverImports: vi.fn().mockResolvedValue(emptyRecovery),
    previewImport: vi.fn().mockRejectedValue(new ApiError(422, "unsupported_format")),
  } as unknown as LocalApiClient;
  const { ChatImportPanel } = await import("./ChatImportPanel");
  const user = userEvent.setup();
  render(<ChatImportPanel api={api} outputRootId="fixture-root" />);

  await user.click(screen.getByRole("radio", { name: "粘贴文本" }));
  fireEvent.change(screen.getByRole("textbox", { name: "聊天文本" }), {
    target: { value: "private-content" },
  });
  await user.click(screen.getByRole("button", { name: "生成导入预览" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("不支持这种文件格式");
  expect(screen.getByRole("alert")).not.toHaveTextContent("private-content");
});

it("恢复全部可见导入批次且不猜测未知会话类型", async () => {
  const older = {
    ...committed, import_id: "import:older", batch_id: "batch:manual:older",
    snapshot_id: "snapshot:manual:older",
    conversations: [{ conversation_selection_id: "selection:manual:older", display_name: "旧批次", conversation_type: null }],
  };
  const api = {
    recoverImports: vi.fn().mockResolvedValue({ imports: [committed, older], next_cursor: null }),
    searchChats: vi.fn().mockResolvedValue({ results: [], next_cursor: null }),
    previewGeneration: vi.fn(), prepareDeletion: vi.fn(), confirmDeletion: vi.fn(),
  } as unknown as LocalApiClient;
  const { ChatImportPanel } = await import("./ChatImportPanel");
  const user = userEvent.setup();
  render(<ChatImportPanel api={api} outputRootId="fixture-root" />);

  expect(await screen.findByText("旧批次", { exact: false })).toBeVisible();
  expect(screen.getAllByRole("button", { name: /当前已打开|打开搜索与整理/ })).toHaveLength(2);
  const olderRegion = screen.getByRole("region", { name: "已导入批次 2" });
  expect(within(olderRegion).getByRole("region", { name: "删除已导入批次 2" })).toBeVisible();
  await user.click(within(olderRegion).getByRole("button", { name: "打开搜索与整理" }));
  expect(await screen.findByText("类型未知")).toBeVisible();
});

it("恢复批次使用提交时区且不受新导入表单时区影响", async () => {
  const api = {
    recoverImports: vi.fn().mockResolvedValue({ imports: [committed], next_cursor: null }),
    searchChats: vi.fn().mockResolvedValue({ results: [], next_cursor: null }),
    previewGeneration: vi.fn(), prepareDeletion: vi.fn(), confirmDeletion: vi.fn(),
  } as unknown as LocalApiClient;
  const { ChatImportPanel } = await import("./ChatImportPanel");
  render(<ChatImportPanel api={api} outputRootId="fixture-root" />);

  const start = await screen.findByLabelText("搜索开始时间") as HTMLInputElement;
  expect(start.value).toBe("2026-09-13T02:20");
  expect((screen.getByLabelText("原始时间时区") as HTMLSelectElement).value).toBe("Asia/Shanghai");
});

it("按页加载更早的可清理导入批次", async () => {
  const older = {
    ...committed, import_id: "import:older", batch_id: "batch:manual:older",
    snapshot_id: "snapshot:manual:older",
    conversations: [{ conversation_selection_id: "selection:manual:older", display_name: "更早批次", conversation_type: "group" }],
  };
  const recoverImports = vi.fn()
    .mockResolvedValueOnce({ imports: [committed], next_cursor: "import:a" })
    .mockResolvedValueOnce({ imports: [older], next_cursor: null });
  const api = {
    recoverImports, searchChats: vi.fn().mockResolvedValue({ results: [], next_cursor: null }),
    previewGeneration: vi.fn(), prepareDeletion: vi.fn(), confirmDeletion: vi.fn(),
  } as unknown as LocalApiClient;
  const { ChatImportPanel } = await import("./ChatImportPanel");
  const user = userEvent.setup();
  render(<ChatImportPanel api={api} outputRootId="fixture-root" />);

  await user.click(await screen.findByRole("button", { name: "加载更早的导入" }));

  expect(await screen.findByText("更早批次", { exact: false })).toBeVisible();
  expect(recoverImports).toHaveBeenNthCalledWith(1, null);
  expect(recoverImports).toHaveBeenNthCalledWith(2, "import:a");
  expect(screen.getByRole("region", { name: "删除已导入批次 2" })).toBeVisible();
});
