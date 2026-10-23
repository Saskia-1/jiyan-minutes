import { test, expect } from "./acknowledged-test";
import { readFile, stat } from "node:fs/promises";

test("笔记从正式网页导入、逐条选择、隔离生成、证据、下载及刷新恢复", async ({ page }) => {
  test.setTimeout(90_000);
  await page.addInitScript(() => { Object.defineProperty(window, "showSaveFilePicker", { value: undefined }); });
  await page.goto("/");
  const chat = page.getByRole("region", { name: "聊天内容" });
  await chat.getByText("截图 OCR / 笔记文字导入").click();
  const importPanel = chat.getByRole("region", { name: "截图或笔记正文导入" });
  await importPanel.getByRole("radio", { name: "粘贴笔记文字" }).check();
  await importPanel.getByRole("textbox", { name: "笔记正文" }).fill("合成主题：用本地便签整理阅读线索，并记录出处。");
  await importPanel.getByRole("button", { name: "识别并预览" }).click();
  const correction = importPanel.getByRole("region", { name: "正文校对预览" });
  await expect(correction).toContainText("合成主题");
  await correction.getByRole("checkbox", { name: /确认以上正文/ }).check();
  await correction.getByRole("button", { name: "确认导入正文" }).click();
  await expect(importPanel).toContainText("已导入 1 条正文");

  await page.reload();
  const restoredChat = page.getByRole("region", { name: "聊天内容" });
  const organize = page.getByRole("region", { name: "关键词与整理方向" });
  await organize.getByRole("textbox", { name: "关键词" }).fill("合成主题");
  await restoredChat.getByText("选择截图／笔记与聊天来源，预览混合检索").click();
  await restoredChat.getByRole("checkbox", { name: /截图／笔记批次 1/ }).check();
  await restoredChat.getByRole("checkbox", { name: "包含时间未知的聊天与笔记" }).check();
  await restoredChat.getByRole("button", { name: "预览勾选来源的相关正文" }).click();
  await expect(restoredChat).toContainText("合成主题：用本地便签");
  await restoredChat.getByRole("checkbox", { name: "纳入整理" }).check();
  await organize.getByRole("button", { name: "开始整理" }).click();
  const outbound = organize.getByRole("region", { name: "AI 外发预览" });
  await expect(outbound).toContainText("fake");
  await expect(outbound).toContainText("合成主题：用本地便签");
  await outbound.getByRole("checkbox", { name: /授权本次向外部 AI/ }).check();
  await outbound.getByRole("button", { name: "确认并生成" }).click();
  await expect(organize.getByRole("status").filter({ hasText: "生成状态" })).toContainText("已完成", { timeout: 30_000 });

  const result = page.getByRole("region", { name: "预览与下载" });
  await expect(result).toContainText("AI 原始版本");
  await result.getByText(/查看原始依据（1 条）/).click();
  await expect(result).toContainText("笔记／网页正文");
  for (const [format, extension] of [["markdown", ".md"], ["docx", ".docx"]] as const) {
    await result.getByRole("combobox", { name: "下载格式" }).selectOption(format);
    const downloadPromise = page.waitForEvent("download");
    await result.getByRole("button", { name: "下载文档" }).click();
    const download = await downloadPromise;
    expect(download.suggestedFilename()).toMatch(new RegExp(`\\${extension}$`));
    expect((await stat((await download.path())!)).size).toBeGreaterThan(0);
    if (format === "markdown") expect(await readFile((await download.path())!, "utf8")).toContain("合成主题");
  }
  await page.reload();
  await page.getByText("已保存的文档", { exact: true }).click();
  await expect(page.getByRole("region", { name: "已生成文档" })).toContainText("最新版本 1");
});

test("截图通过网页上传后由本机 OCR 提取正文候选并等待人工确认", async ({ page }) => {
  await page.goto("/");
  const encoded = await page.evaluate(() => {
    const canvas = document.createElement("canvas");
    canvas.width = 900; canvas.height = 1200;
    const context = canvas.getContext("2d")!;
    context.fillStyle = "white"; context.fillRect(0, 0, canvas.width, canvas.height);
    context.fillStyle = "black"; context.font = "48px Arial";
    context.fillText("Synthetic note body", 80, 500);
    context.fillText("Read and organize ideas", 80, 575);
    context.fillText("Keep the original source", 80, 650);
    return canvas.toDataURL("image/png").split(",")[1];
  });
  const chat = page.getByRole("region", { name: "聊天内容" });
  await chat.getByText("截图 OCR / 笔记文字导入").click();
  const importPanel = chat.getByRole("region", { name: "截图或笔记正文导入" });
  await importPanel.getByLabel("聊天或笔记截图", { exact: true }).setInputFiles({
    name: "synthetic-note.png", mimeType: "image/png", buffer: Buffer.from(encoded, "base64"),
  });
  await importPanel.getByRole("button", { name: "识别并预览" }).click();
  const correction = importPanel.getByRole("region", { name: "正文校对预览" });
  await expect(correction).toContainText("Synthetic note body");
  await expect(correction.getByRole("textbox", { name: "正文 1" })).toHaveValue(/Synthetic note body/);
  await correction.getByRole("checkbox", { name: /确认以上正文/ }).check();
  await correction.getByRole("button", { name: "确认导入正文" }).click();
  await expect(importPanel).toContainText("已导入 1 条正文 · 1 张截图");
  const deletion = importPanel.getByRole("region", { name: "删除这次截图／笔记导入" });
  await deletion.getByRole("button", { name: "查看删除影响" }).click();
  await expect(deletion).toContainText("本机原始截图：1");
  await deletion.getByRole("checkbox", { name: /我已核对上述影响/ }).check();
  await deletion.getByRole("button", { name: "确认删除" }).click();
  await expect(importPanel).toContainText("还没有已确认的截图或笔记");
});
