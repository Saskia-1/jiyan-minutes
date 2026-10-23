import { useEffect, useState } from "react";
import type { ContentPreview, ContentUnitEdit, ImportPreview } from "../../types";
import type { AgentSource } from "../../conversation-types";
export interface PendingAttachment { key: string; label: string; state: "preparing" | "ready" | "error" | "committed"; chat?: ImportPreview; content?: ContentPreview; error?: string; noteFallback?: string; receipt?: string; committedSource?: AgentSource }
export function AttachmentCard({ item, busy, onCommit, onRemove, onNoteFallback, onRevise }: { item: PendingAttachment; busy: boolean; onCommit: (item: PendingAttachment, skipped: boolean) => void; onRemove: () => void; onNoteFallback: () => void; onRevise: (edits: ContentUnitEdit[]) => void }) {
  const [edits, setEdits] = useState<ContentUnitEdit[]>([]);
  useEffect(() => { setEdits(item.content?.units.map(({ image_ref: _image, ...unit }) => unit) ?? []); }, [item.content?.preview_fingerprint]);
  if (item.state === "committed") return null;
  if (item.state === "preparing") return <p className="chat-small" role="status">正在导入 {item.label}…</p>;
  const uncertain = item.content?.status === "boundary_uncertain";
  return <section className="chat-attachment" aria-label="资料处理">
    <div className="chat-card-heading"><h3>{item.label}</h3><button className="chat-text-button" type="button" disabled={busy} onClick={onRemove} aria-label={`移除 ${item.label}`}>移除</button></div>
    {item.error && <p role="alert">{item.error}</p>}
    {item.committedSource ? <button type="button" disabled={busy} onClick={() => onCommit(item, true)}>重试添加</button> : <>
      {uncertain ? <><p>截图正文需要校对，请检查后保存。</p>{edits.map((unit, index) => <label key={unit.unit_id}>校对正文 {index + 1}<textarea aria-label={`校对正文 ${index + 1}`} value={unit.text} disabled={busy} onChange={event => setEdits(values => values.map((entry, i) => i === index ? { ...entry, text: event.target.value } : entry))} /></label>)}<button type="button" disabled={busy || !edits.length || edits.some(unit => !unit.text.trim())} onClick={() => onRevise(edits)}>保存校对并导入</button></> : item.content?.status === "ocr_unavailable" ? <p role="alert">本机 OCR 当前不可用，请改用文字文件。</p> : item.state === "ready" ? <p role="alert">没有识别到可用正文，请检查资料后重试。</p> : null}
      {item.noteFallback && <button type="button" disabled={busy} onClick={onNoteFallback}>按文本重试</button>}
    </>}
  </section>;
}
