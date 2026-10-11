import { useEffect, useRef, useState } from "react";
import type { LocalApiClient } from "../../api/client";
import type { HowToDocument } from "../../types";
import { ResultView } from "../generation/GenerationPanel";
import { VersionHistory } from "./VersionHistory";
import { editableDocumentText, editableTitle, restoreDocumentTitleMarker } from "./documentText";

export { editableDocumentText as documentText } from "./documentText";

const TITLE_MARKER_ERROR = "请保留文档标题标记；你的未保存内容仍完整保留。";

export function DocumentResult({ api, generationJobId, initialDocument = null }: {
  api: LocalApiClient; generationJobId?: string; initialDocument?: HowToDocument | null;
}) {
  const [doc, setDoc] = useState(initialDocument);
  const [editing, setEditing] = useState(false);
  const [previewing, setPreviewing] = useState(true);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [format, setFormat] = useState<"markdown" | "docx">("docx");
  const [sources, setSources] = useState(false);
  const [notice, setNotice] = useState("");
  const creation = useRef<Promise<HowToDocument> | null>(null);
  useEffect(() => {
    let active = true;
    if (!initialDocument && generationJobId) {
      setBusy(true);
      // Preserve one creation request across StrictMode effect replay.
      creation.current ||= api.createDocument(generationJobId);
      creation.current.then(value => { if (active) setDoc(value); })
        .catch(() => { if (active) setError("文档保存失败，请重试保存；生成结果仍在本地保留。"); })
        .finally(() => { if (active) setBusy(false); });
    }
    return () => { active = false; };
  }, [api, generationJobId, initialDocument]);
  useEffect(() => { if (doc) setDraft(editableDocumentText(doc.current_version.content)); }, [doc?.current_version.version_id]);

  async function save() {
    if (!doc || busy || !draft.trim()) return;
    const title = editableTitle(draft);
    if (!title) { setError(TITLE_MARKER_ERROR); return; }
    setBusy(true); setError(""); setNotice("");
    try {
      const content = { ...doc.current_version.content, user_text: draft, title: title.slice(0, 500) };
      setDoc(await api.createDocumentVersion(doc.document_id, doc.current_version.version_id, content, "用户确认完整文本"));
      setEditing(false); setPreviewing(true); setNotice("已确认修改，预览和下载使用新版本。AI 原始版本仍保留。");
    } catch { setError("修改未保存，请保留文本后重试。原版本未被覆盖。"); }
    finally { setBusy(false); }
  }
  async function download() {
    if (!doc || busy) return;
    setError(""); setNotice(""); setBusy(true);
    const filename = `${doc.current_version.content.title.replace(/[\\/:*?"<>|\x00-\x1f]/g, "_").slice(0, 100) || "聊天整理"}.${format === "docx" ? "docx" : "md"}`;
    try {
      const picker = (window as unknown as { showSaveFilePicker?: (options: unknown) => Promise<{ createWritable(): Promise<{ write(blob: Blob): Promise<void>; close(): Promise<void> }> }> }).showSaveFilePicker;
      // Invoke the picker during user activation, before any network await.
      const handle = picker ? await picker({ suggestedName: filename }) : null;
      const blob = await api.downloadDocument(doc.document_id, doc.current_version.version_id, format, sources ? "with_sources" : "body_only");
      if (handle) {
        const writer = await handle.createWritable(); await writer.write(blob); await writer.close();
        setNotice("已保存到你选择的位置。");
      } else {
        const url = URL.createObjectURL(blob); const anchor = window.document.createElement("a");
        anchor.href = url; anchor.download = filename; window.document.body.append(anchor); anchor.click(); anchor.remove();
        setTimeout(() => URL.revokeObjectURL(url), 60_000);
        setNotice("已发起浏览器下载，请在浏览器下载列表或下载文件夹查看。");
      }
    } catch (caught) { if (!(caught instanceof Error && caught.name === "AbortError")) setError("下载未完成，请重试。"); }
    finally { setBusy(false); }
  }
  return <>
    <section className="panel" aria-label="自行修改和补充"><h2>3. 是否自行修改和补充？</h2>
      <div className="choice-row">
        <label className="check"><input type="radio" name="edit-choice" checked={!editing} disabled={busy} onChange={() => setEditing(false)} />否</label>
        <label className="check"><input type="radio" name="edit-choice" checked={editing} disabled={busy || !doc} onChange={() => setEditing(true)} />是</label>
      </div>
      {editing && <><label>完整文档<textarea className="full-document" value={draft} maxLength={100000} disabled={busy} onChange={event => setDraft(event.target.value)} /></label>
        <button type="button" disabled={busy || !draft.trim()} onClick={() => void save()}>确认修改</button>
        {error === TITLE_MARKER_ERROR && doc && <button type="button" disabled={busy} onClick={() => {
          setDraft(value => restoreDocumentTitleMarker(value, doc.current_version.content.title)); setError("");
        }}>恢复当前文档标题</button>}
        <p className="muted">可自由删改、补充和重排。确认后保存为用户版本，不覆盖 AI 原文。</p></>}
    </section>
    <section className="panel" aria-label="预览与下载"><h2>4. 预览与下载</h2>
      {!doc ? <p role="status">{busy ? "正在保存生成结果…" : "整理完成后，文档会出现在这里。"}</p> : <>
        <p className="muted">{doc.current_version.origin === "ai_generated" ? "AI 原始版本" : "用户版本 · 原始依据仅供对照，不代表已核验编辑内容"} · 版本 {doc.current_version.ordinal}</p>
        <button type="button" aria-expanded={previewing} onClick={() => setPreviewing(value => !value)}>预览结果</button>
        {previewing && <ResultView verified={doc.current_version.origin === "ai_generated"} value={{ result: doc.current_version.content, evidence: doc.source_evidence }} />}
        <div className="download-controls"><label>下载格式<select value={format} disabled={busy} onChange={event => setFormat(event.target.value as "markdown" | "docx")}>
          <option value="docx">Word（DOCX）</option><option value="markdown">Markdown</option></select></label>
          <label className="check"><input type="checkbox" checked={sources} disabled={busy} onChange={event => setSources(event.target.checked)} />附带原始依据</label>
          <button type="button" disabled={busy} onClick={() => void download()}>下载文档</button></div>
        {editing && <p className="muted">未确认的修改不影响当前预览和下载。</p>}
        <details><summary>历史版本</summary><VersionHistory versions={doc.versions} currentVersionId={doc.current_version.version_id} disabled={busy}
          onRestore={id => { setBusy(true); setError(""); void api.restoreDocumentVersion(doc.document_id, id).then(setDoc)
            .catch(() => setError("恢复失败，原版本仍保留。")).finally(() => setBusy(false)); }} /></details>
      </>}
      {!doc && error && generationJobId && <button disabled={busy} onClick={() => { setBusy(true); setError(""); void api.createDocument(generationJobId).then(setDoc).catch(() => setError("文档保存失败，请重试。")).finally(() => setBusy(false)); }}>重试保存</button>}
      {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    </section>
  </>;
}
