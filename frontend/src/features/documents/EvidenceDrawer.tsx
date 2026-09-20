import type { ValidatedEvidence } from "../../types";

export function EvidenceDrawer({ evidence }: { evidence: ValidatedEvidence[] }) {
  return <details className="document-evidence"><summary>查看来源证据（{evidence.length} 条）</summary>
    <section aria-label="文档来源证据"><ol>{evidence.map(item => <li key={item.message_id}>
      <div className="result-meta"><strong>{item.sender_name || item.sender_id || "未知发送者"}</strong>
        <time dateTime={item.timestamp}>{new Date(item.timestamp).toLocaleString("zh-CN")}</time></div>
      <p>{item.quote}</p><small>{item.conversation_id} · {item.message_id}</small>
    </li>)}</ol></section>
  </details>;
}
