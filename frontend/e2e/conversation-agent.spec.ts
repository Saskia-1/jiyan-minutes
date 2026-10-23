import { test, expect } from "./acknowledged-test";
import { readFile } from "node:fs/promises";
import type { Page } from "@playwright/test";

async function attachChat(page: Page) {
  await expect(page.getByLabel("添加资料", { exact: true })).toBeEnabled();
  await page.getByLabel("添加资料", { exact: true }).setInputFiles({
    name: "创业讨论.txt", mimeType: "text/plain",
    buffer: Buffer.from("甲：\n创业可以先验证用户需要，再尝试小规模的想法。\n\n乙：\n商业模式可以先研究收入来源，并记录不同观点。"),
  });
  await expect(page.getByRole("region", { name: "本会话资料", exact: true })).toContainText("创业讨论.txt");
  await expect(page.getByRole("region", { name: "导入确认", exact: true })).toHaveCount(0);
  await expect(page.getByRole("region", { name: "已导入资料", exact: true })).toHaveCount(0);
}

async function finishRun(page: Page, artifactCount: number) {
  await expect(page.getByRole("link", { name: "下载整理内容.txt", exact: true })).toHaveCount(artifactCount, { timeout: 45_000 });
  await expect(page.locator(".chat-artifact-body")).toHaveCount(artifactCount, { timeout: 10_000 });
  await expect(page.getByRole("region", { name: "外发授权", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "发送", exact: true })).toBeVisible();
}

test("聊天首页自动导入并整理，连续对话产生独立双 TXT 并刷新恢复", async ({ page }) => {
  test.setTimeout(120_000);
  const operations: string[] = [];
  const automatic: unknown[] = [];
  const artifactVersions: string[] = [];
  page.on("response", async response => {
    if (response.url().endsWith("/text-pair") && response.ok()) {
      try { const pair = await response.json(); artifactVersions.push(`${pair.document_id}:${pair.version_id}`); }
      catch { /* A closing browser can discard an already finished response. */ }
    }
  });
  page.on("request", request => {
    if (request.url().endsWith("/messages")) { operations.push("user"); automatic.push(JSON.parse(request.postData() ?? "{}").automatic); }
    if (request.url().endsWith("/api/imports/previews")) operations.push("import");
    if (request.url().endsWith("/approve")) operations.push("approve");
  });
  await page.goto("/");
  await expect(page).toHaveTitle("记言 · Minutes");
  await expect(page.getByRole("heading", { name: /1\. 添加资料|2\. 交给记言整理/ })).toHaveCount(0);
  await attachChat(page);
  await page.getByRole("textbox", { name: "发送消息", exact: true }).fill("整理创业的想法，归纳值得尝试的方向");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await finishRun(page, 1);
  for (const kind of ["整理内容", "核查参考"]) {
    const event = page.waitForEvent("download");
    await page.getByRole("link", { name: `下载${kind}.txt`, exact: true }).click();
    const download = await event;
    expect(download.suggestedFilename()).toMatch(/\.txt$/);
    const text = await readFile((await download.path())!, "utf8");
    expect(text.trim().length).toBeGreaterThan(0);
    if (kind === "整理内容") expect(text).not.toMatch(/\[证据\d+\]/);
    else expect(text).toContain("[证据1]");
  }
  await expect.poll(() => new Set(artifactVersions).size).toBe(1);
  await expect(page.locator(".chat-artifact-body")).not.toContainText("[证据1]");
  await expect(page.locator(".chat-avatar, .chat-message-author")).toHaveCount(0);
  const alignment = await page.evaluate(() => {
    const bounds = (selector: string) => { const r = document.querySelector(selector)!.getBoundingClientRect(); return { left: r.left, right: r.right, width: r.width }; };
    return { column: bounds(".chat-message-column"), user: bounds(".chat-message-user .chat-message-content"), assistant: bounds(".chat-message-assistant .chat-message-content") };
  });
  expect(Math.abs(alignment.user.right - alignment.column.right)).toBeLessThanOrEqual(1);
  expect(alignment.user.width).toBeLessThanOrEqual(alignment.column.width * .8 + 1);
  expect(Math.abs(alignment.assistant.left - alignment.column.left)).toBeLessThanOrEqual(1);
  expect(alignment.assistant.width).toBeGreaterThanOrEqual(alignment.column.width - 1);

  await page.getByRole("textbox", { name: "发送消息", exact: true }).fill("只保留商业模式相关内容，改成简洁的观点总结");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await finishRun(page, 2);
  await expect.poll(() => new Set(artifactVersions).size).toBe(2);
  expect(operations.filter(item => item === "import")).toHaveLength(1);
  expect(operations.filter(item => item === "approve")).toHaveLength(0);
  expect(automatic).toEqual([true, true]);
  await page.reload();
  await expect(page.getByRole("link", { name: "下载整理内容.txt", exact: true })).toHaveCount(2);
  await expect(page.getByRole("textbox", { name: "发送消息", exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test("新建会话不会带入旧资料或旧成果", async ({ page }) => {
  await page.goto("/"); await attachChat(page);
  if (!(await page.getByRole("button", { name: "新建对话", exact: true }).isVisible()))
    await page.getByRole("button", { name: "切换会话列表", exact: true }).click();
  await page.getByRole("button", { name: "新建对话", exact: true }).click();
  await expect(page.getByRole("textbox", { name: "发送消息", exact: true })).toBeEnabled();
  await expect(page.getByRole("region", { name: "本会话资料", exact: true })).toHaveCount(0);
  await expect(page.getByText("创业讨论.txt", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "下载整理内容.txt", exact: true })).toHaveCount(0);
});

test("主框粘贴聊天后自动加入来源，保留整理要求并直接交付结果", async ({ page }) => {
  test.setTimeout(60_000);
  await page.goto("/");
  const composer = page.getByRole("textbox", { name: "发送消息", exact: true });
  await expect(composer).toBeEnabled();
  await expect(page.getByRole("button", { name: "粘贴笔记", exact: true })).toHaveCount(0);
  await composer.fill("整理创业的不同观点");
  await composer.evaluate((element, text) => {
    const data = new DataTransfer(); data.setData("text/plain", text);
    element.dispatchEvent(new ClipboardEvent("paste", { clipboardData: data, bubbles: true, cancelable: true }));
  }, "合成甲：创业可以先验证用户需要，再尝试小规模的想法。\n合成乙：商业模式可以先研究收入来源，并记录不同观点。");
  await expect(page.getByRole("region", { name: "本会话资料", exact: true })).toContainText("对话框聊天记录");
  await expect(composer).toHaveValue("整理创业的不同观点");
  await expect(page.getByRole("region", { name: "导入确认", exact: true })).toHaveCount(0);
  await expect(page.getByRole("region", { name: "已导入资料", exact: true })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "高级资料与文档工具", exact: true })).toBeHidden();
  await expect(page.getByRole("button", { name: "发送", exact: true })).toBeEnabled();
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await finishRun(page, 1);
  await expect(page.getByRole("heading", { name: "高级资料与文档工具", exact: true })).toBeHidden();
});
