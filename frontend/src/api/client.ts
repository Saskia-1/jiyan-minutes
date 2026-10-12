import {
  completenessStates, conversationTypes, evidenceClasses, secretOperations, secretSectionLabels, sourceBaseUrls, sourceIds, sourceTools, statusLabels, validTimezone, verificationStates,
  type Conversation, type Disclosure, type EnvironmentReport, type Report, type SecretDisclosure, type Selection, type SetupOptions,
  type SecretOperation, type Source, type SourceConfiguration, type SourceId,
  type ChatSearchPage, type ChatSearchRequest, type TopicExpansion, type GenerationContext, type LegacyChatSummary, type ExportConsent, type ExportJob, type ExportSelection,
  generationStates, type GenerationConsent, type GenerationJob, type GenerationPreview, type GenerationSelectionRequest,
  deletionCountKeys, type DeletionCountKey, type DeletionPlan, type DeletionResult,
  type DocumentArtifact, type DocumentRecoveryPage, type DocumentVersion, type HowToDocument, type HowToResult, type ValidatedEvidence, type ValidatedGeneration,
  type ImportCommit, type ImportPreview, type ImportPreviewRequest, type ImportRecoveryPage,
  type ContentBatch, type ContentBatchPage, type ContentPreview, type ContentPreviewRequest,
  type ContentUnitEdit,
  type MixedSearchPage, type MixedSearchRequest, type MixedGenerationRequest, type SourceRef,
} from "../types";

export class ApiError extends Error {
  constructor(public readonly status: number, public readonly code: string | null = null) { super("local_api_request_failed"); }
}
export interface PreparedConsent { consent_challenge: string; scope_fingerprint: string; disclosure: Disclosure; expires_at: string }
export interface SecretConsent { consent_challenge: string; disclosure: SecretDisclosure; expires_at: string }

type JsonObject = Record<string, unknown>;
type Parser<T> = (value: unknown) => T;
const isObject = (value: unknown): value is JsonObject => typeof value === "object" && value !== null && !Array.isArray(value);
const invalid = (): never => { throw new ApiError(0); };
function publicFields(value: JsonObject, allowed: readonly string[]) {
  if (Object.keys(value).some(key => !allowed.includes(key))) return invalid();
}
function exactPublicFields(value: JsonObject, expected: readonly string[]) {
  publicFields(value, expected);
  if (Object.keys(value).length !== expected.length) return invalid();
}
function requiredString(value: unknown): string {
  return typeof value === "string" && value.trim() ? value : invalid();
}
function nullableString(value: unknown): string | null {
  return value === null || value === undefined ? null : requiredString(value);
}
function member<T extends readonly string[]>(values: T, value: unknown): T[number] {
  return typeof value === "string" && values.includes(value) ? value as T[number] : invalid();
}
function expiresAt(value: unknown): string {
  const result = requiredString(value);
  return Number.isFinite(Date.parse(result)) ? result : invalid();
}
function parseStringList(value: unknown, allowEmpty = false): string[] {
  if (!Array.isArray(value) || (!allowEmpty && !value.length) || !value.every(item => typeof item === "string" && item.trim())) return invalid();
  const result = value.map(item => item as string);
  return new Set(result).size === result.length ? result : invalid();
}
function disclosureHash(value: unknown): string {
  const result = requiredString(value);
  return /^[a-f0-9]{64}$/.test(result) ? result : invalid();
}
function parseDisclosure(value: unknown): Disclosure {
  if (!isObject(value) || !isObject(value.sections) || !Array.isArray(value.blockers)) return invalid();
  exactPublicFields(value, ["source_id", "tool", "revision", "sections", "risks", "blockers", "hash"]);
  const rawSections = value.sections;
  const keys = ["source_version", "access_impact", "scope_overread", "storage_cleanup", "permissions_network", "action_stop"] as const;
  exactPublicFields(rawSections, keys);
  const sections = Object.fromEntries(keys.map(key => [key, requiredString(rawSections[key])])) as Disclosure["sections"];
  const source_id = member(sourceIds, value.source_id);
  const tool = requiredString(value.tool);
  if (tool !== sourceTools[source_id]) return invalid();
  return { source_id, tool, revision: requiredString(value.revision), hash: disclosureHash(value.hash),
    risks: parseStringList(value.risks), sections, blockers: parseStringList(value.blockers, true) };
}
function parseSecretDisclosure(value: unknown): SecretDisclosure {
  if (!isObject(value) || !isObject(value.sections)) return invalid();
  exactPublicFields(value, ["source_id", "tool", "operation", "secret_backend", "revision", "sections", "risks", "hash"]);
  const rawSections = value.sections;
  exactPublicFields(rawSections, Object.keys(secretSectionLabels));
  const sections = Object.fromEntries(Object.keys(secretSectionLabels).map(key => [key, requiredString(rawSections[key])])) as SecretDisclosure["sections"];
  const source_id = member(sourceIds, value.source_id);
  const tool = requiredString(value.tool);
  if (tool !== sourceTools[source_id]) return invalid();
  return { source_id, tool, operation: member(secretOperations, value.operation), secret_backend: requiredString(value.secret_backend),
    revision: requiredString(value.revision), hash: disclosureHash(value.hash), risks: parseStringList(value.risks), sections };
}
function parseSetupOptions(value: unknown): SetupOptions {
  if (!isObject(value) || !Array.isArray(value.approved_output_roots) || !isObject(value.source_disclosures)) return invalid();
  exactPublicFields(value, ["approved_output_roots", "source_disclosures"]);
  exactPublicFields(value.source_disclosures, sourceIds);
  const approved_output_roots = value.approved_output_roots.map(item => {
    if (!isObject(item)) return invalid();
    exactPublicFields(item, ["output_root_id", "label"]);
    const output_root_id = requiredString(item.output_root_id);
    if (!/^[A-Za-z0-9_-]{1,64}$/.test(output_root_id)) return invalid();
    return { output_root_id, label: requiredString(item.label) };
  });
  if (new Set(approved_output_roots.map(item => item.output_root_id)).size !== approved_output_roots.length) return invalid();
  const qq = parseDisclosure(value.source_disclosures.qq);
  const wechat = parseDisclosure(value.source_disclosures.wechat);
  if (qq.source_id !== "qq" || wechat.source_id !== "wechat") return invalid();
  return {
    approved_output_roots,
    source_disclosures: { qq, wechat },
  };
}
function parseVersionArchitecture(value: unknown): { version: string; architecture: string; installed: boolean } {
  if (!isObject(value)) return invalid();
  exactPublicFields(value, ["version", "architecture", "installed"]);
  if (typeof value.installed !== "boolean") return invalid();
  return { version: requiredString(value.version), architecture: requiredString(value.architecture), installed: value.installed };
}
function parseEnvironment(value: unknown): EnvironmentReport {
  if (!isObject(value) || !isObject(value.os) || !isObject(value.applications) || !isObject(value.tools) || !Array.isArray(value.issues)) return invalid();
  exactPublicFields(value, ["os", "applications", "tools", "issues"]);
  const osKeys = ["system", "release", "version", "architecture", "physical_architecture", "process_architecture", "rosetta_translated"] as const;
  exactPublicFields(value.os, osKeys);
  exactPublicFields(value.applications, ["qq", "wechat", "qce", "tracememo"]);
  exactPublicFields(value.tools, ["python3"]);
  if (value.os.rosetta_translated !== null && typeof value.os.rosetta_translated !== "boolean") return invalid();
  const issues = value.issues.map(issue => {
    if (!isObject(issue)) return invalid();
    exactPublicFields(issue, ["code", "message"]);
    return { code: requiredString(issue.code), message: requiredString(issue.message) };
  });
  return {
    os: {
      system: requiredString(value.os.system), release: requiredString(value.os.release), version: requiredString(value.os.version),
      architecture: requiredString(value.os.architecture), physical_architecture: requiredString(value.os.physical_architecture),
      process_architecture: requiredString(value.os.process_architecture), rosetta_translated: value.os.rosetta_translated,
    },
    applications: {
      qq: parseVersionArchitecture(value.applications.qq), wechat: parseVersionArchitecture(value.applications.wechat),
      qce: parseVersionArchitecture(value.applications.qce), tracememo: parseVersionArchitecture(value.applications.tracememo),
    },
    tools: { python3: parseVersionArchitecture(value.tools.python3) }, issues,
  };
}
function parseSource(value: unknown): Source {
  if (!isObject(value) || typeof value.secret_configured !== "boolean") return invalid();
  publicFields(value, ["source_id", "status", "account_id", "base_url", "client_version", "tool_version", "account_version", "secret_configured", "error_code"]);
  const source_id = member(sourceIds, value.source_id);
  const status = member(Object.keys(statusLabels) as Array<keyof typeof statusLabels>, value.status);
  if ((value.base_url !== undefined && value.base_url !== sourceBaseUrls[source_id]) ||
      (status !== "unconfigured" && value.base_url === undefined)) return invalid();
  return {
    source_id,
    status,
    account_id: nullableString(value.account_id),
    client_version: nullableString(value.client_version),
    tool_version: nullableString(value.tool_version),
    account_version: nullableString(value.account_version),
    secret_configured: value.secret_configured,
    error_code: nullableString(value.error_code),
  };
}
function parseSources(value: unknown): Source[] {
  if (!isObject(value) || !Array.isArray(value.sources)) return invalid();
  exactPublicFields(value, ["sources"]);
  const sources = value.sources.map(parseSource);
  const present = new Set(sources.map(item => item.source_id));
  if (sources.length !== sourceIds.length || present.size !== sourceIds.length || !sourceIds.every(id => present.has(id))) return invalid();
  return sources;
}
function parseConversations(value: unknown): Conversation[] {
  if (!isObject(value) || !Array.isArray(value.conversations)) return invalid();
  exactPublicFields(value, ["conversations"]);
  const conversations = value.conversations.map(item => {
    if (!isObject(item)) return invalid();
    exactPublicFields(item, ["conversation_selection_id", "display_name", "conversation_type", "account_id"]);
    return {
      conversation_selection_id: requiredString(item.conversation_selection_id),
      display_name: requiredString(item.display_name),
      account_id: requiredString(item.account_id),
      conversation_type: member(conversationTypes, item.conversation_type),
    };
  });
  if (new Set(conversations.map(item => item.conversation_selection_id)).size !== conversations.length) return invalid();
  return conversations;
}
function parsePreparedConsent(value: unknown): PreparedConsent {
  if (!isObject(value)) return invalid();
  exactPublicFields(value, ["consent_challenge", "scope_fingerprint", "disclosure", "expires_at"]);
  return {
    consent_challenge: requiredString(value.consent_challenge),
    scope_fingerprint: requiredString(value.scope_fingerprint),
    disclosure: parseDisclosure(value.disclosure),
    expires_at: expiresAt(value.expires_at),
  };
}
function parseSecretConsent(value: unknown): SecretConsent {
  if (!isObject(value)) return invalid();
  exactPublicFields(value, ["consent_challenge", "disclosure", "expires_at"]);
  return {
    consent_challenge: requiredString(value.consent_challenge),
    disclosure: parseSecretDisclosure(value.disclosure),
    expires_at: expiresAt(value.expires_at),
  };
}
function parseReport(value: unknown): Report {
  if (!isObject(value)) return invalid();
  publicFields(value, ["job_id", "source_id", "client_version", "tool_version", "account_version", "range_start", "range_end", "timezone",
    "count", "sha256", "hash_ok", "range_ok", "completeness", "state", "evidence_class", "error_code", "schema_version", "system_version"]);
  if (value.schema_version !== undefined && (!Number.isInteger(value.schema_version) || Number(value.schema_version) < 1)) return invalid();
  if (value.system_version !== undefined) requiredString(value.system_version);
  const count = value.count === null ? null : Number.isInteger(value.count) && Number(value.count) >= 0 ? Number(value.count) : invalid();
  const booleanOrNull = (item: unknown) => item === null ? null : typeof item === "boolean" ? item : invalid();
  const sha256 = nullableString(value.sha256);
  if (sha256 !== null && !/^[a-f0-9]{64}$/.test(sha256)) return invalid();
  const range_start = expiresAt(value.range_start); const range_end = expiresAt(value.range_end);
  if (Date.parse(range_end) <= Date.parse(range_start)) return invalid();
  const timezone = requiredString(value.timezone);
  if (!validTimezone(timezone)) return invalid();
  return {
    job_id: requiredString(value.job_id), source_id: member(sourceIds, value.source_id),
    state: member(verificationStates, value.state), evidence_class: member(evidenceClasses, value.evidence_class), count,
    client_version: requiredString(value.client_version), tool_version: requiredString(value.tool_version),
    account_version: requiredString(value.account_version), range_start, range_end, timezone,
    hash_ok: booleanOrNull(value.hash_ok), range_ok: booleanOrNull(value.range_ok), sha256,
    completeness: value.completeness === null || value.completeness === undefined ? null : member(completenessStates, value.completeness),
    error_code: nullableString(value.error_code),
  };
}
function parseCreated(value: unknown): { job_id: string; state: "pending_authorization" } {
  if (!isObject(value) || value.state !== "pending_authorization") return invalid();
  exactPublicFields(value, ["job_id", "state"]);
  return { job_id: requiredString(value.job_id), state: "pending_authorization" };
}
const cancellationStates = ["complete", "partial", "failed", "cancelled"] as const;
function parseCancelled(value: unknown): { job_id: string; state: typeof cancellationStates[number] } {
  if (!isObject(value)) return invalid();
  exactPublicFields(value, ["job_id", "state"]);
  return { job_id: requiredString(value.job_id), state: member(cancellationStates, value.state) };
}
const parseVoid = (value: unknown): void => { if (value !== undefined) return invalid(); };
function nullableInteger(value: unknown): number | null {
  return value === null ? null : Number.isInteger(value) && Number(value) >= 0 ? Number(value) : invalid();
}
function parseExportConsent(value: unknown): ExportConsent {
  if (!isObject(value)) return invalid();
  exactPublicFields(value, ["consent_id", "scope_fingerprint", "expires_at_utc"]);
  const fingerprint = disclosureHash(value.scope_fingerprint);
  return { consent_id: requiredString(value.consent_id), scope_fingerprint: fingerprint, expires_at_utc: expiresAt(value.expires_at_utc) };
}
function parseExportJob(value: unknown): ExportJob {
  if (!isObject(value)) return invalid();
  exactPublicFields(value, ["job_id", "state", "batch_id", "snapshot_id", "scope_fingerprint", "known_total", "completed_count",
    "fetched_count", "saved_count", "duplicate_count", "unknown_time_count", "parse_error_count", "manifest_sha256", "completeness",
    "error_code", "searchable", "cancellation_requested", "progress_percent", "revision"]);
  if (typeof value.searchable !== "boolean" || typeof value.cancellation_requested !== "boolean") return invalid();
  const manifest = value.manifest_sha256 === null ? null : disclosureHash(value.manifest_sha256);
  const completeness = value.completeness === null ? null : member(completenessStates, value.completeness);
  const progress = nullableInteger(value.progress_percent);
  if (progress !== null && progress > 100) return invalid();
  return {
    job_id: requiredString(value.job_id), state: member(verificationStates, value.state),
    batch_id: nullableString(value.batch_id), snapshot_id: nullableString(value.snapshot_id), scope_fingerprint: disclosureHash(value.scope_fingerprint),
    known_total: nullableInteger(value.known_total), completed_count: nullableInteger(value.completed_count), fetched_count: nullableInteger(value.fetched_count),
    saved_count: nullableInteger(value.saved_count), duplicate_count: nullableInteger(value.duplicate_count), unknown_time_count: nullableInteger(value.unknown_time_count),
    parse_error_count: nullableInteger(value.parse_error_count), manifest_sha256: manifest, completeness, error_code: nullableString(value.error_code),
    searchable: value.searchable, cancellation_requested: value.cancellation_requested, progress_percent: progress, revision: nullableInteger(value.revision) ?? invalid(),
  };
}
function parseSearchPage(value: unknown): ChatSearchPage {
  if (!isObject(value) || !Array.isArray(value.results)) return invalid();
  exactPublicFields(value, ["results", "next_cursor"]);
  const results = value.results.map(item => {
    if (!isObject(item)) return invalid();
    exactPublicFields(item, ["message_id", "conversation_id", "timestamp", "sender_id", "sender_name", "text", "batch_id", "snapshot_id", ...(Object.hasOwn(item, "source_order") ? ["source_order"] : [])]);
    const nullableText = (input: unknown) => input === null ? null : typeof input === "string" ? input : invalid();
    return {
      message_id: requiredString(item.message_id), conversation_id: requiredString(item.conversation_id), timestamp: item.timestamp === null ? null : expiresAt(item.timestamp),
      ...(Object.hasOwn(item, "source_order") ? { source_order: nullableInteger(item.source_order) ?? invalid() } : {}),
      sender_id: nullableText(item.sender_id), sender_name: nullableText(item.sender_name), text: nullableText(item.text),
      batch_id: requiredString(item.batch_id), snapshot_id: requiredString(item.snapshot_id),
    };
  });
  if (new Set(results.map(item => item.message_id)).size !== results.length) return invalid();
  return { results, next_cursor: value.next_cursor === null ? null : requiredString(value.next_cursor) };
}
function parseTopicExpansion(value: unknown): TopicExpansion {
  if (!isObject(value) || !Array.isArray(value.terms) || typeof value.degraded !== "boolean") return invalid();
  exactPublicFields(value, ["topic", "terms", "degraded"]);
  const topic = requiredString(value.topic);
  const terms = value.terms.map(requiredString);
  if (!terms.length || terms.length > 10 || terms[0] !== topic ||
      terms.some(term => term.length > 512) || new Set(terms.map(term => term.toLowerCase())).size !== terms.length) return invalid();
  return { topic, terms, degraded: value.degraded };
}
function parseGenerationPreview(value: unknown): GenerationPreview {
  if (!isObject(value) || !Array.isArray(value.outbound_payloads)) return invalid();
  exactPublicFields(value, ["preview_id", "scope_fingerprint", "provider", "model", "thinking", "reasoning_effort", "mode", "message_count", "character_count",
    "estimated_input_tokens", "max_output_tokens", "provider_calls", "price_status", "estimated_cost", "cache_hit",
    "will_send_external", "outbound_payloads", "expires_at", ...(Object.hasOwn(value, "synthesis_planned") ? ["synthesis_planned"] : [])]);
  if (Object.hasOwn(value, "synthesis_planned") && typeof value.synthesis_planned !== "boolean") return invalid();
  if (value.price_status !== "unknown" || value.estimated_cost !== null || typeof value.cache_hit !== "boolean" ||
      typeof value.will_send_external !== "boolean") return invalid();
  const thinking = member(["disabled", "enabled"] as const, value.thinking);
  const reasoningEffort = value.reasoning_effort === null ? null : member(["low", "high", "max"] as const, value.reasoning_effort);
  if ((thinking === "enabled") !== (reasoningEffort !== null)) return invalid();
  const integer = (item: unknown) => Number.isInteger(item) && Number(item) >= 0 ? Number(item) : invalid();
  return { preview_id: requiredString(value.preview_id), scope_fingerprint: disclosureHash(value.scope_fingerprint),
    provider: requiredString(value.provider), model: requiredString(value.model), mode: member(["fast", "complete"] as const, value.mode),
    thinking, reasoning_effort: reasoningEffort,
    message_count: integer(value.message_count), character_count: integer(value.character_count),
    estimated_input_tokens: integer(value.estimated_input_tokens), max_output_tokens: integer(value.max_output_tokens),
    provider_calls: integer(value.provider_calls), price_status: "unknown", estimated_cost: null,
    cache_hit: value.cache_hit, will_send_external: value.will_send_external,
    ...(value.synthesis_planned === true ? { synthesis_planned: true } : {}),
    outbound_payloads: value.outbound_payloads.map(item => typeof item === "string" && item.trim() ? item : invalid()),
    expires_at: expiresAt(value.expires_at) };
}
function parseGenerationConsent(value: unknown): GenerationConsent {
  if (!isObject(value)) return invalid();
  exactPublicFields(value, ["consent_id", "scope_fingerprint", "outbound_authorized", "expires_at"]);
  if (typeof value.outbound_authorized !== "boolean") return invalid();
  return { consent_id: requiredString(value.consent_id), scope_fingerprint: disclosureHash(value.scope_fingerprint),
    outbound_authorized: value.outbound_authorized, expires_at: expiresAt(value.expires_at) };
}
const plainStrings = (value: unknown): string[] => Array.isArray(value) && value.every(item => typeof item === "string") ? value as string[] : invalid();
function parseHowTo(value: unknown): HowToResult {
  if (!isObject(value) || !Array.isArray(value.preparations) || !Array.isArray(value.steps) || !Array.isArray(value.checks) ||
      !Array.isArray(value.missing_information) || !Array.isArray(value.conflicts)) return invalid();
  const hasSummary = Object.hasOwn(value, "summary");
  const optionalFields = ["professional_extensions", "user_text"].filter(field => Object.hasOwn(value, field));
  exactPublicFields(value, [...(hasSummary
    ? ["status", "title", "preparations", "steps", "checks", "missing_information", "conflicts", "summary"]
    : ["status", "title", "preparations", "steps", "checks", "missing_information", "conflicts"]), ...optionalFields]);
  const preparations = value.preparations.map(item => {
    if (!isObject(item)) return invalid(); exactPublicFields(item, ["source_message_ids", "text"]);
    return { text: requiredString(item.text), source_message_ids: parseStringList(item.source_message_ids) };
  });
  const steps = value.steps.map(item => {
    if (!isObject(item)) return invalid();
    exactPublicFields(item, ["source_message_ids", "instruction", "location", "parameters", "conditions", "warnings", "expected_result"]);
    return { instruction: requiredString(item.instruction), location: nullableString(item.location), parameters: plainStrings(item.parameters),
      conditions: plainStrings(item.conditions), warnings: plainStrings(item.warnings), expected_result: nullableString(item.expected_result),
      source_message_ids: parseStringList(item.source_message_ids) };
  });
  const checks = value.checks.map(item => {
    if (!isObject(item)) return invalid(); exactPublicFields(item, ["source_message_ids", "text"]);
    return { text: requiredString(item.text), source_message_ids: parseStringList(item.source_message_ids) };
  });
  const conflicts = value.conflicts.map(item => {
    if (!isObject(item)) return invalid();
    exactPublicFields(item, ["description", "claim_a", "claim_a_source_message_ids", "claim_b", "claim_b_source_message_ids", "resolution", "resolution_source_message_ids"]);
    return { description: requiredString(item.description), claim_a: requiredString(item.claim_a),
      claim_a_source_message_ids: parseStringList(item.claim_a_source_message_ids), claim_b: requiredString(item.claim_b),
      claim_b_source_message_ids: parseStringList(item.claim_b_source_message_ids), resolution: nullableString(item.resolution),
      resolution_source_message_ids: parseStringList(item.resolution_source_message_ids, true) };
  });
  const status = member(["complete", "insufficient_evidence"] as const, value.status);
  const title = requiredString(value.title);
  const summary = hasSummary ? parseChatSummary(value.summary) : undefined;
  if ((status === "complete" && !steps.length && !summary) ||
      (summary && (steps.length || preparations.length || checks.length || conflicts.length || plainStrings(value.missing_information).length)) ||
      (status === "insufficient_evidence" &&
      ((!Object.hasOwn(value, "user_text") && title !== "选定范围内未找到足够依据") || preparations.length || steps.length || checks.length || conflicts.length || summary))) return invalid();
  return { status, title, preparations, steps, checks, missing_information: plainStrings(value.missing_information), conflicts,
    ...(Object.hasOwn(value, "professional_extensions") ? { professional_extensions: (() => {
      if (!Array.isArray(value.professional_extensions) || value.professional_extensions.length > 12) return invalid();
      return value.professional_extensions.map(item => {
        if (!isObject(item)) return invalid(); exactPublicFields(item, ["heading", "text"]);
        return { heading: requiredString(item.heading), text: requiredString(item.text) };
      });
    })() } : {}),
    ...(Object.hasOwn(value, "user_text") ? { user_text: requiredString(value.user_text) } : {}),
    ...(summary ? { summary } : {}) };
}
function parseChatSummary(value: unknown): NonNullable<HowToResult["summary"]> {
  if (!isObject(value)) return invalid();
  if (Object.hasOwn(value, "sections")) {
    exactPublicFields(value, ["summary_type", "sections"]);
    if (!Array.isArray(value.sections) || !value.sections.length || value.sections.length > 40) return invalid();
    return { summary_type: member(["viewpoints", "ideas", "mixed"] as const, value.summary_type),
      sections: value.sections.map(section => {
        if (!isObject(section)) return invalid();
        exactPublicFields(section, ["heading", "text", "source_message_ids"]);
        return { heading: requiredString(section.heading), text: requiredString(section.text),
          source_message_ids: parseStringList(section.source_message_ids) };
      }) };
  }
  const sections = ["core_summary", "discussions", "decisions", "action_items", "open_questions"] as const;
  exactPublicFields(value, sections);
  const parsed = Object.fromEntries(sections.map(section => [section, (() => {
    const items = value[section];
    if (!Array.isArray(items)) return invalid();
    return items.map(item => {
      if (!isObject(item)) return invalid();
      exactPublicFields(item, ["text", "source_message_ids"]);
      return { text: requiredString(item.text), source_message_ids: parseStringList(item.source_message_ids) };
    });
  })()])) as unknown as LegacyChatSummary;
  if (!parsed.core_summary.length) return invalid();
  return parsed;
}
function parseValidatedGeneration(value: unknown): ValidatedGeneration {
  if (!isObject(value) || !Array.isArray(value.source_extraction_ids) || !Array.isArray(value.evidence)) return invalid();
  const mixed = value.schema_version === 2;
  exactPublicFields(value, mixed
    ? ["schema_version", "evidence_status", "source_refs", "snapshot_id", "scope_fingerprint", "source_extraction_ids", "result", "evidence"]
    : ["evidence_status", "batch_id", "snapshot_id", "scope_fingerprint", "source_extraction_ids", "result", "evidence"]);
  if (value.evidence_status !== "verified") return invalid();
  const evidence = value.evidence.map(parseEvidence);
  if (isObject(value.result) && Object.hasOwn(value.result, "user_text")) return invalid();
  return { evidence_status: "verified", ...(mixed
    ? { schema_version: 2 as const, source_refs: parseSourceRefs(value.source_refs) }
    : { batch_id: requiredString(value.batch_id) }), snapshot_id: requiredString(value.snapshot_id),
    scope_fingerprint: disclosureHash(value.scope_fingerprint), source_extraction_ids: parseStringList(value.source_extraction_ids, true),
    result: parseHowTo(value.result), evidence };
}
function parseGenerationJob(value: unknown): GenerationJob {
  if (!isObject(value) || !isObject(value.usage)) return invalid();
  exactPublicFields(value, ["job_id", "state", "scope_fingerprint", "completed_chunks", "total_chunks", "cancellation_requested",
    "cache_hit", "evidence_status", "error_code", "revision", "usage", "result"]);
  if (typeof value.cancellation_requested !== "boolean" || typeof value.cache_hit !== "boolean") return invalid();
  const completed = nullableInteger(value.completed_chunks) ?? invalid(); const total = nullableInteger(value.total_chunks) ?? invalid();
  if (completed > total) return invalid();
  const state = member(generationStates, value.state);
  const evidence_status = member(["unverified", "verified"] as const, value.evidence_status);
  const result = value.result === null ? null : parseValidatedGeneration(value.result);
  if ((state === "complete") !== (result !== null && evidence_status === "verified")) return invalid();
  exactPublicFields(value.usage, ["request_count", "completed_requests", "failed_requests", "cancelled_requests",
    "unknown_usage_requests", "known_prompt_tokens", "known_completion_tokens", "models", "price_status",
    "price_source", "price_version", "price_observed_at", "currency", "estimated_cost", "actual_cost", "may_have_incurred_cost"]);
  if (!Array.isArray(value.usage.models) || value.usage.price_status !== "unavailable" ||
      [value.usage.price_source, value.usage.price_version, value.usage.price_observed_at, value.usage.currency,
        value.usage.estimated_cost, value.usage.actual_cost].some(item => item !== null) ||
      typeof value.usage.may_have_incurred_cost !== "boolean") return invalid();
  const usageInteger = (item: unknown) => Number.isInteger(item) && Number(item) >= 0 ? Number(item) : invalid();
  const usage = { request_count: usageInteger(value.usage.request_count), completed_requests: usageInteger(value.usage.completed_requests),
    failed_requests: usageInteger(value.usage.failed_requests), cancelled_requests: usageInteger(value.usage.cancelled_requests),
    unknown_usage_requests: usageInteger(value.usage.unknown_usage_requests), known_prompt_tokens: usageInteger(value.usage.known_prompt_tokens),
    known_completion_tokens: usageInteger(value.usage.known_completion_tokens), models: value.usage.models.map(requiredString),
    price_status: "unavailable" as const, price_source: null, price_version: null, price_observed_at: null,
    currency: null, estimated_cost: null, actual_cost: null, may_have_incurred_cost: value.usage.may_have_incurred_cost };
  if (usage.completed_requests + usage.failed_requests + usage.cancelled_requests > usage.request_count) return invalid();
  return { job_id: requiredString(value.job_id), state, scope_fingerprint: disclosureHash(value.scope_fingerprint),
    completed_chunks: completed, total_chunks: total, cancellation_requested: value.cancellation_requested,
    cache_hit: value.cache_hit, evidence_status, error_code: nullableString(value.error_code),
    revision: nullableInteger(value.revision) ?? invalid(), usage, result };
}
function parseEvidence(value: unknown): ValidatedEvidence {
  if (!isObject(value)) return invalid();
  const mixed = Object.hasOwn(value, "source_kind");
  exactPublicFields(value, ["message_id", "conversation_id", "timestamp", "sender_id", "sender_name", "quote",
    ...(mixed ? ["source_kind", "source_batch_id"] : []), ...(Object.hasOwn(value, "image_id") ? ["image_id"] : [])]);
  if (!mixed && Object.hasOwn(value, "image_id")) return invalid();
  return { message_id: requiredString(value.message_id), conversation_id: mixed ? nullableString(value.conversation_id) : requiredString(value.conversation_id),
    timestamp: value.timestamp === null ? null : expiresAt(value.timestamp), sender_id: nullableString(value.sender_id), sender_name: nullableString(value.sender_name),
    quote: requiredString(value.quote), ...(mixed ? { source_kind: member(["chat_message", "note_passage"] as const, value.source_kind),
      source_batch_id: requiredString(value.source_batch_id), ...(Object.hasOwn(value, "image_id") ? { image_id: requiredString(value.image_id) } : {}) } : {}) };
}
function parseSourceRefs(value: unknown): SourceRef[] {
  if (!Array.isArray(value) || !value.length || value.length > 50) return invalid();
  const refs = value.map(item => {
    if (!isObject(item)) return invalid();
    exactPublicFields(item, ["kind", "id"]);
    return { kind: member(["chat_snapshot", "content_batch"] as const, item.kind), id: requiredString(item.id) };
  });
  return new Set(refs.map(item => `${item.kind}:${item.id}`)).size === refs.length ? refs : invalid();
}
function parseDocumentVersion(value: unknown): DocumentVersion {
  if (!isObject(value)) return invalid();
  exactPublicFields(value, ["document_id", "version_id", "ordinal", "origin", "evidence_status", "parent_version_id",
    "restored_from_version_id", "source_generation_job_id", "change_summary", "created_at", "content"]);
  const origin = member(["ai_generated", "user_edited", "restored"] as const, value.origin);
  const evidence_status = member(["verified", "unverified"] as const, value.evidence_status);
  if ((origin === "ai_generated") !== (evidence_status === "verified") || typeof value.change_summary !== "string") return invalid();
  if (origin === "ai_generated" && isObject(value.content) && Object.hasOwn(value.content, "user_text")) return invalid();
  return { document_id: requiredString(value.document_id), version_id: requiredString(value.version_id),
    ordinal: nullableInteger(value.ordinal) || invalid(), origin, evidence_status,
    parent_version_id: nullableString(value.parent_version_id), restored_from_version_id: nullableString(value.restored_from_version_id),
    source_generation_job_id: requiredString(value.source_generation_job_id), change_summary: value.change_summary,
    created_at: expiresAt(value.created_at), content: parseHowTo(value.content) };
}
function parseDocument(value: unknown): HowToDocument {
  if (!isObject(value) || !Array.isArray(value.versions) || !Array.isArray(value.source_evidence)) return invalid();
  exactPublicFields(value, ["document_id", "source_generation_job_id", "source_snapshot_id", "created_at", "updated_at",
    "current_version", "versions", "source_evidence", ...(Object.hasOwn(value, "source_refs") ? ["source_refs"] : [])]);
  const document_id = requiredString(value.document_id); const source_generation_job_id = requiredString(value.source_generation_job_id);
  const versions = value.versions.map(parseDocumentVersion); const current_version = parseDocumentVersion(value.current_version);
  if (!versions.length || versions.some(item => item.document_id !== document_id || item.source_generation_job_id !== source_generation_job_id) ||
      !versions.some(item => item.version_id === current_version.version_id)) return invalid();
  return { document_id, source_generation_job_id, source_snapshot_id: value.source_snapshot_id === null ? null : requiredString(value.source_snapshot_id),
    ...(Object.hasOwn(value, "source_refs") ? { source_refs: parseSourceRefs(value.source_refs) } : {}),
    created_at: expiresAt(value.created_at), updated_at: expiresAt(value.updated_at), current_version, versions,
    source_evidence: value.source_evidence.map(parseEvidence) };
}
function parseDocumentRecoveryPage(value: unknown): DocumentRecoveryPage {
  if (!isObject(value) || !Array.isArray(value.documents)) return invalid();
  exactPublicFields(value, ["documents", "next_cursor"]);
  const documents = value.documents.map(parseDocument);
  if (new Set(documents.map(item => item.document_id)).size !== documents.length) return invalid();
  return { documents, next_cursor: value.next_cursor === null ? null : requiredString(value.next_cursor) };
}
function parseDocumentArtifact(value: unknown): DocumentArtifact {
  if (!isObject(value)) return invalid();
  exactPublicFields(value, ["artifact_id", "document_id", "version_id", "format", "source_mode", "filename", "byte_size", "sha256", "created_at"]);
  const filename = requiredString(value.filename);
  if (filename.includes("/") || filename.includes("..")) return invalid();
  return { artifact_id: requiredString(value.artifact_id), document_id: requiredString(value.document_id),
    version_id: requiredString(value.version_id), format: member(["markdown", "docx"] as const, value.format),
    source_mode: member(["body_only", "with_sources"] as const, value.source_mode), filename,
    byte_size: nullableInteger(value.byte_size) || invalid(), sha256: disclosureHash(value.sha256), created_at: expiresAt(value.created_at) };
}
function parseDeletionCounts(value: unknown): Record<DeletionCountKey, number> {
  if (!isObject(value)) return invalid();
  exactPublicFields(value, deletionCountKeys);
  return Object.fromEntries(deletionCountKeys.map(key => [key,
    Number.isInteger(value[key]) && Number(value[key]) >= 0 ? Number(value[key]) : invalid(),
  ])) as Record<DeletionCountKey, number>;
}
function parseDeletionPlan(value: unknown): DeletionPlan {
  if (!isObject(value) || !Array.isArray(value.effects) || typeof value.include_managed_files !== "boolean") return invalid();
  exactPublicFields(value, ["plan_id", "target_kind", "target_id", "include_managed_files", "fingerprint", "counts", "effects", "expires_at"]);
  return { plan_id: requiredString(value.plan_id), target_kind: member(["document", "batch", "content_batch"] as const, value.target_kind),
    target_id: requiredString(value.target_id), include_managed_files: value.include_managed_files,
    fingerprint: disclosureHash(value.fingerprint), counts: parseDeletionCounts(value.counts),
    effects: value.effects.map(requiredString), expires_at: expiresAt(value.expires_at) };
}
function parseDeletionResult(value: unknown): DeletionResult {
  if (!isObject(value) || typeof value.managed_files_retained !== "boolean") return invalid();
  exactPublicFields(value, ["plan_id", "state", "deleted_counts", "managed_files_retained"]);
  return { plan_id: requiredString(value.plan_id), state: member(["complete", "cleanup_pending"] as const, value.state),
    deleted_counts: parseDeletionCounts(value.deleted_counts), managed_files_retained: value.managed_files_retained };
}
function parseImportPreview(value: unknown): ImportPreview {
  if (!isObject(value) || !isObject(value.source) || !isObject(value.analysis) || !isObject(value.applied_mapping) ||
      typeof value.ready_to_commit !== "boolean") return invalid();
  exactPublicFields(value, ["preview_id", "preview_fingerprint", "expires_at_utc", "source", "applied_mapping", "analysis", "ready_to_commit"]);
  exactPublicFields(value.source, ["format", "encoding", "container", "member_name", "row_count", "columns", "sha256", "byte_size", "warnings"]);
  const integer = (item: unknown) => Number.isInteger(item) && Number(item) >= 0 ? Number(item) : invalid();
  const columns = plainStrings(value.source.columns); const warnings = plainStrings(value.source.warnings);
  const mapping = Object.fromEntries(Object.entries(value.applied_mapping).map(([key, item]) => [requiredString(key), requiredString(item)]));
  return {
    preview_id: requiredString(value.preview_id), preview_fingerprint: disclosureHash(value.preview_fingerprint),
    expires_at_utc: expiresAt(value.expires_at_utc), ready_to_commit: value.ready_to_commit,
    source: { format: member(["txt", "csv", "json", "jsonl", "xlsx"] as const, value.source.format),
      encoding: requiredString(value.source.encoding), container: member(["plain", "zip", "xlsx"] as const, value.source.container),
      member_name: nullableString(value.source.member_name), row_count: integer(value.source.row_count), columns,
      sha256: disclosureHash(value.source.sha256), byte_size: integer(value.source.byte_size), warnings },
    applied_mapping: mapping, analysis: parseImportAnalysis(value.analysis),
  };
}
function parseImportAnalysis(analysis: unknown): ImportPreview["analysis"] {
  if (!isObject(analysis)) return invalid();
  const integer = (item: unknown) => Number.isInteger(item) && Number(item) >= 0 ? Number(item) : invalid();
  if (!isObject(analysis.message_types) || !Array.isArray(analysis.conversations) || !Array.isArray(analysis.warnings) ||
      !Array.isArray(analysis.issues) || !Array.isArray(analysis.samples)) return invalid();
  exactPublicFields(analysis, ["total_count", "parsed_count", "unresolved_count", "duplicate_count", "sender_count", "time_start", "time_end",
    "message_types", "conversations", "warnings", "issues", "samples"]);
  exactPublicFields(analysis.message_types, ["text", "image", "file", "other", "unknown"]);
  const conversations = analysis.conversations.map(item => {
    if (!isObject(item)) return invalid(); exactPublicFields(item, ["conversation_id", "conversation_name", "message_count"]);
    return { conversation_id: requiredString(item.conversation_id), conversation_name: nullableString(item.conversation_name), message_count: integer(item.message_count) };
  });
  const issues = analysis.issues.map(item => {
    if (!isObject(item)) return invalid(); exactPublicFields(item, ["row_number", "code"]);
    return { row_number: integer(item.row_number), code: requiredString(item.code) };
  });
  const samples = analysis.samples.map(item => {
    if (!isObject(item)) return invalid(); exactPublicFields(item, ["row_number", "conversation_name", "sender_name", "timestamp", "message_type", "text"]);
    return { row_number: integer(item.row_number), conversation_name: nullableString(item.conversation_name), sender_name: nullableString(item.sender_name),
      timestamp: item.timestamp === null ? null : expiresAt(item.timestamp), message_type: requiredString(item.message_type), text: item.text === null ? null : typeof item.text === "string" ? item.text : invalid() };
  });
  return { total_count: integer(analysis.total_count), parsed_count: integer(analysis.parsed_count),
      unresolved_count: integer(analysis.unresolved_count), duplicate_count: integer(analysis.duplicate_count), sender_count: integer(analysis.sender_count),
      time_start: analysis.time_start === null ? null : expiresAt(analysis.time_start), time_end: analysis.time_end === null ? null : expiresAt(analysis.time_end),
      message_types: { text: integer(analysis.message_types.text), image: integer(analysis.message_types.image),
        file: integer(analysis.message_types.file), other: integer(analysis.message_types.other), unknown: integer(analysis.message_types.unknown) },
      conversations, warnings: plainStrings(analysis.warnings), issues, samples,
  };
}
function parseImportCommit(value: unknown): ImportCommit {
  if (!isObject(value) || !Array.isArray(value.conversations) || !Array.isArray(value.warnings) || value.source_id !== "manual") return invalid();
  exactPublicFields(value, ["import_id", "batch_id", "snapshot_id", "source_id", "account_id", "range_start", "range_end", "display_timezone", "conversations", "warnings", ...(Object.hasOwn(value, "analysis") ? ["analysis"] : [])]);
  const range_start = expiresAt(value.range_start); const range_end = expiresAt(value.range_end);
  const display_timezone = requiredString(value.display_timezone);
  if (Date.parse(range_end) <= Date.parse(range_start)) return invalid();
  if (!validTimezone(display_timezone)) return invalid();
  const conversations = value.conversations.map(item => {
    if (!isObject(item)) return invalid(); exactPublicFields(item, ["conversation_selection_id", "display_name", "conversation_type"]);
    return { conversation_selection_id: requiredString(item.conversation_selection_id), display_name: nullableString(item.display_name),
      conversation_type: nullableString(item.conversation_type) };
  });
  if (!conversations.length || new Set(conversations.map(item => item.conversation_selection_id)).size !== conversations.length) return invalid();
  return { import_id: requiredString(value.import_id), batch_id: requiredString(value.batch_id), snapshot_id: requiredString(value.snapshot_id),
    source_id: "manual", account_id: requiredString(value.account_id), range_start, range_end,
    display_timezone,
    conversations, warnings: plainStrings(value.warnings),
    ...(Object.hasOwn(value, "analysis") ? { analysis: parseImportAnalysis(value.analysis) } : {}) };
}
function parseImportRecoveryPage(value: unknown): ImportRecoveryPage {
  if (!isObject(value) || !Array.isArray(value.imports)) return invalid();
  exactPublicFields(value, ["imports", "next_cursor"]);
  const next_cursor = value.next_cursor === null ? null : requiredString(value.next_cursor);
  if (next_cursor !== null && !/^[A-Za-z0-9_-]{1,256}$/.test(next_cursor)) return invalid();
  const imports = value.imports.map(parseImportCommit);
  if (new Set(imports.map(item => item.import_id)).size !== imports.length) return invalid();
  return { imports, next_cursor };
}
function parseContentPreview(value: unknown): ContentPreview {
  if (!isObject(value) || !Array.isArray(value.units) || !Array.isArray(value.images)) return invalid();
  exactPublicFields(value, ["preview_id", "preview_fingerprint", "suggested_kind", "status", "units", "images", "warnings", "expires_at_utc"]);
  const integer = (item: unknown) => Number.isInteger(item) && Number(item) >= 0 ? Number(item) : invalid();
  const units = value.units.map(item => {
    if (!isObject(item)) return invalid();
    exactPublicFields(item, ["unit_id", "kind", "group_id", "text", "sender_name", "sent_at", "order", "image_ref"]);
    return { unit_id: requiredString(item.unit_id), kind: member(["chat_message", "note_passage"] as const, item.kind),
      group_id: requiredString(item.group_id), text: requiredString(item.text), sender_name: nullableString(item.sender_name),
      sent_at: item.sent_at === null ? null : expiresAt(item.sent_at), order: integer(item.order), image_ref: nullableString(item.image_ref) };
  });
  const images = value.images.map(item => {
    if (!isObject(item)) return invalid();
    exactPublicFields(item, ["image_ref", "filename", "input_order", "sha256"]);
    return { image_ref: requiredString(item.image_ref), filename: requiredString(item.filename),
      input_order: integer(item.input_order), sha256: disclosureHash(item.sha256) };
  });
  if (new Set(units.map(item => item.unit_id)).size !== units.length || new Set(images.map(item => item.image_ref)).size !== images.length) return invalid();
  return { preview_id: requiredString(value.preview_id), preview_fingerprint: disclosureHash(value.preview_fingerprint),
    suggested_kind: member(["chat", "note", "uncertain"] as const, value.suggested_kind),
    status: member(["ready", "empty_body", "boundary_uncertain", "ocr_unavailable"] as const, value.status),
    units, images, warnings: plainStrings(value.warnings), expires_at_utc: expiresAt(value.expires_at_utc) };
}
function parseContentBatch(value: unknown): ContentBatch {
  if (!isObject(value) || !Array.isArray(value.images)) return invalid();
  exactPublicFields(value, ["batch_id", "unit_count", "image_count", "images", "created_at_utc"]);
  const integer = (item: unknown) => Number.isInteger(item) && Number(item) >= 0 ? Number(item) : invalid();
  const images = value.images.map(item => {
    if (!isObject(item)) return invalid();
    exactPublicFields(item, ["image_id", "input_order", "sha256"]);
    return { image_id: requiredString(item.image_id), input_order: integer(item.input_order), sha256: disclosureHash(item.sha256) };
  });
  const image_count = integer(value.image_count);
  if (images.length !== image_count) return invalid();
  return { batch_id: requiredString(value.batch_id), unit_count: integer(value.unit_count), image_count,
    images, created_at_utc: expiresAt(value.created_at_utc) };
}
function parseContentBatchPage(value: unknown): ContentBatchPage {
  if (!isObject(value) || !Array.isArray(value.items)) return invalid();
  exactPublicFields(value, ["items", "next_cursor"]);
  const items = value.items.map(parseContentBatch);
  if (new Set(items.map(item => item.batch_id)).size !== items.length) return invalid();
  return { items, next_cursor: value.next_cursor === null ? null : requiredString(value.next_cursor) };
}
function parseMixedSearchPage(value: unknown): MixedSearchPage {
  if (!isObject(value) || !Array.isArray(value.hits)) return invalid();
  exactPublicFields(value, ["hits"]);
  const hits = value.hits.map(item => {
    if (!isObject(item) || !isObject(item.source_ref)) return invalid();
    exactPublicFields(item, ["unit_id", "source_kind", "source_ref", "text", "sender_name", "timestamp", "source_order", "image_id"]);
    exactPublicFields(item.source_ref, ["kind", "id"]);
    return { unit_id: requiredString(item.unit_id), source_kind: member(["chat_message", "note_passage"] as const, item.source_kind),
      source_ref: { kind: member(["chat_snapshot", "content_batch"] as const, item.source_ref.kind), id: requiredString(item.source_ref.id) },
      text: requiredString(item.text), sender_name: nullableString(item.sender_name),
      timestamp: item.timestamp === null ? null : expiresAt(item.timestamp),
      source_order: item.source_order === null ? null : Number.isInteger(item.source_order) && Number(item.source_order) >= 0 ? Number(item.source_order) : invalid(),
      image_id: nullableString(item.image_id) };
  });
  if (new Set(hits.map(item => item.unit_id)).size !== hits.length) return invalid();
  return { hits };
}
function validExportSelection(value: ExportSelection): boolean {
  return sourceIds.includes(value.source_id) && !!value.account_id.trim() && !!value.output_root_id.trim() &&
    validTimezone(value.display_timezone) && value.conversation_selection_ids.length > 0 && value.conversation_selection_ids.length <= 50 &&
    new Set(value.conversation_selection_ids).size === value.conversation_selection_ids.length &&
    value.conversation_selection_ids.every(item => !!item.trim()) && Number.isFinite(Date.parse(value.range_start)) &&
    Date.parse(value.range_end) > Date.parse(value.range_start);
}

/** Credentials and CSRF live only in this client instance; never persist or log responses. */
export function createClient() {
  interface SessionLease { csrfToken: string; expiresAt: number; generation: number }
  let session: Promise<SessionLease> | undefined;
  let latestGeneration = 0;
  function checkedPath(path: string) {
    if (!path.startsWith("/api/") || new URL(path, location.origin).origin !== location.origin) throw new ApiError(0);
  }
  function startBootstrap(): Promise<SessionLease> {
    checkedPath("/api/session");
    const generation = ++latestGeneration;
    const pending = (async () => {
      try {
        const response = await fetch("/api/session", { method: "GET", headers: new Headers({ Accept: "application/json" }),
          credentials: "same-origin", cache: "no-store", redirect: "error" });
        if (!response.ok) throw new ApiError(response.status);
        const value = await response.json() as unknown;
        if (!isObject(value)) return invalid();
        exactPublicFields(value, ["csrf_token", "expires_at"]);
        const expiry = expiresAt(value.expires_at);
        const expiresAtMillis = Date.parse(expiry);
        if (expiresAtMillis <= Date.now()) return invalid();
        return { csrfToken: requiredString(value.csrf_token), expiresAt: expiresAtMillis, generation };
      } catch (error) { throw error instanceof ApiError ? error : new ApiError(0); }
    })();
    session = pending;
    pending.catch(() => { if (session === pending) session = undefined; });
    return pending;
  }
  async function bootstrap(): Promise<SessionLease> {
    while (true) {
      const pending = session ?? startBootstrap();
      const lease = await pending;
      if (lease.expiresAt > Date.now()) return lease;
      if (session === pending) session = undefined;
    }
  }
  async function request<T>(path: string, parser: Parser<T>, body?: unknown, retryGet = true, csrfForGet = false, mutationMethod: "POST" | "PATCH" = "POST"): Promise<T> {
    checkedPath(path);
    try {
      const lease = await bootstrap();
      const headers = new Headers({ Accept: "application/json" });
      if (body !== undefined || csrfForGet) {
        headers.set("X-CSRF-Token", lease.csrfToken);
      }
      if (body !== undefined) {
        headers.set("Content-Type", "application/json");
      }
      const response = await fetch(path, { method: body === undefined ? "GET" : mutationMethod, headers,
        credentials: "same-origin", cache: "no-store", redirect: "error", ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
      if (response.status === 401) {
        if (lease.generation === latestGeneration) session = undefined;
        if (body === undefined && retryGet) return request(path, parser, undefined, false);
      }
      if (!response.ok) {
        let code: string | null = null;
        try {
          const payload = await response.json() as unknown;
          if (isObject(payload) && isObject(payload.detail) &&
              typeof payload.detail.code === "string" && /^[a-z_]{1,64}$/.test(payload.detail.code)) {
            code = payload.detail.code;
          }
        } catch { /* Error bodies are optional and never surfaced verbatim. */ }
        throw new ApiError(response.status, code);
      }
      return parser(response.status === 204 ? undefined : await response.json() as unknown);
    } catch (error) { throw error instanceof ApiError ? error : new ApiError(0); }
  }
  const route = (source: SourceId) => `/api/sources/${source}`;
  function secretOperation(source: SourceId, account_id: string, consent_challenge: string, disclosure: SecretDisclosure, value?: string) {
    if (disclosure.source_id !== source || disclosure.tool !== sourceTools[source] ||
        !secretOperations.includes(disclosure.operation) || !/^[a-f0-9]{64}$/.test(disclosure.hash) || !disclosure.revision ||
        (disclosure.operation === "secret_set" ? typeof value !== "string" || !value : value !== undefined)) {
      return Promise.reject(new ApiError(0));
    }
    return request<void>(`${route(source)}/secret-operations`, parseVoid, {
      operation: disclosure.operation, account_id, consent_challenge, confirmed: true,
      ...(disclosure.operation === "secret_set" ? { value } : {}),
      disclosure_revision: disclosure.revision, disclosure_hash: disclosure.hash,
    });
  }
  return {
    setupOptions: () => request("/api/setup-options", parseSetupOptions),
    environment: () => request("/api/environment", parseEnvironment),
    sources: () => request("/api/sources", parseSources),
    configure: (configuration: SourceConfiguration) => {
      if (!isObject(configuration) || !sourceIds.includes(configuration.source_id) ||
          configuration.base_url !== sourceBaseUrls[configuration.source_id] ||
          ![configuration.account_id, configuration.client_version, configuration.tool_version, configuration.account_version]
            .every(item => typeof item === "string" && !!item.trim())) return Promise.reject(new ApiError(0));
      return request("/api/sources", value => {
        if (!isObject(value)) return invalid();
        exactPublicFields(value, ["source_id", "status"]);
        return { source_id: member(sourceIds, value.source_id), status: member(Object.keys(statusLabels) as Array<keyof typeof statusLabels>, value.status) };
      }, configuration);
    },
    disconnectSource: (source: SourceId) => request<void>(`${route(source)}/disconnect`, parseVoid, { confirmed: true }),
    async conversations(source: SourceId, account: string): Promise<Conversation[]> {
      return request(`${route(source)}/conversations?account_id=${encodeURIComponent(account)}`, parseConversations, undefined, false, true);
    },
    async prepare(source: SourceId, selection: Selection, timezone: string) {
      const value = await request(`${route(source)}/verification-consents`, parsePreparedConsent, { selection, timezone });
      return value.disclosure.source_id === source ? value : invalid();
    },
    start: (source: SourceId, selection: Selection, timezone: string, challenge: string, disclosure: Disclosure) => {
      if (disclosure.source_id !== source || disclosure.tool !== sourceTools[source] || !/^[a-f0-9]{64}$/.test(disclosure.hash) || !disclosure.revision) return Promise.reject(new ApiError(0));
      return request(`${route(source)}/verification-jobs`, parseCreated, { selection, timezone, consent_challenge: challenge, confirmed: true,
        disclosure_revision: disclosure.revision, disclosure_hash: disclosure.hash });
    },
    job: async (source: SourceId, id: string) => {
      const value = await request(`${route(source)}/verification-jobs/${encodeURIComponent(id)}`, parseReport);
      return value.source_id === source && value.job_id === id ? value : invalid();
    },
    cancel: async (source: SourceId, id: string) => {
      const value = await request(`${route(source)}/verification-jobs/${encodeURIComponent(id)}/cancel`, parseCancelled, {});
      return value.job_id === id ? value : invalid();
    },
    async secretConsent(source: SourceId, account_id: string, operation: SecretOperation = "secret_set") {
      if (!secretOperations.includes(operation)) return invalid();
      const value = await request(`${route(source)}/secret-consents`, parseSecretConsent, { operation, account_id });
      return value.disclosure.source_id === source && value.disclosure.operation === operation ? value : invalid();
    },
    saveSecret: (source: SourceId, account_id: string, consent_challenge: string, value: string, disclosure: SecretDisclosure) => {
      return disclosure.operation === "secret_set" ? secretOperation(source, account_id, consent_challenge, disclosure, value) : Promise.reject(new ApiError(0));
    },
    authorizeSecretUse: (source: SourceId, account_id: string, consent_challenge: string, disclosure: SecretDisclosure) =>
      disclosure.operation === "secret_get" ? secretOperation(source, account_id, consent_challenge, disclosure) : Promise.reject(new ApiError(0)),
    deleteSecret: (source: SourceId, account_id: string, consent_challenge: string, disclosure: SecretDisclosure) =>
      disclosure.operation === "secret_delete" ? secretOperation(source, account_id, consent_challenge, disclosure) : Promise.reject(new ApiError(0)),
    issueExportConsent: (selection: ExportSelection) => {
      if (!validExportSelection(selection)) return Promise.reject(new ApiError(0));
      return request("/api/export-jobs/consents/from-selections", parseExportConsent, { selection, confirmed: true });
    },
    startExport: (consent_id: string, idempotency_key: string) => {
      if (![consent_id, idempotency_key].every(value => /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(value))) return Promise.reject(new ApiError(0));
      return request("/api/export-jobs", parseExportJob, { consent_id, idempotency_key });
    },
    exportJob: (id: string) => {
      if (!/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(id)) return Promise.reject(new ApiError(0));
      return request(`/api/export-jobs/${encodeURIComponent(id)}`, parseExportJob);
    },
    cancelExport: (id: string) => {
      if (!/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(id)) return Promise.reject(new ApiError(0));
      return request(`/api/export-jobs/${encodeURIComponent(id)}/cancel`, parseExportJob, {});
    },
    previewImport: (value: ImportPreviewRequest) => {
      const hasFile = value.origin === "imported_file" && typeof value.filename === "string" && !!value.filename &&
        typeof value.payload_base64 === "string" && !!value.payload_base64 && value.pasted_text === undefined;
      const hasPaste = value.origin === "paste" && typeof value.pasted_text === "string" && !!value.pasted_text &&
        value.filename === undefined && value.payload_base64 === undefined;
      if ((!hasFile && !hasPaste) || !["generic", "qq", "wechat"].includes(value.platform_hint) ||
          !validTimezone(value.options.timezone)) return Promise.reject(new ApiError(0));
      return request("/api/imports/previews", parseImportPreview, value);
    },
    commitImport: (preview_id: string, preview_fingerprint: string) => {
      if (!/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(preview_id) || !/^[a-f0-9]{64}$/.test(preview_fingerprint))
        return Promise.reject(new ApiError(0));
      return request(`/api/imports/previews/${encodeURIComponent(preview_id)}/commit`, parseImportCommit,
        { preview_fingerprint, confirmed: true });
    },
    recoverImports: (cursor: string | null = null, limit = 20) => {
      if ((cursor !== null && !/^[A-Za-z0-9_-]{1,256}$/.test(cursor)) ||
          !Number.isInteger(limit) || limit < 1 || limit > 50) return Promise.reject(new ApiError(0));
      return request("/api/imports/recovery", parseImportRecoveryPage, { cursor, limit });
    },
    previewContentImport: (value: ContentPreviewRequest) => {
      const images = value.images;
      const hasImages = Array.isArray(images) && images.length > 0 && images.length <= 20 && value.pasted_note === undefined &&
        images.every(item => !!item.filename && !!item.payload_base64 && Number.isInteger(item.input_order) && item.input_order >= 0);
      const hasNote = typeof value.pasted_note === "string" && !!value.pasted_note.trim() && images === undefined;
      if (!hasImages && !hasNote) return Promise.reject(new ApiError(0));
      return request("/api/content-imports/previews", parseContentPreview, value);
    },
    reviseContentPreview: (preview_id: string, edits: ContentUnitEdit[], image_orders: { image_ref: string; input_order: number }[] = []) => {
      if (!/^[a-f0-9]{32}$/.test(preview_id) || !Array.isArray(edits) || edits.length > 10_000) return Promise.reject(new ApiError(0));
      return request(`/api/content-imports/previews/${encodeURIComponent(preview_id)}`, parseContentPreview,
        { edits, image_orders }, true, false, "PATCH");
    },
    commitContentPreview: (preview_id: string, preview_fingerprint: string) => {
      if (!/^[a-f0-9]{32}$/.test(preview_id) || !/^[a-f0-9]{64}$/.test(preview_fingerprint)) return Promise.reject(new ApiError(0));
      return request(`/api/content-imports/previews/${encodeURIComponent(preview_id)}/commit`, parseContentBatch,
        { preview_fingerprint, confirmed: true });
    },
    recoverContentBatches: (cursor: string | null = null, limit = 20) => {
      if ((cursor !== null && !/^[a-f0-9]{32}$/.test(cursor)) || !Number.isInteger(limit) || limit < 1 || limit > 50) return Promise.reject(new ApiError(0));
      return request("/api/content-imports/recovery", parseContentBatchPage, { cursor, limit });
    },
    getContentImage: async (image_id: string): Promise<Blob> => {
      if (!/^[a-f0-9]{32}$/.test(image_id)) throw new ApiError(0);
      const lease = await bootstrap();
      const response = await fetch(`/api/content-imports/images/${encodeURIComponent(image_id)}`, {
        method: "GET", credentials: "same-origin", cache: "no-store", redirect: "error",
        headers: { Accept: "image/png,image/jpeg", "X-CSRF-Token": lease.csrfToken },
      });
      if (!response.ok || !["image/png", "image/jpeg"].includes(response.headers.get("content-type") || "")) throw new ApiError(response.status);
      const blob = await response.blob();
      if (blob.size > 15 * 1024 * 1024) throw new ApiError(0);
      return blob;
    },
    searchMixed: (query: MixedSearchRequest) => {
      if (!query.source_refs.length || query.source_refs.length > 50 || new Set(query.source_refs.map(item => `${item.kind}:${item.id}`)).size !== query.source_refs.length ||
          query.terms.length < 1 || query.terms.length > 10 || query.terms.some(item => item.length > 512) ||
          (query.start_time != null && !Number.isFinite(Date.parse(query.start_time))) ||
          (query.end_time != null && !Number.isFinite(Date.parse(query.end_time)))) return Promise.reject(new ApiError(0));
      return request("/api/mixed-search", parseMixedSearchPage, query);
    },
    searchChats: (search: ChatSearchRequest) => {
      const validSource = sourceIds.includes(search.source_id as SourceId) || search.source_id === "manual";
      const validScope = validSource && !!search.account_id.trim() && search.conversation_selection_ids.length > 0 &&
        search.conversation_selection_ids.length <= 50 && new Set(search.conversation_selection_ids).size === search.conversation_selection_ids.length &&
        search.conversation_selection_ids.every(item => /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(item)) &&
        Number.isFinite(Date.parse(search.start_time)) && Date.parse(search.end_time) > Date.parse(search.start_time);
      if (!validScope || search.query.length > 512 || search.limit < 1 || search.limit > 100 ||
          (search.cursor !== null && (!search.cursor || search.cursor.length > 2048))) return Promise.reject(new ApiError(0));
      return request("/api/search/from-selections", parseSearchPage, search);
    },
    expandTopic: (topic: string) => {
      if (!topic.trim() || topic.length > 512) return Promise.reject(new ApiError(0));
      return request("/api/generation/topic-expansion", parseTopicExpansion, { topic: topic.trim() });
    },
    previewGenerationContext: (selection: GenerationSelectionRequest) => request(
      "/api/generation/contexts/from-selections", (value: unknown): GenerationContext => {
        if (!isObject(value)) return invalid();
        exactPublicFields(value, ["messages", "added_count"]);
        const messages = parseSearchPage({ results: value.messages, next_cursor: null }).results;
        const count = nullableInteger(value.added_count);
        if (count === null || count > messages.length || messages.length > 2_000) return invalid();
        return { messages, added_count: count };
      }, selection),
    previewGeneration: (selection: GenerationSelectionRequest) => {
      const valid = (sourceIds.includes(selection.source_id as SourceId) || selection.source_id === "manual") && !!selection.account_id.trim() &&
        selection.conversation_selection_ids.length > 0 && selection.conversation_selection_ids.length <= 50 &&
        new Set(selection.conversation_selection_ids).size === selection.conversation_selection_ids.length &&
        selection.conversation_selection_ids.every(item => /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(item)) &&
        selection.excluded_message_ids.length <= 2_000 &&
        new Set(selection.excluded_message_ids).size === selection.excluded_message_ids.length &&
        selection.excluded_message_ids.every(item => /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(item)) &&
        /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(selection.snapshot_id) &&
        Number.isFinite(Date.parse(selection.start_time)) && Date.parse(selection.end_time) > Date.parse(selection.start_time) &&
        !!selection.topic.trim() && selection.topic.length <= 512 && ["fast", "complete"].includes(selection.mode) &&
        (selection.generation_kind === undefined || ["guide", "summary"].includes(selection.generation_kind)) &&
        (selection.summary_type === undefined || ["viewpoints", "ideas", "how_to", "mixed"].includes(selection.summary_type)) &&
        (selection.included_message_ids === undefined ||
          (selection.included_message_ids.length <= 2_000 &&
           new Set(selection.included_message_ids).size === selection.included_message_ids.length &&
           selection.included_message_ids.every(item => /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(item))));
      if (!valid) return Promise.reject(new ApiError(0));
      return request("/api/generation/previews/from-selections", parseGenerationPreview, selection);
    },
    previewMixedGeneration: (selection: MixedGenerationRequest) => {
      const ids = selection.included_unit_ids;
      if (!selection.source_refs.length || !ids.length || ids.length > 2_000 ||
          new Set(ids).size !== ids.length || !selection.excluded_unit_ids.every(id => ids.includes(id)) ||
          !selection.topic.trim() || selection.topic.length > 512) return Promise.reject(new ApiError(0));
      return request("/api/generation/mixed/previews", parseGenerationPreview, selection);
    },
    authorizeGeneration: (preview_id: string) => {
      if (!/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(preview_id)) return Promise.reject(new ApiError(0));
      return request("/api/generation/consents", parseGenerationConsent, { preview_id, confirmed: true });
    },
    startGeneration: (consent_id: string, idempotency_key: string) => {
      if (![consent_id, idempotency_key].every(value => /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(value))) return Promise.reject(new ApiError(0));
      return request("/api/generation/jobs", parseGenerationJob, { consent_id, idempotency_key });
    },
    generationJob: (id: string) => {
      if (!/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(id)) return Promise.reject(new ApiError(0));
      return request(`/api/generation/jobs/${encodeURIComponent(id)}`, parseGenerationJob);
    },
    cancelGeneration: (id: string) => {
      if (!/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(id)) return Promise.reject(new ApiError(0));
      return request(`/api/generation/jobs/${encodeURIComponent(id)}/cancel`, parseGenerationJob, {});
    },
    retryGeneration: (id: string) => {
      if (!/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(id)) return Promise.reject(new ApiError(0));
      return request(`/api/generation/jobs/${encodeURIComponent(id)}/retry`, parseGenerationJob, {});
    },
    createDocument: (generation_job_id: string) => {
      if (!/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(generation_job_id)) return Promise.reject(new ApiError(0));
      return request("/api/documents/from-generation", parseDocument, { generation_job_id });
    },
    recoverDocuments: (cursor: string | null = null, limit = 20) => {
      if ((cursor !== null && !/^[A-Za-z0-9_-]{1,256}$/.test(cursor)) ||
          !Number.isInteger(limit) || limit < 1 || limit > 50) return Promise.reject(new ApiError(0));
      return request("/api/documents/recovery", parseDocumentRecoveryPage, { cursor, limit });
    },
    document: (id: string) => {
      if (!/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(id)) return Promise.reject(new ApiError(0));
      return request(`/api/documents/${encodeURIComponent(id)}`, parseDocument);
    },
    createDocumentVersion: (id: string, parent_version_id: string, content: HowToResult, change_summary: string) => {
      if (![id, parent_version_id].every(value => /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(value)) || change_summary.length > 500)
        return Promise.reject(new ApiError(0));
      return request(`/api/documents/${encodeURIComponent(id)}/versions`, parseDocument, { parent_version_id, content, change_summary });
    },
    restoreDocumentVersion: (id: string, versionId: string) => {
      if (![id, versionId].every(value => /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(value))) return Promise.reject(new ApiError(0));
      return request(`/api/documents/${encodeURIComponent(id)}/versions/${encodeURIComponent(versionId)}/restore`, parseDocument, {});
    },
    exportDocument: (id: string, version_id: string, format: "markdown" | "docx", source_mode: "body_only" | "with_sources", output_root_id: string) => {
      if (![id, version_id, output_root_id].every(value => /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(value))) return Promise.reject(new ApiError(0));
      return request(`/api/documents/${encodeURIComponent(id)}/exports`, parseDocumentArtifact,
        { version_id, format, source_mode, output_root_id });
    },
    downloadDocument: async (id: string, version_id: string, format: "markdown" | "docx", source_mode: "body_only" | "with_sources") => {
      if (![id, version_id].every(value => /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(value)) ||
          !["markdown", "docx"].includes(format) || !["body_only", "with_sources"].includes(source_mode)) throw new ApiError(0);
      const lease = await bootstrap();
      const response = await fetch(`/api/documents/${encodeURIComponent(id)}/download`, {
        method: "POST", credentials: "same-origin", cache: "no-store", redirect: "error",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": lease.csrfToken },
        body: JSON.stringify({ version_id, format, source_mode }),
      });
      if (response.status === 401 && lease.generation === latestGeneration) session = undefined;
      if (!response.ok) throw new ApiError(response.status);
      return response.blob();
    },
    prepareDeletion: (target_kind: "document" | "batch" | "content_batch", target_id: string, include_managed_files: boolean) => {
      if (!["document", "batch", "content_batch"].includes(target_kind) || !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(target_id))
        return Promise.reject(new ApiError(0));
      return request("/api/deletion-plans", parseDeletionPlan, { target_kind, target_id, include_managed_files });
    },
    confirmDeletion: (plan_id: string, fingerprint: string) => {
      if (!/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(plan_id) || !/^[a-f0-9]{64}$/.test(fingerprint))
        return Promise.reject(new ApiError(0));
      return request(`/api/deletion-plans/${encodeURIComponent(plan_id)}/confirm`, parseDeletionResult,
        { fingerprint, confirmed: true });
    },
  };
}

export type LocalApiClient = ReturnType<typeof createClient>;
