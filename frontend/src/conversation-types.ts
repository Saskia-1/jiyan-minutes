export interface AgentSource { kind: "chat_snapshot" | "content_batch"; id: string; label: string }
export interface AgentConversationSummary { conversation_id: string; title: string; revision: number; created_at: string; updated_at: string }
export interface AgentMessage { message_id: string; role: "user" | "assistant"; text: string; created_at: string; run_id: string | null; document_id: string | null; version_id: string | null; artifact_revision: number | null }
export interface AgentApproval { approval_id: string; kind: "agent" | "generation"; preview_fingerprint: string; provider: string; model: string; outbound_payloads: string[]; provider_calls: number; max_output_tokens: number; expires_at: string }
export interface AgentRun { run_id: string; revision: number; state: "waiting_approval" | "running" | "complete" | "cancelled" | "failed" | "needs_input"; stage: string; error_code: string | null; approval: AgentApproval | null; document_id: string | null; version_id: string | null; artifact_revision: number | null }
export interface AgentConversation extends AgentConversationSummary { sources: AgentSource[]; messages: AgentMessage[]; active_run: AgentRun | null }
