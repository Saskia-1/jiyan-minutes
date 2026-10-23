import { test, expect } from "@playwright/test";

test("主题扩展失败时多个关键词仍召回无时间聊天，保留上下文与生成预览", async ({ page }) => {
  await page.route("**/api/generation/topic-expansion", route => route.fulfill({
    status: 200, contentType: "application/json",
    body: JSON.stringify({ topic: "创业、就业", terms: ["创业、就业"], degraded: true }),
  }));
  await page.goto("/e2e/legacy-workspace.html");
  const compliance = page.getByRole("dialog", { name: "使用与合规提示" });
  await expect(compliance).toBeVisible();
  await compliance.getByRole("checkbox", { name: "我已知悉" }).check();
  await compliance.getByRole("button", { name: "继续使用" }).click();
  const chat = page.getByRole("region", { name: "聊天内容" });
  await chat.getByLabel("聊天记录文件").setInputFiles({
    name: "keyword-fallback-synthetic.csv", mimeType: "text/csv",
    buffer: Buffer.from("conversation,sender,text,id\n合成群,A,合成创业讨论,m1\n合成群,B,合成相邻条件,m2\n合成群,A,合成就业讨论,m3\n"),
  });
  await chat.getByRole("button", { name: "生成导入预览" }).click();
  const preview = chat.getByRole("region", { name: "导入预览" });
  await preview.getByRole("checkbox", { name: /确认按此预览导入/ }).check();
  await preview.getByRole("button", { name: "确认导入本地索引" }).click();
  await expect(chat).toContainText("3 条消息");
  await page.getByText("高级设置 · 手动关键词流程").click();
  await page.getByRole("checkbox", { name: "使用手动关键词流程" }).check();
  const organize = page.getByRole("region", { name: "整理任务" });
  await organize.getByRole("textbox", { name: "关键词" }).fill("创业、就业");
  await organize.getByRole("checkbox", { name: /自动扩展相关关键词/ }).check();
  await organize.getByText("查看检索范围与关键词").click();
  page.once("dialog", dialog => dialog.accept());
  await organize.getByRole("button", { name: "预览相关消息" }).click();
  await expect(organize).toContainText("检索及相邻上下文 3 条");
  await expect(organize).toContainText("已按原主题及输入的关键词进行本地搜索");
  await expect(organize.getByRole("alert")).toHaveCount(0);
  await organize.getByRole("button", { name: "开始整理" }).click();
  const outbound = organize.getByRole("region", { name: "AI 外发预览" });
  await expect(outbound).toContainText("发送 3 条纯文本");
  await expect(outbound).toContainText("合成创业讨论");
  await expect(outbound).toContainText("合成就业讨论");
  await expect(outbound).toContainText("合成相邻条件");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});
