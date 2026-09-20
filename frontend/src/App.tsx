import { useEffect, useMemo, useState } from "react";
import { createClient } from "./api/client";
import { VerificationWizard } from "./features/setup/VerificationWizard";
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
  return <main><header><p className="eyebrow">CHAT TO HOW-TO · 本地优先</p><h1>先确认来源，再验证范围。</h1>
    <p>QQ 与微信独立验证。文档和合成测试不能代替真实设备验收。</p></header>
    {failed ? <p role="alert">无法读取本地配置，请检查本地服务后重新打开。</p> : options ? <VerificationWizard setupOptions={options} api={api} /> : <p role="status">正在读取本地配置…</p>}
  </main>;
}
