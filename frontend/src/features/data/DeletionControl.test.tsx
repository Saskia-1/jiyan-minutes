import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import type { LocalApiClient } from "../../api/client";
import type { DeletionPlan } from "../../types";

const counts = {
  documents: 1, versions: 2, document_artifacts: 1, generation_jobs: 1, generation_requests: 1,
  generation_consents: 1, generation_previews: 1, cache_entries: 1,
  export_batches: 0, export_jobs: 0, export_consents: 0, snapshots: 0,
  messages: 0, managed_files: 1, content_batches: 0, content_units: 0, content_images: 0,
};
const plan: DeletionPlan = {
  plan_id: "deletion-plan:p3", target_kind: "document", target_id: "document:p3",
  include_managed_files: true, fingerprint: "d".repeat(64), counts,
  effects: ["数据库：删除文档与版本", "上游：不删除 QQ/微信原始数据"],
  expires_at: "2099-01-01T00:00:00Z",
};

it("先显示精确删除影响，再以指纹二次确认", async () => {
  const api = {
    prepareDeletion: vi.fn().mockResolvedValue(plan),
    confirmDeletion: vi.fn().mockResolvedValue({ plan_id: plan.plan_id, state: "complete",
      deleted_counts: counts, managed_files_retained: false }),
  } as unknown as LocalApiClient;
  const onDeleted = vi.fn();
  const user = userEvent.setup();
  const { DeletionControl } = await import("./DeletionControl");
  render(<DeletionControl api={api} targetKind="document" targetId="document:p3"
    label="删除这份本地文档" onDeleted={onDeleted} />);

  await user.click(screen.getByRole("checkbox", { name: /同时删除受管本地文件/ }));
  await user.click(screen.getByRole("button", { name: "查看删除影响" }));
  expect(await screen.findByText("版本历史：2")).toBeVisible();
  expect(screen.getByText(/不删除 QQ\/微信原始数据/)).toBeVisible();
  expect(api.prepareDeletion).toHaveBeenCalledWith("document", "document:p3", true);

  await user.click(screen.getByRole("checkbox", { name: /我已核对上述影响/ }));
  await user.click(screen.getByRole("button", { name: "确认删除" }));
  expect(api.confirmDeletion).toHaveBeenCalledWith(plan.plan_id, plan.fingerprint);
  expect(await screen.findByRole("status")).toHaveTextContent("删除完成");
  expect(onDeleted).toHaveBeenCalledTimes(1);
});
