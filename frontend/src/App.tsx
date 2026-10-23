import { useEffect, useMemo, useState } from "react";
import { createClient } from "./api/client";
import { ChatImportPanel } from "./features/imports/ChatImportPanel";
import { ComplianceGate } from "./features/imports/ComplianceGate";
import { RetentionNotice } from "./features/data/RetentionNotice";
import type { SetupOptions } from "./types";

export default function App() {
  const api = useMemo(createClient, []);
  const [options, setOptions] = useState<SetupOptions | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let active = true;
    api.setupOptions().then(value => { if (active) setOptions(value); }).catch(() => { if (active) setFailed(true); });
    return () => { active = false; };
  }, [api]);
  return <main className="simple-main"><header><h1>聊天记录整理</h1><p>从聊天中整理观点、想法与方法，保存为一份有用的文档。</p></header>
    <ComplianceGate><RetentionNotice api={api} />
    {failed ? <p role="alert">无法读取本地配置，请检查本地服务后重新打开。</p> : options ? <ChatImportPanel compact outputRootId="" api={api} /> : <p role="status">正在读取本地配置…</p>}
    </ComplianceGate>
  </main>;
}
