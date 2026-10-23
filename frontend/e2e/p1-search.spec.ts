// Component integration harness: retained UI with the real synthetic backend.
import { test, expect, type Response } from "./acknowledged-test";

async function expectResponseStatus(response: Response, expected: number) {
  const actual = response.status();
  if (actual === expected) return;
  const requestBody = response.request().postData() ?? "<empty>";
  const responseBody = await Promise.race([
    response.text().catch(error => `unreadable: ${String(error)}`),
    new Promise<string>(resolve => setTimeout(() => resolve("<timeout>"), 1_000)),
  ]);
  throw new Error(`Expected ${expected}, received ${actual}. Request: ${requestBody}. Response: ${responseBody}`);
}

test("P1/P2 多会话导出、搜索和证据验证生成全链路，窄屏无横向溢出", async ({ page, request }) => {
  const origin = "http://127.0.0.1:4173";
  const externalRequests: string[] = [];
  await page.route("**/*", route => {
    if (new URL(route.request().url()).origin === origin) return route.continue();
    externalRequests.push("forbidden_external_request");
    return route.abort();
  });
  const session = await request.get(`${origin}/api/session`, { headers: { Origin: origin } });
  const csrf = (await session.json()).csrf_token;
  const configured = await request.post(`${origin}/api/sources`, { headers: { Origin: origin, "X-CSRF-Token": csrf }, data: {
    source_id: "qq", account_id: "synthetic-account", base_url: "http://127.0.0.1:40653",
    client_version: "fixture-client-1", tool_version: "fixture-tool-1", account_version: "fixture-account-1",
  } });
  expect([200, 201]).toContain(configured.status());

  await page.goto("/e2e/component-harness.html");
  await page.getByRole("combobox", { name: "数据源" }).selectOption("qq");
  await page.getByRole("combobox", { name: "来源账号" }).selectOption("synthetic-account");
  await page.getByRole("button", { name: "读取本机会话列表" }).click();
  await page.getByRole("combobox", { name: "显示时区" }).selectOption("Asia/Shanghai");
  await page.getByLabel("开始时间", { exact: true }).fill("2026-09-01T08:00");
  await page.getByLabel("结束时间（不包含）", { exact: true }).fill("2026-09-01T08:01");
  await page.getByRole("combobox", { name: "保存位置" }).selectOption("fixture-root");

  const workspace = page.getByRole("region", { name: "导出与本地搜索" });
  const choices = workspace.getByRole("checkbox", { name: /选择 同名合成群 标识/ });
  await expect(choices).toHaveCount(2);
  await choices.nth(0).check();
  await choices.nth(1).check();
  await workspace.getByRole("checkbox", { name: /确认只导出所选会话和时间范围/ }).check();
  const consentResponsePromise = page.waitForResponse(response =>
    response.request().method() === "POST" && response.url().endsWith("/api/export-jobs/consents/from-selections"));
  const startResponsePromise = page.waitForResponse(response =>
    response.request().method() === "POST" && response.url().endsWith("/api/export-jobs"));
  await workspace.getByRole("button", { name: "导出并建立本地索引" }).click();
  const consentResponse = await consentResponsePromise;
  await expectResponseStatus(consentResponse, 201);
  expect((await startResponsePromise).status()).toBe(202);
  await expect(workspace.getByRole("status").filter({ hasText: "已完成并可搜索" })).toBeVisible();

  await workspace.getByRole("searchbox", { name: "搜索聊天内容" }).fill("合成记录");
  await workspace.getByRole("button", { name: "搜索本地聊天" }).click();
  const results = workspace.getByRole("region", { name: "搜索结果" });
  await expect(results.getByText("合成记录，仅用于本地测试")).toHaveCount(4);
  await expect(results.getByText(/batch:.*snapshot:/).first()).toBeVisible();
  const exclusions = results.getByRole("checkbox", { name: /不用于 AI 生成：合成记录，仅用于本地测试/ });
  await expect(exclusions).toHaveCount(4);
  const exclusionNames = await exclusions.evaluateAll(items => items.map(item => item.getAttribute("aria-label")));
  expect(new Set(exclusionNames).size).toBe(4);
  await exclusions.first().check();

  const generation = workspace.getByRole("region", { name: "AI 主题整理" });
  await expect(generation).toContainText("排除：1 条消息");
  await generation.getByLabel("操作主题").fill("合成记录");
  await generation.getByRole("button", { name: "预览 AI 外发" }).click();
  const outbound = generation.getByRole("region", { name: "AI 外发预览" });
  await expect(outbound).toContainText("fake（deterministic");
  await expect(outbound).toContainText("发送 3 条纯文本");
  await outbound.getByText(/查看将要外发的完整脱敏文本/).click();
  await expect(outbound.getByRole("region", { name: "外发分块 1" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await outbound.getByRole("checkbox", { name: /授权本次向外部 AI 发送/ }).check();
  await outbound.getByRole("button", { name: "确认并生成" }).click();
  await expect(generation.getByRole("status")).toContainText("已完成");
  const generated = generation.getByRole("region", { name: "AI 生成结果" });
  await expect(generated).toContainText("AI 生成 · 本地证据已验证");
  await expect(generated.getByRole("heading", { name: "确定性测试：合成记录" })).toBeVisible();
  await generated.getByText("查看原始依据（3 条）", { exact: true }).last().click();
  await expect(generated.getByRole("region", { name: "已验证证据" })).toContainText("合成记录，仅用于本地测试");

  const editor = generation.getByRole("region", { name: "文档编辑与历史" });
  await editor.getByRole("button", { name: "创建可编辑文档" }).click();
  await expect(editor.getByLabel("文档标题")).toHaveValue("确定性测试：合成记录");
  await editor.getByLabel("文档标题").fill("用户编辑的合成指南");
  await editor.getByLabel("步骤 1").fill("用户确认后的合成步骤");
  await editor.getByLabel("修改说明").fill("浏览器端到端编辑");
  await editor.getByRole("button", { name: "保存为新版本" }).click();
  await expect(editor.getByRole("region", { name: "版本历史" })).toContainText("版本 2");
  await editor.getByRole("combobox", { name: "导出格式" }).selectOption("docx");
  await editor.getByRole("combobox", { name: "来源内容" }).selectOption("with_sources");
  await editor.getByRole("button", { name: "导出当前版本" }).click();
  await expect(editor.getByRole("status")).toContainText("已保存本地文件");

  const deletion = editor.getByRole("region", { name: "删除这份本地文档" });
  await deletion.getByRole("checkbox", { name: "同时删除受管本地文件" }).check();
  await deletion.getByRole("button", { name: "查看删除影响" }).click();
  await expect(deletion.getByLabel("删除影响清单")).toContainText("文档导出记录：1");
  await deletion.getByRole("checkbox", { name: /我已核对上述影响/ }).check();
  await deletion.getByRole("button", { name: "确认删除" }).click();
  await expect(editor).toHaveCount(0);

  expect(externalRequests).toHaveLength(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  const importFile = page.locator('input[type="file"]');
  await expect(importFile).toHaveCount(1);
  await expect(importFile).toHaveAttribute("accept", ".txt,.json,.jsonl,.csv,.xlsx,.zip");
});

test("P1 运行中导出可从 UI 取消且不变为可搜索", async ({ page, request }) => {
  const origin = "http://127.0.0.1:4173";
  const session = await request.get(`${origin}/api/session`, { headers: { Origin: origin } });
  const csrf = (await session.json()).csrf_token;
  await request.post(`${origin}/api/sources`, { headers: { Origin: origin, "X-CSRF-Token": csrf }, data: {
    source_id: "qq", account_id: "synthetic-account", base_url: "http://127.0.0.1:40653",
    client_version: "fixture-client-1", tool_version: "fixture-tool-1", account_version: "fixture-account-1",
  } });
  await page.goto("/e2e/component-harness.html");
  await page.getByRole("combobox", { name: "数据源" }).selectOption("qq");
  await page.getByRole("combobox", { name: "来源账号" }).selectOption("synthetic-account");
  await page.getByRole("button", { name: "读取本机会话列表" }).click();
  await page.getByLabel("开始时间", { exact: true }).fill("2026-09-01T08:00");
  await page.getByLabel("结束时间（不包含）", { exact: true }).fill("2026-09-01T08:01");
  await page.getByRole("combobox", { name: "保存位置" }).selectOption("fixture-root");
  const workspace = page.getByRole("region", { name: "导出与本地搜索" });
  await workspace.getByRole("checkbox", { name: "选择 取消测试会话" }).check();
  await workspace.getByRole("checkbox", { name: /确认只导出所选会话和时间范围/ }).check();
  const consentResponsePromise = page.waitForResponse(response =>
    response.request().method() === "POST" && response.url().endsWith("/api/export-jobs/consents/from-selections"));
  const startResponsePromise = page.waitForResponse(response =>
    response.request().method() === "POST" && response.url().endsWith("/api/export-jobs"));
  await workspace.getByRole("button", { name: "导出并建立本地索引" }).click();
  await expectResponseStatus(await consentResponsePromise, 201);
  expect((await startResponsePromise).status()).toBe(202);
  const cancel = workspace.getByRole("button", { name: "取消导出" });
  await expect(cancel).toBeVisible();
  await cancel.click();
  await expect(workspace.getByRole("status")).toContainText("已取消");
  await expect(workspace.getByText("已完成并可搜索")).toHaveCount(0);
});
