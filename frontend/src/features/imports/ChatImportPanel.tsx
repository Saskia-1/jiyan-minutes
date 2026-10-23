import { useEffect, useMemo, useRef, useState } from "react";
import { ApiError, type LocalApiClient } from "../../api/client";
import type {
  Conversation, ImportCommit, ImportOptions, ImportPlatformHint, ImportPreview,
} from "../../types";
import { ChatSearchPanel } from "../search/ChatSearchPanel";
import { DeletionControl } from "../data/DeletionControl";
import { DocumentLibrary } from "../documents/DocumentLibrary";
import { ImportedChatWorkspace } from "./ImportedChatWorkspace";
import { ContentImportPanel } from "./ContentImportPanel";

const mappingFields = [
  ["conversation_id", "会话 ID"], ["conversation_name", "会话名称"],
  ["conversation_type", "会话类型"], ["sent_at", "发送时间"],
  ["sender_id", "发送者 ID"], ["sender_name", "发送者名称"],
  ["message_type", "消息类型"], ["text", "消息正文"],
  ["source_message_id", "消息 ID"], ["reply_to_message_id", "回复消息 ID"],
] as const;
const MAX_IMPORT_FILE_BYTES = 64 * 1024 * 1024;
const PREVIEW_ERRORS: Record<string, string> = {
  unsupported_format: "不支持这种文件格式；请选择 TXT、JSON、JSONL、CSV、XLSX 或受支持的单文件 ZIP。",
  invalid_encoding: "无法识别文本编码；请导出为 UTF-8、UTF-8 BOM 或 GB18030 后重试。",
  corrupt_container: "XLSX 或 ZIP 结构无效；请重新导出未损坏的文件。",
  mapping_invalid: "字段映射与文件列不匹配；请重新选择文件并核对列名。",
  limit_exceeded: "文件内容超过安全解析上限；请缩小导出范围后重试。",
  missing_default_year: "该 TXT 时间没有年份；请明确填写默认年份后重试。",
  invalid_content: "内容不符合受支持的聊天记录结构；请核对格式和必填字段。",
};

function bytesToBase64(value: ArrayBuffer) {
  const bytes = new Uint8Array(value);
  let binary = "";
  for (let start = 0; start < bytes.length; start += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(start, start + 0x8000));
  }
  return btoa(binary);
}

function importedConversations(value: ImportCommit): Conversation[] {
  return value.conversations.map((item, index) => ({
    conversation_selection_id: item.conversation_selection_id,
    display_name: item.display_name || `导入会话 ${index + 1}`,
    account_id: value.account_id,
    conversation_type: ["group", "friend", "user"].includes(item.conversation_type || "")
      ? item.conversation_type as "group" | "friend" | "user" : "unknown",
  }));
}

export function ChatImportPanel({ api, outputRootId, compact = false }: { api: LocalApiClient; outputRootId: string; compact?: boolean }) {
  const rawRevision = useRef(0);
  const [rawEpoch, setRawEpoch] = useState(0);
  const [origin, setOrigin] = useState<"imported_file" | "paste">("imported_file");
  const [platform, setPlatform] = useState<ImportPlatformHint>("generic");
  const [file, setFile] = useState<File | null>(null);
  const [pastedText, setPastedText] = useState("");
  const [timezone, setTimezone] = useState("Asia/Shanghai");
  const [defaultDate, setDefaultDate] = useState("");
  const [defaultYear, setDefaultYear] = useState("");
  const [conversationName, setConversationName] = useState("");
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [mappingDirty, setMappingDirty] = useState(false);
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [acceptSkipped, setAcceptSkipped] = useState(false);
  const [committed, setCommitted] = useState<ImportCommit | null>(null);
  const [imports, setImports] = useState<ImportCommit[]>([]);
  const [nextImportCursor, setNextImportCursor] = useState<string | null>(null);
  const [historyBusy, setHistoryBusy] = useState(false);
  const [historyError, setHistoryError] = useState("");
  const [busy, setBusy] = useState(false);
  const [generationScopeLocked, setGenerationScopeLocked] = useState(false);
  const [showContentPanel, setShowContentPanel] = useState(false);
  const [error, setError] = useState("");
  const conversations = useMemo(() => committed ? importedConversations(committed) : [], [committed]);
  const importLocked = busy || generationScopeLocked;

  useEffect(() => {
    const clear = () => {
      rawRevision.current += 1;
      setPreview(null); setConfirmed(false); setAcceptSkipped(false); setPastedText(""); setFile(null); setImports([]);
      setCommitted(value => value ? { ...value, analysis: undefined } : value);
      setRawEpoch(value => value + 1);
    };
    window.addEventListener("minutes-raw-cleared", clear);
    return () => window.removeEventListener("minutes-raw-cleared", clear);
  }, []);

  useEffect(() => {
    let current = true;
    setHistoryBusy(true); setHistoryError("");
    void api.recoverImports(null).then(page => {
      if (!current) return;
      setImports(existing => [
        ...page.imports,
        ...existing.filter(item => !page.imports.some(found => found.import_id === item.import_id)),
      ]);
      setCommitted(existing => page.imports.find(item => item.import_id === existing?.import_id)
        || existing || page.imports[0] || null);
      setNextImportCursor(page.next_cursor);
    }).catch(() => { if (current) setHistoryError("无法恢复已导入批次；未更改本地数据。"); })
      .finally(() => { if (current) setHistoryBusy(false); });
    return () => { current = false; };
  }, [api, rawEpoch]);

  async function loadMoreImports() {
    if (!nextImportCursor || historyBusy || importLocked) return;
    const revision = rawRevision.current;
    setHistoryBusy(true); setHistoryError("");
    try {
      const page = await api.recoverImports(nextImportCursor);
      if (revision !== rawRevision.current) return;
      setImports(existing => [
        ...existing,
        ...page.imports.filter(item => !existing.some(found => found.import_id === item.import_id)),
      ]);
      setNextImportCursor(page.next_cursor);
    } catch { setHistoryError("无法继续加载已导入批次；未更改本地数据。"); }
    finally { setHistoryBusy(false); }
  }

  function removeImported(item: ImportCommit) {
    setImports(existing => existing.filter(value => value.import_id !== item.import_id));
    setCommitted(existing => existing?.import_id === item.import_id ? { ...existing, analysis: undefined } : existing);
    setPreview(null); setConfirmed(false); setAcceptSkipped(false); setGenerationScopeLocked(false);
  }

  function invalidatePreview(clearMapping = false) {
    setPreview(null); setConfirmed(false); setAcceptSkipped(false); setMappingDirty(false); setError("");
    if (clearMapping) setMapping({});
  }

  async function createPreview() {
    if (importLocked || (origin === "imported_file" ? !file : !pastedText.trim())) return;
    if (origin === "imported_file" && file && file.size > MAX_IMPORT_FILE_BYTES) {
      setError("文件超过 64 MiB 导入上限，未读取或写入。");
      return;
    }
    const revision = rawRevision.current;
    setBusy(true); setError(""); setConfirmed(false); setAcceptSkipped(false);
    try {
      const options: ImportOptions = {
        timezone,
        mapping,
        default_date: defaultDate || null,
        default_year: defaultYear ? Number(defaultYear) : null,
        default_conversation_name: conversationName.trim() || (compact ? file?.name.replace(/\.[^.]+$/, "") || "粘贴的聊天" : null),
      };
      const value = origin === "paste"
        ? await api.previewImport({ origin, platform_hint: platform, pasted_text: pastedText, options })
        : await api.previewImport({ origin, platform_hint: platform, filename: file!.name,
          payload_base64: bytesToBase64(await file!.arrayBuffer()), options });
      if (revision !== rawRevision.current) return;
      setPreview(value); setMapping(value.applied_mapping); setMappingDirty(false);
    } catch (caught) {
      const guidance = caught instanceof ApiError && caught.code ? PREVIEW_ERRORS[caught.code] : null;
      setError(`${guidance || "无法生成导入预览；请核对文件或文本后重试。"} 没有写入聊天索引。`);
    }
    finally { setBusy(false); }
  }

  async function commit() {
    if (!preview || !preview.ready_to_commit || mappingDirty || !confirmed || importLocked || preview.analysis.parsed_count === 0 || (preview.analysis.unresolved_count > 0 && !acceptSkipped)) return;
    const revision = rawRevision.current;
    setBusy(true); setError("");
    try {
      const value = await api.commitImport(preview.preview_id, preview.preview_fingerprint, ...(preview.analysis.unresolved_count > 0 ? [acceptSkipped] as const : []));
      if (revision !== rawRevision.current) return;
      setImports(existing => [value, ...existing.filter(item => item.import_id !== value.import_id)]);
      setCommitted(value); setPreview(null); setConfirmed(false); setAcceptSkipped(false);
    } catch { setError("导入未完成；预览内容没有自动重试。"); }
    finally { setBusy(false); }
  }

  function changeMapping(target: string, source: string) {
    setMapping(current => {
      const next = { ...current };
      if (source) next[target] = source; else delete next[target];
      return next;
    });
    setMappingDirty(true); setConfirmed(false); setAcceptSkipped(false);
  }

  const OptionsContainer = compact ? "details" : "div";
  const HistoryContainer = compact ? "details" : "section";
  const importControls = <>
    {!compact && <h2>导入聊天记录</h2>}
    <details onToggle={event => setShowContentPanel(event.currentTarget.open)}>
      <summary>截图 OCR / 笔记文字导入</summary>
      {showContentPanel && <ContentImportPanel api={api} busy={importLocked} onCommitted={() => undefined} />}
    </details>
    <p className="muted">支持 TXT、JSON、JSONL、CSV、XLSX、单文件 ZIP，也可以直接粘贴聊天。</p>
    <div className="import-origin" role="radiogroup" aria-label="导入方式">
      <label className="check"><input type="radio" name="import-origin" checked={origin === "imported_file"} disabled={importLocked}
        onChange={() => { setOrigin("imported_file"); invalidatePreview(true); }} />导入文件</label>
      <label className="check"><input type="radio" name="import-origin" checked={origin === "paste"} disabled={importLocked}
        onChange={() => { setOrigin("paste"); invalidatePreview(true); }} />粘贴文本</label>
    </div>
    {origin === "imported_file" ? <label>聊天记录文件
      <input type="file" accept=".txt,.json,.jsonl,.csv,.xlsx,.zip" disabled={importLocked}
        onChange={event => { setFile(event.target.files?.[0] || null); invalidatePreview(true); }} />
    </label> : <label>聊天文本<textarea aria-label="聊天文本" value={pastedText} disabled={importLocked}
      onChange={event => { setPastedText(event.target.value); invalidatePreview(); }} /></label>}
    <OptionsContainer>{compact && <summary>导入选项</summary>}<div className="fields">
      <label>来源提示<select value={platform} disabled={importLocked} onChange={event => { setPlatform(event.target.value as ImportPlatformHint); invalidatePreview(); }}>
        <option value="generic">通用导出</option><option value="qq">QQ 导出</option><option value="wechat">微信导出</option>
      </select></label>
      <label>原始时间时区<select value={timezone} disabled={importLocked} onChange={event => { setTimezone(event.target.value); invalidatePreview(); }}>
        <option value="Asia/Shanghai">Asia/Shanghai (UTC+08:00)</option><option value="UTC">UTC</option>
      </select></label>
      <label>默认日期<input aria-label="默认日期" type="date" value={defaultDate} disabled={importLocked}
        onChange={event => { setDefaultDate(event.target.value); invalidatePreview(); }} /></label>
      <label>默认年份<input aria-label="默认年份" type="number" min="1" max="9999" inputMode="numeric"
        value={defaultYear} disabled={importLocked} placeholder="例如 2026"
        onChange={event => { setDefaultYear(event.target.value); invalidatePreview(); }} /></label>
      <label>默认会话名称<input aria-label="默认会话名称" value={conversationName} maxLength={512} disabled={importLocked}
        onChange={event => { setConversationName(event.target.value); invalidatePreview(); }} /></label>
    </div></OptionsContainer>
    <button type="button" disabled={importLocked || (origin === "imported_file" ? !file : !pastedText.trim())}
      onClick={() => void createPreview()}>{mappingDirty ? "更新导入预览" : "生成导入预览"}</button>
    {error && <p role="alert">{error}</p>}
    {preview && <section className="import-preview" aria-label="导入预览">
      <h3>导入预览</h3>
      <p role="status">共 {preview.analysis.total_count} 行 · 可导入 {preview.analysis.parsed_count} 条 · 未解析 {preview.analysis.unresolved_count} 行 · 重复 {preview.analysis.duplicate_count} 条 · 发送者 {preview.analysis.sender_count} 个</p>
      <p>格式：{preview.source.format.toUpperCase()} · 大小：{preview.source.byte_size} 字节 · 时间范围：{preview.analysis.time_start || "未知"} 至 {preview.analysis.time_end || "未知"}</p>
      <p>消息类型：文本 {preview.analysis.message_types.text} · 图片 {preview.analysis.message_types.image} · 文件 {preview.analysis.message_types.file} · 其他 {preview.analysis.message_types.other} · 未知 {preview.analysis.message_types.unknown}</p>
      {!!preview.source.columns.length && <OptionsContainer>{compact && <summary>调整字段映射</summary>}<fieldset><legend>字段映射</legend><div className="fields">{mappingFields.map(([target, label]) =>
        <label key={target}>{label}<select value={mapping[target] || ""} disabled={importLocked}
          onChange={event => changeMapping(target, event.target.value)}><option value="">不映射</option>
          {preview.source.columns.map(column => <option value={column} key={column}>{column}</option>)}</select></label>)}</div></fieldset></OptionsContainer>}
      {mappingDirty && <p className="warning">字段映射已变化，请更新预览后再确认。</p>}
      {!!preview.analysis.conversations.length && <p>会话：{preview.analysis.conversations.map(item => `${item.conversation_name || "未命名"}（${item.message_count} 条）`).join("、")}</p>}
      {!!preview.analysis.samples.length && <HistoryContainer aria-label="消息样例">{compact ? <summary>查看消息样例</summary> : <h3>消息样例</h3>}<ol>{preview.analysis.samples.map(item =>
        <li key={item.row_number}><strong>{item.sender_name || "未知发送者"}</strong> · <time dateTime={item.timestamp || undefined}>{item.timestamp || "时间未知"}</time><p>{item.text ?? "[非文本消息]"}</p></li>)}</ol></HistoryContainer>}
      {!!preview.analysis.warnings.length && <OptionsContainer>{compact && <summary>导入说明</summary>}<div className="warning"><strong>提醒</strong><ul>{preview.analysis.warnings.map(item => <li key={item}><code>{item}</code></li>)}</ul></div></OptionsContainer>}
      {!!preview.analysis.issues.length && <div className="warning"><strong>未解析行</strong><ul>{preview.analysis.issues.map((item, index) =>
        <li key={`${item.row_number}-${item.code}-${index}`}>第 {item.row_number} 条记录：{["invalid_text", "message_invalid"].includes(item.code) ? "消息正文为空或格式无效" : item.code === "timestamp_unresolved" ? "发送时间格式无效" : item.code === "conversation_unresolved" ? "会话信息缺失或格式无效" : "记录格式无效，请核对原文件"}</li>)}</ul></div>}
      {preview.analysis.unresolved_count > 0 && <label className="check"><input type="checkbox" checked={acceptSkipped}
        disabled={importLocked || mappingDirty || !preview.ready_to_commit || preview.analysis.parsed_count === 0}
        onChange={event => { setAcceptSkipped(event.target.checked); setConfirmed(false); }} />接受跳过 {preview.analysis.unresolved_count} 条未解析记录，仅导入 {preview.analysis.parsed_count} 条有效记录</label>}
      <label className="check"><input type="checkbox" checked={confirmed} disabled={importLocked || mappingDirty || !preview.ready_to_commit || preview.analysis.parsed_count === 0 || (preview.analysis.unresolved_count > 0 && !acceptSkipped)}
        onChange={event => setConfirmed(event.target.checked)} />确认按此预览导入；来源完整性不会被推断</label>
      <button type="button" disabled={importLocked || mappingDirty || !preview.ready_to_commit || !confirmed || preview.analysis.parsed_count === 0 || (preview.analysis.unresolved_count > 0 && !acceptSkipped)}
        onClick={() => void commit()}>确认导入本地索引</button>
    </section>}
    <HistoryContainer aria-label="已导入批次">
      {compact && <summary>已导入的聊天</summary>}
      <h3>已导入批次</h3>
      {!imports.length && !historyBusy && <p>当前会话还没有已导入批次。</p>}
      {imports.map((item, index) => <section key={item.import_id} aria-label={`已导入批次 ${index + 1}`}>
        <p>{item.conversations.map(value => value.display_name || "未命名会话").join("、")}{!compact && ` · ${item.range_start} 至 ${item.range_end}`}</p>
        {item.imported_count !== undefined && <p>已导入 {item.imported_count} 条有效记录，已跳过 {item.skipped_count ?? 0} 条未解析记录。</p>}
        {item.raw_expires_at_utc && <p className="muted">原文保留至 {new Date(item.raw_expires_at_utc).toLocaleString("zh-CN")}，文档与版本长期保留。</p>}
        <button type="button" disabled={importLocked} onClick={() => setCommitted(item)}>
          {committed?.import_id === item.import_id ? "当前已打开" : "打开搜索与整理"}
        </button>
        <DeletionControl preserveDocuments api={api} targetKind="batch" targetId={item.batch_id}
          label={committed?.import_id === item.import_id ? "删除这次导入及本地索引" : `删除已导入批次 ${index + 1}`}
          disabled={importLocked} onDeleted={() => removeImported(item)} />
      </section>)}
      {nextImportCursor && <button type="button" disabled={historyBusy || importLocked}
        onClick={() => void loadMoreImports()}>加载更早的导入</button>}
      {historyBusy && <p role="status">正在读取已导入批次…</p>}
      {historyError && <p role="alert">{historyError}</p>}
    </HistoryContainer>
  </>;
  if (compact) return <ImportedChatWorkspace key={committed?.import_id || "empty"} api={api} committed={committed}
    sourceControls={importControls} outputRootId={outputRootId} importBusy={busy} onScopeLockChange={setGenerationScopeLocked} />;
  return <section className="panel import-panel" aria-label="导入聊天记录">
    {importControls}
    {committed && imports.some(item => item.import_id === committed.import_id) && <>
      <p role="status">导入完成：{conversations.length} 个会话已进入本地索引。</p>
      <ChatSearchPanel api={api} sourceId="manual" account={committed.account_id} conversations={conversations}
        rangeStart={committed.range_start} rangeEnd={committed.range_end} timezone={committed.display_timezone}
        outputRootId={outputRootId} disabled={busy} secretConfigured={false} secretGrantReady
        onSecretGrantConsumed={() => undefined}
        onGenerationScopeLockChange={setGenerationScopeLocked} hideExport />
    </>}
    <DocumentLibrary api={api} outputRootId={outputRootId} />
  </section>;
}
