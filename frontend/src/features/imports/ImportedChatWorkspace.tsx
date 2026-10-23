import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import type { LocalApiClient } from "../../api/client";
import type { ChatSearchRequest, ChatSearchResult, ContentBatch, GenerationSelectionRequest, HowToDocument, ImportCommit, MixedSearchHit, SummaryType, AgentPlan } from "../../types";
import { GenerationPanel } from "../generation/GenerationPanel";
import { DocumentEditor } from "../documents/DocumentEditor";
import { DocumentLibrary } from "../documents/DocumentLibrary";
import { recallTopicMessages } from "../search/topicRecall";
import { utcInput } from "../search/ChatSearchPanel";

export function ImportedChatWorkspace({ api, committed, sourceControls, outputRootId, importBusy, onScopeLockChange, latestContentBatch = null, onContentBatchConsumed }: {
  api: LocalApiClient; committed: ImportCommit | null; sourceControls: ReactNode; outputRootId: string;
  latestContentBatch?: ContentBatch | null; onContentBatchConsumed?: () => void; importBusy: boolean; onScopeLockChange: (locked: boolean) => void;
}) {
  const rawRevision = useRef(0);
  const [rawEpoch, setRawEpoch] = useState(0);
  const [rawUnavailable, setRawUnavailable] = useState(false);
  const [instruction, setInstruction] = useState("");
  const [plan, setPlan] = useState<AgentPlan | null>(null);
  const [autoPrepareRevision, setAutoPrepareRevision] = useState<number>();
  const autoPrepareSequence = useRef(0);
  const [planning, setPlanning] = useState(false);
  const [keywordText, setKeywordText] = useState("");
  const [manualMode, setManualMode] = useState(false);
  const planningRevision = useRef(0);
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
  const [mixedOpen, setMixedOpen] = useState(false);
  const [contentBatches, setContentBatches] = useState<ContentBatch[]>([]);
  const [selectedContentBatches, setSelectedContentBatches] = useState<string[]>([]);
  const [includeCurrentChat, setIncludeCurrentChat] = useState(!!committed);
  const [scopeLoaded, setScopeLoaded] = useState(false);
  const [selectedChatSnapshot, setSelectedChatSnapshot] = useState(false);
  const [includeUnknownTime, setIncludeUnknownTime] = useState(false);
  const [mixedHits, setMixedHits] = useState<MixedSearchHit[] | null>(null);
  const [mixedSelectedIds, setMixedSelectedIds] = useState<string[]>([]);
  const [mixedError, setMixedError] = useState("");
  const mixedMode = (mixedOpen && selectedChatSnapshot) || selectedContentBatches.length > 0;
  const activeKeywords = [...new Set(keywordText.split(/[、，,；;\n]+/).map(item => item.trim()).filter(Boolean))].slice(0, 10);
  const agentReady = !!plan && !plan.needs_clarification && (activeKeywords.length > 0 || plan.all_content);
  const taskScopeKey = JSON.stringify([rawEpoch, topic, direction, keywordText, manualMode, [committed?.snapshot_id, selected], includeCurrentChat, selectedContentBatches, selectedChatSnapshot, excluded, start, end, includeUnknownTime]);
  const taskScope = useRef(taskScopeKey); taskScope.current = taskScopeKey;
  useEffect(() => {
    if (!latestContentBatch) return;
    setContentBatches(items => [latestContentBatch, ...items.filter(item => item.batch_id !== latestContentBatch.batch_id)]);
    setSelectedContentBatches(ids => [...new Set([...ids, latestContentBatch.batch_id])]);
    setIncludeUnknownTime(true); setRawUnavailable(false); onContentBatchConsumed?.();
  }, [latestContentBatch, onContentBatchConsumed]);
  async function understand() {
    if (!instruction.trim() || planning) return;
    const revision = ++planningRevision.current; setPlanning(true); setPlan(null); setError("");
    try {
      const value = await api.planAgent(instruction);
      if (revision !== planningRevision.current) return;
      setPlan(value); setTopic(value.instruction); setKeywordText(value.keywords.join("、")); setDirection(value.summary_type);
      if (!value.needs_clarification && (value.keywords.length || value.all_content)) setAutoPrepareRevision(++autoPrepareSequence.current);
    } catch { if (revision === planningRevision.current) setError("暂时无法理解要求，请修改后重试；没有发送资料。"); }
    finally { setPlanning(false); }
  }
  const disabled = locked || importBusy || searchBusy || rawUnavailable;
  useEffect(() => {
    const clear = () => {
      planningRevision.current += 1; setPlan(null);
      rawRevision.current += 1; setRawEpoch(rawRevision.current); setRawUnavailable(true);
      setMessages([]); setCandidates(null); setMixedHits(null); setMixedSelectedIds([]);
      setContentBatches([]); setSelectedContentBatches([]); setSelectedChatSnapshot(false); setExcluded([]);
      setOpenedDocument(value => value ? { ...value, source_evidence: [] } : value);
    };
    window.addEventListener("minutes-raw-cleared", clear);
    return () => window.removeEventListener("minutes-raw-cleared", clear);
  }, []);
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
    setCandidates(null); setTerms([]); setMessages([]); setError(""); setScopeLoaded(false);
    if (request) void recallTopicMessages(api, request, [""]).then(value => {
      if (active) { setMessages(value.filter(item => item.snapshot_id === committed?.snapshot_id)); setRawUnavailable(false); setScopeLoaded(true); }
    }).catch(() => { if (active) setError("聊天范围未能完整加载，请缩小范围或重新打开聊天。"); });
    return () => { active = false; };
  }, [api, requestKey, committed?.snapshot_id, rawEpoch]);
  useEffect(() => { setCandidates(null); setTerms([]); setNotice(""); }, [topic, autoExpand, keywordText, direction, manualMode]);
  useEffect(() => { setMixedHits(null); setMixedSelectedIds([]); }, [topic, autoExpand, keywordText, direction, manualMode, start, end, includeUnknownTime]);
  useEffect(() => {
    if (!mixedOpen) return;
    let active = true;
    void api.recoverContentBatches(null).then(page => {
      if (active) setContentBatches(page.items);
    }).catch(() => { if (active) setMixedError("无法读取已导入的截图或笔记批次。"); });
    return () => { active = false; };
  }, [api, mixedOpen, rawEpoch]);
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
    const revision = rawRevision.current; const requestedScope = taskScope.current;
    setError(""); setNotice(manualMode ? "" : "正在筛选当前资料…");
    let expanded = manualMode ? [...new Set([topic.trim(), ...topic.trim().split(/[、，,；;\n]+/).map(term => term.trim()).filter(Boolean)])].slice(0, 10) : activeKeywords.length ? activeKeywords : plan?.all_content ? [""] : [];
    if (manualMode && autoExpand) {
      try { const result = await api.expandTopic(topic.trim()); expanded = [...new Set([...expanded, ...result.terms])].slice(0, 10);
        if (result.degraded) setNotice("关键词扩展暂不可用，已按原主题及输入的关键词进行本地搜索。");
      } catch { setNotice("关键词扩展暂不可用，已按原主题及输入的关键词进行本地搜索。"); }
    }
    if (!expanded.length) throw new Error("needs_keywords");
    if (requestedScope !== taskScope.current || revision !== rawRevision.current) throw new Error("scope_changed");
    setTerms(expanded);
    const hits = (await recallTopicMessages(api, request, expanded)).filter(item => item.snapshot_id === committed.snapshot_id);
    if (requestedScope !== taskScope.current || revision !== rawRevision.current) throw new Error("scope_changed");
    if (!hits.length) { setCandidates([]); if (!manualMode) setNotice(""); throw new Error("no_hits"); }
    const context = await api.previewGenerationContext(selection(hits.map(item => item.message_id)));
    if (revision !== rawRevision.current || requestedScope !== taskScope.current) throw new Error("raw_scope_changed");
    setCandidates(context.messages); if (!manualMode) setNotice(`已找到 ${context.messages.length} 条相关消息与上下文，等待本次授权。`); return context.messages;
  }
  async function prepare() {
    try {
      const values = candidates?.length ? candidates : await recall();
      if (!values.some(item => !excluded.includes(item.message_id))) throw new Error("no_messages");
      return selection(values.map(item => item.message_id));
    } catch (caught) { setError("没有找到可整理的完整范围，请调整关键词、时间或排除项后重试。"); throw caught; }
  }
  async function searchMixedSources() {
    const revision = rawRevision.current;
    setMixedError(""); setMixedHits(null); setMixedSelectedIds([]);
    const source_refs = [
      ...(selectedChatSnapshot && committed ? [{ kind: "chat_snapshot" as const, id: committed.snapshot_id }] : []),
      ...selectedContentBatches.map(id => ({ kind: "content_batch" as const, id })),
    ];
    if (!source_refs.length || !topic.trim()) { setMixedError("先勾选来源并填写主题。"); return; }
    setSearchBusy(true);
    try {
      if ((start && !end) || (end && !start)) throw new Error("incomplete_range");
      let expanded = manualMode ? [...new Set([topic.trim(), ...topic.trim().split(/[、，,；;\n]+/).map(term => term.trim()).filter(Boolean)])].slice(0, 10) : activeKeywords.length ? activeKeywords : plan?.all_content ? [""] : [];
      if (manualMode && autoExpand) {
        try { const value = await api.expandTopic(topic.trim()); expanded = [...new Set([...expanded, ...value.terms])].slice(0, 10);
          if (value.degraded) setNotice("关键词扩展暂不可用，已按原主题及输入的关键词进行本地搜索。");
        } catch { setNotice("关键词扩展暂不可用，已按原主题及输入的关键词进行本地搜索。"); }
      }
      setTerms(expanded);
      const first = start ? utcInput(start, committed?.display_timezone || "Asia/Shanghai") : null;
      const last = end ? utcInput(end, committed?.display_timezone || "Asia/Shanghai") : null;
      if ((start && !first) || (end && !last) || (first && last && Date.parse(first) >= Date.parse(last))) throw new Error("invalid_range");
      const page = await api.searchMixed({ source_refs, include_undated: includeUnknownTime, terms: expanded,
        ...(first && last ? { start_time: first, end_time: last } : {}) });
      if (revision === rawRevision.current) setMixedHits(page.hits);
    } catch { setMixedError("混合检索失败；请检查来源、时间范围后重试。"); }
    finally { setSearchBusy(false); }
  }
  async function prepareMixedPreview() {
    const source_refs = [
      ...(selectedChatSnapshot && committed ? [{ kind: "chat_snapshot" as const, id: committed.snapshot_id }] : []),
      ...selectedContentBatches.map(id => ({ kind: "content_batch" as const, id })),
    ];
    if (!mixedHits?.length || !mixedSelectedIds.length || !source_refs.length || !topic.trim()) throw new Error("empty_mixed_scope");
    const first = start ? utcInput(start, committed?.display_timezone || "Asia/Shanghai") : null;
    const last = end ? utcInput(end, committed?.display_timezone || "Asia/Shanghai") : null;
    if ((start && !first) || (end && !last) || (first && last && Date.parse(first) >= Date.parse(last))) throw new Error("invalid_range");
    return api.previewMixedGeneration({ source_refs, included_unit_ids: mixedHits.map(hit => hit.unit_id),
      excluded_unit_ids: mixedHits.filter(hit => !mixedSelectedIds.includes(hit.unit_id)).map(hit => hit.unit_id),
      topic: topic.trim(), mode: "complete", summary_type: direction,
      include_undated: includeUnknownTime,
      ...(first && last ? { start_time: first, end_time: last } : {}),
    });
  }
  async function prepareAgentMixedPreview() {
    const revision = rawRevision.current; const requestedScope = taskScope.current;
    const source_refs = [
      ...(committed && includeCurrentChat && selected.length ? [{ kind: "chat_snapshot" as const, id: committed.snapshot_id }] : []),
      ...selectedContentBatches.map(id => ({ kind: "content_batch" as const, id })),
    ];
    const terms = activeKeywords.length ? activeKeywords : plan?.all_content ? [""] : [];
    if (!terms.length || !source_refs.length) throw new Error("empty_scope");
    const first = start ? utcInput(start, committed?.display_timezone || "Asia/Shanghai") : null;
    const last = end ? utcInput(end, committed?.display_timezone || "Asia/Shanghai") : null;
    if ((start && !end) || (end && !start) || (first && last && Date.parse(first) >= Date.parse(last))) throw new Error("invalid_range");
    const range = first && last ? { start_time: first, end_time: last } : {};
    setNotice("正在筛选当前资料…"); setError("");
    const page = await api.searchMixed({ source_refs, terms, include_undated: includeUnknownTime, ...range });
    if (revision !== rawRevision.current || requestedScope !== taskScope.current) throw new Error("scope_changed");
    // A mixed snapshot covers its entire import. Respect the chosen chat conversations and exclusions locally.
    const selectedMessages = new Set(messages.map(item => item.message_id));
    const hits = page.hits.filter(hit => hit.source_kind !== "chat_message" || (selectedMessages.has(hit.unit_id) && !excluded.includes(hit.unit_id)));
    if (!hits.length) { setError("没有找到可整理的正文，请调整关键词或当前来源后重试。"); setNotice(""); throw new Error("no_hits"); }
    setNotice(`已找到 ${hits.length} 条相关正文，等待本次授权。`);
    return api.previewMixedGeneration({ source_refs, included_unit_ids: hits.map(item => item.unit_id), excluded_unit_ids: [],
      topic: topic.trim(), mode: "complete", summary_type: direction, include_undated: includeUnknownTime, ...range });
  }
  const visible = candidates || messages;
  const analysis = committed?.analysis;
  return <div className="simple-workspace">
    <section className="panel" aria-label="聊天内容"><h2>1. 添加资料</h2>
      <div className="choice-row" role="radiogroup" aria-label="聊天来源">
        <label className="check"><input type="radio" checked readOnly />本地导入</label>
        <label className="check muted"><input type="radio" disabled />授权导入（即将支持）</label>
      </div>
      {committed ? <details><summary>导入其他聊天 / 管理已有聊天</summary>{sourceControls}</details> : sourceControls}
      {rawUnavailable && <p role="status">原文已停用或清理，已有文档仍可编辑与下载。</p>}
      {committed && <>{committed.imported_count !== undefined && <p role="status">本次导入 {committed.imported_count} 条有效记录，跳过 {committed.skipped_count ?? 0} 条未解析记录。</p>}<p>当前聊天：{committed.conversations.map(item => item.display_name || "未命名会话").join("、")}</p>
        {!manualMode && <label className="check"><input type="checkbox" checked={includeCurrentChat} disabled={disabled} onChange={event => setIncludeCurrentChat(event.target.checked)} />本次整理包含当前聊天</label>}
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
      {!!selectedContentBatches.length && <p className="scope-summary" role="status">本次已选择 {selectedContentBatches.length} 个截图／笔记批次；可在下方调整。</p>}
      <details onToggle={event => setMixedOpen(event.currentTarget.open)}><summary>选择截图／笔记与聊天来源，预览混合检索</summary>
        {mixedOpen && <>
          <p className="muted">只检索下方明确勾选的整个来源批次；不会自动纳入生成。</p>
          {committed && <label className="check"><input type="checkbox" checked={selectedChatSnapshot} disabled={disabled}
            onChange={event => { setSelectedChatSnapshot(event.target.checked); setMixedHits(null); setMixedSelectedIds([]); }} />当前聊天导入批次（全部会话）</label>}
          {contentBatches.map((batch, index) => <label className="check" key={batch.batch_id}><input type="checkbox" checked={selectedContentBatches.includes(batch.batch_id)} disabled={disabled}
            onChange={event => { setSelectedContentBatches(current => event.target.checked ? [...current, batch.batch_id] : current.filter(id => id !== batch.batch_id)); setMixedHits(null); setMixedSelectedIds([]); }} />截图／笔记批次 {index + 1} · {batch.unit_count} 条正文</label>)}
          {!contentBatches.length && <p>还没有已导入的截图或笔记。可在上方导入。</p>}
          <label className="check"><input type="checkbox" checked={includeUnknownTime} disabled={disabled}
            onChange={event => { setIncludeUnknownTime(event.target.checked); setMixedHits(null); setMixedSelectedIds([]); }} />包含时间未知的聊天与笔记</label>
          <button type="button" disabled={disabled || !topic.trim()} onClick={() => void searchMixedSources()}>预览勾选来源的相关正文</button>
          {mixedError && <p role="alert">{mixedError}</p>}
          {mixedHits && <details open><summary>相关正文 {mixedHits.length} 条 · 已选择 {mixedSelectedIds.length} 条</summary><ol>{mixedHits.map(hit => <li key={hit.unit_id}>
            <strong>{hit.source_kind === "note_passage" ? "笔记／网页" : "聊天消息"}</strong> · {hit.timestamp || "时间未知"}
            <p>{hit.text}</p><label className="check"><input type="checkbox" checked={mixedSelectedIds.includes(hit.unit_id)} disabled={disabled}
              onChange={event => setMixedSelectedIds(current => event.target.checked ? [...current, hit.unit_id] : current.filter(id => id !== hit.unit_id))} />纳入整理</label>
          </li>)}</ol></details>}
          <p className="muted">只会发送逐条勾选的正文；未勾选的结果会被排除。生成前仍会显示完整外发预览。</p>
        </>}
      </details>
    </section>
    <section className="panel agent-composer" aria-label="整理任务"><h2>2. 交给记言整理</h2>
      {!manualMode && <>
        <label>整理要求<textarea className="agent-instruction" value={instruction} maxLength={512} disabled={locked || importBusy}
          placeholder="例如：整理这段聊天里有关创业的想法，列出各方观点和分歧"
          onChange={event => { planningRevision.current += 1; setInstruction(event.target.value); setPlan(null); setAutoPrepareRevision(undefined); setTopic(""); setError(""); }} /></label>
        <div className="actions"><button disabled={disabled || planning || !instruction.trim()} onClick={() => void understand()}>{planning ? "正在理解要求…" : "理解并整理"}</button>
          <span className="muted">自动理解并筛选本机资料，外发前请你确认。</span></div>
        {plan && <section className="agent-task-card" aria-label="任务理解结果">
          <p className="agent-eyebrow">请核对这次任务</p><p>{plan.goal}</p>
          {plan.notices.map((text, index) => <p className="warning" key={index}>{text}</p>)}
          {plan.needs_clarification && <><p role="status">本地规则无法确定全部含义，请调整关键词和整理方向后继续。</p><button disabled={disabled} onClick={() => setPlan({ ...plan, needs_clarification: false })}>已核对以上任务</button></>}
          <label>整理目标与完整要求<textarea value={topic} maxLength={512} disabled={disabled} onChange={event => setTopic(event.target.value)} /></label>
          <div className="fields"><label>检索关键词<input value={keywordText} maxLength={512} disabled={disabled} placeholder="用顿号分隔，例如：创业、就业" onChange={event => { setKeywordText(event.target.value); setPlan(value => value ? { ...value, needs_clarification: false } : value); }} /></label>
          <label>任务整理方向<select value={direction} disabled={disabled} onChange={event => { setDirection(event.target.value as SummaryType); setPlan(value => value ? { ...value, needs_clarification: false } : value); }}>
            <option value="viewpoints">观点</option><option value="ideas">想法</option><option value="how_to">方法</option><option value="mixed">综合</option>
          </select></label></div>
          <p className="muted">{plan.all_content ? "要求包含全部内容：关键词留空时检索当前选中资料的全文。" : "只按以上关键词检索当前资料；没有匹配时不会生成。"}</p>
        </section>}
      </>}
      <details onToggle={event => { if (!event.currentTarget.open && manualMode) { setManualMode(false); setTopic(plan?.instruction || ""); setAutoExpand(false); } }}><summary>高级设置 · 手动关键词流程</summary>
        <label className="check"><input type="checkbox" checked={manualMode} disabled={disabled} onChange={event => { setManualMode(event.target.checked); setTopic(event.target.checked ? "" : plan?.instruction || ""); setAutoExpand(false); }} />使用手动关键词流程</label>
      {manualMode && <section aria-label="关键词与整理方向">
      <label>关键词<input value={topic} maxLength={512} disabled={disabled} placeholder="例如：腾讯音乐黑客松" onChange={event => setTopic(event.target.value)} /></label>
      <label className="check"><input type="checkbox" checked={autoExpand} disabled={disabled} onChange={event => setAutoExpand(event.target.checked)} />自动扩展相关关键词（单独使用非思考请求，只发送主题，可能产生一次额外费用；不发送聊天正文；每次另行确认）</label>
      <label>整理方向<select value={direction} disabled={disabled} onChange={event => { setDirection(event.target.value as SummaryType); setPlan(value => value ? { ...value, needs_clarification: false } : value); }}>
        <option value="viewpoints">观点</option><option value="ideas">Idea / 想法</option><option value="how_to">方法论 / 怎么做</option><option value="mixed">综合整理</option>
      </select></label>
      <details><summary>查看检索范围与关键词</summary><p>{terms.length ? terms.join("、") : "开始整理时自动检索，也可以提前查看相关消息。"}</p>
        <button disabled={disabled || !request || !topic.trim()} onClick={() => { setSearchBusy(true); void recall().catch(() => setError("未找到可整理的消息，请调整主题或范围。")).finally(() => setSearchBusy(false)); }}>预览相关消息</button>
        {candidates && <p>检索及相邻上下文 {candidates.length} 条；可展开上方聊天内容并排除。</p>}
      </details>
      </section>}</details>
      {notice && <p role="status">{notice}</p>}{error && <p role="alert">{error}</p>}
      <GenerationPanel api={api} compact autoPrepareRevision={manualMode || !plan ? undefined : autoPrepareRevision}
        prepareLabel={manualMode ? undefined : "重新预览外发内容"} showPrepareAction={manualMode || !!plan} sourceId="manual" account={committed?.account_id || ""}
        conversationSelectionIds={selected} conversationLabels={[]} excludedMessageIds={excluded} snapshotId={committed?.snapshot_id || null}
        rangeStart={request?.start_time || ""} rangeEnd={request?.end_time || ""} direction={direction} retrievalTopic={topic}
        disabled={importBusy || searchBusy || rawUnavailable || (!!committed && (manualMode || includeCurrentChat) && !scopeLoaded) || (!manualMode && (!agentReady || (!mixedMode && !includeCurrentChat))) || (mixedMode ? (manualMode ? !mixedSelectedIds.length : !selectedContentBatches.length && !selectedChatSnapshot) : !request)} onScopeLockChange={updateLock}
        scopeRevision={JSON.stringify([includeCurrentChat, keywordText, manualMode, plan, rawEpoch, mixedMode, selectedChatSnapshot, selectedContentBatches, mixedHits?.map(hit => hit.unit_id), mixedSelectedIds, includeUnknownTime, start, end])}
        {...(mixedMode ? { preparePreview: manualMode ? prepareMixedPreview : prepareAgentMixedPreview } : { prepareSelection: prepare })} onComplete={complete} />
    </section>
    <DocumentEditor key={jobId || (openedDocument ? `${openedDocument.document_id}:${openedDocument.current_version.version_id}` : "empty")} compact api={api} generationJobId={jobId} initialDocument={openedDocument} />
    <details><summary>已保存的文档</summary><DocumentLibrary key={jobId || "history"} api={api} outputRootId={outputRootId}
      onOpen={doc => { if (!disabled) { setJobId(undefined); setOpenedDocument(doc); } }} /></details>
  </div>;
}
