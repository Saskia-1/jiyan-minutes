import type { DocumentVersion } from "../../types";

const LABELS: Record<DocumentVersion["origin"], string> = {
  ai_generated: "AI 原始", user_edited: "用户编辑", restored: "恢复副本",
};

export function VersionHistory({ versions, currentVersionId, disabled, onRestore }: {
  versions: DocumentVersion[]; currentVersionId: string; disabled: boolean;
  onRestore: (versionId: string) => void;
}) {
  return <section aria-label="版本历史" className="version-history"><h5>版本历史</h5><ol>
    {[...versions].reverse().map(version => <li key={version.version_id}>
      <strong>版本 {version.ordinal}</strong> · {LABELS[version.origin]} · <time dateTime={version.created_at}>{new Date(version.created_at).toLocaleString("zh-CN")}</time>
      {version.change_summary && <p>{version.change_summary}</p>}
      {version.version_id === currentVersionId ? <small>当前版本</small> :
        <button type="button" disabled={disabled} onClick={() => onRestore(version.version_id)}>从版本 {version.ordinal} 创建恢复版本</button>}
    </li>)}
  </ol></section>;
}
