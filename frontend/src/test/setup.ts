import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, beforeEach, vi } from "vitest";

beforeEach(() => {
  localStorage.clear(); sessionStorage.clear();
  vi.stubGlobal("fetch", () => Promise.reject(new Error("unexpected_network_request")));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
