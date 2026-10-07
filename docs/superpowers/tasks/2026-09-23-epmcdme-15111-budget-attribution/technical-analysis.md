# Technical Research

**Task**: budget billing attribution litellm project-resolution skill workflow proxy
**Generated**: 2026-09-23
**Research path**: filesystem (codegraph unavailable)

---

## 1. Original Context

EPMCDME-15111 (Budget Attribution) - sub-task of parent EPMCDME-15109, depends on EPMCDME-15110 (Default Project Foundation, DONE on this branch - adds is_default boolean on UserProject membership rows, UserProjectRepository.get_default_for_user/set_default/clear_default, UserAccessService.set_default_project/clear_default_project). Story: As a platform administrator in an organization where every project is a department with its own budget, I want the user's default project used as the billing project wherever the charged project is today arbitrary or personal, so that spend by people who work across several projects is charged to a predictable project instead of a personal budget or an arbitrary one, and analytics show the same project that paid.

Acceptance Criteria:
1. Member of marketplace assistant's project -> charge assistant's project, current behavior preserved.
2. Not a member, has default project -> charge default project's budget.
3. Not a member, no default project -> charge personal budget, current behavior preserved.
4. User in several projects with default assigned, runs a skill/workflow/any flow not bound to a specific project asset -> charged to default project not an arbitrary one.
5. User in several projects with no default assigned, unbound flow -> charged project deterministic on every request, not dependent on data ordering.
6. Default project has no budget allocation for user -> falls back to personal/default budget, fallback visible in logs/analytics not silently looking like personal usage.
7. Member of assistant's project AND has different default project -> assistant's project wins (membership beats default).
8. CLI/IDE request without explicit project, user has default -> charged to default project, same fallback rules as web.
9. CLI/IDE request with explicit project -> explicit wins regardless of default, current behavior preserved.
10. Request charged to a project -> analytics show same project whose budget was charged.
11. Admin changes default project -> new spend goes to new default within ~1min normal budget cache delay, previously recorded spend not moved.
12. User removed from project set as default -> that project no longer charged, non-default rules apply.

Out of scope: storage/API/role-gating for default project (EPMCDME-15110, done); User Management UI (EPMCDME-15112, separate); per-chat project picker; showing users their billing project; searching other projects for budget when default has none (fallback always personal); blocking requests when default has no budget; changing how project-owned assets (non-marketplace assistants, workflows, datasources) bill; changing CLI explicit project selection; re-attributing historical spend.

---

## 2. Codebase Findings

### Existing Implementations

**The billing-attribution core (web/assistant/skill/workflow path)**:
- `src/codemie/service/llm_service/utils.py:69` — `set_llm_context(asset, fallback_project_name, user)`, the single shared entry point. Calls `_resolve_effective_project(asset, fallback_project_name, user)` (same file, ~line 20):
  - `asset is None` → returns `fallback_project_name` verbatim (no arbitrary pick happens *inside* this function).
  - `asset.is_global` (marketplace/global asset) → if `project in (user.project_names | user.admin_project_names)` return `project` (membership wins), else fall back to `user.email` (personal).
  - otherwise → returns `asset.project`/`asset.project_name` directly.

**THE ROOT BUG (AC4/AC5)**: `User.current_project` — `src/codemie/rest_api/security/user.py:112-115`:
  ```python
  @property
  def current_project(self) -> str:
      apps = self.project_names if self.project_names else [DEMO_PROJECT]
      return apps[0]
  ```
  Picks `project_names[0]` — first element of a `list[str]` populated from an **unordered** DB query (see below) — with zero awareness of `is_default`. This property is the `fallback_project_name` passed by 9 of the "unbound flow" call sites into `set_llm_context(None, user.current_project, user)`:
  - `src/codemie/rest_api/routers/assistant.py:1829` (`generate_assistant`)
  - `src/codemie/rest_api/routers/assistant.py:1868,1874` (`generate_assistant_prompt`)
  - `src/codemie/rest_api/routers/assistant.py:1908` (`refine_with_ai` — `request.project or user.current_project`, explicit project already wins here)
  - `src/codemie/rest_api/routers/assistant.py:3156,3189` (marketplace index/remove background tasks)
  - `src/codemie/rest_api/routers/workflow_executions.py:522` (`request_workflow_execution_output_changes` — fetches `workflow_config` at line 516 but does NOT pass it as `asset`, passes `None` + `user.current_project` instead; a related pre-existing quirk, separate bug from the ordering issue)
  - `src/codemie/rest_api/routers/workflow.py:752,795` (`generate_workflow`, `refine_workflow`)
  - `src/codemie/rest_api/routers/skill.py:776,804,844` (`generate_skill`, `refine_skill`, `generate_skill_instructions`)

  **Skill/assistant/workflow *chat execution*, by contrast, is already bound**: there is no separate "skill execution service" — skills execute as assistant tool calls inside `LangGraphAgent`, which always calls `set_llm_context(self.assistant, None, self.user)` (`src/codemie/agents/langgraph_agent.py:609,636,675,737,1010`; also `tool_call_confirmation_mixin.py:109`, `workflow.py:982`, `assistant_service.py:524,808`, `assistant_handlers.py:432`, `assistant.py:1498`, `base_datasource_processor.py:283`). These are bound to a real asset and go through the `is_global` membership-check branch above — this is the AC1/2/3/7 surface, not the AC4/5 surface. **AC4/5's "unbound flow" is specifically the 9 AI-generation endpoints listed above** (generate/refine assistant, workflow, skill; plus 2 marketplace background tasks), not chat/skill execution itself.

**Where `project_names` ordering comes from (why AC5's non-determinism is real)**:
- `User.project_names: list[str]`, `User.admin_project_names: list[str]` — `src/codemie/rest_api/security/user.py:43-44`.
- Populated at two construction sites, both via `UserProjectRepository.aget_by_user_id()` (`src/codemie/repository/user_project_repository.py:595-599` — `select(UserProject).where(UserProject.user_id == user_id)`, **no ORDER BY**):
  - `AuthenticationService.load_user_for_auth()` — `src/codemie/service/user/authentication_service.py:140-174`, builds `project_names=[p.project_name for p in projects]` at line 167.
  - `LocalIdp` (local dev IDP) — `src/codemie/rest_api/security/idp/local.py:82-117`, identical pattern.
- **`is_default` is never read at either construction site.** Confirmed nowhere on the `User` (security) model — no `default_project` field/property/computed_field exists.

**Where `is_default` DOES exist today (EPMCDME-15110 scope, admin-facing only, never on runtime `User`)**:
- `UserProjectRepository.get_default_for_user/set_default/clear_default` — `src/codemie/repository/user_project_repository.py:128-199`.
- `UserAccessService.set_default_project/clear_default_project` — `src/codemie/service/user/user_access_service.py:138-197`.
- `UserAccessService.get_user_projects_list()` — exposes `is_default` in the admin listing dict only.
- `ProjectInfo(..., is_default=...)` DTOs in `user_management_service.py`, `registration_service.py`, `authentication_service.py:732`.

**CLI/IDE proxy path (real file locations — differ from ticket's assumed paths)**:
- `src/codemie/enterprise/litellm/proxy_router.py:539` — `async def _probe_project_budget_scopes(project_name, user_id) -> set[BudgetCategory]` (repository-based, uses `_resolution_cache`).
- `src/codemie/enterprise/litellm/llm_factory.py:631` — `def _probe_direct_project_budget_scopes(project_name, user_id) -> set[CoreBudgetCategory]` (sync, raw SQL via `sqlalchemy.text()`, populates the *same* `_resolution_cache`).
- **These two do NOT live at `rest_api/routers/proxy_router.py` / `service/llm_service/llm_factory.py` as previously assumed** — correct paths are `src/codemie/enterprise/litellm/{proxy_router,llm_factory}.py`. Must still be edited in lockstep — never unified/refactored (explicit prior instruction).
- **AC8/AC9 root location**: `_extract_request_info()` — `src/codemie/enterprise/litellm/proxy_router.py:412-427`:
  ```python
  project = headers.get(HEADER_CODEMIE_CLI_PROJECT) or (user.username if user else "")
  ```
  "Explicit project" = the `HEADER_CODEMIE_CLI_PROJECT` header (AC9, already correct — explicit wins). When absent, falls straight to `user.username` (personal identity) — **never consults a default project**. This is the exact line needing a default-project check before the personal fallback, for AC8. Called once, from `proxy_router.py:1739`.

**Budget resolution / fallback-to-personal (AC3/AC6)**:
- `ProjectBudgetAssignmentRepository.get_project_budget_context()` — `src/codemie/repository/project_budget_repository.py:327-382`. INNER JOIN across `project_budget_assignments` / `project_member_budget_assignments` / `budgets`, filtered `deleted_at IS NULL AND b.is_active = TRUE AND pmba_deleted_at IS NULL`. Returns `None` when no active project budget OR no per-user member allocation exists — this `None` is exactly AC6's "default project has no budget for this user" case.
- `BudgetResolutionService.resolve()` (async) / `.resolve_sync()` — `src/codemie/service/budget/budget_resolution_service.py:64-132` / `134-237`. `project_name` falsy → `_global_context()` immediately (DEBUG log, `reason=missing_project_name`). Repository returns `None` → caches negative, **`return self._global_context(budget_category)`**, logging at **INFO** with `budget_event=budget_resolution_global_fallback ... reason=project_budget_not_found` (`project_name` still present in the log). **This INFO-level structured fallback log already exists and already satisfies AC6's "fallback visible in logs" requirement** for the project-has-no-budget case — it just needs to actually fire once callers pass a default project instead of `None`.
- `_global_context()` (`budget_resolution_service.py:345-352`) returns `ResolvedBudgetContext(scope=GLOBAL, project_name=None, ...)`, which routes to the pre-existing personal/global budget-ID lookup (`budget_service.get_all_category_budget_ids_for_request[_sync]` → `get_category_budget_id`).
- Callers: `proxy_router.py:863` (`_resolve_project_budget_runtime`), `project_member_runtime_sync.py:104`, `llm_factory.py:818`.

**Analytics/metrics attachment (AC10)**:
- `get_current_project(fallback=None)` (`src/codemie/core/dependecies.py:137-147`) reads the LiteLLM ContextVar set by `set_llm_context`. `get_project_for_metric()` (lines 150-155) wraps it, falling back to `current_user_email`.
- Concrete spend/analytics sink: `ConversationMonitoringService.send_conversation_metric()` (`src/codemie/service/monitoring/conversation_monitoring_service.py:49-84`) — `attributes[PROJECT] = get_current_project(fallback=assistant.project)` (line 72) alongside `MONEY_SPENT`, emitted together via `send_count_metric(name="conversation_assistant_usage", ...)`. Same pattern in `SkillMonitoringService` (`skill_monitoring_service.py:62,110,160,211,250,295`) and ~20 other `*_monitoring_service.py` files. Backend: OpenTelemetry counters (`base_monitoring_service.py:86-115`), exported to Prometheus/Grafana (`prometheus.yml` present at repo root).
- **Risk for AC10**: the web/assistant path's project tag (`get_current_project`, LiteLLM ContextVar) and the CLI/proxy path's project value (`request_info.get(PROJECT)` dict) are two independently-populated values today. Any default-project fallback change must keep both in sync with whatever `BudgetResolutionService.resolve()` actually charged, or AC10 (analytics matches charged project) can silently drift.

**Cache TTL/invalidation (AC11)**:
- `_resolution_cache` — `budget_resolution_service.py:35-38`, `TTLCache(maxsize=50000, ttl=60)` (`config.BUDGET_RESOLUTION_CACHE_TTL/MAX_SIZE`, `config.py:289-290`). Key: `(project_name, budget_category_value, user_id)`. Value: `ResolvedBudgetContext | None`. Invalidation: TTL-only for normal ops; explicit full `.clear()` only on budget lifecycle events (activate/deactivate/backfill), never on default-project change. **This is fine for AC11 as stated**: since the cache key includes `project_name`, changing a user's default to a different project name is a natural cache-key change (miss on the new project, old entries just age out within the 60s TTL) — no explicit invalidation needed for `set_default_project`/`clear_default_project` to satisfy the "~1min normal budget cache delay" tolerance.
- `_budget_assignment_cache` — `budget_service.py:62-68`, same TTL/maxsize, keyed `(user_id, category_value)` — user-level, not project-scoped; not directly implicated by default-project logic, explicitly invalidated on user-budget-assignment writes.

**Membership removal correctness (AC12)**:
- `UserProject` is a single row per `(user_id, project_name)` carrying both membership and `is_default` (added by EPMCDME-15110's migration `bf3cb9db22b7`, with a partial unique index enforcing at most one default per user).
- All removal paths hard-delete the row: `UserProjectRepository.remove_project()`/`.aremove_project()` (`user_project_repository.py:109-126`, `628-636`), called from `UserAccessService.revoke_project_access()`, `ProjectAssignmentService` (line 788, project-member removal), `project_service.py:539`, `personal_project_service.py:137`. **No bypass path found.** Deleting the row removes `is_default=True` with it — `get_default_for_user()` naturally returns `None` immediately after. **AC12 is already satisfied at the data layer**, provided the new attribution code re-resolves the default per request (via `get_default_for_user` or an equivalently freshly-loaded field) rather than caching a stale value on a long-lived `User` object.

### Architecture and Layers Affected

- **Security/auth layer**: `src/codemie/rest_api/security/user.py` (`User.current_project` property, `project_names`/`admin_project_names` population via `AuthenticationService`/`LocalIdp`) — likely needs a `default_project` field added here plus a fix to how `current_project` resolves, OR calls that use `user.current_project` need to instead resolve the default directly via a service/repository call.
- **LLM billing-attribution layer**: `src/codemie/service/llm_service/utils.py` (`set_llm_context`, `_resolve_effective_project`) — the `is_global` membership branch needs a default-project fallback tier inserted between membership and `user.email`.
- **9 "unbound flow" REST router handlers**: `assistant.py`, `workflow.py`, `workflow_executions.py`, `skill.py` — all currently pass `user.current_project` as `fallback_project_name`.
- **Enterprise/LiteLLM proxy layer**: `src/codemie/enterprise/litellm/proxy_router.py` (`_extract_request_info`) and `llm_factory.py` — CLI/IDE "no explicit project" fallback.
- **Budget resolution layer**: `src/codemie/service/budget/budget_resolution_service.py` — likely unchanged (fallback-to-global logic already correct); only the `project_name` fed into `.resolve()`/`.resolve_sync()` changes upstream.
- **Monitoring/analytics layer**: `*_monitoring_service.py` files — likely unchanged (already reads the same ContextVar), but worth a consistency check per AC10.

### Integration Points

- `set_llm_context` → `SettingsService.get_litellm_creds`/`get_dial_creds` → `set_litellm_context`/`set_dial_credentials` (LiteLLM/DIAL credential + context propagation).
- `BudgetResolutionService.resolve()` → `ProjectBudgetAssignmentRepository.get_project_budget_context()` → Postgres (`project_budget_assignments`, `project_member_budget_assignments`, `budgets` tables).
- `_extract_request_info()` → LiteLLM proxy request handling → `_resolve_budget_availability` → `_probe_project_budget_scopes`/`_probe_direct_project_budget_scopes`.
- Monitoring services → OpenTelemetry meter → Prometheus/Grafana.

### Patterns and Conventions

- Structured logging convention used pervasively in budget code: `logger.info/debug(f"budget_event=<name> component=<module> user_id=... project_name=... ...")`. Any new default-project-fallback logging (AC6/AC8) should follow this exact `budget_event=` key=value convention.
- Repository methods come in sync/async pairs (`remove_project`/`aremove_project`, `get_default_for_user` likely has both per EPMCDME-15110's pattern).
- Dual proxy/direct implementations (`proxy_router.py` async+repository vs `llm_factory.py` sync+raw-SQL) are a deliberate existing duplication in this codebase, not an oversight — confirmed via explicit prior user instruction not to unify them.

---

## 3. Documentation Findings

### Guides and Architecture Docs

Per `AGENTS.md`'s guide-import table, relevant guides: `.ai-run/guides/data/database-patterns.md`, `.ai-run/guides/data/repository-patterns.md` (repository conventions), `.ai-run/guides/development/configuration-patterns.md` (TTLCache config pattern), `.ai-run/guides/development/logging-patterns.md` (the `budget_event=` structured logging convention). Not read in full this pass; load the relevant one(s) at plan/implementation time per AGENTS.md's "Check Guides First" rule.

### Architectural Decisions

Alembic migration docstring, `src/external/alembic/versions/bf3cb9db22b7_add_is_default_to_user_projects.py:7-10`: "Additive only, no backfill... A partial unique index enforces at most one default per user at the DB layer, backstopping the application-level swap-old-for-new transaction." This guarantees EPMCDME-15111 can trust "at most one default project per user" as a DB-level invariant, not just an application convention.

### Derived Conventions

Membership-beats-default (AC7) is already the natural order of the existing `is_global` branch in `_resolve_effective_project` (membership check happens first, personal fallback second) — inserting a default-project tier between them preserves this order without restructuring the branch.

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/service/llm_service/test_utils_litellm_context.py` — covers `_resolve_effective_project`/`set_llm_context` for global/private/shared assets and credential nulling; explicitly tests `asset=None` → returns `fallback_project_name` verbatim (lines 377-389).
- `tests/codemie/service/user/test_user_access_service_default_project.py` — covers `UserAccessService.set_default_project`/`clear_default_project` (EPMCDME-15110 scope) with mocked repository.
- `tests/codemie/service/budget/test_budget_resolution_service.py` — exists, covers `BudgetResolutionService`.
- `tests/enterprise/litellm/test_proxy_router.py` — exists, presumably covers `_probe_project_budget_scopes`/`_extract_request_info`/`_resolve_budget_availability` (not opened in full this pass).

### Testing Framework and Patterns

pytest 8.3.1, pytest-asyncio 0.23.7, pytest-mock 3.14.0, pytest-httpx 0.35.0. Heavy `unittest.mock.MagicMock`/`@patch` use for services/repositories. `_make_user()`-style helpers build a `MagicMock` with `.email`, `.project_names`, `.admin_project_names`, `.id`, `.username` set explicitly — will need a `.default_project`-equivalent attribute once implemented, and any test asserting on `project_names` order will need explicit determinism coverage.

### Coverage Gaps

- No test exercises `User.current_project`/`project_names` ordering determinism — the exact AC5 regression surface.
- No test exercises `_resolve_effective_project`'s `is_global=True` + "member of neither list" branch together with a default-project scenario (AC2, AC7).
- No test correlates `BudgetResolutionService.resolve()`'s fallback-to-global path with the `MetricsAttributes.PROJECT` value emitted by monitoring services (AC10).
- No test exists for `_extract_request_info`'s no-header fallback together with a default project (AC8).

---

## 5. Configuration and Environment

### Environment Variables

Not individually grepped for `os.environ`/`getenv` this pass; config fields below are pydantic-settings style, presumably env-var-backed per the codebase's established pattern.

### Configuration Files

`src/codemie/configs/config.py`: `BUDGET_RESOLUTION_CACHE_TTL=60`, `BUDGET_RESOLUTION_CACHE_MAX_SIZE=50000` (lines 289-290), `BUDGET_ASSIGNMENT_CACHE_TTL=60`, `BUDGET_ASSIGNMENT_CACHE_MAX_SIZE=50000` (lines 285-286).

### Feature Flags and Deployment Concerns

`config.LLM_PROXY_SHARED_ASSET_PROJECT_BUDGET_ROUTING_ENABLED` — gates whether a shared/marketplace asset forces project-budget routing (nulls personal creds) in `_is_shared_asset_creds_override()` (`utils.py:59-66`). Sits in the same billing-attribution decision path; interacts with whatever effective-project value default-project logic picks. Worth a compatibility check during implementation, not necessarily a change.

---

## 6. Risk Indicators

- **Two independently-populated "current project" concepts today** (LiteLLM ContextVar via `get_current_project`, vs. the CLI proxy's `request_info` dict) must both reflect the same default-project fallback consistently, or AC10 (analytics matches charged project) can silently drift between the web and CLI paths.
- **Non-deterministic list ordering is a real, confirmed bug**, not a theoretical one: `aget_by_user_id()` has no `ORDER BY`, so `project_names[0]` truly can vary across requests/replicas — AC5 is not a hypothetical edge case.
- **`User.current_project` has 9 call sites**, all AI-generation endpoints (not chat/skill/workflow execution, which is already correctly bound to a real asset) — the fix must either patch the property itself (affects all 9 uniformly) or patch each call site (more surgical, but must not miss any of the 9, plus the `workflow_executions.py:522` quirk where a real asset is already in scope but unused).
- **Two independently-implemented budget-scope-probe functions** (`_probe_project_budget_scopes` vs `_probe_direct_project_budget_scopes`) — confirmed still real, must be edited in lockstep per explicit prior instruction; a mismatch between them would silently break CLI budget enforcement for one of the two proxy paths.
- **File paths in the original ticket text for the CLI proxy functions were wrong** (`rest_api/routers/proxy_router.py`/`service/llm_service/llm_factory.py` don't exist) — actual paths are `src/codemie/enterprise/litellm/{proxy_router,llm_factory}.py`. Plan must use the corrected paths.
- **No `default_project` concept exists anywhere on the runtime `User` object** — this is new surface area, not a modification of existing surface. Two implementation options exist (add a field to `User`, populated at both construction sites; or resolve on-demand via `get_default_for_user` at each call site) — this is a real design decision for the spec/plan stage, not yet settled by research.
- **AC6's "fallback visible in logs" requirement may already be satisfied** by `BudgetResolutionService`'s existing INFO-level `budget_event=budget_resolution_global_fallback ... reason=project_budget_not_found` log — implementation may only need to confirm this fires correctly once default projects flow through, not build new logging.
- **AC12 may already be satisfied at the data layer** (hard-delete removes `is_default` with the row) — implementation risk is only in *not* caching a stale default on a long-lived `User`/session object, not in the repository/migration layer.

---

## 7. Summary for Complexity Assessment

This task touches five layers: the security/auth `User` model (adding default-project awareness where none exists today), the shared LLM billing-attribution function (`_resolve_effective_project`, inserting one new fallback tier), nine REST router call sites across four router files (`assistant.py`, `workflow.py`, `workflow_executions.py`, `skill.py`) that currently pass a buggy `user.current_project` fallback, and the CLI/IDE proxy's request-info extraction (one function, `_extract_request_info`) plus its two independently-duplicated budget-scope probes that must move in lockstep. Budget resolution and analytics/monitoring layers are believed correct as-is and mostly need verification rather than modification, which reduces scope versus a naive reading of the 12 ACs.

Technical novelty is low-to-moderate: this is fallback-chain insertion into existing, well-understood resolution functions, not new architecture. The main design decision left open is *how* the default project reaches these call sites — as a new field materialized once per `User` load (touching two construction sites and requiring an ordering/determinism fix as a side effect) versus an on-demand repository lookup at each of the ~10 call sites (more call sites touched, but no `User` model change and no auth-flow risk). Test coverage for the exact regression surfaces (list ordering, `is_global` membership+default interplay, CLI header-absent+default) is currently zero, so every AC needs new test coverage, not just extension of existing suites. Risk concentrates in keeping the CLI (`llm_factory.py`) and proxy (`proxy_router.py`) duplicated probes synchronized, and in not silently caching a stale default across a long-lived session (AC11/AC12's time-bound correctness requirements).
