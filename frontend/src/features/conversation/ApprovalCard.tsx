import { useEffect, useState } from "react";
import type { AgentApproval } from "../../conversation-types";
export function ApprovalCard({ approval, busy, onApprove }: { approval: AgentApproval; busy: boolean; onApprove: () => void }) {
  const [confirmed, setConfirmed] = useState(false);
  const [now, setNow] = useState(Date.now());
  useEffect(() => { setConfirmed(false); setNow(Date.now()); const timer = window.setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(timer); }, [approval.approval_id]);
  const expired = Date.parse(approval.expires_at) <= now;
  const localReuse = approval.kind === "generation" && approval.provider_calls === 0 && approval.outbound_payloads.length === 0;
  return <section className="chat-approval" aria-label={localReuse ? "本地复用确认" : "外发授权"}>
    <p className="chat-card-kicker">需要你确认 · {approval.kind === "agent" ? "下一次工具决策" : "整理正文"}</p><h3>{localReuse ? "复用本地整理结果" : "核对本次外发内容"}</h3>
    {localReuse ? <p>本次直接复用本机已有整理结果，不会向外部模型发送内容，也不会调用模型。</p> : <><p>接收方：{approval.provider} · {approval.model}。本次最多 {approval.provider_calls} 次模型请求，输出上限 {approval.max_output_tokens} tokens，可能产生费用。</p>
    <p>以下是将发送的完整脱敏正文。脱敏不保证覆盖全部个人信息；请核对是否包含不应外发的内容。</p>
    {approval.outbound_payloads.map((payload, index) => <pre className="chat-payload" key={index} aria-label={`完整脱敏正文 ${index + 1}`}>{payload}</pre>)}</>}
    <label className="check"><input type="checkbox" checked={confirmed} disabled={busy || expired} onChange={event => setConfirmed(event.target.checked)} />{localReuse ? "我已知悉并确认复用本地结果" : "我已核对并授权本次外发"}</label>
    {expired && <p role="alert">本次授权已过期，请停止后重新发送要求。</p>}
    <button type="button" disabled={!confirmed || busy || expired} onClick={onApprove}>{localReuse ? "确认复用并继续" : "授权并继续"}</button>
    <p className="chat-small">{localReuse ? "本次仅在本机复用；后续需要调用模型时，会单独请你核对外发内容。" : "仅授权这一次发送；下一次模型请求会再次请你核对。"}</p>
  </section>;
}
