# EPMCDME-13904 Audit Event Details Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every project/budget management activity event states what changed; group delete and early-return group updates emit events; allowed-models changes get an event and a log line.

**Architecture:** One pure helper computes a flat `updated_fields` + `previous_<f>`/`new_<f>` diff. Each service snapshots old values *before* the repository call (repositories mutate in place) and passes the diff as `attributes` to its **existing** `activity_event_repository` call. New emits only where none exists today.

**Tech Stack:** Python, SQLModel, FastAPI, pytest + pytest-asyncio, `unittest.mock`.

**Spec:** none (sdlc-light). Requirements are inline; see Acceptance criteria. Research: `technical-analysis.md` in this folder.

Commit per task using the repository's existing convention.

## Acceptance criteria

- `project.updated` lists changed fields with previous/new values, including `enforce_member_spend_limits` when it is toggled.
- `budget.updated` lists changed soft/hard limit, reset period (`budget_duration`) and `budget_category` with previous/new values. `budget.created` records the initial limits.
- `budget.project_budget.updated` records previous/new amounts and duration, and `new_models` when models change. `budget.project_budget.deleted` records what was deleted.
- `budget.project_budget_group.updated` is emitted exactly once on every successful group PATCH, including scalar-only and notification-only updates, and carries the diff.
- Deleting a group emits `budget.project_budget_group.deleted`. The child `project_budget.deleted` events still fire.
- Allowed-models update and reset emit `project.updated` with the diff and log a `logger.info` line.
- Email and free-text fields (`notification_owner_email`, `description`) show up only as names in `updated_fields`, never as values. Attribute values are scalars or flat lists, never nested objects.
- No action gets a second event or a duplicate log line.

## Global Constraints / Assumptions (autonomous run, not asked)

- Flat keys only. Each changed field gives `previous_<f>`/`new_<f>`. `updated_fields` holds only the fields whose value actually changed. A no-op PATCH still emits its existing event with `updated_fields: []`.
- Values are recorded for: limits/amounts, `budget_duration`, `budget_category`, booleans/enums, `allowed_models`/`default_model`, `cost_center_id` (stringified), `name`/`display_name`. Names are identifiers that already appear as entity ids and `project_name` attributes. Name-only: `description`, `notification_owner_email`. Secrets never reach these paths.
- Previous model allow-lists are not stored on `Budget` (they live on the provider), so only `new_models` is recorded.
- `entity_type=project_budget_group` for single project-budget ids stays as it is (out of scope).
- Out of scope, already detailed, not flagged by QA: rebalance, reset, member override, stop/restore. Also out of scope: cost-center CRUD and project-integration routers, which already log via `logger.info` and have no domain constant.
- No DB migration, no UI change. New log lines carry ids and field names only, never values. Use the `budget_event=… component=…` style in budget services and `project_updated: …, domain=project_management` in the project service.
- **Dedup rule (applies to every task):** extend the existing emit and log line in place. Each task's verification step re-greps its path. Coverage was checked on this branch: routers (`projects/management.py`, `budget_router.py`, `project_budget_router.py`) have no `logger.info` or activity calls on these paths (only an access-denied `logger.warning` at `project_budget_router.py:213`). No decorator or middleware inserts activity events (every `activity_event_repository.insert` caller is a service or an unrelated router). Repositories log only `logger.debug` (`application_repository.py:623`).

## Review Focus

1. In-place mutation: a "previous" value read after `budget_repository.update`/`application_repository.update_project` equals the new one, so the diff comes out empty. Tests make the mocked `update` mutate the *same* object (`side_effect` doing `setattr`) and still expect the diff (Tasks 2, 4, 5, 6).
2. A PATCH with identical values gives `updated_fields: []` and no `previous_*`/`new_*` keys. `display_name`/`cost_center_id` are always reassigned (Task 1, Task 2).
3. Project rename plus enforcement toggle: the old flag is read using the pre-rename name (Task 2).
4. A notification-only group PATCH that sets an email: `notification_owner_email` appears in `updated_fields`, and the email string appears nowhere in attributes (Task 6).
5. A group PATCH with `pct=0`: child `project_budget.deleted` fires and exactly one `group.updated` fires, with no `group.deleted` (Task 6).

---

### Task 1: Change-diff helper and `PROJECT_BUDGET_GROUP_DELETED`

**Files:** Create `src/codemie/service/activity/activity_attributes.py`. Modify `src/codemie/service/activity/activity_models.py:94-114`. Test: create `tests/codemie/service/activity/test_activity_attributes.py`.

**Current coverage:** no diff helper exists (analysis §2 Patterns). No group-deleted constant (`activity_models.py:94-114`).

**Interfaces — Produces:**
```python
def build_change_attributes(
    previous: Mapping[str, Any],
    new: Mapping[str, Any],
    *,
    value_fields: Iterable[str],
    name_only_fields: Iterable[str] = (),
) -> dict[str, Any]: ...
BudgetManagementEvent.PROJECT_BUDGET_GROUP_DELETED = "budget.project_budget_group.deleted"
```
Only keys of `new` that are in `value_fields ∪ name_only_fields` are considered. Keys outside both sets are ignored (for example the internal `soft_limit_notified_at`). A missing key in `previous` counts as `None`. Values are normalized before comparison and output: `Enum`→`.value`, `UUID`→`str`, `datetime`→`isoformat()`, `tuple`→`list`. The result is `{"updated_fields": sorted(changed), "previous_<f>": …, "new_<f>": …}` (value fields only) and always contains `updated_fields`.

- [ ] **Step 1: Write failing tests**: `test_value_field_change_records_previous_and_new` (`{"max_budget": 10}`→`{"max_budget": 20}` gives `{"updated_fields": ["max_budget"], "previous_max_budget": 10, "new_max_budget": 20}`); `test_unchanged_value_omitted` (equal values give `{"updated_fields": []}`); `test_name_only_field_has_no_values` (`description` changed gives `{"updated_fields": ["description"]}`); `test_unlisted_key_ignored`; `test_enum_and_uuid_normalized_to_str`; `test_constant_value` (`PROJECT_BUDGET_GROUP_DELETED == "budget.project_budget_group.deleted"`).
- [ ] **Step 2:** `poetry run pytest tests/codemie/service/activity/test_activity_attributes.py -v` gives FAIL (ImportError).
- [ ] **Step 3:** Implement the helper and add the constant after `PROJECT_BUDGET_GROUP_UPDATED`.
- [ ] **Step 4:** Same command gives PASS.

Test-first: yes — `test_value_field_change_records_previous_and_new` fails on import because `build_change_attributes` does not exist.

### Task 2: `project.updated` diff in `update_project`, incl. member enforcement

**Files:** Modify `src/codemie/service/project/project_service.py:272-334`. Test: `tests/codemie/service/project/test_project_service_delete_update.py`.

**Current coverage:** event at `project_service.py:322-331` has no attributes. `logger.info` at `:334` has no field names. `SettingsService.set_enforce_member_spend_limits` (`settings.py:1172`) does not log.

**Interfaces — Consumes:** `build_change_attributes` (Task 1). `SettingsService.get_enforce_member_spend_limits(project_name) -> bool` (`settings.py:1136`).

- [ ] **Step 1: Write failing tests**: `test_update_project_event_records_enforcement_toggle`: `get_enforce_member_spend_limits.return_value=False`, call with `enforce_member_spend_limits=True`. Expect attributes to include `updated_fields` containing `"enforce_member_spend_limits"`, `previous_enforce_member_spend_limits is False`, `new_enforce_member_spend_limits is True`. `test_update_project_event_rename_and_description_name_only`: `name` gives previous/new, while `description` appears only in `updated_fields` and its text is absent from attributes. The mocked `update_project` mutates the passed project in place. `test_update_project_event_unchanged_display_name_not_listed`.
- [ ] **Step 2:** `poetry run pytest tests/codemie/service/project/test_project_service_delete_update.py -k "update_project" -v` gives the new tests FAILING.
- [ ] **Step 3:** Before line 305, snapshot `name, display_name, description, cost_center_id, chargeback_enabled, chargeback_attribution, cli_analytics_enabled` from `project`. When `enforce_member_spend_limits is not None`, also read the old flag using the pre-rename `project.name`. After the update, build `new` from the mutated `project` plus the requested flag. Pass `build_change_attributes(...)` (name-only: `description`) as `attributes` on the **existing** insert at `:322`. Append `updated_fields=[…]` to the existing log line at `:334` instead of adding a new line. Update any exact-attribute assertions in the existing `test_update_project_*` tests (`:1026-1160`).
- [ ] **Step 4:** Same command gives PASS.
- [ ] **Step 5: Confirm no existing event/log covers this path**: `Grep "activity_event_repository|logger.info" project_service.py` shows exactly one insert and one info line in `update_project`. `routers/projects/management.py` has no `logger.info`.

Test-first: yes — `test_update_project_event_records_enforcement_toggle` fails because the event currently has `attributes=None`.

### Task 3: Allowed-models update/reset emit `project.updated` and log

**Files:** Modify `src/codemie/service/project/project_service.py:819-931`. Test: create `tests/codemie/service/project/test_project_service_allowed_models_activity.py`.

**Current coverage:** no event. The only log is `logger.exception` at `:927`. The router `management.py:96,132` does not log.

**Interfaces — Consumes:** Task 1 helper. **Produces:** `_persist_allowed_models(session, project_name, allowed_models, default_model, actor_id: str) -> Application`.

- [ ] **Step 1: Write failing tests**: `test_update_allowed_models_emits_project_updated_with_diff` (`previous_allowed_models: None`, `new_allowed_models: ["gpt-4o"]`, `new_default_model`); `test_reset_allowed_models_emits_diff_to_none`; `test_allowed_models_no_event_on_commit_failure`. Patch `activity_event_repository`, `get_session` and validators.
- [ ] **Step 2:** `poetry run pytest tests/codemie/service/project/test_project_service_allowed_models_activity.py -v` gives FAIL.
- [ ] **Step 3:** In `_persist_allowed_models`, snapshot `allowed_models`/`default_model` before assigning. Insert one `PROJECT_UPDATED` event (entity project/`project.name`, `actor_id`) before `session.commit()`, so a rollback discards it. After a successful commit, add `logger.info(f"project_updated: project={…!r}, by={actor_id}, updated_fields=[…], domain=project_management")`. Pass `user.id` from both callers.
- [ ] **Step 4:** Same command gives PASS.
- [ ] **Step 5: Confirm no existing event/log covers this path**: `Grep "activity_event_repository|logger.info"` in the range `project_service.py:819-931` returns only the new lines.

Test-first: yes — `test_update_allowed_models_emits_project_updated_with_diff` fails because nothing calls the activity repo.

### Task 4: `budget.updated` / `budget.created` details

**Files:** Modify `src/codemie/service/budget/budget_service.py:283-292,345-384`. Test: `tests/codemie/service/budget/test_budget_service_activity.py`.

**Current coverage:** the events at `:283` and `:375` have no attributes. `logger.info` at `:278` and `:370` already exist (the latter with `updated_fields`). Keep both log lines and do not add another.

- [ ] **Step 1: Write failing tests**: extend `test_update_budget_emits_budget_updated_event` (`:182`) and add `test_update_budget_event_records_limits_duration_category`. Old `soft 5/max 10/30d/platform` becomes `8/20/7d/cli`, and the mocked `update` mutates the same budget object; expect four previous/new pairs. `test_update_budget_event_email_name_only`. `test_create_budget_event_records_initial_limits` expects `{budget_category, soft_budget, max_budget, budget_duration}`.
- [ ] **Step 2:** `poetry run pytest tests/codemie/service/budget/test_budget_service_activity.py -v` gives FAIL.
- [ ] **Step 3:** Snapshot the value fields from `budget` before `:356`. Compute `build_change_attributes(previous, update_fields, value_fields={name, soft_budget, max_budget, budget_duration, budget_category, soft_limit_notify_once, soft_limit_notification_enabled}, name_only_fields={description, notification_owner_email})` and pass it on the existing insert. For create, pass the four initial values from `data` on the existing insert.
- [ ] **Step 4:** Same command gives PASS.
- [ ] **Step 5: Confirm no existing event/log covers this path**: one insert per method in `budget_service.py`. `budget_router.py` has no `logger.info` or insert.

Test-first: yes — `test_update_budget_event_records_limits_duration_category` fails because attributes are `None`.

### Task 5: `project_budget.updated` / `.deleted` details

**Files:** Modify `src/codemie/service/budget/project_budget_service.py:1313-1370,1841-1856`. Test: `tests/codemie/service/budget/test_budget_service_activity.py`.

**Current coverage:** `:1360` sends `{updated_fields: [names]}` only, with no values and without `models`. `:1847` sends no attributes. Log lines exist at `:1354` and `:1841`. Extend the existing log line at `:1354` to list `models` when it is provided, and do not add a new line.

- [ ] **Step 1: Write failing tests**: `test_update_project_budget_event_records_amount_diff` (the mocked `update` mutates the same object); `test_update_project_budget_models_only_records_new_models` (`updated_fields == ["models"]`, `new_models == [...]`, no `previous_models`); `test_update_project_budget_unchanged_amount_not_listed`. Extend `test_delete_project_budget_emits_project_budget_deleted_event` (`:578`) to assert `{project_name, budget_category, soft_budget, max_budget, budget_duration, allocation_count, child_budget_count}`.
- [ ] **Step 2:** `poetry run pytest tests/codemie/service/budget/test_budget_service_activity.py -k project_budget -v` gives FAIL.
- [ ] **Step 3:** Snapshot `name, soft_budget, max_budget, budget_duration` (values) and `description` (name-only) before `:1331`. Build the diff from `changed_fields`. If `data.models is not None`, add `"models"` to `updated_fields` (keep it sorted) and set `new_models`. Add `project_name` from `_assignment` when it is available. For delete, build the attributes from the already loaded `budget`/`assignment`/`allocations`/`child_budgets`. Update the fan-out child-event assertions in `test_project_budget_service*.py` where `updated_fields` used to contain unchanged max/soft values.
- [ ] **Step 4:** Same command plus `poetry run pytest tests/codemie/service/budget -k "project_budget" -q` gives PASS.
- [ ] **Step 5: Confirm no existing event/log covers this path**: exactly one insert in each of `update_project_budget` and `delete_project_budget`. Router handlers at `project_budget_router.py:304,326` do not log.

Test-first: yes — `test_update_project_budget_event_records_amount_diff` fails because only names are sent.

### Task 6: `project_budget_group.updated` on every exit path, with diff

**Files:** Modify `src/codemie/service/budget/project_budget_service.py:2082-2095,2151-2235`. Test: create `tests/codemie/service/budget/test_project_budget_group_activity.py`.

**Current coverage:** the event fires only on the full path (`:2224`, `{project_name}`). The early returns at `:2177` (scalar-only) and `:2201` (notification-only) only log. Each exit has exactly one log line, which stays.

**Interfaces — Produces:** `_emit_group_updated_event(self, session, group, actor_id: str, attributes: dict[str, Any]) -> None`, which merges `project_name`.

- [ ] **Step 1: Write failing tests**: `test_group_scalar_only_update_emits_event_with_duration_diff`; `test_group_notification_only_update_emits_event_email_name_only` (asserts that the email string is not in `str(attributes)`, and `previous/new_soft_limit_notification_enabled`); `test_group_full_update_emits_single_group_event_with_total_diff` (`previous/new_total_amount`, `"categories"` in `updated_fields`, `insert` called once with `PROJECT_BUDGET_GROUP_UPDATED`); `test_group_update_pct_zero_child_deleted_no_group_deleted`.
- [ ] **Step 2:** `poetry run pytest tests/codemie/service/budget/test_project_budget_group_activity.py -v` gives FAIL.
- [ ] **Step 3:** Add `soft_limit_notification_enabled` to `_collect_updated_group_fields`. Before `:2168`, snapshot `group.name`, `group.budget_duration` and `group.description`. Before `:2185`, snapshot the first category budget's `soft_limit_notify_once`, `soft_limit_notification_enabled` and `notification_owner_email`. `previous_total_amount` is `current_result.total_amount`. Value fields: `name, budget_duration, total_amount, soft_limit_notify_once, soft_limit_notification_enabled`. Name-only: `description, notification_owner_email, categories`. Replace the inline insert at `:2224` with the helper, and call the helper once just before each early `return`. Child per-category events stay unchanged.
- [ ] **Step 4:** Same command gives PASS.
- [ ] **Step 5: Confirm no existing event/log covers this path**: after the change, `Grep PROJECT_BUDGET_GROUP_UPDATED project_budget_service.py` returns only the helper. The router at `:571` does not log.

Test-first: yes — `test_group_scalar_only_update_emits_event_with_duration_diff` fails because the early return emits nothing.

### Task 7: `project_budget_group.deleted` event

**Files:** Modify `src/codemie/service/budget/project_budget_service.py:2425-2445`. Test: `tests/codemie/service/budget/test_project_budget_group_activity.py`.

**Current coverage:** no group event. `logger.info` at `:2441` stays as it is. Child `project_budget.deleted` events fire through `delete_project_budget` (`:2438`).

- [ ] **Step 1: Write failing tests**: `test_delete_group_emits_group_deleted_event`. Expect entity `project_budget_group`/`group_id` and attributes `{project_name, name, budget_duration, category_count, deleted_budget_ids: [sorted ids]}`, emitted after `soft_delete`. The child delete calls still happen. `test_delete_group_not_found_emits_nothing`.
- [ ] **Step 2:** `poetry run pytest tests/codemie/service/budget/test_project_budget_group_activity.py -k delete -v` gives FAIL.
- [ ] **Step 3:** After the existing log line, add one `async_insert` with `PROJECT_BUDGET_GROUP_DELETED`.
- [ ] **Step 4:** Same command gives PASS.
- [ ] **Step 5: Confirm no existing event/log covers this path**: `Grep "PROJECT_BUDGET_GROUP_DELETED|budget.project_budget_group.deleted" src/` returns only the constant and this site. The router at `:593` does not log.

Test-first: yes — `test_delete_group_emits_group_deleted_event` fails because `insert` is never called with a group event.

---

## Negative-constraint pass

- No nested objects: the helper (T1) emits only scalars and flat lists. `categories` is recorded by name only (T6).
- Only changed fields: comparison happens in the helper (T1), and snapshots are taken before mutation (T2, T4, T5, T6).
- No values for email or free text: name-only sets in T2, T4, T5 and T6. The T6 test asserts the email is absent.
- No secrets in logs: new log lines (T2, T3) carry ids and field names only.
- No duplicate event or log line: each task extends its existing emit/log and has its own Step 5 check. The child events in T6 and T7 are kept.
- No DB migration, no UI change: no task touches either.
- Only related tests: each task runs targeted pytest paths. Whole-suite gates belong to the flow.
