# Synthetic E2E Provider Wiring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore the synthetic browser acceptance flow by explicitly wiring its deterministic FakeProvider into the provider-neutral application factory.

**Architecture:** Keep `create_app` fail-closed when no generation provider factory is supplied. The synthetic-only server owns its test provider composition, matching the explicit dependency injection already used by the backend end-to-end test and keeping FakeProvider out of production assembly.

**Tech Stack:** Python 3.11, FastAPI, FakeProvider, Playwright 1.63, Node.js 24.19.0.

**Spec:** `chat_to_howto_codex_prompt_v3.md`

## Global Constraints

- Preserve all existing tracked and untracked user work.
- Do not make `create_app` silently select FakeProvider from a provider name.
- Do not change production DeepSeek composition or the Gate 1 source blockers.
- Do not access real sources, Keychain values, chat data, or external networks.
- Do not commit unless the user separately requests it.

---

### Task 1: Inject the deterministic provider in the synthetic E2E server

**Files:**
- Modify: `frontend/e2e/synthetic_server.py`
- Test: `frontend/e2e/p1-search.spec.ts`

**Interfaces:**
- Consumes: `create_app(..., generation_provider_factory: Callable[[str], LLMProvider])` and `FakeProvider()`.
- Produces: a synthetic application whose `fake` generation scope resolves to an in-process deterministic provider and whose unknown provider names fail closed.

- [x] **Step 1: Verify the existing browser regression test fails for the intended reason**

Run:

```sh
/Users/saskia/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node e2e/run.mjs
```

Observed before implementation: the desktop and 360px full-chain cases reached `failed · 0/1`; the synthetic database recorded `provider=fake` and `error_code=generation_failed` because no provider factory was passed.

- [x] **Step 2: Add the minimum explicit test-provider composition**

Import `FakeProvider`, create a factory that returns `FakeProvider()` only for `fake`, and pass it as `generation_provider_factory`:

```python
from chat_to_howto.providers.fake import FakeProvider

def generation_provider_factory(provider_name: str):
    if provider_name != "fake":
        raise ValueError("generation provider is not configured")
    return FakeProvider()
```

- [x] **Step 3: Run the focused browser case and verify GREEN**

Run:

```sh
/Users/saskia/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node e2e/run.mjs e2e/p1-search.spec.ts --grep "P1/P2"
```

Expected: both desktop and 360px variants pass and the document/export/delete tail of the scenario executes.

- [x] **Step 4: Run the complete regression gates**

Run the six Playwright cases, backend full pytest suite, frontend Vitest, TypeScript typecheck, Vite production build, dependency check, secret scan, and `git diff --check`. Expected: all executable checks pass; the opt-in real Keychain test remains explicitly skipped.
