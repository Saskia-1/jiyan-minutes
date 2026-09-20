# Ultra Gate 2 Security and Privacy Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Perform an adversarial, evidence-backed security and privacy audit of the complete current Chat to How-To project without accessing real chats, secrets, or blocked source tools.

**Architecture:** Audit the current working tree as the authoritative deliverable because most implemented files are not represented by the Git HEAD baseline. Split review into independent Web/Secret, Files/DB/Jobs, and LLM/Privacy/Client tracks; consolidate only reproducible findings into one durable report with severity, location, impact, reproduction, fix, and test fields.

**Tech Stack:** Python 3.11/3.12, FastAPI, SQLite/FTS5, httpx, macOS Keychain CLI, React 19, TypeScript 7, Vite 8, Vitest 5, Playwright 1.63, pnpm 11.

**Spec:** `/Users/saskia/.codex/attachments/c734073a-81c6-4bbc-8b9e-e0df0e6bd0bc/pasted-text.txt:1767` and `chat_to_howto_codex_prompt_v3.md`

## Global Constraints

- Assume: `This application processes highly sensitive private chat records.`
- Review Web, Secret, Files, DB, Jobs, LLM, and Privacy with an adversarial mindset.
- Every confirmed finding must be classified as Critical, High, Medium, Low, or Informational and include location, impact, reproduction, fix, and test.
- Keep QQ and WeChat `real_device_verified=false`; keep QCE and TraceMemo blocked and do not resume real-adapter exploration.
- Do not access or print real chat content, account identifiers, databases, Keychain values, API keys, or other secrets.
- Do not weaken SIP, Gatekeeper, TCC, Full Disk Access, app signatures, browser protections, or filesystem permissions.
- Do not send project data, chats, keys, database contents, or private paths to third-party services. Dependency names and public version numbers may be checked against authoritative public advisories.
- Preserve the existing dirty working tree, user data, databases, generated delivery documents, and unrelated untracked files. Do not reset, clean, commit, push, publish, install source tools, or overwrite existing data.
- Gate 2 is an audit gate. If explicit code defects are confirmed, record them and stop for a manual switch back to xhigh before remediation.
- Do not enter Ultra Gate 3.

---

### Task 1: Freeze Audit Scope and Baseline

**Files:**
- Create: `docs/ultra-gate-2-security-privacy-audit.md`
- Read: `backend/pyproject.toml`, `frontend/package.json`, lockfiles, runtime scripts, source tree, tests, and delivery docs

**Interfaces:**
- Consumes: Gate 2 rubric and the current working tree.
- Produces: A report header containing scope, threat model, exclusions, current commit/branch state, dependency inventory method, and baseline test evidence.

- [ ] **Step 1: Inventory only explicit project paths**

Run `rg --files` within `backend`, `frontend`, `scripts`, and `docs`; do not enumerate user chat/data locations.

- [ ] **Step 2: Record the audit threat model**

Document local malicious websites, another local process/user, malformed source/export/provider data, crashes/concurrency, prompt injection, and accidental operator disclosure as adversaries. Record physical root compromise and forensic SSD erasure as out of scope.

- [ ] **Step 3: Run the existing safe baseline**

Run backend tests, frontend unit/type/build checks, secret scan, dependency consistency check, and synthetic browser E2E. Do not start a user-configured production instance.

- [ ] **Step 4: Create the report skeleton**

Create sections for Executive verdict, Scope, Method, Findings by severity, Negative findings, Verification, Residual risk, and Gate 2 disposition.

### Task 2: Audit Web and Secret Boundaries

**Files:**
- Read: `backend/src/chat_to_howto/main.py`
- Read: `backend/src/chat_to_howto/api/*.py`
- Read: `backend/src/chat_to_howto/api/session.py`
- Read: `backend/src/chat_to_howto/security/secrets.py`
- Read: corresponding `backend/tests/api/*` and `backend/tests/security/*`

**Interfaces:**
- Consumes: Host/Origin/session/CSRF middleware, cookies, consent/grant records, Keychain boundary, public error serialization.
- Produces: Reproducible Web/Secret findings or explicit negative evidence for each rubric item.

- [ ] **Step 1: Trace every route through Host, Origin, session, CSRF, and CORS behavior**

Check GET/HEAD versus mutation behavior, non-API static paths, cookie flags, session rotation/fixation, exact loopback assumptions, cache headers, validation reflection, and cross-session object access.

- [ ] **Step 2: Trace every secret lifecycle**

Check Keychain invocation arguments/stdin, secret references, logs/exceptions/repr, grant scope fingerprints, expiry, one-time claim atomics, replay, source/version changes, concurrent claims, deletion, and process-memory lifetime.

- [ ] **Step 3: Reproduce candidates only with synthetic sentinels**

Use existing TestClient/security tests or a new temporary one-off script under `/private/tmp`; never insert actual secrets. Record exact request shape and observed status/side effect.

- [ ] **Step 4: Return structured candidate findings**

For each candidate provide severity, file/line, impact, minimal synthetic reproduction, proposed fix, regression-test target, and confidence. Separately list checked controls that held.

### Task 3: Audit Files, Database, and Jobs

**Files:**
- Read: `backend/src/chat_to_howto/services/artifacts.py`, `manual_import.py`, `importer.py`, `document_export.py`, `deletion.py`, `export_jobs.py`, `p0_verifier.py`
- Read: `backend/src/chat_to_howto/db.py`, `migrations.py`, `repositories/chat.py`
- Read: corresponding tests

**Interfaces:**
- Consumes: approved-root capabilities, filesystem primitives, parsers, SQLite transactions/migrations, job state machines and recovery.
- Produces: Reproducible Files/DB/Jobs findings or explicit negative evidence for each rubric item.

- [ ] **Step 1: Trace all attacker-controlled paths and bytes**

Check permissions, symlinks/hardlinks, traversal, descriptor-based validation, TOCTOU, atomic create/replace, temp cleanup, archive/ZIP/XML hazards, file/count/line/response limits, overwrite behavior, and cross-device assumptions.

- [ ] **Step 2: Trace persistence invariants**

Check parameterized SQL, schema migrations, transaction boundaries, referential/orphan cleanup, stale cache identity, deleted-data remnants, WAL/journal behavior, and startup reconciliation.

- [ ] **Step 3: Trace every job state machine**

Check cancellation races, retry/idempotency, concurrent claims, restart recovery, legal transitions, terminal-state immutability, partial artifacts, source/consent changes, and usage accounting.

- [ ] **Step 4: Reproduce candidates with temporary synthetic roots/databases**

Use only test-owned directories and synthetic message sentinels. Record exact setup, command/test, observed effect, and cleanup state.

### Task 4: Audit LLM, Privacy, Client, and Delivery Boundaries

**Files:**
- Read: `backend/src/chat_to_howto/providers/*.py`
- Read: `backend/src/chat_to_howto/services/generation_*.py`, `evidence_validation.py`, `documents.py`
- Read: `frontend/src/**/*`, `frontend/e2e/*`, `scripts/*`, `.gitignore`, README and privacy/API docs
- Read: corresponding tests

**Interfaces:**
- Consumes: generation scopes/previews/consents, provider transport, evidence/cache/documents, UI rendering, startup scripts and logging.
- Produces: Reproducible LLM/Privacy/Client findings or explicit negative evidence for each rubric item.

- [ ] **Step 1: Trace outbound authorization and minimization**

Check preview-to-request byte identity, secret/path/identifier exclusion, provider endpoint allowlist, redirects/proxies/ambient environment, authorization replay, budget/call-count atomics, cancellation and cost records.

- [ ] **Step 2: Attack model output and chat instructions**

Check prompt injection, extra fields, malformed/truncated JSON, fabricated/modified/out-of-scope evidence, insufficient-evidence handling, merge conflicts, document provenance, and HTML/Markdown/URL execution surfaces.

- [ ] **Step 3: Check cache and privacy lifecycle**

Check complete cache scope identity and invalidators, cross-session/snapshot/source/model mixing, logs/analytics/crash output/temp directories/exports/backups/deletion completeness, UI secret reflection, and stale state after disconnect/delete/restart.

- [ ] **Step 4: Check scripts and dependency exposure**

Check shell quoting, PID/port ownership, umask/modes, config/log permissions, symlinks, environment leakage, lockfile integrity, and current authoritative dependency advisories without transmitting private project content.

### Task 5: Consolidate, Reproduce, and Adjudicate Findings

**Files:**
- Modify: `docs/ultra-gate-2-security-privacy-audit.md`

**Interfaces:**
- Consumes: all three independent audit reports and main-agent verification.
- Produces: The authoritative Gate 2 finding list; duplicates merged and unsupported suspicions excluded.

- [ ] **Step 1: Independently verify every Critical/High/Medium candidate**

Read the exact code path and reproduce with synthetic inputs. Downgrade or reject candidates only with written counter-evidence.

- [ ] **Step 2: Sample-verify Low/Informational candidates**

Confirm location and behavior; distinguish vulnerability, hardening opportunity, accepted limitation, and documentation mismatch.

- [ ] **Step 3: Write every confirmed finding in the required schema**

Use exactly: severity, location, impact, reproduction, fix, test. Do not include live payloads, secrets, account data, or absolute private data paths.

- [ ] **Step 4: Record negative findings and coverage limits**

For every Gate 2 rubric item, state what was checked, which tests support it, and what remains untested.

### Task 6: Gate 2 Disposition

**Files:**
- Modify: `docs/ultra-gate-2-security-privacy-audit.md`
- Modify: `docs/acceptance-report.md` only to link the audit and record its actual disposition

**Interfaces:**
- Consumes: confirmed findings and fresh verification evidence.
- Produces: One of `FAIL — remediation required`, `CONDITIONAL PASS`, or `PASS`, without entering Gate 3.

- [ ] **Step 1: Re-run safe verification after report-only changes**

Run document/secret checks and any candidate reproductions needed to ensure the report matches observed behavior.

- [ ] **Step 2: Set the disposition conservatively**

Any unresolved Critical/High/Medium finding yields `FAIL — remediation required`. Low findings require explicit residual-risk treatment. Missing real Keychain/provider/source verification remains an untested limitation, not a synthetic pass.

- [ ] **Step 3: Stop at the required model gate**

If remediation is required, request a manual switch to xhigh and do not edit production code in Ultra. If Gate 2 passes, stop before Ultra Gate 3 and request the separately required manual model transition before any release-readiness audit.

