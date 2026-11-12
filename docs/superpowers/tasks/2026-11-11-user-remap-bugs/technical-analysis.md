# Technical Research

**Task**: user remap project-assignment workflow
**Generated**: 2026-11-11
**Research path**: filesystem

---

## 1. Original Context

fix 2 bugs:

1A user remapped to a non-UUID id (via POST /v1/admin/users/remap) can never be removed from a project afterward. Both member-removal endpoints validate user_id as UUID-only, while the remap feature's own ids (intended "pre-Keycloak" ids) don't satisfy that format. As a result, a project containing such a remapped user becomes permanently undeletable, since there is no API path to clear the assignment.
Preconditions
    Environment: preview (codemie-preview.lab.epam.com)
    Reproduced via codemie-sdk-python 0.1.713
    A user exists and is assigned to a project
    The user has been remapped via POST /v1/admin/users/remap with new_user_id set to a non-UUID string (e.g. autotest-remap-adc036d3ea7e) — a documented, intended use of remap for pre-Keycloak ids
Steps to Reproduce
    Create a user and assign them to a project.
    Send POST /v1/admin/users/remap with new_user_id set to a non-UUID string (e.g. autotest-remap-adc036d3ea7e).
    Attempt DELETE /v1/projects/<project>/assignment/<new_user_id> (single removal) or DELETE /v1/projects/<project>/assignments?user_id=<new_user_id> (bulk removal).
Expected Result
Either:
    These endpoints accept the same id format that GET /v1/admin/users/<user_id> and related endpoints already accept (confirmed: get_admin_user resolves the non-UUID id fine), OR
    The remap documentation should explicitly state that remapped users can never leave a project through the normal admin API.
Actual Result
Both removal endpoints return:
400 Bad Request: {"error":{"message":"Invalid user_id format","details":"user_id must be a valid UUID"}}
Additional confirmed findings:
    DELETE /v1/projects/<name> returns 409 with "cannot be deleted because it has assigned users" and there is no way to clear that assignment.
    Removal via the pre-remap UUID also fails (404 "User not found" — expected, since remap rewrites the row's PK in place and the old id no longer exists).
    Deactivating the user first does not help either — deactivation doesn't clear project assignment.
    Test-harness consequence: test_remap_moves_owned_content_to_new_user_id (PR #55) leaks one project on every run, deterministically, for this reason.
Affected Areas
    DELETE /v1/projects/<project>/assignment/<user_id> (single member removal)
    DELETE /v1/projects/<project>/assignments?user_id=<user_id> (bulk member removal)
    POST /v1/admin/users/remap (identity-remap feature)
    Project deletion flow (DELETE /v1/projects/<name>), blocked indirectly by this issue
    User-id format validation shared across admin/project endpoints
Acceptance Criteria
1. Given a user was remapped to a non-UUID new_user_id via POST /v1/admin/users/remap, when an admin calls DELETE /v1/projects/<project>/assignment/<new_user_id>, then the removal succeeds instead of returning 400 "Invalid user_id format".
2. Given the same scenario, when an admin calls DELETE /v1/projects/<project>/assignments?user_id=<new_user_id> (bulk removal), then the removal succeeds instead of returning 400.
3. Given a project whose only assigned user was remapped to a non-UUID id and has since been removed, when an admin calls DELETE /v1/projects/<name>, then the project is deleted successfully (no 409 "has assigned users").
4. The id-format validation used by both removal endpoints accepts the same id formats already accepted by GET /v1/admin/users/<user_id> and related admin endpoints.
5. test_remap_moves_owned_content_to_new_user_id (PR #55) no longer leaks a project on any run.

2.Description
POST /v1/admin/users/remap reliably rewrites project membership, budget member allocation, assistant ownership, and conversation ownership to the new user id, but intermittently (observed ~60–70% of runs, small sample) leaves a workflow's created_by pointing at the old (now-nonexistent) user id.
Environment: Preview, codemie-sdk-python 0.1.713.
Preconditions
    A disposable test user exists and can create content.
    The /v1/admin/users/remap endpoint is available and callable to remap old → new user id.
Steps to Reproduce
    As a disposable user, create a project, an assistant, a conversation on that assistant, and a workflow (sequential, one state/assistant) in that project.
    Confirm all four resolve under the old user id.
    POST /v1/admin/users/remap old → new.
    Re-fetch all four under the new user id.
Expected Result
Workflow's created_by should move to the new id on every remap, same as the other content types, matching the SDK's own remap_user docstring ("rewrites ... every table that references it ... denormalized user_id/created_by columns ... in one transaction").
Actual Result
    Project membership, budget allocation, assistant created_by, and conversation user_id move to the new id every time observed (0 failures across ~9 runs).
    The workflow's created_by.user_id sometimes stays on the old id — observed failing in roughly 6 of 9 pytest-driven runs of test_remap_moves_owned_content_to_new_user_id, both solo (-n1, zero concurrency) and under -n5.
    Key evidence against a timing/propagation explanation: an 8-attempt poll (14+ seconds, 2s interval) was added right before the failing assertion. When the bug occurs, the value never changes across all 8 attempts — it is not a replication-lag race that resolves with waiting; the transaction appears to have genuinely skipped the workflow row on that remap. This rules out "just wait longer" as a test-side fix.
Affected Areas
    POST /v1/admin/users/remap endpoint / remap_user service logic
    Workflow created_by denormalized column handling during identity remap
    test_remap_moves_owned_content_to_new_user_id (pytest, CI/local)
Impact
Intermittent, non-deterministic failure of test_remap_moves_owned_content_to_new_user_id in CI/local runs, roughly 60–70% failure rate in this sample. Not fixable from the test side without either accepting flakiness or weakening the assertion (which would hide a real data-integrity gap: a workflow silently left owned by a deleted/nonexistent user id after migration).
Acceptance Criteria
1. On every invocation of /v1/admin/users/remap (old → new), the workflow's created_by.user_id is updated to the new user id, with 0 failures across repeated runs (solo and concurrent, e.g. -n1 and -n5).
2. The fix addresses the root cause within the remap transaction (the workflow row is not skipped), not merely by adding a wait/retry in the test.
3. test_remap_moves_owned_content_to_new_user_id passes consistently in CI across multiple consecutive runs under both -n1 and -n5.
4. Regression test coverage confirms workflow created_by remap behavior is included and verified alongside the other content types (project membership, budget allocation, assistant, conversation).

Coordinator clarification: there is only ONE user remap endpoint (POST /v1/admin/users/remap) and one service path (`UserRemapService.remap_user_flow` -> `remap_user`). Bug 1 is its consequence on the project member-removal endpoints; bug 2 is the workflow created_by gap around that same remap logic.

---

## 2. Codebase Findings

### Existing Implementations

**Remap (single code path)**
- `src/codemie/rest_api/routers/user_management_router.py:271-282` — `POST /v1/admin/users/remap` (`admin_access_only`, gated on `config.ENABLE_USER_MANAGEMENT`), delegates to `UserRemapService.remap_user_flow(old, new, actor)`.
- `src/codemie/rest_api/models/user_management.py:255-271` — `UserRemapRequest`: `old_user_id`/`new_user_id` are `str`, `min_length=1`, `pattern=r"\S"`. No UUID constraint — arbitrary non-UUID ids are accepted by design.
- `src/codemie/service/user/user_remap_service.py` — `UserRemapService`:
  - `remap_user_flow` (l.147) owns `get_session()` and the commit.
  - `remap_user` (l.163): `_lock_old_user_row` (SELECT ... FOR UPDATE on `users`), `_preflight_check_conflicts` (409), `_remap_core_row_and_true_fk_tables` (`SET CONSTRAINTS ALL DEFERRED`, `UPDATE users SET id`, `_USER_ID_FK_TABLES` incl. `user_projects`, `codemie.user_enrichment`), `_update_plain_id_columns` (`_PLAIN_ID_COLUMNS`, incl. `applications.created_by`, `conversations.user_id`, `project_member_budget_assignments.user_id`), `_update_fk_columns_referencing_users_id` (pg_constraint discovery), `_update_created_by_jsonb`, then an activity event `USER_REMAPPED`.
  - `_USER_ENTITY_JSONB_COLUMNS` (l.137-140) = `("workflows","created_by")`, `("workflow_executions","created_by")`; SQL (l.294-302): `UPDATE workflows SET created_by = jsonb_set(created_by, '{user_id}', to_jsonb(CAST(:new_id AS text))) WHERE created_by->>'user_id' = :old_id`. The JSON path matches the stored shape (`UserEntity.user_id`, `src/codemie/core/models.py:84-87`) and the index `ix_workflows_created_by_user_id` on `(created_by->>'user_id')` (`workflow_config.py:129`). The in-transaction SQL itself is correct.
  - Not touched by remap: `workflows.updated_by` (also a `UserEntity`, `workflow_config.py:101`), `workflows.yaml_config_history[].created_by` (`YamlConfigHistory.created_by: UserEntity`, l.67). No `invalidate_user_from_cache` call anywhere in the remap service (contrast `user_management_service.py:1431`).

**Workflow storage**
- `src/codemie/core/workflow_models/workflow_config.py:321-` — `WorkflowConfig(BaseModelWithSQLSupport, ..., table=True)`, `__tablename__ = "workflows"` (PostgreSQL only, no ES index for workflows). `created_by: Optional[UserEntity]` stored via `PydanticType(UserEntity)` JSONB column (l.84). `WorkflowConfig.update()` (l.326-339) re-reads `current = find_by_id(self.id)` but copies back ONLY `yaml_config_history`, then calls `super().update()`.
- `src/codemie/rest_api/models/base.py:532-546` — `BaseModelWithSQLSupport.update()` does `session.merge(self); session.commit()` — a full-row write of every field of the in-memory object, including `created_by`.

**Root cause of bug 2 (concrete, from code)** — a lost update by a post-response background task, not a skip inside the remap transaction:
- `src/codemie/rest_api/routers/workflow.py:520-559` `_persist_and_finalize_new_workflow`: after `workflow_service.create_workflow(...)` (which sets `workflow_config.created_by = user.as_user_model()` and `save()`s, `workflow_service.py:216-222`), it schedules `background_tasks.add_task(run_in_thread_pool, update_workflow_schema, workflow_config, user)` with the same in-memory object.
- `workflow.py:904-911` `update_workflow_schema` -> `WorkflowExecutor.validate_workflow_and_draw` (`src/codemie/workflows/workflow.py:299-327`, which builds the graph and calls the external Mermaid renderer `MermaidService.draw_mermaid`, timeout `MERMAID_SERVER_TIMEOUT=50`s) -> `workflow_service.save_workflow_schema(workflow_config, schema)` (`workflow_service.py:800-815`), which sets `schema_url` and calls `workflow_config.update()`.
- That `update()` merges the stale object (still holding `created_by.user_id = <old id>`) back into the row. If the remap commits after the create response but before the Mermaid render + update finishes, the remap's correct rewrite is overwritten with the old id. Afterwards nothing changes the value again — which is why the 14 s poll sees a constant stale value. The variable Mermaid latency explains the ~60-70% intermittency, at both -n1 and -n5. Assistants and conversations have no equivalent stale full-row write, so they never fail.
- Same stale-object pattern is scheduled on workflow update (`workflow.py:698`, `updated_workflow`) and rollback (`workflow.py:1051`, `rolled_back`), so a remap concurrent with any of those can also be reverted. Under READ COMMITTED, a merge that runs while the remap holds the row lock waits for the remap commit and then overwrites it.
- The write only happens when `workflow_schema` is truthy (Mermaid configured and rendering succeeds); with no renderer the race does not occur.

**Bug 1 — UUID validation**
- `src/codemie/service/project/project_assignment_service.py:255-270` `_validate_user_id_format`: skipped when `Environment.LOCAL.value == config.ENV`; otherwise `uuid.UUID(user_id)` -> 400 `"Invalid user_id format"` / `"user_id must be a valid UUID"`. Callers: `assign_user_to_project` (l.303), `update_user_project_role` (l.380), `bulk_assign_users_to_project` (l.472), `bulk_remove_users_from_project` (l.610), `remove_user_from_project` (l.685).
- Routes: `src/codemie/rest_api/routers/projects/membership.py:92-113` `DELETE /projects/{projectName}/assignment/{userId}` -> `remove_user_from_project`; `membership.py:226-` `DELETE /projects/{projectName}/assignments?user_id=...` -> `bulk_remove_users_from_project`. The admin route `DELETE /v1/admin/users/{user_id}/projects/{project_name}` (`user_management_router.py:389-400`) -> `user_access_service.revoke_project_access` (`user_access_service.py:206-222`) also delegates to `remove_user_from_project`, so it hits the same validator. No removal path bypasses it.
- Comparison: `GET /v1/admin/users/{user_id}` (`user_management_router.py:187-207`) takes `user_id: str` and calls `user_management_service.get_user_detail` (`user_management_service.py:758-`), which does a plain DB lookup with no format check. Other admin user routes (`PUT /{user_id}`, `DELETE /{user_id}`, `/reactivate`, `/projects`, `/budgets`) also take bare `str`.
- Other UUID checks (422 "User ID must be a valid UUID") exist in `src/codemie/service/jwt_service.py:153` and `src/codemie/service/user/authentication_service.py:196,425`. They are on auth/token paths, not on the removal endpoints.
- Ordering inside removal: `_reject_if_personal_project` -> `_reject_if_creator` (400 if target is `project.created_by`) -> `_validate_user_id_format` -> existence 404 -> remove -> `_sync_project_budget_member_removed` -> activity event.

**Project delete check**
- `src/codemie/service/project/project_service.py:715-790` (delete flow) builds `assigned` from `user_project_repository.get_by_project_name`, excluding `up.user_id == creator_id`. If any remain -> 409 `HAS_ASSIGNED_USERS` (l.142-144). `creator_id` is passed as `authorized_project.created_by` from `src/codemie/rest_api/routers/projects/management.py:199`. Remap rewrites `applications.created_by` (`_PLAIN_ID_COLUMNS`), so a creator who was remapped is still excluded. A remapped non-creator member blocks deletion until removed, and the UUID validator currently blocks that removal. The 409 is a downstream consequence of the validator, not a separate defect.

### Architecture and Layers Affected
- API/router: `rest_api/routers/projects/membership.py` (two DELETE routes), `rest_api/routers/user_management_router.py` (remap route), `rest_api/routers/workflow.py` (create/update/rollback background schema task).
- Service: `service/project/project_assignment_service.py` (`_validate_user_id_format`), `service/user/user_remap_service.py`, `service/workflow_service.py` (`save_workflow_schema`, `create_workflow`), `service/project/project_service.py` (delete check, unchanged behavior).
- Model/persistence: `core/workflow_models/workflow_config.py` (`WorkflowConfig.update`), `rest_api/models/base.py` (`BaseModelWithSQLSupport.update` merge semantics).
- Workflow engine: `workflows/workflow.py` `validate_workflow_and_draw` (external Mermaid call that sets the timing).

### Integration Points
- `user_management_router` -> `UserRemapService` -> raw SQL over PostgreSQL tables (`users`, `user_projects`, `applications`, `workflows`, ...) plus `activity_event_repository`.
- `membership` router -> `project_assignment_service` -> `user_repository`, `user_project_repository`, budget sync (`_sync_project_budget_member_removed`), activity events.
- `workflow` router -> FastAPI `BackgroundTasks` -> `run_in_thread_pool` -> `WorkflowExecutor.validate_workflow_and_draw` -> external Mermaid server (`MERMAID_SERVER_URL`) -> `FileRepositoryFactory` (writes `workflows/<id>.svg`) -> `WorkflowConfig.update()`.
- User cache: `authentication_service.invalidate_user_from_cache` exists but remap does not call it.

### Patterns and Conventions
- Remap uses static methods with table lists and f-string SQL with `# noqa: S608`; the caller owns the session (`remap_user_flow` commits).
- Services raise `ExtendedHTTPException(code, message, details, help)`.
- Singletons (`user_remap_service`, `project_assignment_service`, `workflow_service`).
- SQLModel full-object `merge` for updates; `WorkflowConfig.update` already contains a "re-read current and keep server-owned field" precedent for `yaml_config_history`.
- Feature gating via `config.ENABLE_USER_MANAGEMENT`.

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `.ai-run/guides/` exists (api, architecture, data, development, testing, workflows, ...). No guide mentions remap (grep for "remap" in `.ai-run/guides` returned nothing). Relevant guides: `.ai-run/guides/api/endpoint-conventions.md`, `.ai-run/guides/architecture/service-layer-patterns.md`, `.ai-run/guides/data/database-patterns.md`, `.ai-run/guides/testing/testing-service-patterns.md`, `.ai-run/guides/workflows/langgraph-workflows.md`.
- Prior task artifacts for the remap feature: `docs/superpowers/tasks/2026-11-02-user-assets-remap/` (code-review diffs, lens reviews; no spec.md present).

### Architectural Decisions
- Module docstring and inline NOTEs in `user_remap_service.py`: the remap is a request-triggered, repeatable service, not an Alembic migration; it runs in a single transaction; FKs into `users` are `DEFERRABLE INITIALLY DEFERRED` via migration `src/external/alembic/versions/c6f1a2b3d4e5_defer_users_fks_for_id_remap.py`; `cli_analytics.user_id` and `agent_workspace_files.blob_owner` are excluded on purpose.
- `UserRemapRequest` intentionally allows arbitrary non-empty ids ("pre-Keycloak" ids).
- `_validate_user_id_format` docstring says "Validate user_id is a valid UUID format", with a LOCAL-env bypass.

### Derived Conventions
- Admin user endpoints treat `user_id` as an opaque string with DB-lookup validation (404 if missing). Only the project-assignment service enforces UUID format.

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/service/user/test_user_remap_service.py` — 15 lines: license header plus a module docstring only. **No remap service tests exist.**
- `tests/codemie/rest_api/routers/test_user_management_router_authorization.py:351-` — remap route requires superadmin and delegates to the service (service is mocked).
- `tests/codemie/service/project/test_project_assignment_service.py:33-56` — `_validate_user_id_format` accepts a UUID and rejects a non-UUID (patches `config.ENV` to `"production"`), asserting `"must be a valid UUID"`.
- `tests/codemie/service/test_workflow_service.py:1318-1340, ~1470-1545` — `save_workflow_schema` persists via `update()` (update is stubbed with `_stub_update`); there is no test of stale-field overwrite.
- `tests/codemie/rest_api/routers/test_workflow.py` (l.332, 498, 529, 563, 597, 634) patches `save_workflow_schema` away.
- The failing e2e `test_remap_moves_owned_content_to_new_user_id` (PR #55) lives in the SDK/autotest harness, not in this repository.

### Testing Framework and Patterns
- pytest with `unittest.mock.patch`/`MagicMock`, class-based suites, and config patched by module path (`codemie.service.project.project_assignment_service.config.ENV`). Workflow tests use fixtures `workflow_service`, `workflow_config`, `user`. See `.ai-run/guides/testing/testing-patterns.md`.

### Coverage Gaps
- No unit tests for any `UserRemapService` method, including the workflow `UserEntity` JSONB rewrite.
- No test for the interaction between remap and a later `WorkflowConfig.update()` / `save_workflow_schema` (the actual bug-2 mechanism).
- No test that removal endpoints accept non-UUID ids. The existing test asserts the opposite and will conflict with an accept-non-UUID change.
- No test for project delete after a remapped member is removed.

---

## 5. Configuration and Environment

### Environment Variables
- `ENV` (`src/codemie/configs/config.py:52`, default `"local"`): when `local`, `_validate_user_id_format` is bypassed. This is why bug 1 does not reproduce locally and only shows on preview.
- `ENABLE_USER_MANAGEMENT`: gates the remap and admin user routes.
- `MERMAID_SERVER_URL`, `MERMAID_SERVER_TIMEOUT` (50 s), `MERMAID_USE_MERMAID_INC`, `MERMAID_ENABLED` (`config.py:83-86`): govern whether and how long the background schema render runs, which controls whether and when the stale `update()` happens.
- `CODEMIE_STORAGE_BUCKET_NAME`: owner for the stored workflow SVG.

### Configuration Files
- `src/codemie/configs/config.py` — settings above.

### Feature Flags and Deployment Concerns
- `ENABLE_USER_MANAGEMENT` toggles the remap feature. Preview (non-local ENV with a Mermaid renderer) is where both bugs show up. No DB migration is involved in the findings above.

---

## 6. Risk Indicators

- Bug 2's root cause is outside `user_remap_service.py`: the stale full-row `merge` in `BaseModelWithSQLSupport.update()`, driven by `save_workflow_schema` from the background task at `workflow.py:559` (and at l.698 and l.1051). AC 2 says "within the remap transaction". A fix confined to the remap SQL cannot stop a later overwrite, so the spec must reconcile that wording with the actual mechanism.
- Speculative: fix options include (a) `WorkflowConfig.update()`/`save_workflow_schema` re-reading `created_by` (as already done for `yaml_config_history`) or doing a targeted `UPDATE workflows SET schema_url=...`, (b) re-fetching the workflow in `update_workflow_schema` before saving. Option (a) changes shared update semantics used by every workflow save.
- The same lost-update pattern can revert any column the remap touches on any model saved via a stale full-object `merge`. Other background or deferred writers were not audited beyond workflows.
- Remap does not rewrite `workflows.updated_by` or `yaml_config_history[].created_by` (both `UserEntity`), and it does not call `invalidate_user_from_cache`. These are adjacent gaps that may surface in the same e2e test.
- Bug 1: relaxing `_validate_user_id_format` also affects assign, update-role, bulk-assign and CSV flows (5 call sites). The existing unit test `test_project_assignment_service.py:44-56` asserts UUID rejection and will need revisiting.
- `ENV=local` bypass hides bug 1 in local runs, so local verification needs `ENV` patched.
- `_reject_if_creator` runs before format validation. If the e2e test's remapped user is the project creator, removal returns 400 "Cannot remove the project creator" even after the UUID fix. Project delete already excludes the remapped creator, though, so AC 3/5 depend on which user created the project in PR #55.
- Zero unit coverage for `UserRemapService` (test file is an empty stub).

---

## 7. Summary for Complexity Assessment

Both bugs trace back to the single remap endpoint `POST /v1/admin/users/remap` (`UserRemapService.remap_user_flow`). Bug 1 is a narrow validation problem. `ProjectAssignmentService._validate_user_id_format` (`project_assignment_service.py:255-270`) enforces `uuid.UUID(...)` outside `ENV=local`. Both member-removal routes go through it (`membership.py:92` single, `membership.py:226` bulk), and so does the admin `DELETE /v1/admin/users/{id}/projects/{name}`. Meanwhile `UserRemapRequest` accepts any non-empty string, and `GET /v1/admin/users/{user_id}` does a plain DB lookup with no format check. The project-delete 409 (`project_service.py`, `HAS_ASSIGNED_USERS`) only follows from the blocked removal: it already excludes the creator, whose id the remap rewrites in `applications.created_by`. The validator has 5 call sites, plus one existing unit test that asserts UUID rejection.

Bug 2 has a concrete, code-evidenced root cause, and it is not a skipped row. The remap SQL for `workflows.created_by->>'user_id'` is correct. The problem is that workflow creation schedules a background `update_workflow_schema` task (`workflow.py:559`) holding the in-memory `WorkflowConfig`. That task renders Mermaid through an external server, then calls `save_workflow_schema` -> `WorkflowConfig.update()` -> `session.merge(self)` (`base.py:532-546`), a full-row write that puts back the stale `created_by.user_id`. When the remap commits before the render finishes, its rewrite is lost permanently. That matches the constant value across the 14 s poll and the latency-dependent ~60-70% rate. `WorkflowConfig.update()` already re-reads `current` to preserve `yaml_config_history`, which is a precedent for preserving server-owned fields. The same pattern exists on the update (l.698) and rollback (l.1051) paths.

Expected change surface is about 3-5 source files across the router, service and model layers, with no migration. Test coverage is weak: `test_user_remap_service.py` is an empty stub, there are no stale-overwrite tests, and the e2e test is in an external harness. The main risks are the gap between AC 2's "within the remap transaction" wording and the real mechanism, the shared semantics of `WorkflowConfig.update`, the `ENV=local` bypass hiding bug 1, and the adjacent unremapped `updated_by` / `yaml_config_history` fields.

---

## 8. External References

None named by the task. PR #55 / `test_remap_moves_owned_content_to_new_user_id` and the codemie-sdk-python `remap_user` docstring are mentioned but no path was given; neither is in this repository, and neither was read.
