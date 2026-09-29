## Acceptance criteria

- [ ] `LLMModel` has `max_input_tokens: Optional[int] = None`.
- [ ] `map_litellm_to_llm_model` copies `model_info["max_input_tokens"]` into `LLMModel`.
- [ ] Every LLMModel-returning endpoint (`/v1/llm_models`, `/v1/default_models`, `/v1/default_models/{category}`, `/v1/embeddings_models`, `/v1/llm_models/image_generation`) includes the field when known and OMITS it (not null) when unknown; no change for existing consumers.
- [ ] `.ai-run/sdlc-factory/doctor.json` (modified by tooling, unrelated) stays out of the feature commit.

---

# Expose max_input_tokens in /v1/llm_models Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the already-written change that exposes LiteLLM `max_input_tokens` per model through the LLMModel-returning endpoints.

**Architecture:** The implementation is ALREADY PRESENT as uncommitted working-tree changes on branch `EPMCDME-14763_expose-max-input-tokens`. Do NOT re-implement. Verify it against the acceptance criteria, then commit only the two source files. Routers already use `response_model_exclude_none=True`, so no router change is needed.

**Tech Stack:** Python 3.12, Pydantic v2, FastAPI, LiteLLM proxy `model_info`.

## Global Constraints

- Commit per task using the repository's existing convention: `EPMCDME-14763: <subject>` (`.ai-run/guides/standards/git-workflow.md`), with trailer lines `Generated with AI` and `Co-Authored-By: codemie-ai <codemie.ai@gmail.com>`.
- Tests are written or run only when the user asks (AGENTS.md); none requested, so no test-writing tasks.
- `.ai-run/sdlc-factory/doctor.json` is modified by tooling and MUST NOT be staged or committed. Stage files by explicit path, never `git add -A` / `git add .`.
- Non-goals / follow-ups: static YAML catalog values (`config/llms/*.yaml`), `LlmRouterOption` exposure, unit tests, non-int validation in the mapper.

## Negative-constraint pass

- "OMITS it (not null) when unknown": honored by Task 1 step 2 (`response_model_exclude_none` already on all five endpoints; field defaults to `None`).
- "no change for existing consumers": honored by Task 1 (additive optional field only).
- doctor.json "stays out of the feature commit": honored by Task 2 step 2.
- Non-goals (YAML values, router option, tests, mapper validation): no task touches them.

---

### Task 1: Verify the existing working-tree change against the acceptance criteria

**Files:**
- Inspect only: `src/codemie/configs/llm_config.py:219`, `src/codemie/enterprise/litellm/models.py:161,179`, `src/codemie/rest_api/routers/llm_models.py`

Test-first: no — tests not requested per AGENTS.md; verified via ruff + live endpoint check.

- [ ] **Step 1: Confirm the diff is exactly the intended change.** Run `git diff -- src/codemie/configs/llm_config.py src/codemie/enterprise/litellm/models.py`. Expect only: `max_input_tokens: Optional[int] = None` on `LLMModel` (next to `max_output_tokens`), `max_input_tokens = model_info.get("max_input_tokens")` in `map_litellm_to_llm_model`, and `max_input_tokens=max_input_tokens` passed to `LLMModel(...)`. Fix nothing unless the diff contains anything else; report unexpected hunks instead of committing them.
- [ ] **Step 2: Confirm the omit-when-unknown behavior.** Grep `response_model_exclude_none` in `src/codemie/rest_api/routers/llm_models.py` and confirm it is set on `/v1/llm_models`, `/v1/llm_models/image_generation`, `/v1/embeddings_models`, `/v1/default_models`, `/v1/default_models/{category_id}`.
- [ ] **Step 3: Run the lint gate.** Run `make ruff` (see `.ai-run/guides/quality-gates.md`). Expected: passes. Report the exact result.

---

### Task 2: Commit the feature change

**Files:**
- Commit: `src/codemie/configs/llm_config.py`, `src/codemie/enterprise/litellm/models.py`

Test-first: no — tests not requested per AGENTS.md; verified via ruff + live endpoint check.

- [ ] **Step 1: Stage by explicit path.** Run `git add src/codemie/configs/llm_config.py src/codemie/enterprise/litellm/models.py`.
- [ ] **Step 2: Verify staging.** Run `git status` and `git diff --cached --stat`. Expect exactly those two files staged; `.ai-run/sdlc-factory/doctor.json` and the `docs/superpowers/tasks/...` directory must not be staged. Unstage anything else with `git restore --staged <path>`.
- [ ] **Step 3: Commit.** Subject: `EPMCDME-14763: Expose max_input_tokens in LLM models catalog`. Body: one or two sentences (LiteLLM already reports `max_input_tokens`; CodeMie's catalog dropped it; the CLI can now replace its hard-coded 1M-context table). Add the trailers `Generated with AI` and `Co-Authored-By: codemie-ai <codemie.ai@gmail.com>`. If a commit hook modifies files, review and re-stage only the two intended files, then retry. Do not push.
