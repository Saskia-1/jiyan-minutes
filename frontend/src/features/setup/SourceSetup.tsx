import { isTrustedRealReport, statusLabels, type Report, type Source, type SourceId } from "../../types";

export function SourceSetup({ sources, reports, onSourceChange, selectedSource = "", disabled = false }: {
  sources: Source[]; reports: Report[]; onSourceChange: (source: SourceId) => void;
  selectedSource?: SourceId | ""; disabled?: boolean;
}) {
  return <div className="source-grid">{sources.map(source => {
    const name = source.source_id === "qq" ? "QQ" : "微信";
    const sourceReports = reports.filter(report => report.source_id === source.source_id);
    const synthetic = sourceReports.some(report => report.evidence_class === "synthetic");
    // This component has no authority to choose among historical candidates.
    const trustedReal = sourceReports.length === 1 && isTrustedRealReport(sourceReports[0], source);
    const unverifiedReady = source.status === "ready" && !trustedReal;
    const status = unverifiedReady ? "documented_not_tested" : source.status;
    return <section key={source.source_id} aria-label={name} className="source-card">
      <h2>{name}</h2><p role="status">{statusLabels[status] ?? "运行错误"}</p>
      {synthetic && <p>合成测试证据，不代表真实来源已验证。</p>}
      {unverifiedReady && !synthetic && <p>缺少当前版本绑定的真实设备完整证据，保持未验证。</p>}
      <dl className="source-meta">
        <div><dt>客户端版本</dt><dd>{source.client_version ?? "未检测"}</dd></div>
        <div><dt>本地工具版本</dt><dd>{source.tool_version ?? "未检测"}</dd></div>
        <div><dt>账号数据版本</dt><dd>{source.account_version ?? "未检测"}</dd></div>
        <div><dt>令牌状态</dt><dd>{source.secret_configured ? "已保存" : "未保存"}</dd></div>
      </dl>
      <button type="button" disabled={disabled} aria-pressed={selectedSource === source.source_id} onClick={() => onSourceChange(source.source_id)}>选择 {name}</button>
    </section>;
  })}</div>;
}
