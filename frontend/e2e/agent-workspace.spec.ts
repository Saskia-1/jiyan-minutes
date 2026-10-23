import { test, expect } from "./acknowledged-test";
import { readFile } from "node:fs/promises";

test("正式 Agent 首页自然要求、修改撤销、取消零外发与同版本双 TXT", async ({ page }) => {
  test.setTimeout(90_000);
  let starts = 0, expansions = 0;
  page.on("request", request => { if (request.method() === "POST" && new URL(request.url()).pathname === "/api/generation/jobs") starts++;
    if (new URL(request.url()).pathname.startsWith("/api/generation/topic-expansion")) expansions++; });
  await page.goto("/"); await expect(page.getByRole("heading", { name: "记言 · Minutes", exact: true })).toBeVisible();
  const chat = page.getByRole("region", { name: "聊天内容" });
  await chat.getByRole("radio", { name: "粘贴文本" }).check();
  await chat.getByRole("textbox", { name: "聊天文本" }).fill("甲：\n创业可以先记录用户需要，再尝试验证小规模的想法。\n\n乙：\n创业时也要记录不同观点和每次验证的反馈。");
  await chat.getByRole("button", { name: "生成导入预览" }).click();
  await chat.getByRole("checkbox", { name: /确认按此预览导入/ }).check();
  await chat.getByRole("button", { name: "确认导入本地索引" }).click();
  const task = page.getByRole("region", { name: "整理任务" });
  const instruction = "整理创业的想法";
  await task.getByLabel("整理要求", { exact: true }).fill(instruction);
  await task.getByRole("button", { name: "理解并整理" }).click();
  await expect(task.getByLabel("检索关键词", { exact: true })).toHaveValue("创业");
  await expect(task.getByLabel("任务整理方向")).toHaveValue("ideas");
  let outbound = task.getByRole("region", { name: "AI 外发预览" });
  await expect(outbound.locator("pre").first()).toBeVisible(); await expect(outbound).toContainText(instruction);
  expect(starts).toBe(0); expect(expansions).toBe(0);
  await outbound.getByRole("checkbox", { name: /授权本次向外部 AI/ }).check();
  await task.getByLabel("检索关键词", { exact: true }).fill("不存在的主题");
  await expect(outbound).toHaveCount(0);
  await task.getByRole("button", { name: "重新预览外发内容" }).click();
  await expect(task.getByText(/没有找到可整理的完整范围/)).toBeVisible(); expect(starts).toBe(0);
  await task.getByLabel("检索关键词", { exact: true }).fill("创业");
  await task.getByRole("button", { name: "重新预览外发内容" }).click();
  outbound = task.getByRole("region", { name: "AI 外发预览" });
  await expect(outbound.getByRole("checkbox", { name: /授权本次向外部 AI/ })).not.toBeChecked();
  await outbound.getByRole("button", { name: "取消本次整理" }).click();
  await expect(outbound).toHaveCount(0); expect(starts).toBe(0);
  await task.getByRole("button", { name: "重新预览外发内容" }).click();
  await outbound.getByRole("checkbox", { name: /授权本次向外部 AI/ }).check();
  await outbound.getByRole("button", { name: "确认并生成" }).click();
  await expect(task.getByRole("status").filter({ hasText: "生成状态" })).toContainText("已完成", { timeout: 30_000 });
  const result = page.getByRole("region", { name: "预览与下载" });
  await expect(result).toContainText("AI 原始版本");
  const versions: string[] = []; page.on("response", async response => { if (response.url().endsWith("/text-pair")) versions.push((await response.json()).version_id); });
  const texts: string[] = [];
  for (const name of ["整理内容", "核查参考"]) {
    const event = page.waitForEvent("download"); await result.getByRole("button", { name: `下载${name}.txt` }).click();
    const download = await event; expect(download.suggestedFilename()).toMatch(new RegExp(`${name}\\.txt$`));
    texts.push(await readFile((await download.path())!, "utf8"));
  }
  expect(versions).toHaveLength(2); expect(new Set(versions).size).toBe(1); expect(texts[0]).toContain("[证据1]"); expect(texts[1]).toContain("[证据1]");
  expect(starts).toBe(1); expect(expansions).toBe(0);
});

test("新导入笔记在 Agent 主流程直接整理，刷新不自动纳入历史聊天", async ({ page }) => {
  await page.goto("/"); const chat = page.getByRole("region", { name: "聊天内容" });
  await chat.getByText("截图 OCR / 笔记文字导入").click();
  const importer = chat.getByRole("region", { name: "截图或笔记正文导入" });
  await importer.getByRole("radio", { name: "粘贴笔记文字" }).check();
  await importer.getByLabel("笔记正文", { exact: true }).fill("阅读：为笔记记录出处，并整理不同作者的观点。");
  await importer.getByRole("button", { name: "识别并预览" }).click();
  await importer.getByRole("checkbox", { name: /确认以上正文/ }).check();
  await importer.getByRole("button", { name: "确认导入正文" }).click();
  const task = page.getByRole("region", { name: "整理任务" });
  await task.getByLabel("整理要求", { exact: true }).fill("整理关于阅读的观点");
  const mixed = page.waitForRequest(request => new URL(request.url()).pathname === "/api/mixed-search");
  await task.getByRole("button", { name: "理解并整理" }).click();
  await expect(task.getByLabel("检索关键词")).toHaveValue("阅读");
  expect((await mixed).postDataJSON().source_refs).toHaveLength(1);
  await expect(task.getByRole("region", { name: "AI 外发预览" })).toContainText("不同作者的观点");
});
