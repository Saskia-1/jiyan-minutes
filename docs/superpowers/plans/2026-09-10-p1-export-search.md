# P1 Export and Snapshot Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the one-click conversation/range selection → local artifacts → validation → idempotent import → immutable snapshot → local Chinese search path for either configured source.

**Architecture:** Source adapters stream normalized messages into an atomic batch writer. A transactional SQLite repository separates stable messages from batch membership, creates immutable snapshots, and exposes range-bound local search; durable jobs make every stage and partial-completeness decision explicit.

**Tech Stack:** Python 3.11, FastAPI, Pydantic v2, SQLite, pytest; React, TypeScript, Vite, Vitest, Playwright.

**Spec:** `chat_to_howto_codex_prompt_v3.md`

## Global Constraints

- Selection order is source/account → conversation(s) → `[start,end)` → approved export root → export.
- Export, indexing, search, and evidence viewing never invoke an external model.
- Batch directories are unique, private, inside an approved root, symlink-safe, atomically committed, and never overwrite existing files.
- `source_range_verified` means only traversal of the upstream-visible selected range; `partial`, `unknown`, and `empty` remain distinct.
- Search reads only the selected immutable snapshot and never refreshes or scans upstream implicitly.

---

### Task 1: Durable domain schema and migrations

**Files:**
- Create: `backend/src/chat_to_howto/domain/messages.py`, `backend/src/chat_to_howto/domain/jobs.py`, `backend/src/chat_to_howto/repositories/chat.py`, `backend/src/chat_to_howto/migrations.py`
- Test: `backend/tests/repositories/test_chat_repository.py`, `backend/tests/test_migrations.py`

**Interfaces:**
- Produces repositories for sources, accounts, conversations, export batches, messages, batch membership, snapshots, jobs, search runs, consent records, and audit events.

- [ ] Write failing repository tests for cross-account identity, stable source message IDs, conservative fallback identity, repeated identical speech, batch membership, snapshot immutability, restart persistence, and migration idempotence.
- [ ] Run the focused tests and verify expected failures.
- [ ] Implement versioned SQLite migrations with foreign keys, WAL, busy timeout, explicit transactions, and indexes for snapshot/conversation/time/source order.
- [ ] Re-run repository and migration tests.

### Task 2: Safe atomic artifact writer and importer

**Files:**
- Create: `backend/src/chat_to_howto/services/artifacts.py`, `backend/src/chat_to_howto/services/importer.py`, `backend/src/chat_to_howto/domain/manifest.py`
- Test: `backend/tests/services/test_artifacts.py`, `backend/tests/services/test_importer.py`

**Interfaces:**
- Produces: `ArtifactWriter.write_batch(...) -> BatchArtifacts`; `BatchImporter.import_verified(...) -> SnapshotRef`.

- [ ] Write failing tests for JSONL/TXT/manifest parsing, hashes/counts, Chinese paths, spaces, exclusive range validation, unknown times, duplicate click/import idempotence, disk-full cleanup, unwritable roots, traversal, symlink escape, corrupt input, and no-overwrite semantics.
- [ ] Run the focused tests and confirm failures.
- [ ] Implement `0600` temporary files under a `0700` task directory, streaming SHA-256, `fsync`, atomic rename, and manifest validation before the formal directory becomes visible.
- [ ] Implement transactional import and snapshot creation; make indexing retryable without re-fetching.
- [ ] Re-run the tests and inspect temporary directories after injected failure.

### Task 3: Durable export orchestration

**Files:**
- Create: `backend/src/chat_to_howto/services/export_jobs.py`, `backend/src/chat_to_howto/api/export_jobs.py`, `backend/src/chat_to_howto/api/export_batches.py`
- Test: `backend/tests/services/test_export_jobs.py`, `backend/tests/api/test_export_jobs.py`

**Interfaces:**
- Produces: `POST/GET/DELETE /api/export-jobs`; `POST /api/export-jobs/{id}/accept-limitations`; `POST /api/export-jobs/{id}/retry-index`; `GET /api/export-batches/{id}`.

- [ ] Write failing tests for every specified job state, idempotency keys, cancel-before-next-action, restart reconciliation, account switching, partial/unknown acceptance, empty source, known/unknown totals, and index-only retry.
- [ ] Run the focused tests and verify failures.
- [ ] Implement the orchestrator with explicit persisted stage transitions and bounded retry classification; no fabricated percentages.
- [ ] Re-run service/API tests and verify cancelled/deleted jobs cannot write back.

### Task 4: Snapshot-bound local Chinese search and backup import

**Files:**
- Create: `backend/src/chat_to_howto/services/search.py`, `backend/src/chat_to_howto/services/manual_import.py`, `backend/src/chat_to_howto/api/search.py`, `backend/src/chat_to_howto/api/imports.py`
- Test: `backend/tests/services/test_search.py`, `backend/tests/services/test_manual_import.py`, `backend/tests/api/test_search.py`
- Create: `backend/tests/fixtures/synthetic_chat.jsonl`, `backend/tests/fixtures/synthetic_chat.csv`

**Interfaces:**
- Produces: `POST /api/search`; backup `POST /api/imports/preview` and `POST /api/imports`, visually secondary to source export.

- [ ] Write failing tests for two accounts, duplicate names, three conversations, two topics, corrections/conflicts, `[start,end)`, same timestamps, missing time, media placeholders, injection text, context bounds, synonyms/exclusions, and snapshot isolation.
- [ ] Write failing import tests for BOM, multiline Chinese, field mapping, unsupported/corrupt/oversized/path-traversal ZIP input.
- [ ] Run focused tests and verify failures.
- [ ] Implement parameterized literal/substring matching as the explainable Chinese baseline, deterministic ordering, bounded reply/neighbor context, and explicit coverage warnings.
- [ ] Implement preview-before-import with strict byte/file/entry limits and content treated only as data.
- [ ] Re-run focused tests.

### Task 5: Main export/search user flow

**Files:**
- Create: `frontend/src/features/export/ExportWizard.tsx`, `frontend/src/features/export/JobProgress.tsx`, `frontend/src/features/search/SearchWorkspace.tsx`, `frontend/src/features/search/ResultList.tsx`, `frontend/src/features/import/BackupImport.tsx`
- Test: matching `*.test.tsx`; `frontend/e2e/export-search.spec.ts`

**Interfaces:**
- Consumes P0 source and P1 job/search APIs.

- [ ] Write failing UI tests for exact selection order, seven-day editable default, UTC+8 display, inclusive date-to-exclusive-instant conversion, no automatic start, no model call, completeness warnings, retry-index path, and backup import remaining secondary.
- [ ] Run component tests and confirm failures.
- [ ] Implement the accessible responsive flow and preserve batch/snapshot identity in route state without storing secrets.
- [ ] Add an E2E synthetic source scenario proving one click performs fetch → artifacts → import → searchable result with no second upload.
- [ ] Run frontend unit and E2E tests.

