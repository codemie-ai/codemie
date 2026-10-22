# EPMCDME-15538 Backend: Drop Auto-Generated Clone Names Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the backend's "suggest a `Copy N of <name>` clone name" feature entirely, and make the clone endpoint's duplicate-name error identical in wording to the existing edit (update) endpoint's duplicate-name error.

**Architecture:** `MCPConfigService.clone_from_form` already requires a non-empty, caller-supplied `name` (via `MCPConfigCloneRequest.name: str = Field(min_length=1, ...)`) and already 400s when that name collides with an existing config owned by the same user — this is unchanged by this plan. What changes is: (1) the now-unused name-suggestion machinery (`clone_naming.py`, the `suggested_clone_name` service method, and the `GET /mcp-configs/{config_id}/suggested-clone-name` route) is deleted outright, since the new UI flow (see the companion frontend plan) opens the Clone form with an empty name field instead of asking the backend to suggest one; (2) the clone endpoint's duplicate-name error message/details/help text is changed to exactly match the update endpoint's, so the user sees the same wording whether they hit a duplicate name while editing or while cloning.

**Tech Stack:** Python, FastAPI, SQLModel, pytest, Poetry/Makefile gates (`make ruff`, `make test`).

## Global Constraints

- The clone endpoint continues to reject an empty or whitespace-only `name` with the existing Pydantic `min_length=1` validation (HTTP 422) — this plan does not relax that; "empty name" is a frontend-only initial-form-state concern (see the frontend plan).
- No two MCP configs owned by the same user may share the same non-empty `name` — unchanged existing behavior, just re-worded in its error.
- Regenerate the OpenAPI baseline only once, after both the router and model changes are in, and review the diff — it must show only the removed `/suggested-clone-name` path and nothing else.
- Run `make ruff` and `make test` (scoped to the touched test files is fine mid-task; run the full `make test` at the end) before each commit — see `.ai-run/guides/quality-gates.md`.

---

### Task 1: Delete the `Copy N of <name>` suggestion feature end-to-end

**Files:**
- Delete: `src/codemie/service/mcp/clone_naming.py`
- Delete: `tests/codemie/service/mcp/test_clone_naming.py`
- Delete: `tests/codemie/service/test_mcp_config_service_suggested_name.py`
- Modify: `src/codemie/service/mcp_config_service.py:52` (import), `:426-452` (method body — line numbers as of this plan's writing; re-locate by searching for `def suggested_clone_name`)
- Modify: `src/codemie/rest_api/routers/mcp_config.py:89-109` (route — re-locate by searching for `suggested-clone-name`)
- Modify: `tests/codemie/rest_api/routers/test_mcp_config.py` (remove `SUGGESTED_NAME_URL` constant and the three `test_suggested_name_*` tests)
- Modify: `tests/codemie/rest_api/router_guards/baselines/openapi.json` (regenerated, not hand-edited — see Step 6)

**Interfaces:**
- Consumes: nothing new.
- Produces: nothing new — this task only removes the `MCPConfigService.suggested_clone_name(config_id: str, user: User) -> str` classmethod and the `GET /v1/mcp-configs/{config_id}/suggested-clone-name` route. No other task in this plan or the companion frontend plan depends on anything this task adds, only on these being *gone*.

- [x] **Step 1: Confirm nothing else in the backend imports the module being deleted**

Run: `grep -rn "clone_naming\|suggested_clone_name\|suggested-clone-name" src/ tests/`

Expected: every hit is inside one of the six files listed under **Files** above (plus the `openapi.json` baseline, handled separately in Step 6). If a hit shows up anywhere else, stop and re-scope this task before continuing.

- [x] **Step 2: Delete the name-allocation module and its unit tests**

```bash
git rm src/codemie/service/mcp/clone_naming.py
git rm tests/codemie/service/mcp/test_clone_naming.py
git rm tests/codemie/service/test_mcp_config_service_suggested_name.py
```

- [x] **Step 3: Remove the service method and its now-dead import**

In `src/codemie/service/mcp_config_service.py`, delete this import line:

```python
from codemie.service.mcp.clone_naming import allocate_name, compute_base
```

Then delete the entire `suggested_clone_name` classmethod (search for `def suggested_clone_name`):

```python
    @classmethod
    def suggested_clone_name(cls, config_id: str, user: User) -> str:
        """
        Suggest the next available `Copy N of <name>` clone name for this user (pure read).

        Args:
            config_id: ID of the configuration to clone (system-provided sources are allowed)
            user: User the suggestion is scoped to

        Returns:
            The next available clone name

        Raises:
            ExtendedHTTPException: 404 unknown source
        """
        source = MCPConfig.find_by_id(config_id)
        if not source:
            raise ExtendedHTTPException(
                code=status.HTTP_404_NOT_FOUND,
                message=MCP_CONFIG_NOT_FOUND_MESSAGE,
                details=f"No MCP configuration found with id: {config_id}",
                help=VERIFY_CONFIG_ID_HELP,
            )

        with Session(MCPConfig.get_engine()) as session:
            return allocate_name(session, user.id, compute_base(source.name))
```

Leave the `from sqlmodel import Session, select` import in place — `Session` is still used elsewhere in this file (e.g. in `get_usage_stats`-style methods further down).

- [x] **Step 4: Remove the route**

In `src/codemie/rest_api/routers/mcp_config.py`, delete this entire route (search for `suggested-clone-name`):

```python
@router.get(
    "/mcp-configs/{config_id}/suggested-clone-name",
    status_code=status.HTTP_200_OK,
    summary="Suggest Clone Name",
    description="Return the next available Copy N of <name> for this user (Admin only, read-only)",
    dependencies=[Depends(authenticate), Depends(admin_access_only)],
)
def suggest_mcp_config_clone_name(config_id: str, user: User = Depends(authenticate)) -> dict:
    """
    Suggest the next available clone name for an MCP server configuration.

    Pure read: does not reserve the name or write to the database.

    **Path Parameters:**
    - **config_id**: ID of the configuration to clone (system-provided configurations are allowed)

    **Returns:**
    - `{"suggested_name": str}`
    """
    logger.info(f"Suggesting clone name for MCP config: {config_id} by user: {user.id}")
    return {"suggested_name": MCPConfigService.suggested_clone_name(config_id, user)}
```

- [x] **Step 5: Remove the router tests for the deleted route**

In `tests/codemie/rest_api/routers/test_mcp_config.py`, delete the `SUGGESTED_NAME_URL` constant:

```python
SUGGESTED_NAME_URL = "/v1/mcp-configs/src-1/suggested-clone-name"
```

and delete these three tests (search for `test_suggested_name_`):

```python
@pytest.mark.asyncio
async def test_suggested_name_returns_200_with_body_shape(admin):
    _authenticate_as(admin)

    with patch.object(MCPConfigService, "suggested_clone_name", return_value="Copy 1 of Server") as suggest:
        response = await _get(SUGGESTED_NAME_URL)

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"suggested_name": "Copy 1 of Server"}
    suggest.assert_called_once_with("src-1", admin)


@pytest.mark.asyncio
async def test_suggested_name_returns_404_when_source_missing(admin):
    _authenticate_as(admin)
    error = ExtendedHTTPException(
        code=404, message="MCP configuration not found", details="some details", help="some help"
    )

    with patch.object(MCPConfigService, "suggested_clone_name", side_effect=error):
        response = await _get(SUGGESTED_NAME_URL)

    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_suggested_name_returns_403_for_non_admin(regular_user):
    _authenticate_as(regular_user)

    with patch.object(MCPConfigService, "suggested_clone_name") as suggest:
        response = await _get(SUGGESTED_NAME_URL)

    assert response.status_code == status.HTTP_403_FORBIDDEN
    suggest.assert_not_called()
```

Leave the parametrized `test_service_errors_propagate_as_typed_bodies` test untouched — it mocks `clone_from_form` generically and is not specific to the suggestion route.

- [x] **Step 6: Run the affected tests, confirm they fail only where the deleted route/method is still referenced, then run the full suite**

Run: `poetry run pytest tests/codemie/rest_api/routers/test_mcp_config.py tests/codemie/service/test_mcp_config_service_clone.py -v`

Expected at this point (route + service method deleted, baseline not yet regenerated): all tests in these two files PASS. If anything fails with `AttributeError: ... has no attribute 'suggested_clone_name'` or similar, you missed a reference — grep again (Step 1's command) and remove it.

- [x] **Step 7: Regenerate the OpenAPI baseline and review the diff**

Run: `poetry run python -m tests.codemie.rest_api.router_guards.regenerate_baselines`

Then: `git diff tests/codemie/rest_api/router_guards/baselines/openapi.json`

Expected: the diff shows only the removal of the `/v1/mcp-configs/{config_id}/suggested-clone-name` path entry (and its associated `operationId`/schema references, if any are now orphaned). If the diff touches any other path, stop — something else changed unexpectedly; investigate before continuing.

- [x] **Step 8: Run the full backend test suite and lint**

Run: `make ruff && make test`

Expected: both exit 0. `make test` must show no collection errors (an import error for the deleted module would show up as a collection error, not a normal failure).

- [x] **Step 9: Commit**

```bash
git add -A
git commit -m "$(cat <<'EOF'
EPMCDME-15538: remove Copy N of <name> clone-name suggestion feature

The clone form now opens with an empty name (frontend change in a
companion MR) instead of asking the backend to suggest one, so the
suggested-clone-name endpoint, service method, and allocate_name/
compute_base module are dead code.
EOF
)"
```

---

### Task 2: Make the clone endpoint's duplicate-name error match the edit endpoint's wording exactly

**Files:**
- Modify: `src/codemie/service/mcp_config_service.py` (the duplicate-name check inside `clone_from_form` — search for `def clone_from_form`, the check is the first `if existing:` block in that method)
- Modify: `tests/codemie/service/test_mcp_config_service_clone.py:189-197` (`test_duplicate_name_raises_same_error_as_create` — re-locate by searching for that test name; it currently only asserts the status code, not the message)

**Interfaces:**
- Consumes: nothing new.
- Produces: nothing new — this task only changes the `message`/`details`/`help` string literals raised by `MCPConfigService.clone_from_form` when `MCPConfig.get_by_fields({"name": request.name, "user_id": user.id})` finds an existing config. The HTTP status code (400) and the exception type (`ExtendedHTTPException`) are unchanged.

- [ ] **Step 1: Write the failing test — assert exact wording parity with `update`'s duplicate-name error**

In `tests/codemie/service/test_mcp_config_service_clone.py`, replace `test_duplicate_name_raises_same_error_as_create` with:

```python
def test_duplicate_name_raises_same_wording_as_update(encrypt):
    source = _make_source()
    existing = MagicMock()
    request = _build_clone_request(name="Server")

    with pytest.raises(ExtendedHTTPException) as exc:
        _clone_from_form(source, request, get_by_fields_result=existing)

    assert exc.value.code == 400
    assert exc.value.message == "Name already exists"
    assert exc.value.details == "An MCP configuration with name 'Server' already exists"
    assert exc.value.help == "Use a different name"
```

- [ ] **Step 2: Run it to confirm it fails on the wording assertions**

Run: `poetry run pytest tests/codemie/service/test_mcp_config_service_clone.py::test_duplicate_name_raises_same_wording_as_update -v`

Expected: FAIL — `code == 400` passes, but the `message`/`details`/`help` assertions fail against the current clone-specific wording (`"MCP configuration already exists"` / `"Use a different name or update the existing configuration"`).

- [ ] **Step 3: Update the duplicate-name check in `clone_from_form` to match `update`'s wording**

In `src/codemie/service/mcp_config_service.py`, inside `clone_from_form`, change:

```python
        if existing:
            raise ExtendedHTTPException(
                code=status.HTTP_400_BAD_REQUEST,
                message="MCP configuration already exists",
                details=f"An MCP configuration with name '{request.name}' already exists",
                help="Use a different name or update the existing configuration",
            )
```

to:

```python
        if existing:
            raise ExtendedHTTPException(
                code=status.HTTP_400_BAD_REQUEST,
                message="Name already exists",
                details=f"An MCP configuration with name '{request.name}' already exists",
                help="Use a different name",
            )
```

(This now matches `update`'s duplicate-name check verbatim, aside from the two different local variable names both named `request` holding the respective request object — no further change needed there.)

- [ ] **Step 4: Run the test again to confirm it passes**

Run: `poetry run pytest tests/codemie/service/test_mcp_config_service_clone.py::test_duplicate_name_raises_same_wording_as_update -v`

Expected: PASS.

- [ ] **Step 5: Run the full clone and router test files to confirm nothing else broke**

Run: `poetry run pytest tests/codemie/service/test_mcp_config_service_clone.py tests/codemie/rest_api/routers/test_mcp_config.py -v`

Expected: all PASS. (The parametrized `test_service_errors_propagate_as_typed_bodies` test in the router file mocks the message string directly and is unaffected by this change.)

- [ ] **Step 6: Run full lint + test gates**

Run: `make ruff && make test`

Expected: both exit 0.

- [ ] **Step 7: Commit**

```bash
git add src/codemie/service/mcp_config_service.py tests/codemie/service/test_mcp_config_service_clone.py
git commit -m "$(cat <<'EOF'
EPMCDME-15538: align clone's duplicate-name error with edit's wording

Cloning an MCP config into a name that already exists for the same
user now raises the identical message/details/help as editing a
config into a duplicate name, so the UI's generic error-toast path
shows the same text in both flows.
EOF
)"
```
