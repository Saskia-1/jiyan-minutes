import { expect, it, vi } from "vitest";
import { ApiError, createClient } from "./client";

const disclosure = {
  source_id: "qq", tool: "QCE", revision: "fixture-contract-1", hash: "1".repeat(64),
  risks: ["qce_local_history_may_be_incomplete"],
  sections: {
    source_version: "版本与许可",
    access_impact: "访问影响",
    scope_overread: "范围读取",
    storage_cleanup: "保存清理",
    permissions_network: "权限联网",
    action_stop: "操作停止",
  },
  blockers: [] as string[],
};
const setupOptions = {
  approved_output_roots: [{ output_root_id: "fixture-root", label: "批准目录" }],
  source_disclosures: { qq: disclosure, wechat: { ...disclosure, source_id: "wechat", tool: "TraceMemo", hash: "2".repeat(64) } },
};
const configuredSource = {
  source_id: "qq", status: "needs_user_action", account_id: "account-1",
  base_url: "http://127.0.0.1:40653",
  client_version: "client-1", tool_version: "tool-1", account_version: "account-version-1",
  secret_configured: false,
};
const configuredWechat = { ...configuredSource, source_id: "wechat", base_url: "http://127.0.0.1:6131" };

function asRequest(input: RequestInfo | URL, init?: RequestInit) {
  return input instanceof Request ? input : new Request(new URL(String(input), location.href), init);
}
function json(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
}

it("文档下载绑定同源 CSRF，401 后仅在下次明确下载时恢复会话", async () => {
  let sessions = 0, downloads = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = asRequest(input, init);
    const path = new URL(request.url).pathname;
    if (path === "/api/session") return json({ csrf_token: `csrf-${++sessions}`, expires_at: "2099-01-01T00:00:00Z" });
    expect(path).toBe("/api/documents/document%3Aa/download");
    expect(request.method).toBe("POST");
    expect(request.headers.get("X-CSRF-Token")).toBe(`csrf-${sessions}`);
    expect(init?.credentials).toBe("same-origin");
    expect(await request.json()).toEqual({ version_id: "version:2", format: "docx", source_mode: "body_only" });
    return ++downloads === 1 ? new Response(null, { status: 401 }) : new Response("DOCX bytes");
  }));
  const api = createClient();
  await expect(api.downloadDocument("document:a", "version:2", "docx", "body_only")).rejects.toMatchObject({ status: 401 });
  expect(downloads).toBe(1);
  const file = await api.downloadDocument("document:a", "version:2", "docx", "body_only");
  expect(file.size).toBe(10);
  expect(sessions).toBe(2);
});

it("截图与笔记客户端按严格契约发送预览和 PATCH 校对，不接受额外响应字段", async () => {
  const id = "a".repeat(32);
  const unit = { unit_id: "candidate-0", kind: "note_passage", group_id: "note-1", text: "正文",
    sender_name: null, sent_at: null, order: 0, image_ref: null };
  const preview = { preview_id: id, preview_fingerprint: "f".repeat(64), suggested_kind: "note", status: "ready",
    units: [unit], images: [], warnings: [], expires_at_utc: "2099-01-01T00:00:00Z" };
  const requests: Request[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = asRequest(input, init); requests.push(request.clone());
    const path = new URL(request.url).pathname;
    if (path === "/api/session") return json({ csrf_token: "csrf-1", expires_at: "2099-01-01T00:00:00Z" });
    if (path === "/api/content-imports/previews") return json(preview, 201);
    if (path === `/api/content-imports/previews/${id}`) return json({ ...preview, preview_fingerprint: "e".repeat(64) });
    if (path === "/api/content-imports/recovery") return json({ items: [], next_cursor: null, unexpected: "not allowed" });
    throw new Error(`unexpected_route_${path}`);
  }));
  const client = createClient();
  expect((await client.previewContentImport({ pasted_note: "正文" })).units[0].text).toBe("正文");
  expect((await client.reviseContentPreview(id, [{ unit_id: unit.unit_id, kind: "note_passage", group_id: unit.group_id,
    text: unit.text, sender_name: null, sent_at: null, order: 0 }], [])).preview_fingerprint).toBe("e".repeat(64));
  await expect(client.recoverContentBatches()).rejects.toBeInstanceOf(ApiError);
  expect(requests[2].method).toBe("PATCH");
  expect(requests[2].headers.get("X-CSRF-Token")).toBe("csrf-1");
});

it("混合检索保持笔记与聊天的类型，并拒绝重复的同一来源", async () => {
  const batch = "a".repeat(32);
  const requests: Request[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = asRequest(input, init); requests.push(request.clone());
    if (new URL(request.url).pathname === "/api/session") return json({ csrf_token: "csrf-1", expires_at: "2099-01-01T00:00:00Z" });
    return json({ hits: [{ unit_id: "content:v1:note:" + "b".repeat(64), source_kind: "note_passage",
      source_ref: { kind: "content_batch", id: batch }, text: "合成笔记", sender_name: null,
      timestamp: null, source_order: 0, image_id: null }] });
  }));
  const client = createClient();
  const source_refs = [{ kind: "content_batch" as const, id: batch }];
  const result = await client.searchMixed({ source_refs, include_undated: true, terms: ["笔记"] });
  expect(result.hits[0].source_kind).toBe("note_passage");
  expect(await requests[1].json()).toEqual({ source_refs, include_undated: true, terms: ["笔记"] });
  await expect(client.searchMixed({ source_refs: [...source_refs, ...source_refs], include_undated: true, terms: ["笔记"] })).rejects.toBeInstanceOf(ApiError);
  expect(requests).toHaveLength(2);
});

it("所有并发保护 GET 先共享一次 session bootstrap", async () => {
  const paths: string[] = [];
  let bootstrapped = false;
  const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = asRequest(input, init);
    const path = new URL(request.url).pathname;
    paths.push(path);
    if (path === "/api/session") {
      bootstrapped = true;
      return json({ csrf_token: "csrf-1", expires_at: "2099-01-01T00:00:00Z" });
    }
    if (!bootstrapped) return json({ code: "request_rejected" }, 401);
    if (path === "/api/setup-options") return json(setupOptions);
    if (path === "/api/sources") return json({ sources: [configuredSource, configuredWechat] });
    throw new Error("unexpected_route");
  });
  vi.stubGlobal("fetch", fetch);

  const client = createClient();
  await Promise.all([client.setupOptions(), client.sources()]);

  expect(paths.filter(path => path === "/api/session")).toHaveLength(1);
  expect(paths[0]).toBe("/api/session");
});

it("bootstrap 返回已过期 lease 时不发送任何保护请求", async () => {
  let protectedReads = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = asRequest(input, init);
    if (new URL(request.url).pathname === "/api/session") return json({ csrf_token: "csrf-stale", expires_at: "2000-01-01T00:00:00Z" });
    protectedReads++;
    return json({ sources: [] });
  }));

  await expect(createClient().sources()).rejects.toBeInstanceOf(ApiError);
  expect(protectedReads).toBe(0);
});

it("缓存的 session 到期后先重新 bootstrap，再发送下一次保护请求", async () => {
  let now = Date.parse("2026-09-11T00:00:00Z");
  const nowSpy = vi.spyOn(Date, "now").mockImplementation(() => now);
  const paths: string[] = [];
  let sessions = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = asRequest(input, init);
    const path = new URL(request.url).pathname;
    paths.push(path);
    if (path === "/api/session") {
      sessions++;
      return json({ csrf_token: `csrf-${sessions}`, expires_at: new Date(now + 1_000).toISOString() });
    }
    if (path === "/api/sources") return json({ sources: [configuredSource, configuredWechat] });
    throw new Error("unexpected_route");
  }));

  try {
    const client = createClient();
    await client.sources();
    now += 2_000;
    await client.sources();

    expect(paths).toEqual(["/api/session", "/api/sources", "/api/session", "/api/sources"]);
  } finally {
    nowSpy.mockRestore();
  }
});

it("GET 收到 401 后换新 session 并只安全重放一次 GET", async () => {
  let sessions = 0;
  let setupReads = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = asRequest(input, init);
    const path = new URL(request.url).pathname;
    if (path === "/api/session") return json({ csrf_token: `csrf-${++sessions}`, expires_at: "2099-01-01T00:00:00Z" });
    if (path === "/api/setup-options" && ++setupReads === 1) return json({ code: "expired" }, 401);
    if (path === "/api/setup-options") return json(setupOptions);
    throw new Error("unexpected_route");
  }));

  await expect(createClient().setupOptions()).resolves.toEqual(setupOptions);
  expect(sessions).toBe(2);
  expect(setupReads).toBe(2);
});

it("列会话 GET 携带 CSRF 且 401 后不重放一次性授权", async () => {
  let sessions = 0;
  let conversationReads = 0;
  let observedCsrf = "";
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = asRequest(input, init);
    const path = new URL(request.url).pathname;
    if (path === "/api/session") {
      return json({ csrf_token: `csrf-${++sessions}`, expires_at: "2099-01-01T00:00:00Z" });
    }
    if (path === "/api/sources/qq/conversations") {
      conversationReads++;
      observedCsrf = request.headers.get("X-CSRF-Token") ?? "";
      return json({ code: "expired" }, 401);
    }
    throw new Error("unexpected_route");
  }));

  await expect(createClient().conversations("qq", "account-1")).rejects.toMatchObject({ status: 401 });
  expect(observedCsrf).toBe("csrf-1");
  expect(conversationReads).toBe(1);
  expect(sessions).toBe(1);
});

it("敏感 POST 收到 401 后不重放，并让下一次明确操作重新 bootstrap", async () => {
  let sessions = 0;
  let mutations = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = asRequest(input, init);
    const path = new URL(request.url).pathname;
    if (path === "/api/session") return json({ csrf_token: `csrf-${++sessions}`, expires_at: "2099-01-01T00:00:00Z" });
    if (path === "/api/sources" && request.method === "POST") {
      mutations++;
      return json({ code: "expired" }, 401);
    }
    if (path === "/api/setup-options") return json(setupOptions);
    throw new Error("unexpected_route");
  }));
  const client = createClient();

  await expect(client.configure({
    source_id: "qq", account_id: "account-1", base_url: "http://127.0.0.1:40653",
    client_version: "client-1", tool_version: "tool-1", account_version: "account-version-1",
  })).rejects.toMatchObject({ status: 401 });
  expect(mutations).toBe(1);
  expect(sessions).toBe(1);

  await expect(client.setupOptions()).resolves.toEqual(setupOptions);
  expect(mutations).toBe(1);
  expect(sessions).toBe(2);
});

it("取消与任务完成竞态时接受后端返回的真实终态，而不伪报已取消", async () => {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = asRequest(input, init);
    const path = new URL(request.url).pathname;
    if (path === "/api/session") return json({ csrf_token: "csrf-1", expires_at: "2099-01-01T00:00:00Z" });
    if (path === "/api/sources/qq/verification-jobs/job-1/cancel") {
      return json({ job_id: "job-1", state: "complete" });
    }
    throw new Error("unexpected_route");
  }));

  await expect(createClient().cancel("qq", "job-1")).resolves.toEqual({ job_id: "job-1", state: "complete" });
});

it("运行时拒绝未列入契约的来源状态和证据类别", async () => {
  let invalid: "status" | "evidence" = "status";
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = asRequest(input, init);
    const path = new URL(request.url).pathname;
    if (path === "/api/session") return json({ csrf_token: "csrf-1", expires_at: "2099-01-01T00:00:00Z" });
    if (path === "/api/sources") return json({ sources: [{ ...configuredSource, status: "ready_from_health" }] });
    if (path.endsWith("/verification-jobs/job-1")) return json({
      job_id: "job-1", source_id: "qq", client_version: "client-1", tool_version: "tool-1",
      account_version: "account-version-1", range_start: "2026-09-01T00:00:00Z",
      range_end: "2026-09-01T00:00:01Z", timezone: "Asia/Shanghai", count: 1,
      sha256: "a".repeat(64), hash_ok: true, range_ok: true, completeness: "source_range_verified",
      state: "complete", evidence_class: "browser_claimed_real_device",
    });
    throw new Error("unexpected_route");
  }));
  const client = createClient();

  await expect(client.sources()).rejects.toBeInstanceOf(ApiError);
  invalid = "evidence";
  expect(invalid).toBe("evidence");
  await expect(client.job("qq", "job-1")).rejects.toBeInstanceOf(ApiError);
});

it("运行时字段白名单拒绝来源、任务和秘密披露中的额外字段", async () => {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = asRequest(input, init);
    const path = new URL(request.url).pathname;
    if (path === "/api/session") return json({ csrf_token: "csrf-1", expires_at: "2099-01-01T00:00:00Z" });
    if (path === "/api/sources") return json({ sources: [configuredSource, configuredWechat], private_path: "/not-public" });
    if (path.endsWith("/verification-jobs/job-1")) return json({
      job_id: "job-1", source_id: "qq", client_version: "client-1", tool_version: "tool-1",
      account_version: "account-version-1", range_start: "2026-09-01T00:00:00Z",
      range_end: "2026-09-01T00:00:01Z", timezone: "Asia/Shanghai", count: 1,
      sha256: "a".repeat(64), hash_ok: true, range_ok: true, completeness: "source_range_verified",
      state: "complete", evidence_class: "real_device", message_text: "must-not-reach-the-UI",
    });
    if (path.endsWith("/secret-consents")) return json({ consent_challenge: "challenge-2", expires_at: "2099-01-01T00:00:00Z",
      disclosure: { source_id: "qq", tool: "QCE", operation: "secret_get", secret_backend: "fixture-memory", revision: "secret-1",
        sections: { storage: "storage", scope: "scope", cleanup: "cleanup" }, risks: ["server_side"], hash: "3".repeat(64), value: "not-a-public-field" } });
    throw new Error("unexpected_route");
  }));
  const client = createClient();

  await expect(client.sources()).rejects.toBeInstanceOf(ApiError);
  await expect(client.job("qq", "job-1")).rejects.toBeInstanceOf(ApiError);
  await expect(client.secretConsent("qq", "account-1", "secret_get")).rejects.toBeInstanceOf(ApiError);
});

it("秘密操作只接受无正文 204，拒绝任何疑似秘密回传", async () => {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = asRequest(input, init);
    const path = new URL(request.url).pathname;
    if (path === "/api/session") return json({ csrf_token: "csrf-1", expires_at: "2099-01-01T00:00:00Z" });
    if (path.endsWith("/secret-operations")) return json({ value: "must-not-round-trip" }, 200);
    throw new Error("unexpected_route");
  }));
  const client = createClient();
  const secretDisclosure = {
    source_id: "qq" as const, tool: "QCE", operation: "secret_set" as const, secret_backend: "fixture-memory", revision: "secret-1",
    sections: { storage: "storage", scope: "scope", cleanup: "cleanup" }, risks: ["server_side"], hash: "3".repeat(64),
  };

  await expect(client.saveSecret("qq", "account-1", "challenge-1", "synthetic-input", secretDisclosure)).rejects.toBeInstanceOf(ApiError);
});

it("运行时拒绝缺少独立来源行和未列入契约的会话类型", async () => {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = asRequest(input, init);
    const path = new URL(request.url).pathname;
    if (path === "/api/session") return json({ csrf_token: "csrf-1", expires_at: "2099-01-01T00:00:00Z" });
    if (path === "/api/sources") return json({ sources: [configuredSource] });
    if (path.endsWith("/conversations")) return json({ conversations: [{
      conversation_selection_id: "opaque-1", display_name: "本地会话", account_id: "account-1",
      conversation_type: "untrusted_type",
    }] });
    throw new Error("unexpected_route");
  }));
  const client = createClient();

  await expect(client.sources()).rejects.toBeInstanceOf(ApiError);
  await expect(client.conversations("qq", "account-1")).rejects.toBeInstanceOf(ApiError);
});

it("P1 多会话导出和搜索只发送 opaque selection handles", async () => {
  const requests: Request[] = [];
  const exportJob = {
    job_id: "job:p1", state: "complete", batch_id: "batch:p1", snapshot_id: "snapshot:p1",
    scope_fingerprint: "a".repeat(64), known_total: 2, completed_count: 2,
    fetched_count: 2, saved_count: 2, duplicate_count: 0, unknown_time_count: 0,
    parse_error_count: 0, manifest_sha256: "b".repeat(64), completeness: "source_range_verified",
    error_code: null, searchable: true, cancellation_requested: false, progress_percent: 100, revision: 7,
  };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = asRequest(input, init); requests.push(request.clone());
    const path = new URL(request.url).pathname;
    if (path === "/api/session") return json({ csrf_token: "csrf-1", expires_at: "2099-01-01T00:00:00Z" });
    if (path === "/api/export-jobs/consents/from-selections") return json({
      consent_id: "consent:p1", scope_fingerprint: "a".repeat(64), expires_at_utc: "2099-01-01T00:00:00Z",
    }, 201);
    if (path === "/api/export-jobs" && request.method === "POST") return json(exportJob, 202);
    if (path === "/api/export-jobs/job%3Ap1") return json(exportJob);
    if (path === "/api/search/from-selections") return json({ results: [{
      message_id: `message:v1:${"c".repeat(64)}`, conversation_id: "internal-conversation-a",
      timestamp: "2026-09-01T00:00:01Z", sender_id: "sender-a", sender_name: "合成发送者",
      text: "合成搜索结果", batch_id: "batch:p1", snapshot_id: "snapshot:p1",
    }], next_cursor: null });
    throw new Error(`unexpected_route_${path}`);
  }));
  const client = createClient();
  const selection = {
    source_id: "qq" as const, account_id: "account-1",
    conversation_selection_ids: ["opaque-a", "opaque-b"],
    range_start: "2026-09-01T00:00:00Z", range_end: "2026-09-02T00:00:00Z",
    output_root_id: "fixture-root", display_timezone: "Asia/Shanghai",
  };

  const consent = await client.issueExportConsent(selection);
  await client.startExport(consent.consent_id, "idempotency-1");
  await client.exportJob("job:p1");
  const page = await client.searchChats({
    source_id: "qq", account_id: "account-1", query: "合成", conversation_selection_ids: ["opaque-a"],
    start_time: selection.range_start, end_time: selection.range_end, limit: 25, cursor: null,
  });

  expect(page.results[0].text).toBe("合成搜索结果");
  const bodies = await Promise.all(requests.filter(request => request.method === "POST").map(request => request.json()));
  expect(JSON.stringify(bodies)).not.toContain("internal-conversation-a");
  expect(bodies[0].selection.conversation_selection_ids).toEqual(["opaque-a", "opaque-b"]);
  expect(bodies[1]).toEqual({ consent_id: "consent:p1", idempotency_key: "idempotency-1" });
  expect(bodies[2].conversation_selection_ids).toEqual(["opaque-a"]);
  expect(requests.filter(request => request.method === "POST").every(request => request.headers.get("X-CSRF-Token") === "csrf-1")).toBe(true);
});

it("运行时拒绝已配置来源缺失固定 loopback base_url", async () => {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = asRequest(input, init);
    const path = new URL(request.url).pathname;
    if (path === "/api/session") return json({ csrf_token: "csrf-1", expires_at: "2099-01-01T00:00:00Z" });
    if (path === "/api/sources") {
      const missingBaseUrl = { ...configuredSource } as Record<string, unknown>;
      delete missingBaseUrl.base_url;
      return json({ sources: [missingBaseUrl, { source_id: "wechat", status: "unconfigured", secret_configured: false }] });
    }
    throw new Error("unexpected_route");
  }));

  await expect(createClient().sources()).rejects.toBeInstanceOf(ApiError);
});

it("来源配置在发送前拒绝外部、localhost 别名和串源端点", async () => {
  const fetch = vi.fn(async () => json({ csrf_token: "csrf-1", expires_at: "2099-01-01T00:00:00Z" }));
  vi.stubGlobal("fetch", fetch);
  const client = createClient();
  const base = {
    source_id: "qq" as const, account_id: "account-1", client_version: "client-1",
    tool_version: "tool-1", account_version: "account-version-1",
  };

  for (const base_url of ["https://example.invalid", "http://localhost:40653", "http://127.0.0.1:6131"]) {
    await expect(client.configure({ ...base, base_url })).rejects.toBeInstanceOf(ApiError);
  }
  expect(fetch).not.toHaveBeenCalled();
});

it("运行时拒绝缺失或串源的 disclosure seal", async () => {
  let responseKind: "setup" | "verification" | "secret" = "setup";
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = asRequest(input, init);
    const path = new URL(request.url).pathname;
    if (path === "/api/session") return json({ csrf_token: "csrf-1", expires_at: "2099-01-01T00:00:00Z" });
    if (path === "/api/setup-options") {
      const missing = structuredClone(setupOptions) as any; delete missing.source_disclosures.qq.hash;
      return json(missing);
    }
    if (path.endsWith("/verification-consents")) return json({ consent_challenge: "challenge-1", scope_fingerprint: "scope-1",
      disclosure: { ...disclosure, source_id: "wechat" }, expires_at: "2099-01-01T00:00:00Z" });
    if (path.endsWith("/secret-consents")) return json({ consent_challenge: "challenge-2",
      disclosure: { source_id: "qq", tool: "TraceMemo", operation: "secret_set", secret_backend: "fixture-memory", revision: "secret-1",
        sections: { storage: "storage", scope: "scope", cleanup: "cleanup" }, risks: ["server_side"], hash: "3".repeat(64) },
      expires_at: "2099-01-01T00:00:00Z" });
    throw new Error(`unexpected_route_${responseKind}`);
  }));
  const client = createClient();

  await expect(client.setupOptions()).rejects.toBeInstanceOf(ApiError);
  responseKind = "verification";
  await expect(client.prepare("qq", {
    source_id: "qq", account_id: "account-1", conversation_selection_id: "selection-1",
    range_start: "2026-09-01T00:00:00Z", range_end: "2026-09-01T00:00:01Z", output_root_id: "fixture-root",
  }, "Asia/Shanghai")).rejects.toBeInstanceOf(ApiError);
  responseKind = "secret";
  await expect(client.secretConsent("qq", "account-1")).rejects.toBeInstanceOf(ApiError);
});

it.each([null, "viewpoints", "ideas", "how_to", "mixed"] as const)("生成客户端严格解析 %s 结果并保留原证据", async direction => {
  const requests: Request[] = [];
  const complete = {
    job_id: "generation-job:p2", state: "complete", scope_fingerprint: "a".repeat(64), completed_chunks: 1,
    total_chunks: 1, cancellation_requested: false, cache_hit: false, evidence_status: "verified", error_code: null,
    revision: 5, usage: { request_count: 1, completed_requests: 1, failed_requests: 0, cancelled_requests: 0,
      unknown_usage_requests: 0, known_prompt_tokens: 42, known_completion_tokens: 7, models: ["deepseek-v4-flash"],
      price_status: "unavailable", price_source: null, price_version: null, price_observed_at: null, currency: null,
      estimated_cost: null, actual_cost: null, may_have_incurred_cost: true },
    result: { evidence_status: "verified", batch_id: "batch:p1", snapshot_id: "snapshot:p1",
      scope_fingerprint: "a".repeat(64), source_extraction_ids: [`extraction:v1:${"b".repeat(64)}`],
      result: { status: "complete", title: "合成指南", preparations: [], steps: [{ instruction: "操作", location: null,
        parameters: [], conditions: [], warnings: [], expected_result: null, source_message_ids: [`message:v1:${"c".repeat(64)}`] }],
        checks: [], missing_information: [], conflicts: [] },
      evidence: [{ message_id: `message:v1:${"c".repeat(64)}`, conversation_id: "internal-conversation-a",
        timestamp: "2026-09-01T00:00:01Z", sender_id: null, sender_name: "合成发送者", quote: "合成原文" }] },
  };
  if (direction && direction !== "how_to") {
    Object.assign(complete.result, { result: { ...complete.result.result, steps: [], summary: {
      summary_type: direction, sections: [{ heading: "具体主题", text: "合并后的内容",
        source_message_ids: [`message:v1:${"c".repeat(64)}`] }],
    } } });
    Object.assign(complete.result.evidence[0], { timestamp: null });
  }
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = asRequest(input, init); requests.push(request.clone());
    const path = new URL(request.url).pathname;
    if (path === "/api/session") return json({ csrf_token: "csrf-1", expires_at: "2099-01-01T00:00:00Z" });
    if (path === "/api/generation/previews/from-selections") return json({
      preview_id: "preview:p2", scope_fingerprint: "a".repeat(64), provider: "deepseek", model: direction === null ? "deepseek-v4-flash" : "deepseek-v4-pro",
      thinking: direction === null ? "disabled" : "enabled", reasoning_effort: direction === null ? null : "max",
      mode: "complete", message_count: 1, character_count: 4, estimated_input_tokens: 20, max_output_tokens: 2000,
      provider_calls: 1, price_status: "unknown", estimated_cost: null, cache_hit: false, will_send_external: true,
      outbound_payloads: ['{"messages":[{"message_id":"opaque-a","text":"合成"}],"schema_version":1,"topic":"操作主题"}'],
      expires_at: "2099-01-01T00:00:00Z",
    }, 201);
    if (path === "/api/generation/consents") return json({ consent_id: "generation-consent:p2",
      scope_fingerprint: "a".repeat(64), outbound_authorized: true, expires_at: "2099-01-01T00:00:00Z" }, 201);
    if (path === "/api/generation/jobs" && request.method === "POST") return json(complete, 202);
    if (path === "/api/generation/jobs/generation-job%3Ap2") return json(complete);
    throw new Error(`unexpected_route_${path}`);
  }));
  const client = createClient();
  const excludedMessageId = `message:v1:${"d".repeat(64)}`;
  const previewValue = await client.previewGeneration({ source_id: "qq", account_id: "account-a",
    conversation_selection_ids: ["opaque-a"], snapshot_id: "snapshot:p1", start_time: "2026-09-01T00:00:00Z",
    end_time: "2026-09-02T00:00:00Z", topic: "操作主题", mode: "complete",
    excluded_message_ids: [excludedMessageId], ...(direction ? { summary_type: direction } : {}) });
  expect(previewValue.thinking).toBe(direction === null ? "disabled" : "enabled");
  expect(previewValue.reasoning_effort).toBe(direction === null ? null : "max");
  await expect(client.previewGeneration({ source_id: "qq", account_id: "account-a",
    conversation_selection_ids: ["opaque-a"], snapshot_id: "snapshot:p1", start_time: "2026-09-01T00:00:00Z",
    end_time: "2026-09-02T00:00:00Z", topic: "操作主题", mode: "complete",
    excluded_message_ids: [excludedMessageId, excludedMessageId] })).rejects.toBeInstanceOf(ApiError);
  await expect(client.previewGeneration({ source_id: "qq", account_id: "account-a",
    conversation_selection_ids: ["opaque-a"], snapshot_id: "snapshot:p1", start_time: "2026-09-01T00:00:00Z",
    end_time: "2026-09-02T00:00:00Z", topic: "操作主题", mode: "complete",
    excluded_message_ids: [".."] })).rejects.toBeInstanceOf(ApiError);
  const consentValue = await client.authorizeGeneration(previewValue.preview_id);
  await client.startGeneration(consentValue.consent_id, "generation-idempotency");
  const result = await client.generationJob("generation-job:p2");

  expect(result.result?.evidence[0].quote).toBe("合成原文");
  if (direction && direction !== "how_to") {
    expect(result.result?.result.summary).toEqual(expect.objectContaining({ summary_type: direction }));
    expect(result.result?.evidence[0].timestamp).toBeNull();
  }
  const bodies = await Promise.all(requests.filter(item => item.method === "POST").map(item => item.json()));
  expect(bodies[0].conversation_selection_ids).toEqual(["opaque-a"]);
  expect(bodies[0].excluded_message_ids).toEqual([excludedMessageId]);
  expect(JSON.stringify(bodies[0])).not.toContain("internal-conversation-a");
  expect(requests.filter(item => item.method === "POST").every(item => item.headers.get("X-CSRF-Token") === "csrf-1")).toBe(true);
});

it("手动导入客户端严格解析预览，只用预览指纹确认提交", async () => {
  const requests: Request[] = [];
  const preview = {
    preview_id: "import-preview:a", preview_fingerprint: "a".repeat(64), expires_at_utc: "2099-01-01T00:00:00Z",
    source: { format: "txt", encoding: "utf-8", container: "plain", member_name: null, row_count: 1,
      columns: ["sent_at", "sender_name", "text"], sha256: "b".repeat(64), byte_size: 30,
      warnings: ["manual_backup_is_secondary"] },
    applied_mapping: { sent_at: "sent_at", sender_name: "sender_name", text: "text" }, ready_to_commit: true,
    analysis: { total_count: 1, parsed_count: 1, unresolved_count: 0, duplicate_count: 0, sender_count: 1,
      time_start: "2026-09-13T02:20:00Z", time_end: "2026-09-13T02:20:00Z",
      message_types: { text: 1, image: 0, file: 0, other: 0, unknown: 0 },
      conversations: [{ conversation_id: "internal", conversation_name: "项目群", message_count: 1 }],
      warnings: ["manual_backup_is_secondary"], issues: [], samples: [{ row_number: 1, conversation_name: "项目群",
        sender_name: "张三", timestamp: "2026-09-13T02:20:00Z", message_type: "text", text: "今天开会" }] },
  };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = asRequest(input, init); requests.push(request.clone());
    const path = new URL(request.url).pathname;
    if (path === "/api/session") return json({ csrf_token: "csrf-1", expires_at: "2099-01-01T00:00:00Z" });
    if (path === "/api/imports/previews") return json(preview, 201);
    if (path === "/api/imports/previews/import-preview%3Aa/commit") return json({
      import_id: "import:a", batch_id: "batch:manual:a", snapshot_id: "snapshot:manual:a", source_id: "manual",
      account_id: "import:account", range_start: "2026-09-13T02:20:00Z", range_end: "2026-09-13T02:20:00.001Z",
      display_timezone: "Asia/Shanghai",
      conversations: [{ conversation_selection_id: "selection:manual:a", display_name: "项目群", conversation_type: "group" }],
      warnings: ["manual_backup_is_secondary"],
    }, 201);
    if (path === "/api/imports/recovery") return json({
      imports: [{
        import_id: "import:a", batch_id: "batch:manual:a", snapshot_id: "snapshot:manual:a", source_id: "manual",
        account_id: "import:account", range_start: "2026-09-13T02:20:00Z", range_end: "2026-09-13T02:20:00.001Z",
        display_timezone: "Asia/Shanghai",
        conversations: [{ conversation_selection_id: "selection:manual:fresh", display_name: "项目群", conversation_type: "group" }],
        warnings: ["manual_backup_is_secondary"],
      }], next_cursor: null,
    });
    throw new Error(`unexpected_route_${path}`);
  }));
  const client = createClient();

  const value = await client.previewImport({ origin: "paste", platform_hint: "generic", pasted_text: "[10:20] 张三：今天开会",
    options: { timezone: "Asia/Shanghai", default_date: "2026-09-13", default_conversation_name: "项目群" } });
  const committed = await client.commitImport(value.preview_id, value.preview_fingerprint);
  const recovered = await client.recoverImports();

  expect(value.analysis.samples[0].text).toBe("今天开会");
  expect(committed.source_id).toBe("manual");
  expect(recovered.imports[0].conversations[0].conversation_selection_id).toBe("selection:manual:fresh");
  const bodies = await Promise.all(requests.filter(item => item.method === "POST").map(item => item.json()));
  expect(bodies[1]).toEqual({ preview_fingerprint: "a".repeat(64), confirmed: true });
  expect(bodies[2]).toEqual({ cursor: null, limit: 20 });
  expect(requests.filter(item => item.method === "POST").every(item => item.headers.get("X-CSRF-Token") === "csrf-1")).toBe(true);
});

it.each([false, true])("文档恢复客户端列出最新版本，包括证据不足后的自由编辑（%s）", async (freeEdit) => {
  const messageId = `message:v1:${"c".repeat(64)}`;
  const content = { status: "complete", title: "指南 version 2", preparations: [], steps: [{
    instruction: "step", location: null, parameters: [], conditions: [], warnings: [], expected_result: null,
    source_message_ids: [messageId],
  }], checks: [], missing_information: [], conflicts: [] };
  const version = { document_id: "document:p3", version_id: "document-version:2", ordinal: 2,
    origin: "user_edited", evidence_status: "unverified", parent_version_id: "document-version:1",
    restored_from_version_id: null, source_generation_job_id: "generation-job:p2", change_summary: "P0",
    created_at: "2026-09-05T00:00:01Z", content: freeEdit ? { ...content, steps: [],
      status: "insufficient_evidence", user_text: "指南 version 2\n\n用户自己的补充" } : content };
  const document = { document_id: "document:p3", source_generation_job_id: "generation-job:p2",
    source_snapshot_id: "snapshot:p1", created_at: "2026-09-05T00:00:00Z", updated_at: "2026-09-05T00:00:01Z",
    current_version: version, versions: [version], source_evidence: [{ message_id: messageId,
      conversation_id: "conversation-a", timestamp: "2026-09-01T00:00:01Z", sender_id: null,
      sender_name: "测试", quote: "evidence" }] };
  const requests: Request[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = asRequest(input, init); requests.push(request.clone());
    const path = new URL(request.url).pathname;
    if (path === "/api/session") return json({ csrf_token: "csrf-1", expires_at: "2099-01-01T00:00:00Z" });
    if (path === "/api/documents/recovery") return json({ documents: [document], next_cursor: null });
    throw new Error(`unexpected_route_${path}`);
  }));

  const recovered = await createClient().recoverDocuments();

  expect(recovered.documents[0].current_version.ordinal).toBe(2);
  expect(recovered.documents[0].current_version.content.title).toBe("指南 version 2");
  if (freeEdit) expect(recovered.documents[0].current_version.content.user_text).toBe("指南 version 2\n\n用户自己的补充");
  expect(await requests[1].json()).toEqual({ cursor: null, limit: 20 });
  expect(requests[1].headers.get("X-CSRF-Token")).toBe("csrf-1");
});

it("断开来源与删除数据都要明确确认，并严格解析影响清单", async () => {
  const requests: Request[] = [];
  const countKeys = ["documents", "versions", "document_artifacts", "generation_jobs", "generation_requests", "generation_consents",
    "generation_previews", "cache_entries", "export_batches", "export_jobs", "export_consents", "snapshots", "messages", "managed_files",
    "content_batches", "content_units", "content_images"];
  const counts = Object.fromEntries(countKeys.map(key => [key, key === "documents" ? 1 : 0]));
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = asRequest(input, init); requests.push(request.clone());
    const path = new URL(request.url).pathname;
    if (path === "/api/session") return json({ csrf_token: "csrf-1", expires_at: "2099-01-01T00:00:00Z" });
    if (path === "/api/sources/qq/disconnect") return new Response(null, { status: 204 });
    if (path === "/api/deletion-plans") return json({ plan_id: "deletion-plan:p3", target_kind: "document",
      target_id: "document:p3", include_managed_files: true, fingerprint: "d".repeat(64), counts,
      effects: ["数据库：删除文档"], expires_at: "2099-01-01T00:00:00Z" }, 201);
    if (path === "/api/deletion-plans/deletion-plan%3Ap3/confirm") return json({ plan_id: "deletion-plan:p3",
      state: "complete", deleted_counts: counts, managed_files_retained: false });
    throw new Error(`unexpected_route_${path}`);
  }));
  const client = createClient();

  await client.disconnectSource("qq");
  const plan = await client.prepareDeletion("document", "document:p3", true);
  await client.confirmDeletion(plan.plan_id, plan.fingerprint);

  const bodies = await Promise.all(requests.filter(item => item.method === "POST").map(item => item.json()));
  expect(bodies).toEqual([
    { confirmed: true },
    { target_kind: "document", target_id: "document:p3", include_managed_files: true },
    { fingerprint: "d".repeat(64), confirmed: true },
  ]);
  expect(requests.filter(item => item.method === "POST").every(item => item.headers.get("X-CSRF-Token") === "csrf-1")).toBe(true);
});

it("原文保留状态只接受公开非负计数", async () => {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = new URL(asRequest(input, init).url).pathname;
    if (path === "/api/session") return json({ csrf_token: "csrf-1", expires_at: "2099-01-01T00:00:00Z" });
    return json({ cleaned_batches: 2, pending_cleanups: 1 });
  }));
  expect(await createClient().retentionStatus()).toEqual({ cleaned_batches: 2, pending_cleanups: 1 });
});

it.each([true, false])("主题扩展每次预览完整脱敏主题并独立授权（接受：%s）", async accepted => {
  const calls: Request[] = [];
  const confirm = vi.fn().mockReturnValue(accepted);
  vi.stubGlobal("confirm", confirm);
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = asRequest(input, init); calls.push(request.clone());
    const path = new URL(request.url).pathname;
    if (path === "/api/session") return json({ csrf_token: "csrf-1", expires_at: "2099-01-01T00:00:00Z" });
    if (path.endsWith("/previews")) return json({ preview_id: "topic-preview:1", topic: "合成主题", outbound_text: "完整脱敏主题",
      preview_fingerprint: "a".repeat(64), model: "synthetic-model", expires_at_utc: "2099-01-01T00:00:00Z" });
    return json({ topic: "合成主题", terms: ["合成主题", "关键词"], degraded: false });
  }));
  const result = await createClient().expandTopic("合成主题");
  expect(confirm).toHaveBeenCalledOnce();
  expect(confirm.mock.calls[0][0]).toContain("完整脱敏主题");
  expect(confirm.mock.calls[0][0]).toContain("synthetic-model");
  const executions = calls.filter(request => new URL(request.url).pathname.endsWith("/topic-expansion"));
  expect(executions).toHaveLength(accepted ? 1 : 0);
  if (accepted) {
    expect(await executions[0].json()).toEqual({ preview_id: "topic-preview:1", preview_fingerprint: "a".repeat(64), confirmed: true });
    expect(result.degraded).toBe(false);
  } else expect(result).toEqual({ topic: "合成主题", terms: ["合成主题"], degraded: true });
});
