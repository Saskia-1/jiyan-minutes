import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { sectionLabels, selection, setupOptions, source } from "../../test/fixtures";
import { loadSubject } from "../../test/loadSubject";

function props() {
  return {
    source: source("qq"), selection: { ...selection }, timezone: "Asia/Shanghai",
    disclosure: structuredClone(setupOptions.source_disclosures.qq),
    scopeFingerprint: "server-scope-1", consentChallenge: "opaque-consent-1", expiresAt: "2099-01-01T00:00:00Z",
    onConfirm: vi.fn(), onDecline: vi.fn(),
  };
}
async function acknowledge(user: ReturnType<typeof userEvent.setup>) {
  for (const title of Object.values(sectionLabels)) {
    await user.click(within(screen.getByRole("region", { name: title })).getByRole("checkbox"));
  }
}

it("显示服务端来源、工具、风险与不可编辑的 revision/hash seal", async () => {
  const { ConsentCard } = await loadSubject("ConsentCard");
  const value = props();
  render(<ConsentCard {...value} />);
  expect(screen.getByText(/QCE.*fixture-contract-1/)).toBeVisible();
  expect(screen.getByText(new RegExp(value.disclosure.hash))).toBeVisible();
  expect(screen.getByText("qce_local_history_may_be_incomplete")).toBeVisible();
  expect(screen.getByText(/确认有效期/)).toHaveTextContent(value.expiresAt);
});

for (const missing of ["source_id", "tool", "revision", "hash", "risks"] as const) {
  it(`披露 seal 缺少 ${missing} 时不能授权`, async () => {
    const { ConsentCard } = await loadSubject("ConsentCard");
    const value = props();
    if (missing === "risks") value.disclosure.risks = [];
    else (value.disclosure as Record<string, unknown>)[missing] = "";
    render(<ConsentCard {...value} />);
    await acknowledge(userEvent.setup());
    expect(screen.getByRole("button", { name: "确认并开始验证" })).toBeDisabled();
  });
}

for (const [key, title] of Object.entries(sectionLabels)) {
  it(`披露 ${key} 独立可访问且必须确认`, async () => {
    const { ConsentCard } = await loadSubject("ConsentCard");
    const value = props();
    const user = userEvent.setup();
    render(<ConsentCard {...value} />);
    const section = screen.getByRole("region", { name: title });
    expect(section).toHaveTextContent(value.disclosure.sections[key as keyof typeof sectionLabels]);
    for (const other of Object.values(sectionLabels).filter(item => item !== title)) {
      await user.click(within(screen.getByRole("region", { name: other })).getByRole("checkbox"));
    }
    expect(screen.getByRole("button", { name: "确认并开始验证" })).toBeDisabled();
    await user.click(within(section).getByRole("checkbox"));
    expect(screen.getByRole("button", { name: "确认并开始验证" })).toBeEnabled();
  });
}

for (const missing of [...Object.keys(selection), "timezone", "scopeFingerprint", "client_version", "tool_version", "account_version"]) {
  it(`缺少 ${missing} 不能授权`, async () => {
    const { ConsentCard } = await loadSubject("ConsentCard");
    const value = props();
    if (missing === "timezone" || missing === "scopeFingerprint") value[missing] = "";
    else if (missing.endsWith("_version")) (value.source as Record<string, unknown>)[missing] = "";
    else (value.selection as Record<string, string>)[missing] = "";
    render(<ConsentCard {...value} />);
    await acknowledge(userEvent.setup());
    expect(screen.getByRole("button", { name: "确认并开始验证" })).toBeDisabled();
  });
}

for (const change of [...Object.keys(selection), "timezone", "client_version", "tool_version", "account_version", "scopeFingerprint", "consentChallenge", "expiresAt", "disclosureRevision", "disclosureHash"]) {
  it(`${change} 变化后清空全部确认`, async () => {
    const { ConsentCard } = await loadSubject("ConsentCard");
    const value = props();
    const user = userEvent.setup();
    const view = render(<ConsentCard {...value} />);
    await acknowledge(user);
    expect(screen.getByRole("button", { name: "确认并开始验证" })).toBeEnabled();
    const changed = { ...value, selection: { ...value.selection }, source: { ...value.source }, disclosure: { ...value.disclosure } };
    if (change === "timezone") changed.timezone = "UTC";
    else if (change === "scopeFingerprint") changed.scopeFingerprint = "server-scope-after-reconfiguration";
    else if (change === "consentChallenge") changed.consentChallenge = "opaque-consent-2";
    else if (change === "expiresAt") changed.expiresAt = "2099-01-02T00:00:00Z";
    else if (change === "disclosureRevision") changed.disclosure.revision = "fixture-contract-2";
    else if (change === "disclosureHash") changed.disclosure.hash = "f".repeat(64);
    else if (change.endsWith("_version")) (changed.source as Record<string, unknown>)[change] = "changed-version";
    else (changed.selection as Record<string, string>)[change] = "changed-selection";
    view.rerender(<ConsentCard {...changed} />);
    for (const checkbox of screen.getAllByRole("checkbox")) expect(checkbox).not.toBeChecked();
    expect(screen.getByRole("button", { name: "确认并开始验证" })).toBeDisabled();
    expect(value.onConfirm).not.toHaveBeenCalled();
  });
}

it("拒绝显示阻止状态并且不触发确认或备用来源", async () => {
  const { ConsentCard } = await loadSubject("ConsentCard");
  const value = props();
  render(<ConsentCard {...value} />);
  await userEvent.setup().click(screen.getByRole("button", { name: "拒绝本次操作" }));
  expect(value.onDecline).toHaveBeenCalledTimes(1);
  expect(value.onConfirm).not.toHaveBeenCalled();
  expect(screen.getByRole("status")).toHaveTextContent(/已阻止|已拒绝/);
  expect(screen.getByRole("button", { name: "确认并开始验证" })).toBeDisabled();
});

for (const [id, blockers] of [
  ["qq", ["QCE 复制并修改、重签名独立 QQ 客户端，与当前边界冲突"]],
  ["wechat", ["TraceMemo SIP / 管理员 / 内存读取路线被阻止", "客户端版本未实测；发布完整性与许可证明未确认"]],
] as const) {
  it(`${id} 披露路线阻碍，勾选也不允许突破边界`, async () => {
    const { ConsentCard } = await loadSubject("ConsentCard");
    const value = props();
    value.source = source(id, "blocked");
    value.disclosure.blockers = [...blockers];
    render(<ConsentCard {...value} />);
    for (const blocker of blockers) expect(screen.getByText(blocker)).toBeVisible();
    await acknowledge(userEvent.setup());
    expect(screen.getByRole("button", { name: "确认并开始验证" })).toBeDisabled();
  });
}

for (const invalid of ["range", "timezone", "missing_disclosure"] as const) {
  it(`不接受 ${invalid} 不完整披露或错误范围`, async () => {
    const { ConsentCard } = await loadSubject("ConsentCard");
    const value = props();
    if (invalid === "range") value.selection.range_end = value.selection.range_start;
    if (invalid === "timezone") value.timezone = "Mars/Olympus";
    if (invalid === "missing_disclosure") value.disclosure.sections.storage_cleanup = "";
    render(<ConsentCard {...value} />);
    for (const checkbox of screen.queryAllByRole("checkbox")) await userEvent.setup().click(checkbox);
    expect(screen.getByRole("button", { name: "确认并开始验证" })).toBeDisabled();
  });
}
