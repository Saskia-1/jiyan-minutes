import { useEffect, useState } from "react";
import type { LocalApiClient } from "../../api/client";
import { deletionCountKeys, type DeletionCountKey, type DeletionPlan } from "../../types";

const COUNT_LABELS: Record<DeletionCountKey, string> = {
  documents: "文档", versions: "版本历史", document_artifacts: "文档导出记录",
  generation_jobs: "AI 生成任务", generation_consents: "AI 授权记录",
  generation_requests: "AI 请求用量记录",
  generation_previews: "AI 外发预览", cache_entries: "AI 缓存项",
  export_batches: "导出批次", export_jobs: "导出任务", export_consents: "导出授权记录",
  snapshots: "本地快照", messages: "无其他引用的本地消息", managed_files: "受管本地文件",
  content_batches: "截图／笔记批次", content_units: "正文条目", content_images: "本机原始截图",
};

export function DeletionControl({ api, targetKind, targetId, label, disabled = false, onDeleted, preserveDocuments = false }: {
  api: LocalApiClient; targetKind: "document" | "batch" | "content_batch"; targetId: string; label: string;
  preserveDocuments?: boolean; disabled?: boolean; onDeleted: () => void;
}) {
  const [includeFiles, setIncludeFiles] = useState(false);
  const [plan, setPlan] = useState<DeletionPlan | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");

  useEffect(() => { setPlan(null); setConfirmed(false); setError(""); setStatus(""); }, [targetKind, targetId]);

  function changeInclude(value: boolean) {
    setIncludeFiles(value); setPlan(null); setConfirmed(false); setError(""); setStatus("");
  }
  async function prepare() {
    if (busy || disabled) return;
    setBusy(true); setError(""); setStatus(""); setConfirmed(false);
    try { setPlan(await (preserveDocuments ? api.prepareDeletion(targetKind, targetId, true, true) : api.prepareDeletion(targetKind, targetId, includeFiles))); }
    catch { setPlan(null); setError("无法生成删除影响清单；未删除任何数据或文件。"); }
    finally { setBusy(false); }
  }
  async function remove() {
    if (!plan || !confirmed || busy || disabled) return;
    if (Date.parse(plan.expires_at) <= Date.now()) {
      setPlan(null); setConfirmed(false); setError("删除计划已过期，请重新查看影响。"); return;
    }
    setBusy(true); setError(""); setStatus("");
    try {
      const result = await api.confirmDeletion(plan.plan_id, plan.fingerprint);
      setStatus(result.state === "complete" ? "删除完成。" : "数据库删除完成；受管文件清理将在下次启动重试。");
      setPlan(null); setConfirmed(false); window.dispatchEvent(new Event("minutes-raw-cleared")); onDeleted();
    } catch { setError("删除未完成；目标可能已变化，请重新查看影响。"); }
    finally { setBusy(false); }
  }

  return <section className="deletion-control" aria-label={label}>
    <h5>{label}</h5>
    <p>{preserveDocuments ? "仅删除此批原文与内部副本，保留文档、修改和版本；清理后原文依据不可查看。先查看影响，再确认删除。" : "先查看影响清单，再单独确认。此操作不会删除上游聊天。"}</p>
    {!preserveDocuments && <label className="check"><input type="checkbox" checked={includeFiles} disabled={busy || disabled}
      onChange={event => changeInclude(event.target.checked)} />同时删除受管本地文件</label>}
    {targetKind === "content_batch" && <p className="muted">删除此导入批次时，原始截图会一并从本机私有目录删除；上方选项仅控制已导出的受管文件。</p>}
    <button type="button" disabled={busy || disabled} onClick={() => void prepare()}>查看删除影响</button>
    {plan && <div className="deletion-impact" aria-label="删除影响清单">
      <p>本计划将影响：</p><ul>{deletionCountKeys.filter(key => plan.counts[key] > 0).map(key =>
        <li key={key}>{COUNT_LABELS[key]}：{plan.counts[key]}</li>)}</ul>
      <ul>{plan.effects.map(effect => <li key={effect}>{effect}</li>)}</ul>
      <p>计划指纹：<code>{plan.fingerprint}</code></p>
      <p>有效至：<time dateTime={plan.expires_at}>{plan.expires_at}</time></p>
      <label className="check"><input type="checkbox" checked={confirmed} disabled={busy || disabled}
        onChange={event => setConfirmed(event.target.checked)} />我已核对上述影响，确认执行这一次删除</label>
      <button type="button" disabled={!confirmed || busy || disabled} onClick={() => void remove()}>确认删除</button>
    </div>}
    {error && <p role="alert">{error}</p>}
    {status && <p role="status">{status}</p>}
  </section>;
}
