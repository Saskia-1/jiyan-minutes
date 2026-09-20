import type { ComponentType } from "react";

// Defer missing-production-module failures until each test runs. No stub or
// implementation is supplied: when the real file exists its exports are loaded.
const modules = import.meta.glob(["../features/setup/SourceSetup.tsx", "../features/setup/ConsentCard.tsx", "../features/setup/VerificationWizard.tsx", "../App.tsx"]);
export async function loadSubject(name: "SourceSetup" | "ConsentCard" | "VerificationWizard" | "App") {
  const path = name === "App" ? "../App.tsx" : `../features/setup/${name}.tsx`;
  const load = modules[path];
  if (!load) throw new Error(`RED: production ${name} is not implemented`);
  return await load() as Record<string, ComponentType<any>>;
}
