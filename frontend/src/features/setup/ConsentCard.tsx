import { useState } from "react";
import { sectionLabels, sourceTools, validTimezone, type Disclosure, type Selection, type Source } from "../../types";

export function ConsentCard({ source, selection, timezone, disclosure, scopeFingerprint, consentChallenge, expiresAt, onConfirm, onDecline, busy = false }: {
  source: Source; selection: Selection; timezone: string; disclosure: Disclosure; scopeFingerprint: string; consentChallenge: string; expiresAt: string;
  onConfirm: () => void; onDecline: () => void; busy?: boolean;
}) {
  const binding = JSON.stringify([selection, timezone, scopeFingerprint, source.source_id, source.account_id,
    source.client_version, source.tool_version, source.account_version, source.status, disclosure, consentChallenge, expiresAt]);
  const [approval, setApproval] = useState({ binding, checks: [] as string[], refused: false });
  const current = approval.binding === binding ? approval : { binding, checks: [], refused: false };
  // Persist invalidation: returning from scope B to scope A must not revive A's checks.
  if (approval.binding !== binding) setApproval(current);
  const valid = Object.values(selection).every(value => typeof value === "string" && !!value.trim()) &&
    source.source_id === selection.source_id && source.account_id === selection.account_id &&
    !!source.client_version && !!source.tool_version && !!source.account_version && !!scopeFingerprint && !!consentChallenge &&
    Number.isFinite(Date.parse(expiresAt)) && Date.parse(expiresAt) > Date.now() &&
    validTimezone(timezone) && Date.parse(selection.range_end) > Date.parse(selection.range_start) &&
    disclosure.source_id === source.source_id && disclosure.tool === sourceTools[source.source_id] &&
    !!disclosure.revision && /^[a-f0-9]{64}$/.test(disclosure.hash) && disclosure.risks.length > 0 &&
    Object.keys(sectionLabels).every(key => !!disclosure.sections[key as keyof typeof sectionLabels]?.trim()) &&
    disclosure.blockers.length === 0 && !["blocked", "unsupported", "error"].includes(source.status);
  const enabled = valid && current.checks.length === 6 && !current.refused && !busy;
  return <div className="consent-card">
    <h2>本次操作确认</h2>
    <p>只授权当前选定的来源、账号、会话、时间范围和保存位置。任一项变化后需重新确认。</p>
    <p>披露来源：{disclosure.source_id === "qq" ? "QQ" : "微信"} · 工具：{disclosure.tool} · 修订：{disclosure.revision}</p>
    <p>披露摘要（SHA-256）：<code>{disclosure.hash}</code></p>
    <p>确认有效期：<time dateTime={expiresAt}>{expiresAt}</time></p>
    <div aria-label="来源风险代码"><h3>来源风险代码</h3><ul>{disclosure.risks.map(risk => <li key={risk}><code>{risk}</code></li>)}</ul></div>
    {disclosure.blockers.map(blocker => <p className="warning" key={blocker}>{blocker}</p>)}
    {Object.entries(sectionLabels).map(([key, title]) => <section aria-label={title} key={key}>
      <h3>{title}</h3><p>{disclosure.sections[key as keyof typeof sectionLabels] || "披露信息缺失，不能开始。"}</p>
      <label className="check"><input type="checkbox" checked={current.checks.includes(key)} disabled={current.refused || busy}
        onChange={event => setApproval({ ...current, checks: event.target.checked ? [...current.checks, key] : current.checks.filter(item => item !== key) })} />
        我已阅读并确认：{title}</label>
    </section>)}
    {current.refused && <p role="status">已拒绝，操作已阻止。</p>}
    <div className="actions"><button type="button" disabled={!enabled} onClick={() => { if (enabled) onConfirm(); }}>确认并开始验证</button>
      <button type="button" disabled={busy || current.refused} onClick={() => { setApproval({ binding, checks: [], refused: true }); onDecline(); }}>拒绝本次操作</button></div>
  </div>;
}
