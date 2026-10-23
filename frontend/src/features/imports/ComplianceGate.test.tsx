import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it } from "vitest";
import { ComplianceGate } from "./ComplianceGate";

it("首次使用未知悉时不能进入导入，并可再次查看提示", async () => {
  const user = userEvent.setup();
  render(<ComplianceGate><button>导入资料</button></ComplianceGate>);
  expect(screen.queryByRole("button", { name: "导入资料" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "继续使用" })).toBeDisabled();
  await user.click(screen.getByRole("checkbox", { name: "我已知悉" }));
  await user.click(screen.getByRole("button", { name: "继续使用" }));
  expect(screen.getByRole("button", { name: "导入资料" })).toBeVisible();
  await user.click(screen.getByRole("button", { name: "查看使用与合规提示" }));
  expect(screen.getByRole("dialog")).toBeVisible();
});

it("旧版提示确认不能跳过新版提示", () => {
  localStorage.setItem("minutes-compliance", "obsolete");
  render(<ComplianceGate><button>导入资料</button></ComplianceGate>);
  expect(screen.getByRole("dialog")).toBeVisible();
  expect(screen.queryByRole("button", { name: "导入资料" })).not.toBeInTheDocument();
});
