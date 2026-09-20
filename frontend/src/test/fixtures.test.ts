import { expect, it } from "vitest";
import { installApi } from "./fixtures";

const scope = { operation: "secret_set", account_id: "synthetic-account" };
async function authorize(api: ReturnType<typeof installApi>) {
  if (!api.state.sessionCount) await api.fetch("/api/session");
}
async function post(api: ReturnType<typeof installApi>, endpoint: string, body: Record<string, unknown>, source = "qq") {
  await authorize(api);
  return api.fetch(`/api/sources/${source}/${endpoint}`, {
    method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": "synthetic-csrf" }, body: JSON.stringify(body),
  });
}
async function prepare(api: ReturnType<typeof installApi>, operation = "secret_set") {
  const response = await post(api, "secret-consents", { ...scope, operation });
  expect(response.status).toBe(200);
  const issued = await response.json();
  return { challenge: issued.consent_challenge as string, revision: issued.disclosure.revision as string, hash: issued.disclosure.hash as string };
}
const sealed = (value: { challenge: string; revision: string; hash: string }) => ({
  consent_challenge: value.challenge, disclosure_revision: value.revision, disclosure_hash: value.hash,
});

it("fixture rejects direct secret-operation without a previously issued challenge", async () => {
  const api = installApi();
  const response = await post(api, "secret-operations", { ...scope, consent_challenge: "unissued", confirmed: true,
    disclosure_revision: "fixture-secret-contract-1", disclosure_hash: "3".repeat(64), value: "synthetic-only" });
  expect(response.status).toBe(403);
  expect(api.secretConsents).toHaveLength(0);
});

for (const operation of ["secret_set", "secret_get", "secret_delete"]) {
  it(`fixture accepts only a fresh scoped ${operation} exchange and records its order`, async () => {
    const api = installApi();
    const grant = await prepare(api, operation);
    const body = { ...scope, operation, ...sealed(grant), confirmed: true, ...(operation === "secret_set" ? { value: "synthetic-only" } : {}) };
    expect((await post(api, "secret-operations", body)).status).toBe(204);
    expect(api.requests.filter(item => item.path.includes("/secret-")).map(item => `${item.method} ${item.path}`)).toEqual([
      "POST /api/sources/qq/secret-consents", "POST /api/sources/qq/secret-operations",
    ]);
    expect(api.secretConsents[0].consumed).toBe(true);
    expect((await post(api, "secret-operations", body)).status).toBe(403);
  });
}

for (const mismatch of ["source", "account", "operation", "challenge", "disclosure_revision", "disclosure_hash", "confirmation", "extra_field", "missing_value"] as const) {
  it(`fixture rejects ${mismatch} mismatch instead of manufacturing success`, async () => {
    const api = installApi();
    const grant = await prepare(api);
    const body: Record<string, unknown> = { ...scope, ...sealed(grant), confirmed: true, value: "synthetic-only" };
    if (mismatch === "account") body.account_id = "other-synthetic-account";
    if (mismatch === "operation") { body.operation = "secret_get"; delete body.value; }
    if (mismatch === "challenge") body.consent_challenge = "unissued";
    if (mismatch === "disclosure_revision") body.disclosure_revision = "stale-revision";
    if (mismatch === "disclosure_hash") body.disclosure_hash = "f".repeat(64);
    if (mismatch === "confirmation") body.confirmed = false;
    if (mismatch === "extra_field") body.extra = true;
    if (mismatch === "missing_value") delete body.value;
    expect((await post(api, "secret-operations", body, mismatch === "source" ? "wechat" : "qq")).status).toBe(403);
  });
}

it("fixture rejects values in consent preparation and in get/delete operations", async () => {
  const api = installApi();
  expect((await post(api, "secret-consents", { ...scope, value: "synthetic-only" })).status).toBe(403);
  for (const operation of ["secret_get", "secret_delete"]) {
    const grant = await prepare(api, operation);
    expect((await post(api, "secret-operations", { ...scope, operation, ...sealed(grant), confirmed: true, value: "synthetic-only" })).status).toBe(403);
  }
});

it("fixture consumes the challenge before a pending failed response and rejects replay", async () => {
  const api = installApi();
  const grant = await prepare(api);
  let finish!: (response: Response) => void;
  api.state.delaySecret = new Promise(resolve => { finish = resolve; });
  const body = { ...scope, ...sealed(grant), confirmed: true, value: "synthetic-only" };
  const pending = post(api, "secret-operations", body);
  // Request.json() is asynchronous; wait for the recorded operation, not wall-clock time.
  await expect.poll(() => api.secretConsents[0].consumed).toBe(true);
  expect((await post(api, "secret-operations", body)).status).toBe(403);
  finish(new Response(null, { status: 500 }));
  expect((await pending).status).toBe(500);
  expect((await post(api, "secret-operations", body)).status).toBe(403);
});

it("fixture rejects GET for both secret POST endpoints", async () => {
  const api = installApi();
  await authorize(api);
  for (const endpoint of ["secret-consents", "secret-operations"]) {
    expect((await api.fetch(`/api/sources/qq/${endpoint}`)).status).toBe(405);
  }
});
