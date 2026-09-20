# P4 Real Chat Ingestion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Import safe user-supplied chat data into the existing canonical Snapshot → Search → Generation → Document pipeline and add a strictly limited macOS Accessibility POC.

**Architecture:** Extend the existing deterministic backup parser, add a session-bound preview/commit coordinator, atomically persist manual imports beside existing export batches, and generalize snapshot authority without changing downstream business services. A separate opt-in Accessibility CLI reports aggregate AX capabilities and never becomes a production source adapter.

**Tech Stack:** Python 3.11, FastAPI, Pydantic v2, SQLite, standard-library ZIP/XML/CSV/JSON parsing, React 19, TypeScript, Vitest, Playwright.

**Spec:** `docs/superpowers/specs/2026-09-13-p4-real-chat-ingestion-design.md`

## Global Constraints

- All parsing is local; no AI provider or external conversion is called.
- Preserve QQ/QCE and WeChat/TraceMemo `blocked / NO-GO` enforcement.
- Do not access real chat content, real secrets, or paid providers.
- Do not disable SIP, attach/debug processes, scan memory, extract keys, inject code, or modify clients.
- Do not commit, push, reset, delete, or overwrite existing uncommitted work.
- Every production behavior is introduced after a focused failing test.

---

### Task 1: Deterministic TXT, Paste, and XLSX parsing

**Files:**
- Modify: `backend/src/chat_to_howto/domain/imports.py`
- Modify: `backend/src/chat_to_howto/services/manual_import.py`
- Modify: `backend/tests/services/test_manual_import.py`

**Interfaces:**
- Consumes: `BackupParser.preview(filename: str, payload: bytes)` and `BackupParser.parse(...)`.
- Produces: structural rows for `txt` and `xlsx`, plus explicit TXT dialect columns usable by the import coordinator.

- [x] Add failing tests for both supported TXT dialects, missing default-date-ready timestamp text, a hand-built XLSX worksheet, corrupt XLSX, formulas-as-inert-data, extension/magic mismatch, empty input, invalid encoding, row/column/ZIP size bounds, and existing CSV/JSON behavior.
- [x] Run `backend/.venv/bin/python -m pytest backend/tests/services/test_manual_import.py -q` and confirm failures are caused by unsupported TXT/XLSX.
- [x] Extend format/preview models and parser dispatch; parse XLSX through bounded archive/XML members without extraction or evaluation; parse only the two documented text dialects.
- [x] Re-run the focused parser suite and confirm it passes.

### Task 2: Canonical import analysis

**Files:**
- Modify: `backend/src/chat_to_howto/domain/imports.py`
- Create: `backend/src/chat_to_howto/services/chat_imports.py`
- Create: `backend/tests/services/test_chat_imports.py`

**Interfaces:**
- Consumes: exact bytes and structural rows from `BackupParser`.
- Produces: `ImportAnalysis`, `ImportIssue`, `ImportSample`, and normalized `StoredMessage` tuples with explicit provenance and stable IDs.

- [x] Add failing tests for suggested/manual mappings, explicit timezone conversion, default conversation/date, unknown sender, unresolved timestamp/conversation, message-type counts, bounded warnings/issues/samples, stable repeat IDs, duplicate IDs, and identity conflicts.
- [x] Run the focused service tests and verify expected missing-type/service failures.
- [x] Implement deterministic mapping and normalization into existing `MessageInput`; never infer missing time/conversation without explicit defaults.
- [x] Re-run parser and import-analysis tests.

### Task 3: Atomic import persistence and generalized snapshot authority

**Files:**
- Modify: `backend/src/chat_to_howto/migrations.py`
- Modify: `backend/src/chat_to_howto/repositories/chat.py`
- Modify: `backend/tests/test_migrations.py`
- Modify: `backend/tests/repositories/test_chat_repository.py`
- Modify: `backend/tests/services/test_search.py`
- Modify: `backend/tests/services/test_generation_scope.py`

**Interfaces:**
- Consumes: session digest, import metadata, conversations, `StoredMessage` values, and generated opaque handles.
- Produces: `commit_manual_import(...) -> SnapshotRef`, `resolve_manual_selections(...)`, and a shared authorized-snapshot query used by Search and Generation.

- [x] Add failing migration/repository tests for an atomic batch+snapshot+authority commit, immutable snapshots, fresh opaque handles, cross-session denial, identical reimport, duplicate membership, message conflict rollback, and imported-snapshot Search/Generation authority.
- [x] Run the focused repository/search/generation tests and verify the new schema/API is absent.
- [x] Add migration 11 and minimal repository methods; update Search and Generation SQL authority with a union of completed exports and completed imports.
- [x] Re-run all focused persistence and downstream service tests.

### Task 4: Session-bound preview/analyze/commit API

**Files:**
- Create: `backend/src/chat_to_howto/api/imports.py`
- Modify: `backend/src/chat_to_howto/services/chat_imports.py`
- Modify: `backend/src/chat_to_howto/main.py`
- Create: `backend/tests/api/test_imports.py`
- Modify: `backend/tests/api/test_security.py`

**Interfaces:**
- Produces: bounded JSON `POST /api/imports/previews`, confirmation/commit and recovery routes, and `ImportCommitView` containing batch/snapshot/range/timezone/conversation handles.
- Security: Host/Origin/session/CSRF, content-length and streamed byte limits, constant-time preview fingerprint binding, TTL, bounded in-memory entries, one-time commit, and safe errors.

- [x] Add failing API tests for file/paste preview, manual mapping, detailed counts, confirmation, session/CSRF/expiry/replay, oversized/chunked bodies, malformed formats, missing fields, and no Provider calls.
- [x] Run the focused API tests and confirm 404/missing route failures.
- [x] Implement the coordinator cache and routes, wire them into `create_app`, and return only basenames/opaque provenance.
- [x] Re-run API/security and service tests.

### Task 5: Import UI reusing the existing search/generation workspace

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/api/client.test.ts`
- Create: `frontend/src/features/imports/ChatImportPanel.tsx`
- Create: `frontend/src/features/imports/ChatImportPanel.test.tsx`
- Modify: `frontend/src/features/search/ChatSearchPanel.tsx`
- Modify: `frontend/src/features/search/ChatSearchPanel.test.tsx`
- Modify: `frontend/src/features/setup/VerificationWizard.tsx`
- Modify: `frontend/src/features/setup/VerificationWizard.test.tsx`
- Modify: `frontend/src/styles.css`

**Interfaces:**
- Consumes: preview/analyze/commit client methods and existing `ChatSearchPanel`.
- Produces: accessible file/paste controls, mapping/preview/confirmation UI, and import-mode search/generation using returned handles/snapshot.

- [x] Add failing client/component tests for TXT/JSON/CSV/XLSX file selection, paste, mapping controls, preview statistics/warnings, disabled confirm until reviewed, recoverable errors, no raw upload before action, and reuse of the existing search/generation component.
- [x] Run focused Vitest and verify missing UI/client behavior.
- [x] Implement client contracts and `ChatImportPanel`; add a narrow import mode to `ChatSearchPanel` without changing source-export behavior.
- [x] Re-run focused and full frontend tests, TypeScript, and build.

### Task 6: Full synthetic imported-data pipeline

**Files:**
- Create: `backend/tests/e2e/test_manual_import_pipeline.py`
- Modify: `frontend/e2e/synthetic_server.py`
- Create: `frontend/e2e/p4-import.spec.ts`
- Modify: `frontend/playwright.config.ts`

**Interfaces:**
- Exercises the production API and FakeProvider from imported bytes through a generated document.

- [x] Add failing backend and browser tests for import preview/confirm, Snapshot search, one excluded result, exact outbound body, verified evidence, document creation, and 360px layout.
- [x] Run the new tests and confirm failure at the missing import route/UI.
- [x] Complete only the integration wiring required by those tests.
- [x] Re-run the complete backend and Playwright suites.

### Task 7: Strict macOS Accessibility POC

**Files:**
- Create: `tools/accessibility_poc/main.m`
- Create: `tools/accessibility_poc/README.md`
- Create: `backend/tests/tools/test_accessibility_poc.py`
- Modify: `docs/p4-real-chat-ingestion.md`

**Interfaces:**
- CLI: `chat-accessibility-poc --self-test` or `--target qq|wechat --acknowledge-visible-content`.
- Output: JSON capability summary with application/version/window metadata, role counts, text/sender/timestamp/order judgments, virtualization/scroll indicators, and no captured values.

- [x] Add failing source/CLI tests for self-test metadata, explicit target acknowledgement, exact bundle allowlist, bounded focused-window traversal, content-free output, and forbidden-API absence.
- [x] Run focused tests and confirm the command is absent.
- [x] Implement a macOS-only AppKit/Accessibility command with normal authorization, strict app allowlist, bounded focused-window traversal, default metadata-only self-test, and no forbidden APIs.
- [x] Run synthetic tests and content-free self-test only; do not invoke visible-content mode against real QQ/WeChat.

### Task 8: Documentation and final verification

**Files:**
- Modify: `README.md`
- Modify: `docs/how-it-works.md`
- Modify: `docs/api-setup.md`
- Modify: `docs/privacy-and-cleanup.md`
- Modify: `docs/acceptance-report.md`

**Interfaces:**
- Documents exact supported formats/dialects, mapping/defaults, local-only behavior, unresolved rows, duplicate identity, Accessibility POC limits, and observed validation results.

- [x] Update user-facing documentation without claiming real QQ/WeChat or Accessibility content success.
- [x] Run backend pytest, frontend Vitest, TypeScript, production build, Playwright, `pip check`, repository secret scan, and `git diff --check`.
- [x] Inspect `git status` and report inherited versus P4 changes; do not commit.
