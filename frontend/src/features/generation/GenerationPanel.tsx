import { useEffect, useRef, useState } from "react";
import type { LocalApiClient } from "../../api/client";
import type { ChatSourceId, GenerationJob, GenerationPreview, ValidatedGeneration } from "../../types";
import { DocumentEditor } from "../documents/DocumentEditor";

const TERMINAL = new Set(["complete", "failed", "cancelled"]);
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
}

function safeDate(value: string) {
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? new Date(parsed).toLocaleString("zh-CN") : value;
}

function ResultView({ value }: { value: ValidatedGeneration }) {
  const result = value.result;
  return <section className="generation-result" aria-label="AI 生成结果">
    <p className="verified-badge">AI 生成 · 本地证据已验证</p>
    <h4>{result.title}</h4>
    {result.status === "insufficient_evidence" ? <p>选定范围内未找到足够依据；未生成推测性步骤。</p> : <>
      {!!result.preparations.length && <section><h5>准备</h5><ul>{result.preparations.map((item, index) =>
        <li key={`${item.text}-${index}`}>{item.text}</li>)}</ul></section>}
      <section><h5>步骤</h5><ol>{result.steps.map((step, index) => <li key={`${step.instruction}-${index}`}>
        <strong>{step.instruction}</strong>
        {step.location && <p>位置：{step.location}</p>}
        {!!step.parameters.length && <p>参数：{step.parameters.join("、")}</p>}
        {!!step.conditions.length && <p>条件：{step.conditions.join("、")}</p>}
        {!!step.warnings.length && <p className="warning">注意：{step.warnings.join("、")}</p>}
        {step.expected_result && <p>预期结果：{step.expected_result}</p>}
        <small>证据引用：{step.source_message_ids.length} 条</small>
      </li>)}</ol></section>
      {!!result.checks.length && <section><h5>检查</h5><ul>{result.checks.map((item, index) =>
        <li key={`${item.text}-${index}`}>{item.text}</li>)}</ul></section>}
      {!!result.missing_information.length && <section><h5>仍缺信息</h5><ul>{result.missing_information.map(item => <li key={item}>{item}</li>)}</ul></section>}
      {!!result.conflicts.length && <section><h5>冲突</h5>{result.conflicts.map((item, index) => <article key={`${item.description}-${index}`}>
        <strong>{item.description}</strong><p>{item.claim_a}</p><p>{item.claim_b}</p>
        <p>{item.resolution ? `已解析：${item.resolution}` : "尚未解析"}</p>
      </article>)}</section>}
    </>}
    <details><summary>查看已验证证据（{value.evidence.length} 条）</summary>
      <section aria-label="已验证证据" className="validated-evidence"><ol>{value.evidence.map(item => <li key={item.message_id}>
        <div className="result-meta"><strong>{item.sender_name || item.sender_id || "未知发送者"}</strong>
          <time dateTime={item.timestamp}>{safeDate(item.timestamp)}</time></div>
        <p>{item.quote}</p><small>{item.conversation_id} · {item.message_id}</small>
      </li>)}</ol></section>
    </details>
  </section>;
}

export function GenerationPanel(props: Props) {
  const [topic, setTopic] = useState("");
  const [mode, setMode] = useState<"fast" | "complete">("complete");
  const [preview, setPreview] = useState<GenerationPreview | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [job, setJob] = useState<GenerationJob | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const alive = useRef(true);
  const scopeKey = JSON.stringify([
    props.sourceId, props.account, props.snapshotId, props.rangeStart, props.rangeEnd,
    props.conversationSelectionIds, props.excludedMessageIds, topic.trim(), mode,
  ]);
  const currentScopeKey = useRef(scopeKey);
  const scopeLocked = busy || (!!job && !TERMINAL.has(job.state));

  useEffect(() => () => { alive.current = false; }, []);
  useEffect(() => {
    props.onScopeLockChange?.(scopeLocked);
    return () => { if (scopeLocked) props.onScopeLockChange?.(false); };
  }, [props.onScopeLockChange, scopeLocked]);
  useEffect(() => {
    currentScopeKey.current = scopeKey;
    setPreview(null); setConfirmed(false); setJob(null); setError("");
  }, [scopeKey]);

  function resetAuthorization() {
    setPreview(null); setConfirmed(false); setJob(null); setError("");
  }

  async function poll(jobId: string) {
    for (let attempt = 0; attempt < 10; attempt++) {
      const value = await props.api.generationJob(jobId);
      if (!alive.current) return value;
      setJob(value);
      if (TERMINAL.has(value.state)) return value;
      await new Promise(resolve => setTimeout(resolve, Math.min(4_000, 300 * (2 ** attempt))));
    }
    throw new Error("generation_polling_paused");
  }

  async function createPreview() {
    if (!props.snapshotId || !topic.trim() || !props.conversationSelectionIds.length || busy || props.disabled) return;
    const requestedScopeKey = scopeKey;
    setBusy(true); setError(""); setPreview(null); setConfirmed(false); setJob(null);
    try {
      const value = await props.api.previewGeneration({
        source_id: props.sourceId, account_id: props.account,
        conversation_selection_ids: props.conversationSelectionIds,
        snapshot_id: props.snapshotId, start_time: props.rangeStart, end_time: props.rangeEnd,
        topic: topic.trim(), mode,
        excluded_message_ids: props.excludedMessageIds,
      });
      if (Date.parse(value.expires_at) <= Date.now()) throw new Error("preview_expired");
      if (alive.current && currentScopeKey.current === requestedScopeKey) setPreview(value);
    } catch { if (alive.current) setError("无法建立 AI 外发预览；没有发送聊天内容。"); }
    finally { if (alive.current) setBusy(false); }
  }

  async function begin() {
    if (!preview || !confirmed || busy || props.disabled) return;
    const authorizedScopeKey = scopeKey;
    setBusy(true); setError("");
    try {
      const consent = await props.api.authorizeGeneration(preview.preview_id);
      if (Date.parse(consent.expires_at) <= Date.now() || consent.scope_fingerprint !== preview.scope_fingerprint ||
          consent.outbound_authorized !== preview.will_send_external) throw new Error("consent_mismatch");
      if (!alive.current || currentScopeKey.current !== authorizedScopeKey) return;
      const started = await props.api.startGeneration(consent.consent_id, `ui-${crypto.randomUUID()}`);
      if (!alive.current) return;
      setJob(started); setConfirmed(false); setBusy(false);
      if (!TERMINAL.has(started.state)) await poll(started.job_id);
    } catch { if (alive.current) setError("生成未完成；聊天范围不会自动扩大或改用其他服务。保持当前页面可重试。"); }
    finally { if (alive.current) setBusy(false); }
  }

  async function cancel() {
    if (!job || TERMINAL.has(job.state) || busy) return;
    setBusy(true); setError("");
    try { if (alive.current) setJob(await props.api.cancelGeneration(job.job_id)); }
    catch { if (alive.current) setError("取消状态未能确认；不会自动重新发送。" ); }
    finally { if (alive.current) setBusy(false); }
  }

  async function retry() {
    if (!job || job.state !== "failed" || busy) return;
    setBusy(true); setError("");
    try {
      const value = await props.api.retryGeneration(job.job_id);
      if (!alive.current) return;
      setJob(value); setBusy(false);
      if (!TERMINAL.has(value.state)) await poll(value.job_id);
    } catch { if (alive.current) setError("重试未完成；冻结的聊天范围没有改变。" ); }
    finally { if (alive.current) setBusy(false); }
  }

  const canPreview = !!props.snapshotId && !!topic.trim() && !!props.conversationSelectionIds.length && !busy && !props.disabled;
  const failedEvidence = job?.state === "failed" && ["evidence_invalid", "extraction_invalid", "extraction_incomplete", "provider_malformed"].includes(job.error_code || "");
  return <section className="generation-workspace" aria-label="AI 操作指南生成">
    <div className="search-divider" />
    <h3>生成操作指南</h3>
    <p>先建立本地范围与预算预览；只有确认后，冻结范围内的纯文本才可能发送给所示 AI Provider。</p>
    {!props.snapshotId && <p className="warning">请先完成导出与本地导入，取得可搜索的 immutable snapshot。</p>}
    <label>操作主题<input type="text" maxLength={512} value={topic} disabled={props.disabled || busy}
      onChange={event => { setTopic(event.target.value); resetAuthorization(); }} /></label>
    <label>生成模式<select value={mode} disabled={props.disabled || busy} onChange={event => { setMode(event.target.value as "fast" | "complete"); resetAuthorization(); }}>
      <option value="complete">完整范围</option><option value="fast">主题字面匹配（快速）</option></select></label>
    <div className="scope-summary"><p>会话：{props.conversationLabels.length ? props.conversationLabels.join("、") : "未选择"}</p>
      <p>时间：{safeDate(props.rangeStart)} 至 {safeDate(props.rangeEnd)}（结束不包含）</p>
      <p>排除：{props.excludedMessageIds.length} 条消息</p></div>
    <div className="actions"><button type="button" disabled={!canPreview} onClick={() => void createPreview()}>{busy && !preview ? "正在建立预览…" : "预览 AI 外发"}</button></div>
    {preview && <section className="outbound-preview" aria-label="AI 外发预览">
      <h4>外发与预算确认</h4>
      {preview.cache_hit ? <p>已命中同一 snapshot 与完整 scope 的本地 verified 缓存，本次预计不会外发。</p> : preview.will_send_external ?
        <p>将向 {preview.provider}（{preview.model}）发送 {preview.message_count} 条纯文本；不发送账号凭据、附件文件或数据库。</p> :
        <p>范围内没有可发送的文本，将在本地返回依据不足。</p>}
      <p>字符数：{preview.character_count} · 最多 {preview.provider_calls} 次调用 · 预计输入不超过 {preview.estimated_input_tokens} token · 总输出上限 {preview.max_output_tokens} token</p>
      <p>费用：未知（以 Provider 实际账单为准） · 授权有效期：<time dateTime={preview.expires_at}>{safeDate(preview.expires_at)}</time></p>
      {preview.outbound_payloads.length > 0 && <details><summary>查看将要外发的完整脱敏文本（{preview.outbound_payloads.length} 个分块）</summary>
        <p>下方是确认后将作为 user content 发送的逐字节 UTF-8 文本。已处理明确标注的手机号、邮箱、QQ/微信号和秘密样式；自由文本不能保证识别所有个人信息，请人工核对。</p>
        {preview.outbound_payloads.map((payload, index) => <section key={index} aria-label={`外发分块 ${index + 1}`}><h5>分块 {index + 1}</h5><pre>{payload}</pre></section>)}
      </details>}
      <label className="check"><input type="checkbox" checked={confirmed} disabled={busy}
        onChange={event => setConfirmed(event.target.checked)} />{preview.will_send_external ?
          "授权本次向外部 AI 发送上述冻结范围纯文本" : "确认使用上述本地范围生成结果"}</label>
      <div className="actions"><button type="button" disabled={!confirmed || busy} onClick={() => void begin()}>{busy ? "正在生成…" : "确认并生成"}</button>
        {job && !TERMINAL.has(job.state) && <button type="button" disabled={busy} onClick={() => void cancel()}>取消生成</button>}</div>
    </section>}
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
