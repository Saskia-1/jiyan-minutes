import { test, expect } from "@playwright/test";

test("部分解析成功时接受跳过后导入，并在刷新后保留实际计数", async ({ page }) => {
  await page.goto("/");
  const compliance = page.getByRole("dialog", { name: "使用与合规提示" });
  await expect(compliance).toBeVisible();
  await compliance.getByRole("checkbox", { name: "我已知悉" }).check();
  await compliance.getByRole("button", { name: "继续使用" }).click();
  const chat = page.getByRole("region", { name: "聊天内容" });
  await chat.getByLabel("聊天记录文件").setInputFiles({
    name: "partial-synthetic.csv", mimeType: "text/csv",
    buffer: Buffer.from("conversation,time,sender,text,id\n合成群,,A,有效合成消息,m1\n合成群,,B,,m2\n"),
  });
  await chat.getByRole("button", { name: "生成导入预览" }).click();
  const preview = chat.getByRole("region", { name: "导入预览" });
  await expect(preview).toContainText("可导入 1 条 · 未解析 1 行");
  await expect(preview).toContainText("第 2 条记录：消息正文为空或格式无效");
  const confirmation = preview.getByRole("checkbox", { name: /确认按此预览导入/ });
  await expect(confirmation).toBeDisabled();
  const skip = preview.getByRole("checkbox", { name: "接受跳过 1 条未解析记录，仅导入 1 条有效记录" });
  await skip.check();
  await confirmation.check();
  await skip.uncheck();
  await expect(confirmation).not.toBeChecked();
  await expect(preview.getByRole("button", { name: "确认导入本地索引" })).toBeDisabled();
  await skip.check();
  await confirmation.check();
  await preview.getByRole("button", { name: "确认导入本地索引" }).click();
  await expect(chat).toContainText("已导入 1 条有效记录，已跳过 1 条未解析记录。");
  await expect(chat.getByText(/本次导入 1 条有效记录，跳过 1 条未解析记录/)).toBeVisible();
  await expect(chat).toContainText("1 条消息");
  await page.reload();
  await page.getByText("导入其他聊天 / 管理已有聊天", { exact: true }).click();
  await page.getByText("已导入的聊天", { exact: true }).click();
  await expect(page.getByRole("region", { name: "聊天内容" })).toContainText("已导入 1 条有效记录，已跳过 1 条未解析记录。");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});
