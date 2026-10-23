export type SourceId = "qq" | "wechat";
export type ChatSourceId = SourceId | "manual";
export const sourceIds = ["qq", "wechat"] as const;
export const sourceBaseUrls: Record<SourceId, string> = {
  qq: "http://127.0.0.1:40653",
  wechat: "http://127.0.0.1:6131",
};
export const sourceTools: Record<SourceId, string> = { qq: "QCE", wechat: "TraceMemo" };
export const statusLabels = {
  unconfigured: "未配置", needs_user_action: "等待用户操作", documented_not_tested: "有文档，未实测",
  ready: "真实导出已验证", blocked: "已阻止", unsupported: "不支持", error: "运行错误",
} as const;
export type SourceStatus = keyof typeof statusLabels;
export const verificationStates = [
  "pending_authorization", "connecting", "reading", "saving", "verifying", "indexing",
  "complete", "partial", "failed", "cancelled",
] as const;
export type VerificationState = typeof verificationStates[number];
export const evidenceClasses = ["unknown", "synthetic", "real_device"] as const;
export type EvidenceClass = typeof evidenceClasses[number];
export const completenessStates = ["source_range_verified", "partial", "unknown", "empty"] as const;
export type Completeness = typeof completenessStates[number];
export interface Source {
  source_id: SourceId; status: SourceStatus; account_id: string | null;
  client_version: string | null; tool_version: string | null; account_version: string | null;
  secret_configured: boolean; error_code: string | null;
}
export interface SourceConfiguration {
  source_id: SourceId; account_id: string; base_url: string;
  client_version: string; tool_version: string; account_version: string;
}
export const sectionLabels = {
  source_version: "工具、版本与许可", access_impact: "访问方式与客户端影响", scope_overread: "账号、目录与超范围读取",
  storage_cleanup: "秘密、缓存与清理", permissions_network: "权限与联网", action_stop: "本次操作与安全停止",
} as const;
export interface Disclosure {
  source_id: SourceId; tool: string; revision: string; hash: string; risks: string[];
  sections: Record<keyof typeof sectionLabels, string>; blockers: string[];
}
export const secretSectionLabels = { storage: "存储位置与保护", scope: "本次秘密作用域", cleanup: "保留与清理" } as const;
export const secretOperations = ["secret_set", "secret_get", "secret_delete"] as const;
export type SecretOperation = typeof secretOperations[number];
export interface SecretDisclosure {
  source_id: SourceId; tool: string; operation: SecretOperation; secret_backend: string; revision: string; hash: string; risks: string[];
  sections: Record<keyof typeof secretSectionLabels, string>;
}
export interface SetupOptions {
  approved_output_roots: { output_root_id: string; label: string }[];
  source_disclosures: Record<SourceId, Disclosure>;
}
export interface VersionArchitecture { version: string; architecture: string; installed: boolean }
export interface EnvironmentReport {
  os: {
    system: string; release: string; version: string; architecture: string; physical_architecture: string;
    process_architecture: string; rosetta_translated: boolean | null;
  };
  applications: Record<"qq" | "wechat" | "qce" | "tracememo", VersionArchitecture>;
  tools: { python3: VersionArchitecture };
  issues: { code: string; message: string }[];
}
export interface Selection {
  source_id: SourceId; account_id: string; conversation_selection_id: string;
  range_start: string; range_end: string; output_root_id: string;
}
export const conversationTypes = ["friend", "group", "user", "unknown"] as const;
export type ConversationType = typeof conversationTypes[number];
export interface Conversation { conversation_selection_id: string; display_name: string; account_id: string; conversation_type: ConversationType }
export interface Report {
  job_id: string; source_id: SourceId; state: VerificationState; evidence_class: EvidenceClass; count: number | null;
  client_version: string; tool_version: string; account_version: string;
  range_start: string; range_end: string; timezone: string;
  hash_ok: boolean | null; range_ok: boolean | null; sha256: string | null;
  completeness: Completeness | null; error_code: string | null;
}
export interface ExportSelection {
  source_id: SourceId; account_id: string; conversation_selection_ids: string[];
  range_start: string; range_end: string; output_root_id: string; display_timezone: string;
}
export interface ExportConsent {
  consent_id: string; scope_fingerprint: string; expires_at_utc: string;
}
export interface ExportJob {
  job_id: string; state: VerificationState; batch_id: string | null; snapshot_id: string | null;
  scope_fingerprint: string; known_total: number | null; completed_count: number | null;
  fetched_count: number | null; saved_count: number | null; duplicate_count: number | null;
  unknown_time_count: number | null; parse_error_count: number | null;
  manifest_sha256: string | null; completeness: Completeness | null; error_code: string | null;
  searchable: boolean; cancellation_requested: boolean; progress_percent: number | null; revision: number;
}
export interface ChatSearchRequest {
  source_id: ChatSourceId; account_id: string; query: string; conversation_selection_ids: string[];
  start_time: string; end_time: string; limit: number; cursor: string | null;
  include_undated?: boolean;
}
export interface ChatSearchResult {
  message_id: string; conversation_id: string; timestamp: string | null; sender_id: string | null;
  source_order?: number;
  sender_name: string | null; text: string | null; batch_id: string; snapshot_id: string;
}
export interface ChatSearchPage { results: ChatSearchResult[]; next_cursor: string | null }
export interface TopicExpansion { topic: string; terms: string[]; degraded: boolean }
export type SummaryType = "viewpoints" | "ideas" | "how_to" | "mixed";
export interface GenerationContext { messages: ChatSearchResult[]; added_count: number }
export const generationStates = ["pending", "checking_cache", "generating", "extracting", "merging", "validating", "complete", "failed", "cancelled"] as const;
export type GenerationState = typeof generationStates[number];
export interface GenerationSelectionRequest {
  source_id: ChatSourceId; account_id: string; conversation_selection_ids: string[]; snapshot_id: string;
  start_time: string; end_time: string; topic: string; mode: "fast" | "complete";
  excluded_message_ids: string[];
  generation_kind?: "guide" | "summary";
  summary_type?: SummaryType;
  included_message_ids?: string[];
  include_undated?: boolean;
}
export interface GenerationPreview {
  preview_id: string; scope_fingerprint: string; provider: string; model: string; mode: "fast" | "complete";
  thinking: "disabled" | "enabled"; reasoning_effort: "low" | "high" | "max" | null;
  message_count: number; character_count: number; estimated_input_tokens: number; max_output_tokens: number;
  provider_calls: number; price_status: "unknown"; estimated_cost: null; cache_hit: boolean;
  will_send_external: boolean; outbound_payloads: string[]; expires_at: string;
  synthesis_planned?: boolean;
}
export interface GenerationConsent {
  consent_id: string; scope_fingerprint: string; outbound_authorized: boolean; expires_at: string;
}
export interface HowToEvidenceItem { source_message_ids: string[] }
export interface HowToPreparation extends HowToEvidenceItem { text: string }
export interface HowToStep extends HowToEvidenceItem {
  instruction: string; location: string | null; parameters: string[]; conditions: string[];
  warnings: string[]; expected_result: string | null;
}
export interface HowToCheck extends HowToEvidenceItem { text: string }
export interface HowToConflict {
  description: string; claim_a: string; claim_a_source_message_ids: string[];
  claim_b: string; claim_b_source_message_ids: string[]; resolution: string | null;
  resolution_source_message_ids: string[];
}
export interface HowToResult {
  status: "complete" | "insufficient_evidence"; title: string; preparations: HowToPreparation[];
  steps: HowToStep[]; checks: HowToCheck[]; missing_information: string[]; conflicts: HowToConflict[];
  summary?: ChatSummary;
  professional_extensions?: { heading: string; text: string }[];
  user_text?: string;
}
export interface ChatSummaryItem extends HowToEvidenceItem { text: string }
export interface LegacyChatSummary {
  core_summary: ChatSummaryItem[]; discussions: ChatSummaryItem[]; decisions: ChatSummaryItem[];
  action_items: ChatSummaryItem[]; open_questions: ChatSummaryItem[];
}
export interface TopicSection extends HowToEvidenceItem { heading: string; text: string }
export interface TopicSummary { summary_type: Exclude<SummaryType, "how_to">; sections: TopicSection[] }
export type ChatSummary = LegacyChatSummary | TopicSummary;
export interface ValidatedEvidence {
  message_id: string; conversation_id: string | null; timestamp: string | null; sender_id: string | null;
  sender_name: string | null; quote: string;
  source_kind?: ContentUnitKind; source_batch_id?: string; image_id?: string;
}
export interface ValidatedGeneration {
  evidence_status: "verified"; schema_version?: 2; batch_id?: string; source_refs?: SourceRef[];
  snapshot_id: string; scope_fingerprint: string;
  source_extraction_ids: string[]; result: HowToResult; evidence: ValidatedEvidence[];
}
export interface GenerationJob {
  job_id: string; state: GenerationState; scope_fingerprint: string; completed_chunks: number;
  total_chunks: number; cancellation_requested: boolean; cache_hit: boolean;
  evidence_status: "unverified" | "verified"; error_code: string | null; revision: number;
  usage: GenerationUsage;
  result: ValidatedGeneration | null;
}
export interface GenerationUsage {
  request_count: number; completed_requests: number; failed_requests: number; cancelled_requests: number;
  unknown_usage_requests: number; known_prompt_tokens: number; known_completion_tokens: number;
  models: string[]; price_status: "unavailable"; price_source: null; price_version: null;
  price_observed_at: null; currency: null; estimated_cost: null; actual_cost: null;
  may_have_incurred_cost: boolean;
}
export type DocumentVersionOrigin = "ai_generated" | "user_edited" | "restored";
export interface DocumentVersion {
  document_id: string; version_id: string; ordinal: number; origin: DocumentVersionOrigin;
  evidence_status: "verified" | "unverified"; parent_version_id: string | null;
  restored_from_version_id: string | null; source_generation_job_id: string;
  change_summary: string; created_at: string; content: HowToResult;
}
export interface HowToDocument {
  source_deleted?: boolean;
  document_id: string; source_generation_job_id: string; source_snapshot_id: string | null; source_refs?: SourceRef[];
  created_at: string; updated_at: string; current_version: DocumentVersion;
  versions: DocumentVersion[]; source_evidence: ValidatedEvidence[];
}
export interface DocumentRecoveryPage { documents: HowToDocument[]; next_cursor: string | null }
export interface DocumentArtifact {
  artifact_id: string; document_id: string; version_id: string; format: "markdown" | "docx";
  source_mode: "body_only" | "with_sources"; filename: string; byte_size: number;
  sha256: string; created_at: string;
}
export const deletionCountKeys = [
  "documents", "versions", "document_artifacts", "generation_jobs", "generation_requests", "generation_consents",
  "generation_previews", "cache_entries", "export_batches", "export_jobs", "export_consents",
  "snapshots", "messages", "managed_files", "content_batches", "content_units", "content_images",
] as const;
export type DeletionCountKey = typeof deletionCountKeys[number];
export interface DeletionPlan {
  plan_id: string; target_kind: "document" | "batch" | "content_batch"; target_id: string;
  include_managed_files: boolean; preserve_documents?: boolean; fingerprint: string;
  counts: Record<DeletionCountKey, number>; effects: string[]; expires_at: string;
}
export interface DeletionResult {
  plan_id: string; state: "complete" | "cleanup_pending";
  deleted_counts: Record<DeletionCountKey, number>; managed_files_retained: boolean;
}
export type ImportOrigin = "imported_file" | "paste";
export type ImportPlatformHint = "generic" | "qq" | "wechat";
export interface ImportOptions {
  mapping?: Record<string, string>; timezone: string; default_date?: string | null;
  default_year?: number | null;
  default_conversation_id?: string | null; default_conversation_name?: string | null;
}
export interface ImportPreviewRequest {
  origin: ImportOrigin; platform_hint: ImportPlatformHint; filename?: string;
  payload_base64?: string; pasted_text?: string; options: ImportOptions;
}
export interface ImportIssue { row_number: number; code: string }
export interface ImportPreview {
  preview_id: string; preview_fingerprint: string; expires_at_utc: string; ready_to_commit: boolean;
  source: { format: "txt" | "csv" | "json" | "jsonl" | "xlsx"; encoding: string;
    container: "plain" | "zip" | "xlsx"; member_name: string | null; row_count: number;
    columns: string[]; sha256: string; byte_size: number; warnings: string[] };
  applied_mapping: Record<string, string>;
  analysis: { total_count: number; parsed_count: number; unresolved_count: number; duplicate_count: number;
    sender_count: number; time_start: string | null; time_end: string | null;
    message_types: { text: number; image: number; file: number; other: number; unknown: number };
    conversations: { conversation_id: string; conversation_name: string | null; message_count: number }[];
    warnings: string[]; issues: ImportIssue[];
    samples: { row_number: number; conversation_name: string | null; sender_name: string | null;
      timestamp: string | null; message_type: string; text: string | null }[] };
}
export interface ImportCommit {
  import_id: string; batch_id: string; snapshot_id: string; source_id: "manual"; account_id: string;
  range_start: string; range_end: string; display_timezone: string; warnings: string[];
  conversations: { conversation_selection_id: string; display_name: string | null; conversation_type: string | null }[];
  analysis?: ImportPreview["analysis"];
  imported_count?: number; skipped_count?: number; raw_expires_at_utc?: string;
}
export interface ImportRecoveryPage { imports: ImportCommit[]; next_cursor: string | null }

export type ContentUnitKind = "chat_message" | "note_passage";
export interface ContentUnitEdit {
  unit_id: string; kind: ContentUnitKind; group_id: string; text: string;
  sender_name: string | null; sent_at: string | null; order: number;
}
export interface ContentPreviewUnit extends ContentUnitEdit { image_ref: string | null }
export interface ContentPreviewImage { image_ref: string; filename: string; input_order: number; sha256: string }
export interface ContentPreview {
  preview_id: string; preview_fingerprint: string; suggested_kind: "chat" | "note" | "uncertain";
  status: "ready" | "empty_body" | "boundary_uncertain" | "ocr_unavailable";
  units: ContentPreviewUnit[]; images: ContentPreviewImage[]; warnings: string[]; expires_at_utc: string;
}
export interface ContentBatch {
  batch_id: string; unit_count: number; image_count: number;
  images: { image_id: string; input_order: number; sha256: string }[]; created_at_utc: string; raw_expires_at_utc?: string;
}
export interface ContentBatchPage { items: ContentBatch[]; next_cursor: string | null }
export interface ContentPreviewRequest {
  images?: { filename: string; payload_base64: string; input_order: number }[];
  pasted_note?: string;
}
export interface SourceRef { kind: "chat_snapshot" | "content_batch"; id: string }
export interface MixedSearchRequest {
  source_refs: SourceRef[]; include_undated: boolean;
  start_time?: string | null; end_time?: string | null; terms: string[];
}
export interface MixedSearchHit {
  unit_id: string; source_kind: ContentUnitKind; source_ref: SourceRef;
  text: string; sender_name: string | null; timestamp: string | null;
  source_order: number | null; image_id: string | null;
}
export interface MixedSearchPage { hits: MixedSearchHit[] }
export interface MixedGenerationRequest {
  source_refs: SourceRef[]; included_unit_ids: string[]; excluded_unit_ids: string[];
  topic: string; mode: "fast" | "complete"; summary_type: SummaryType;
  include_undated: boolean; start_time?: string | null; end_time?: string | null;
}
export function isTrustedRealReport(value: unknown, source: Source | undefined): boolean {
  if (!source || typeof value !== "object" || value === null) return false;
  const report = value as Record<string, unknown>;
  return source.status === "ready" && report.source_id === source.source_id && report.evidence_class === "real_device" &&
    report.state === "complete" && report.completeness === "source_range_verified" &&
    typeof report.job_id === "string" && !!report.job_id.trim() &&
    Number.isInteger(report.count) && Number(report.count) >= 0 &&
    typeof report.range_start === "string" && typeof report.range_end === "string" &&
    Number.isFinite(Date.parse(report.range_start)) && Date.parse(report.range_end) > Date.parse(report.range_start) &&
    typeof report.timezone === "string" && validTimezone(report.timezone) && report.error_code === null &&
    report.hash_ok === true && report.range_ok === true &&
    typeof report.sha256 === "string" && /^[a-f0-9]{64}$/.test(report.sha256) &&
    report.client_version === source.client_version && report.tool_version === source.tool_version &&
    report.account_version === source.account_version && !!source.client_version &&
    !!source.tool_version && !!source.account_version;
}
export function validTimezone(value: string): boolean {
  if (!value) return false;
  try { new Intl.DateTimeFormat("en", { timeZone: value }); return true; } catch { return false; }
}

export interface AgentPlanPreview {
  preview_id: string; preview_fingerprint: string; instruction: string; outbound_text: string;
  provider: string; model: string; expires_at_utc: string; max_output_tokens: number;
}
export interface AgentPlan {
  instruction: string; goal: string; keywords: string[]; summary_type: SummaryType;
  all_content: boolean; needs_clarification: boolean; notices: string[];
}
export interface DocumentTextPair {
  document_id: string; version_id: string;
  body: { filename: string; text: string }; reference: { filename: string; text: string };
}
