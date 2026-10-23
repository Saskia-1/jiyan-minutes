// Development-only integration entry for retained components. This is not a
// production Vite build input and does not change the application's homepage.
import { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import { createClient } from "../src/api/client";
import { VerificationWizard } from "../src/features/setup/VerificationWizard";
import { ComplianceGate } from "../src/features/imports/ComplianceGate";
import type { SetupOptions } from "../src/types";
import "../src/styles.css";

function ComponentHarness() {
  const api = useMemo(createClient, []);
  const [options, setOptions] = useState<SetupOptions | null>(null);
  useEffect(() => { void api.setupOptions().then(setOptions); }, [api]);
  return <main><h1>保留组件集成测试</h1><ComplianceGate>
    {options ? <VerificationWizard setupOptions={options} api={api} /> : <p role="status">正在读取本地配置…</p>}
  </ComplianceGate></main>;
}
createRoot(document.getElementById("root")!).render(<ComponentHarness />);
