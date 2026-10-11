import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import type { LocalApiClient } from "../../api/client";
import type { ChatSearchRequest, ChatSearchResult, GenerationSelectionRequest, HowToDocument, ImportCommit, SummaryType } from "../../types";
import { GenerationPanel } from "../generation/GenerationPanel";
import { DocumentEditor } from "../documents/DocumentEditor";
import { DocumentLibrary } from "../documents/DocumentLibrary";
import { recallTopicMessages } from "../search/topicRecall";
import { utcInput } from "../search/ChatSearchPanel";

export function ImportedChatWorkspace({ api, committed, sourceControls, outputRootId, importBusy, onScopeLockChange }: {
  api: LocalApiClient; committed: ImportCommit | null; sourceControls: ReactNode; outputRootId: string;
  importBusy: boolean; onScopeLockChange: (locked: boolean) => void;
}) {
  const [topic, setTopic] = useState("");
  const [direction, setDirection] = useState<SummaryType>("ideas");
  const [autoExpand, setAutoExpand] = useState(false);
  const [terms, setTerms] = useState<string[]>([]);
  const [messages, setMessages] = useState<ChatSearchResult[]>([]);
  const [candidates, setCandidates] = useState<ChatSearchResult[] | null>(null);
  const [excluded, setExcluded] = useState<string[]>([]);
  const [locked, setLocked] = useState(false);
  const [searchBusy, setSearchBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [selected, setSelected] = useState(committed?.conversations.map(item => item.conversation_selection_id) || []);
  const [jobId, setJobId] = useState<string>();
  const [openedDocument, setOpenedDocument] = useState<HowToDocument | null>(null);
  const disabled = locked || importBusy || searchBusy;
  const request = useMemo<ChatSearchRequest | null>(() => {
    if (!committed || !selected.length) return null;
    const first = start ? utcInput(start, committed.display_timezone) : committed.range_start;
    const last = end ? utcInput(end, committed.display_timezone) : committed.range_end;
    if (!first || !last || Date.parse(first) >= Date.parse(last)) return null;
    return { source_id: "manual", account_id: committed.account_id, conversation_selection_ids: selected,
      start_time: first, end_time: last, include_undated: !start && !end, query: "", limit: 100, cursor: null };
  }, [committed, selected, start, end]);
  const requestKey = JSON.stringify(request);
  useEffect(() => {
    let active = true;
    setCandidates(null); setTerms([]); setMessages([]); setError("");
    if (request) void recallTopicMessages(api, request, [""]).then(value => {
      if (active) setMessages(value.filter(item => item.snapshot_id === committed?.snapshot_id));
    }).catch(() => { if (active) setError("聊天范围未能完整加载，请缩小范围或重新打开聊天。"); });
    return () => { active = false; };
  }, [api, requestKey, committed?.snapshot_id]);
  useEffect(() => { setCandidates(null); setTerms([]); setNotice(""); }, [topic, autoExpand]);
  const updateLock = useCallback((value: boolean) => { setLocked(value); onScopeLockChange(value); }, [onScopeLockChange]);
  const complete = useCallback((id: string) => { setJobId(id); setOpenedDocument(null); }, []);

  function selection(ids: string[]): GenerationSelectionRequest {
    if (!request || !committed || !topic.trim()) throw new Error("empty_scope");
    return { source_id: "manual", account_id: committed.account_id, snapshot_id: committed.snapshot_id,
      conversation_selection_ids: selected, start_time: request.start_time, end_time: request.end_time,
      include_undated: request.include_undated, topic: topic.trim(), mode: "complete", summary_type: direction,
      included_message_ids: ids, excluded_message_ids: excluded.filter(id => ids.includes(id)) };
  }
  async function recall(): Promise<ChatSearchResult[]> {
    if (!request || !committed || !topic.trim()) throw new Error("empty_scope");
    setError(""); setNotice("");
    let expanded = [topic.trim()];
    if (autoExpand) {
      try { const result = await api.expandTopic(topic.trim()); expanded = result.terms;
        if (result.degraded) setNotice("关键词扩展暂不可用，已使用原主题搜索。");
      } catch { setNotice("关键词扩展暂不可用，已使用原主题搜索。"); }
    }
    setTerms(expanded);
    const hits = (await recallTopicMessages(api, request, expanded)).filter(item => item.snapshot_id === committed.snapshot_id);
    if (!hits.length) { setCandidates([]); throw new Error("no_hits"); }
    const context = await api.previewGenerationContext(selection(hits.map(item => item.message_id)));
    setCandidates(context.messages); return context.messages;
  }
  async function prepare() {
    try {
      const values = candidates?.length ? candidates : await recall();
      if (!values.some(item => !excluded.includes(item.message_id))) throw new Error("no_messages");
      return selection(values.map(item => item.message_id));
    } catch (caught) { setError("没有找到可整理的完整范围，请调整关键词、时间或排除项后重试。"); throw caught; }
  }
  const visible = candidates || messages;
  const analysis = committed?.analysis;
  return <div className="simple-workspace">
    <section className="panel" aria-label="聊天内容"><h2>1. 聊天内容</h2>
      <div className="choice-row" role="radiogroup" aria-label="聊天来源">
        <label className="check"><input type="radio" checked readOnly />本地导入</label>
        <label className="check muted"><input type="radio" disabled />授权导入（即将支持）</label>
      </div>
      {committed ? <details><summary>导入其他聊天 / 管理已有聊天</summary>{sourceControls}</details> : sourceControls}
      {committed && <><p>当前聊天：{committed.conversations.map(item => item.display_name || "未命名会话").join("、")}</p>
        <p>{analysis ? `${analysis.parsed_count} 条消息 · ${analysis.message_types.text} 条文本 · ${analysis.sender_count} 位发送者` : `已加载 ${messages.length} 条消息`}</p>
        <p className="muted">{analysis?.time_start ? `已知时间：${analysis.time_start} 至 ${analysis.time_end}` : "时间未知，保留原文件消息顺序。"}
          {!!messages.length && ` 当前范围 ${messages.filter(item => item.timestamp !== null).length} 条有时间，${messages.filter(item => item.timestamp === null).length} 条时间未知。`}</p>
        <details><summary>范围与时间筛选</summary>
          {committed.conversations.map(item => <label className="check" key={item.conversation_selection_id}><input type="checkbox" disabled={disabled} checked={selected.includes(item.conversation_selection_id)}
            onChange={event => setSelected(items => event.target.checked ? [...items, item.conversation_selection_id] : items.filter(id => id !== item.conversation_selection_id))} />{item.display_name || "未命名会话"}</label>)}
          <div className="fields"><label>开始时间<input type="datetime-local" value={start} disabled={disabled} onChange={event => setStart(event.target.value)} /></label>
            <label>结束时间（不含）<input type="datetime-local" value={end} disabled={disabled} onChange={event => setEnd(event.target.value)} /></label></div>
          <p className="muted">留空包含无时间消息；指定日期只筛选有时间的消息。时区：{committed.display_timezone}</p>
        </details>
        <details><summary>展开聊天内容（{visible.length} 条，已排除 {excluded.length} 条）</summary>
          {candidates && <button disabled={disabled} onClick={() => setCandidates(null)}>查看全部范围</button>}
          <div className="search-results compact-messages"><ol>{visible.map(item => <li key={item.message_id}>
            <div className="result-meta"><strong>{item.sender_name || item.sender_id || "未知发送者"}</strong><time>{item.timestamp ? new Date(item.timestamp).toLocaleString("zh-CN") : "时间未知"}</time></div>
            <p>{item.text || "非文本消息"}</p><label className="check"><input type="checkbox" checked={excluded.includes(item.message_id)} disabled={disabled}
              onChange={event => setExcluded(ids => event.target.checked ? [...ids, item.message_id] : ids.filter(id => id !== item.message_id))} />排除此消息</label>
          </li>)}</ol></div>
        </details>
      </>}
    </section>
    <section className="panel" aria-label="关键词与整理方向"><h2>2. 关键词 / 整理方向</h2>
      <label>关键词<input value={topic} maxLength={512} disabled={disabled} placeholder="例如：腾讯音乐黑客松" onChange={event => setTopic(event.target.value)} /></label>
      <label className="check"><input type="checkbox" checked={autoExpand} disabled={disabled} onChange={event => setAutoExpand(event.target.checked)} />自动扩展相关关键词（单独使用非思考请求，只发送主题，可能产生一次额外费用；不发送聊天正文）</label>
      <label>整理方向<select value={direction} disabled={disabled} onChange={event => setDirection(event.target.value as SummaryType)}>
        <option value="viewpoints">观点</option><option value="ideas">Idea / 想法</option><option value="how_to">方法论 / 怎么做</option><option value="mixed">综合整理</option>
      </select></label>
      <details><summary>查看检索范围与关键词</summary><p>{terms.length ? terms.join("、") : "开始整理时自动检索，也可以提前查看相关消息。"}</p>
        <button disabled={disabled || !request || !topic.trim()} onClick={() => { setSearchBusy(true); void recall().catch(() => setError("未找到可整理的消息，请调整主题或范围。")).finally(() => setSearchBusy(false)); }}>预览相关消息</button>
        {candidates && <p>检索及相邻上下文 {candidates.length} 条；可展开上方聊天内容并排除。</p>}
      </details>
      {notice && <p role="status">{notice}</p>}{error && <p role="alert">{error}</p>}
      <GenerationPanel api={api} compact sourceId="manual" account={committed?.account_id || ""}
        conversationSelectionIds={selected} conversationLabels={[]} excludedMessageIds={excluded} snapshotId={committed?.snapshot_id || null}
        rangeStart={request?.start_time || ""} rangeEnd={request?.end_time || ""} direction={direction} retrievalTopic={topic}
        disabled={importBusy || searchBusy || !request} onScopeLockChange={updateLock} prepareSelection={prepare} onComplete={complete} />
    </section>
    <DocumentEditor key={jobId || (openedDocument ? `${openedDocument.document_id}:${openedDocument.current_version.version_id}` : "empty")} compact api={api} generationJobId={jobId} initialDocument={openedDocument} />
    <details><summary>已保存的文档</summary><DocumentLibrary key={jobId || "history"} api={api} outputRootId={outputRootId}
      onOpen={doc => { if (!disabled) { setJobId(undefined); setOpenedDocument(doc); } }} /></details>
  </div>;
}
