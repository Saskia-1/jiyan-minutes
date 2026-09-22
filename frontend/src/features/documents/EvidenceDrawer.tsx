import type { ValidatedEvidence } from "../../types";

export function EvidenceDrawer({ evidence, label = "查看来源证据" }: { evidence: ValidatedEvidence[]; label?: string }) {
  return <details className="document-evidence"><summary>{label}（{evidence.length} 条）</summary>
    <section aria-label="文档来源证据"><ol>{evidence.map(item => <li key={item.message_id}>
      <div className="result-meta"><strong>{item.sender_name || item.sender_id || "未知发送者"}</strong>
        <time dateTime={item.timestamp || undefined}>{item.timestamp ? new Date(item.timestamp).toLocaleString("zh-CN") : "时间未知"}</time></div>
      <p>{item.quote}</p>
    </li>)}</ol></section>
  </details>;
}
