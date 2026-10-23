import { test, expect } from "./acknowledged-test";
import { fileURLToPath } from "node:url";
import { readFile, stat } from "node:fs/promises";

const sample = fileURLToPath(new URL("./fixtures/undated-browser-sample.txt", import.meta.url));

test("简洁网页从文件上传到隔离 AI 生成、编辑、下载和刷新恢复", async ({ page }) => {
  test.setTimeout(90_000);
  await page.addInitScript(() => { Object.defineProperty(window, "showSaveFilePicker", { value: undefined }); });
  await page.goto("/e2e/legacy-workspace.html");

  const chat = page.getByRole("region", { name: "聊天内容" });
  await chat.getByLabel("聊天记录文件").setInputFiles(sample);
  await chat.getByRole("button", { name: "生成导入预览" }).click();
  const importPreview = chat.getByRole("region", { name: "导入预览" });
  await expect(importPreview.getByRole("status")).toContainText("可导入 3 条");
  await expect(importPreview).toContainText("时间范围：未知 至 未知");
  await importPreview.getByRole("checkbox", { name: /确认按此预览导入/ }).check();
  await importPreview.getByRole("button", { name: "确认导入本地索引" }).click();
  await expect(chat).toContainText("3 条消息");
  await chat.getByText(/展开聊天内容（3 条/).click();
  await expect(chat.getByText("时间未知", { exact: true })).toHaveCount(3);
  await chat.getByRole("listitem").filter({ hasText: "无关的合成噪声" }).getByRole("checkbox", { name: "排除此消息" }).check();

  await page.getByText("高级设置 · 手动关键词流程").click();
  await page.getByRole("checkbox", { name: "使用手动关键词流程" }).check();
  const organize = page.getByRole("region", { name: "整理任务" });
  await organize.getByRole("textbox", { name: "关键词" }).fill("歌单");
  const expansion = organize.getByRole("checkbox", { name: /自动扩展相关关键词/ });
  await expect(expansion).not.toBeChecked();
  await expansion.check();
  await organize.getByText("查看检索范围与关键词").click();
  const expansionResponse = page.waitForResponse(response => response.url().includes("/api/generation/topic-expansion"));
  await organize.getByRole("button", { name: "预览相关消息" }).click();
  expect((await expansionResponse).status()).toBe(200);
  await expect(organize).toContainText("检索及相邻上下文");
  await expect(organize.getByRole("combobox", { name: "整理方向" })).toHaveValue("ideas");
  await organize.getByRole("button", { name: "开始整理" }).click();
  const outbound = organize.getByRole("region", { name: "AI 外发预览" });
  await expect(outbound).toContainText("fake");
  await expect(outbound).toContainText("发送 2 条纯文本");
  await expect(outbound).not.toContainText("无关的合成噪声");
  await outbound.getByRole("checkbox", { name: /授权本次向外部 AI/ }).check();
  await outbound.getByRole("button", { name: "确认并生成" }).click();
  await expect(organize.getByRole("status").filter({ hasText: "生成状态" })).toContainText("已完成", { timeout: 30_000 });

  const result = page.getByRole("region", { name: "预览与下载" });
  await expect(result).toContainText("AI 原始版本");
  const edit = page.getByRole("region", { name: "自行修改和补充" });
  await edit.getByRole("radio", { name: "是" }).check();
  const text = edit.getByRole("textbox", { name: "完整文档" });
  const original = await text.inputValue();
  expect(original).toMatch(/^# /);
  await text.fill(original.replace(/^## .+/m, "## 合成歌单构思（已校订）"));
  await edit.getByRole("button", { name: "确认修改" }).click();
  await expect(result).toContainText("用户版本 · 原始依据仅供对照");
  await expect(result).toContainText("合成歌单构思（已校订）");

  for (const [format, extension] of [["markdown", ".md"], ["docx", ".docx"]] as const) {
    await result.getByRole("combobox", { name: "下载格式" }).selectOption(format);
    const downloadPromise = page.waitForEvent("download");
    await result.getByRole("button", { name: "下载文档" }).click();
    const download = await downloadPromise;
    expect(download.suggestedFilename()).toMatch(new RegExp(`\\${extension}$`));
    const downloadedPath = (await download.path())!;
    expect((await stat(downloadedPath)).size).toBeGreaterThan(0);
    if (format === "markdown") expect(await readFile(downloadedPath, "utf8")).toContain("合成歌单构思（已校订）");
  }

  await page.reload();
  await page.getByText("已保存的文档", { exact: true }).click();
  const library = page.getByRole("region", { name: "已生成文档" });
  await expect(library).toContainText("最新版本 2");
  await library.getByRole("button", { name: "打开最新版本" }).click();
  await expect(page.getByRole("region", { name: "预览与下载" })).toContainText("合成歌单构思（已校订）");
});
