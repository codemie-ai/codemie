# Spec: Standalone Core Gating: Backend (EPMCDME-16884)

## Goal
In standalone mode with no LLM provider configured, the backend unmounts Chats, Assistants, Workflows, Skills, Schedulers and webhook routers, skips due schedules, logs why, and tells the UI what is available. Everything else behaves as today.

## Approach
1. **New settings-only check** `src/codemie/configs/llm_provider_availability.py`, sibling of `code_executor_availability.py:22`. `core_availability(config)` returns `(available, provider, missing)`; `missing` names settings only, never values. It makes no network call and no boto3 credential lookup, and it never raises. It is computed once per process (D6) with `functools.cache`; the `core` component, the mount guard, `require_core` and the scheduler all read that one value. It is never called when `STANDALONE` is off. An exception while computing it means not available, logged at ERROR with the cause in standalone mode.
2. **Rules** (D12, D15-D17). Take the provider of the default GLOBAL model from the static model profile (`MODELS_ENV`) read directly, the same default model `model_provider_readiness.py:127` reads. It does not wait for the LiteLLM model initialisation in `lifespan` (`main.py:333`), so it is valid at import; nothing is called. `LLM_PROXY_ENABLED` with `LLM_PROXY_MODE` other than `lite_llm` uses the model's provider rule.
   - Azure: `AZURE_OPENAI_API_KEY` and `AZURE_OPENAI_URL`.
   - Bedrock: `AWS_BEDROCK_REGION` or a region from standard AWS config. Credentials are never checked.
   - Google Vertex: `GOOGLE_PROJECT_ID` and `GOOGLE_VERTEXAI_REGION`.
   - Vertex Anthropic: `GOOGLE_PROJECT_ID` and `GOOGLE_CLAUDE_VERTEXAI_REGION`.
   - Anthropic: `ANTHROPIC_API_KEY`.
   - LiteLLM proxy mode (`is_litellm_enabled()` and `LLM_PROXY_MODE == "lite_llm"`): `LITE_LLM_URL` and `LITE_LLM_APP_KEY`. This takes precedence over the model's provider.
   - No default model or unknown provider: not configured.
   - "Set" means a non-empty string. No strip and no placeholder detection (D17).
   - Stored per-project or per-user credentials are never read (D15).
3. **Single source** (Constraint). In standalone mode, `_get_runtime_config` (`customer_config.py:154`) emits one runtime component `core`, enabled iff `core_availability` is available. When `STANDALONE` is off it is always enabled and does not depend on provider settings. The UI receives it through the existing `resolve_components` path. Backend flag reads use `is_feature_enabled("core")` and the main.py mount guards use the same function. The UI payload never carries the `STANDALONE` value (AC15). `resolve_component` rebuilds the runtime config per read, so request paths use the cached `core_availability` value (or `require_core`, below), never `is_feature_enabled("core")` directly.
4. **Gate surface.** Core membership lives in one place: a `CORE_ROUTERS` tuple in `main.py` beside `register_marketplace_management_router` (`main.py:1461`), and one shared `require_core` dependency (beside `core_availability`) that raises 404 when core is off. Routers in `CORE_ROUTERS` are mounted only when core is available; the rest of the gate is per-route `require_core`, following the SYNC NOTE CR-011 (`main.py:1468`). Mounting happens at import and needs only settings, so `custom_openapi()` (`main.py:1394`) omits unmounted routes without changes. Gated routes return 404 and no model call is made.
   - **Unmounted** (`CORE_ROUTERS`): `assistant`, `a2a`, `category`, `conversation`, `conversation_analysis`, `share`, `workflow`, `workflow_marketplace`, `workflow_executions`, `webhook`, `skill`, `skill_events`, `schedulers`.
   - **Inventory of always-mounted routers that import gated services.** A grep of `routers/` confirms `user`, `ide` and `feedback`; `assistant_mapping` and `assistant_prompt_variable_mapping` are assistant-only by name; `admin`, `background_tasks` and `agent_workspace` are to be checked by the implementer with the same grep. Verdicts:
     - `ide`: `POST /inference/{assistant_id}` (`ide.py:141`) gets `require_core`; config and the other routes stay.
     - `user`: `GET /v1/user/reactions` gets `require_core`; the rest stays.
     - `feedback`: `POST /conversations/{conversation_id}/feedback` (`feedback.py:118`) gets `require_core`; the generic feedback routes stay unless they reach a gated service.
     - `assistant_mapping`, `assistant_prompt_variable_mapping`: added to `CORE_ROUTERS` if every route is assistant-bound (implementer confirms), else per-route `require_core`.
     - `admin`, `background_tasks`, `agent_workspace`: per-route `require_core` on any route that reaches an assistant, workflow, conversation or skill; the rest stays. Each stays-verdict is recorded with its reason in the plan.
   - A test walks `app.routes` with core off and asserts no route under `/v1/assistants`, `/v1/workflows`, `/v1/conversations`, `/v1/skills` or the IDE inference path remains reachable (unmounted, or 404 through `require_core`).
5. **Startup log.** `_run_standalone_startup` (`main.py:609`) logs the outcome only in standalone mode (D18). If the check passes, it states that and names the provider found. If not, it lists the missing settings and says Chats, Assistants and Workflows are off. Startup never fails.
6. **Scheduler (AC5, AC6).** The trigger engine and stored schedules are untouched. The check sits at the top of the shared `_tracked_run` (`cron.py:80-102`), before it opens the `running` row, and applies only when the actor is `invoke_assistant` or `invoke_workflow`; datasource jobs are unaffected. When core is unavailable it does not call the actor, writes no `SchedulerRun` row, and logs one warning per tick naming the schedule and the reason. This meets AC5 and keeps the table from growing (a row per tick is 1,440 a day per minute-level schedule, with no reader since the schedulers router is off). Nothing is deleted or modified, so after a provider is configured and the backend restarts, schedules run again.
7. **Env example.** `standalone/.env.standalone.example` gets a comment above the provider block: any non-empty provider value counts as configured, so placeholders must be replaced.
8. **Docs.** The docstrings of `llm_provider_availability.py` and `model_provider_readiness.py`, and the healthcheck doc, state that `/v1/healthcheck/model` can report NOT_CONFIGURED while core is on (different rules), and that a single rule table is a later follow-up.

## Router verdicts
- `a2a`, `share`, `category`, `conversation_analysis`: off with core. Each depends on conversations or assistants, which are off, and remote A2A assistants are off per D13.
- `analytics`, `metrics`, `cli_analytics`: stay mounted. The router layer has no Elasticsearch reference (grep of `routers/`; ES appears only in `index`, `user.py`, `workflow_executions.py`), so AC7 holds when they stay mounted. Service-layer behaviour without ES is not verified; one test covers it.
- `skill`: off. The toolkit's MCP merge of skills (`toolkit_service.py:1076`) is internal code and does not use the router.

## Acceptance criteria
AC1-AC16 of the ticket apply unchanged and are verified by these tests:
- availability rules (one case per provider, partial settings, placeholders, Bedrock without credentials, LiteLLM);
- mount and OpenAPI absence for the guarded routers, with standalone on and off;
- the `app.routes` walk with core off (Approach item 4) and `require_core` returning 404;
- importing `main` with a valid Azure profile mounts core; `LLM_PROXY_ENABLED` with `LLM_PROXY_MODE` not `lite_llm` follows the model rule; an exception in the check yields core off in standalone;
- the scheduler skip (warning, no row, actor not called), the unchanged stored schedule, and a datasource job unaffected with core off;
- startup log content in each case;
- the UI components payload lacks `STANDALONE`;
- the Analytics API responds with no ES and no provider (run first).

## Non-goals
- All UI changes.
- Voice input, smart tool selection, conversation analytics, marketplace similarity gating.
- Moving Analytics off Elasticsearch.
- The standalone switch itself (EPMCDME-16473); `config.STANDALONE` is used as is.
- Changes to `check_model_provider_readiness`, `/v1/healthcheck/model` and `app.state.model_provider_readiness` (docstring notes aside).
- Runtime re-evaluation of availability without a restart.
- Deleting or editing stored schedules.
- Unifying `is_standalone()` in `code_executor_tool.py:70` with `config.STANDALONE`.

## Decisions
- New settings-only check module over changing `check_model_provider_readiness`: its NOT_CHECKED, Bedrock-credentials and strip rules conflict with D12/D16/D17, and the healthcheck consumes it. Existing behaviour stays as today (AC1).
- Default model's provider read from the static model profile over reading only raw env: D12 names that provider, and the profile read involves no call.
- Default provider read at import, an exception meaning core off with an ERROR log in standalone, over waiting for `lifespan`: the mounts need the value at import.
- One runtime component `core` over six per-feature components: all six switch together.
- Mount guard on the cached `core_availability` value over a startup-time `app.state` flag: the guard runs at import, before `lifespan`.
- Core membership in one `CORE_ROUTERS` tuple over a hand-written inline list: a later core-adjacent router is then added in one place.
- Per-route `require_core` for gated routes on always-mounted routers over unmounting those routers: they also serve routes that must stay (IDE config, generic feedback).
- Scheduler skip in `_tracked_run` for the assistant and workflow actors over a check in the job callables: it runs before the `running` row opens and datasource jobs are untouched.
- Warning log per skipped tick and no `SchedulerRun` row over a `skipped` row per tick: no reader exists and the table would grow unbounded; AC5 needs the log.
- a2a, share, category, conversation_analysis off; Analytics, metrics, cli_analytics on.
- D1, D6, D11-D18 as given by the product owner — user

## Risks
- AC2 literal reading: the existing `_check_model_provider_readiness` (`main.py:1345`) still logs at startup when standalone is off. It is left alone because AC1 and the healthcheck depend on it; the new check logs nothing when standalone is off.
- The static profile is assumed readable at import (test: import `main` with a valid Azure profile, assert core mounted). If it is not, the fallback is raw env only, to be raised before coding.
- Startup tasks outside the gated routers that may call a model with no provider (preconfigured content, tool indexing, datasource sync) are not verified.
- Service-layer ES use of `metrics` and `cli_analytics` is unverified.
- Adding the `core` component id is visible to every `is_feature_enabled` caller.
