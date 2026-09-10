# P0 Source Feasibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the privacy-preserving environment audit, source configuration, explicit-consent, and narrow real-export verification path for QQ and WeChat without installing or initializing an upstream tool automatically.

**Architecture:** A FastAPI backend owns source configuration, secrets, status transitions, and loopback-only HTTP access. A React setup flow exposes only non-secret metadata, records narrowly scoped consent, and drives a local verifier whose report contains counts and range checks but never message bodies or secret values.

**Tech Stack:** Python 3.11, FastAPI, Pydantic v2, httpx, SQLite, pytest; React 19, TypeScript, Vite, Vitest.

**Spec:** `chat_to_howto_codex_prompt_v3.md`

## Global Constraints

- Target macOS ARM64 and listen only on `127.0.0.1`.
- Keep QQ and WeChat status independent: `unconfigured`, `needs_user_action`, `documented_not_tested`, `ready`, `blocked`, `unsupported`, or `error`.
- Never read chat content during environment detection and never print source responses, chat bodies, tokens, keys, or complete environment variables.
- Never install or initialize QCE/TraceMemo, lower system protection, request Full Disk Access, change SIP, modify/re-sign clients, close clients, log in, or use a key without explicit operation-specific consent.
- All real-source endpoints must be loopback-only, reject redirects, and keep upstream tokens server-side.
- Real integration tests must emit only redacted counts, time-range verdicts, versions, and error codes.

---

### Task 1: Project foundation and safe environment inventory

**Files:**
- Create: `.gitignore`, `.env.example`, `Makefile`, `README.md`
- Create: `backend/pyproject.toml`, `backend/src/chat_to_howto/__init__.py`, `backend/src/chat_to_howto/config.py`, `backend/src/chat_to_howto/environment.py`
- Test: `backend/tests/test_environment.py`, `backend/tests/test_config.py`

**Interfaces:**
- Produces: `Settings.load() -> Settings`; `collect_environment(app_paths: Mapping[str, Path]) -> EnvironmentReport`; `EnvironmentReport.public_dict() -> dict[str, object]`.

- [ ] **Step 1: Write failing tests** that pass fixture `Info.plist` files and assert only OS/app/tool versions and architectures are returned; include assertions that home paths, usernames, environment values, and plist contents are absent.
- [ ] **Step 2: Run `python3 -m pytest backend/tests/test_environment.py backend/tests/test_config.py -q`** and verify failure because the package does not exist.
- [ ] **Step 3: Implement the minimum typed settings and metadata collectors**, using `platform`, `plistlib`, `shutil.which`, and fixed known app paths only; never enumerate chat/data directories.
- [ ] **Step 4: Re-run the two tests** and verify they pass.
- [ ] **Step 5: Add `.env.example` with empty secret fields and `.gitignore` rules** for `.env`, databases, exports, secrets, generated documents, caches, and third-party state.

### Task 2: Source state, consent, and secret storage boundaries

**Files:**
- Create: `backend/src/chat_to_howto/domain/sources.py`, `backend/src/chat_to_howto/domain/consent.py`
- Create: `backend/src/chat_to_howto/security/secrets.py`, `backend/src/chat_to_howto/security/redaction.py`
- Test: `backend/tests/domain/test_sources.py`, `backend/tests/domain/test_consent.py`, `backend/tests/security/test_secrets.py`, `backend/tests/security/test_redaction.py`

**Interfaces:**
- Produces: `SourceCapability`, `SourceStatus`, `ConsentScope`, `ConsentRecord`, `SecretStore.get/set/delete`, `redact_exception(exc) -> PublicError`.
- Consumes: `Settings` from Task 1.

- [ ] **Step 1: Write failing state-transition tests** proving a document review cannot set `ready`, changed client/tool/account versions invalidate prior verification, and explicit declined/unsafe requirements produce `blocked` without fallback.
- [ ] **Step 2: Write failing secret tests** proving secret values never appear in `repr`, serialized API data, logs, exception messages, or SQLite rows; use an in-memory test implementation.
- [ ] **Step 3: Run the focused tests** and verify expected import failures.
- [ ] **Step 4: Implement immutable Pydantic domain records, transition validation, scoped consent fingerprints, redaction, and a macOS Keychain-backed store** whose command arguments never expose values; add a `0600` file fallback only when explicitly enabled outside the source tree.
- [ ] **Step 5: Re-run the focused tests** and verify all transitions and non-disclosure assertions pass.

### Task 3: Loopback transport and source-adapter contracts

**Files:**
- Create: `backend/src/chat_to_howto/sources/base.py`, `backend/src/chat_to_howto/sources/transport.py`, `backend/src/chat_to_howto/sources/qce.py`, `backend/src/chat_to_howto/sources/tracememo.py`
- Test: `backend/tests/sources/test_transport.py`, `backend/tests/sources/test_qce_contract.py`, `backend/tests/sources/test_tracememo_contract.py`

**Interfaces:**
- Produces: `SourceAdapter.health`, `list_accounts`, `list_conversations`, `fetch_range`, `cancel`; normalized `ConversationRef`, `SourceMessage`, `FetchPage`, and `CompletenessEvidence`.
- Consumes: server-side `SecretStore`; locked contract facts from `docs/source-contracts.md`.

- [ ] **Step 1: Write failing SSRF tests** for non-loopback hosts, userinfo, unexpected schemes/ports, redirects, DNS resolution outside loopback, token-in-query, and response-size limits.
- [ ] **Step 2: Write failing adapter contract tests** with local mock transports for exact upstream methods, headers, timestamp units, cursor termination, duplicate cursors, same-second saturation, last-page failure, and cancellation.
- [ ] **Step 3: Run the source test directory** and confirm failures arise from missing transport/adapters.
- [ ] **Step 4: Implement the loopback client and normalized adapter interfaces** with `follow_redirects=False`, bounded timeouts/body sizes, typed errors, and no response-body logging.
- [ ] **Step 5: Implement only contract fields verified against a locked upstream version**; represent unknown pagination/completeness as `unknown` or `partial`, never infer success.
- [ ] **Step 6: Re-run source tests** and verify the complete matrix passes.

### Task 4: P0 verification service and setup API

**Files:**
- Create: `backend/src/chat_to_howto/db.py`, `backend/src/chat_to_howto/services/p0_verifier.py`, `backend/src/chat_to_howto/api/session.py`, `backend/src/chat_to_howto/api/sources.py`, `backend/src/chat_to_howto/main.py`
- Test: `backend/tests/services/test_p0_verifier.py`, `backend/tests/api/test_sources.py`, `backend/tests/api/test_security.py`

**Interfaces:**
- Produces: `GET /api/environment`; `GET/POST /api/sources`; `GET /api/sources/{id}/conversations`; `POST /api/sources/{id}/verification-jobs`; `GET /api/sources/{id}/verification-jobs/{job_id}`.
- Consumes: source adapters, consent fingerprints, and redaction from Tasks 2-3.

- [ ] **Step 1: Write failing API tests** proving an empty local database can list upstream conversation metadata, requests without a valid local session/CSRF header fail, secrets never round-trip, and a verification request requires explicit account/conversation/range/output consent.
- [ ] **Step 2: Write a failing verifier test** that streams synthetic normalized messages into a private temporary directory and asserts its public result exposes count/range/hash verdicts but no text/sender/conversation names.
- [ ] **Step 3: Run the focused API/service tests** and verify failure before implementation.
- [ ] **Step 4: Implement schema migrations, localhost Host/Origin enforcement, strict same-site session and CSRF handling, source configuration, and durable verification-job states.**
- [ ] **Step 5: Implement the redacted verifier** so cancellation and any exception cannot print or persist message bodies in the report.
- [ ] **Step 6: Re-run the focused tests and a log-capture scan** for synthetic secret/message sentinels.

### Task 5: Chinese P0 setup flow and operation confirmation card

**Files:**
- Create: `frontend/package.json`, `frontend/pnpm-lock.yaml`, `frontend/tsconfig.json`, `frontend/vite.config.ts`, `frontend/index.html`
- Create: `frontend/src/main.tsx`, `frontend/src/App.tsx`, `frontend/src/api/client.ts`, `frontend/src/features/setup/SourceSetup.tsx`, `frontend/src/features/setup/ConsentCard.tsx`, `frontend/src/features/setup/VerificationWizard.tsx`, `frontend/src/styles.css`
- Test: `frontend/src/features/setup/SourceSetup.test.tsx`, `frontend/src/features/setup/ConsentCard.test.tsx`, `frontend/e2e/p0-verification.spec.ts`

**Interfaces:**
- Consumes: Task 4 API; produces no browser-held upstream token.

- [ ] **Step 1: Write failing component tests** for independent QQ/WeChat badges, exact seven-state labels, mandatory disclosure fields, and disabled verification until account/conversation/range/output consent exists.
- [ ] **Step 2: Run `pnpm test -- --run` from `frontend`** and verify tests fail because components are absent.
- [ ] **Step 3: Implement the accessible Chinese setup UI**, keeping secret entry in a backend-mediated masked control and displaying tool/version/access/cache/permission/network/cleanup disclosures.
- [ ] **Step 4: Add the Playwright scenario** that uses a synthetic loopback source, chooses one conversation and narrow range, produces a local file, and shows a redacted verdict; label it synthetic, never `ready`.
- [ ] **Step 5: Run component and E2E tests** and verify exact status/consent behavior.

### Task 6: P0 evidence and real-device gate

**Files:**
- Create: `docs/environment.md`, `docs/source-contracts.md`, `docs/source-setup.md`, `docs/decisions.md`, `docs/acceptance-report.md`
- Create: `scripts/verify_real_source.py`
- Test: `backend/tests/scripts/test_verify_real_source.py`

**Interfaces:**
- Produces a redacted JSON result with `source`, versions, requested range, count, `range_ok`, completeness, and error code only.

- [ ] **Step 1: Write a failing CLI test** with sentinel message/token values and assert neither stdout nor stderr contains either sentinel on success or failure.
- [ ] **Step 2: Run the CLI test** and verify expected failure.
- [ ] **Step 3: Implement the CLI as a client of the local backend**, requiring a user-created verification-job ID instead of accepting secrets or conversation identifiers on the command line.
- [ ] **Step 4: Fill the five documents with observed evidence**, keeping each platform `documented_not_tested`, `needs_user_action`, or `blocked` until an actual user-approved export runs.
- [ ] **Step 5: Present one concrete confirmation card per upstream tool before any install/initialization**, then run the real verifier only after the user approves and makes the local selection.
- [ ] **Step 6: Record actual P0 results without chat content**; never promote mock results or port health to `ready`.

