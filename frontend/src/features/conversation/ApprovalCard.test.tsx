import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { ApprovalCard } from "./ApprovalCard";
import type { AgentApproval } from "../../conversation-types";
const cached: AgentApproval = { approval_id: "cached", kind: "generation", preview_fingerprint: "a".repeat(64), provider: "configured", model: "synthetic", outbound_payloads: [], provider_calls: 0, max_output_tokens: 1000, expires_at: "2099-01-01T00:00:00Z" };
it("clearly confirms local cached reuse without implying external transmission", () => {
  const approve = vi.fn(); render(<ApprovalCard approval={cached} busy={false} onApprove={approve} />);
  expect(screen.getByRole("region", { name: "本地复用确认" })).toBeInTheDocument();
  expect(screen.getByText(/本次直接复用本机已有整理结果，不会向外部模型发送内容/)).toBeVisible();
  expect(screen.queryByText(/可能产生费用/)).not.toBeInTheDocument();
  const button = screen.getByRole("button", { name: "确认复用并继续" }); expect(button).toBeDisabled(); expect(approve).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("checkbox", { name: "我已知悉并确认复用本地结果" })); fireEvent.click(button); expect(approve).toHaveBeenCalledTimes(1);
});
it("retains full outbound consent for model calls", () => {
  render(<ApprovalCard approval={{ ...cached, approval_id: "external", kind: "agent", provider_calls: 1, outbound_payloads: ["完整脱敏payload"] }} busy={false} onApprove={() => {}} />);
  expect(screen.getByRole("region", { name: "外发授权" })).toBeInTheDocument(); expect(screen.getByText("完整脱敏payload")).toBeVisible();
  expect(screen.getByRole("checkbox", { name: "我已核对并授权本次外发" })).not.toBeChecked(); expect(screen.getByRole("button", { name: "授权并继续" })).toBeDisabled();
});
