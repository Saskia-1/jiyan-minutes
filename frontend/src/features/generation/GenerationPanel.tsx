import { useEffect, useRef, useState } from "react";
import type { LocalApiClient } from "../../api/client";
import type { ChatSourceId, GenerationJob, GenerationPreview, GenerationSelectionRequest, SummaryType, ValidatedGeneration } from "../../types";
import { DocumentEditor } from "../documents/DocumentEditor";
import { EvidenceDrawer } from "../documents/EvidenceDrawer";
import { parseEditableText } from "../documents/documentText";

const TERMINAL = new Set(["complete", "failed", "cancelled"]);
const DIRECTION_LABELS: Record<SummaryType, string> = {
  viewpoints: "观点", ideas: "Idea / 想法", how_to: "方法论 / 怎么做", mixed: "综合总结",
};
const STATE_LABELS: Record<GenerationJob["state"], string> = {
  pending: "等待执行", checking_cache: "正在检查本地缓存", generating: "AI 正在生成",
  extracting: "正在校验输出结构", merging: "正在合并分块", validating: "正在核对本地证据",
  complete: "已完成", failed: "失败", cancelled: "已取消",
};

interface Props {
  api: LocalApiClient;
  sourceId: ChatSourceId;
  account: string;
  conversationSelectionIds: string[];
  conversationLabels: string[];
  excludedMessageIds: string[];
  snapshotId: string | null;
  rangeStart: string;
  rangeEnd: string;
  outputRootId?: string;
  disabled: boolean;
  onScopeLockChange?: (locked: boolean) => void;
  retrievalTopic?: string;
  includedMessageIds?: string[];
  compact?: boolean;
  autoPrepareRevision?: number;
  prepareLabel?: string;
  showPrepareAction?: boolean;
  direction?: SummaryType;
  prepareSelection?: () => Promise<GenerationSelectionRequest>;
  preparePreview?: () => Promise<GenerationPreview>;
  scopeRevision?: string;
  editRequested?: boolean;
  onEditRequestedChange?: (value: boolean) => void;
  onComplete?: (jobId: string) => void;
}

function safeDate(value: string | null) {
  if (value === null) return "时间未知";
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? new Date(parsed).toLocaleString("zh-CN") : value;
}

export function ResultView({ value, verified = true }: { value: Pick<ValidatedGeneration, "result" | "evidence">; verified?: boolean }) {
  const result = value.result;
  const topicSummary = result.summary && "sections" in result.summary ? result.summary : null;
  const legacySummary = result.summary && "core_summary" in result.summary ? result.summary : null;
  const edited = result.user_text != null ? parseEditableText(result.user_text, result.title) : null;
  const summarySections = legacySummary ? ([
    ["core_summary", "核心摘要"], ["discussions", "主要信息与讨论"],
    ["decisions", "结论与决定"], ["action_items", "待办事项"],
    ["open_questions", "尚未解决的问题"],
  ] as const) : [];
  return <section className="generation-result" aria-label="AI 生成结果">
    {verified && <p className="verified-badge">AI 生成 · 本地证据已验证</p>}
    <h4>{edited?.title || result.title}</h4>
    {edited ? <div className="edited-document">{edited.blocks.map((block, index) => block.kind === "heading"
      ? <h5 key={`${block.kind}-${index}`}>{block.text}</h5>
      : <p className="topic-prose" key={`${block.kind}-${index}`}>{block.text}</p>)}</div>
      : result.status === "insufficient_evidence" ? <p>选定范围内未找到足够依据；未生成推测内容。</p> : topicSummary ? <>
      {topicSummary.sections.map((section, index) => <section key={index} className="topic-section">
        <h5>{section.heading}</h5><p className="topic-prose">{section.text}</p>
        {topicSummary.summary_type !== "ideas" &&
          <EvidenceDrawer label="查看原始依据" evidence={value.evidence.filter(item => section.source_message_ids.includes(item.message_id))} />}
      </section>)}
    </> : legacySummary ? <>
      {summarySections.map(([field, label]) => !!legacySummary[field].length &&
        <section key={field}><h5>{label}</h5><ul>{legacySummary[field].map((item, index) =>
          <li key={`${field}-${index}`}><p>{item.text}</p>
            <EvidenceDrawer evidence={value.evidence.filter(evidence => item.source_message_ids.includes(evidence.message_id))} />
          </li>)}</ul></section>)}
    </> : <>
      {!!result.preparations.length && <section><h5>准备</h5><ul>{result.preparations.map((item, index) =>
        <li key={`${item.text}-${index}`}>{item.text}</li>)}</ul></section>}
      <section><h5>步骤</h5><ol>{result.steps.map((step, index) => <li key={`${step.instruction}-${index}`}>
        <strong>{step.instruction}</strong>
        {step.location && <p>位置：{step.location}</p>}
        {!!step.parameters.length && <p>参数：{step.parameters.join("、")}</p>}
        {!!step.conditions.length && <p>条件：{step.conditions.join("、")}</p>}
        {!!step.warnings.length && <p className="warning">注意：{step.warnings.join("、")}</p>}
        {step.expected_result && <p>预期结果：{step.expected_result}</p>}
        <EvidenceDrawer label="查看原始依据" evidence={value.evidence.filter(item => step.source_message_ids.includes(item.message_id))} />
      </li>)}</ol></section>
      {!!result.checks.length && <section><h5>检查</h5><ul>{result.checks.map((item, index) =>
        <li key={`${item.text}-${index}`}>{item.text}</li>)}</ul></section>}
      {!!result.missing_information.length && <section><h5>仍缺信息</h5><ul>{result.missing_information.map(item => <li key={item}>{item}</li>)}</ul></section>}
      {!!result.conflicts.length && <section><h5>冲突</h5>{result.conflicts.map((item, index) => <article key={`${item.description}-${index}`}>
        <strong>{item.description}</strong><p>{item.claim_a}</p><p>{item.claim_b}</p>
        <p>{item.resolution ? `已解析：${item.resolution}` : "尚未解析"}</p>
      </article>)}</section>}
    </>}
    {result.user_text == null && result.professional_extensions?.map((item, index) => <section className="professional-extension" key={index}>
      <h5>专业补充 · {item.heading}</h5><p className="muted">AI 提供的延伸解释，不代表原聊天。</p><p className="topic-prose">{item.text}</p>
    </section>)}
    <details><summary>查看原始依据（{value.evidence.length} 条）</summary>
      <section aria-label="已验证证据" className="validated-evidence"><ol>{value.evidence.map(item => <li key={item.message_id}>
        <div className="result-meta"><strong>{item.source_kind === "note_passage" ? "笔记／网页正文" : item.sender_name || item.sender_id || "未知发送者"}</strong>
          <time dateTime={item.timestamp || undefined}>{safeDate(item.timestamp)}</time></div>
        <p>{item.quote}</p>
      </li>)}</ol></section>
    </details>
  </section>;
}

export function GenerationPanel(props: Props) {
  const rawRevision = useRef(0);
  const preparedRevision = useRef<number | undefined>(undefined);
  const [topic, setTopic] = useState("");
  const [mode, setMode] = useState<"fast" | "complete">("complete");
  const [summaryType, setSummaryType] = useState<SummaryType>("how_to");
  const hasRetrievedScope = props.retrievalTopic !== undefined;
  const topicValue = hasRetrievedScope ? props.retrievalTopic || "" : topic;
  const generationMode = hasRetrievedScope ? "complete" : mode;
  const [preview, setPreview] = useState<GenerationPreview | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [job, setJob] = useState<GenerationJob | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const alive = useRef(true);
  const scopeKey = JSON.stringify([
    props.sourceId, props.account, props.snapshotId, props.rangeStart, props.rangeEnd,
    props.conversationSelectionIds, props.excludedMessageIds, topicValue.trim(), generationMode,
    props.direction || summaryType, props.includedMessageIds,
    props.scopeRevision,
  ]);
  const currentScopeKey = useRef(scopeKey);
  const scopeLocked = busy || (!!job && !TERMINAL.has(job.state));

  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => {
    const clear = () => {
      rawRevision.current += 1; setPreview(null); setConfirmed(false);
      setJob(value => value?.state === "complete" && value.result
        ? { ...value, result: { ...value.result, evidence: [] } } : null);
    };
    window.addEventListener("minutes-raw-cleared", clear);
    return () => window.removeEventListener("minutes-raw-cleared", clear);
  }, []);
  useEffect(() => { if (job?.state === "complete") props.onComplete?.(job.job_id); }, [job?.job_id, job?.state, props.onComplete]);
  useEffect(() => {
    props.onScopeLockChange?.(scopeLocked);
    return () => { if (scopeLocked) props.onScopeLockChange?.(false); };
  }, [props.onScopeLockChange, scopeLocked]);
  useEffect(() => {
    currentScopeKey.current = scopeKey;
    setPreview(null); setConfirmed(false); setJob(null); setError("");
  }, [scopeKey]);

  useEffect(() => {
    if (props.autoPrepareRevision === undefined || preparedRevision.current === props.autoPrepareRevision || scopeLocked || props.disabled) return;
    preparedRevision.current = props.autoPrepareRevision;
    void quickPreview();
  }, [props.autoPrepareRevision, props.disabled, scopeLocked, scopeKey]);

  function resetAuthorization() {
    setPreview(null); setConfirmed(false); setJob(null); setError("");
  }

  async function poll(jobId: string) {
    const requestedScope = currentScopeKey.current;
    const revision = rawRevision.current;
    for (let attempt = 0; alive.current; attempt++) {
      const value = await props.api.generationJob(jobId);
      if (!alive.current || currentScopeKey.current !== requestedScope || revision !== rawRevision.current) return value;
      setJob(value);
      if (TERMINAL.has(value.state)) return value;
      await new Promise(resolve => setTimeout(resolve, Math.min(4_000, 300 * (2 ** attempt))));
    }
  }

  async function quickPreview() {
    if ((!props.prepareSelection && !props.preparePreview) || scopeLocked || props.disabled) return;
    const revision = rawRevision.current;
    const requestedScope = scopeKey;
    setBusy(true); setError(""); setPreview(null); setJob(null); setConfirmed(false);
    try {
      const value = props.preparePreview
        ? await props.preparePreview()
        : await props.api.previewGeneration(await props.prepareSelection!());
      if (Date.parse(value.expires_at) <= Date.now()) throw new Error("preview_expired");
      if (alive.current && currentScopeKey.current === requestedScope && revision === rawRevision.current) setPreview(value);
    } catch {
      if (alive.current) setError("无法建立 AI 外发预览；没有发送正文。请检查所选范围后重试。");
    } finally { if (alive.current) setBusy(false); }
  }

  async function createPreview() {
    if (!props.snapshotId || !topicValue.trim() || !props.conversationSelectionIds.length || busy || props.disabled ||
        (hasRetrievedScope && !props.includedMessageIds?.length)) return;
    const revision = rawRevision.current;
    const requestedScopeKey = scopeKey;
    setBusy(true); setError(""); setPreview(null); setConfirmed(false); setJob(null);
    try {
      const value = await props.api.previewGeneration({
        source_id: props.sourceId, account_id: props.account,
        conversation_selection_ids: props.conversationSelectionIds,
        snapshot_id: props.snapshotId, start_time: props.rangeStart, end_time: props.rangeEnd,
        topic: topicValue.trim(), mode: generationMode,
        summary_type: summaryType,
        excluded_message_ids: props.excludedMessageIds,
        ...(hasRetrievedScope ? {
          included_message_ids: props.includedMessageIds || [],
        } : {}),
      });
      if (Date.parse(value.expires_at) <= Date.now()) throw new Error("preview_expired");
      if (alive.current && currentScopeKey.current === requestedScopeKey && revision === rawRevision.current) setPreview(value);
    } catch { if (alive.current) setError("无法建立 AI 外发预览；没有发送聊天内容。"); }
    finally { if (alive.current) setBusy(false); }
  }

  async function begin() {
    if (!preview || !confirmed || busy || props.disabled) return;
    const revision = rawRevision.current;
    const authorizedScopeKey = scopeKey;
    setBusy(true); setError("");
    try {
      const consent = await props.api.authorizeGeneration(preview.preview_id);
      if (Date.parse(consent.expires_at) <= Date.now() || consent.scope_fingerprint !== preview.scope_fingerprint ||
          consent.outbound_authorized !== preview.will_send_external) throw new Error("consent_mismatch");
      if (!alive.current || currentScopeKey.current !== authorizedScopeKey || revision !== rawRevision.current) return;
      const started = await props.api.startGeneration(consent.consent_id, `ui-${crypto.randomUUID()}`);
      if (!alive.current || currentScopeKey.current !== authorizedScopeKey || revision !== rawRevision.current) return;
      setJob(started); setConfirmed(false); setBusy(false);
      if (!TERMINAL.has(started.state)) await poll(started.job_id);
    } catch { if (alive.current) setError("生成未完成；聊天范围不会自动扩大或改用其他服务。保持当前页面可重试。"); }
    finally { if (alive.current) setBusy(false); }
  }

  async function cancel() {
    if (!job || TERMINAL.has(job.state) || busy) return;
    setBusy(true); setError("");
    const revision = rawRevision.current;
    const requestedScope = currentScopeKey.current;
    try {
      const value = await props.api.cancelGeneration(job.job_id);
      if (alive.current && revision === rawRevision.current && requestedScope === currentScopeKey.current) setJob(value);
    }
    catch { if (alive.current) setError("取消状态未能确认；不会自动重新发送。" ); }
    finally { if (alive.current) setBusy(false); }
  }

  async function retry() {
    if (!job || job.state !== "failed" || busy) return;
    const revision = rawRevision.current;
    setBusy(true); setError("");
    try {
      const value = await props.api.retryGeneration(job.job_id);
      if (!alive.current || revision !== rawRevision.current) return;
      setJob(value); setBusy(false);
      if (!TERMINAL.has(value.state)) await poll(value.job_id);
    } catch { if (alive.current) setError("重试未完成；冻结的聊天范围没有改变。" ); }
    finally { if (alive.current) setBusy(false); }
  }

  const canPreview = !!props.snapshotId && !!topicValue.trim() && !!props.conversationSelectionIds.length && !busy && !props.disabled &&
    (!hasRetrievedScope || !!props.includedMessageIds?.length);
  const failedEvidence = job?.state === "failed" && ["evidence_invalid", "extraction_invalid", "extraction_incomplete", "provider_malformed"].includes(job.error_code || "");
  const outboundPreview = preview && <section className="outbound-preview" aria-label="AI 外发预览">
    <h4>等待授权 · 外发与预算确认</h4>
    {preview.synthesis_planned && <p>预算包含一次跨段整合：先核对各段引用，再用本次原消息和中间提炼结果组织完整文档。中间结果在生成后产生，发送范围不会增加。</p>}
    {preview.cache_hit ? <p>已命中同一冻结范围的本地已验证缓存，本次预计不会外发。</p> : preview.will_send_external ?
      <p>将向 {preview.provider}（{preview.model}；思考模式：{preview.thinking === "enabled" ? `开启，强度 ${preview.reasoning_effort}` : "关闭"}）发送 {preview.message_count} 条纯文本；不发送账号凭据、附件文件或数据库。</p> :
      <p>范围内没有可发送的文本，将在本地返回依据不足。</p>}
    <p>字符数：{preview.character_count} · 最多 {preview.provider_calls} 次调用 · 预计输入不超过 {preview.estimated_input_tokens} token · 总输出上限 {preview.max_output_tokens} token</p>
    <p>费用：未知（以 Provider 实际账单为准） · 授权有效期：<time dateTime={preview.expires_at}>{safeDate(preview.expires_at)}</time></p>
    {preview.outbound_payloads.length > 0 && <details open><summary>查看将要外发的完整脱敏文本（{preview.outbound_payloads.length} 个分块）</summary>
      <p>下方是确认后将作为 user content 发送的逐字节 UTF-8 文本。已处理明确标注的手机号、邮箱、QQ/微信号和秘密样式；自由文本不能保证识别所有个人信息，请人工核对。</p>
      {preview.outbound_payloads.map((payload, index) => <section key={index} aria-label={`外发分块 ${index + 1}`}><h5>分块 {index + 1}</h5><pre>{payload}</pre></section>)}
    </details>}
    <label className="check"><input type="checkbox" checked={confirmed} disabled={busy}
      onChange={event => setConfirmed(event.target.checked)} />{preview.will_send_external ?
        "授权本次向外部 AI 发送上述冻结范围纯文本" : "确认使用上述本地范围生成结果"}</label>
    <div className="actions"><button type="button" disabled={busy || (!!job && !TERMINAL.has(job.state))} onClick={resetAuthorization}>取消本次整理</button><button type="button" disabled={!confirmed || busy} onClick={() => void begin()}>{busy ? "正在生成…" : "确认并生成"}</button>
      {job && !TERMINAL.has(job.state) && <button type="button" disabled={busy} onClick={() => void cancel()}>取消生成</button>}</div>
  </section>;
  if (props.compact) return <>
    <div className="actions">{props.showPrepareAction !== false && <button type="button" disabled={props.disabled || scopeLocked || !topicValue.trim()}
      onClick={() => void quickPreview()}>{scopeLocked ? "正在整理…" : job?.state === "complete" ? "再次整理" : props.prepareLabel || (job?.state === "failed" || error ? "重新整理" : "开始整理")}</button>}
      {job && !TERMINAL.has(job.state) && <button type="button" disabled={busy} onClick={() => void cancel()}>取消整理</button>}
    </div>
    <p className="muted">先预览冻结范围与预算，确认后才会向已配置的 AI 服务发送所选正文。</p>
    {outboundPreview}
    {job && <><p role="status">生成状态：{STATE_LABELS[job.state]} · 分块 {job.completed_chunks}/{job.total_chunks}{job.cache_hit ? " · 本地缓存命中" : ""}</p>
      <p>AI 用量：{job.usage.request_count} 次请求 · 已知输入 {job.usage.known_prompt_tokens} token · 已知输出 {job.usage.known_completion_tokens} token
        {job.usage.unknown_usage_requests ? ` · ${job.usage.unknown_usage_requests} 次用量未知` : ""}</p></>}
    {job?.state === "failed" && <p role="alert">生成失败，{failedEvidence ? "返回内容未通过校验" : "AI 服务暂时未完成请求"}。可以重新整理。</p>}
    {job?.state === "cancelled" && <p role="status">已取消，可以重新整理。</p>}
    {error && <p role="alert">{error}</p>}
    {error && job && !TERMINAL.has(job.state) && <button type="button" onClick={() => { setError(""); void poll(job.job_id).catch(() => setError("暂时无法读取结果，请重试查询或取消。")); }}>查询结果</button>}
    {!props.onComplete && job?.state === "complete" && job.result && <DocumentEditor key={job.job_id} compact api={props.api}
      generationJobId={job.job_id} outputRootId={props.outputRootId}
      editRequested={props.editRequested} onEditRequestedChange={props.onEditRequestedChange} />}
  </>;
  return <section className="generation-workspace" aria-label="AI 主题整理">
    <div className="search-divider" />
    <h3>生成主题文档</h3>
    <p>先建立本地范围与预算预览；只有确认后，冻结范围内的纯文本才可能发送给所示 AI Provider。</p>
    {!props.snapshotId && <p className="warning">请先完成导出与本地导入，取得可搜索的 immutable snapshot。</p>}
    {!hasRetrievedScope && <><label>操作主题<input type="text" maxLength={512} value={topic} disabled={props.disabled || scopeLocked}
      onChange={event => { setTopic(event.target.value); resetAuthorization(); }} /></label>
    <label>生成模式<select value={mode} disabled={props.disabled || busy} onChange={event => { setMode(event.target.value as "fast" | "complete"); resetAuthorization(); }}>
      <option value="complete">完整范围</option><option value="fast">主题字面匹配（快速）</option></select></label></>}
    {hasRetrievedScope && <p>主题：{topicValue || "未填写"} · 候选与上下文共 {props.includedMessageIds?.length || 0} 条消息；排除项不会用于生成。</p>}
    <div className="scope-summary"><p>会话：{props.conversationLabels.length ? props.conversationLabels.join("、") : "未选择"}</p>
      <p>时间：{safeDate(props.rangeStart)} 至 {safeDate(props.rangeEnd)}（结束不包含）</p>
      <p>排除：{props.excludedMessageIds.length} 条消息</p></div>
    <label>你希望从这些聊天中总结什么？<select value={summaryType} disabled={props.disabled || scopeLocked}
      onChange={event => setSummaryType(event.target.value as SummaryType)}>
      {(Object.keys(DIRECTION_LABELS) as SummaryType[]).map(type => <option key={type} value={type}>{DIRECTION_LABELS[type]}</option>)}
    </select></label>
    <div className="actions"><button type="button" disabled={!canPreview} onClick={() => void createPreview()}>{busy && !preview ? "正在建立预览…" : "预览 AI 外发"}</button></div>
    {outboundPreview}
    {job && <><p role="status">生成状态：{STATE_LABELS[job.state]} · 分块 {job.completed_chunks}/{job.total_chunks}{job.cache_hit ? " · 本地缓存命中" : ""}</p>
      <p>AI 用量：{job.usage.request_count} 次请求 · 已知输入 {job.usage.known_prompt_tokens} token · 已知输出 {job.usage.known_completion_tokens} token
        {job.usage.unknown_usage_requests ? ` · ${job.usage.unknown_usage_requests} 次用量未知` : ""} · 费用无法估算（未配置价格来源/版本/币种）</p></>}
    {job?.state === "failed" && <p role="alert">{failedEvidence ? "AI 结果未通过本地证据验证，已标记为未验证且不会作为指南展示。" : `生成失败（${job.error_code || "unknown"}），未验证结果不会展示。`}</p>}
    {job?.state === "cancelled" && <p>生成已取消；没有未验证结果被展示。{job.usage.may_have_incurred_cost ? "已发出的请求仍可能已产生费用。" : "未记录外发请求。"}</p>}
    {job?.state === "failed" && <button type="button" disabled={busy} onClick={() => void retry()}>重试生成</button>}
    {error && <p role="alert">{error}</p>}
    {job?.state === "complete" && job.evidence_status === "verified" && job.result && <>
      <ResultView value={job.result} />
      <DocumentEditor key={job.job_id} api={props.api} generationJobId={job.job_id} outputRootId={props.outputRootId} />
    </>}
  </section>;
}
