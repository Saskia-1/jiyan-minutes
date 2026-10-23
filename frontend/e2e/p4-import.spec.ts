// Component integration harness: retained UI with the real synthetic backend.
import { test, expect } from "./acknowledged-test";

test("P4 粘贴导入进入现有搜索、排除、生成与文档链路", async ({ page }) => {
  const origin = "http://127.0.0.1:4173";
  const externalRequests: string[] = [];
  await page.route("**/*", route => {
    if (new URL(route.request().url()).origin === origin) return route.continue();
    externalRequests.push("forbidden_external_request");
    return route.abort();
  });

  await page.goto("/e2e/component-harness.html");
  const importer = page.getByRole("region", { name: "导入聊天记录" });
  await importer.getByRole("radio", { name: "粘贴文本" }).check();
  await importer.getByRole("textbox", { name: "聊天文本" }).fill(
    "[10:20] A：保留的浏览器测试步骤\n[10:21] B：应被排除的浏览器测试步骤\n",
  );
  await importer.getByLabel("默认日期").fill("2026-09-13");
  await importer.getByLabel("默认会话名称").fill("手动导入测试群");
  await importer.getByRole("button", { name: "生成导入预览" }).click();

  const preview = importer.getByRole("region", { name: "导入预览" });
  await expect(preview.getByRole("status")).toContainText("可导入 2 条");
  await expect(preview).toContainText("文本 2 · 图片 0 · 文件 0 · 其他 0 · 未知 0");
  await preview.getByRole("checkbox", { name: /确认按此预览导入/ }).check();
  await preview.getByRole("button", { name: "确认导入本地索引" }).click();

  await expect(importer.getByText(/导入完成/)).toBeVisible();
  const workspace = importer.getByRole("region", { name: "导入聊天的搜索与生成" });
  await workspace.getByRole("checkbox", { name: "选择 手动导入测试群" }).check();
  await workspace.getByRole("searchbox", { name: "搜索聊天内容" }).fill("测试步骤");
  await workspace.getByRole("button", { name: "搜索本地聊天" }).click();

  const results = workspace.getByRole("region", { name: "搜索结果" });
  await expect(results.getByRole("listitem")).toHaveCount(2);
  await results.getByRole("checkbox", { name: /不用于 AI 生成：应被排除/ }).check();

  const generation = workspace.getByRole("region", { name: "AI 主题整理" });
  await expect(generation).toContainText("排除：1 条消息");
  await generation.getByLabel("操作主题").fill("整理测试步骤");
  await generation.getByRole("button", { name: "预览 AI 外发" }).click();
  const outbound = generation.getByRole("region", { name: "AI 外发预览" });
  await expect(outbound).toContainText("发送 1 条纯文本");
  await outbound.getByText(/查看将要外发的完整脱敏文本/).click();
  const payload = outbound.getByRole("region", { name: "外发分块 1" });
  await expect(payload).toContainText("保留的浏览器测试步骤");
  await expect(payload).not.toContainText("应被排除的浏览器测试步骤");
  await outbound.getByRole("checkbox", { name: /授权本次向外部 AI 发送/ }).check();
  await outbound.getByRole("button", { name: "确认并生成" }).click();

  await expect(generation.getByRole("status")).toContainText("已完成");
  await expect(generation.getByRole("region", { name: "AI 生成结果" })).toContainText(
    "AI 生成 · 本地证据已验证",
  );
  const editor = generation.getByRole("region", { name: "文档编辑与历史" });
  await editor.getByRole("button", { name: "创建可编辑文档" }).click();
  await expect(editor.getByLabel("文档标题")).toHaveValue("确定性测试：整理测试步骤");
  await editor.getByLabel("文档标题").fill("刷新恢复测试 version 2");
  await editor.getByRole("button", { name: "保存为新版本" }).click();
  await expect(editor.getByText("版本 2")).toBeVisible();

  await importer.getByRole("textbox", { name: "聊天文本" }).fill("[11:00] C：第二个导入批次\n");
  await importer.getByLabel("默认会话名称").fill("第二个手动导入群");
  await importer.getByRole("button", { name: "生成导入预览" }).click();
  const secondPreview = importer.getByRole("region", { name: "导入预览" });
  await secondPreview.getByRole("checkbox", { name: /确认按此预览导入/ }).check();
  await secondPreview.getByRole("button", { name: "确认导入本地索引" }).click();
  await expect(importer.getByText("第二个手动导入群", { exact: false }).first()).toBeVisible();

  await page.reload();
  const restoredImporter = page.getByRole("region", { name: "导入聊天记录" });
  await expect(restoredImporter.getByText("第二个手动导入群", { exact: false }).first()).toBeVisible();
  await expect(restoredImporter.getByText("手动导入测试群", { exact: false }).first()).toBeVisible();
  const library = restoredImporter.getByRole("region", { name: "已生成文档" });
  await expect(library.getByText("刷新恢复测试 version 2", { exact: true })).toBeVisible();
  await library.getByRole("button", { name: "打开最新版本" }).click();
  await expect(library.getByLabel("文档标题")).toHaveValue("刷新恢复测试 version 2");

  const newestDeletion = restoredImporter.getByRole("region", { name: "删除这次导入及本地索引" });
  await newestDeletion.getByRole("button", { name: "查看删除影响" }).click();
  await newestDeletion.getByRole("checkbox", { name: /我已核对上述影响/ }).check();
  await newestDeletion.getByRole("button", { name: "确认删除" }).click();
  const olderDeletion = restoredImporter.getByRole("region", { name: "删除已导入批次 1" });
  await olderDeletion.getByRole("button", { name: "查看删除影响" }).click();
  await expect(olderDeletion.getByLabel("删除影响清单")).toContainText("保留正文、全部版本");
  await olderDeletion.getByRole("checkbox", { name: /我已核对上述影响/ }).check();
  await olderDeletion.getByRole("button", { name: "确认删除" }).click();
  await expect(restoredImporter.getByRole("region", { name: "导入聊天的搜索与生成" })).toHaveCount(0);

  await expect(library.getByLabel("文档标题")).toHaveValue("刷新恢复测试 version 2");
  expect(externalRequests).toHaveLength(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});
