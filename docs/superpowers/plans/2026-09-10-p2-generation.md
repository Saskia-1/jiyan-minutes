# P2 Controlled Generation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add evidence preview/exclusion, complete screening, per-scope outbound consent, one real configurable model provider, structured how-to generation, and strict evidence validation without requiring a model for local workflows.

**Architecture:** A generation plan freezes the allowed snapshot and exclusions, estimates the exact outbound scope, and requires a fingerprinted consent before any provider call. Chunk extraction, merge, and bounded repair return Pydantic-validated structures; the validator rejects missing, excluded, or cross-snapshot evidence IDs before publishing a document version.

**Tech Stack:** FastAPI, Pydantic v2, provider SDK behind an interface, pytest; React, TypeScript, Vitest, Playwright.

**Spec:** `chat_to_howto_codex_prompt_v3.md`

**Normative acceptance supplement:** `docs/superpowers/plans/2026-09-10-v3-traceability-amendments.md`

## Global Constraints

- Provider defaults to `unconfigured`; discovering a key never authorizes using it.
- Real keys are never placed in the repository, browser, command arguments, logs, screenshots, or model-visible output.
- Any range/provider/model/redaction/message/budget change invalidates outbound consent.
- Complete screening discloses that all allowed text chunks may be sent; fast search sends only the explicitly previewed allowed set.
- Provider input contains only necessary text and local anonymous IDs, never upstream tokens, database keys, phone/QQ IDs, or avatars.
- Output is a Chinese how-to, not a summary or reasons essay; every factual item needs allowed evidence.

---

### Task 1: Generation schemas, scope freeze, and consent fingerprint

**Files:**
- Create: `backend/src/chat_to_howto/domain/generation.py`, `backend/src/chat_to_howto/services/generation_scope.py`, `backend/src/chat_to_howto/api/generation.py`
- Test: `backend/tests/domain/test_generation.py`, `backend/tests/services/test_generation_scope.py`, `backend/tests/api/test_generation_consent.py`

**Interfaces:**
- Produces `GenerationResult`, `Preparation`, `Step`, `Check`, `Conflict`, `GenerationScope`, `OutboundConsent`; endpoints for preview, consent, and job creation.

- [ ] Write failing schema tests for required empty arrays/nulls, real evidence IDs, insufficient-evidence result, corrections, unresolved conflicts, and forbidden explanatory sections.
- [ ] Write failing consent tests for every invalidator and for zero provider calls before consent.
- [ ] Run focused tests and confirm failures.
- [ ] Implement immutable schemas, canonical JSON fingerprints, redacted preview statistics, token estimate ranges, unknown-price display, and consent expiration.
- [ ] Re-run tests.

### Task 2: Provider abstraction and one real configurable provider

**Files:**
- Create: `backend/src/chat_to_howto/providers/base.py`, `backend/src/chat_to_howto/providers/unconfigured.py`, `backend/src/chat_to_howto/providers/openai_provider.py`, `backend/src/chat_to_howto/providers/fake.py`
- Test: `backend/tests/providers/test_unconfigured.py`, `backend/tests/providers/test_openai_provider.py`, `backend/tests/providers/test_provider_redaction.py`

**Interfaces:**
- Produces `LLMProvider.generate_structured(request) -> ProviderResult`; real OpenAI configuration is loaded server-side after explicit local setup.

- [ ] Write failing tests around a fake SDK client for `store=False`, structured parsing, refusal, incomplete/empty output, authentication, rate limit, timeout, context length, request IDs, usage accounting, and secret/body-free logs.
- [ ] Run provider tests and confirm failure.
- [ ] Implement the unconfigured provider and current official OpenAI Responses structured-output adapter without hard-coded model names.
- [ ] Re-run provider tests; run only a synthetic live smoke test if the user separately authorizes the existing/new API key and possible charge.

### Task 3: Extraction, merge, validation, and bounded repair

**Files:**
- Create: `backend/src/chat_to_howto/services/chunking.py`, `backend/src/chat_to_howto/services/generation_pipeline.py`, `backend/src/chat_to_howto/services/evidence.py`, `backend/src/chat_to_howto/prompts/howto_zh.txt`
- Test: `backend/tests/services/test_generation_pipeline.py`, `backend/tests/services/test_evidence.py`, `backend/tests/fixtures/howto_cases.json`

**Interfaces:**
- Consumes frozen `GenerationScope` and `LLMProvider`; produces a validated draft and complete usage ledger.

- [ ] Write failing tests using the required m1-m4 case plus unresolved conflict, no evidence, excluded ID, fabricated ID, cross-snapshot ID, injection text, chunk boundary, and budget exhaustion cases.
- [ ] Run focused tests and confirm failures.
- [ ] Implement deterministic chunking with no silent truncation, source-ID-preserving extraction, correction-aware merge, validation against the exact allow-set, and finite repair attempts.
- [ ] Add assertions that URLs/code in chat never trigger tools or network access and output contains no reasons/background/conclusion sections.
- [ ] Re-run focused tests and inspect provider call counts/usage totals.

### Task 4: Durable generation jobs, cache, budgets, and UI

**Files:**
- Create: `backend/src/chat_to_howto/services/generation_jobs.py`, `backend/src/chat_to_howto/services/generation_cache.py`
- Create: `frontend/src/features/generation/EvidenceReview.tsx`, `frontend/src/features/generation/OutboundConsent.tsx`, `frontend/src/features/generation/GenerationProgress.tsx`, `frontend/src/features/generation/HowToViewer.tsx`
- Test: backend job/cache tests, matching frontend component tests, `frontend/e2e/generation.spec.ts`

**Interfaces:**
- Produces durable generation state endpoints and source-backed draft viewing.

- [ ] Write failing tests for specified job states, cancellation, crash/restart, finite retries, message/token/daily limits, exact cache key components, cache invalidation, and no use across batch/snapshot/consent changes.
- [ ] Write failing UI tests for redacted preview, fast/complete disclosure, excluded messages, unconfigured provider, estimate uncertainty, consent invalidation, progress, and evidence drill-down.
- [ ] Run focused backend/frontend tests and confirm failures.
- [ ] Implement durable orchestration, accounting, cache invalidation, and UI; evidence drill-down reads local snapshot only and makes zero provider calls.
- [ ] Re-run tests and E2E synthetic generation flow.
