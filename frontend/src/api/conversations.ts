import type { AgentApproval, AgentConversation, AgentConversationSummary, AgentMessage, AgentRun, AgentSource } from "../conversation-types";
export type ConversationRequest = <T>(path: string, parse: (value: unknown) => T, body?: unknown, retryGet?: boolean, csrfForGet?: boolean) => Promise<T>;
type Obj = Record<string, unknown>;
function invalid(): never { throw new Error("invalid_agent_response"); }
function object(value: unknown): Obj { return value && typeof value === "object" && !Array.isArray(value) ? value as Obj : invalid(); }
function fields(value: unknown, keys: string[]): Obj { const result = object(value); return Object.keys(result).length === keys.length && keys.every(key => key in result) ? result : invalid(); }
function string(value: unknown, empty = false): string { return typeof value === "string" && (empty || !!value.trim()) ? value : invalid(); }
function integer(value: unknown, min = 0): number { return typeof value === "number" && Number.isSafeInteger(value) && value >= min ? value : invalid(); }
function date(value: unknown): string { const s = string(value); return Number.isFinite(Date.parse(s)) ? s : invalid(); }
function nullable<T>(value: unknown, parse: (v: unknown) => T): T | null { return value === null ? null : parse(value); }
function array<T>(value: unknown, parse: (v: unknown) => T): T[] { return Array.isArray(value) ? value.map(item => parse(item)) : invalid(); }
function choice<T extends string>(value: unknown, values: readonly T[]): T { return values.includes(value as T) ? value as T : invalid(); }
function hash(value: unknown): string { const s = string(value); return /^[a-f0-9]{64}$/.test(s) ? s : invalid(); }
const summaryKeys = ["conversation_id", "title", "revision", "created_at", "updated_at"];
function summary(v: Obj): AgentConversationSummary { return { conversation_id: string(v.conversation_id), title: string(v.title), revision: integer(v.revision), created_at: date(v.created_at), updated_at: date(v.updated_at) }; }
function source(value: unknown): AgentSource { const v = fields(value, ["kind", "id", "label"]); return { kind: choice(v.kind, ["chat_snapshot", "content_batch"] as const), id: string(v.id), label: string(v.label) }; }
function message(value: unknown): AgentMessage {
  const v = fields(value, ["message_id", "role", "text", "created_at", "run_id", "document_id", "version_id", "artifact_revision"]);
  const m = { message_id: string(v.message_id), role: choice(v.role, ["user", "assistant"] as const), text: string(v.text, true), created_at: date(v.created_at), run_id: nullable(v.run_id, string), document_id: nullable(v.document_id, string), version_id: nullable(v.version_id, string), artifact_revision: nullable(v.artifact_revision, integer) };
  if (!!m.document_id !== !!m.version_id) return invalid(); return m;
}
function approval(value: unknown): AgentApproval {
  const v = fields(value, ["approval_id", "kind", "preview_fingerprint", "provider", "model", "outbound_payloads", "provider_calls", "max_output_tokens", "expires_at"]);
  const payloads = array(v.outbound_payloads, string);
  const kind = choice(v.kind, ["agent", "generation"] as const);
  const provider_calls = integer(v.provider_calls, kind === "generation" ? 0 : 1);
  if ((provider_calls === 0) !== (payloads.length === 0)) return invalid();
  return { approval_id: string(v.approval_id), kind, preview_fingerprint: hash(v.preview_fingerprint), provider: string(v.provider), model: string(v.model), outbound_payloads: payloads, provider_calls, max_output_tokens: integer(v.max_output_tokens, 1), expires_at: date(v.expires_at) };
}
function run(value: unknown): AgentRun {
  const v = fields(value, ["run_id", "revision", "state", "stage", "error_code", "approval", "document_id", "version_id", "artifact_revision"]);
  const result: AgentRun = { run_id: string(v.run_id), revision: integer(v.revision), state: choice(v.state, ["waiting_approval", "running", "complete", "cancelled", "failed", "needs_input"] as const), stage: string(v.stage), error_code: nullable(v.error_code, string), approval: nullable(v.approval, approval), document_id: nullable(v.document_id, string), version_id: nullable(v.version_id, string), artifact_revision: nullable(v.artifact_revision, integer) };
  if ((result.state === "waiting_approval") !== !!result.approval || !!result.document_id !== !!result.version_id) return invalid(); return result;
}
export function parseAgentConversation(value: unknown): AgentConversation { const v = fields(value, [...summaryKeys, "sources", "messages", "active_run"]); return { ...summary(v), sources: array(v.sources, source), messages: array(v.messages, message), active_run: nullable(v.active_run, run) }; }
const id = (value: string) => /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(value) ? encodeURIComponent(value) : invalid();
export function createConversationClient(request: ConversationRequest) {
  const base = "/api/agent/conversations";
  const route = (conversation: string) => `${base}/${id(conversation)}`;
  const read = async (conversation: string, suffix = "", body?: unknown) => { const result = await request(`${route(conversation)}${suffix}`, parseAgentConversation, body, true, true); return result.conversation_id === conversation ? result : invalid(); };
  return {
    listAgentConversations: () => request(base, value => ({ conversations: array(fields(value, ["conversations"]).conversations, v => summary(fields(v, summaryKeys))) }), undefined, true, true),
    createAgentConversation: () => request(base, parseAgentConversation, {}),
    agentConversation: (conversation: string) => read(conversation),
    setAgentConversationSources: (conversation: string, sources: AgentSource[]) => read(conversation, "/sources", { sources: sources.map(source) }),
    sendAgentMessage: async (conversation: string, text: string, client_message_id: string) => { if (!text.trim() || text.length > 512) return invalid(); id(client_message_id); return read(conversation, "/messages", { text, client_message_id }); },
    approveAgentRun: async (conversation: string, run_id: string, approval_id: string, preview_fingerprint: string) => { id(approval_id); hash(preview_fingerprint); return read(conversation, `/runs/${id(run_id)}/approve`, { approval_id, preview_fingerprint, confirmed: true }); },
    cancelAgentRun: (conversation: string, run_id: string) => read(conversation, `/runs/${id(run_id)}/cancel`, {}),
  };
}
