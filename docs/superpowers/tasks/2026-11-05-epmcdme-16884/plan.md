# Standalone Core Gating: Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox syntax.

**Goal:** In standalone mode with no LLM provider, unmount core routers, skip due schedules, log why, and expose availability to the UI. With standalone off, the new core-gating check does not run and writes nothing to the log.

**Architecture:** A settings-only, cached `core_availability(config)` feeds one runtime component `core`, the `CORE_ROUTERS` mount guard in `main.py`, a `require_core` dependency, and the scheduler skip. The legacy startup readiness check (`_check_model_provider_readiness`), `app.state.model_provider_readiness`, `/v1/healthcheck/model` and every log line added by EPMCDME-14653 are not touched.

**Tech Stack:** FastAPI, pytest, APScheduler.

**Spec:** `docs/superpowers/tasks/2026-11-05-epmcdme-16884/spec.md`

Commit per task using the repository's existing convention.

## Global Constraints

- `core_availability` never calls the network or boto3 credentials, never raises, is never called when `STANDALONE` is off, and `missing` names settings only (never values).
- "Set" means non-empty string: no strip, no placeholder detection. Stored per-project/per-user credentials are never read.
- Do not touch the legacy EPMCDME-14653 code: `_check_model_provider_readiness` and its startup call (`main.py:1345`), `app.state.model_provider_readiness`, `/v1/healthcheck/model`, `check_model_provider_readiness`, and all of their log lines stay exactly as they are in both modes. No existing test of that behavior changes. No UI changes. No runtime re-evaluation. Stored schedules are never edited or deleted. `is_standalone()` in `code_executor_tool.py:70` is left alone.
- Request paths use `require_core` / the cached value, never `is_feature_enabled("core")`.
- UI components payload must not carry `STANDALONE`.
- With standalone off, the new check does not run and the new code writes no log line at startup. In standalone, the new check writes its own outcome line(s), all prefixed `Core gating:` so they are distinct from the legacy line (which may still appear).

## Review Focus

- Partly filled provider (key, no URL) is not configured (Task 2).
- Exception inside the check means core off with ERROR log in standalone (Task 2, 5).
- Datasource cron jobs keep running with core off (Task 7).
- Importing `main` with valid Azure profile mounts core (Task 5).
- `LLM_PROXY_ENABLED` with mode not `lite_llm` follows the model's provider rule (Task 2).
- Legacy 14653 startup code and logs are untouched; new lines are distinctly prefixed (Task 6).

## Decision on AC2 scope

AC2 is satisfied for the new core-gating check only: with standalone off, the new availability check is not called and the new code logs nothing. The legacy EPMCDME-14653 startup readiness logs are intentionally out of AC2's scope, per the user's explicit decision at plan.approved (this supersedes the previous plan revision, which removed the legacy startup call). In standalone, the legacy line may appear next to the new `Core gating:` lines.

---

### Task 1: Analytics baseline test (run first)

**Files:** Test: `tests/codemie/rest_api/routers/test_analytics_no_core.py`

- [ ] Write a test that, with `STANDALONE` on, no provider settings and ES unconfigured, calls the Analytics, metrics and cli_analytics endpoints and asserts no 5xx. Run it before any other change (it should pass; if not, stop and raise it).

Test-first: no — characterization test of existing behavior; no production change.

### Task 2: `core_availability` and `require_core`

**Files:** Create `src/codemie/configs/llm_provider_availability.py`; Test `tests/codemie/configs/test_llm_provider_availability.py`

**Interfaces:** Produces `core_availability(config) -> tuple[bool, str | None, list[str]]` (available, provider, missing; `functools.cache`d per process, cache clearable in tests) and `require_core() -> None` raising `HTTPException(404)` when unavailable. Pattern sibling: `code_executor_availability.py:22`.

- [ ] Write failing tests: one case per provider (Azure key+URL; Bedrock region from `AWS_BEDROCK_REGION` or AWS config, credentials missing still available; Google Vertex project+region; Vertex Anthropic project+Claude region; Anthropic key; LiteLLM URL+app key with precedence over model provider); partial settings -> unavailable with `missing` names; placeholder `<your-azure-openai-key>` -> available; whitespace-only counts as set; no default model/unknown provider -> unavailable; `LLM_PROXY_ENABLED` with mode `other` uses model rule; only stored per-project/per-user credentials -> unavailable; patched exception -> `(False, None, ...)` plus ERROR log; `require_core` raises 404 when off.
- [ ] Run: `pytest tests/codemie/configs/test_llm_provider_availability.py -v`. Expected: FAIL (module missing).
- [ ] Implement. Read the default GLOBAL model's provider from the static `MODELS_ENV` profile directly (same default model as `model_provider_readiness.py:127`, no `llm_service` init, no call). Docstring notes `/v1/healthcheck/model` can report NOT_CONFIGURED while core is on and that a single rule table is a later follow-up.
- [ ] Run tests; expected PASS.

Test-first: yes — `test_azure_key_without_url_is_unavailable` fails because module does not exist.

### Task 3: `core` runtime component

**Files:** Modify `src/codemie/configs/customer_config.py` (`_get_runtime_config` near `:312-341`, `CONFIG_IDS` at `:30`); Test `tests/codemie/configs/test_customer_config.py`

**Interfaces:** Consumes `core_availability`. Produces component id `core`: enabled iff `STANDALONE` off, or available.

- [ ] Write failing tests: standalone off with no provider -> `is_feature_enabled("core")` True and `core_availability` not called; standalone on, no provider -> False; configured -> True; `resolve_components` payload contains `core` and no `STANDALONE` key, with standalone on and off.
- [ ] Run, expect FAIL; implement the component following the `dynamicCodeInterpreter` entry; run, expect PASS.

Test-first: yes — `test_core_component_disabled_in_standalone_without_provider`.

### Task 4: Route inventory (early)

**Files:** Modify this plan, appending a "Route verdicts" table.

- [ ] Grep `src/codemie/rest_api/routers/` for imports of assistant, workflow, conversation, skill services in `admin`, `background_tasks`, `agent_workspace`, `assistant_mapping`, `assistant_prompt_variable_mapping`. For each: verdict (CORE_ROUTERS / per-route `require_core` listing the routes / stays) with a one-line reason. Confirm `ide`, `user`, `feedback` verdicts from the spec. Task 5 implements exactly this table.

Test-first: no — investigation; output is the verdict table.

### Task 5: Mount guard, `CORE_ROUTERS`, per-route gating

**Files:** Modify `src/codemie/rest_api/main.py` (mounts `:1488-1537`, beside `register_marketplace_management_router` `:1461`), `routers/ide.py:141`, `routers/user.py` (`GET /v1/user/reactions`), `routers/feedback.py:118`, plus routes from Task 4; Test `tests/codemie/rest_api/test_core_gating.py`

**Interfaces:** Consumes `core_availability`, `require_core`. Produces `CORE_ROUTERS` tuple: `assistant, a2a, category, conversation, conversation_analysis, share, workflow, workflow_marketplace, workflow_executions, webhook, skill, skill_events, schedulers` (plus Task 4 additions). Mounted only when core is on (always when `STANDALONE` off), following SYNC NOTE CR-011 (`main.py:1468`).

- [ ] Write failing tests: standalone on and off with core off/on for mount presence and OpenAPI absence; walk `app.routes` with core off and assert nothing under `/v1/assistants`, `/v1/workflows`, `/v1/conversations`, `/v1/skills` or the IDE inference path is reachable (unmounted or 404 via `require_core`); stay-routes (IDE config, generic feedback) still work; standalone off keeps every router mounted; importing `main` with a valid Azure profile mounts core; exception in check -> core off in standalone.
- [ ] Run, expect FAIL; implement; run, expect PASS. Existing mounts (ES, litellm, marketplace) stay untouched.

Test-first: yes — `test_no_core_route_reachable_when_core_off`.

### Task 6: Startup log lines for the new check

**Files:** Modify `src/codemie/rest_api/main.py` (`_run_standalone_startup` `:609` only, adding the new `Core gating:` log lines); Test `tests/codemie/rest_api/test_standalone_startup.py`. Nothing else: `_check_model_provider_readiness`, its call at `:1345`, `routers/common.py` and their existing tests are not modified.

**Interfaces:** Consumes `core_availability`. Called only when `STANDALONE` is on.

- [ ] Write failing tests, using `caplog`:
  - `test_startup_logs_missing_settings_and_core_off` (AC13): standalone on, no provider, default flags -> startup completes without raising; one `Core gating:` record names the missing settings (names only) and states that Chats, Assistants and Workflows are off.
  - `test_startup_logs_provider_check_passed_and_provider` (AC14): provider configured -> one `Core gating:` record says the check passed and names the provider found.
  - `test_no_core_gating_check_or_log_when_standalone_off` (AC2): run startup with standalone off and no provider; assert `core_availability` is not called and caplog has no record starting with `Core gating:`.
  - `test_core_gating_lines_distinct_from_legacy_line`: standalone on; every new record starts with `Core gating:` and none contains the legacy "Model provider readiness" text; the legacy line, if emitted, is left as is. Asserts the two messages are clearly distinct and cannot be mistaken for each other.
- [ ] Run, expect FAIL; implement; run, expect PASS. Run the existing startup-readiness tests unchanged and confirm they still pass.

Test-first: yes — `test_no_core_gating_check_or_log_when_standalone_off` and `test_startup_logs_missing_settings_and_core_off`.

### Task 7: Scheduler skip

**Files:** Modify `src/codemie/triggers/bindings/cron.py` `_tracked_run` (`:80-102`); Test `tests/codemie/triggers/bindings/test_cron.py`

- [ ] Write failing tests: actor `invoke_assistant` and `invoke_workflow` with core off -> actor not called, no `SchedulerRun` row, one warning naming the schedule and reason; datasource actor with core off -> runs normally with its row; core on -> unchanged; stored schedule objects not modified.
- [ ] Run, expect FAIL; implement the check at the top of `_tracked_run`, before the `running` row, only for those two actors; run, expect PASS.

Test-first: yes — `test_assistant_job_skipped_without_row_when_core_off`.

### Task 8: Env example and docs

**Files:** Modify `standalone/.env.standalone.example` (above the provider block, lines ~19-21), `src/codemie/service/model_provider_readiness.py` docstring (comment only); Test `tests/codemie/standalone/test_standalone_compose_contract.py`

- [ ] Add the comment: any non-empty provider value counts as configured, so placeholders must be replaced. Add a contract-test assertion for it. Docstring states that `/v1/healthcheck/model` can report NOT_CONFIGURED while core is on (divergence from `core_availability`); no behavior change.

Test-first: yes — `test_env_example_warns_placeholders_count_as_configured`.

## Negative-constraint coverage

negative-constraints checked: no network/boto3/raise/strip/placeholder detection (Task 2); legacy 14653 startup check, `app.state.model_provider_readiness`, `/v1/healthcheck/model` and its log lines untouched (no task modifies them; Task 6 only adds distinct `Core gating:` lines; Task 8 is a docstring comment); new check not run and nothing new logged at startup with standalone off (Task 6); new lines distinguishable from the legacy line (Task 6); no `is_feature_enabled` on request paths (Tasks 2, 5, 7 use the cached value); no `STANDALONE` in the UI payload (Task 3); no row per tick and no schedule edits (Task 7); datasource jobs unaffected (Task 7); no UI, runtime re-evaluation or `is_standalone()` unification (no task touches them).

## AC traceability

| AC | Ticket requirement | Task(s) | Proving test(s) |
|---|---|---|---|
| AC1 | Standalone off + no provider: every endpoint as today | 3, 5 | standalone-off cases of `test_core_component...`, `test_core_gating` (all routers mounted) |
| AC2 | Standalone off: provider check does not run, writes nothing to log at startup (new check only; legacy 14653 logs out of scope by user decision) | 6 | `test_no_core_gating_check_or_log_when_standalone_off` |
| AC3 | Standalone on + provider: Chats, Assistants, Workflows, Skills, Schedulers, webhook endpoints available | 3, 5 | configured cases in `test_core_gating`; Azure-profile import test |
| AC4 | Standalone on + no provider: those endpoints not found, absent from API schema, no model call | 5 | `test_no_core_route_reachable_when_core_off`, mount/OpenAPI absence tests |
| AC5 | Due stored schedule for assistant/workflow does not run, attempt logged | 7 | `test_assistant_job_skipped_without_row_when_core_off`, workflow variant |
| AC6 | Schedules stored before core was off unchanged, run again after provider configured + restart | 7 | core-on unchanged test, stored schedule not modified test |
| AC7 | Standalone on, no provider, no ES: Analytics API responds without errors | 1 | `test_analytics_no_core` |
| AC8 | Partly filled provider settings (key, no URL) = not configured | 2 | `test_azure_key_without_url_is_unavailable` and partial cases |
| AC9 | LiteLLM proxy with URL and key both set = core available | 2 | LiteLLM precedence case |
| AC10 | Any non-empty required value incl. unchanged placeholder = configured | 2 | placeholder and whitespace cases |
| AC11 | Bedrock/Google settings set, cloud credentials missing: core stays on | 2 | Bedrock and Vertex cases |
| AC12 | Only per-project/per-user stored credentials = not configured | 2 | stored-credentials-only case |
| AC13 | Standalone on, no provider, default flags: startup completes, log names missing settings, Chats/Assistants/Workflows off | 6 | `test_startup_logs_missing_settings_and_core_off`, `test_core_gating_lines_distinct_from_legacy_line` |
| AC14 | Standalone on + provider: log states check passed and which provider | 6 | `test_startup_logs_provider_check_passed_and_provider` |
| AC15 | UI config receives feature availability only, not the standalone setting | 3 | components payload test (standalone on and off) |
| AC16 | Standalone env example comment: non-empty provider value counts as configured, replace placeholders | 8 | `test_env_example_warns_placeholders_count_as_configured` |

## Route verdicts

Grep of `src/codemie/rest_api/routers/*.py` for assistant/workflow/conversation/skill service or router imports. Prefixes: all `/v1` unless noted. "Gate" = per-route `require_core` (404).

| Router | Route (method path -> function) | Verdict | Reason |
|---|---|---|---|
| assistant_mapping | POST `/v1/assistants/{assistant_id}/users/mapping` -> `create_or_update_mapping`; GET same path -> `get_assistant_mapping` | CORE_ROUTERS | Every route is `/assistants/{id}/...` and imports assistant service and `get_assistant_by_id_or_raise`. |
| assistant_prompt_variable_mapping | POST/GET `/v1/assistants/{assistant_id}/users/prompt-variables` -> `create_or_update_prompt_variable_mapping`, `get_assistant_prompt_variable_mapping` | CORE_ROUTERS | Assistant-only routes, assistant service import. |
| admin | GET `/v1/admin/users/{user_id}/conversations/{conversation_id}` -> `get_conversation_by_ids` | Gate | Reads Conversation (core data). |
| admin | PUT `/v1/admin/users/{user_id}/conversations/{conversation_id}/feedback` -> `update_conversation_final_feedback` | Gate | Mutates Conversation. |
| admin | GET/POST `/v1/applications` (`get_applications`, `add_application`), GET `/v1/speech/config`, POST `/v1/admin/marketplace/reindex` (`reindex_marketplace_assistants`), LLM model reload/retire routes | Stays | No assistant/workflow/skill service import. Reindex is a data-sources-page action using embeddings, not a chat LLM; open decision: gate it if marketplace assistants must be fully hidden. |
| background_tasks | GET `/v1/tasks/{id}` -> `get_task` | Stays | Generic `BackgroundTasksService`, no core import. |
| agent_workspace | all 10 `/v1/workspaces...` routes (create, list, by-conversation, files list/get/download/upsert/edit/delete/grep, script execute) | Stays | Workspace file store keyed by conversation_id string only; no conversation/assistant service import, no LLM call. |
| ide | POST `/v1/ide/inference/{assistant_id}` -> `inference` (ide.py:141) | Gate | Calls `ask_assistant_by_id`. |
| ide | POST `/v1/ide/inference/virtual` -> `inference_virtual` (ide.py:91) | Gate (add; spec omits) | Uses `get_request_handler` and runs the LLM; same failure as `inference`. |
| ide | GET `/v1/ide/virtual/conversations/{virtual_assistant_id}` -> `get_virtual_assistant_conversations`; POST `/v1/ide/virtual/conversations` -> `create_virtual_conversation` (calls `conversation.crud.create_conversation`) | Gate (add; spec omits) | Conversation CRUD, core data. Spec says "other routes stay": confirm or drop. |
| ide | POST `/v1/ide/config` -> `get_config` | Stays | IDE settings only. |
| user | GET `/v1/user/reactions` -> `get_user_reactions` (user.py:421) | Gate | Joins assistant and skill interaction services. |
| user | `/user`, `/user/profile`, `/user/log_out`, GET/PUT `/user/data` | Stays | Identity and user data; `Assistant`/`SkillService` imports are used only by the reactions helpers. |
| feedback | POST `/v1/conversations/{conversation_id}/feedback` -> `final_feedback` (feedback.py:118) | Gate | Reads and updates Conversation. |
| feedback | GET/POST/DELETE `/v1/feedback`, GET `/v1/feedback/{document_id}` | Stays, except see note | Generic feedback entries. Note: `create`/`delete` (feedback.py:58, 91) import `ConversationService`/`ConversationMonitoringService` and 404 on missing conversation; they fail cleanly without conversations, so leave unless the implementer finds a model call. |

Other routers found with core imports (all already in CORE_ROUTERS): category, conversation_analysis, schedulers, skill, skill_events, workflow, workflow_executions, workflow_marketplace. No further always-mounted router imports assistant/workflow/conversation/skill services (mcp_config, settings, guardrail only mention the words, no core imports).

Gate total: 6 per-route gates beyond the spec list: admin x2, ide x4 (inference, inference/virtual, 2 virtual/conversations), user x1, feedback x1 (plus the 2 mapping routers into CORE_ROUTERS).
