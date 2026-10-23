import { test, expect } from "./acknowledged-test";

// Real browser geometry and the real local parser, with synthetic chats only.
test("长聊天粘贴与窄低屏下输入区不越过已展开的高级工具", async ({ page }) => {
  const viewport = page.viewportSize()!;
  await page.setViewportSize({ width: viewport.width, height: 620 });
  await page.goto("/");
  const input = page.getByRole("textbox", { name: "发送消息", exact: true });
  await expect(input).toBeEnabled();
  await page.getByText("设置与高级工具", { exact: true }).click();
  await expect(page.getByRole("heading", { name: "高级资料与文档工具", exact: true })).toBeVisible();
  for (let index = 0; index < 3; index++) {
    const chat = `甲：\n${Array.from({ length: 100 }, (_, row) => `合成聊天第${index + 1}批第${row + 1}行，讨论复盘方法。`).join("\n")}\n\n乙：\n这是用于布局回归的合成回复。`;
    await input.evaluate((element, text) => {
      const data = new DataTransfer(); data.setData("text/plain", text);
      element.dispatchEvent(new ClipboardEvent("paste", { clipboardData: data, bubbles: true, cancelable: true }));
    }, chat);
    await expect(page.getByRole("region", { name: "本会话资料", exact: true }).locator(".chat-source-chip")).toHaveCount(index + 1);
    await expect(page.getByRole("region", { name: "导入确认", exact: true })).toHaveCount(0);
    await expect(input).toBeEnabled();
  }
  await input.fill("请整理这些合成讨论中的观点，并保留出处。");
  // Pressure the existing main textarea to its maximum supported height.
  await input.evaluate(element => { element.style.height = "170px"; });
  for (const height of [620, 520]) {
    await page.setViewportSize({ width: viewport.width, height });
    await page.evaluate(() => window.scrollTo(0, 0));
    const layout = await page.evaluate(() => {
      const bounds = (selector: string) => { const r = document.querySelector(selector)!.getBoundingClientRect(); return { top: r.top, bottom: r.bottom, left: r.left, right: r.right }; };
      return { shell: bounds(".conversation-shell"), area: bounds(".chat-composer-area"), composer: bounds(".chat-composer"), advanced: bounds(".chat-settings"), content: bounds(".chat-settings-content"), width: innerWidth, documentWidth: document.documentElement.scrollWidth };
    });
    expect(layout.area.top).toBeGreaterThanOrEqual(layout.shell.top);
    expect(layout.area.bottom, JSON.stringify(layout)).toBeLessThanOrEqual(layout.shell.bottom + 1);
    expect(layout.composer.bottom).toBeLessThanOrEqual(layout.shell.bottom + 1);
    expect(layout.advanced.top).toBeGreaterThanOrEqual(layout.shell.bottom - 1);
    expect(layout.content.top).toBeGreaterThanOrEqual(layout.shell.bottom - 1);
    expect(layout.area.bottom).toBeLessThanOrEqual(layout.advanced.top + 1);
    expect(layout.documentWidth).toBeLessThanOrEqual(layout.width);
  }
  await expect(page.getByRole("region", { name: "本会话资料", exact: true }).locator(".chat-source-chip")).toHaveCount(3);
  await expect(page.getByRole("region", { name: "已导入资料", exact: true })).toHaveCount(0);
  await expect(page.getByRole("region", { name: "外发授权", exact: true })).toHaveCount(0);
});

test("直接粘贴聊天后自动导入，再输入整理要求自动交付", async ({ page }) => {
  test.setTimeout(60_000);
  await page.goto("/");
  const input = page.getByRole("textbox", { name: "发送消息", exact: true });
  await expect(input).toBeEnabled();
  await input.evaluate(element => {
    const data = new DataTransfer(); data.setData("text/plain", "甲：\n合成创业讨论：先验证需求，再记录反馈。\n\n乙：\n合成回复：创业复盘时保留不同观点。");
    element.dispatchEvent(new ClipboardEvent("paste", { clipboardData: data, bubbles: true, cancelable: true }));
  });
  await expect(page.getByRole("region", { name: "本会话资料", exact: true })).toContainText("对话框聊天记录");
  await expect(page.getByRole("region", { name: "导入确认", exact: true })).toHaveCount(0);
  await input.fill("请整理合成讨论中的创业复盘观点。");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page.getByRole("link", { name: "下载整理内容.txt", exact: true })).toHaveCount(1, { timeout: 45_000 });
  await expect(page.locator(".chat-artifact-body")).toHaveCount(1);
  await expect(page.getByRole("region", { name: "外发授权", exact: true })).toHaveCount(0);
});
