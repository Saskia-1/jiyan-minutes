import { useCallback, useEffect, useRef, useState } from "react";
import type { LocalApiClient } from "../../api/client";
import type { ChatSearchRequest, ChatSearchResult, ChatSourceId, Conversation, ExportJob } from "../../types";
import { GenerationPanel } from "../generation/GenerationPanel";
import { DeletionControl } from "../data/DeletionControl";
import { recallTopicMessages } from "./topicRecall";

const TERMINAL = new Set(["complete", "partial", "failed", "cancelled"]);
const MAX_EXCLUDED_MESSAGES = 2_000;
const MAX_SELECTED_CONVERSATIONS = 50;
const STATE_LABEL: Record<ExportJob["state"], string> = {
  pending_authorization: "等待授权", connecting: "正在连接", reading: "正在读取", saving: "正在保存",
  verifying: "正在校验", indexing: "正在建立索引", complete: "已完成", partial: "部分完成",
  failed: "失败", cancelled: "已取消",
};

interface Props {
  api: LocalApiClient;
  sourceId: ChatSourceId;
  account: string;
  conversations: Conversation[];
  rangeStart: string;
  rangeEnd: string;
  timezone: string;
  outputRootId: string;
  disabled: boolean;
  secretConfigured: boolean;
  secretGrantReady: boolean;
  onSecretGrantConsumed: () => void;
  onGenerationScopeLockChange?: (locked: boolean) => void;
  hideExport?: boolean;
}

interface WallTimeParts {
  year: number; month: number; day: number; hour: number; minute: number; second: number;
}
const wallTimeFormatters = new Map<string, Intl.DateTimeFormat>();

function wallTimeParts(instantMs: number, timezone: string): WallTimeParts | null {
  try {
    let formatter = wallTimeFormatters.get(timezone);
    if (!formatter) {
      formatter = new Intl.DateTimeFormat("en-CA-u-ca-iso8601", {
        timeZone: timezone, year: "numeric", month: "2-digit", day: "2-digit",
        hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23",
      });
      wallTimeFormatters.set(timezone, formatter);
    }
    const values = Object.fromEntries(
      formatter.formatToParts(new Date(instantMs))
        .filter(part => part.type !== "literal")
        .map(part => [part.type, Number(part.value)]),
    );
    if (["year", "month", "day", "hour", "minute", "second"].some(key => !Number.isInteger(values[key]))) return null;
    return values as unknown as WallTimeParts;
  } catch {
    return null;
  }
}
function wallTimeValue(parts: WallTimeParts) {
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${String(parts.year).padStart(4, "0")}-${pad(parts.month)}-${pad(parts.day)}T${pad(parts.hour)}:${pad(parts.minute)}:${pad(parts.second)}`;
}
function localInput(instant: string, timezone: string) {
  const parsed = Date.parse(instant);
  if (!Number.isFinite(parsed)) return "";
  const parts = wallTimeParts(parsed, timezone);
  return parts ? wallTimeValue(parts) : "";
}
export function utcInput(value: string, timezone: string) {
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$/.exec(value);
  if (!match) return "";
  const desired: WallTimeParts = {
    year: Number(match[1]), month: Number(match[2]), day: Number(match[3]),
    hour: Number(match[4]), minute: Number(match[5]), second: Number(match[6] || "0"),
  };
  const nominal = Date.UTC(
    desired.year, desired.month - 1, desired.day,
    desired.hour, desired.minute, desired.second,
  );
  const normalizedDate = new Date(nominal);
  if (
    normalizedDate.getUTCFullYear() !== desired.year
    || normalizedDate.getUTCMonth() + 1 !== desired.month
    || normalizedDate.getUTCDate() !== desired.day
    || normalizedDate.getUTCHours() !== desired.hour
    || normalizedDate.getUTCMinutes() !== desired.minute
    || normalizedDate.getUTCSeconds() !== desired.second
  ) return "";
  const offsets = new Set<number>();
  for (const delta of [-36, -12, 0, 12, 36]) {
    const probe = nominal + delta * 60 * 60 * 1000;
    const parts = wallTimeParts(probe, timezone);
    if (parts) offsets.add(Date.UTC(
      parts.year, parts.month - 1, parts.day,
      parts.hour, parts.minute, parts.second,
    ) - probe);
  }
  const expected = wallTimeValue(desired);
  const candidates = [...offsets]
    .map(offset => nominal - offset)
    .filter((candidate, index, values) => values.indexOf(candidate) === index)
    .filter(candidate => {
      const parts = wallTimeParts(candidate, timezone);
      return parts !== null && wallTimeValue(parts) === expected;
    });
  return candidates.length === 1 ? new Date(candidates[0]).toISOString() : "";
}
function safeSender(result: ChatSearchResult) {
  return result.sender_name || result.sender_id || "未知发送者";
}
function shortHandle(value: string) {
  let hash = 0x811c9dc5;
  for (let index = 0; index < value.length; index++) hash = Math.imul(hash ^ value.charCodeAt(index), 0x01000193);
  return (hash >>> 0).toString(16).padStart(8, "0");
}

export function ChatSearchPanel(props: Props) {
  const [selected, setSelected] = useState<string[]>([]);
  const [confirmed, setConfirmed] = useState(false);
  const [exportJob, setExportJob] = useState<ExportJob | null>(null);
  const [exportBusy, setExportBusy] = useState(false);
  const [exportError, setExportError] = useState("");
  const [query, setQuery] = useState("");
  const [retrievalMode, setRetrievalMode] = useState<"direct" | "topic">("direct");
  const [summaryTopic, setSummaryTopic] = useState("");
  const [summaryTerms, setSummaryTerms] = useState<string[]>([]);
  const [summaryDegraded, setSummaryDegraded] = useState(false);
  const [expansionBusy, setExpansionBusy] = useState(false);
  const [searchStart, setSearchStart] = useState(() => localInput(props.rangeStart, props.timezone));
  const [searchEnd, setSearchEnd] = useState(() => localInput(props.rangeEnd, props.timezone));
  const [results, setResults] = useState<ChatSearchResult[]>([]);
  const [contextMessageIds, setContextMessageIds] = useState<string[]>([]);
  const [excludedMessageIds, setExcludedMessageIds] = useState<string[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [searchBusy, setSearchBusy] = useState(false);
  const [searchError, setSearchError] = useState("");
  const [searched, setSearched] = useState(false);
  const [generationScopeLocked, setGenerationScopeLocked] = useState(false);
  const rawRevision = useRef(0);
  const lastSearch = useRef<ChatSearchRequest | null>(null);
  const alive = useRef(true);
  const reportGenerationScopeLock = useCallback((locked: boolean) => {
    setGenerationScopeLocked(locked);
    props.onGenerationScopeLockChange?.(locked);
  }, [props.onGenerationScopeLockChange]);

  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => {
    const clear = () => {
      rawRevision.current += 1; lastSearch.current = null;
      setResults([]); setContextMessageIds([]); setExcludedMessageIds([]); setExportJob(null);
      setCursor(null); setSearched(false); setConfirmed(false);
    };
    window.addEventListener("minutes-raw-cleared", clear);
    return () => window.removeEventListener("minutes-raw-cleared", clear);
  }, []);
  useEffect(() => {
    const available = new Set(props.conversations.map(item => item.conversation_selection_id));
    setSelected(current => current.filter(item => available.has(item)));
    setConfirmed(false); setExportJob(null); setExportError(""); setResults([]); setExcludedMessageIds([]);
    setCursor(null); setSearched(false); setSearchError("");
  }, [props.sourceId, props.account, props.conversations]);
  useEffect(() => {
    setSearchStart(localInput(props.rangeStart, props.timezone));
    setSearchEnd(localInput(props.rangeEnd, props.timezone));
  }, [props.rangeStart, props.rangeEnd, props.timezone]);

  function toggle(handle: string) {
    if (props.disabled || exportBusy || searchBusy || generationScopeLocked) return;
    setSelected(current => current.includes(handle)
      ? current.filter(item => item !== handle)
      : current.length >= MAX_SELECTED_CONVERSATIONS ? current : [...current, handle]);
    setConfirmed(false); setResults([]); setExcludedMessageIds([]); setCursor(null); setSearched(false); setSearchError("");
  }

  async function expandSummaryTopic() {
    const topic = summaryTopic.trim();
    if (!topic || expansionBusy || searchBusy || props.disabled || generationScopeLocked) return;
    setExpansionBusy(true); setSearchError(""); setResults([]); setExcludedMessageIds([]); setSearched(false);
    try {
      const expanded = await props.api.expandTopic(topic);
      if (alive.current) { setSummaryTerms(expanded.terms); setSummaryDegraded(expanded.degraded); }
    } catch {
      if (alive.current) { setSummaryTerms([topic]); setSummaryDegraded(true); }
    } finally { if (alive.current) setExpansionBusy(false); }
  }

  function toggleExcluded(messageId: string) {
    if (props.disabled || exportBusy || searchBusy || generationScopeLocked) return;
    setExcludedMessageIds(current => {
      if (current.includes(messageId)) return current.filter(item => item !== messageId);
      if (current.length >= MAX_EXCLUDED_MESSAGES) return current;
      return [...current, messageId];
    });
  }

  async function poll(id: string) {
    for (let attempt = 0; attempt < 9; attempt++) {
      const value = await props.api.exportJob(id);
      if (!alive.current) return value;
      setExportJob(value);
      if (TERMINAL.has(value.state)) return value;
      await new Promise(resolve => setTimeout(resolve, Math.min(4_000, 350 * (2 ** attempt))));
    }
    throw new Error("polling_paused");
  }

  async function beginExport() {
    if (props.sourceId === "manual" || !confirmed || !selected.length || exportBusy || props.disabled || generationScopeLocked || !props.outputRootId ||
        (props.secretConfigured && !props.secretGrantReady)) return;
    const sourceId = props.sourceId;
    setExportBusy(true); setExportError("");
    try {
      const consent = await props.api.issueExportConsent({
        source_id: sourceId, account_id: props.account, conversation_selection_ids: selected,
        range_start: props.rangeStart, range_end: props.rangeEnd, output_root_id: props.outputRootId, display_timezone: props.timezone,
      });
      if (Date.parse(consent.expires_at_utc) <= Date.now()) throw new Error("consent_expired");
      const started = await props.api.startExport(consent.consent_id, `ui-${crypto.randomUUID()}`);
      if (props.secretConfigured) props.onSecretGrantConsumed();
      if (!alive.current) return;
      setExportJob(started); setConfirmed(false); setResults([]); setExcludedMessageIds([]);
      setCursor(null); setSearched(false); setSearchError(""); lastSearch.current = null;
      setExportBusy(false);
      if (!TERMINAL.has(started.state)) await poll(started.job_id);
    } catch { if (alive.current) setExportError("导出或本地导入失败；不会自动改用其他数据源。"); }
    finally { if (alive.current) setExportBusy(false); }
  }

  async function cancelExport() {
    if (!exportJob || TERMINAL.has(exportJob.state) || exportBusy) return;
    setExportBusy(true); setExportError("");
    try { setExportJob(await props.api.cancelExport(exportJob.job_id)); }
    catch { if (alive.current) setExportError("取消状态未能确认，不会自动重启。"); }
    finally { if (alive.current) setExportBusy(false); }
  }

  function request(cursorValue: string | null): ChatSearchRequest | null {
    const start = utcInput(searchStart, props.timezone); const end = utcInput(searchEnd, props.timezone);
    if (!selected.length || !start || !end || Date.parse(end) <= Date.parse(start)) return null;
    return { source_id: props.sourceId, account_id: props.account, query, conversation_selection_ids: selected,
      start_time: start, end_time: end, limit: 25, cursor: cursorValue };
  }
  async function search(searchRequest: ChatSearchRequest | null = request(null), append = false) {
    if (!searchRequest || searchBusy || props.disabled || generationScopeLocked) return;
    const revision = rawRevision.current;
    lastSearch.current = searchRequest; setSearchBusy(true); setSearchError("");
    if (!append) { setResults([]); setContextMessageIds([]); setExcludedMessageIds([]); setCursor(null); setSearched(false); }
    try {
      const page = await props.api.searchChats(searchRequest);
      if (!alive.current || revision !== rawRevision.current) return;
      setResults(current => {
        const combined = append ? [...current, ...page.results] : page.results;
        return combined.filter((item, index) => combined.findIndex(candidate => candidate.message_id === item.message_id) === index);
      });
      setCursor(page.next_cursor); setSearched(true);
    } catch { if (alive.current) setSearchError("搜索失败；范围保持不变，可重试。"); }
    finally { if (alive.current) setSearchBusy(false); }
  }

  async function searchSummary() {
    const base = request(null);
    if (!base || !summaryTerms.length || searchBusy || expansionBusy || props.disabled || generationScopeLocked) return;
    const revision = rawRevision.current;
    setSearchBusy(true); setSearchError(""); setResults([]); setContextMessageIds([]); setExcludedMessageIds([]); setCursor(null); setSearched(false);
    try {
      const recalled = await recallTopicMessages(props.api, base, summaryTerms);
      if (!recalled.length) { if (alive.current) setSearched(true); return; }
      if (new Set(recalled.map(item => item.snapshot_id)).size !== 1) throw new Error("multiple_snapshots");
      const context = await props.api.previewGenerationContext({
        source_id: base.source_id, account_id: base.account_id,
        conversation_selection_ids: base.conversation_selection_ids,
        snapshot_id: recalled[0].snapshot_id, start_time: base.start_time, end_time: base.end_time,
        topic: summaryTopic.trim(), mode: "complete", summary_type: "mixed",
        included_message_ids: recalled.map(item => item.message_id), excluded_message_ids: [],
      });
      if (alive.current && revision === rawRevision.current) {
        const hits = new Set(recalled.map(item => item.message_id));
        setResults(context.messages); setContextMessageIds(context.messages.filter(item => !hits.has(item.message_id)).map(item => item.message_id));
        setSearched(true);
      }
    } catch {
      if (alive.current) setSearchError("相关消息搜索失败或结果过多；请缩小时间范围后重试。");
    } finally { if (alive.current) setSearchBusy(false); }
  }

  const invalidRange = !utcInput(searchStart, props.timezone) || !utcInput(searchEnd, props.timezone) ||
    Date.parse(utcInput(searchEnd, props.timezone)) <= Date.parse(utcInput(searchStart, props.timezone));
  const resultSnapshots = [...new Set(results.map(item => item.snapshot_id))];
  const generationSnapshot = exportJob?.state === "complete" && exportJob.searchable ? exportJob.snapshot_id :
    resultSnapshots.length === 1 ? resultSnapshots[0] : null;
  const generationStart = utcInput(searchStart, props.timezone) || props.rangeStart;
  const generationEnd = utcInput(searchEnd, props.timezone) || props.rangeEnd;
  return <section className={props.hideExport ? "chat-workspace" : "panel chat-workspace"} aria-label={props.hideExport ? "导入聊天的搜索与生成" : "导出与本地搜索"}>
    <h2>{props.hideExport ? "搜索与整理已导入聊天" : "导出与本地搜索"}</h2>
    <p>{props.hideExport ? "选择导入会话并在本地搜索；只有经过 AI 外发预览和再次确认后，选中消息才会发送给已配置模型。" : "仅处理下方勾选的本机会话和时间范围。导出会校验后自动导入 SQLite，搜索不会联网。"}</p>
    {!props.conversations.length ? <p>请先在上方读取本机会话列表。</p> : <fieldset disabled={props.disabled || exportBusy || searchBusy || generationScopeLocked}>
      <legend>会话范围（可多选）</legend>
      <div className="conversation-checks">{props.conversations.map(item => {
        const duplicateName = props.conversations.filter(candidate => candidate.display_name === item.display_name).length > 1;
        const identifier = shortHandle(item.conversation_selection_id);
        return <label className="check" key={item.conversation_selection_id}>
          <input type="checkbox" checked={selected.includes(item.conversation_selection_id)}
            disabled={!selected.includes(item.conversation_selection_id) && selected.length >= MAX_SELECTED_CONVERSATIONS}
            onChange={() => toggle(item.conversation_selection_id)} aria-label={`选择 ${item.display_name}${duplicateName ? ` 标识 ${identifier}` : ""}`} />
          <span>{item.display_name}<small>{item.conversation_type === "group" ? "群聊" :
            ["friend", "user"].includes(item.conversation_type) ? "单聊" : "类型未知"}{duplicateName ? ` · 标识 ${identifier}` : ""}</small></span>
        </label>;
      })}</div>
      {props.conversations.length > MAX_SELECTED_CONVERSATIONS &&
        <p>单次最多选择 50 个会话；更多会话请分批处理。</p>}
    </fieldset>}
    {!props.hideExport && <>
    {props.secretConfigured && !props.secretGrantReady && <p className="warning">导出前需在“上游访问令牌”中再完成一次 Token 使用确认。</p>}
    <label className="check"><input type="checkbox" checked={confirmed} disabled={props.disabled || exportBusy || generationScopeLocked || !selected.length || !props.outputRootId || (props.secretConfigured && !props.secretGrantReady)} onChange={event => setConfirmed(event.target.checked)} />
      确认只导出所选会话和时间范围，并保存到已批准目录。</label>
    <div className="actions"><button type="button" disabled={!confirmed || !selected.length || exportBusy || props.disabled || generationScopeLocked || !props.outputRootId || (props.secretConfigured && !props.secretGrantReady)} onClick={() => void beginExport()}>
      {exportBusy ? "正在处理…" : "导出并建立本地索引"}</button>
      {exportJob && !TERMINAL.has(exportJob.state) && <button type="button" disabled={exportBusy} onClick={() => void cancelExport()}>取消导出</button>}</div>
    {exportJob && <p role="status">{exportJob.state === "complete" && exportJob.searchable ? "已完成并可搜索" : `导出状态：${STATE_LABEL[exportJob.state]}`}
      {exportJob.saved_count !== null ? ` · 已保存 ${exportJob.saved_count} 条` : ""}</p>}
    {exportError && <p role="alert">{exportError}</p>}
    {exportJob?.batch_id && TERMINAL.has(exportJob.state) && <DeletionControl
      api={props.api} targetKind="batch" targetId={exportJob.batch_id}
      label="删除这个导出批次及本地索引" disabled={props.disabled || exportBusy || searchBusy || generationScopeLocked}
      onDeleted={() => { setExportJob(null); setResults([]); setExcludedMessageIds([]); setCursor(null); setSearched(false); setSearchError(""); }} />}
    </>}

    <div className="search-divider" />
    <h3>搜索本地聊天</h3>
    <label>检索方式<select value={retrievalMode} disabled={props.disabled || searchBusy || expansionBusy || generationScopeLocked}
      onChange={event => { setRetrievalMode(event.target.value as "direct" | "topic"); setResults([]); setContextMessageIds([]); setExcludedMessageIds([]); setCursor(null); setSearched(false); setSearchError(""); }}>
      <option value="direct">直接搜索</option><option value="topic">主题相关检索（扩展关键词）</option>
    </select></label>
    {retrievalMode === "topic" ? <>
      <label>主题<input type="text" value={summaryTopic} maxLength={512} disabled={props.disabled || searchBusy || expansionBusy || generationScopeLocked}
        onChange={event => { setSummaryTopic(event.target.value); setSummaryTerms([]); setResults([]); setExcludedMessageIds([]); setSearched(false); setSearchError(""); }} /></label>
      <button type="button" disabled={!summaryTopic.trim() || props.disabled || searchBusy || expansionBusy || generationScopeLocked}
        onClick={() => void expandSummaryTopic()}>{expansionBusy ? "正在扩展…" : "扩展相关关键词"}</button>
      <p>仅将主题词发送给当前 AI Provider 以寻找检索词；扩展词不是聊天事实。</p>
      {!!summaryTerms.length && <p>检索词：{summaryTerms.join("、")}{summaryDegraded ? "（扩展失败，已退回原词）" : ""}</p>}
    </> : <label>搜索聊天内容<input type="search" value={query} maxLength={512} disabled={props.disabled || searchBusy || generationScopeLocked} onChange={event => { setQuery(event.target.value); setSearched(false); setSearchError(""); }} /></label>}
    <div className="fields search-time-fields">
      <label>搜索开始时间<input type="datetime-local" step="1" value={searchStart} disabled={props.disabled || searchBusy || generationScopeLocked} onChange={event => { setSearchStart(event.target.value); setExcludedMessageIds([]); if (retrievalMode === "topic") { setResults([]); setSearched(false); } }} /></label>
      <label>搜索结束时间（不包含）<input type="datetime-local" step="1" value={searchEnd} disabled={props.disabled || searchBusy || generationScopeLocked} onChange={event => { setSearchEnd(event.target.value); setExcludedMessageIds([]); if (retrievalMode === "topic") { setResults([]); setSearched(false); } }} /></label>
    </div>
    {invalidRange && <p role="alert">搜索时间范围无效。</p>}
    <div className="actions"><button type="button" disabled={!selected.length || invalidRange || searchBusy || expansionBusy || props.disabled || generationScopeLocked || (retrievalMode === "topic" && !summaryTerms.length)}
      onClick={() => retrievalMode === "topic" ? void searchSummary() : void search()}>{retrievalMode === "topic" ? "搜索相关消息" : "搜索本地聊天"}</button>
      {retrievalMode === "direct" && searchError && lastSearch.current && <button type="button" disabled={searchBusy || generationScopeLocked} onClick={() => void search(lastSearch.current)}>重试上次搜索</button>}</div>
    {searchBusy && <p role="status">正在搜索本地索引…</p>}
    {searchError && <p role="alert">{searchError}</p>}
    {searched && !results.length && !searchBusy && <p>没有匹配的本地聊天记录。</p>}
    {searched && retrievalMode === "topic" && !!results.length && <p>已补充 {contextMessageIds.length} 条上下文（命中前后 6 条及命中段之间不超过 12 条的短间隙，同一会话和时间范围）；请一并预览、排除。</p>}
    {!!results.length && <section aria-label="搜索结果" className="search-results"><ol>{results.map(item => <li key={item.message_id}>
      <div className="result-meta"><strong>{safeSender(item)}</strong><time dateTime={item.timestamp || undefined}>{item.timestamp ? new Date(item.timestamp).toLocaleString("zh-CN") : "时间未知"}</time></div>
      {contextMessageIds.includes(item.message_id) && <small>补充上下文</small>}
      <p>{item.text ?? "[非文本消息]"}</p><small>{item.conversation_id} · {item.batch_id} · {item.snapshot_id}</small>
      <label className="check result-exclusion"><input type="checkbox"
        checked={excludedMessageIds.includes(item.message_id)}
        disabled={props.disabled || exportBusy || searchBusy || generationScopeLocked ||
          (!excludedMessageIds.includes(item.message_id) && excludedMessageIds.length >= MAX_EXCLUDED_MESSAGES)}
        aria-label={`不用于 AI 生成：${item.text?.slice(0, 80) || "非文本消息"} · 标识 ${shortHandle(item.message_id)}`}
        onChange={() => toggleExcluded(item.message_id)} />不用于 AI 生成</label>
    </li>)}</ol>{cursor && <button type="button" disabled={searchBusy || generationScopeLocked} onClick={() => { const next = request(cursor); void search(next, true); }}>加载更多结果</button>}</section>}
    <GenerationPanel api={props.api} sourceId={props.sourceId} account={props.account}
      retrievalTopic={retrievalMode === "topic" ? summaryTopic : undefined}
      includedMessageIds={retrievalMode === "topic" ? (searched ? results.map(item => item.message_id) : []) : undefined}
      conversationSelectionIds={selected}
      conversationLabels={props.conversations.filter(item => selected.includes(item.conversation_selection_id)).map(item => item.display_name)}
      excludedMessageIds={excludedMessageIds}
      snapshotId={generationSnapshot} rangeStart={generationStart} rangeEnd={generationEnd}
      outputRootId={props.outputRootId}
      onScopeLockChange={reportGenerationScopeLock}
      disabled={props.disabled || exportBusy || searchBusy || invalidRange} />
  </section>;
}
