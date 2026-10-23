import { test as base, expect } from "@playwright/test";

// Exercise the real first-use acknowledgment before each established flow.
export const test = base.extend<{ acknowledged: void }>({
  acknowledged: [async ({ page }, use) => {
    await page.goto("/");
    const dialog = page.getByRole("dialog", { name: "使用与合规提示" });
    await expect(dialog).toBeVisible();
    await dialog.getByRole("checkbox", { name: "我已知悉" }).check();
    await dialog.getByRole("button", { name: "继续使用" }).click();
    await use();
  }, { auto: true }],
});
export { expect };
export type { Response } from "@playwright/test";
