import { useEffect, useRef, useState } from "react";
import { ApiError, type LocalApiClient } from "../../api/client";
import type { AgentConversation, AgentConversationSummary } from "../../conversation-types";
import type { ContentPreview, ContentUnitEdit } from "../../types";
import { ApprovalCard } from "./ApprovalCard";
import { ArtifactCard } from "./ArtifactCard";
import { AttachmentCard, type PendingAttachment } from "./AttachmentCard";
import { composerChatText } from "./composerChat";
const active = (value: AgentConversation | null) => value?.active_run && ["running", "waiting_approval"].includes(value.active_run.state) ? value.active_run : null;
function base64(file: File): Promise<string> {
  return new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => { const value = String(reader.result); resolve(value.slice(value.indexOf(",") + 1)); }; reader.onerror = () => reject(new Error("file_read_failed")); reader.readAsDataURL(file); });
}
function readText(file: File): Promise<string> { return new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.onerror = () => reject(new Error("file_read_failed")); reader.readAsText(file); }); }
function safeError(error: unknown, fallback: string) {
  if (error instanceof ApiError) {
    if (error.status === 401) return "当前本地会话已失效，请刷新页面重新打开。";
    if (error.code === "agent_instruction_too_long" || error.code === "instruction_too_long") return "连续要求超过可处理长度，请简化要求后再试。";
    if (error.code?.includes("expired") || error.code?.includes("source_unavailable")) return "来源或授权已过期，请重新添加资料后再试。";
  }
  return fallback;
}
const runLabels: Record<string, string> = { waiting_approval: "等待本次外发授权", running: "正在处理本次任务…", complete: "本次整理已完成", cancelled: "任务已停止", failed: "本次任务未完成，请重新发送要求后重试。", needs_input: "等待你的补充" };
export function ConversationWorkspace({ api }: { api: LocalApiClient }) {
  const [sessions, setSessions] = useState<AgentConversationSummary[]>([]);
  const [conversation, setConversation] = useState<AgentConversation | null>(null);
  const current = useRef<AgentConversation | null>(null);
  const epoch = useRef(0);
  const mounted = useRef(true);
  const lock = useRef(false);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [text, setText] = useState("");
  const [attachments, setAttachments] = useState<PendingAttachment[]>([]);
  const [viewEpoch, setViewEpoch] = useState(0);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const messagesEnd = useRef<HTMLDivElement>(null);
  function valid(token: number) { return mounted.current && token === epoch.current; }
  function apply(value: AgentConversation, token: number) {
    if (!valid(token) || (current.current?.conversation_id === value.conversation_id && current.current.revision > value.revision)) return;
    current.current = value; setConversation(value);
    setSessions(items => [value, ...items.filter(item => item.conversation_id !== value.conversation_id)]);
  }
  function clearLocal() { setAttachments([]); setText(""); setError(""); setViewEpoch(value => value + 1); if (fileInput.current) fileInput.current.value = ""; }
  async function stopOld(value: AgentConversation | null) { const run = active(value); if (value && run) await api.cancelAgentRun(value.conversation_id, run.run_id); }
  async function openConversation(id?: string) {
    const previous = current.current;
    const token = ++epoch.current;
    lock.current = true; setBusy(true); setLoading(true); current.current = null; setConversation(null); clearLocal(); setSidebarOpen(false);
    try { await stopOld(previous); if (!valid(token)) return; const value = id ? await api.agentConversation(id) : await api.createAgentConversation(); apply(value, token); }
    catch (caught) { if (valid(token)) setError(safeError(caught, "无法打开本地对话，请重试。")); }
    finally { if (valid(token)) { lock.current = false; setBusy(false); setLoading(false); } }
  }
  useEffect(() => {
    mounted.current = true;
    const token = ++epoch.current;
    void api.listAgentConversations().then(async page => {
      if (!valid(token)) return; setSessions(page.conversations);
      const value = page.conversations.length ? await api.agentConversation(page.conversations[0].conversation_id) : await api.createAgentConversation(); apply(value, token);
    }).catch(() => { if (valid(token)) setError("无法读取本地对话，请刷新页面重试。"); }).finally(() => { if (valid(token)) setLoading(false); });
    return () => { mounted.current = false; epoch.current++; };
  }, [api]);
  useEffect(() => {
    if (conversation?.active_run?.state !== "running") return;
    const token = epoch.current; const id = conversation.conversation_id;
    let stopped = false; let timer: number | undefined;
    async function poll() {
      try { const value = await api.agentConversation(id); if (stopped || !valid(token)) return; apply(value, token); setError(""); if (value.active_run?.state === "running") timer = window.setTimeout(() => void poll(), 750); }
      catch (caught) { if (!stopped && valid(token)) { setError(safeError(caught, "暂时无法读取任务状态，稍后会重试；你仍可停止任务。")); timer = window.setTimeout(() => void poll(), 1500); } }
    }
    timer = window.setTimeout(() => void poll(), 750);
    return () => { stopped = true; clearTimeout(timer); };
  }, [api, conversation?.conversation_id, conversation?.active_run?.state]);
  useEffect(() => {
    function cleared() {
      const previous = current.current; const token = ++epoch.current;
      clearLocal(); setBusy(true); lock.current = true; current.current = null; setConversation(null);
      void (async () => { try { await stopOld(previous); } catch { /* Reload authoritative state even if already invalidated. */ }
        try {
          if (!valid(token)) return;
          if (previous) apply(await api.agentConversation(previous.conversation_id), token);
          else { const page = await api.listAgentConversations(); if (!valid(token)) return; setSessions(page.conversations); apply(page.conversations.length ? await api.agentConversation(page.conversations[0].conversation_id) : await api.createAgentConversation(), token); }
        }
        catch { if (valid(token)) setError("原文已停用，请重新打开对话检查可用来源。已有文档仍保留。"); }
        finally { if (valid(token)) { setBusy(false); setLoading(false); lock.current = false; } }
      })();
    }
    window.addEventListener("minutes-raw-cleared", cleared); return () => window.removeEventListener("minutes-raw-cleared", cleared);
  }, [api]);
  useEffect(() => { messagesEnd.current?.scrollIntoView?.({ block: "end", behavior: "smooth" }); }, [conversation?.messages.length, conversation?.active_run?.approval?.approval_id, attachments.length]);
  async function mutate(operation: (value: AgentConversation, token: number) => Promise<AgentConversation>, fallback: string, after?: () => void) {
    if (lock.current || !current.current) return;
    const value = current.current; const token = epoch.current;
    lock.current = true; setBusy(true); setError("");
    try {
      const next = await operation(value, token);
      if (!valid(token)) { const obsolete = active(next); if (obsolete) void api.cancelAgentRun(next.conversation_id, obsolete.run_id).catch(() => { /* A newer run or cancellation can already have invalidated this run. */ }); return; }
      apply(next, token); after?.();
    }
    catch (caught) { if (valid(token)) setError(safeError(caught, fallback)); }
    finally { if (valid(token)) { lock.current = false; setBusy(false); } }
  }
  function patchAttachment(key: string, patch: Partial<PendingAttachment>, token: number) { if (valid(token)) setAttachments(items => items.map(item => item.key === key ? { ...item, ...patch } : item)); }
  async function prepareFiles(files: File[]) {
    if (lock.current || !current.current || active(current.current) || !files.length) return;
    const token = epoch.current; lock.current = true; setBusy(true); setError("");
    const cards = files.map(file => ({ key: crypto.randomUUID(), label: file.name, state: "preparing" as const })); setAttachments(items => [...items, ...cards]);
    try { for (const [index, file] of files.entries()) {
      if (!valid(token)) return;
      const key = cards[index].key;
      const image = /\.(png|jpe?g)$/i.test(file.name);
      if (!/\.(txt|csv|json|jsonl|xlsx|zip|png|jpe?g)$/i.test(file.name)) { patchAttachment(key, { state: "error", error: "不支持这种文件。可添加 TXT、CSV、JSON、JSONL、XLSX、单文件 ZIP 或 PNG／JPEG 图片。" }, token); continue; }
      if (file.size > (image ? 15 : 64) * 1024 * 1024) { patchAttachment(key, { state: "error", error: image ? "图片超过 15 MiB 上限。" : "文件超过 64 MiB 上限。" }, token); continue; }
      try {
        const payload_base64 = await base64(file); if (!valid(token)) return;
        if (image) { const content = await api.previewContentImport({ images: [{ filename: file.name, payload_base64, input_order: 0 }], retain_pending: true }); patchAttachment(key, { state: "ready", content }, token); }
        else { const chat = await api.previewImport({ origin: "imported_file", platform_hint: "generic", filename: file.name, payload_base64, retain_pending: true, options: { timezone: "Asia/Shanghai", default_conversation_name: file.name.replace(/\.[^.]+$/, "") } }); const noteFallback = chat.analysis.parsed_count === 0 && /\.txt$/i.test(file.name) ? await readText(file) : undefined; patchAttachment(key, { state: "ready", chat, noteFallback }, token); }
      } catch (caught) {
        const noteFallback = /\.txt$/i.test(file.name) ? await readText(file).catch(() => "") : undefined;
        patchAttachment(key, { state: "error", error: safeError(caught, image ? "图片解析失败，请重试或上传文字文件。" : "无法按聊天记录解析。请核对文件格式；普通 TXT 文字可以按普通文本预览。"), noteFallback }, token);
      }
    } } finally { if (valid(token)) { lock.current = false; setBusy(false); } }
  }
  async function preparePastedChat(value: string, clearDraft = false) {
    if (lock.current || !current.current || active(current.current) || !value.trim()) return;
    if (new Blob([value]).size > 64 * 1024 * 1024) { setError("聊天记录超过 64 MiB 上限，请分批添加；内容尚未发送。"); return; }
    const token = epoch.current; const key = crypto.randomUUID();
    lock.current = true; setBusy(true); setError("");
    setAttachments(items => [...items, { key, label: "对话框聊天记录", state: "preparing" }]);
    try {
      const chat = await api.previewImport({ origin: "paste", platform_hint: "generic", pasted_text: value, retain_pending: true, options: { timezone: "Asia/Shanghai", default_conversation_name: "对话框聊天记录" } });
      patchAttachment(key, { state: "ready", chat, noteFallback: chat.analysis.parsed_count === 0 ? value : undefined }, token);
      if (valid(token) && clearDraft) setText("");
    } catch (caught) { patchAttachment(key, { state: "error", error: safeError(caught, "聊天识别失败，请检查消息格式，或按普通文本预览。"), noteFallback: value }, token); }
    finally { if (valid(token)) { lock.current = false; setBusy(false); } }
  }
  async function prepareTextFallback(value: string, key: string) {
    if (lock.current || !current.current || active(current.current) || !value.trim()) return;
    const token = epoch.current; lock.current = true; setBusy(true); setError("");
    setAttachments(items => items.map(item => item.key === key ? { key, label: item.label, state: "preparing" } : item));
    try { const content: ContentPreview = await api.previewContentImport({ pasted_note: value, retain_pending: true }); patchAttachment(key, { state: "ready", content }, token); }
    catch (caught) { patchAttachment(key, { state: "error", error: safeError(caught, "文本预览失败，请移除后重试。"), noteFallback: value }, token); }
    finally { if (valid(token)) { lock.current = false; setBusy(false); } }
  }
  async function reviseAttachment(item: PendingAttachment, edits: ContentUnitEdit[]) {
    if (lock.current || !item.content || item.committedSource) return;
    const token = epoch.current; lock.current = true; setBusy(true); setError("");
    try { const content = await api.reviseContentPreview(item.content.preview_id, edits); patchAttachment(item.key, { content }, token); }
    catch (caught) { if (valid(token)) setError(safeError(caught, "校对未保存，请核对正文后重试。")); }
    finally { if (valid(token)) { lock.current = false; setBusy(false); } }
  }
  async function commitAttachment(item: PendingAttachment, acceptSkipped: boolean) {
    let receipt = item.receipt ?? "";
    await mutate(async (value, token) => {
      let source = item.committedSource;
      if (source) { /* A confirmed import can retry only its conversation association. */ }
      else if (item.chat) { const result = await api.commitImport(item.chat.preview_id, item.chat.preview_fingerprint, acceptSkipped); source = { kind: "chat_snapshot" as const, id: result.snapshot_id, label: item.label }; receipt = `实际导入 ${result.imported_count ?? item.chat.analysis.parsed_count} 条，跳过 ${result.skipped_count ?? item.chat.analysis.unresolved_count} 条。已加入本会话。`; }
      else if (item.content) { const result = await api.commitContentPreview(item.content.preview_id, item.content.preview_fingerprint); source = { kind: "content_batch" as const, id: result.batch_id, label: item.label }; receipt = `实际导入 ${result.unit_count} 条。已加入本会话。`; }
      else throw new Error("missing_import_preview");
      // A changed view must never attach the late import to another conversation.
      if (!valid(token) || current.current?.conversation_id !== value.conversation_id) throw new Error("view_changed");
      patchAttachment(item.key, { committedSource: source, receipt }, token);
      const updated = await api.setAgentConversationSources(value.conversation_id, [...value.sources, source]);
      return updated;
    }, "导入或关联本会话失败，请重新打开对话核对来源。", () => setAttachments(items => items.map(card => card.key === item.key ? { key: card.key, label: card.label, state: "committed", receipt } : card)));
  }
  async function stop() {
    const previous = current.current; if (!previous || !active(previous)) return;
    const token = ++epoch.current; clearLocal(); setBusy(true); lock.current = true;
    // Hide the consumed authorization immediately while the server cancels.
    current.current = { ...previous, active_run: null }; setConversation(current.current);
    try { await stopOld(previous); if (valid(token)) apply(await api.agentConversation(previous.conversation_id), token); }
    catch (caught) { if (valid(token)) setError(safeError(caught, "停止请求未确认，请重新打开对话核对任务状态。")); }
    finally { if (valid(token)) { setBusy(false); lock.current = false; } }
  }
  const run = conversation?.active_run;
  const running = !!active(conversation);
  const pending = attachments.some(item => item.state !== "committed");
  const disabled = loading || busy || !conversation;
  function send() {
    if (!text.trim() || disabled || running || pending) return;
    const chat = composerChatText(text);
    if (chat !== null) { void preparePastedChat(chat, true); return; }
    if (text.length > 512) { setError("本次要求超过512字，请简化后再发送；内容尚未发送。"); return; }
    const draft = text; void mutate(value => api.sendAgentMessage(value.conversation_id, draft, crypto.randomUUID()), "消息未发送成功，请核对来源或简化要求后重试。", () => setText(""));
  }
  return <div className={`conversation-shell${sidebarOpen ? " sidebar-open" : ""}`}>
    <aside className="chat-sidebar" aria-label="会话列表"><h2 className="chat-brand-title"><a className="chat-brand" href="/">记言 <span>· Minutes</span></a></h2><button className="chat-new" type="button" aria-label="新建对话" onClick={() => void openConversation()}>＋ 新建对话</button><p className="chat-sidebar-heading">本地对话</p><nav>{sessions.map(item => <button key={item.conversation_id} type="button" aria-current={conversation?.conversation_id === item.conversation_id ? "page" : undefined} onClick={() => { if (current.current?.conversation_id !== item.conversation_id) void openConversation(item.conversation_id); else setSidebarOpen(false); }}>{item.title}</button>)}</nav><div className="chat-sidebar-footer"><span className="chat-local-dot" />资料保存在本机<p>原文保留 7 天<br />整理文档长期保留</p></div></aside>
    <div className="chat-main"><header className="chat-topbar"><button type="button" className="chat-menu-button" aria-label="切换会话列表" aria-expanded={sidebarOpen} onClick={() => setSidebarOpen(value => !value)}>☰</button><div><strong>{conversation?.title ?? "记言 · Minutes"}</strong><span>你的本地整理助手</span></div><span className="chat-local-label">● 本地优先</span></header>
      <main className="chat-message-scroll" aria-label="对话消息"><div className="chat-message-column">
        {!conversation?.messages.length && !loading && <div className="chat-welcome"><div className="chat-mark">记</div><p className="chat-card-kicker">让零散的信息，留下有用的记忆</p><h1>今天，想整理什么？</h1><p>添加聊天记录、截图或笔记，<br />直接告诉我你的想法，我们一起把它理清楚。</p><div className="chat-suggestions"><span>整理讨论中的不同观点</span><span>把经验汇成可复用的方法</span></div></div>}
        {loading && <p role="status">正在打开本地对话…</p>}
        {conversation?.sources.length ? <section className="chat-sources" aria-label="本会话资料"><span>本会话资料</span>{conversation.sources.map(source => <span className="chat-source-chip" key={`${source.kind}:${source.id}`}>{source.label}</span>)}</section> : null}
        {conversation?.messages.map(message => <article className={`chat-message chat-message-${message.role}`} key={message.message_id}><span className="chat-avatar">{message.role === "user" ? "你" : "记"}</span><div className="chat-message-content"><p className="chat-message-author">{message.role === "user" ? "你" : "记言"}</p>{message.text && <p className="chat-message-text">{message.text}</p>}{message.document_id && message.version_id && <ArtifactCard key={`${message.document_id}:${message.version_id}:${viewEpoch}`} api={api} message={message} />}</div></article>)}
        {attachments.map(item => <AttachmentCard key={item.key} item={item} busy={busy || running} onRemove={() => setAttachments(items => items.filter(card => card.key !== item.key))} onCommit={(card, skipped) => void commitAttachment(card, skipped)} onNoteFallback={() => void prepareTextFallback(item.noteFallback ?? "", item.key)} onRevise={edits => void reviseAttachment(item, edits)} />)}
        {run && <div className="chat-run-status" role="status"><span className={run.state === "running" ? "chat-status-dot is-running" : "chat-status-dot"} />{runLabels[run.state]}</div>}
        {run?.state === "waiting_approval" && run.approval && <ApprovalCard key={run.approval.approval_id} approval={run.approval} busy={busy} onApprove={() => void mutate(value => api.approveAgentRun(value.conversation_id, run.run_id, run.approval!.approval_id, run.approval!.preview_fingerprint), "授权未被接受，请停止后重新发送要求。")} />}
        {error && <p role="alert">{error}</p>}<div ref={messagesEnd} />
      </div></main>
      <div className="chat-composer-area"><div className="chat-composer-wrap">
        {pending && <p className="chat-pending-note">请先确认或移除上方的待导入资料，再发送消息。</p>}
        <div className="chat-composer"><textarea aria-label="发送消息" placeholder={conversation?.sources.length ? "告诉记言你想怎么整理，也可继续粘贴聊天记录…" : "直接粘贴聊天记录，或输入你想整理的要求…"} rows={2} value={text} disabled={disabled} onChange={event => setText(event.target.value)} onPaste={event => { if (disabled || running) return; const chat = composerChatText(event.clipboardData.getData("text/plain")); if (chat !== null) { event.preventDefault(); void preparePastedChat(chat); } }} onKeyDown={event => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); send(); } }} />
          <div className="chat-composer-toolbar"><div className="chat-attach-actions"><button type="button" className="chat-attach-button" aria-label="添加文件或图片" title="添加文件或图片" disabled={disabled || running} onClick={() => fileInput.current?.click()}>＋</button><input ref={fileInput} className="chat-file-input" type="file" multiple accept=".txt,.csv,.json,.jsonl,.xlsx,.zip,.png,.jpg,.jpeg" aria-label="添加资料" disabled={disabled || running} onChange={event => { const files = Array.from(event.target.files ?? []); event.target.value = ""; void prepareFiles(files); }} /></div>
          {running ? <button type="button" className="chat-send" aria-label="停止" onClick={() => void stop()}>■ <span>停止</span></button> : <button type="button" className="chat-send" aria-label="发送" disabled={disabled || !text.trim() || pending} onClick={send}>↑ <span>发送</span></button>}</div>
        </div><p className="chat-small">聊天记录会自动识别并在本机预览，确认后再整理。</p><p className="chat-composer-hint">仅在你逐次授权后向 AI 外发 · 脱敏不保证覆盖全部个人信息</p>
      </div></div>
    </div>
  </div>;
}
