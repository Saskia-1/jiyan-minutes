import { useEffect, useMemo, useState } from "react";
import { createClient } from "./api/client";
import { ChatImportPanel } from "./features/imports/ChatImportPanel";
import { ComplianceGate } from "./features/imports/ComplianceGate";
import { RetentionNotice } from "./features/data/RetentionNotice";
import { ConversationWorkspace } from "./features/conversation/ConversationWorkspace";
import type { SetupOptions } from "./types";

export default function App() {
  const api = useMemo(createClient, []);
  const [options, setOptions] = useState<SetupOptions | null>(null);
  const [advanced, setAdvanced] = useState(false);
  useEffect(() => {
    let current = true;
    void api.setupOptions().then(value => { if (current) setOptions(value); }).catch(() => { /* The conversation service reports its own connectivity state. */ });
    return () => { current = false; };
  }, [api]);
  return <div className="conversation-app"><ComplianceGate><RetentionNotice api={api} /><ConversationWorkspace api={api} />
    <details className="chat-settings" onToggle={event => setAdvanced(event.currentTarget.open)}><summary>设置与高级工具</summary><div className="chat-settings-content"><h2>高级资料与文档工具</h2><p>这里保留导入选项、往期文档与数据清理。对话中的资料仅用于各自会话。</p>{advanced && (options ? <ChatImportPanel compact outputRootId={options.approved_output_roots[0]?.output_root_id ?? ""} api={api} /> : <p role="status">正在读取高级工具配置，请稍后重新展开。</p>)}</div></details>
  </ComplianceGate></div>;
}
