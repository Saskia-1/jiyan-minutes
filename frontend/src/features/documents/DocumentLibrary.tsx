import { useEffect, useState } from "react";
import type { LocalApiClient } from "../../api/client";
import type { HowToDocument } from "../../types";
import { DocumentEditor } from "./DocumentEditor";

export function DocumentLibrary({ api, outputRootId, onOpen }: { api: LocalApiClient; outputRootId: string; onOpen?: (document: HowToDocument) => void }) {
  const [documents, setDocuments] = useState<HowToDocument[]>([]);
  const [opened, setOpened] = useState<HowToDocument | null>(null);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function load(cursor: string | null) {
    // Injected test doubles from older panels may not yet implement recovery.
    // The production client always provides this method.
    if (typeof api.recoverDocuments !== "function" || busy) return;
    setBusy(true); setError("");
    try {
      const page = await api.recoverDocuments(cursor);
      setDocuments(existing => cursor === null ? page.documents : [
        ...existing,
        ...page.documents.filter(item => !existing.some(found => found.document_id === item.document_id)),
      ]);
      setNextCursor(page.next_cursor);
      setOpened(current => current && page.documents.find(item => item.document_id === current.document_id) || current);
    } catch { setError("无法恢复已生成文档；未更改本地数据。"); }
    finally { setBusy(false); }
  }

  useEffect(() => { void load(null); }, [api]);

  async function openLatest(documentId: string) {
    if (busy) return;
    setBusy(true); setError("");
    try {
      const latest = await api.document(documentId);
      setDocuments(items => items.map(item => item.document_id === documentId ? latest : item));
      if (onOpen) onOpen(latest); else setOpened(latest);
    } catch { setError("无法打开最新版本，请重试；当前文档未被更改。"); }
    finally { setBusy(false); }
  }

  function remove(documentId: string) {
    setDocuments(items => items.filter(item => item.document_id !== documentId));
    setOpened(current => current?.document_id === documentId ? null : current);
  }

  return <section aria-label="已生成文档" className="document-library">
    <h3>已生成文档</h3>
    {!documents.length && !busy && <p>当前会话还没有可恢复的文档。</p>}
    {documents.map(item => <article key={item.document_id}>
      <strong>{item.current_version.content.title}</strong>
      <p>最新版本 {item.current_version.ordinal} · 更新于 <time dateTime={item.updated_at}>{item.updated_at}</time></p>
      <button type="button" disabled={busy} onClick={() => void openLatest(item.document_id)}>
        {opened?.document_id === item.document_id ? "当前已打开" : "打开最新版本"}
      </button>
    </article>)}
    {nextCursor && <button type="button" disabled={busy} onClick={() => void load(nextCursor)}>加载更早文档</button>}
    {busy && <p role="status">正在恢复已生成文档…</p>}
    {error && <p role="alert">{error}</p>}
    {opened && <DocumentEditor key={`${opened.document_id}:${opened.current_version.version_id}`} api={api} initialDocument={opened}
      outputRootId={outputRootId} onDeleted={remove} />}
  </section>;
}
