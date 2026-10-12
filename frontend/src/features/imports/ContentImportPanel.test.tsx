import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import type { LocalApiClient } from "../../api/client";
import type { ContentBatch, ContentPreview } from "../../types";

const preview: ContentPreview = {
  preview_id: "a".repeat(32), preview_fingerprint: "f".repeat(64),
  suggested_kind: "note", status: "ready", expires_at_utc: "2099-01-01T00:00:00Z",
  images: [], warnings: [], units: [{
    unit_id: "candidate-0", kind: "note_passage", group_id: "note-1",
    text: "第一段正文", sender_name: null, sent_at: null, order: 0, image_ref: null,
  }],
};
const batch: ContentBatch = {
  batch_id: "b".repeat(32), unit_count: 1, image_count: 0,
  images: [], created_at_utc: "2026-10-12T00:00:00Z",
};

it("粘贴笔记必须预览、校对和重新确认才能导入，并能刷新恢复", async () => {
  const api = {
    recoverContentBatches: vi.fn().mockResolvedValue({ items: [batch], next_cursor: null }),
    previewContentImport: vi.fn().mockResolvedValue(preview),
    reviseContentPreview: vi.fn().mockResolvedValue({ ...preview, preview_fingerprint: "e".repeat(64), units: [{ ...preview.units[0], text: "校对后的正文" }] }),
    commitContentPreview: vi.fn().mockResolvedValue(batch),
  } as unknown as LocalApiClient;
  const { ContentImportPanel } = await import("./ContentImportPanel");
  const user = userEvent.setup();
  const { unmount } = render(<ContentImportPanel api={api} busy={false} onCommitted={() => undefined} />);
  expect(await screen.findByText(/已导入 1 条正文/)).toBeVisible();
  await user.click(screen.getByRole("radio", { name: "粘贴笔记文字" }));
  await user.type(screen.getByRole("textbox", { name: "笔记正文" }), "第一段正文");
  await user.click(screen.getByRole("button", { name: "识别并预览" }));
  expect(await screen.findByRole("textbox", { name: "正文 1" })).toHaveValue("第一段正文");
  await user.clear(screen.getByRole("textbox", { name: "正文 1" }));
  await user.type(screen.getByRole("textbox", { name: "正文 1" }), "校对后的正文");
  expect(screen.getByRole("checkbox", { name: /确认以上正文/ })).not.toBeChecked();
  await user.click(screen.getByRole("button", { name: "保存校对" }));
  await waitFor(() => expect(api.reviseContentPreview).toHaveBeenCalledWith(preview.preview_id, expect.arrayContaining([expect.objectContaining({ text: "校对后的正文" })]), []));
  await user.click(screen.getByRole("checkbox", { name: /确认以上正文/ }));
  await user.click(screen.getByRole("button", { name: "确认导入正文" }));
  await waitFor(() => expect(api.commitContentPreview).toHaveBeenCalledWith(preview.preview_id, "e".repeat(64)));
  unmount();
  render(<ContentImportPanel api={api} busy={false} onCommitted={() => undefined} />);
  expect(await screen.findByText(/已导入 1 条正文/)).toBeVisible();
});

it("截图只显示服务端过滤后的正文候选，类型和图片顺序可校对", async () => {
  const imagePreview: ContentPreview = { ...preview, status: "boundary_uncertain", suggested_kind: "uncertain",
    images: [{ image_ref: "image-0", filename: "screen.png", input_order: 0, sha256: "1".repeat(64) }],
    units: [{ ...preview.units[0], image_ref: "image-0", text: "正文内容" }],
  };
  const api = {
    recoverContentBatches: vi.fn().mockResolvedValue({ items: [], next_cursor: null }),
    previewContentImport: vi.fn().mockResolvedValue(imagePreview),
    reviseContentPreview: vi.fn().mockResolvedValue({ ...imagePreview, status: "ready", preview_fingerprint: "e".repeat(64) }),
  } as unknown as LocalApiClient;
  const { ContentImportPanel } = await import("./ContentImportPanel");
  const user = userEvent.setup();
  render(<ContentImportPanel api={api} busy={false} onCommitted={() => undefined} />);
  const bytes = new Uint8Array([137, 80, 78, 71]);
  const file = new File([bytes], "screen.png", { type: "image/png" });
  Object.defineProperty(file, "arrayBuffer", { value: async () => bytes.buffer });
  fireEvent.change(screen.getByLabelText("聊天或笔记截图"), { target: { files: [file] } });
  await user.click(screen.getByRole("button", { name: "识别并预览" }));
  expect(await screen.findByDisplayValue("正文内容")).toBeVisible();
  expect(screen.queryByText("点赞 85")).not.toBeInTheDocument();
  expect(screen.getByText(/边界不确定/)).toBeVisible();
  await user.selectOptions(screen.getByRole("combobox", { name: "类型 1" }), "chat_message");
  await user.click(screen.getByRole("button", { name: "保存校对" }));
  await waitFor(() => expect(api.reviseContentPreview).toHaveBeenCalledWith(imagePreview.preview_id,
    expect.arrayContaining([expect.objectContaining({ kind: "chat_message" })]),
    [{ image_ref: "image-0", input_order: 0 }]));
});

it("超限截图在浏览器读取之前就被拒绝", async () => {
  const api = { recoverContentBatches: vi.fn().mockResolvedValue({ items: [], next_cursor: null }), previewContentImport: vi.fn() } as unknown as LocalApiClient;
  const { ContentImportPanel } = await import("./ContentImportPanel");
  const user = userEvent.setup();
  render(<ContentImportPanel api={api} busy={false} onCommitted={() => undefined} />);
  const file = new File(["x"], "huge.png", { type: "image/png" });
  const read = vi.fn();
  Object.defineProperties(file, { size: { value: 15 * 1024 * 1024 + 1 }, arrayBuffer: { value: read } });
  fireEvent.change(screen.getByLabelText("聊天或笔记截图"), { target: { files: [file] } });
  await user.click(screen.getByRole("button", { name: "识别并预览" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("15 MiB");
  expect(read).not.toHaveBeenCalled();
  expect(api.previewContentImport).not.toHaveBeenCalled();
});
