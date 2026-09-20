import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom", environmentOptions: { jsdom: { url: "http://127.0.0.1:4173/" } },
    setupFiles: ["./src/test/setup.ts"], include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
    restoreMocks: true, clearMocks: true,
  },
});
