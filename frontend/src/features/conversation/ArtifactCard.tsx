import { useEffect, useRef, useState, type MouseEvent } from "react";
import type { LocalApiClient } from "../../api/client";
import type { AgentMessage } from "../../conversation-types";
interface Preview { body: string; bodyName: string; referenceName: string }
export function ArtifactCard({ api, message }: { api: LocalApiClient; message: AgentMessage }) {
  const [preview, setPreview] = useState<Preview | null>(null);
  const [error, setError] = useState("");
  const [downloading, setDownloading] = useState(false);
  const downloadLock = useRef(false);
  const epoch = useRef(0);
  const urls = useRef(new Map<string, number>());
  useEffect(() => {
    const token = ++epoch.current;
    setPreview(null); setError(""); setDownloading(false); downloadLock.current = false;
    void api.documentTextPair(message.document_id!, message.version_id!).then(pair => {
      if (token !== epoch.current) return;
      if (pair.document_id !== message.document_id || pair.version_id !== message.version_id) throw new Error("artifact_version_mismatch");
      // Keep only the document body for reading; reference text is fetched at download time.
      setPreview({ body: pair.body.text, bodyName: pair.body.filename, referenceName: pair.reference.filename });
    }).catch(() => { if (token === epoch.current) setError("暂时无法读取这份文档，可点击下载重试。"); });
    return () => { epoch.current++; urls.current.forEach((timer, url) => { clearTimeout(timer); URL.revokeObjectURL(url); }); urls.current.clear(); };
  }, [api, message.document_id, message.version_id]);
  async function download(event: MouseEvent<HTMLAnchorElement>, kind: "body" | "reference") {
    event.preventDefault();
    if (downloadLock.current) return;
    const token = epoch.current; downloadLock.current = true; setDownloading(true); setError("");
    try {
      const pair = await api.documentTextPair(message.document_id!, message.version_id!);
      if (token !== epoch.current) return;
      if (pair.document_id !== message.document_id || pair.version_id !== message.version_id) throw new Error("artifact_version_mismatch");
      const file = pair[kind];
      const url = URL.createObjectURL(new Blob(["\uFEFF", file.text], { type: "text/plain;charset=utf-8" }));
      const timer = window.setTimeout(() => { URL.revokeObjectURL(url); urls.current.delete(url); }, 1000); urls.current.set(url, timer);
      const link = document.createElement("a"); link.href = url; link.download = file.filename; document.body.appendChild(link); link.click(); link.remove();
    } catch { if (token === epoch.current) setError("下载未完成，请重新打开这份文档后重试。"); }
    finally { if (token === epoch.current) { downloadLock.current = false; setDownloading(false); } }
  }
  return <section className="chat-artifact" aria-label={`整理成果 第${message.artifact_revision ?? 1}版`}>
    <div className="chat-card-heading"><span aria-hidden="true">▤</span><div><h3>第 {message.artifact_revision ?? 1} 版整理</h3><p>两份 TXT 来自同一份保留版本，可另存到本地。</p></div><span className="chat-badge">已完成</span></div>
    {preview ? <details><summary>查看整理内容</summary><pre className="chat-payload">{preview.body}</pre></details> : !error && <p role="status">正在读取保留版本…</p>}
    <div className="chat-file-links"><a href="#" download={preview?.bodyName ?? "整理内容.txt"} aria-disabled={downloading} onClick={event => void download(event, "body")}>下载整理内容.txt</a><a href="#" download={preview?.referenceName ?? "核查参考.txt"} aria-disabled={downloading} onClick={event => void download(event, "reference")}>下载核查参考.txt</a></div>
    {downloading && <p role="status">正在读取这份保留版本并准备下载…</p>}{error && <p role="alert">{error}</p>}
  </section>;
}
