/** Entirely synthetic HTTP fixtures matching the committed Task 4 contract. */
export const statusLabels = {
  unconfigured: "未配置", needs_user_action: "等待用户操作", documented_not_tested: "有文档，未实测",
  ready: "真实导出已验证", blocked: "已阻止", unsupported: "不支持", error: "运行错误",
} as const;
export const sectionLabels = {
  source_version: "工具、版本与许可", access_impact: "访问方式与客户端影响", scope_overread: "账号、目录与超范围读取",
  storage_cleanup: "秘密、缓存与清理", permissions_network: "权限与联网", action_stop: "本次操作与安全停止",
} as const;
export type SourceId = "qq" | "wechat";
export const source = (source_id: SourceId, status: keyof typeof statusLabels = "needs_user_action") => ({
  source_id, status, account_id: "synthetic-account", base_url: source_id === "qq" ? "http://127.0.0.1:40653" : "http://127.0.0.1:6131",
  client_version: "fixture-client-1", tool_version: "fixture-tool-1", account_version: "fixture-account-1",
  secret_configured: false, error_code: null,
});
export const sections = {
  source_version: "合成工具 fixture-tool-1；仅合成测试；许可待核实，不能作为真实接入依据。",
  access_impact: "只使用注入的合成适配器；不会修改、重签名或关闭客户端。",
  scope_overread: "合成账号 synthetic-account；只写批准的合成目录；真实工具可能读取或缓存更大范围。",
  storage_cleanup: "令牌只交本机后端；私有文件与缓存由用户单独清理；断开不等于清理上游缓存。",
  permissions_network: "不申请系统权限；遥测、媒体下载、AI、监控、漫游关闭；真实工具联网事实未知。",
  action_stop: "仅验证本次窄范围合成导出；重开文件核对哈希与范围；拒绝或失败即安全停止，不选备用源。",
};
export const setupOptions = {
  approved_output_roots: [{ output_root_id: "fixture-root", label: "已批准的合成目录" }, { output_root_id: "fixture-root-2", label: "第二合成目录" }],
  source_disclosures: {
    qq: { source_id: "qq" as const, tool: "QCE", revision: "fixture-contract-1", sections: { ...sections },
      blockers: [] as string[], risks: ["qce_local_history_may_be_incomplete"], hash: "1".repeat(64) },
    wechat: { source_id: "wechat" as const, tool: "TraceMemo", revision: "fixture-contract-1", sections: { ...sections },
      blockers: [] as string[], risks: ["tracememo_initialization_may_read_broader_database", "tracememo_system_protection_change_is_blocked"], hash: "2".repeat(64) },
  },
};
export const selection = {
  source_id: "qq", account_id: "synthetic-account", conversation_selection_id: "opaque-selection-B",
  range_start: "2026-09-01T00:00:00+00:00", range_end: "2026-09-01T00:00:02+00:00", output_root_id: "fixture-root",
};
export const report = {
  job_id: "synthetic-job", source_id: "qq", client_version: "fixture-client-1", tool_version: "fixture-tool-1", account_version: "fixture-account-1",
  range_start: selection.range_start, range_end: selection.range_end, timezone: "Asia/Shanghai", count: 2,
  sha256: "a".repeat(64), hash_ok: true, range_ok: true, completeness: "source_range_verified" as const,
  state: "complete" as const, evidence_class: "synthetic" as const, error_code: null,
};

export function installApi() {
  const requests: { method: string; path: string; url: string; body: Record<string, any> | null; headers: Headers }[] = [];
  const secretConsents: { source_id: string; operation: string; account_id: string; challenge: string; disclosure_revision: string; disclosure_hash: string; consumed: boolean }[] = [];
  const state = {
    sources: [source("qq"), source("wechat")] as Array<Record<string, any>>, setupOptions: structuredClone(setupOptions),
    environment: {
      os: { system: "Darwin", release: "25.0.0", version: "macOS fixture", architecture: "arm64", physical_architecture: "arm64", process_architecture: "arm64", rosetta_translated: false },
      applications: Object.fromEntries(["qq", "wechat", "qce", "tracememo"].map(name => [name, { version: "unknown", architecture: "unknown", installed: false }])),
      tools: { python3: { version: "3.13-fixture", architecture: "arm64", installed: true } }, issues: [] as Array<{ code: string; message: string }>,
    },
    sessionCount: 0, jobStatus: 202, secretStatus: 204, report: { ...report } as Record<string, any>, challenge: "opaque-consent-1",
    agentConversations: [] as Array<Record<string, any>>,
    jobReads: 0, reportQueue: [] as Array<Record<string, any>>, delayJob: null as Promise<Response> | null,
    delaySources: null as Promise<Response> | null,
    delayEnvironment: null as Promise<Response> | null,
    delaySecret: null as Promise<Response> | null,
    delayGenerationPreview: null as Promise<Response> | null,
  };
  const fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = input instanceof Request ? input : new Request(new URL(String(input), location.href), init);
    if (new URL(request.url).origin !== location.origin) throw new Error("external_request_forbidden");
    const path = new URL(request.url).pathname;
    const body = request.method === "GET" ? null : await request.json();
    requests.push({ method: request.method, path, url: request.url, body, headers: request.headers });
    const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
    if (path === "/api/session") {
      state.sessionCount++;
      return json({ csrf_token: `synthetic-csrf-${state.sessionCount}`, expires_at: "2099-01-01T00:00:00Z" });
    }
    if (!state.sessionCount) return json({ code: "request_rejected" }, 401);
    if (path === "/api/agent/conversations") {
      if (request.method === "GET") return json({ conversations: state.agentConversations.map(({ conversation_id, title, revision, created_at, updated_at }) => ({ conversation_id, title, revision, created_at, updated_at })) });
      const value = { conversation_id: "conversation:fixture", title: "新会话", revision: 1,
        created_at: "2026-10-23T00:00:00Z", updated_at: "2026-10-23T00:00:00Z", sources: [], messages: [], active_run: null };
      state.agentConversations.push(value); return json(value);
    }
    if (path.startsWith("/api/agent/conversations/") && request.method === "GET") return json(state.agentConversations[0]);
    if (path === "/api/setup-options") return json(state.setupOptions);
    if (path === "/api/environment") {
      if (state.delayEnvironment) { const pending = state.delayEnvironment; state.delayEnvironment = null; return await pending; }
      return json(state.environment);
    }
    if (path === "/api/sources" && request.method === "GET") {
      if (state.delaySources) { const pending = state.delaySources; state.delaySources = null; return await pending; }
      return json({ sources: state.sources });
    }
    if (path === "/api/sources" && request.method === "POST") {
      if (!body || !["qq", "wechat"].includes(body.source_id)) return json({ code: "invalid_source" }, 422);
      const current = state.sources.find(item => item.source_id === body.source_id);
      const configured = { ...current, ...body, status: "needs_user_action", secret_configured: current?.secret_configured === true, error_code: null };
      state.sources = state.sources.filter(item => item.source_id !== body.source_id).concat(configured);
      return json({ source_id: body.source_id, status: "needs_user_action" }, 201);
    }
    const disconnectRoute = path.match(/^\/api\/sources\/(qq|wechat)\/disconnect$/);
    if (disconnectRoute) {
      const source_id = disconnectRoute[1];
      const current = state.sources.find(item => item.source_id === source_id);
      if (!body || Object.keys(body).join(",") !== "confirmed" || body.confirmed !== true || current?.secret_configured)
        return json({ code: "request_rejected" }, current?.secret_configured ? 409 : 403);
      state.sources = state.sources.map(item => item.source_id === source_id ?
        { source_id, status: "unconfigured", secret_configured: false } : item);
      return new Response(null, { status: 204 });
    }
    if (path.endsWith("/conversations")) return json({ conversations: [
      { conversation_selection_id: "opaque-selection-A", display_name: "同名合成群", conversation_type: "group", account_id: "synthetic-account" },
      { conversation_selection_id: "opaque-selection-B", display_name: "同名合成群", conversation_type: "group", account_id: "synthetic-account" },
    ] });
    if (path.endsWith("/verification-consents")) {
      const source_id = path.includes("/wechat/") ? "wechat" : "qq";
      return json({ consent_challenge: state.challenge, scope_fingerprint: "public-scope-digest", disclosure: setupOptions.source_disclosures[source_id], expires_at: "2099-01-01T00:00:00Z" });
    }
    if (path.endsWith("/verification-jobs") && request.method === "POST") {
      const source_id = path.includes("/wechat/") ? "wechat" : "qq";
      const disclosure = setupOptions.source_disclosures[source_id];
      if (!body || body.disclosure_revision !== disclosure.revision || body.disclosure_hash !== disclosure.hash) return json({ code: "consent_invalidated" }, 403);
      return state.jobStatus === 202 ? json({ job_id: "synthetic-job", state: "pending_authorization" }, 202) : json({ code: "consent_invalidated", message: "配置已变化，请重新确认" }, state.jobStatus);
    }
    if (path.endsWith("/verification-jobs/synthetic-job/cancel")) {
      state.report = { ...state.report, state: "cancelled", count: null, sha256: null, hash_ok: null, range_ok: null, completeness: null, error_code: "cancelled" };
      return json({ job_id: "synthetic-job", state: "cancelled" });
    }
    if (path.endsWith("/verification-jobs/synthetic-job")) {
      state.jobReads++;
      if (state.delayJob) { const pending = state.delayJob; state.delayJob = null; return await pending; }
      return json(state.reportQueue.shift() ?? state.report);
    }
    if (path === "/api/search/from-selections") return json({ results: [{
      message_id: `message:v1:${"c".repeat(64)}`, conversation_id: "conversation-a",
      timestamp: "2026-09-01T00:00:01Z", sender_id: "sender-a", sender_name: "合成发送者",
      text: "生成锁测试消息", batch_id: "batch:p1", snapshot_id: "snapshot:p1",
    }], next_cursor: null });
    if (path === "/api/imports/recovery") return json({ imports: [], next_cursor: null });
    if (path === "/api/documents/recovery") return json({ documents: [], next_cursor: null });
    if (path === "/api/generation/previews/from-selections") {
      if (state.delayGenerationPreview) {
        const pending = state.delayGenerationPreview;
        state.delayGenerationPreview = null;
        return await pending;
      }
      return json({
        preview_id: "preview:p2", scope_fingerprint: "a".repeat(64), provider: "fake", model: "deterministic",
        mode: "complete", message_count: 1, character_count: 6, estimated_input_tokens: 20,
        max_output_tokens: 2000, provider_calls: 1, price_status: "unknown", estimated_cost: null,
        cache_hit: false, will_send_external: true, outbound_payloads: ["{}"], expires_at: "2099-01-01T00:00:00Z",
      }, 201);
    }
    const secretRoute = path.match(/^\/api\/sources\/(qq|wechat)\/(secret-consents|secret-operations)$/);
    if (secretRoute) {
      const deny = () => json({ code: "secret_consent_invalid", message: "秘密操作需要重新授权" }, 403);
      if (request.method !== "POST") return json({ code: "method_not_allowed" }, 405);
      const exactKeys = (keys: string[]) => body !== null && Object.keys(body).sort().join(",") === keys.sort().join(",");
      const source_id = secretRoute[1];
      if (!body || !["secret_set", "secret_get", "secret_delete"].includes(body.operation) ||
          !state.sources.some(item => item.source_id === source_id && item.account_id === body.account_id)) return deny();
      if (secretRoute[2] === "secret-consents") {
        if (!exactKeys(["operation", "account_id"])) return deny();
        const challenge = "opaque-secret-" + crypto.randomUUID();
        const disclosure = {
          source_id, tool: source_id === "qq" ? "QCE" : "TraceMemo", operation: body.operation, secret_backend: "fixture-memory", revision: "fixture-secret-contract-1",
          sections: { storage: "令牌只保存在本机后端秘密存储。", scope: "仅为当前来源和账号保存本机上游访问令牌。", cleanup: "断开来源不代表已清理上游缓存。" },
          risks: ["source_api_token_stays_server_side", ...(source_id === "wechat" ? ["wechat_database_key_is_not_stored_by_platform"] : [])],
          hash: (source_id === "qq" ? "3" : "4").repeat(64),
        };
        secretConsents.push({ source_id, operation: body.operation, account_id: body.account_id, challenge,
          disclosure_revision: disclosure.revision, disclosure_hash: disclosure.hash, consumed: false });
        return json({ consent_challenge: challenge, disclosure, expires_at: "2099-01-01T00:00:00Z" });
      }
      const keys = ["operation", "account_id", "consent_challenge", "confirmed", "disclosure_revision", "disclosure_hash"];
      if (body.operation === "secret_set") keys.push("value");
      const grant = secretConsents.find(item => item.challenge === body.consent_challenge);
      if (!exactKeys(keys) || body.confirmed !== true || !grant || grant.consumed ||
          grant.source_id !== source_id || grant.account_id !== body.account_id || grant.operation !== body.operation ||
          grant.disclosure_revision !== body.disclosure_revision || grant.disclosure_hash !== body.disclosure_hash ||
          (body.operation === "secret_set" && (typeof body.value !== "string" || !body.value))) return deny();
      // Consume before waiting for the operation response, including failed operations.
      grant.consumed = true;
      const response = state.delaySecret ? await state.delaySecret : (state.secretStatus === 204 ? new Response(null, { status: 204 }) : json({ code: "secret_failed", message: "秘密操作未完成" }, state.secretStatus));
      if (response.ok) state.sources = state.sources.map(item => item.source_id === source_id ?
        { ...item, secret_configured: body.operation !== "secret_delete" } : item);
      return response;
    }
    throw new Error("unexpected_api_route");
  };
  return { fetch, requests, state, secretConsents };
}
