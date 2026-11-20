# User-Level Model Override Enforcement — Implementation Plan

> **Status:** rewritten after implementation to describe the design as built. The first version of this plan (4-tier chain, platform disablement, `override_enabled` column, async code, POST upsert) was superseded step by step during review; the step-by-step code snippets it carried are gone with it. The spec is the source of truth: `spec.md` in this folder.

**Goal:** Let a project or platform admin set a model override per user within a project (one user, or a bulk selection), and honour it whenever the user invokes a project-scoped asset or lists models: once a user has an override it is that user's whole model set, with the override's default, then the project default, then the platform default as fallback.

**Architecture:** One table (`user_model_overrides`) tied to the user's membership of the project; a synchronous repository and service for the admin side; the resolution in `ModelAvailabilityService` next to the existing project-level logic (which is not changed), applied at every place that resolves or lists a model; admin routes in the projects package. Everything is behind the customer-config flag `features:userModelOverride` (default off).

**Tech Stack:** SQLModel/SQLAlchemy (synchronous sessions), FastAPI, Alembic, pytest.

**Spec:** `docs/superpowers/tasks/2026-10-07-epmcdme-15082-user-level-model-override/spec.md`
**Ticket:** `ticket.md` in this folder (EPMCDME-15082).

## Global Constraints

- One override per `(user_id, project_name)`; `allowed_models` is never empty. A missing row means no override.
- Foreign keys: `user_id` → `users.id` (ON DELETE CASCADE) and `(user_id, project_name)` → `user_projects` (ON DELETE and ON UPDATE CASCADE), both DEFERRABLE INITIALLY DEFERRED. No key to `applications`: a project rename moves `user_projects.project_name` but not `applications.id`. Remove or rename a membership and the override follows.
- Set-time validation: 1 to 100 non-blank model ids (repeats dropped), every id names a catalog model or an enabled router, the project and the user exist, the user is a member, and `default_model` is one of `allowed_models`. No subset check against the project whitelist.
- Batch (`grant`/`revoke`): at most 500 users, atomic, and one query for the users, one for the memberships and one for the existing overrides, then a single flush.
- Resolution: the requested model if the override lists it, else the override's `default_model`, else the project default, else the platform default. At each tier a model disabled at platform level (not in the enabled-model catalog and not an enabled router, `is_known_model`) is skipped to the next tier rather than returned or raised. Asset creation keeps its check as it is: the override alone decides by membership, with no liveness check. Global assets, callers without a user, projects without a row, and the flag being off all skip the override and leave the project logic as it was.
- Every change to an override writes an activity event (`user.model_override.set` / `.removed`) in the same transaction, with the acting admin and the previous and new values. A save that changes nothing writes none.
- Authorization: the shared `_authorize_project_access` dependency (WRITE access to the project; 404 otherwise) plus the user management guard. With the flag off only the writes answer 501.
- Everything on the admin side uses the synchronous session; error responses use `ExtendedHTTPException`.

## File Structure

| Area | Files |
|---|---|
| Model, migration, flag, events | `rest_api/models/user_model_override.py` (new), `external/alembic/versions/bbba81be075b_add_user_model_overrides_table.py` (new), `external/alembic/env.py`, `config/customer/customer-config.yaml`, `service/activity/activity_models.py` |
| Admin side | `repository/user_model_override_repository.py` (new), `service/user/user_model_override_service.py` (new), `rest_api/routers/projects/user_model_overrides.py` (new), `rest_api/routers/projects/__init__.py` |
| Resolution | `service/llm/model_availability_service.py`, `service/llm/user_override_cache.py` (new), `service/monitoring/metrics_constants.py`, `rest_api/main.py` (per-request cache middleware) |
| Assistants | `service/assistant_service.py`, `rest_api/handlers/execution_model.py`, `rest_api/handlers/assistant_handlers.py`, `agents/assistant_agent.py`, `rest_api/routers/assistant/catalog.py`, `rest_api/routers/assistant/management.py` |
| Workflows | `workflows/nodes/base_node.py`, `agent_node.py`, `summarize_conversation_node.py`, `result_finalizer_node.py`, `state_processor_node.py`, `supervisor_node.py`, `workflows/workflow.py`, `workflows/supervisor_workflow.py` |
| Tools, core | `service/tools/toolkit_service.py` (OCR model), `core/dependecies.py` (`enforce_allowed_model`) |
| Model lists | `service/llm_service/llm_service.py`, `rest_api/routers/llm_models.py` |

Tests mirror these under `tests/codemie/`; the migration, the resolver, the cache, the wiring and the endpoints each have their own test module.

## Task Breakdown

Each task was done test-first and committed on its own. "Done when" is what the tests pin.

### Task 1: Model, migration, flag and event types
- Add `UserModelOverride` in its own module beside `UserProject`, with the two foreign keys, the unique and the check constraints above; import it in the alembic env so the table stays in the metadata.
- One migration creating the table and its indexes, chained on the current head.
- Customer-config flag `features:userModelOverride`, default off; two activity event types.
- **Done when:** the model declares exactly the two keys (cascade and deferral as above); the migration emits the same DDL and none for `applications`; upgrade and downgrade run on PostgreSQL.

### Task 2: Repository
- Single-row reads and writes by `(user_id, project_name)`, a paged read by project, and the bulk operations the batch needs: `get_by_users_and_project`, `upsert_many`, `delete_many` (one flush each).
- **Done when:** each method is covered, including that each user gets their own copy of the model list.

### Task 3: Service
- `set_override`, `get_override`, `delete_override`, `get_overrides_by_project`, `batch_set_overrides`, with the validation above, the shared upsert that decides whether an event is needed, the audit events, and a clear of the request cache after a write.
- **Done when:** create, update, no-op re-save, delete, batch grant and revoke, the first failing user named in order, and a failed audit write failing the change are all covered, and the batch makes the same number of queries for 3 users as for 300.

### Task 4: Resolution
- In `model_availability_service.py`: `model_in_override` (the single "is this model in the override" rule), `resolve_user_override_model` (returns `None` when no override applies), `_pick_from_override`, `user_override_allows_model` (for callers that must reject rather than substitute), and `validate_model_for_asset_creation` deciding by the override alone when one exists. A substitution is logged and counted on the existing fallback counter with a `fallback_source` label.
- `user_override_cache.py`: one loader that remembers the answer per `(user, project)` for the length of a request (including "no override"); a middleware opens the scope and the write paths clear it. Threads that do not inherit the request's context read the database each time.
- **Done when:** every row of the resolution order is covered, a disabled model at each tier (requested, override default, project default) moves resolution to the next tier, plus the flag off, global, no user, deny-all project, substitutions counted only when the model really changed, and one read per request across the resolver, the reject-style check and the model list.

### Task 5: Apply it where a model is chosen
- Assistant chat: pre-build model resolution (`resolve_execution_model` takes the user), `build_agent`, the agent's own model check, the effective model shown in the assistant catalog, and `create_assistant` passing the user to the creation check.
- Workflows: `BaseNode.resolve_user_override_model(requested, fail_open=False)`; the summarize, finalizer, state processor and supervisor nodes use it before the project resolution; the agent node uses it with `fail_open=True`; the workflow executors pass `user` to the nodes.
- The OCR model tools request by name is rejected when the override omits it; `enforce_allowed_model` tries the override first with the request user from the logging context.
- **Done when:** each call site is covered, a failure in the agent node's lookup does not abort the run, and an override without a default still blocks an excluded model in the finalizer and summarize nodes.

### Task 6: Model lists
- `LLMService.get_allowed_chat_models` filters by the override first (falling back to the project set only when none of the override's models is in the catalog); `get_allowed_router_options` takes the caller and offers only the routers the override lists, with no fallback; `GET /v1/llm_models` marks the default from the override (skipped when disabled at platform level), then the project, and passes the caller on.
- The project access check on `GET /v1/llm_models` and `/v1/default_models/{category_id}` applies only with the flag on.
- **Done when:** the list and the run agree for a user with an override, a router granted outside the project whitelist is offered, and with the flag off the endpoints behave as before.

### Task 7: Admin routes
- `routers/projects/user_model_overrides.py` in the projects package: `PUT /projects/{projectName}/user-model-overrides/{userId}` (idempotent replace), list, get, delete, and `POST .../batch`; synchronous handlers inside one transaction per request; errors mapped to 404, 400 or 409 without leaking SQL.
- **Done when:** each route's success, validation, authorization (404 without WRITE access), user management guard, flag-off behaviour (only the writes answer 501), the model-list cap and dedupe, and the error mapping are covered.

### Task 8: Gates and documentation
- `ruff check` and `ruff format`, the targeted pytest modules for each task, and the migration run on a real PostgreSQL (remap in both orders, rename, membership removal, upgrade and downgrade).
- Spec and this plan kept in step with the code; MR description with the deviations, limits and test evidence.
- **Pending outside the code:** regenerate the OpenAPI baseline (the guard is skipped until then) and run the integration harness on the final head.

## Known limitations and follow-ups

- The notice to the user before a fallback model is used: EPMCDME-16924. Until then a substitution is only logged and counted.
- Global (marketplace) assets are out of scope for this ticket. Platform-disabled models are handled only as the ticket's Context asks: resolution skips them to the next tier. A separate disablement store or hard-stop setting is not part of this ticket.
- Asset creation does not check liveness: a model the override lists passes even after it was disabled at platform level, as before. At run time resolution skips it to the next tier.
- With `projectModelOverride` off the project's stored default is not used as a fallback (the platform ignores it everywhere in that case).
- The per-request cache has not been measured under load; check override SELECTs per request with the flag on.
- The UI (codemie-ui !2054) must call `PUT .../{userId}` to set an override and has to be merged together with this change.

## Summary

Eight tasks: the table and its keys, the admin repository and service, the resolution and its cache, the call sites that choose a model, the model lists, the routes, and the gates. The override replaces the project set for the user it belongs to; everything else about project-level model availability is unchanged.
