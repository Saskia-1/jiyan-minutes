import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { installApi, sectionLabels, selection, setupOptions } from "../../test/fixtures";
import { loadSubject } from "../../test/loadSubject";

async function mount() {
  const { VerificationWizard } = await loadSubject("VerificationWizard");
  const api = installApi();
  vi.stubGlobal("fetch", api.fetch);
  const user = userEvent.setup();
  render(<VerificationWizard setupOptions={setupOptions} />);
  await screen.findByRole("combobox", { name: "数据源" });
  return { api, user };
}
async function selectScope(user: ReturnType<typeof userEvent.setup>) {
  await user.selectOptions(screen.getByRole("combobox", { name: "数据源" }), "qq");
  await user.selectOptions(await screen.findByRole("combobox", { name: "来源账号" }), "synthetic-account");
  await user.click(screen.getByRole("button", { name: "读取本机会话列表" }));
  await finishScope(user);
}
async function finishScope(user: ReturnType<typeof userEvent.setup>) {
  await user.selectOptions(await screen.findByRole("combobox", { name: "聊天会话" }), "opaque-selection-B");
  await user.selectOptions(screen.getByRole("combobox", { name: "显示时区" }), "Asia/Shanghai");
  // datetime-local picker commits complete values; Chrome E2E covers real input.
  fireEvent.change(screen.getByLabelText("开始时间"), { target: { value: "2026-09-01T08:00:00" } });
  fireEvent.change(screen.getByLabelText("结束时间（不包含）"), { target: { value: "2026-09-01T08:00:02" } });
  await user.selectOptions(screen.getByRole("combobox", { name: "保存位置" }), "fixture-root");
}
async function consent(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: "查看操作确认" }));
  for (const title of Object.values(sectionLabels)) {
    await user.click(within(await screen.findByRole("region", { name: title })).getByRole("checkbox"));
  }
}
function jobResponse(state: string, overrides: Record<string, unknown> = {}) {
  return new Response(JSON.stringify({
    ...installApi().state.report, state, count: null, sha256: null, hash_ok: null, range_ok: null,
    completeness: null, error_code: null, ...overrides,
  }), { status: 200, headers: { "Content-Type": "application/json" } });
}
async function startDelayedJob(api: ReturnType<typeof installApi>, user: ReturnType<typeof userEvent.setup>) {
  let finish!: (response: Response) => void;
  api.state.report = { ...api.state.report, state: "connecting", count: null, sha256: null, hash_ok: null, range_ok: null, completeness: null, error_code: null };
  api.state.delayJob = new Promise(resolve => { finish = resolve; });
  await selectScope(user); await consent(user);
  await user.click(screen.getByRole("button", { name: "确认并开始验证" }));
  await waitFor(() => expect(api.state.jobReads).toBe(1));
  return finish;
}

it("生成预览进行中锁定外层来源和时间范围，完成后解锁", async () => {
  const { api, user } = await mount();
  await selectScope(user);
  const workspace = screen.getByRole("region", { name: "导出与本地搜索" });
  await user.click(within(workspace).getAllByRole("checkbox", { name: /选择 同名合成群 标识/ })[0]);
  await user.click(within(workspace).getByRole("button", { name: "搜索本地聊天" }));
  await within(workspace).findByText("生成锁测试消息");

  let finishPreview!: (response: Response) => void;
  api.state.delayGenerationPreview = new Promise(resolve => { finishPreview = resolve; });
  await user.type(within(workspace).getByRole("textbox", { name: "操作主题" }), "target");
  await user.click(within(workspace).getByRole("button", { name: "预览 AI 外发" }));
  await waitFor(() => expect(api.requests.filter(item => item.path === "/api/generation/previews/from-selections")).toHaveLength(1));

  expect(screen.getByRole("combobox", { name: "数据源" })).toBeDisabled();
  expect(screen.getByRole("combobox", { name: "来源账号" })).toBeDisabled();
  expect(screen.getByRole("combobox", { name: "显示时区" })).toBeDisabled();
  expect(screen.getByLabelText("开始时间")).toBeDisabled();
  expect(screen.getByRole("button", { name: "读取本机会话列表" })).toBeDisabled();

  finishPreview(new Response(JSON.stringify({
    preview_id: "preview:p2", scope_fingerprint: "a".repeat(64), provider: "fake", model: "deterministic",
    thinking: "disabled", reasoning_effort: null,
    mode: "complete", message_count: 1, character_count: 6, estimated_input_tokens: 20,
    max_output_tokens: 2000, provider_calls: 1, price_status: "unknown", estimated_cost: null,
    cache_hit: false, will_send_external: true, outbound_payloads: ["{}"], expires_at: "2099-01-01T00:00:00Z",
  }), { status: 201, headers: { "Content-Type": "application/json" } }));
  await within(workspace).findByRole("region", { name: "AI 外发预览" });
  await waitFor(() => expect(screen.getByRole("combobox", { name: "数据源" })).toBeEnabled());
});

it("未配置来源只在明确点击后保存固定 loopback 与版本元数据，再刷新状态", async () => {
  const { VerificationWizard } = await loadSubject("VerificationWizard");
  const api = installApi();
  api.state.sources[0] = { ...api.state.sources[0], account_id: null, client_version: null, tool_version: null, account_version: null };
  vi.stubGlobal("fetch", api.fetch);
  const user = userEvent.setup();
  render(<VerificationWizard setupOptions={setupOptions} />);
  await user.selectOptions(await screen.findByRole("combobox", { name: "数据源" }), "qq");

  expect(within(screen.getByRole("region", { name: "本机来源配置" })).getByText("http://127.0.0.1:40653")).toBeVisible();
  await user.type(screen.getByLabelText("来源账号标识"), "local-account");
  await user.type(screen.getByLabelText("QQ 客户端版本"), "qq-client-1");
  await user.type(screen.getByLabelText("本地工具版本"), "qce-tool-1");
  await user.type(screen.getByLabelText("账号数据版本"), "account-data-1");
  await user.click(screen.getByRole("button", { name: "保存本机来源配置" }));

  const configuration = api.requests.find(item => item.path === "/api/sources" && item.method === "POST");
  expect(configuration?.body).toEqual({
    source_id: "qq", account_id: "local-account", base_url: "http://127.0.0.1:40653",
    client_version: "qq-client-1", tool_version: "qce-tool-1", account_version: "account-data-1",
  });
  expect(configuration?.headers.get("X-CSRF-Token")).toMatch(/^synthetic-csrf-/);
  expect(await screen.findByRole("option", { name: "local-account" })).toBeVisible();
  expect(screen.queryByRole("button", { name: /安装|登录|提权|关闭 SIP|降低系统保护|重签名/ })).not.toBeInTheDocument();
});

it("来源配置提交成功但状态刷新失败时不把已提交误报为未保存", async () => {
  const { VerificationWizard } = await loadSubject("VerificationWizard");
  const api = installApi();
  api.state.sources[0] = { ...api.state.sources[0], account_id: null, client_version: null, tool_version: null, account_version: null };
  vi.stubGlobal("fetch", api.fetch);
  const user = userEvent.setup();
  render(<VerificationWizard setupOptions={setupOptions} />);
  await user.selectOptions(await screen.findByRole("combobox", { name: "数据源" }), "qq");
  await user.type(screen.getByLabelText("来源账号标识"), "local-account");
  await user.type(screen.getByLabelText("QQ 客户端版本"), "qq-client-1");
  await user.type(screen.getByLabelText("本地工具版本"), "qce-tool-1");
  await user.type(screen.getByLabelText("账号数据版本"), "account-data-1");
  api.state.delaySources = Promise.resolve(new Response('{"code":"refresh_failed"}', { status: 500, headers: { "Content-Type": "application/json" } }));
  await user.click(screen.getByRole("button", { name: "保存本机来源配置" }));

  expect(await screen.findByText(/配置已保存.*状态刷新失败/)).toBeVisible();
  expect(screen.queryByText(/配置未保存/)).not.toBeInTheDocument();
  expect(api.requests.filter(item => item.path === "/api/sources" && item.method === "POST")).toHaveLength(1);
});

it("展示后端安全检测的系统与应用版本，并优先预填可检测版本", async () => {
  const { VerificationWizard } = await loadSubject("VerificationWizard");
  const api = installApi();
  api.state.sources[0] = { ...api.state.sources[0], account_id: null, client_version: null, tool_version: null, account_version: null };
  api.state.environment.applications.qq = { version: "9.9.20", architecture: "arm64", installed: true };
  api.state.environment.applications.qce = { version: "0.4.2", architecture: "arm64", installed: true };
  vi.stubGlobal("fetch", api.fetch);
  const user = userEvent.setup();
  render(<VerificationWizard setupOptions={setupOptions} />);

  const audit = await screen.findByRole("region", { name: "本机安全检测" });
  expect(audit).toHaveTextContent(/Darwin.*macOS fixture.*arm64/);
  expect(audit).toHaveTextContent(/QQ.*9\.9\.20.*arm64/);
  expect(audit).toHaveTextContent(/QCE.*0\.4\.2.*arm64/);
  expect(audit.textContent).not.toMatch(/\/Applications|\/Users|序列号|UUID/);
  await user.selectOptions(screen.getByRole("combobox", { name: "数据源" }), "qq");
  expect(screen.getByLabelText("QQ 客户端版本")).toHaveValue("9.9.20");
  expect(screen.getByLabelText("本地工具版本")).toHaveValue("0.4.2");
  expect(screen.getByLabelText("账号数据版本")).toHaveValue("");
});

it("安全检测较晚返回时只填空白版本，不覆盖用户已输入的版本", async () => {
  const { VerificationWizard } = await loadSubject("VerificationWizard");
  const api = installApi();
  api.state.sources[0] = { ...api.state.sources[0], account_id: null, client_version: null, tool_version: null, account_version: null };
  api.state.environment.applications.qq = { version: "9.9.20", architecture: "arm64", installed: true };
  api.state.environment.applications.qce = { version: "0.4.2", architecture: "arm64", installed: true };
  let finishEnvironment!: (response: Response) => void;
  api.state.delayEnvironment = new Promise(resolve => { finishEnvironment = resolve; });
  vi.stubGlobal("fetch", api.fetch);
  const user = userEvent.setup();
  render(<VerificationWizard setupOptions={setupOptions} />);
  await user.selectOptions(await screen.findByRole("combobox", { name: "数据源" }), "qq");
  await user.type(screen.getByLabelText("QQ 客户端版本"), "manual-client-version");

  finishEnvironment(new Response(JSON.stringify(api.state.environment), { status: 200, headers: { "Content-Type": "application/json" } }));
  await screen.findByRole("region", { name: "本机安全检测" });
  expect(screen.getByLabelText("QQ 客户端版本")).toHaveValue("manual-client-version");
  await waitFor(() => expect(screen.getByLabelText("本地工具版本")).toHaveValue("0.4.2"));
});

it("对话首页共享一个 session，默认不连接 QQ/微信来源", async () => {
  const { default: App } = await loadSubject("App");
  const api = installApi(); vi.stubGlobal("fetch", api.fetch);
  render(<App />);
  const acknowledgment = userEvent.setup();
  await acknowledgment.click(screen.getByRole("checkbox", { name: "我已知悉" }));
  await acknowledgment.click(screen.getByRole("button", { name: "继续使用" }));
  await screen.findByRole("textbox", { name: "发送消息" });
  await waitFor(() => expect(api.requests.filter(item => item.path === "/api/agent/conversations" && item.method === "GET")).toHaveLength(1));
  expect(api.requests.filter(item => item.path === "/api/imports/recovery")).toHaveLength(0);
  expect(api.requests.filter(item => item.path === "/api/sources")).toHaveLength(0);
  expect(api.requests.filter(item => item.path === "/api/session")).toHaveLength(1);
});

it("默认给出可编辑的最近七个上海自然日建议", async () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-09-11T04:00:00Z"));
  try {
    const { VerificationWizard } = await loadSubject("VerificationWizard");
    const api = installApi(); vi.stubGlobal("fetch", api.fetch);
    render(<VerificationWizard setupOptions={setupOptions} />);
    const start = screen.getByLabelText("开始时间") as HTMLInputElement;
    const end = screen.getByLabelText("结束时间（不包含）") as HTMLInputElement;
    expect(start.value).toBe("2026-09-05T00:00");
    expect(end.value).toBe("2026-09-12T00:00");
    fireEvent.change(start, { target: { value: "2026-09-06T09:30:00" } });
    expect(start.value).toBe("2026-09-06T09:30");
  } finally {
    vi.useRealTimers();
  }
});

it("重名会话使用类型和不泄漏原始 handle 的稳定短标识区分", async () => {
  const { user } = await mount();
  await user.selectOptions(screen.getByRole("combobox", { name: "数据源" }), "qq");
  await user.selectOptions(await screen.findByRole("combobox", { name: "来源账号" }), "synthetic-account");
  await user.click(screen.getByRole("button", { name: "读取本机会话列表" }));
  const options = within(screen.getByRole("combobox", { name: "聊天会话" })).getAllByRole("option").filter(option => option.textContent?.includes("同名合成群"));
  expect(options).toHaveLength(2);
  expect(options[0]).toHaveTextContent(/群聊.*标识 [0-9a-f]{8}/);
  expect(options[1]).toHaveTextContent(/群聊.*标识 [0-9a-f]{8}/);
  expect(options[0].textContent).not.toBe(options[1].textContent);
  expect(options.map(option => option.textContent).join(" ")).not.toContain("opaque-selection-");
});

it("选择来源和账号不会自动访问本地工具，只有明确点击才读取会话元数据", async () => {
  const { api, user } = await mount();
  await user.selectOptions(screen.getByRole("combobox", { name: "数据源" }), "qq");
  await user.selectOptions(await screen.findByRole("combobox", { name: "来源账号" }), "synthetic-account");
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });
  expect(api.requests.filter(item => item.path.endsWith("/conversations"))).toHaveLength(0);

  await user.click(screen.getByRole("button", { name: "读取本机会话列表" }));
  await waitFor(() => expect(api.requests.filter(item => item.path.endsWith("/conversations"))).toHaveLength(1));
  expect(await screen.findAllByRole("option", { name: /同名合成群.*标识/ })).toHaveLength(2);
});

it("按真实 connecting/reading/saving/verifying 阶段显示，并把 null 与错误代码如实呈现", async () => {
  const { api, user } = await mount();
  const finish = await startDelayedJob(api, user);
  api.state.reportQueue = [
    { ...api.state.report, state: "reading", count: null, sha256: null, hash_ok: null, range_ok: null, completeness: null, error_code: null },
    { ...api.state.report, state: "saving", count: null, sha256: null, hash_ok: null, range_ok: null, completeness: null, error_code: null },
    { ...api.state.report, state: "verifying", count: null, sha256: null, hash_ok: null, range_ok: null, completeness: null, error_code: null },
    { ...api.state.report, state: "failed", count: null, sha256: null, hash_ok: null, range_ok: null, completeness: null, error_code: "token_expired" },
  ];
  vi.useFakeTimers();
  try {
    finish(jobResponse("connecting"));
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    const result = () => within(screen.getByRole("region", { name: "验证结果" }));
    expect(result().getByRole("status")).toHaveTextContent("正在连接");
    await act(async () => { await vi.advanceTimersByTimeAsync(350); });
    expect(result().getByRole("status")).toHaveTextContent("正在读取");
    await act(async () => { await vi.advanceTimersByTimeAsync(700); });
    expect(result().getByRole("status")).toHaveTextContent("正在保存");
    await act(async () => { await vi.advanceTimersByTimeAsync(1_400); });
    expect(result().getByRole("status")).toHaveTextContent("正在核对");
    await act(async () => { await vi.advanceTimersByTimeAsync(2_800); });
    expect(result().getByRole("status")).toHaveTextContent("失败");
    expect(result().getByText(/哈希核对：未核对/)).toBeVisible();
    expect(result().getByText(/范围核对：未核对/)).toBeVisible();
    expect(result().getByText(/完整性：未核对/)).toBeVisible();
    expect(result().getByText(/错误代码：token_expired/)).toBeVisible();
  } finally { vi.clearAllTimers(); vi.useRealTimers(); }
});

it("启动响应后立即显示等待授权，不在首次轮询前伪造证据", async () => {
  const { api, user } = await mount();
  const finish = await startDelayedJob(api, user);
  const result = within(screen.getByRole("region", { name: "验证结果" }));
  expect(result.getByRole("status")).toHaveTextContent("等待授权");
  expect(result.getByText(/记录数：未知/)).toBeVisible();
  expect(result.getByText(/哈希核对：未核对/)).toBeVisible();
  expect(result.getByText(/错误代码：未提供/)).toBeVisible();
  finish(jobResponse("cancelled", { error_code: "cancelled" }));
});

it("非终态轮询按有限退避次数暂停，保留任务并继续锁定原作用域", async () => {
  const { api, user } = await mount();
  const finish = await startDelayedJob(api, user);
  vi.useFakeTimers();
  try {
    finish(jobResponse("connecting"));
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    await act(async () => { await vi.advanceTimersByTimeAsync(21_250); });
    expect(api.state.jobReads).toBe(9);
    expect(screen.getByRole("button", { name: "重新查询任务" })).toBeVisible();
    expect(screen.getByRole("button", { name: "取消当前任务" })).toBeVisible();
    expect(screen.getByLabelText("开始时间")).toBeDisabled();
    expect(screen.getByRole("button", { name: "选择 微信" })).toBeDisabled();
  } finally { vi.clearAllTimers(); vi.useRealTimers(); }
});

it("用户取消只调用一次 cancel，停止旧轮询并显示持久化取消状态", async () => {
  const { api, user } = await mount();
  api.state.report = { ...api.state.report, state: "connecting", count: null, sha256: null, hash_ok: null, range_ok: null, completeness: null, error_code: null };
  await selectScope(user); await consent(user);
  await user.click(screen.getByRole("button", { name: "确认并开始验证" }));
  expect(await within(await screen.findByRole("region", { name: "验证结果" })).findByRole("status")).toHaveTextContent("正在连接");
  await user.click(screen.getByRole("button", { name: "取消当前任务" }));
  await waitFor(() => expect(within(screen.getByRole("region", { name: "验证结果" })).getByRole("status")).toHaveTextContent("已取消"));
  expect(api.requests.filter(item => item.path.endsWith("/cancel"))).toHaveLength(1);
});

it("终态结果不会在用户改动时间后继续挂在新范围下", async () => {
  const { user } = await mount();
  await selectScope(user); await consent(user);
  await user.click(screen.getByRole("button", { name: "确认并开始验证" }));
  expect(await screen.findByRole("region", { name: "验证结果" })).toBeVisible();
  fireEvent.change(screen.getByLabelText("开始时间"), { target: { value: "2026-09-01T08:00:01" } });
  expect(screen.queryByRole("region", { name: "验证结果" })).not.toBeInTheDocument();
});

it("终态报告后刷新来源权威状态，只在当前绑定真机证据完整时显示 ready", async () => {
  const { api, user } = await mount();
  const finish = await startDelayedJob(api, user);
  api.state.sources[0] = { ...api.state.sources[0], status: "ready" };
  finish(jobResponse("complete", {
    evidence_class: "real_device", count: 2, sha256: "b".repeat(64), hash_ok: true,
    range_ok: true, completeness: "source_range_verified", error_code: null,
  }));

  await waitFor(() => expect(api.requests.filter(item => item.path === "/api/sources")).toHaveLength(2));
  expect(within(screen.getByRole("region", { name: "QQ" })).getByRole("status")).toHaveTextContent("真实导出已验证");
});

it("终态后的权威刷新未完成或失败时不能复用旧 ready 状态", async () => {
  const { api, user } = await mount();
  api.state.sources[0] = { ...api.state.sources[0], status: "ready" };
  await user.click(screen.getByRole("button", { name: "刷新数据源" }));
  await waitFor(() => expect(within(screen.getByRole("region", { name: "QQ" })).getByRole("status")).toHaveTextContent(/未实测|未验证/));
  const finishJob = await startDelayedJob(api, user);
  let finishAuthority!: (response: Response) => void;
  api.state.delaySources = new Promise(resolve => { finishAuthority = resolve; });
  finishJob(jobResponse("complete", {
    evidence_class: "real_device", count: 2, sha256: "b".repeat(64), hash_ok: true,
    range_ok: true, completeness: "source_range_verified", error_code: null,
  }));

  const result = within(screen.getByRole("region", { name: "验证结果" }));
  await waitFor(() => expect(result.getByText(/阶段：完成/)).toBeVisible());
  expect(result.queryByText("真实设备导出验证完成")).not.toBeInTheDocument();
  expect(within(screen.getByRole("region", { name: "QQ" })).queryByText("真实导出已验证")).not.toBeInTheDocument();

  finishAuthority(new Response(JSON.stringify({ sources: api.state.sources }), { status: 200, headers: { "Content-Type": "application/json" } }));
  expect(await result.findByText("真实设备导出验证完成")).toBeVisible();
  expect(within(screen.getByRole("region", { name: "QQ" })).getByText("真实导出已验证")).toBeVisible();
});

it("真机形式的报告未被后端权威来源状态接受时不宣称真实验收", async () => {
  const { api, user } = await mount();
  const finish = await startDelayedJob(api, user);
  finish(jobResponse("complete", {
    evidence_class: "real_device", count: 2, sha256: "b".repeat(64), hash_ok: true,
    range_ok: true, completeness: "source_range_verified", error_code: null,
  }));

  await waitFor(() => expect(api.requests.filter(item => item.path === "/api/sources")).toHaveLength(2));
  const result = within(screen.getByRole("region", { name: "验证结果" }));
  expect(result.getByRole("status")).toHaveTextContent("证据未达到真实来源验收标准");
  expect(result.queryByText("真实设备导出验证完成")).not.toBeInTheDocument();
});

it("来源 ready 也不能让范围或时区未绑定当前任务的真机报告通过", async () => {
  const { api, user } = await mount();
  const finish = await startDelayedJob(api, user);
  api.state.sources[0] = { ...api.state.sources[0], status: "ready" };
  finish(jobResponse("complete", {
    evidence_class: "real_device", count: 2, sha256: "b".repeat(64), hash_ok: true,
    range_ok: true, completeness: "source_range_verified", error_code: null,
    range_start: "2026-09-01T00:00:01.000Z", timezone: "UTC",
  }));

  await waitFor(() => expect(api.requests.filter(item => item.path === "/api/sources")).toHaveLength(2));
  expect(within(screen.getByRole("region", { name: "验证结果" })).getByRole("status")).toHaveTextContent("证据未达到真实来源验收标准");
  expect(within(screen.getByRole("region", { name: "QQ" })).getByRole("status")).not.toHaveTextContent("真实导出已验证");
});

it("来源刷新只接受最新响应，迟到的旧版本不能覆盖", async () => {
  const { VerificationWizard } = await loadSubject("VerificationWizard");
  const api = installApi();
  const oldSources = structuredClone(api.state.sources);
  let finishOld!: (response: Response) => void;
  api.state.delaySources = new Promise(resolve => { finishOld = resolve; });
  vi.stubGlobal("fetch", api.fetch);
  const user = userEvent.setup();
  render(<VerificationWizard setupOptions={setupOptions} />);
  await waitFor(() => expect(api.requests.some(item => item.path === "/api/sources")).toBe(true));
  api.state.sources[0] = { ...api.state.sources[0], client_version: "newest-client" };
  await user.click(screen.getByRole("button", { name: "刷新数据源" }));
  await waitFor(() => expect(api.requests.filter(item => item.path === "/api/sources")).toHaveLength(2));
  expect(await within(screen.getByRole("region", { name: "QQ" })).findByText("newest-client")).toBeVisible();
  finishOld(new Response(JSON.stringify({ sources: oldSources }), { status: 200, headers: { "Content-Type": "application/json" } }));
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });
  expect(within(screen.getByRole("region", { name: "QQ" })).getByText("newest-client")).toBeVisible();
});

it("选择第二个同名会话只提交 opaque 六字段和时区；nonce/版本绑定留在服务端", async () => {
  const { api, user } = await mount();
  await selectScope(user);
  expect(api.requests.filter(item => item.path.endsWith("/verification-jobs"))).toHaveLength(0);
  await consent(user);
  const prepared = api.requests.find(item => item.path.endsWith("/verification-consents"));
  const preparedBody = prepared?.body!;
  expect(Object.keys(preparedBody).sort()).toEqual(["selection", "timezone"]);
  expect(Object.keys(preparedBody.selection).sort()).toEqual(Object.keys(selection).sort());
  expect(preparedBody.selection).toMatchObject({ source_id: "qq", account_id: "synthetic-account", conversation_selection_id: "opaque-selection-B", output_root_id: "fixture-root" });
  expect(Date.parse(preparedBody.selection.range_start)).toBe(1788220800000);
  expect(Date.parse(preparedBody.selection.range_end)).toBe(1788220802000);
  expect(preparedBody.timezone).toBe("Asia/Shanghai");
  await user.click(screen.getByRole("button", { name: "确认并开始验证" }));
  expect(await within(screen.getByRole("region", { name: "验证结果" })).findByText(/本次仅有合成证据/)).toBeVisible();
  const job = api.requests.find(item => item.path.endsWith("/verification-jobs"));
  expect(job?.body).toEqual({ ...preparedBody, consent_challenge: "opaque-consent-1", confirmed: true,
    disclosure_revision: setupOptions.source_disclosures.qq.revision,
    disclosure_hash: setupOptions.source_disclosures.qq.hash });
  expect(job?.headers.get("X-CSRF-Token")).toMatch(/^synthetic-csrf-/);
  expect(screen.queryByText("真实导出已验证")).toBeNull();
  expect(document.body.textContent).not.toContain("configuration_nonce");
  expect(document.body.textContent).not.toContain("opaque-selection-B");
});

it("后端因 nonce/版本失效返回 409 后必须重新授权，不自动重试", async () => {
  const { api, user } = await mount();
  await selectScope(user); await consent(user);
  api.state.jobStatus = 409;
  await user.click(screen.getByRole("button", { name: "确认并开始验证" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(/重新确认|已变化/);
  expect(api.requests.filter(item => item.path.endsWith("/verification-jobs"))).toHaveLength(1);
  for (const checkbox of screen.queryAllByRole("checkbox")) expect(checkbox).not.toBeChecked();
  const confirm = screen.queryByRole("button", { name: "确认并开始验证" });
  if (confirm) expect(confirm).toBeDisabled();
});

for (const version of ["client_version", "tool_version", "account_version"] as const) {
  it(`刷新发现 ${version} 改变后原勾选失效`, async () => {
    const { api, user } = await mount();
    await selectScope(user); await consent(user);
    api.state.sources[0] = { ...api.state.sources[0], [version]: "changed-version" };
    await user.click(screen.getByRole("button", { name: "刷新数据源" }));
    await waitFor(() => {
      for (const checkbox of screen.queryAllByRole("checkbox")) expect(checkbox).not.toBeChecked();
    });
    expect(api.requests.filter(item => item.path.endsWith("/verification-jobs"))).toHaveLength(0);
  });
}

it("已保存 Token 必须先显式完成一次性 secret_get 授权，才可准备验证", async () => {
  const { api, user } = await mount();
  api.state.sources[0] = { ...api.state.sources[0], secret_configured: true };
  await user.click(screen.getByRole("button", { name: "刷新数据源" }));
  await waitFor(() => expect(within(screen.getByRole("region", { name: "QQ" })).getByText("已保存")).toBeVisible());
  await user.selectOptions(screen.getByRole("combobox", { name: "数据源" }), "qq");
  await user.selectOptions(await screen.findByRole("combobox", { name: "来源账号" }), "synthetic-account");
  await user.click(screen.getByRole("button", { name: "查看下一次 Token 使用确认" }));
  await user.click(await screen.findByRole("checkbox", { name: "授权本次读取已保存 Token" }));
  await user.click(screen.getByRole("button", { name: "授权本次使用 Token" }));
  await user.click(screen.getByRole("button", { name: "读取本机会话列表" }));
  await finishScope(user);

  expect(screen.getByRole("button", { name: "查看操作确认" })).toBeDisabled();
  await user.click(screen.getByRole("button", { name: "查看下一次 Token 使用确认" }));
  expect(await screen.findByText(/操作：读取已保存 Token/)).toBeVisible();
  expect(screen.getByText(/工具：QCE.*操作：读取已保存 Token/)).toBeVisible();
  expect(screen.queryByLabelText("上游 API Token")).not.toBeInTheDocument();
  await user.click(screen.getByRole("checkbox", { name: "授权本次读取已保存 Token" }));
  await user.click(screen.getByRole("button", { name: "授权本次使用 Token" }));

  await waitFor(() => expect(screen.getByRole("button", { name: "查看操作确认" })).toBeEnabled());
  expect(screen.getByText(/下一次 Token 使用授权有效期/)).toBeVisible();
  const chain = api.requests.filter(item => /\/secret-(consents|operations)$/.test(item.path)).slice(-2);
  expect(chain.map(item => [item.method, item.path])).toEqual([
    ["POST", "/api/sources/qq/secret-consents"],
    ["POST", "/api/sources/qq/secret-operations"],
  ]);
  expect(chain[0].body).toEqual({ operation: "secret_get", account_id: "synthetic-account" });
  expect(chain[1].body).toEqual({
    operation: "secret_get", account_id: "synthetic-account", consent_challenge: api.secretConsents[1].challenge,
    confirmed: true, disclosure_revision: api.secretConsents[1].disclosure_revision,
    disclosure_hash: api.secretConsents[1].disclosure_hash,
  });
  expect(api.secretConsents[1].consumed).toBe(true);
});

it("已保存 Token 的列会话操作先授权并在读取后要求新的导出授权", async () => {
  const { api, user } = await mount();
  api.state.sources[0] = { ...api.state.sources[0], secret_configured: true };
  await user.click(screen.getByRole("button", { name: "刷新数据源" }));
  await user.selectOptions(screen.getByRole("combobox", { name: "数据源" }), "qq");
  await user.selectOptions(await screen.findByRole("combobox", { name: "来源账号" }), "synthetic-account");

  const listButton = screen.getByRole("button", { name: "读取本机会话列表" });
  expect(listButton).toBeDisabled();
  await user.click(screen.getByRole("button", { name: "查看下一次 Token 使用确认" }));
  await user.click(await screen.findByRole("checkbox", { name: "授权本次读取已保存 Token" }));
  await user.click(screen.getByRole("button", { name: "授权本次使用 Token" }));
  await waitFor(() => expect(listButton).toBeEnabled());

  await user.click(listButton);
  await waitFor(() => expect(api.requests.filter(item => item.path.endsWith("/conversations"))).toHaveLength(1));
  expect(listButton).toBeDisabled();
  expect(screen.getByRole("button", { name: "查看操作确认" })).toBeDisabled();
  expect(screen.getByText(/列出会话已消费本次 Token 授权/)).toBeVisible();
});

it("断开来源有独立二次确认且明示不删除已导入数据", async () => {
  const { api, user } = await mount();
  await user.selectOptions(screen.getByRole("combobox", { name: "数据源" }), "qq");
  await user.click(screen.getByRole("button", { name: "准备断开本机来源" }));
  expect(screen.getByText(/不删除已导入的聊天快照/)).toBeVisible();
  await user.click(screen.getByRole("checkbox", { name: /确认忘记这个来源配置/ }));
  await user.click(screen.getByRole("button", { name: "确认断开本机来源" }));
  await waitFor(() => expect(api.requests.filter(item => item.path === "/api/sources/qq/disconnect")).toHaveLength(1));
  expect(api.requests.find(item => item.path.endsWith("/disconnect"))?.body).toEqual({ confirmed: true });
  expect(await screen.findByText(/已断开本机来源配置/)).toBeVisible();
});

it("断开前必须通过独立 secret_delete 同意流删除已保存 Token", async () => {
  const { api, user } = await mount();
  api.state.sources[0] = { ...api.state.sources[0], secret_configured: true };
  await user.click(screen.getByRole("button", { name: "刷新数据源" }));
  await user.selectOptions(screen.getByRole("combobox", { name: "数据源" }), "qq");
  expect(screen.getByRole("button", { name: "准备断开本机来源" })).toBeDisabled();

  await user.click(screen.getByRole("button", { name: "查看 Token 删除确认" }));
  expect(await screen.findByText(/操作：删除已保存 Token/)).toBeVisible();
  await user.click(screen.getByRole("checkbox", { name: "授权本次删除已保存 Token" }));
  await user.click(screen.getByRole("button", { name: "删除已保存 Token" }));

  await waitFor(() => expect(api.requests.filter(item => item.path.endsWith("/secret-operations")).at(-1)?.body?.operation).toBe("secret_delete"));
  expect(await screen.findByText(/Token 已从本机后端秘密存储删除/)).toBeVisible();
  expect(screen.getByRole("button", { name: "准备断开本机来源" })).toBeEnabled();
});

it("刷新来源会丢弃旧 selection handle 并从当前配置重新列会话", async () => {
  const { api, user } = await mount();
  await selectScope(user);
  expect(api.requests.filter(item => item.path.endsWith("/conversations"))).toHaveLength(1);
  api.state.sources[0] = { ...api.state.sources[0], tool_version: "new-tool-version" };

  await user.click(screen.getByRole("button", { name: "刷新数据源" }));

  expect(api.requests.filter(item => item.path.endsWith("/conversations"))).toHaveLength(1);
  expect(screen.getByRole("combobox", { name: "聊天会话" })).toHaveValue("");
  expect(screen.getByRole("button", { name: "查看操作确认" })).toBeDisabled();
  await user.click(screen.getByRole("button", { name: "读取本机会话列表" }));
  await waitFor(() => expect(api.requests.filter(item => item.path.endsWith("/conversations"))).toHaveLength(2));
});

for (const outcome of ["success", "failure"] as const) {
  it(`秘密保存 ${outcome}：值只交同源后端一次并立即清空，存储/日志不含值`, async () => {
    const { api, user } = await mount();
    await user.selectOptions(screen.getByRole("combobox", { name: "数据源" }), "qq");
    await user.selectOptions(await screen.findByRole("combobox", { name: "来源账号" }), "synthetic-account");
    expect(screen.queryByLabelText("上游 API Token")).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/微信数据库密钥|WeChat DB.?key/i)).toBeNull();
    const token = "synthetic-secret-" + crypto.randomUUID();
    const storage = vi.spyOn(Storage.prototype, "setItem");
    const logs = (["log", "error", "warn", "info", "debug", "trace"] as const).map(name => vi.spyOn(console, name).mockImplementation(() => {}));
    let finish!: (response: Response) => void;
    api.state.delaySecret = new Promise(resolve => { finish = resolve; });
    await user.click(screen.getByRole("button", { name: "查看令牌保存确认" }));
    expect(await screen.findByText(/仅为当前来源和账号保存/)).toBeVisible();
    expect(screen.getByText(/fixture-secret-contract-1/)).toBeVisible();
    expect(screen.getByText(/fixture-memory/)).toBeVisible();
    expect(screen.getByText(/工具：QCE.*操作：保存 Token/)).toBeVisible();
    expect(screen.getByText(/source_api_token_stays_server_side/)).toBeVisible();
    expect(screen.getByText(/确认有效期/)).toBeVisible();
    const input = await screen.findByLabelText("上游 API Token") as HTMLInputElement;
    expect(input.type).toBe("password");
    expect(screen.queryByLabelText(/微信数据库密钥|WeChat DB.?key/i)).toBeNull();
    await user.type(input, token);
    await user.click(screen.getByRole("checkbox", { name: "授权本次保存 Token" }));
    await user.click(screen.getByRole("button", { name: "保存 Token" }));
    await waitFor(() => {
      expect(api.requests.filter(item => item.path.endsWith("/secret-operations")).length).toBe(1);
      if (input.value !== "") throw new Error("secret_input_not_cleared");
    });
    const assertSecretExchange = () => {
      const chain = api.requests.filter(item => /\/secret-(consents|operations)$/.test(item.path));
      // Do not rich-diff request bodies: a broken implementation may put the token anywhere.
      if (chain.length !== 2 || chain[0].method !== "POST" || chain[1].method !== "POST" ||
          chain[0].path !== "/api/sources/qq/secret-consents" || chain[1].path !== "/api/sources/qq/secret-operations") {
        throw new Error("secret_consent_request_order_invalid");
      }
      const [prepare, operation] = chain.map(item => item.body);
      const issued = api.secretConsents[0];
      if (!prepare || Object.keys(prepare).sort().join(",") !== "account_id,operation" ||
          prepare.operation !== "secret_set" || prepare.account_id !== "synthetic-account" ||
          api.secretConsents.length !== 1 || !issued || !issued.consumed) throw new Error("secret_consent_scope_invalid");
      if (!operation || Object.keys(operation).sort().join(",") !== "account_id,confirmed,consent_challenge,disclosure_hash,disclosure_revision,operation,value" ||
          operation.operation !== prepare.operation || operation.account_id !== prepare.account_id ||
          operation.consent_challenge !== issued.challenge || operation.confirmed !== true || operation.value !== token) {
        throw new Error("secret_delivery_contract_failed");
      }
      if (operation.disclosure_revision !== issued.disclosure_revision || operation.disclosure_hash !== issued.disclosure_hash) {
        throw new Error("secret_disclosure_seal_not_bound");
      }
      if (chain.some(item => !/^synthetic-csrf-/.test(item.headers.get("X-CSRF-Token") ?? ""))) throw new Error("secret_csrf_missing");
      for (const request of api.requests) {
        const publicBody = request === chain[1] ? { ...request.body, value: "[omitted]" } : request.body;
        if (JSON.stringify([request.url, [...request.headers], publicBody]).includes(token)) throw new Error("secret_extra_transmission");
      }
    };
    assertSecretExchange(); // Assert scope and clearing while the response is still pending.
    finish(outcome === "success" ? new Response(null, { status: 204 }) : new Response('{"code":"secret_failed","message":"秘密操作未完成"}', { status: 500, headers: { "Content-Type": "application/json" } }));
    await screen.findByText(outcome === "success" ? /已保存/ : /未完成|失败/);
    if (outcome === "success") await waitFor(() => expect(screen.getAllByText(/令牌状态.*已保存/).length).toBeGreaterThan(0));
    assertSecretExchange(); // Neither success nor failure may retry or resend the value.
    const forbiddenSurfaces = [document.body.textContent, location.href, JSON.stringify(storage.mock.calls), ...logs.map(log => JSON.stringify(log.mock.calls))];
    if (forbiddenSurfaces.some(value => value?.includes(token))) throw new Error("secret_public_surface_leak");
    if (input.value !== "") throw new Error("secret_input_not_cleared");
  });
}

it("秘密写入成功但非秘密状态刷新失败时保留成功事实且不重发 Token", async () => {
  const { api, user } = await mount();
  await user.selectOptions(screen.getByRole("combobox", { name: "数据源" }), "qq");
  await user.selectOptions(await screen.findByRole("combobox", { name: "来源账号" }), "synthetic-account");
  await user.click(screen.getByRole("button", { name: "查看令牌保存确认" }));
  const input = await screen.findByLabelText("上游 API Token") as HTMLInputElement;
  await user.type(input, "synthetic-secret-status-refresh");
  await user.click(screen.getByRole("checkbox", { name: "授权本次保存 Token" }));
  api.state.delaySources = Promise.resolve(new Response('{"code":"refresh_failed"}', { status: 500, headers: { "Content-Type": "application/json" } }));
  await user.click(screen.getByRole("button", { name: "保存 Token" }));

  expect(await screen.findByText(/Token 已保存.*状态刷新失败/)).toBeVisible();
  expect(input.value).toBe("");
  expect(api.requests.filter(item => item.path.endsWith("/secret-operations"))).toHaveLength(1);
});

it("App 保留同源配置，首页为对话且文件导入只通过系统选择器", async () => {
  const { default: App } = await loadSubject("App");
  const api = installApi(); vi.stubGlobal("fetch", api.fetch);
  render(<App />);
  const acknowledgment = userEvent.setup();
  await acknowledgment.click(screen.getByRole("checkbox", { name: "我已知悉" }));
  await acknowledgment.click(screen.getByRole("button", { name: "继续使用" }));
  await screen.findByRole("textbox", { name: "发送消息" });
  expect(api.requests.some(item => item.path === "/api/setup-options")).toBe(true);
  expect(screen.queryByRole("heading", { name: /1\. 添加资料|2\. 交给记言整理/ })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "新建对话" })).toBeVisible();
  expect(screen.queryByRole("combobox", { name: "保存位置" })).not.toBeInTheDocument();
  const fileInput = document.querySelector('input[type="file"]');
  expect(fileInput).toHaveAttribute("accept", ".txt,.csv,.json,.jsonl,.xlsx,.zip,.png,.jpg,.jpeg");
  expect(document.querySelector('input[placeholder*="/"]')).toBeNull();
});

it("浏览器下载无需批准输出目录，首页不擅自替换服务端根目录或发起来源验证", async () => {
  const { default: App } = await loadSubject("App");
  const api = installApi(); api.state.setupOptions.approved_output_roots = [];
  vi.stubGlobal("fetch", api.fetch);
  render(<App />);
  const acknowledgment = userEvent.setup();
  await acknowledgment.click(screen.getByRole("checkbox", { name: "我已知悉" }));
  await acknowledgment.click(screen.getByRole("button", { name: "继续使用" }));
  expect(await screen.findByRole("textbox", { name: "发送消息" })).toBeVisible();
  expect(screen.getByRole("button", { name: "发送" })).toBeDisabled();
  expect(api.requests.some(item => item.path.endsWith("/verification-jobs"))).toBe(false);
  expect(api.requests.some(item => item.path.endsWith("/exports"))).toBe(false);
});
