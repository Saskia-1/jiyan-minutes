// Component integration harness: retained UI with the real synthetic backend.
import { test, expect } from "./acknowledged-test";
import { readdir, readFile, stat } from "node:fs/promises";
import { createHash } from "node:crypto";
import { join } from "node:path";

test("窄范围合成导出通过真实后端落盘，360px 与键盘可用，结果永不冒充 ready", async ({ page, request }) => {
  const origin = "http://127.0.0.1:4173";
  const runRoot = process.env.P0_E2E_RUN_ROOT;
  if (!runRoot) throw new Error("synthetic_runner_required");
  const output = join(runRoot, "approved-output");
  const beforeFiles = new Set(await readdir(output, { recursive: true }));
  const externalRequests: string[] = [];
  await page.route("**/*", route => {
    if (new URL(route.request().url()).origin === origin) return route.continue();
    externalRequests.push("forbidden_external_request");
    return route.abort();
  });
  // No route mocks: configure the injected synthetic source using real HTTP.
  const session = await request.get(`${origin}/api/session`, { headers: { Origin: origin } });
  expect(session.status()).toBe(200);
  const csrf = (await session.json()).csrf_token;
  const configured = await request.post(`${origin}/api/sources`, { headers: { Origin: origin, "X-CSRF-Token": csrf }, data: {
    source_id: "qq", account_id: "synthetic-account", base_url: "http://127.0.0.1:40653",
    client_version: "fixture-client-1", tool_version: "fixture-tool-1", account_version: "fixture-account-1",
  } });
  expect([200, 201]).toContain(configured.status());
  const leaked: string[] = [];
  page.on("console", event => { if (/合成记录，仅用于本地测试|group:synthetic-/.test(event.text())) leaked.push("console"); });
  page.on("pageerror", event => { if (/合成记录，仅用于本地测试|group:synthetic-/.test(event.message)) leaked.push("pageerror"); });
  await page.goto("/e2e/component-harness.html");
  const sourceChoice = page.getByRole("combobox", { name: "数据源" });
  await expect(sourceChoice).toBeVisible();
  for (let index = 0; index < 12 && !(await sourceChoice.evaluate(element => element === document.activeElement)); index++) await page.keyboard.press("Tab");
  await expect(sourceChoice).toBeFocused();
  expect(await sourceChoice.evaluate(element => {
    const style = getComputedStyle(element);
    return (style.outlineStyle !== "none" && parseFloat(style.outlineWidth) > 0) || style.boxShadow !== "none";
  })).toBe(true);
  await sourceChoice.selectOption("qq");
  await page.getByRole("combobox", { name: "来源账号" }).selectOption("synthetic-account");
  await page.getByRole("button", { name: "读取本机会话列表" }).click();
  const conversations = page.getByRole("combobox", { name: "聊天会话" });
  const duplicateOptions = conversations.getByRole("option").filter({ hasText: "同名合成群" });
  await expect(duplicateOptions).toHaveCount(2);
  await conversations.selectOption((await duplicateOptions.nth(1).getAttribute("value"))!);
  await page.getByRole("combobox", { name: "显示时区" }).selectOption("Asia/Shanghai");
  await page.getByLabel("开始时间", { exact: true }).fill("2026-09-01T08:00");
  await page.getByLabel("结束时间（不包含）", { exact: true }).fill("2026-09-01T08:01");
  await page.getByRole("combobox", { name: "保存位置" }).selectOption("fixture-root");
  await page.getByRole("button", { name: "查看操作确认" }).click();
  for (const title of ["工具、版本与许可", "访问方式与客户端影响", "账号、目录与超范围读取", "秘密、缓存与清理", "权限与联网", "本次操作与安全停止"]) {
    const check = page.getByRole("region", { name: title }).getByRole("checkbox");
    await check.focus(); await page.keyboard.press("Space"); await expect(check).toBeChecked();
  }
  const responsePromise = page.waitForResponse(response => response.request().method() === "POST" && response.url().endsWith("/verification-jobs"));
  const confirm = page.getByRole("button", { name: "确认并开始验证" });
  await confirm.focus(); await page.keyboard.press("Enter");
  const created = await responsePromise;
  expect(created.status()).toBe(202);
  const jobId = (await created.json()).job_id;
  await expect(page.getByText(/合成测试/).first()).toBeVisible();
  await expect(page.getByRole("status").filter({ hasText: /验证完成|合成验证完成/ })).toBeVisible();
  await expect(page.getByText("真实导出已验证", { exact: true })).toHaveCount(0);
  const publicJob = await page.evaluate(async id => (await fetch(`/api/sources/qq/verification-jobs/${id}`)).json(), jobId);
  expect(publicJob.state).toBe("complete"); expect(publicJob.evidence_class).toBe("synthetic");
  expect(publicJob.count).toBe(2); expect(publicJob.range_ok).toBe(true); expect(publicJob.hash_ok).toBe(true);
  const entries = await readdir(output, { recursive: true });
  const candidates = entries.filter(entry => entry.endsWith(".jsonl") && !beforeFiles.has(entry));
  expect(candidates).toHaveLength(1);
  let matching = 0;
  for (const entry of candidates) {
    const path = join(output, entry); const raw = await readFile(path);
    if (createHash("sha256").update(raw).digest("hex") !== publicJob.sha256) continue;
    matching++;
    const records = raw.toString("utf8").trim().split("\n").map(line => JSON.parse(line));
    if (records.length !== 2 || records.some(record => record.conversation_id !== "group:synthetic-B" || record.sent_at_ms < 1788220800000 || record.sent_at_ms >= 1788220860000)) throw new Error("synthetic_artifact_scope_invalid");
    expect((await stat(path)).mode & 0o777).toBe(0o600);
    expect((await stat(join(output, entry, ".."))).mode & 0o777).toBe(0o700);
  }
  expect(matching).toBe(1);
  expect(externalRequests).toHaveLength(0);
  const documentText = await page.locator("body").innerText();
  if (/合成记录，仅用于本地测试|group:synthetic-/.test(documentText) || leaked.length) throw new Error("private_artifact_leaked_into_browser");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  const importFile = page.locator('input[type="file"]');
  await expect(importFile).toHaveCount(1);
  await expect(importFile).toHaveAttribute("accept", ".txt,.json,.jsonl,.csv,.xlsx,.zip");
});
