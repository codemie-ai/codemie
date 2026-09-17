# Scheduler ownerType Filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an optional `ownerType` query parameter to `GET /v1/schedulers` that filters results by owner type ("User" or "Project"), preserving backward compatibility when absent.

**Architecture:** The API router gains one new `Query` parameter forwarded to the service layer. The service's `_build_list_filters` method appends a `s.setting_type = :owner_type` SQL condition when the value is present, normalising the incoming capitalised value ("User"/"Project") to lowercase ("user"/"project") to match the database column. No model changes, no migration.

**Tech Stack:** Python, FastAPI (`Query`), SQLAlchemy `text()`, pytest / `unittest.mock`

**Spec:** docs/superpowers/tasks/2026-09-15-epmcdme-15020-scheduler-type-switch/spec.md (backend contract section)

## Global Constraints

- `ownerType` is optional; its absence MUST preserve current behaviour (no filter applied).
- Valid API values: `"User"` | `"Project"` — must be lowercased before DB binding.
- No new dependencies, no database migration, no model changes.
- Follow the existing `conditions` / `params` accumulator pattern in `_build_list_filters`.
- Commit message format: `EPMCDME-14805: <description>`

---

### Task 1: Extend `_build_list_filters` and `list_schedulers` in the service

**Files:**
- Modify: `src/codemie/service/settings/scheduler_settings_service.py:444-508`
- Test: `tests/codemie/rest_api/routers/test_schedulers.py`

**Interfaces:**
- Produces: `_build_list_filters(project_id, resource_type, resource_id, search, status, last_run_status, owner_type=None)` — appends `"s.setting_type = :owner_type"` to `conditions` and `params["owner_type"] = owner_type.lower()` when `owner_type` is truthy.
- Produces: `list_schedulers(..., owner_type=None)` — accepts the new kwarg and forwards it to `_build_list_filters`.

- [ ] **Step 1: Write failing tests**

Add to `tests/codemie/rest_api/routers/test_schedulers.py`:

```python
# ── Task 1: ownerType filter (EPMCDME-14805) ─────────────────────────────────


def test_build_list_filters_appends_setting_type_when_owner_type_provided():
    from codemie.service.settings.scheduler_settings_service import SchedulerSettingsService

    conditions, params = SchedulerSettingsService._build_list_filters(
        project_id=None,
        resource_type=None,
        resource_id=None,
        search=None,
        status=None,
        last_run_status=None,
        owner_type="User",
    )

    assert "s.setting_type = :owner_type" in conditions
    assert params["owner_type"] == "user"


def test_build_list_filters_omits_setting_type_when_owner_type_is_none():
    from codemie.service.settings.scheduler_settings_service import SchedulerSettingsService

    conditions, params = SchedulerSettingsService._build_list_filters(
        project_id=None,
        resource_type=None,
        resource_id=None,
        search=None,
        status=None,
        last_run_status=None,
        owner_type=None,
    )

    assert "s.setting_type = :owner_type" not in conditions
    assert "owner_type" not in params
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
poetry run pytest tests/codemie/rest_api/routers/test_schedulers.py::test_build_list_filters_appends_setting_type_when_owner_type_provided tests/codemie/rest_api/routers/test_schedulers.py::test_build_list_filters_omits_setting_type_when_owner_type_is_none -v
```

Expected: FAIL — `_build_list_filters` does not yet accept `owner_type`.

- [ ] **Step 3: Extend `_build_list_filters` signature and add condition**

In `src/codemie/service/settings/scheduler_settings_service.py`, change line 444:

```python
    @staticmethod
    def _build_list_filters(project_id, resource_type, resource_id, search, status, last_run_status, owner_type=None):
```

After the `last_run_status` block (before `return conditions, params`), add:

```python
        if owner_type:
            conditions.append("s.setting_type = :owner_type")
            params["owner_type"] = owner_type.lower()
```

- [ ] **Step 4: Extend `list_schedulers` signature and forward `owner_type`**

In `src/codemie/service/settings/scheduler_settings_service.py`, change the `list_schedulers` signature (line 489):

```python
    @staticmethod
    def list_schedulers(
        page=0,
        per_page=10,
        search=None,
        resource_type=None,
        project_id=None,
        resource_id=None,
        status=None,
        last_run_status=None,
        owner_type=None,
    ):
```

Change the `_build_list_filters` call (line 507) to forward `owner_type`:

```python
        conditions, params = SchedulerSettingsService._build_list_filters(
            project_id, resource_type, resource_id, search, status, last_run_status, owner_type
        )
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
poetry run pytest tests/codemie/rest_api/routers/test_schedulers.py::test_build_list_filters_appends_setting_type_when_owner_type_provided tests/codemie/rest_api/routers/test_schedulers.py::test_build_list_filters_omits_setting_type_when_owner_type_is_none -v
```

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/codemie/service/settings/scheduler_settings_service.py tests/codemie/rest_api/routers/test_schedulers.py
git commit -m "EPMCDME-14805: Extend _build_list_filters and list_schedulers with owner_type param"
```

---

### Task 2: Add `ownerType` query parameter to the router

**Files:**
- Modify: `src/codemie/rest_api/routers/schedulers.py:54-74`
- Test: `tests/codemie/rest_api/routers/test_schedulers.py`

**Interfaces:**
- Consumes: `SchedulerSettingsService.list_schedulers(..., owner_type=None)` from Task 1.
- Produces: `GET /v1/schedulers?ownerType=User` → forwards `owner_type="User"` to service.

- [ ] **Step 1: Write failing test**

Add to `tests/codemie/rest_api/routers/test_schedulers.py`:

```python
def test_router_forwards_owner_type_to_service():
    """ownerType query param is forwarded as owner_type to SchedulerSettingsService."""
    with patch(
        "codemie.rest_api.routers.schedulers.SchedulerSettingsService"
    ) as mock_svc_cls:
        mock_svc_cls.list_schedulers.return_value = MagicMock(items=[], total=0, page=0, pageSize=10, totalPages=0)

        from codemie.rest_api.routers.schedulers import list_schedulers

        list_schedulers(
            page=0,
            page_size=10,
            search=None,
            resource_type=None,
            project_id=None,
            resource_id=None,
            status=None,
            last_run_status=None,
            owner_type="Project",
        )

        _, kwargs = mock_svc_cls.list_schedulers.call_args
        assert kwargs["owner_type"] == "Project"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
poetry run pytest tests/codemie/rest_api/routers/test_schedulers.py::test_router_forwards_owner_type_to_service -v
```

Expected: FAIL — `list_schedulers` does not accept `owner_type` yet.

- [ ] **Step 3: Add the `ownerType` query parameter to the router**

In `src/codemie/rest_api/routers/schedulers.py`, update `list_schedulers`:

```python
@router.get("/schedulers", response_model=SchedulersPaginatedResponse, status_code=status.HTTP_200_OK)
def list_schedulers(
    page: int = Query(0, ge=0),
    page_size: int = Query(10, ge=1, le=100, alias="pageSize"),
    search: Optional[str] = Query(None),
    resource_type: Optional[str] = Query(None, alias="resourceType"),
    project_id: Optional[str] = Query(None, alias="projectId"),
    resource_id: Optional[str] = Query(None, alias="resourceId"),
    status: Optional[str] = Query(None),
    last_run_status: Optional[str] = Query(None, alias="lastRunStatus"),
    owner_type: Optional[str] = Query(None, alias="ownerType"),
):
    return SchedulerSettingsService.list_schedulers(
        page=page,
        per_page=page_size,
        search=search,
        resource_type=resource_type,
        project_id=project_id,
        resource_id=resource_id,
        status=status,
        last_run_status=last_run_status,
        owner_type=owner_type,
    )
```

- [ ] **Step 4: Run test to verify it passes**

```bash
poetry run pytest tests/codemie/rest_api/routers/test_schedulers.py::test_router_forwards_owner_type_to_service -v
```

Expected: PASS

- [ ] **Step 5: Run full scheduler test suite**

```bash
poetry run pytest tests/codemie/rest_api/routers/test_schedulers.py -v
```

Expected: all tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/codemie/rest_api/routers/schedulers.py tests/codemie/rest_api/routers/test_schedulers.py
git commit -m "EPMCDME-14805: Add ownerType query param to GET /v1/schedulers router"
```
