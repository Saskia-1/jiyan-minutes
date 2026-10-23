import { useEffect, useState } from "react";
import type { LocalApiClient } from "../../api/client";

export function RetentionNotice({ api }: { api: LocalApiClient }) {
  const [status, setStatus] = useState({ cleaned_batches: 0, pending_cleanups: 0 });
  useEffect(() => {
    let active = true;
    let previous = 0;
    const refresh = async () => {
      try {
        const value = await api.retentionStatus();
        if (!active) return;
        setStatus(value);
        if (value.cleaned_batches !== previous || value.pending_cleanups) window.dispatchEvent(new Event("minutes-raw-cleared"));
        previous = value.cleaned_batches;
      } catch { /* Raw endpoints still enforce expiration on the server. */ }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 30_000);
    return () => { active = false; clearInterval(timer); };
  }, [api]);
  return <>
    {status.cleaned_batches > 0 && <p role="status">本次运行已有 {status.cleaned_batches} 批原文到期停用，文档与版本仍保留。</p>}
    {status.pending_cleanups > 0 && <p role="alert">原文清理尚未完成，检索与生成已暂停；应用会继续重试清理。已有文档仍可查看与另存。</p>}
  </>;
}
