# Technical Research

**Task**: standalone core-gating feature-flags routers
**Generated**: 2026-11-05
**Research path**: codegraph

---

## 1. Original Context

Jira EPMCDME-16884 "Standalone Core Gating: Backend"

h2. Context
* Chats, Assistants, Workflows, Skills, Schedulers and webhooks have no feature flag, and their routers always mount.
* The backend already mounts some routers conditionally, based on configuration, so their routes return not found.
* Provider readiness runs after routers mount, and only a health check endpoint reads it. LiteLLM, Google and Anthropic come back as "not checked".
* Schedules start assistants and workflows by calling the app's own API.
* The standalone switch (EPMCDME-16473) is approved but not merged.

h2. Story
As a platform operator running CodeMie in standalone mode, I want the backend to turn off Chats, Assistants, Workflows, Skills, Schedulers and webhooks when no LLM provider is configured so that nothing can start work that cannot succeed, and the UI knows what is available.

h2. Background
A standalone deployment can start without an LLM provider. Today the API still accepts requests for features that cannot work in that state. This story delivers the check, the switch-off on the API, the availability result the UI needs and the startup log that tells the operator what happened.

h2. Acceptance Criteria
1. Standalone off and no LLM provider configured: every endpoint behaves exactly as today.
2. Standalone off: at startup the provider check does not run and writes nothing to the log.
3. Standalone on and LLM provider configured: Chats, Assistants, Workflows, Skills, Schedulers and webhook endpoints are available.
4. Standalone on and no provider: calls to Chats, Assistants, Workflows, Skills, Schedulers or webhook endpoints return not found, are absent from the API schema, and no model call is made.
5. Standalone on and no provider: when a stored schedule comes due for an assistant or workflow, it does not run and the attempt is logged.
6. Schedules stored before core was switched off: when a provider is configured and backend restarts, schedules are unchanged and run again.
7. Standalone on, no provider, no Elasticsearch: the Analytics API responds without errors.
8. Standalone on and a provider's settings only partly filled (e.g. key with no URL): provider counts as not configured.
9. Standalone on and a LiteLLM proxy has both URL and key set: core is available.
10. Standalone on and a provider's required settings hold any non-empty value, including an unchanged placeholder from the example file: provider counts as configured.
11. Bedrock or Google settings set and cloud credentials missing: core stays on and requests fail at request time.
12. Only per-project or per-user stored credentials exist: provider counts as not configured.
13. Standalone on, no provider, default flags: startup completes and does not fail; the log names the missing settings and states that Chats, Assistants and Workflows are off.
14. Standalone on and provider configured: log states the provider check passed and which provider was found.
15. Standalone on or off: when the UI reads its configuration it receives feature availability only and does not receive the standalone setting.
16. The standalone env example has a comment stating any non-empty provider value counts as configured, so placeholders must be replaced.

h2. Out of Scope
All UI changes (child standalone-core-gating-ui); voice input, smart tool selection, conversation analytics and marketplace similarity gating; moving Analytics data off Elasticsearch; the standalone switch itself (EPMCDME-16473).

h2. Decisions
D1: With no LLM provider, core is disabled in standalone mode; no flag required for this check.
D6: Availability changes at backend restart; provider readiness computed once at startup.
D11: Routers of disabled features are turned off, following the existing conditional router pattern; the check runs from settings only, before routers mount.
D12: "Configured" is decided from settings for the default model's provider: Azure needs API key and URL; Bedrock needs region (own setting or standard AWS config); Google Vertex needs project ID and Vertex region; Vertex Anthropic needs project ID and Claude Vertex region; Anthropic needs API key; LiteLLM proxy mode needs URL and app key; no default model or no provider means not configured.
D13: Remote (A2A) assistants and other paths that need no global provider switch off too.
D14: Schedulers and webhook routers turn off; stored schedules stay.
D15: Stored per-project or per-user credentials do not count.
D16: With settings set and cloud credentials missing, core stays on.
D17: Any non-empty value counts as configured; the env example gets a comment.
D18: The check runs and logs only in standalone mode.

h2. Constraints
* Depends on EPMCDME-16473 (standalone switch), which must land first (check whether it is already present in this branch).
* Lands before standalone-core-gating-ui, which consumes the availability result.
* The spec must decide whether the a2a, share, category and conversation_analysis routers turn off, and keep Analytics working.
* Availability must reach both the UI and backend flag reads through one source.

Open Questions: None.

---

## 2. Codebase Findings

### Existing Implementations

**Purpose A: know whether an LLM provider is configured**
- `src/codemie/service/model_provider_readiness.py:118` `check_model_provider_readiness()` returns `ModelProviderReadiness(status, provider, missing)`; statuses CONFIGURED / NOT_CONFIGURED / NOT_CHECKED (`:38`). Rules today:
  - `is_litellm_enabled() and LLM_PROXY_MODE == "lite_llm"` -> NOT_CHECKED, provider `litellm_proxy`, no URL/key test (`:120`).
  - Reads default GLOBAL model via `llm_service.get_default_model_for_category` (`:127`); none -> NOT_CONFIGURED.
  - Azure: `AZURE_OPENAI_API_KEY` + `AZURE_OPENAI_URL` non-blank via `_is_present` (strip) (`:138`).
  - Bedrock: boto3 session region + credential chain; credentials missing -> NOT_CONFIGURED; chain error -> NOT_CHECKED (`:87`). This diverges from D16 (creds missing keeps core on).
  - provider None -> NOT_CONFIGURED; every other provider (Google Vertex, Vertex Anthropic, Anthropic, ...) -> NOT_CHECKED (`:161`).
- `src/codemie/rest_api/main.py:290` `_check_model_provider_readiness(app)`: async, 5 s timeout, never raises, stores `app.state.model_provider_readiness`, logs via `log_model_provider_readiness` (`model_provider_readiness.py:168`). Called in `lifespan` at `main.py:1345`, i.e. after router mount (module level, `main.py:1488-1573`). Runs regardless of STANDALONE (contradicts AC2 as written for any change that keeps this call as-is).
- Sole reader: `GET /v1/healthcheck/model` `src/codemie/rest_api/routers/common.py:69` (`include_in_schema=False`), plus tests `tests/codemie/service/test_model_provider_readiness.py`, `tests/codemie/rest_api/routers/test_common_healthcheck.py`, `tests/codemie/rest_api/test_startup_integration.py`.
- Settings present in `src/codemie/configs/config.py`: `MODELS_ENV` (`:53`, default `"dial"`), `AZURE_OPENAI_API_KEY/URL` (`:61`), `ANTHROPIC_API_KEY` (`:65`), `AWS_BEDROCK_REGION` (`:431`), `GOOGLE_PROJECT_ID` (`:435`), `GOOGLE_VERTEXAI_REGION` (`:445`), `GOOGLE_CLAUDE_VERTEXAI_REGION` (`:446`), `LLM_PROXY_MODE` (`:722`), `LITE_LLM_URL` / `LITE_LLM_APP_KEY` (`:729`). `is_litellm_enabled` imported from `codemie.enterprise.litellm` (not read).

**Purpose B: switch off features / routers by configuration**
- Conditional router mounts in `main.py` (module level): `index.router` if `ElasticSearchClient.is_configured()` (`:1493`); `budget_router` if litellm + flag (`:1538`); user-management group (`:1551`); servicenow (`:1558`); `register_marketplace_management_router` helper with `is_marketplace_management_enabled()` (`~:1465-1485`, docstring explains mount guard plus runtime guard for routes on always-mounted routers).
- Unconditional mounts relevant to the story: `a2a` (`:1488`), `assistant` (`:1490`), `category` (`:1492`), `conversation` (`:1500`), `conversation_analysis` (`:1501`), `workflow`, `workflow_marketplace`, `workflow_executions` (`:1503-1505`), `webhook` (`:1517`), `share` (`:1521`), `skill_events`, `skill` (`:1534-1535`), `schedulers` (`:1537`), plus `analytics` (`:1527`), `metrics` (`:1526`), `cli_analytics` (`:1528`), `assistant_mapping`, `assistant_prompt_variable_mapping`, `background_tasks`, `llm_models`.
- Custom OpenAPI: `main.py:1394` `custom_openapi()` builds from `app.routes` and caches `app.openapi_schema`; routers not mounted are absent from it.
- Webhook has a runtime standalone guard for rate limiting only: `src/codemie/rest_api/routers/webhook.py:31`.

**Purpose C: single source of feature availability for UI and backend**
- `src/codemie/configs/customer_config.py:154` `_get_runtime_config()` builds runtime components (never YAML/DB-overridable) read by `is_feature_enabled` (`:463`), `resolve_component` (`:436`) and served to the UI by `src/codemie/service/customer_config_service.py:157` `resolve_components()` (via `src/codemie/rest_api/routers/customer_config.py:71,190`). Existing standalone-derived entries: `mermaid` (`:312`), `dynamicCodeInterpreter` (`:324`, from `src/codemie/configs/code_executor_availability.py`), `workspaceScriptRunner` (`:336`), `googleOauth` (`:341`), plus `mcpConnect` filtered in `resolve_components` (`customer_config_service.py:171`). `CONFIG_IDS` dict at `customer_config.py:30`. `configured_feature_enabled` (`:362`) reads YAML/DB value ignoring runtime. 35 callers of `customer_config` (see blast radius), e.g. `core/dependecies.py`, `rest_api/handlers/assistant_handlers.py`.
- No runtime component exists for chat, assistants, workflows, skills, schedulers or webhooks. `resolve_components` does not emit the `STANDALONE` setting itself (AC15 not at risk from the component list; the to-be-checked path is `config.to_safe_dict()` at `main.py:1248`, which logs, not served).
- Sibling pattern for standalone-only availability: `code_executor_available(config)` / `warn_code_executor_unavailable()` in `code_executor_availability.py:22,37`, called from `_run_standalone_startup` (`main.py:609`).

**Purpose D: standalone startup logging**
- `main.py:609` `_run_standalone_startup()` is a no-op unless `config.STANDALONE` (`config.py:595`, bool, default False; present on this branch). Logs MCP Connect, Mermaid, Code Executor, Redis degradations. Called at `main.py:1340`, before `_check_model_provider_readiness` (`:1345`). Tests: `tests/codemie/rest_api/test_standalone_startup.py`.
- Second STANDALONE reader that bypasses `config`: `src/codemie_tools/data_management/code_executor/code_executor_tool.py:70` `is_standalone()` reads `os.getenv("STANDALONE")`.

**Purpose E: schedules run assistants/workflows**
- `src/codemie/triggers/bindings/cron.py` `Cron` (`:228`): `__schedule_assistant_job` (`:716`) / `__schedule_workflow_job` (`:739`) add APScheduler jobs running `_tracked_run` with actor `invoke_assistant` / `invoke_workflow`. `_tracked_run` writes `SchedulerRun` rows (running/completed/failed/skipped, `scheduler_run.py:31`). `__write_dropped_run` (`cron.py:288`) already records a `skipped` row with a reason for runs APScheduler drops.
- `src/codemie/triggers/actors/assistant.py:26` posts via httpx to `BASE_API_URL` + `/v1/assistants/{id}/model` (and `create_conversation` via the same API), so an unmounted assistant router makes these calls 404 (AC5 path), not a model call.
- Started from `lifespan` `main.py:1352` (`TRIGGER_ENGINE_ENABLED`) -> `NodeController().start()` (`triggers/node_controller.py:48`) -> leader lock -> `Cron.start_async`. Jobs loaded from stored scheduler settings (DB), independent of the router.
- `schedulers` router (`routers/schedulers.py`) lists runs/stats; `SchedulerRunService`, `SchedulerRunRepository` serve it.

**Purpose F: Analytics works without Elasticsearch**
- `analytics` package `src/codemie/rest_api/routers/analytics/` contains one ES reference (`usage_attribution.py`); `index.router` is the only router gated on `ElasticSearchClient.is_configured()`. `metrics` router and `cli_analytics` ES use not examined (unverified).

**Other touched code**
- `standalone/.env.standalone.example`: sets `MODELS_ENV=azure`, `AZURE_OPENAI_API_KEY=<your-azure-openai-key>`, `AZURE_OPENAI_URL=<https://your-resource.openai.azure.com>` (lines 19-21), commented `MODELS_ENV=aws` / `AWS_BEDROCK_REGION` (25-26). No comment about placeholders. `standalone/.env.standalone` also exists. Contract test `tests/codemie/standalone/test_standalone_compose_contract.py`.

### Decisions Already in the Code
- Convention for hiding a feature: runtime-computed `Component` in `_get_runtime_config` (comments cite "UI and backend read the same source of truth", `customer_config.py:284-288`) plus mount guard in `main.py`; where a route lives on an always-mounted router, a per-request guard is added (`register_marketplace_management_router` docstring).
- Readiness never blocks startup (EPMCDME-14653): all failures degrade to NOT_CHECKED (`main.py:293-322`).
- `_is_present` uses `strip()`, so whitespace-only counts as missing; AC10 says "any non-empty value".
- Readiness uses `llm_service` default model for GLOBAL category under `MODELS_ENV` profile (`model_provider_readiness.py:127`), the same default model D12 names.
- Readiness `missing` entries name settings only, never values (comment `:50`); returned on unauthenticated `/v1/healthcheck/model`.
- Feature flag env override pattern: `FEATURE_<UPPER_SNAKE>` env vars (`customer_config.py:136`) apply only to YAML-backed components, not runtime ones.
- Availability data is stored on `app.state` (readiness) and computed per call for components; the readiness result is a one-shot snapshot, whereas `_get_runtime_config` is recomputed on every call.

### Task Statements Checked Against the Code
- "Chats, Assistants, Workflows, Skills, Schedulers and webhooks have no feature flag, routers always mount": holds (`main.py:1488-1537`, `1517`).
- "Backend already mounts some routers conditionally": holds (`main.py:1493,1538,1551,1558`, marketplace helper).
- "Provider readiness runs after routers mount, only a health check reads it": holds (`main.py:1345` vs `1488+`; only `common.py:71`).
- "LiteLLM, Google and Anthropic come back as not checked": holds (`model_provider_readiness.py:120,161`).
- "Schedules start assistants and workflows by calling the app's own API": holds (`triggers/actors/assistant.py:57-60`, `triggers/config BASE_API_URL`).
- "Standalone switch (EPMCDME-16473) not merged": contradicted for the config field; `config.STANDALONE` exists (`config.py:595`) and is used by commits already on this branch (`_run_standalone_startup`, mermaid, code executor). Whether it is the full 16473 deliverable is unverified.
- AC2 / D18 (check runs only in standalone): keeps in place the existing unconditional `_check_model_provider_readiness` call (`main.py:1345`) and its consumers (`common.py:69`, `/v1/healthcheck/model`, three test files).
- D12 (Google Vertex, Anthropic, LiteLLM by URL+key): contradicted by current readiness rules, which return NOT_CHECKED for them and do not test LiteLLM URL/key; keeps `check_model_provider_readiness` and its status vocabulary in place (consumers above).
- D16 (Bedrock creds missing: core stays on): contradicted by `_check_aws_bedrock_configured` (credentials None -> NOT_CONFIGURED, `:108`).
- D11 ("check runs before routers mount"): router mounts are module-level in `main.py` executing on import; the readiness check is in async `lifespan` (after). Existing readiness path also touches `llm_service` model profile and boto3, not "settings only".
- AC5 (stored schedule skipped and logged): `Cron` jobs run actors that call the API; existing skipped-run recording is `__write_dropped_run` for APScheduler-dropped events.
- AC7 (Analytics without Elasticsearch): partially verified (`analytics/usage_attribution.py` has one ES reference); `metrics`/`cli_analytics` unverified.
- AC15 (UI gets no standalone setting): `resolve_components` returns component ids only; no `STANDALONE` component found. Holds today.
- Constraint "decide a2a, share, category, conversation_analysis": see Consumers below; no verdict.

### Consumers of Changed Contracts
- `check_model_provider_readiness` / `ModelProviderStatus` / `ModelProviderReadiness`: `main.py:123-128,290-324`; `routers/common.py:22,71`; tests `test_model_provider_readiness.py`, `test_common_healthcheck.py`, `test_startup_integration.py`.
- `app.state.model_provider_readiness`: written `main.py:323`; read `common.py:71`.
- Mounted routers (any unmount affects): `main.py:1488-1537`. Cross-router dependants not traced: `share` (1 LLM/ES-ish reference), `skill` (10 matches for ES/LLM terms in `routers/skill.py`), `conversation_analysis` (gated at runtime by `CONVERSATION_ANALYSIS_ENABLED`, `customer_config.py:266`).
- `customer_config` runtime component list: consumers `customer_config_service.resolve_components`, `rest_api/routers/customer_config.py:71,190`, 35 callers across `core/dependecies.py`, `handlers/assistant_handlers.py`, `main.py`.
- Scheduler actors: `Cron` -> `invoke_assistant/invoke_workflow` -> `/v1/assistants/{id}/model` and workflow endpoints on the same app; `TRIGGER_ENGINE_ENABLED` gate (`main.py:1352`).
- Webhook triggering path: `triggers/bindings/webhook.py` (`WebhookService`) behind `routers/webhook.py`.
- `is_standalone()` in `code_executor_tool.py:70` and `config.STANDALONE`: two independent readers of one setting.

### Architecture and Layers Affected
- Config (`configs/config.py`, `customer_config.py`, `code_executor_availability.py`), startup/composition (`rest_api/main.py`), service (`service/model_provider_readiness.py`, `customer_config_service.py`), routers, triggers (`triggers/bindings/cron.py`, `actors/*`, `node_controller.py`), standalone packaging (`standalone/.env.standalone.example`).

### Integration Points
- LiteLLM enterprise module (`codemie.enterprise.litellm`: `is_litellm_enabled`), `llm_service` model profiles, boto3 for Bedrock, httpx self-calls from scheduler actors, APScheduler + Postgres advisory leader lock.

### Patterns and Conventions
- Router mount conditions as module-level `if` around `app.include_router`; feature availability as runtime `Component` in `_get_runtime_config`; `_run_standalone_startup` as the single standalone-log entry point; warnings logged by name only.

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `.ai-run/guides/development/configuration-patterns.md` exists (not read); other guides per AGENTS.md (architecture, API, testing). Consult at spec time.

### Architectural Decisions
- Inline docstring for the marketplace router mount plus runtime guard (`main.py` ~1465). Comments in `customer_config.py:284-306` on "one source of truth".

### Derived Conventions
- See Section 2 Patterns.

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/service/test_model_provider_readiness.py`, `tests/codemie/rest_api/routers/test_common_healthcheck.py`, `tests/codemie/rest_api/test_startup_integration.py` (readiness); `tests/codemie/rest_api/test_standalone_startup.py` (standalone logging); `tests/codemie/configs/test_config_code_executor_available.py`, `test_customer_config.py`, `test_customer_config_override.py` (components); `tests/codemie/triggers/bindings/test_cron.py`, `tests/codemie/triggers/test_node_controller.py`, `tests/codemie/rest_api/routers/test_schedulers.py` (scheduler); `tests/codemie/standalone/test_standalone_compose_contract.py` (env example/compose).

### Testing Framework and Patterns
- pytest; patch `config` attributes; tests per module under `tests/codemie/...`.

### Coverage Gaps
- No test found for router mount gating in `main.py` by standalone/provider; no test for "unmounted routers absent from OpenAPI".

---

## 5. Configuration and Environment

### Environment Variables
- `STANDALONE`, `MODELS_ENV`, `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_URL`, `ANTHROPIC_API_KEY`, `AWS_BEDROCK_REGION`, `GOOGLE_PROJECT_ID`, `GOOGLE_VERTEXAI_REGION`, `GOOGLE_CLAUDE_VERTEXAI_REGION`, `LLM_PROXY_MODE`, `LITE_LLM_URL`, `LITE_LLM_APP_KEY`, `TRIGGER_ENGINE_ENABLED`, `CONVERSATION_ANALYSIS_ENABLED`, `FEATURE_*` overrides.

### Configuration Files
- `src/codemie/configs/config.py`, `customer_config.py`, `standalone/.env.standalone.example`, `standalone/.env.standalone`, LLM profile YAML `config/llms/llm-{MODELS_ENV}-config.yaml` (referenced by example comment).

### Feature Flags and Deployment Concerns
- Dynamic overrides via DB (`customer_config_service`), cached and warmed at `main.py:1334` before readiness at `:1345`.

---

## 6. Risk Indicators

- Existing mechanism for same purpose kept in place by task: `check_model_provider_readiness` + `/v1/healthcheck/model` + `app.state.model_provider_readiness` (Section 2, Task Statements); its rules disagree with D12/D16/AC9-11.
- Router mounts execute at module import; readiness runs in async `lifespan` after them (`main.py:1345` vs `1488`). D11's "before routers mount" has no existing hook.
- `check_model_provider_readiness` is not settings-only (default model via `llm_service`, boto3 chain, 5 s thread timeout).
- Scheduler jobs are loaded independent of routers; actors rely on same-app HTTP (`BASE_API_URL`), so AC5 behaviour depends on where the skip happens.
- `webhook`, `share`, `skill` and `a2a` routers may be reached by other features (e.g. skill MCP merge in `toolkit_service.py:1076`); cross-references untraced.
- Two STANDALONE readers (`config.STANDALONE`, `os.getenv` in code_executor_tool) may disagree.
- OpenAPI schema caches on first call (`main.py:1394`); fine if mounting is at import, a risk if mounting moves later.
- Speculative: a new runtime component id and CONFIG_IDS entry would be visible to the UI and to every `is_feature_enabled` caller.
- Speculative: `metrics`/`cli_analytics` ES use unexamined; AC7 may need more than unmounting avoidance.
- Missing tests for mount gating.

---

## 7. Summary for Complexity Assessment

The change touches configuration (`config.py`, `customer_config.py`), the composition root (`rest_api/main.py`, where router mounts are module-level and the existing readiness check runs later in `lifespan`), one service (`model_provider_readiness.py`), the scheduler/trigger path, and the standalone env example. The `STANDALONE` setting is already present. Patterns for conditional routers, runtime feature components and standalone startup logging all exist and are directly reusable; the readiness check exists but its rules (NOT_CHECKED for LiteLLM/Google/Anthropic, Bedrock credential test, whitespace trimming) differ from the story's decisions.

Novelty is moderate: the ordering change (decide availability before module-level mounts) has no precedent in `main.py`, and one availability result must feed both mount guards and the UI component list. Scheduler skip behaviour (AC5/AC6) builds on `Cron`, `_tracked_run` and the existing `skipped` run record.

Test posture: readiness, standalone startup, customer config and cron have tests; router mount gating and OpenAPI absence have none. Key risks: cross-router dependencies (skill, share, a2a, conversation_analysis), Analytics ES independence unverified for `metrics`/`cli_analytics`, duplicate STANDALONE readers.

---

## 8. External References

None named by the task (EPMCDME-16473 and child ticket are referred to by key only; no path or URL supplied).
