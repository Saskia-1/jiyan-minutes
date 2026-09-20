import { test, expect } from "@playwright/test";
import { createHash } from "node:crypto";
import { readFile, readdir, stat } from "node:fs/promises";
import { join } from "node:path";

const sourcePath = process.env.P0_REAL_CHAT_FILE;
const expectedSha = process.env.P0_REAL_CHAT_SHA256;
const exclusionText = process.env.P0_REAL_EXCLUSION_TEXT;
const runRoot = process.env.P0_E2E_RUN_ROOT;

test.skip(!sourcePath || !expectedSha || !exclusionText || !runRoot, "explicit real-content acceptance inputs are required");

test("P0 原始 TXT 直接导入、生成与刷新恢复", async ({ page }) => {
  test.setTimeout(60_000);
  const origin = "http://127.0.0.1:4173";
  const externalRequests: string[] = [];
  await page.route("**/*", route => {
    if (new URL(route.request().url()).origin === origin) return route.continue();
    externalRequests.push("forbidden_external_request");
    return route.abort();
  });

  const before = createHash("sha256").update(await readFile(sourcePath!)).digest("hex");
  expect(before).toBe(expectedSha);

  await page.goto("/");
  await page.getByLabel("保存位置").selectOption("fixture-root");
  const importer = page.getByRole("region", { name: "导入聊天记录" });
  await importer.getByLabel("聊天记录文件").setInputFiles(sourcePath!);
  await importer.getByLabel("默认年份").fill("2026");
  await importer.getByLabel("默认会话名称").fill("cs发展路径");
  await importer.getByRole("button", { name: "生成导入预览" }).click();
  const preview = importer.getByRole("region", { name: "导入预览" });
  await expect(preview.getByRole("status")).toContainText("共 88 行 · 可导入 88 条 · 未解析 0 行 · 重复 0 条 · 发送者 11 个");
  await preview.getByRole("checkbox", { name: /确认按此预览导入/ }).check();
  await preview.getByRole("button", { name: "确认导入本地索引" }).click();

  const workspace = importer.getByRole("region", { name: "导入聊天的搜索与生成" });
  await workspace.getByRole("checkbox", { name: "选择 cs发展路径" }).check();

  async function search(query: string, expected: number) {
    await workspace.getByRole("searchbox", { name: "搜索聊天内容" }).fill(query);
    await workspace.getByRole("button", { name: "搜索本地聊天" }).click();
    await expect(workspace.getByRole("region", { name: "搜索结果" }).getByRole("listitem")).toHaveCount(expected);
  }
  await search("就业", 10);
  await search("保研", 6);

  for (const [label, value] of [
    ["搜索开始时间", "2026-09-09T22:00:00"],
    ["搜索结束时间（不包含）", "2026-09-10T00:25:00"],
  ] as const) {
    await workspace.getByLabel(label).evaluate((element, next) => {
      const input = element as HTMLInputElement;
      input.value = next;
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
    }, value);
  }
  await workspace.getByRole("searchbox", { name: "搜索聊天内容" }).fill("");
  await workspace.getByRole("button", { name: "搜索本地聊天" }).click();
  const results = workspace.getByRole("region", { name: "搜索结果" });
  await expect(results.getByRole("listitem")).toHaveCount(25);
  for (const expected of [50, 75, 86]) {
    await results.getByRole("button", { name: "加载更多结果" }).click();
    await expect(results.getByRole("listitem")).toHaveCount(expected);
  }
  const excluded = results.getByRole("listitem").filter({ hasText: exclusionText! });
  await expect(excluded).toHaveCount(1);
  await excluded.getByRole("checkbox", { name: /不用于 AI 生成/ }).check();

  const generation = workspace.getByRole("region", { name: "AI 操作指南生成" });
  await expect(generation).toContainText("排除：1 条消息");
  await generation.getByLabel("操作主题").fill("根据聊天记录制作 2026 年 CS 发展路径指南，涵盖就业与保研");
  await generation.getByRole("button", { name: "预览 AI 外发" }).click();
  const outbound = generation.getByRole("region", { name: "AI 外发预览" });
  await expect(outbound).toContainText("发送 85 条纯文本");
  await outbound.getByRole("checkbox", { name: /授权本次向外部 AI 发送/ }).check();
  await outbound.getByRole("button", { name: "确认并生成" }).click();
  await expect(generation.getByRole("status")).toContainText("已完成", { timeout: 30_000 });
  await expect(generation.getByRole("region", { name: "AI 生成结果" })).toContainText("AI 生成 · 本地证据已验证");

  const editor = generation.getByRole("region", { name: "文档编辑与历史" });
  await editor.getByRole("button", { name: "创建可编辑文档" }).click();
  await editor.getByLabel("文档标题").fill("2026年 CS 发展路径指南");
  await editor.getByLabel("步骤 1").fill("根据聊天记录制定兼顾就业与保研的 CS 发展路径计划。");
  await editor.getByRole("button", { name: "保存为新版本" }).click();
  await expect(editor.getByText("版本 2")).toBeVisible();
  for (const format of ["markdown", "docx"] as const) {
    await editor.getByLabel("导出格式").selectOption(format);
    await editor.getByRole("button", { name: "导出当前版本" }).click();
    await expect(editor.getByRole("status")).toContainText(format === "markdown" ? ".md" : ".docx");
  }

  await page.reload();
  await page.getByLabel("保存位置").selectOption("fixture-root");
  const restoredImporter = page.getByRole("region", { name: "导入聊天记录" });
  await expect(restoredImporter.getByText("cs发展路径", { exact: false }).first()).toBeVisible();
  const restoredWorkspace = restoredImporter.getByRole("region", { name: "导入聊天的搜索与生成" });
  await restoredWorkspace.getByRole("checkbox", { name: "选择 cs发展路径" }).check();
  await restoredWorkspace.getByRole("searchbox", { name: "搜索聊天内容" }).fill("就业");
  await restoredWorkspace.getByRole("button", { name: "搜索本地聊天" }).click();
  await expect(restoredWorkspace.getByRole("region", { name: "搜索结果" }).getByRole("listitem")).toHaveCount(10);

  const library = restoredImporter.getByRole("region", { name: "已生成文档" });
  await expect(library.getByText("2026年 CS 发展路径指南", { exact: true })).toBeVisible();
  await library.getByRole("button", { name: "打开最新版本" }).click();
  await expect(library.getByLabel("文档标题")).toHaveValue("2026年 CS 发展路径指南");
  await expect(library.getByText("版本 2", { exact: true })).toBeVisible();
  await library.getByLabel("文档标题").fill("2026年 CS 发展路径指南（刷新后续编）");
  await library.getByRole("button", { name: "保存为新版本" }).click();
  await expect(library.getByText("版本 3", { exact: true })).toBeVisible();
  for (const format of ["markdown", "docx"] as const) {
    await library.getByLabel("导出格式").selectOption(format);
    await library.getByRole("button", { name: "导出当前版本" }).click();
    await expect(library.getByRole("status")).toContainText(format === "markdown" ? ".md" : ".docx");
  }

  const artifactRoot = join(runRoot!, "approved-output", "documents");
  const artifacts = await readdir(artifactRoot);
  expect(artifacts.filter(name => name.endsWith(".md"))).toHaveLength(2);
  expect(artifacts.filter(name => name.endsWith(".docx"))).toHaveLength(2);
  for (const name of artifacts) expect((await stat(join(artifactRoot, name))).size).toBeGreaterThan(0);

  const after = createHash("sha256").update(await readFile(sourcePath!)).digest("hex");
  expect(after).toBe(before);
  expect(externalRequests).toHaveLength(0);
  console.log(JSON.stringify({ input: 88, time_filtered_out: 2, after_time_filter: 86,
    explicitly_excluded: 1, ignored_empty_or_system: 0, sent_to_fake_ai: 85 }));
});
