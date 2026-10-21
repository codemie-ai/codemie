# EPMCDME-12423 LiteLLM model load recovery Implementation Plan

**Goal:** Stop silently serving YAML models when LiteLLM is down at startup; retry in the background until models load, and surface the state to operators.

**Conventions:** Commit per task using the repository's existing convention. Follow `.ai-run/guides/` (error-handling, logging, configuration). Keep the name `_initialize_litellm_models` (patched by `tests/codemie/rest_api/test_startup_integration.py`).

## Acceptance criteria

1. LiteLLM unreachable at startup produces a clear warning/error log, not a silent "0 models" info line.
2. A background retry loads LiteLLM models once the service is reachable.
3. Operators can see that LiteLLM models are inactive via logs and `GET /v1/healthcheck/model`.
4. No restart is needed after LiteLLM recovers.
5. Tests cover LiteLLM available, unavailable and recovery.

## Assumptions (recorded, not asked)

- A fetch failure must be distinguished from a genuinely empty model list.
- Recovery is an asyncio task started from `lifespan`, modelled on `_schedule_customer_config_refresh` (`main.py:413`), with capped backoff and a new interval setting; it exits after the first successful load.
- The retry reuses the admin reload sequence (`admin.py:276-289`: cache clear, fetch, `initialize_default_litellm_models`).
- Notification = logs plus the `/v1/healthcheck/model` payload. UI code is not in this repo, so UI work is out of scope.
- The external `codemie_enterprise` `LiteLLMService.get_available_models` was not inspected; if it swallows connection errors itself, an empty raw list cannot be told from failure (see Task 1 note).

## Task 1 — Surface fetch errors from get_available_models

**Files:** `src/codemie/enterprise/litellm/dependencies.py:858-934`; test in `tests/codemie/enterprise/litellm/` (follow the existing test file there for this module).

- Add keyword `raise_on_error: bool = False`. When True, the `except Exception` at line 932 re-raises after logging, and the `litellm is None` branch (line 889) raises `RuntimeError`. Default behaviour is unchanged, so the other callers (`admin.py:286`, `llm_proxy_monitoring_service.py:419`) are untouched.
- Genuinely empty raw list (line 897) still returns empty `LiteLLMModels()` without raising.

Test-first: yes — `get_available_models(raise_on_error=True)` raises when the mocked service raises, returns empty (no raise) when it returns `[]`, and the default call still returns empty on exception.

## Task 2 — Config setting and LiteLLM model status state

**Files:** `src/codemie/configs/config.py` (next to `LITELLM_MODELS_CACHE_TTL`, line 772); `src/codemie/rest_api/main.py`.

- Add `LITELLM_MODELS_RETRY_INTERVAL_SECONDS: int = 30` (initial delay; backoff doubles up to a cap of 10x, clamped to a minimum of 1 as in the customer-config loop).
- Record state on `app.state.litellm_models_status` with values `loaded`, `unavailable` (fetch failed or zero models while LiteLLM is enabled), `not_applicable` (LiteLLM disabled).

Test-first: no — configuration constant only; exercised by Task 3 tests.

## Task 3 — Failure detection, reload helper and retry task

**Files:** `src/codemie/rest_api/main.py:153-174` (`_initialize_litellm_models`), `main.py:370-375` (`_setup_litellm_features`), `main.py:413-423` pattern, lifespan near `main.py:923-928`; tests in `tests/codemie/rest_api/test_startup_integration.py` or a new `tests/codemie/rest_api/test_litellm_model_recovery.py`.

- Refactor `_initialize_litellm_models(app)` to call `get_available_models(raise_on_error=True)`, then `initialize_default_litellm_models`. On exception, or on zero chat models, log at `error`/`warning` ("LiteLLM models are NOT active, serving YAML defaults; retrying in background"), set status `unavailable`, and return False. On success, log the existing info line, set `loaded`, return True. Never raises, never blocks startup.
- Reload sequence is shared with the retry: call `get_active_llm_proxy_provider().reload_models_cache()` before the fetch (as in `admin.py`), so a cached empty result cannot mask recovery; run the blocking fetch via `asyncio.to_thread`.
- New `_schedule_litellm_models_recovery(app, tasks)`: only schedules when LiteLLM is enabled and status is `unavailable`. Task name `litellm_models_recovery`; loop sleeps with capped exponential backoff, reruns the reload, logs each failed attempt at warning, and on success logs "LiteLLM models recovered", sets status `loaded`, and returns. Cancelled with the other `tasks` on shutdown.
- Call the scheduler from `lifespan` after `_schedule_customer_config_refresh(tasks)`. `_setup_litellm_features` keeps its position before `_initialize_preconfigured_content` (ordering test). Preconfigured content created against the YAML fallback is a known limitation; log it in the recovery message, do not re-run it.

Test-first: yes — startup with `get_available_models` raising sets status `unavailable`, logs an error and schedules a task; with models available it sets `loaded` and schedules none; the recovery loop (patched `asyncio.sleep`) fails twice, then succeeds, calls `initialize_default_litellm_models` once with the models and ends; empty-but-successful result is treated as `unavailable`.

## Task 4 — Expose status on the model healthcheck

**Files:** `src/codemie/rest_api/routers/common.py:69-78`; test next to the existing common router tests (locate with `Grep model_provider_healthcheck tests`).

- Add `litellm_models` key to the `/healthcheck/model` response from `request.app.state.litellm_models_status`, defaulting to `not_checked` when unset. Keep existing keys unchanged; no URLs or secrets in the payload.

Test-first: yes — response contains `litellm_models: "unavailable"` when state is set so, `"loaded"` when loaded, and `"not_checked"` when state is absent, with existing keys unchanged.

## Negative-constraint pass

- "do not silently ignore missing models": Task 3 errors/warns and sets status; Task 1 stops errors being collapsed into empty.
- "No manual restart required": Task 3 retry task.
- Keep `_initialize_litellm_models` name stable: Task 3 keeps it.
- Reuse the admin reload rather than duplicating: Task 3 uses the same cache clear, fetch and initialize calls; the admin endpoint is left unchanged. No task violates any of these.
- UI work out of scope: no task touches UI.
