# Post-Ultra Gate 1 Deterministic Closeout Implementation Plan

> **For Codex:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Close Ultra Gate 1 without further real-device exploration, enforce the QCE/TraceMemo blocked verdict in production, prove P2 remains provider-neutral with DeepSeek as one concrete adapter, and stop before Ultra Gate 2.

**Architecture:** Keep source verdicts as public setup disclosures and enforce them again at every server-side operation that could initialize, enumerate, authenticate, or read a blocked source. Keep P2 orchestration dependent only on the `LLMProvider` protocol and an injected provider factory; DeepSeek remains a composition-root adapter, while deterministic Fake Provider tests continue to exercise the same scope, budget, evidence, cache, and one-time authorization path.

**Tech Stack:** Python 3.12, FastAPI, SQLite/FTS5, pytest, React, TypeScript, Vitest, Playwright.

---

## Global constraints

- Work in the existing user-selected workspace and preserve all unrelated dirty/untracked files.
- Do not resume QCE/TraceMemo real-device exploration or change `real_device_verified=false`.
- Do not auto-change macOS system protections, access real chats, print secrets, or send chat content externally.
- Do not enter the full-project Security / Privacy Audit. Stop and request a manual Ultra switch immediately before that gate.
- Do not commit unless the user separately requests it.

### Task 1: Lock the Gate 1 source verdict into production behavior

**Files:**
- Modify: `backend/src/chat_to_howto/runtime.py`
- Modify: `backend/src/chat_to_howto/main.py`
- Test: `backend/tests/api/test_sources.py`
- Test: `backend/tests/test_runtime.py`

**Step 1: Write failing tests**

Add synthetic API tests proving that a disclosure with blockers:

- reports the source as `blocked`,
- rejects configuration, enumeration, verification consent/job creation, and secret set/get consent before any adapter or secret-store access,
- still permits cleanup operations such as disconnect and secret deletion,
- rejects a new P1 source export scope before any adapter access.

Add a runtime test proving production QQ/WeChat disclosures contain the approved QCE/TraceMemo blocker codes.

**Step 2: Run the focused tests and verify RED**

Run the new source/runtime tests only. Confirm failures are caused by missing server-side blocker enforcement or missing production blocker declarations.

**Step 3: Implement the minimum fail-closed boundary**

- Put the approved blockers into the production setup options.
- Add one source-blocked guard in the application composition layer.
- Apply it to source initialization, enumeration, verification, secret set/get, and new P1 export-scope creation.
- Keep disconnect and secret deletion available for cleanup.
- Never call the adapter or secret store on a rejected operation.

**Step 4: Run the focused tests and verify GREEN**

Run the source/runtime tests and confirm all new assertions pass.

### Task 2: Prove P2 is generic-provider-first

**Files:**
- Modify: `backend/src/chat_to_howto/providers/base.py`
- Modify: `backend/src/chat_to_howto/providers/__init__.py`
- Modify: `backend/src/chat_to_howto/main.py`
- Test: `backend/tests/providers/test_llm_providers.py`
- Test: `backend/tests/e2e/test_synthetic_export_search.py`
- Test: `backend/tests/test_architecture_boundaries.py`

**Step 1: Write failing architecture/behavior tests**

Add tests proving:

- core generation services and API orchestration do not import the DeepSeek adapter,
- a provider with a non-DeepSeek name can be injected through the same factory and complete a generation job,
- Fake Provider remains a deterministic implementation of the generic contract.

Keep the existing tests for scope fingerprints, token/time budgets, evidence validation, cache identity, cancellation, and single-use consent unchanged and passing.

**Step 2: Run the focused tests and verify RED**

Confirm the injected non-DeepSeek provider test exposes any provider-name hard-coding and the architecture test exposes any concrete adapter import outside the composition boundary.

**Step 3: Remove provider-name coupling from orchestration**

- Keep `LLMProvider` and provider request/response types in the generic provider module.
- Make provider selection accept any configured provider name and defer construction to the injected factory.
- Keep DeepSeek construction only in the production composition root.
- Retain Fake Provider as the deterministic test implementation; do not introduce product behavior that switches to Fake silently.

**Step 4: Run focused P2 tests and verify GREEN**

Run provider, generation API, scope, planning/budget, evidence validation, cache, and consent tests.

### Task 3: Update durable evidence and operator documentation

**Files:**
- Modify: `docs/ultra-gate-1-binary-audit.md`
- Modify: `docs/ultra-gate-1-research.md`
- Modify: `docs/decisions.md`
- Modify: `docs/api-setup.md`
- Modify: `docs/acceptance-report.md`

**Step 1: Record the exact Gate 1 closure**

Record that the approved temporary audit directory was deleted, the deletion was direct/non-recoverable, and project/user/chat/database/formal-delivery files were not targeted. Keep both sources `real_device_verified=false`, adapters blocked, and explicitly state that no more real exploration was performed.

**Step 2: Document provider layering**

Describe DeepSeek as the current concrete production adapter behind the generic Provider abstraction. Document that Fake Provider is deterministic test infrastructure and that scope, budget, evidence validation, cache, cancellation, and one-time authorization are provider-neutral.

**Step 3: Update acceptance evidence from fresh runs**

Record commands, counts, skips, and any untested items without claiming real-device success.

### Task 4: Final deterministic verification and gate stop

**Files:**
- Verify only; modify documentation only if the fresh evidence differs.

**Step 1: Run backend verification**

Run focused tests, then the full backend suite. Preserve the explicit live-Keychain skip as an untested item unless the environment is explicitly opted in.

**Step 2: Run frontend verification**

Using the workspace-bundled Node runtime, run unit tests, typecheck, and production build.

**Step 3: Run browser acceptance**

Run Playwright acceptance against the local application with synthetic data only. Record exact results.

**Step 4: Inspect the diff and stop**

Review only the files changed by this closeout, confirm no source was marked real-device verified, and stop before Ultra Gate 2 with a clear manual-switch prompt.
