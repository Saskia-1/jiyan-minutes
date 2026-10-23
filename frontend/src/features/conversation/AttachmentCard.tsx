import { useEffect, useState } from "react";
import type { ContentPreview, ContentUnitEdit, ImportPreview } from "../../types";
import type { AgentSource } from "../../conversation-types";
export interface PendingAttachment { key: string; label: string; state: "preparing" | "ready" | "error" | "committed"; chat?: ImportPreview; content?: ContentPreview; error?: string; noteFallback?: string; receipt?: string; committedSource?: AgentSource }
export function AttachmentCard({ item, busy, onCommit, onRemove, onNoteFallback, onRevise }: { item: PendingAttachment; busy: boolean; onCommit: (item: PendingAttachment, skipped: boolean) => void; onRemove: () => void; onNoteFallback: () => void; onRevise: (edits: ContentUnitEdit[]) => void }) {
  const [edits, setEdits] = useState<ContentUnitEdit[]>([]);
  const [dirty, setDirty] = useState(false);
  useEffect(() => { setEdits(item.content?.units.map(({ image_ref: _image, ...unit }) => unit) ?? []); setDirty(false); setConfirmed(false); }, [item.content?.preview_fingerprint]);
  const [confirmed, setConfirmed] = useState(false);
  const [acceptSkipped, setAcceptSkipped] = useState(false);
  const parsed = item.chat?.analysis.parsed_count ?? item.content?.units.length ?? 0;
  const skipped = item.chat?.analysis.unresolved_count ?? 0;
  const expired = !item.committedSource && Date.parse(item.chat?.expires_at_utc ?? item.content?.expires_at_utc ?? "") <= Date.now();
  const ready = item.chat ? item.chat.ready_to_commit : item.content?.status === "ready";
  return <section className="chat-attachment" aria-label={item.state === "committed" ? "已导入资料" : "导入确认"}>
    <div className="chat-card-heading"><span aria-hidden="true">↗</span><h3>{item.label}</h3>{item.state !== "committed" && <button className="chat-text-button" type="button" disabled={busy} onClick={onRemove} aria-label={`移除 ${item.label}`}>移除</button>}</div>
    {item.state === "preparing" && <p role="status">正在本机解析资料…</p>}
    {item.state === "error" && <><p role="alert">{item.error}</p>{item.noteFallback && <button type="button" disabled={busy} onClick={onNoteFallback}>按普通文本预览</button>}</>}
    {item.state === "committed" && <p role="status">{item.receipt}</p>}
    {item.state === "ready" && <>
      {item.noteFallback && parsed === 0 && <button type="button" disabled={busy} onClick={onNoteFallback}>按普通文本预览</button>}
      <p>可导入 {parsed} 条{skipped > 0 ? `，未解析 ${skipped} 条` : ""}。确认前不会加入本会话来源。</p>
      {[...(item.chat?.source.warnings ?? []), ...(item.chat?.analysis.warnings ?? []), ...(item.content?.warnings ?? [])].map((warning, index) => <p className="chat-small" key={index}>{warning}</p>)}
      <details open><summary>核对导入预览</summary><div className="chat-import-samples">{item.chat ? item.chat.analysis.samples.map((sample, index) => <p key={index}>{sample.sender_name && <strong>{sample.sender_name}：</strong>}{sample.text ?? "（非文本记录）"}</p>) : item.content?.units.map((unit, index) => <p key={index}>{unit.text}</p>)}</div></details>
      {item.content && item.content.units.length > 0 && <details open={item.content.status === "boundary_uncertain"}><summary>校对识别正文</summary>{edits.map((unit, index) => <label key={unit.unit_id}>校对正文 {index + 1}<textarea aria-label={`校对正文 ${index + 1}`} value={unit.text} disabled={busy || !!item.committedSource} onChange={event => { setEdits(items => items.map((entry, i) => i === index ? { ...entry, text: event.target.value } : entry)); setDirty(true); setConfirmed(false); }} /></label>)}<button type="button" disabled={busy || !!item.committedSource || !edits.length || edits.some(unit => !unit.text.trim())} onClick={() => onRevise(edits)}>保存校对</button>{dirty && <p>校对已修改，请先保存校对。</p>}</details>}
      {item.content && item.content.status !== "ready" && <p role="alert">{item.content.status === "ocr_unavailable" ? "本机 OCR 当前不可用，暂时无法导入这张图片。" : item.content.status === "boundary_uncertain" ? "正文与界面边界不确定，请核对全部识别正文并保存校对。" : "未识别到正文，请添加清晰图片或文字文件。"}</p>}
      {expired && <p role="alert">导入预览已过期，请移除后重新添加。</p>}
      <label className="check"><input type="checkbox" checked={confirmed} disabled={busy} onChange={event => setConfirmed(event.target.checked)} />我已核对预览并确认导入</label>
      {skipped > 0 && <label className="check"><input type="checkbox" checked={acceptSkipped} disabled={busy} onChange={event => setAcceptSkipped(event.target.checked)} />接受跳过 {skipped} 条未解析记录，仅导入 {parsed} 条有效记录</label>}
      <button type="button" disabled={busy || dirty || !confirmed || !ready || parsed === 0 || (skipped > 0 && !acceptSkipped) || expired} onClick={() => onCommit(item, acceptSkipped)}>确认导入</button>
    </>}
  </section>;
}
