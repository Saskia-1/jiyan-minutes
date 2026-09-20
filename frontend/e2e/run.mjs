/** Explicit synthetic-only runner; no browser installation and no user data. */
import { mkdtemp, chmod } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawn } from "node:child_process";

const args = process.argv.slice(2).filter(value => value !== "--");
const listing = args.includes("--list");
const root = listing ? "" : await mkdtemp(join(tmpdir(), "chat-to-howto-synthetic-e2e-"));
if (root) await chmod(root, 0o700);
const child = spawn(process.execPath, ["node_modules/@playwright/test/cli.js", "test", ...args], {
  stdio: "inherit",
  env: { ...process.env, P0_SYNTHETIC_E2E: "1", P0_E2E_RUN_ROOT: root, PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD: "1" },
});
child.on("error", () => { process.exitCode = 1; });
child.on("exit", code => { process.exitCode = code ?? 1; });
// Retain this newly allocated private synthetic directory for local inspection.
// Never enumerate or clean other temporary directories or user output roots.
