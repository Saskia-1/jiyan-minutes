import { useEffect, useState } from "react";
import { ApiError, type LocalApiClient } from "../../api/client";
import type { ContentBatch, ContentPreview, ContentUnitEdit } from "../../types";

const MAX_IMAGE = 15 * 1024 * 1024;
const MAX_TOTAL = 64 * 1024 * 1024;
const MAX_NOTE = 1024 * 1024;

function encode(bytes: ArrayBuffer): string {
  const data = new Uint8Array(bytes);
  let value = "";
  for (let start = 0; start < data.length; start += 0x8000) {
    value += String.fromCharCode(...data.subarray(start, start + 0x8000));
  }
  return btoa(value);
}

const errorLabels: Record<string, string> = {
  image_limit_exceeded: "截图超出数量、大小或像素上限；请缩小后重试。",
  note_limit_exceeded: "笔记文字超过 1 MiB；请拆成较小的内容。",
  unsupported_image: "只支持 PNG、JPG 或 JPEG 截图。",
  invalid_image_encoding: "截图文件无法读取；请重新选择。",
  preview_capacity_exceeded: "待确认的导入预览过多，请稍后再试。",
};

export function ContentImportPanel({ api, busy, onCommitted }: {
  api: LocalApiClient; busy: boolean; onCommitted: (batch: ContentBatch) => void;
}) {
  const [mode, setMode] = useState<"images" | "paste_note">("images");
  const [files, setFiles] = useState<File[]>([]);
  const [note, setNote] = useState("");
  const [preview, setPreview] = useState<ContentPreview | null>(null);
  const [edits, setEdits] = useState<ContentUnitEdit[]>([]);
  const [imageOrders, setImageOrders] = useState<{ image_ref: string; input_order: number }[]>([]);
  const [dirty, setDirty] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState("");
  const [batches, setBatches] = useState<ContentBatch[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [historyBusy, setHistoryBusy] = useState(false);
  const locked = busy || working;

  useEffect(() => {
    let active = true;
    void api.recoverContentBatches(null).then(page => {
      if (active) { setBatches(page.items); setCursor(page.next_cursor); }
    }).catch(() => { if (active) setError("无法恢复已导入的截图或笔记；未更改本地数据。"); });
    return () => { active = false; };
  }, [api]);

  function clearPreview() {
    setPreview(null); setEdits([]); setImageOrders([]); setConfirmed(false); setDirty(false); setError("");
  }

  async function previewInput() {
    if (locked) return;
    if (mode === "images") {
      if (!files.length || files.length > 20) { setError("每批请选择 1～20 张截图。"); return; }
      if (files.some(file => !/\.(png|jpe?g)$/i.test(file.name))) { setError("只支持 PNG、JPG 或 JPEG 截图。"); return; }
      if (files.some(file => file.size > MAX_IMAGE)) { setError("单张截图不能超过 15 MiB；文件尚未读取。"); return; }
      if (files.reduce((size, file) => size + file.size, 0) > MAX_TOTAL) { setError("本批截图不能超过 64 MiB；文件尚未读取。"); return; }
    } else if (!note.trim() || new TextEncoder().encode(note).byteLength > MAX_NOTE) {
      setError("笔记正文不能为空，且不能超过 1 MiB。"); return;
    }
    setWorking(true); setError(""); setConfirmed(false);
    try {
      const value = mode === "images"
        ? await api.previewContentImport({ images: await Promise.all(files.map(async (file, index) => ({
          filename: file.name, payload_base64: encode(await file.arrayBuffer()), input_order: index,
        }))) })
        : await api.previewContentImport({ pasted_note: note });
      setPreview(value);
      setEdits(value.units.map(unit => ({ unit_id: unit.unit_id, kind: unit.kind, group_id: unit.group_id,
        text: unit.text, sender_name: unit.sender_name, sent_at: unit.sent_at, order: unit.order })));
      setImageOrders(value.images.map(image => ({ image_ref: image.image_ref, input_order: image.input_order })));
      setDirty(false);
    } catch (caught) {
      const code = caught instanceof ApiError ? caught.code : null;
      setError(code && errorLabels[code] ? errorLabels[code] : "无法识别或预览内容；请核对文件后重试。未保存任何正文。");
    } finally { setWorking(false); }
  }

  function changeEdit(index: number, change: Partial<ContentUnitEdit>) {
    setEdits(current => current.map((item, itemIndex) => itemIndex === index ? { ...item, ...change } : item));
    setDirty(true); setConfirmed(false);
  }

  async function saveCorrection() {
    if (!preview || locked || !edits.length || edits.some(item => !item.text.trim())) return;
    setWorking(true); setError(""); setConfirmed(false);
    try {
      const value = await api.reviseContentPreview(preview.preview_id, edits, imageOrders);
      setPreview(value);
      setEdits(value.units.map(unit => ({ unit_id: unit.unit_id, kind: unit.kind, group_id: unit.group_id,
        text: unit.text, sender_name: unit.sender_name, sent_at: unit.sent_at, order: unit.order })));
      setImageOrders(value.images.map(image => ({ image_ref: image.image_ref, input_order: image.input_order })));
      setDirty(false);
    } catch { setError("校对未保存；请检查类型、正文、顺序和时间后重试。"); }
    finally { setWorking(false); }
  }

  async function commit() {
    if (!preview || preview.status !== "ready" || dirty || !confirmed || locked) return;
    setWorking(true); setError("");
    try {
      const batch = await api.commitContentPreview(preview.preview_id, preview.preview_fingerprint);
      setBatches(current => [batch, ...current.filter(item => item.batch_id !== batch.batch_id)]);
      onCommitted(batch); clearPreview();
    } catch { setError("导入未完成；请确认当前预览仍有效。不会自动重试。"); }
    finally { setWorking(false); }
  }

  async function loadMore() {
    if (!cursor || historyBusy || locked) return;
    setHistoryBusy(true);
    try {
      const page = await api.recoverContentBatches(cursor);
      setBatches(current => [...current, ...page.items.filter(item => !current.some(existing => existing.batch_id === item.batch_id))]);
      setCursor(page.next_cursor);
    } catch { setError("无法继续恢复较早的导入批次。"); }
    finally { setHistoryBusy(false); }
  }

  return <section className="content-import-panel" aria-label="截图或笔记正文导入">
    <p className="muted">截图只在本机识别；导航、账号、点赞等界面文字不作为正文候选。最终内容请自行校对后确认。</p>
    <div role="radiogroup" aria-label="内容导入方式">
      <label className="check"><input type="radio" name="content-import-mode" checked={mode === "images"} disabled={locked}
        onChange={() => { setMode("images"); clearPreview(); }} />上传聊天或笔记截图</label>
      <label className="check"><input type="radio" name="content-import-mode" checked={mode === "paste_note"} disabled={locked}
        onChange={() => { setMode("paste_note"); clearPreview(); }} />粘贴笔记文字</label>
    </div>
    {mode === "images" ? <label>聊天或笔记截图<input type="file" accept=".png,.jpg,.jpeg,image/png,image/jpeg" multiple disabled={locked}
      onChange={event => { setFiles(Array.from(event.target.files || [])); clearPreview(); }} /></label>
      : <label>笔记正文<textarea aria-label="笔记正文" value={note} disabled={locked} onChange={event => { setNote(event.target.value); clearPreview(); }} /></label>}
    <button type="button" disabled={locked || (mode === "images" ? files.length === 0 : !note.trim())} onClick={() => void previewInput()}>识别并预览</button>
    {error && <p role="alert">{error}</p>}
    {preview && <section aria-label="正文校对预览">
      <h3>正文校对预览</h3>
      <p>建议类型：{preview.suggested_kind === "chat" ? "聊天" : preview.suggested_kind === "note" ? "笔记／网页" : "不确定"} · 正文候选 {preview.units.length} 条</p>
      {preview.status === "boundary_uncertain" && <p className="warning">正文与界面边界不确定；请裁剪图片重试，或手动核对下方全部正文并保存校对。</p>}
      {preview.status === "empty_body" && <p className="warning">未找到可导入的正文。请裁剪图片或改为粘贴笔记正文。</p>}
      {preview.status === "ocr_unavailable" && <p className="warning">本机图片识别暂不可用；请检查安装说明或改为粘贴笔记正文。</p>}
      {preview.warnings.includes("exact_duplicate_image") && <p className="warning">存在完全相同的截图，请检查是否重复；系统没有自动删除。</p>}
      {preview.images.length > 1 && <p className="warning">多张截图可能有重叠，请人工核对顺序与重复内容。</p>}
      {imageOrders.map(image => <label key={image.image_ref}>截图顺序 {image.image_ref}<input type="number" min={0} max={1000} value={image.input_order} disabled={locked}
        onChange={event => { setImageOrders(items => items.map(item => item.image_ref === image.image_ref ? { ...item, input_order: Number(event.target.value) } : item)); setDirty(true); setConfirmed(false); }} /></label>)}
      {edits.map((unit, index) => <fieldset key={unit.unit_id}><legend>正文 {index + 1}</legend>
        <label>类型 {index + 1}<select aria-label={`类型 ${index + 1}`} value={unit.kind} disabled={locked}
          onChange={event => changeEdit(index, { kind: event.target.value as ContentUnitEdit["kind"], sender_name: null, sent_at: null })}>
          <option value="note_passage">笔记／网页正文</option><option value="chat_message">聊天消息</option>
        </select></label>
        <label>正文 {index + 1}<textarea aria-label={`正文 ${index + 1}`} value={unit.text} disabled={locked}
          onChange={event => changeEdit(index, { text: event.target.value })} /></label>
        <label>所属组<input aria-label={`所属组 ${index + 1}`} value={unit.group_id} disabled={locked} maxLength={128}
          onChange={event => changeEdit(index, { group_id: event.target.value })} /></label>
        <label>条目顺序<input aria-label={`条目顺序 ${index + 1}`} type="number" min={0} max={10000} value={unit.order} disabled={locked}
          onChange={event => changeEdit(index, { order: Number(event.target.value) })} /></label>
        {unit.kind === "chat_message" && <>
          <label>可见发送者（可留空）<input aria-label={`发送者 ${index + 1}`} value={unit.sender_name || ""} disabled={locked}
            onChange={event => changeEdit(index, { sender_name: event.target.value || null })} /></label>
          <label>可见时间（需含时区，未知则留空）<input aria-label={`时间 ${index + 1}`} type="text" value={unit.sent_at || ""} disabled={locked}
            placeholder="2026-10-12T10:00:00+08:00" onChange={event => changeEdit(index, { sent_at: event.target.value || null })} /></label>
        </>}
      </fieldset>)}
      {!!edits.length && <button type="button" disabled={locked || edits.some(item => !item.text.trim())} onClick={() => void saveCorrection()}>保存校对</button>}
      {dirty && <p className="warning">校对内容已变更，保存校对后才能确认导入。</p>}
      <label className="check"><input type="checkbox" checked={confirmed} disabled={locked || dirty || preview.status !== "ready"}
        onChange={event => setConfirmed(event.target.checked)} />确认以上正文、类型和顺序；未显示的发送者与时间保持未知</label>
      <button type="button" disabled={locked || dirty || !confirmed || preview.status !== "ready"} onClick={() => void commit()}>确认导入正文</button>
    </section>}
    <section aria-label="已导入截图或笔记"><h3>已导入截图或笔记</h3>
      {!batches.length && <p>还没有已确认的截图或笔记。</p>}
      {batches.map(item => <p key={item.batch_id}>已导入 {item.unit_count} 条正文 · {item.image_count} 张截图</p>)}
      {cursor && <button type="button" disabled={locked || historyBusy} onClick={() => void loadMore()}>加载更早批次</button>}
      {!!batches.length && <p className="muted">已导入，暂不可跨来源整理；混合检索接入后可逐项选择。</p>}
    </section>
  </section>;
}
