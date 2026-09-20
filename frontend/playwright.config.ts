import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e", fullyParallel: false, workers: 1, retries: 0,
  timeout: 30_000, reporter: "line", outputDir: "test-results",
  use: { baseURL: "http://127.0.0.1:4173", channel: "chrome", headless: true, screenshot: "off", video: "off", trace: "off" },
  projects: [
    { name: "chrome-desktop", use: { viewport: { width: 1280, height: 900 } } },
    { name: "chrome-360", use: { viewport: { width: 360, height: 800 }, reducedMotion: "reduce" } },
  ],
  webServer: [
    { command: "../backend/.venv/bin/python e2e/synthetic_server.py", port: 18765, reuseExistingServer: false, timeout: 20_000, stdout: "ignore", stderr: "pipe" },
    { command: `"${process.execPath}" node_modules/vite/bin/vite.js --host 127.0.0.1`, url: "http://127.0.0.1:4173", reuseExistingServer: false, timeout: 20_000 },
  ],
});
