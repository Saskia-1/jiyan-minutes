import { useEffect, useState } from "react";
import type { LocalApiClient } from "../../api/client";
import type { HowToDocument } from "../../types";
import { EvidenceDrawer } from "./EvidenceDrawer";
import { VersionHistory } from "./VersionHistory";
import { DeletionControl } from "../data/DeletionControl";

const ORIGIN_LABELS = {
  ai_generated: "AI 原始版本 · 证据已验证",
  user_edited: "用户编辑版本 · 不再视为原始模型输出",
  restored: "恢复版本 · 不覆盖历史，内容不再视为原始模型输出",
} as const;

export function DocumentEditor({ api, generationJobId, initialDocument = null, outputRootId = "", onDeleted }: {
  api: LocalApiClient; generationJobId?: string; initialDocument?: HowToDocument | null;
  outputRootId?: string; onDeleted?: (documentId: string) => void;
}) {
  const [document, setDocument] = useState<HowToDocument | null>(initialDocument);
  const [title, setTitle] = useState("");
  const [steps, setSteps] = useState<string[]>([]);
  const [summary, setSummary] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [format, setFormat] = useState<"markdown" | "docx">("markdown");
  const [sourceMode, setSourceMode] = useState<"body_only" | "with_sources">("body_only");
  const [artifact, setArtifact] = useState("");

  useEffect(() => {
    if (!document) return;
    setTitle(document.current_version.content.title);
    setSteps(document.current_version.content.steps.map(item => item.instruction));
    setSummary("");
  }, [document?.current_version.version_id]);

  useEffect(() => { setDocument(initialDocument); }, [initialDocument?.document_id, initialDocument?.current_version.version_id]);

  async function create() {
    if (busy || !generationJobId) return;
    setBusy(true); setError("");
    try { setDocument(await api.createDocument(generationJobId)); }
    catch { setError("无法创建本地可编辑文档；生成结果没有被改写。"); }
    finally { setBusy(false); }
  }

  async function save() {
    if (!document || busy || !title.trim() || steps.some(item => !item.trim())) return;
    setBusy(true); setError("");
    const content = { ...document.current_version.content, title: title.trim(),
      steps: document.current_version.content.steps.map((item, index) => ({ ...item, instruction: steps[index].trim() })) };
    try { setDocument(await api.createDocumentVersion(document.document_id, document.current_version.version_id, content, summary.trim())); }
    catch { setError("版本未保存；可能已有更新，请保留页面内容并重新载入后确认。"); }
    finally { setBusy(false); }
  }

  async function restore(versionId: string) {
    if (!document || busy) return;
    setBusy(true); setError("");
    try { setDocument(await api.restoreDocumentVersion(document.document_id, versionId)); }
    catch { setError("无法创建恢复版本；原历史版本保持不变。"); }
    finally { setBusy(false); }
  }

  async function exportCurrent() {
    if (!document || !outputRootId || busy) return;
    setBusy(true); setError(""); setArtifact("");
    try {
      const value = await api.exportDocument(
        document.document_id, document.current_version.version_id, format, sourceMode, outputRootId,
      );
      setArtifact(`已保存本地文件：${value.filename}（${value.byte_size} bytes）`);
    } catch { setError("文档导出失败；没有覆盖同名文件。"); }
    finally { setBusy(false); }
  }

  if (!document && !generationJobId) return <section className="document-editor" aria-label="文档编辑与历史">
    <p>这份文档已从本地列表移除。</p>
  </section>;
  if (!document) return <section className="document-editor" aria-label="文档编辑与历史">
    <h4>编辑与版本</h4><p>生成结果会先保留为 AI 原始版本；每次保存都追加用户版本，不覆盖原文。</p>
    <button type="button" disabled={busy} onClick={() => void create()}>{busy ? "正在创建…" : "创建可编辑文档"}</button>
    {error && <p role="alert">{error}</p>}
  </section>;

  const current = document.current_version;
  return <section className="document-editor" aria-label="文档编辑与历史">
    <h4>编辑与版本</h4><p className={current.evidence_status === "verified" ? "verified-badge" : "warning"}>{ORIGIN_LABELS[current.origin]}</p>
    <label>文档标题<input value={title} maxLength={500} disabled={busy} onChange={event => setTitle(event.target.value)} /></label>
    {steps.map((value, index) => <label key={current.content.steps[index].source_message_ids.join("|")}>步骤 {index + 1}
      <textarea value={value} maxLength={8_000} disabled={busy} onChange={event => setSteps(items => items.map((item, itemIndex) => itemIndex === index ? event.target.value : item))} />
    </label>)}
    <label>修改说明<input value={summary} maxLength={500} disabled={busy} onChange={event => setSummary(event.target.value)} /></label>
    <button type="button" disabled={busy || !title.trim() || steps.some(item => !item.trim())} onClick={() => void save()}>保存为新版本</button>
    {error && <p role="alert">{error}</p>}
    <VersionHistory versions={document.versions} currentVersionId={current.version_id} disabled={busy} onRestore={versionId => void restore(versionId)} />
    <section aria-label="文档导出" className="document-export"><h5>导出本地文档</h5>
      <label>导出格式<select value={format} disabled={busy} onChange={event => setFormat(event.target.value as "markdown" | "docx")}>
        <option value="markdown">Markdown UTF-8</option><option value="docx">Word DOCX</option>
      </select></label>
      <label>来源内容<select value={sourceMode} disabled={busy} onChange={event => setSourceMode(event.target.value as "body_only" | "with_sources")}>
        <option value="body_only">仅指南正文</option><option value="with_sources">指南与来源证据</option>
      </select></label>
      <button type="button" disabled={busy || !outputRootId} onClick={() => void exportCurrent()}>导出当前版本</button>
      {!outputRootId && <p className="warning">请先选择已批准的保存位置。</p>}
      {artifact && <p role="status">{artifact}</p>}
    </section>
    <DeletionControl api={api} targetKind="document" targetId={document.document_id}
      label="删除这份本地文档" disabled={busy}
      onDeleted={() => { onDeleted?.(document.document_id); setDocument(null); setArtifact(""); }} />
    <EvidenceDrawer evidence={document.source_evidence} />
  </section>;
}
