import { test, expect } from "./acknowledged-test";
import { readFile } from "node:fs/promises";
import type { Page } from "@playwright/test";

async function attachChat(page: Page) {
  await expect(page.getByLabel("添加资料", { exact: true })).toBeEnabled();
  await page.getByLabel("添加资料", { exact: true }).setInputFiles({
    name: "创业讨论.txt", mimeType: "text/plain",
    buffer: Buffer.from("甲：\n创业可以先验证用户需要，再尝试小规模的想法。\n\n乙：\n商业模式可以先研究收入来源，并记录不同观点。"),
  });
  const card = page.getByRole("region", { name: "导入确认", exact: true });
  await expect(card).toContainText("创业讨论.txt");
  await card.getByRole("checkbox", { name: "我已核对预览并确认导入", exact: true }).check();
  await card.getByRole("button", { name: "确认导入", exact: true }).click();
  await expect(card).toHaveCount(0);
}

async function finishRun(page: Page, artifactCount: number) {
  const downloads = page.getByRole("link", { name: "下载整理内容.txt", exact: true });
  for (let index = 0; index < 6; index++) {
    const approval = page.getByRole("region", { name: "外发授权", exact: true });
    await expect.poll(async () => await downloads.count() >= artifactCount || await approval.isVisible(), { timeout: 20_000 }).toBe(true);
    if (await downloads.count() >= artifactCount) return;
    const text = await approval.locator("pre").allTextContents();
    expect(text.join("\n").length).toBeGreaterThan(0);
    await expect(approval.getByRole("button", { name: "授权并继续", exact: true })).toBeDisabled();
    await approval.getByRole("checkbox", { name: "我已核对并授权本次外发", exact: true }).check();
    await approval.getByRole("button", { name: "授权并继续", exact: true }).click();
    await expect.poll(async () => await downloads.count() >= artifactCount || !(await approval.isVisible()) ||
      JSON.stringify(await approval.locator("pre").allTextContents()) !== JSON.stringify(text), { timeout: 20_000 }).toBe(true);
  }
  await expect(downloads).toHaveCount(artifactCount);
}

test("聊天首页上传后自主执行工具，连续对话产生同版双 TXT 并刷新恢复", async ({ page }) => {
  test.setTimeout(100_000);
  const operations: string[] = []; const controllers: Record<string, unknown>[] = [];
  const artifactVersions: string[] = [];
  page.on("response", async response => {
    if (response.request().method() !== "GET" && response.url().endsWith("/approve") && response.ok()) operations.push("approved");
    if (response.url().endsWith("/text-pair") && response.ok()) artifactVersions.push((await response.json()).version_id);
  });
  page.on("request", request => {
    if (request.url().endsWith("/messages")) operations.push("user");
    if (request.url().endsWith("/api/imports/previews")) operations.push("import");
  });
  await page.goto("/");
  await expect(page).toHaveTitle("记言 · Minutes");
  await expect(page.getByRole("heading", { name: /1\. 添加资料|2\. 交给记言整理/ })).toHaveCount(0);
  await attachChat(page);
  await page.getByRole("textbox", { name: "发送消息", exact: true }).fill("整理创业的想法，归纳值得尝试的方向");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  const approval = page.getByRole("region", { name: "外发授权", exact: true });
  await expect(approval.locator("pre")).toContainText("agent_tool");
  expect(operations.filter(item => item === "approved")).toHaveLength(0);
  controllers.push(JSON.parse(await approval.locator("pre").first().innerText()));
  await finishRun(page, 1);
  expect(operations.filter(item => item === "approved").length).toBeGreaterThanOrEqual(3);
  for (const kind of ["整理内容", "核查参考"]) {
    const event = page.waitForEvent("download");
    await page.getByRole("link", { name: `下载${kind}.txt`, exact: true }).click();
    const download = await event; expect(download.suggestedFilename()).toMatch(/\.txt$/);
    expect(await readFile((await download.path())!, "utf8")).toContain("[证据1]");
  }
  expect(new Set(artifactVersions).size).toBe(1);
  await page.getByRole("textbox", { name: "发送消息", exact: true }).fill("只保留商业模式相关内容，改成简洁的观点总结");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(approval.locator("pre")).toContainText("整理创业的想法");
  await expect(approval.locator("pre")).toContainText("只保留商业模式");
  await finishRun(page, 2);
  expect(operations.filter(item => item === "import")).toHaveLength(1);
  await page.reload();
  await expect(page.getByRole("link", { name: "下载整理内容.txt", exact: true })).toHaveCount(2);
  await expect(page.getByRole("textbox", { name: "发送消息", exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test("对话等待授权时停止且新建会话不会带入旧资料", async ({ page }) => {
  await page.goto("/"); await attachChat(page);
  await page.getByRole("textbox", { name: "发送消息", exact: true }).fill("整理创业观点");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page.getByRole("region", { name: "外发授权", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "停止", exact: true }).click();
  await expect(page.getByRole("region", { name: "外发授权", exact: true })).toHaveCount(0);
  if (!(await page.getByRole("button", { name: "新建对话", exact: true }).isVisible()))
    await page.getByRole("button", { name: "切换会话列表", exact: true }).click();
  await page.getByRole("button", { name: "新建对话", exact: true }).click();
  await expect(page.getByText("创业讨论.txt", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "下载整理内容.txt", exact: true })).toHaveCount(0);
});

test("主输入框自动识别粘贴聊天，确认后发送要求，不打开旧页面", async ({ page }) => {
  await page.goto("/");
  const composer = page.getByRole("textbox", { name: "发送消息", exact: true });
  await expect(composer).toBeEnabled();
  await expect(page.getByRole("button", { name: "粘贴笔记", exact: true })).toHaveCount(0);
  await composer.fill("整理创业的不同观点");
  await composer.evaluate((element, text) => {
    const data = new DataTransfer(); data.setData("text/plain", text);
    element.dispatchEvent(new ClipboardEvent("paste", { clipboardData: data, bubbles: true, cancelable: true }));
  }, "合成甲：创业可以先验证用户需要，再尝试小规模的想法。\n合成乙：商业模式可以先研究收入来源，并记录不同观点。");
  const card = page.getByRole("region", { name: "导入确认", exact: true });
  await expect(card).toContainText("对话框聊天记录");
  await expect(card).toContainText("可导入 2 条");
  await expect(composer).toHaveValue("整理创业的不同观点");
  await expect(page.getByRole("region", { name: "外发授权", exact: true })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "高级资料与文档工具", exact: true })).toBeHidden();
  await expect(page.getByRole("button", { name: "发送", exact: true })).toBeDisabled();
  await card.getByRole("checkbox", { name: "我已核对预览并确认导入", exact: true }).check();
  await card.getByRole("button", { name: "确认导入", exact: true }).click();
  await expect(page.getByRole("region", { name: "已导入资料", exact: true })).toContainText("实际导入 2 条，跳过 0 条");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  const approval = page.getByRole("region", { name: "外发授权", exact: true });
  await expect(approval).toContainText("整理创业的不同观点");
  await expect(approval.getByRole("button", { name: "授权并继续", exact: true })).toBeDisabled();
  await expect(page.getByRole("heading", { name: "高级资料与文档工具", exact: true })).toBeHidden();
});
