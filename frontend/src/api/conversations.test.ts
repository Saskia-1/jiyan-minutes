import { describe, expect, it } from "vitest";
import { createConversationClient } from "./conversations";
const summary = { conversation_id: "c1", title: "新对话", revision: 1, created_at: "2026-10-23T00:00:00Z", updated_at: "2026-10-23T00:00:00Z" };
const conversation = { ...summary, sources: [], messages: [], active_run: null };
describe("conversation transport", () => {
  it("uses the fixed conversation routes and exact approval payload", async () => {
    const calls: { path: string; body: unknown }[] = [];
    const api = createConversationClient(async (path, parse, body) => { calls.push({ path, body }); return parse(path.endsWith("conversations") && body === undefined ? { conversations: [summary] } : conversation); });
    expect((await api.listAgentConversations()).conversations[0].conversation_id).toBe("c1");
    await api.createAgentConversation(); await api.agentConversation("c1");
    await api.setAgentConversationSources("c1", [{ kind: "chat_snapshot", id: "s1", label: "资料" }]);
    await api.sendAgentMessage("c1", "整理资料", "client1");
    await api.approveAgentRun("c1", "r1", "a1", "a".repeat(64)); await api.cancelAgentRun("c1", "r1");
    expect(calls).toEqual([
      { path: "/api/agent/conversations", body: undefined }, { path: "/api/agent/conversations", body: {} },
      { path: "/api/agent/conversations/c1", body: undefined },
      { path: "/api/agent/conversations/c1/sources", body: { sources: [{ kind: "chat_snapshot", id: "s1", label: "资料" }] } },
      { path: "/api/agent/conversations/c1/messages", body: { text: "整理资料", client_message_id: "client1" } },
      { path: "/api/agent/conversations/c1/runs/r1/approve", body: { approval_id: "a1", preview_fingerprint: "a".repeat(64), confirmed: true } },
      { path: "/api/agent/conversations/c1/runs/r1/cancel", body: {} },
    ]);
  });
  it("rejects internal response fields and malformed approvals", async () => {
    const api = createConversationClient(async (_path, parse) => parse({ ...conversation, internal_state: {} }));
    await expect(api.agentConversation("c1")).rejects.toThrow();
    await expect(api.approveAgentRun("c1", "r1", "a1", "bad")).rejects.toThrow();
  });
});

it("preserves CSRF for conversation reads after refreshing an expired browser session", async () => {
  const { createClient } = await import("./client");
  const { vi } = await import("vitest");
  let session = 0; let reads = 0;
  vi.stubGlobal("fetch", vi.fn(async (path: string, options?: RequestInit) => {
    if (path === "/api/session") return new Response(JSON.stringify({ csrf_token: `csrf-${++session}`, expires_at: "2099-01-01T00:00:00Z" }), { status: 200 });
    expect(new Headers(options?.headers).get("X-CSRF-Token")).toBe(`csrf-${session}`);
    return ++reads === 1 ? new Response(null, { status: 401 }) : new Response(JSON.stringify({ conversations: [] }), { status: 200 });
  }));
  await expect(createClient().listAgentConversations()).resolves.toEqual({ conversations: [] });
});

function cachedApproval(kind: "agent" | "generation", provider_calls: number, outbound_payloads: string[]) {
  return { ...conversation, active_run: { run_id: "r1", revision: 1, state: "waiting_approval", stage: "generation_authorization", error_code: null, approval: { approval_id: "a1", kind, preview_fingerprint: "a".repeat(64), provider: "configured", model: "synthetic", outbound_payloads, provider_calls, max_output_tokens: 1000, expires_at: "2099-01-01T00:00:00Z" }, document_id: null, version_id: null, artifact_revision: null } };
}
it("accepts generation cache reuse with zero external calls and empty payloads", async () => {
  const value = cachedApproval("generation", 0, []);
  const api = createConversationClient(async (_path, parse) => parse(value));
  expect((await api.agentConversation("c1")).active_run?.approval).toMatchObject({ kind: "generation", provider_calls: 0, outbound_payloads: [] });
});
it.each([
  ["agent", 0, []], ["agent", 1, []], ["agent", 0, ["full payload"]],
  ["generation", 0, ["full payload"]], ["generation", 1, []],
] as const)("rejects contradictory approval kind=%s calls=%s payloads=%j", async (kind, calls, payloads) => {
  const api = createConversationClient(async (_path, parse) => parse(cachedApproval(kind, calls, [...payloads])));
  await expect(api.agentConversation("c1")).rejects.toThrow();
});
