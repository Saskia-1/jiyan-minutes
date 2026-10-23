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
  return <main className="simple-main"><header><p className="brand-kicker">本地资料 · 自然语言整理</p><h1>记言 · Minutes</h1><p>添加聊天、截图或笔记，告诉记言你想整理什么。</p></header>
    <ComplianceGate><RetentionNotice api={api} />
    {failed ? <p role="alert">无法读取本地配置，请检查本地服务后重新打开。</p> : options ? <ChatImportPanel compact outputRootId="" api={api} /> : <p role="status">正在读取本地配置…</p>}
    </ComplianceGate>
  </main>;
}
