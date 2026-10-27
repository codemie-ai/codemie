# Scheduler multi-project/resource filter (EPMCDME-16235, backend) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** `GET /v1/schedulers` accepts several `projectId` / `resourceId` values (OR semantics).
**Architecture:** Router params become `list[str] | None`; service args are renamed `project_ids` / `resource_ids` (same positions) and bound with `= ANY(:list)`. Non-admin scope block is untouched.
**Spec:** `docs/superpowers/tasks/2026-10-21-scheduler-multi-project-filter/spec.md`

Commit per task using the repository's existing convention.

## Global Constraints

- Repeated query params only (no comma-separated); `resourceType`, `status`, `lastRunStatus`, `ownerType`, `search` and `/scheduler-runs*` stay single-valued.
- Lines `scheduler_settings_service.py:473-479` (non-admin scope, `1=0` branch) must not change.
- Positional order of `_build_list_filters(project_ids, resource_type, resource_ids, ...)` unchanged.

## Review Focus

- Non-admin sends a mix of accessible and inaccessible projects: `project_names` scope condition still ANDed (Task 1).
- `?projectId=` (blank) or `[""]` returns unfiltered, not empty (Task 1).
- Single `?projectId=a` still reaches the service as `["a"]` (Task 2).

### Task 1: Service filters take lists

**Files:**
- Modify: `src/codemie/service/settings/scheduler_settings_service.py:466-495` (`_build_list_filters`) and `:517-539` (`list_schedulers` signature and call)
- Test: `tests/codemie/service/settings/test_scheduler_settings_service_class.py` (update test near line 378)

Test-first: yes — `_build_list_filters(["a","b"], ...)` yields `s.project_name = ANY(:project_ids)` with `params["project_ids"] == ["a","b"]`; currently emits `:project_id`.

- [ ] Rename args to `project_ids`, `resource_ids` in both methods (keep positions; `list_schedulers` keeps keyword defaults `None`). In `_build_list_filters`, first normalize each with `[v for v in (x or []) if v and v.strip()]`; if non-empty append `s.project_name = ANY(:project_ids)` / the existing resource `EXISTS` with `cv->>'value' = ANY(:resource_ids)` (ANY stays inside the subquery) and set `params["project_ids"]` / `params["resource_ids"]` to the lists.
- [ ] Tests (same style as the existing `_make_user` tests): update line-378 test to `:project_ids` / list; add single value, several values, `None`, `[]`, `["", " "]` (no condition, no param), resource ids single and several, and non-admin with `["inaccessible"]` plus a mixed list asserting both the `:project_names` scope condition and `params["project_names"]` remain alongside `:project_ids`.
- [ ] Run `pytest tests/codemie/service/settings/test_scheduler_settings_service_class.py -q`; expected PASS.

### Task 2: Router accepts repeated params

**Files:**
- Modify: `src/codemie/rest_api/routers/schedulers.py:60-61` and `:72-73`
- Test: `tests/codemie/rest_api/routers/test_schedulers.py` (direct-call tests at ~507, 522, 539)

Test-first: yes — a `TestClient` request `?projectId=a&projectId=b&resourceId=r1` makes the patched `SchedulerSettingsService.list_schedulers` receive `project_ids=["a","b"]`, `resource_ids=["r1"]`; fails today (kwargs named `project_id`).

- [ ] Change to `project_ids: list[str] | None = Query(None, alias="projectId")` and `resource_ids: list[str] | None = Query(None, alias="resourceId")`; forward as `project_ids=` / `resource_ids=` (precedent: `activity_events_router.py:55-59`).
- [ ] Update the three existing direct-call tests to the renamed kwargs. Add the `TestClient` test (mount `router` on a bare `FastAPI` app, override `authenticate`; patch `codemie.rest_api.routers.schedulers.SchedulerSettingsService`) covering repeated values, a single `?projectId=a` -> `["a"]`, and no params -> `None`.
- [ ] Run `pytest tests/codemie/rest_api/routers/test_schedulers.py -q`; expected PASS.
