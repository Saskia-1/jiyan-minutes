import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { report, source, statusLabels } from "../../test/fixtures";
import { loadSubject } from "../../test/loadSubject";

const realReport = { ...report, evidence_class: "real_device" as const };

describe("来源状态不能串源或把合成运行当作真实验收", () => {
  for (const id of ["qq", "wechat"] as const) {
    for (const [status, label] of Object.entries(statusLabels)) {
      it(`${id} 单独显示 ${status}，另一来源保持 blocked`, async () => {
        const { SourceSetup } = await loadSubject("SourceSetup");
        const sources = [source("qq", "blocked"), source("wechat", "blocked")];
        sources[id === "qq" ? 0 : 1] = source(id, status as keyof typeof statusLabels);
        const reports = status === "ready" ? [{ ...realReport, source_id: id }] : [];
        render(<SourceSetup sources={sources} reports={reports} onSourceChange={vi.fn()} />);
        const current = screen.getByRole("region", { name: id === "qq" ? "QQ" : "微信" });
        const other = screen.getByRole("region", { name: id === "qq" ? "微信" : "QQ" });
        expect(within(current).getByRole("status")).toHaveTextContent(label);
        expect(within(other).getByRole("status")).toHaveTextContent("已阻止");
      });
    }
  }

  it("合成文件验证成功后仍显示未实测，并明确合成证据", async () => {
    const { SourceSetup } = await loadSubject("SourceSetup");
    render(<SourceSetup sources={[source("qq", "ready"), source("wechat", "blocked")]} reports={[report]} onSourceChange={vi.fn()} />);
    expect(within(screen.getByRole("region", { name: "QQ" })).getByRole("status")).toHaveTextContent("有文档，未实测");
    expect(screen.getByText(/合成测试/)).toBeVisible();
    expect(screen.queryByText("真实导出已验证")).not.toBeInTheDocument();
  });

  for (const evidence of ["unknown", "mock", "health", "documentation", ""] as const) {
    it(`${evidence || "missing"} 证据不能显示 ready`, async () => {
      const { SourceSetup } = await loadSubject("SourceSetup");
      const untrusted = { ...realReport, evidence_class: evidence };
      render(<SourceSetup sources={[source("qq", "ready")]} reports={[untrusted]} onSourceChange={vi.fn()} />);
      expect(screen.queryByText("真实导出已验证")).not.toBeInTheDocument();
      expect(screen.getByRole("status")).toHaveTextContent(/未实测|未验证/);
    });
  }

  for (const flaw of ["job_id", "client_version", "tool_version", "account_version", "state", "count", "range_start", "range_end", "timezone", "sha256", "hash_ok", "range_ok", "completeness", "error_code"] as const) {
    it(`real_device 的 ${flaw} 不完整或未绑定时不能显示 ready`, async () => {
      const { SourceSetup } = await loadSubject("SourceSetup");
      const untrusted = { ...realReport } as Record<string, unknown>;
      if (flaw.endsWith("_version")) untrusted[flaw] = "stale-version";
      else if (flaw === "state") untrusted[flaw] = "partial";
      else if (flaw === "completeness") untrusted[flaw] = "unknown";
      else if (flaw === "error_code") untrusted[flaw] = "unexpected_success_error";
      else if (flaw === "range_start") untrusted[flaw] = "not-an-instant";
      else if (flaw === "range_end") untrusted[flaw] = realReport.range_start;
      else if (flaw === "timezone") untrusted[flaw] = "not-a-timezone";
      else if (flaw === "job_id") untrusted[flaw] = "";
      else untrusted[flaw] = null;
      render(<SourceSetup sources={[source("qq", "ready")]} reports={[untrusted]} onSourceChange={vi.fn()} />);
      expect(screen.queryByText("真实导出已验证")).not.toBeInTheDocument();
      expect(screen.getByRole("status")).toHaveTextContent(/未实测|未验证/);
    });
  }

  it("ready 没有当前绑定报告时保持未验证", async () => {
    const { SourceSetup } = await loadSubject("SourceSetup");
    render(<SourceSetup sources={[source("qq", "ready")]} reports={[]} onSourceChange={vi.fn()} />);
    expect(screen.queryByText("真实导出已验证")).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(/未实测|未验证/);
  });

  it("同一来源有多份候选报告时不猜测哪份是当前绑定证据", async () => {
    const { SourceSetup } = await loadSubject("SourceSetup");
    render(<SourceSetup sources={[source("qq", "ready")]} reports={[
      realReport,
      { ...realReport, job_id: "newer-job", evidence_class: "unknown" },
    ]} onSourceChange={vi.fn()} />);
    expect(screen.queryByText("真实导出已验证")).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(/未实测|未验证/);
  });

  it("首屏可键盘选来源，不提供上传文件或危险操作按钮", async () => {
    const { SourceSetup } = await loadSubject("SourceSetup");
    const selected = vi.fn();
    const user = userEvent.setup();
    render(<SourceSetup sources={[source("qq"), source("wechat")]} reports={[]} onSourceChange={selected} />);
    const choice = screen.getByRole("button", { name: /选择 QQ/ });
    choice.focus();
    await user.keyboard("{Enter}");
    expect(selected).toHaveBeenCalledWith("qq");
    expect(document.querySelector('input[type="file"]')).toBeNull();
    expect(screen.queryByRole("button", { name: /安装|关闭 SIP|重签名|完全磁盘访问|登录客户端/ })).toBeNull();
  });

  it("显示当前版本和仅有的秘密保存状态，不显示秘密值", async () => {
    const { SourceSetup } = await loadSubject("SourceSetup");
    const configured = { ...source("qq"), secret_configured: true };
    render(<SourceSetup sources={[configured]} reports={[]} onSourceChange={vi.fn()} />);
    const card = screen.getByRole("region", { name: "QQ" });
    expect(card).toHaveTextContent("fixture-client-1");
    expect(card).toHaveTextContent("fixture-tool-1");
    expect(card).toHaveTextContent("fixture-account-1");
    expect(card).toHaveTextContent(/令牌状态.*已保存/);
    expect(card).not.toHaveTextContent(/synthetic-secret|Token 值/);
  });
});
