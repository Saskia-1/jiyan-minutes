# Task 5 frontend RED handoff

> **Historical RED snapshot, superseded by the GREEN implementation.** The
> production frontend now exists. On 2026-09-11 the final local gate observed
> 5/5 Vitest files and 141/141 tests passing, `tsc --noEmit` passing, and a
> successful Vite production build. Playwright still was only enumerated (two
> synthetic scenarios: desktop and 360px); no browser E2E or real QQ/WeChat
> export was run as part of this frontend task. The remainder of this document
> preserves the original RED evidence and must not be read as current status.

This change contains test/build scaffolding, synthetic fixtures and acceptance
tests only. There is no production App, SourceSetup, ConsentCard,
VerificationWizard, API client, stylesheet or main entry implementation. No
backend file was changed. The controller separately authorized the narrow root
`.gitignore` addition for `.pnpm-store/`; no cache or user file was deleted.

Read before authoring: the complete v3 specification, traceability amendments,
Task 5 brief, locked source contracts, committed Task 4 API/service tests and
TDD/writing-good-tests instructions. The controller approved the public
`GET /api/setup-options` contract described below while Task 4 GREEN was running.
The backend's final endpoint and browser behavior still require integration
verification; this report does not claim a completed UI or real-source export.

## Verification actually run

Commands run from `frontend/` with the bundled native Node/pnpm on PATH:

| Command | Observed result |
| --- | --- |
| `pnpm test -- --run` | Initial 56 component cases all failed because production components are absent; no skipped cases. |
| `pnpm exec vitest run --reporter=dot` | Secret-consent revision: 74 cases collected, 60 component cases individually RED and 14 fixture self-checks passed; final rerun 1.05 s, across four files. |
| `pnpm typecheck` | Exit 0 after final changes; verifies scaffold/test code only. |
| `pnpm build` | Exit 1 as expected: `/src/main.tsx` absent; no production entry was added to mask RED. |
| `pnpm test:e2e -- --list` | Exit 0; two Chrome projects collected (1280px and 360px). |
| Python `ast.parse` of `e2e/synthetic_server.py` | Valid syntax; the server module was not executed. |
| `git diff --check` | Exit 0. |

`loadSubject.ts` uses an explicit import glob to defer absent production-module
failures until each test executes. It supplies no component stub or mock. Once
real component files exist, their real exports execute the remaining assertions.
The temporary loose component-export cast keeps test scaffolding typecheckable
before those files exist; production prop typing remains a GREEN obligation.

The first direct-import run failed at Vite import analysis before collecting
cases. That was replaced by deferred loading, producing the 60 individual RED
failures above. RED proves absence, not that the unexecuted behavioral assertions
have passed. GREEN must run every case and review helper compatibility.

No local HTTP server or browser was started in this RED-only task. The actual
synthetic Chrome E2E remains **not run**. Playwright collection is not evidence
that a file was created. Browser binary downloads, real adapters, real chats,
Keychain access, screenshots, videos, traces and provider calls were not used.

## Dependencies and provenance

Runtime verified locally: Node **24.19.0 arm64**, pnpm **11.19.0**. System Chrome
executable exists. Package versions/engine/peer metadata were queried from the
official npm registry through an approved network tool call; installation was
separately approved. Exact installed dependencies:

| Packages | Versions |
| --- | --- |
| react / react-dom | 19.3.0 / 19.3.0 |
| typescript | 7.0.2 |
| vite / @vitejs/plugin-react | 8.3.0 / 6.1.1 |
| vitest / jsdom | 5.0.0 / 30.0.1 |
| @testing-library/react / dom | 16.3.3 / 10.4.1 |
| @testing-library/user-event / jest-dom | 14.6.7 / 7.0.1 |
| @playwright/test | 1.63.0 |
| @types/react / react-dom / node | 19.3.0 / 19.3.0 / 24.13.1 |

Metadata source pattern: `https://registry.npmjs.org/<URL-encoded-package>/latest`;
Node 24 types were checked at the explicit `/24.13.1` version endpoint.
Examples: [React metadata](https://registry.npmjs.org/react/19.3.0),
[Vite metadata](https://registry.npmjs.org/vite/8.3.0),
[Vitest metadata](https://registry.npmjs.org/vitest/5.0.0),
[Playwright metadata](https://registry.npmjs.org/@playwright%2Ftest/1.63.0).

`pnpm install --ignore-scripts --store-dir .pnpm-store` completed successfully,
creating `pnpm-lock.yaml` and installing 114 packages. Browser download was
disabled with `PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1`. Lifecycle scripts were not
executed. pnpm added three exact minimum-release-age exemptions for the selected
Vite and React type versions; those are recorded, not a wildcard policy change.
Normal scripts use `verifyDepsBeforeRun: false` so running tests cannot silently
perform another network install. The local store remains ignored.

A later `--offline --frozen-lockfile` attempt unexpectedly made pnpm 11's
supply-chain verification request registry metadata despite offline mode. Those
requests failed under network restrictions; the attempt was immediately
interrupted (exit 130). It is **not** reported as a successful offline lock
reproduction. Initial installed versions and the committed-intended lock are
real; an isolated frozen installation remains a GREEN/release verification step.

## Acceptance coverage and implementation seams

- `SourceSetup.test.tsx`: both sources separately exercise all seven status
  labels, keep the other source blocked, display synthetic evidence without
  ready, accept keyboard activation and expose no upload/dangerous-action button.
- `ConsentCard.test.tsx`: all six disclosure regions independently require a
  checkbox; six selection fields, timezone, public scope fingerprint and all
  three versions are required. Each selection/version/timezone/fingerprint or
  disclosure revision change resets confirmation. Refusal stays blocked. QCE
  copied/re-signed-client conflict and TraceMemo SIP/version/license blockers
  disable confirmation even when every checkbox is checked.
- `VerificationWizard.test.tsx`: real components with an HTTP-boundary fixture
  select the second duplicate-name conversation and hand-check UTC instants,
  exactly six selection keys, timezone, challenge, confirmation and CSRF.
  A 409 after server-side nonce/version invalidation clears consent without
  auto-retry. Refreshed versions invalidate checks. Secret success/failure sends
  exactly one POST to `/api/sources/qq/secret-consents` containing only operation
  and selected account, then exactly one POST to `/secret-operations` containing
  the same scope, the issued opaque challenge, `confirmed: true`, and value.
  Both require the session CSRF token. No value is allowed in any other request
  body, URL or header. It clears the password control while the response is pending, and never
  stores or shows the token in DOM text, browser storage, URL or console calls.
  No WeChat DB-key control exists. Missing approved roots stop the flow.
- `fixtures.test.ts`: 14 executed infrastructure self-checks prove the HTTP
  fixture cannot manufacture success for a direct secret operation, wrong
  source/account/operation/challenge, missing confirmation/value, extra fields,
  GET requests, a value in consent/get/delete, or replay (including while a
  failed operation is pending). Three legal scoped operations exercise the
  positive path. Challenges are issued dynamically and consumed before awaiting
  the response; request order is recorded. These passing helper tests are not
  UI GREEN evidence: all 60 production-component cases remain RED.
- `e2e/p0-verification.spec.ts`: real local backend through Vite's same-origin
  proxy; explicitly injected synthetic adapter; actual narrow-range JSONL,
  hash/count/range and 0600/0700 checks; only a newly created file can satisfy the
  test. It rejects external browser requests, checks keyboard reachability/focus
  ring and no horizontal overflow at 360px, disables screenshots/traces/videos,
  and never uses route mocks to manufacture API success or file evidence.

Public `GET /api/setup-options` shape agreed with controller:

```typescript
{
  approved_output_roots: [{ output_root_id: string, label: string }],
  source_disclosures: {
    qq: { revision: string, sections: {
      source_version: string, access_impact: string, scope_overread: string,
      storage_cleanup: string, permissions_network: string, action_stop: string
    }, blockers: string[] },
    wechat: { /* same disclosure structure */ }
  }
}
```

This contains no path, secret or configuration nonce. `revision` versions the
public disclosure text only. Browser selection uses opaque
`conversation_selection_id` and `output_root_id`; actual IDs, versions/nonce and
operation binding are resolved by the backend. The public `scope_fingerprint`
and fresh challenge connect the consent card to that binding. Do not introduce a
browser-supplied nonce or raw stable upstream ID to satisfy wording in the brief.

Test-first component props (incidental names may be aligned during GREEN):

```typescript
SourceSetup({ sources, reports, onSourceChange })
ConsentCard({ source, selection, timezone, disclosure, scopeFingerprint,
              onConfirm, onDecline })
VerificationWizard({ setupOptions })
// default App export fetches /api/setup-options and passes public metadata on.
```

The synthetic E2E Python runner expects the real `create_app` factory's
`setup_options` injection plus `run_jobs=True`. It creates a fresh private
temporary root, uses only synthetic normalized messages, denies outbound socket
connections, and configures the source through its real public API. It does not
patch backend handlers or pre-populate a success report. Its retained directory
contains synthetic data only and is never copied into this repository.

## GREEN integration points already reported

1. Browser same-origin GETs normally lack Origin; JavaScript cannot reliably
   synthesize that forbidden header. The real backend must support its legitimate
   browser GET flow while retaining exact Host checks, rejecting hostile nonempty
   Origin and requiring Origin/CSRF for mutations. HTTP unit mocks do not prove it.
2. `run_jobs=True` must actually schedule verification; the in-progress backend
   factory currently discards this argument. E2E must not manually call `.run()`
   to hide a broken browser workflow.
3. The P0 service supports one conversation per job. This UI uses one opaque
   selection; multiple-conversation export remains the P1 scope from the phase
   plan, not an invented incompatible Task 4 payload.
4. Validate the final OpenAPI/public schemas and disclosure facts when Task 4
   GREEN settles. Six-class render fixtures are not an audit of a real tool or
   proof that its route is safe/approved.
