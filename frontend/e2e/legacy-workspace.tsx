// Development-only regression entry. It is not a production Vite build input.
import { useMemo } from "react";
import { createRoot } from "react-dom/client";
import { createClient } from "../src/api/client";
import { ChatImportPanel } from "../src/features/imports/ChatImportPanel";
import { ComplianceGate } from "../src/features/imports/ComplianceGate";
import "../src/styles.css";
function LegacyWorkspace() {
  const api = useMemo(createClient, []);
  return <main className="simple-main"><h1>记言 · Minutes</h1><ComplianceGate>
    <ChatImportPanel compact outputRootId="" api={api} />
  </ComplianceGate></main>;
}
createRoot(document.getElementById("root")!).render(<LegacyWorkspace />);
