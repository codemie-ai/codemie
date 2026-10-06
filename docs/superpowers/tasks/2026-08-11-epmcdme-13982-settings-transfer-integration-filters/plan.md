# Settings Transfer Integration Filters — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend `POST /v1/settings/transfer` with an optional `type_of_integration` filter (user/project scope) and an optional `list` of aliases, preserving full backwards compatibility when both are omitted.

**Architecture:** Add a `TypeOfIntegration` nested Pydantic model and `integrations_list` field (JSON alias `list`) to `TransferSettingsRequest`. Thread `setting_types: set[SettingType]` and `integrations_list: list[str] | None` from the router through `SettingsTransferService.transfer()` into `_select_candidates()`, where both filters are applied after the existing ES fetch and subsystem-managed exclusion. Validation (both-flags-false, unknown aliases) raises `ExtendedHTTPException` before any write occurs.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, SQLModel, pytest + anyio, `unittest.mock`

## Global Constraints

- Both new fields are optional; omitting either preserves current behaviour exactly.
- `_partition`, `_validate_aliases_present`, `_validate_no_collisions`, `_apply` are not changed.
- Advisory lock and all-or-nothing semantics are untouched.
- Run `make ruff` after each task before committing; run `make test` at the end of the plan.
- Commit message format: `EPMCDME-13982: <description>`

---

### Task 1: Extend request model with `TypeOfIntegration` and `integrations_list`

**Test-first: yes — test that the new fields parse correctly and that both-flags-false fails at the router layer (422)**

**Files:**
- Modify: `src/codemie/rest_api/models/settings_transfer.py`
- Modify: `tests/codemie/service/settings/test_settings_transfer_service.py` (class `TestTransferSettingsRequest`)

**Interfaces:**
- Produces: `TypeOfIntegration` model; `TransferSettingsRequest.type_of_integration: TypeOfIntegration`; `TransferSettingsRequest.integrations_list: list[str] | None`

- [ ] **Step 1: Write failing tests for the new model fields**

Add these cases to `TestTransferSettingsRequest` in `tests/codemie/service/settings/test_settings_transfer_service.py`:

```python
from pydantic import ConfigDict

def test_type_of_integration_defaults_to_both_true(self):
    req = TransferSettingsRequest(source_project_name="x", target_project_name="y", mode="move")
    assert req.type_of_integration.user_integrations is True
    assert req.type_of_integration.project_integrations is True

def test_type_of_integration_accepts_user_only(self):
    req = TransferSettingsRequest(
        source_project_name="x",
        target_project_name="y",
        mode="move",
        type_of_integration={"user_integrations": True, "project_integrations": False},
    )
    assert req.type_of_integration.user_integrations is True
    assert req.type_of_integration.project_integrations is False

def test_integrations_list_defaults_to_none(self):
    req = TransferSettingsRequest(source_project_name="x", target_project_name="y", mode="move")
    assert req.integrations_list is None

def test_integrations_list_accepts_alias_list(self):
    req = TransferSettingsRequest(
        source_project_name="x",
        target_project_name="y",
        mode="move",
        **{"list": ["jira-prod", "git-main"]},
    )
    assert req.integrations_list == ["jira-prod", "git-main"]

def test_integrations_list_accepts_by_python_name(self):
    req = TransferSettingsRequest(
        source_project_name="x",
        target_project_name="y",
        mode="move",
        integrations_list=["jira-prod"],
    )
    assert req.integrations_list == ["jira-prod"]
```

- [ ] **Step 2: Run tests to verify they fail**

```
poetry run pytest tests/codemie/service/settings/test_settings_transfer_service.py::TestTransferSettingsRequest -v
```

Expected: FAIL — `TypeOfIntegration` and `integrations_list` do not exist yet.

- [ ] **Step 3: Implement the model changes**

In `src/codemie/rest_api/models/settings_transfer.py`:

```python
from pydantic import BaseModel, ConfigDict, Field


class TypeOfIntegration(BaseModel):
    user_integrations: bool = True
    project_integrations: bool = True


class TransferSettingsRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    source_project_name: str = Field(min_length=1)
    target_project_name: str = Field(min_length=1)
    mode: TransferMode
    type_of_integration: TypeOfIntegration = Field(default_factory=TypeOfIntegration)
    integrations_list: list[str] | None = Field(default=None, alias="list")
```

- [ ] **Step 4: Run tests to verify they pass**

```
poetry run pytest tests/codemie/service/settings/test_settings_transfer_service.py::TestTransferSettingsRequest -v
```

Expected: all `TestTransferSettingsRequest` tests PASS.

- [ ] **Step 5: Run ruff**

```
make ruff
```

- [ ] **Step 6: Commit**

```bash
git add src/codemie/rest_api/models/settings_transfer.py \
        tests/codemie/service/settings/test_settings_transfer_service.py
git commit -m "EPMCDME-13982: add TypeOfIntegration model and integrations_list field to TransferSettingsRequest"
```

---

### Task 2: Add filtering logic to `_select_candidates` and update `transfer` signature

**Test-first: yes — tests for `_select_candidates` filtering and `transfer` both-flags-false validation**

**Files:**
- Modify: `src/codemie/service/settings/settings_transfer_service.py`
- Modify: `tests/codemie/service/settings/test_settings_transfer_service.py`

**Interfaces:**
- Consumes: `SettingType` from `codemie.rest_api.models.settings`
- Produces:
  - `SettingsTransferService._select_candidates(source_project_name: str, setting_types: set[SettingType], integrations_list: list[str] | None) -> list[Settings]`
  - `SettingsTransferService.transfer(source_project_name: str, target_project_name: str, mode: TransferMode, setting_types: set[SettingType], integrations_list: list[str] | None) -> TransferSettingsResponse`

- [ ] **Step 1: Write failing tests**

Add `TestSelectCandidatesFiltering` class and a both-flags-false test to `tests/codemie/service/settings/test_settings_transfer_service.py`:

```python
class TestSelectCandidatesFiltering:
    @patch("codemie.service.settings.settings_transfer_service.Settings.get_all_by_fields")
    def test_user_only_excludes_project_rows(self, mock_get_all):
        mock_get_all.return_value = [
            _setting("proj-cred", setting_type=SettingType.PROJECT),
            _setting("user-cred", setting_type=SettingType.USER),
        ]
        result = SettingsTransferService._select_candidates(
            "source", {SettingType.USER}, None
        )
        assert [s.alias for s in result] == ["user-cred"]

    @patch("codemie.service.settings.settings_transfer_service.Settings.get_all_by_fields")
    def test_project_only_excludes_user_rows(self, mock_get_all):
        mock_get_all.return_value = [
            _setting("proj-cred", setting_type=SettingType.PROJECT),
            _setting("user-cred", setting_type=SettingType.USER),
        ]
        result = SettingsTransferService._select_candidates(
            "source", {SettingType.PROJECT}, None
        )
        assert [s.alias for s in result] == ["proj-cred"]

    @patch("codemie.service.settings.settings_transfer_service.Settings.get_all_by_fields")
    def test_both_flags_keeps_all_types(self, mock_get_all):
        mock_get_all.return_value = [
            _setting("proj-cred", setting_type=SettingType.PROJECT),
            _setting("user-cred", setting_type=SettingType.USER),
        ]
        result = SettingsTransferService._select_candidates(
            "source", {SettingType.USER, SettingType.PROJECT}, None
        )
        assert {s.alias for s in result} == {"proj-cred", "user-cred"}

    @patch("codemie.service.settings.settings_transfer_service.Settings.get_all_by_fields")
    def test_list_filter_returns_subset(self, mock_get_all):
        mock_get_all.return_value = [
            _setting("jira-prod", setting_type=SettingType.PROJECT),
            _setting("git-main", setting_type=SettingType.PROJECT),
            _setting("slack-ops", setting_type=SettingType.PROJECT),
        ]
        result = SettingsTransferService._select_candidates(
            "source", {SettingType.PROJECT}, ["jira-prod", "git-main"]
        )
        assert {s.alias for s in result} == {"jira-prod", "git-main"}

    @patch("codemie.service.settings.settings_transfer_service.Settings.get_all_by_fields")
    def test_list_with_unknown_alias_raises_422(self, mock_get_all):
        mock_get_all.return_value = [_setting("jira-prod", setting_type=SettingType.PROJECT)]
        with pytest.raises(ExtendedHTTPException) as excinfo:
            SettingsTransferService._select_candidates(
                "source", {SettingType.PROJECT}, ["jira-prod", "ghost-alias"]
            )
        assert excinfo.value.code == status.HTTP_422_UNPROCESSABLE_ENTITY
        assert "ghost-alias" in excinfo.value.details

    @patch("codemie.service.settings.settings_transfer_service.Settings.get_all_by_fields")
    def test_alias_excluded_by_type_filter_counts_as_unknown(self, mock_get_all):
        mock_get_all.return_value = [
            _setting("user-cred", setting_type=SettingType.USER),
        ]
        with pytest.raises(ExtendedHTTPException) as excinfo:
            SettingsTransferService._select_candidates(
                "source", {SettingType.PROJECT}, ["user-cred"]
            )
        assert excinfo.value.code == status.HTTP_422_UNPROCESSABLE_ENTITY
        assert "user-cred" in excinfo.value.details


class TestTransferBothFlagsFalse:
    def test_both_flags_false_raises_422(self):
        with pytest.raises(ExtendedHTTPException) as excinfo:
            SettingsTransferService.transfer(
                "source", "target", TransferMode.MOVE,
                set(), None
            )
        assert excinfo.value.code == status.HTTP_422_UNPROCESSABLE_ENTITY
        assert "at least one integration type" in excinfo.value.details.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

```
poetry run pytest tests/codemie/service/settings/test_settings_transfer_service.py::TestSelectCandidatesFiltering tests/codemie/service/settings/test_settings_transfer_service.py::TestTransferBothFlagsFalse -v
```

Expected: FAIL — `_select_candidates` doesn't accept the new parameters yet.

- [ ] **Step 3: Update `_select_candidates` in the service**

Replace the existing `_select_candidates` method in `src/codemie/service/settings/settings_transfer_service.py`:

```python
@classmethod
def _select_candidates(
    cls,
    source_project_name: str,
    setting_types: set[SettingType],
    integrations_list: list[str] | None,
) -> list[Settings]:
    """All integrations attached to the source project, filtered by type and optional alias list."""
    rows = Settings.get_all_by_fields({PROJECT_NAME_TERM: source_project_name})
    candidates = [
        row for row in rows
        if not cls._is_subsystem_managed(row.alias) and row.setting_type in setting_types
    ]

    if integrations_list is not None:
        requested = set(integrations_list)
        matched_aliases = {row.alias for row in candidates}
        unknown = requested - matched_aliases
        if unknown:
            raise ExtendedHTTPException(
                code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                message="Unknown integration identifiers",
                details=f"These aliases were not found in project '{source_project_name}': {', '.join(sorted(unknown))}.",
                help="Check the alias names and try again.",
            )
        candidates = [row for row in candidates if row.alias in requested]

    logger.info(
        "settings_transfer: selected %s row(s) in project %r (types=%s, list=%s)",
        len(candidates),
        source_project_name,
        {t.value for t in setting_types},
        integrations_list,
    )

    return candidates
```

Also add the `SettingType` import at the top of the file (it is already available via `codemie.rest_api.models.settings`):

```python
from codemie.rest_api.models.settings import PROJECT_NAME_TERM, CredentialValues, Settings, SettingType
```

- [ ] **Step 4: Update `transfer` signature and add both-flags-false validation**

Replace the `transfer` classmethod in `src/codemie/service/settings/settings_transfer_service.py`:

```python
@classmethod
def transfer(
    cls,
    source_project_name: str,
    target_project_name: str,
    mode: TransferMode,
    setting_types: set[SettingType],
    integrations_list: list[str] | None,
) -> TransferSettingsResponse:
    """Move or copy filtered integrations from the source project to the target project."""
    if not setting_types:
        raise ExtendedHTTPException(
            code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            message="Invalid transfer request",
            details="At least one integration type must be selected.",
            help="Set user_integrations or project_integrations (or both) to true.",
        )

    cls._validate_projects(source_project_name, target_project_name)

    candidates = cls._select_candidates(source_project_name, setting_types, integrations_list)
    cls._validate_aliases_present(candidates)

    transferable, skipped = cls._partition(candidates, mode)
    skipped_payload = [
        TransferItem(id=row.id, alias=row.alias, credential_type=row.credential_type) for row in skipped
    ]

    if not transferable:
        return TransferSettingsResponse(
            message=f"No transferable integrations found in project '{source_project_name}'.",
            source_project_name=source_project_name,
            target_project_name=target_project_name,
            mode=mode,
            transferred_count=0,
            transferred=[],
            skipped_count=len(skipped_payload),
            skipped=skipped_payload,
        )

    transferred_payload, has_litellm = cls._apply(transferable, source_project_name, target_project_name, mode)

    if has_litellm:
        from codemie.enterprise.litellm.credentials import clear_litellm_user_credentials_cache

        clear_litellm_user_credentials_cache(None)

    logger.info(
        "settings_transfer: %s %s integration(s) from %r to %r (%s skipped)",
        mode.value,
        len(transferred_payload),
        source_project_name,
        target_project_name,
        len(skipped_payload),
    )

    return TransferSettingsResponse(
        message=(
            f"Transferred {len(transferred_payload)} integration(s) from "
            f"'{source_project_name}' to '{target_project_name}'."
        ),
        source_project_name=source_project_name,
        target_project_name=target_project_name,
        mode=mode,
        transferred_count=len(transferred_payload),
        transferred=transferred_payload,
        skipped_count=len(skipped_payload),
        skipped=skipped_payload,
    )
```

- [ ] **Step 5: Update `TestSelectCandidates.test_keeps_both_setting_types` and `test_drops_subsystem_managed_rows` to pass new required args**

In `tests/codemie/service/settings/test_settings_transfer_service.py`, update both existing `TestSelectCandidates` tests:

```python
class TestSelectCandidates:
    def test_litellm_prefix_matches_budget_provider_adapter(self):
        from codemie.enterprise.litellm.budget_provider_adapter import _PROJECT_KEY_ALIAS_PREFIX
        assert SettingsTransferService.LITELLM_PROJECT_KEY_ALIAS_PREFIX == _PROJECT_KEY_ALIAS_PREFIX

    @patch("codemie.service.settings.settings_transfer_service.Settings.get_all_by_fields")
    def test_drops_subsystem_managed_rows(self, mock_get_all):
        keep = _setting("jira-prod")
        mock_get_all.return_value = [
            keep,
            _setting("__internal__IDE_abc"),
            _setting("codemie:project:source:category:premium_models", CredentialTypes.LITE_LLM),
            _setting("Schedule_my-datasource", CredentialTypes.SCHEDULER),
            _setting("project_member_budget_tracking_enabled", CredentialTypes.ENVIRONMENT_VARS),
        ]
        result = SettingsTransferService._select_candidates(
            "source", {SettingType.USER, SettingType.PROJECT}, None
        )
        assert [s.alias for s in result] == ["jira-prod"]
        mock_get_all.assert_called_once_with({"project_name.keyword": "source"})

    @patch("codemie.service.settings.settings_transfer_service.Settings.get_all_by_fields")
    def test_keeps_both_setting_types(self, mock_get_all):
        mock_get_all.return_value = [
            _setting("project-cred", setting_type=SettingType.PROJECT),
            _setting("user-cred", setting_type=SettingType.USER),
        ]
        result = SettingsTransferService._select_candidates(
            "source", {SettingType.USER, SettingType.PROJECT}, None
        )
        assert {s.alias for s in result} == {"project-cred", "user-cred"}
```

- [ ] **Step 6: Update `TestTransfer` (9 tests) and `TestApply*` classes to pass new required args to `transfer()`**

Every call `SettingsTransferService.transfer("source", "target", TransferMode.X)` needs two new trailing kwargs. Apply this change to all occurrences in `TestTransfer`, `TestApplyOrdersLockingBeforeValidation`, and `TestApplyDoesNotLeakOrmState`:

```python
# Before:
SettingsTransferService.transfer("source", "target", TransferMode.MOVE)
SettingsTransferService.transfer("source", "target", TransferMode.COPY)

# After (add the two new kwargs to every call):
SettingsTransferService.transfer(
    "source", "target", TransferMode.MOVE,
    {SettingType.USER, SettingType.PROJECT}, None
)
SettingsTransferService.transfer(
    "source", "target", TransferMode.COPY,
    {SettingType.USER, SettingType.PROJECT}, None
)
```

There are 9 calls total in `TestTransfer` and 2 in the `TestApply*` classes — update all of them.

- [ ] **Step 7: Run new and updated service tests**

```
poetry run pytest tests/codemie/service/settings/test_settings_transfer_service.py -v
```

Expected: all tests PASS.

- [ ] **Step 8: Run ruff**

```
make ruff
```

- [ ] **Step 9: Commit**

```bash
git add src/codemie/service/settings/settings_transfer_service.py \
        tests/codemie/service/settings/test_settings_transfer_service.py
git commit -m "EPMCDME-13982: add setting_type and alias-list filtering to _select_candidates; extend transfer() signature"
```

---

### Task 3: Update router to derive filters and pass to service

**Test-first: yes — router tests for new filter fields and both-flags-false 422**

**Files:**
- Modify: `src/codemie/rest_api/routers/settings.py`
- Modify: `tests/codemie/rest_api/routers/test_settings_transfer.py`

**Interfaces:**
- Consumes: `TypeOfIntegration`, `TransferSettingsRequest.integrations_list`, `SettingType`
- Produces: router calls `SettingsTransferService.transfer(..., setting_types=..., integrations_list=...)`

- [ ] **Step 1: Write failing router tests**

Add to `tests/codemie/rest_api/routers/test_settings_transfer.py`:

```python
from codemie.rest_api.models.settings import SettingType
from codemie.rest_api.models.settings_transfer import TypeOfIntegration


@pytest.mark.anyio
@patch("codemie.rest_api.routers.settings.SettingsTransferService.transfer")
@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
async def test_transfer_user_only_filter(mock_authenticate, mock_transfer, admin_user):
    mock_authenticate.return_value = admin_user
    mock_transfer.return_value = _ok_response()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.post(
            "/v1/settings/transfer",
            headers={"user-id": "admin-1"},
            json={
                "source_project_name": "source",
                "target_project_name": "target",
                "mode": "move",
                "type_of_integration": {"user_integrations": True, "project_integrations": False},
            },
        )

    assert response.status_code == status.HTTP_200_OK
    mock_transfer.assert_called_once_with(
        source_project_name="source",
        target_project_name="target",
        mode=TransferMode.MOVE,
        setting_types={SettingType.USER},
        integrations_list=None,
    )


@pytest.mark.anyio
@patch("codemie.rest_api.routers.settings.SettingsTransferService.transfer")
@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
async def test_transfer_project_only_filter(mock_authenticate, mock_transfer, admin_user):
    mock_authenticate.return_value = admin_user
    mock_transfer.return_value = _ok_response()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.post(
            "/v1/settings/transfer",
            headers={"user-id": "admin-1"},
            json={
                "source_project_name": "source",
                "target_project_name": "target",
                "mode": "move",
                "type_of_integration": {"user_integrations": False, "project_integrations": True},
            },
        )

    assert response.status_code == status.HTTP_200_OK
    mock_transfer.assert_called_once_with(
        source_project_name="source",
        target_project_name="target",
        mode=TransferMode.MOVE,
        setting_types={SettingType.PROJECT},
        integrations_list=None,
    )


@pytest.mark.anyio
@patch("codemie.rest_api.routers.settings.SettingsTransferService.transfer")
@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
async def test_transfer_with_list_filter(mock_authenticate, mock_transfer, admin_user):
    mock_authenticate.return_value = admin_user
    mock_transfer.return_value = _ok_response()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.post(
            "/v1/settings/transfer",
            headers={"user-id": "admin-1"},
            json={
                "source_project_name": "source",
                "target_project_name": "target",
                "mode": "move",
                "list": ["jira-prod", "git-main"],
            },
        )

    assert response.status_code == status.HTTP_200_OK
    mock_transfer.assert_called_once_with(
        source_project_name="source",
        target_project_name="target",
        mode=TransferMode.MOVE,
        setting_types={SettingType.USER, SettingType.PROJECT},
        integrations_list=["jira-prod", "git-main"],
    )


@pytest.mark.anyio
@patch("codemie.rest_api.routers.settings.SettingsTransferService.transfer")
@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
async def test_transfer_both_flags_false_returns_422(mock_authenticate, mock_transfer, admin_user):
    mock_authenticate.return_value = admin_user

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.post(
            "/v1/settings/transfer",
            headers={"user-id": "admin-1"},
            json={
                "source_project_name": "source",
                "target_project_name": "target",
                "mode": "move",
                "type_of_integration": {"user_integrations": False, "project_integrations": False},
            },
        )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    mock_transfer.assert_not_called()
```

- [ ] **Step 2: Run new router tests to verify they fail**

```
poetry run pytest tests/codemie/rest_api/routers/test_settings_transfer.py::test_transfer_user_only_filter tests/codemie/rest_api/routers/test_settings_transfer.py::test_transfer_project_only_filter tests/codemie/rest_api/routers/test_settings_transfer.py::test_transfer_with_list_filter tests/codemie/rest_api/routers/test_settings_transfer.py::test_transfer_both_flags_false_returns_422 -v
```

Expected: FAIL — router still passes old three-arg call.

- [ ] **Step 3: Update the router**

Replace the body of `transfer_settings` in `src/codemie/rest_api/routers/settings.py`:

```python
from codemie.rest_api.models.settings import SettingType
from codemie.rest_api.models.settings_transfer import TransferSettingsRequest, TransferSettingsResponse
from codemie.rest_api.security.authentication import User, admin_or_maintainer_access_only, authenticate
from codemie.service.settings.settings_transfer_service import SettingsTransferService
from codemie.core.exceptions import ExtendedHTTPException
from fastapi import status as http_status


@router.post(
    "/settings/transfer",
    status_code=http_status.HTTP_200_OK,
    response_model=TransferSettingsResponse,
    dependencies=[Depends(authenticate), Depends(admin_or_maintainer_access_only)],
)
def transfer_settings(request: TransferSettingsRequest, user: User = Depends(authenticate)):
    """
    Move or copy every transferable integration from one project to another.
    ...
    """
    toi = request.type_of_integration
    setting_types: set[SettingType] = set()
    if toi.user_integrations:
        setting_types.add(SettingType.USER)
    if toi.project_integrations:
        setting_types.add(SettingType.PROJECT)

    if not setting_types:
        raise ExtendedHTTPException(
            code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            message="Invalid transfer request",
            details="At least one integration type must be selected.",
            help="Set user_integrations or project_integrations (or both) to true.",
        )

    logger.info(
        "settings_transfer_requested: actor_user_id=%s mode=%s source=%r target=%r types=%s list=%s",
        user.id,
        request.mode.value,
        request.source_project_name,
        request.target_project_name,
        {t.value for t in setting_types},
        request.integrations_list,
    )

    return SettingsTransferService.transfer(
        source_project_name=request.source_project_name,
        target_project_name=request.target_project_name,
        mode=request.mode,
        setting_types=setting_types,
        integrations_list=request.integrations_list,
    )
```

Keep the existing docstring intact; only the body logic changes.

- [ ] **Step 4: Update `test_transfer_move_success` and `test_transfer_propagates_service_errors` to use new kwargs**

In `tests/codemie/rest_api/routers/test_settings_transfer.py`, update the existing `mock_transfer.assert_called_once_with` in `test_transfer_move_success`:

```python
mock_transfer.assert_called_once_with(
    source_project_name="source",
    target_project_name="target",
    mode=TransferMode.MOVE,
    setting_types={SettingType.USER, SettingType.PROJECT},
    integrations_list=None,
)
```

`test_transfer_propagates_service_errors` has no `assert_called_once_with` — no change needed there.

- [ ] **Step 5: Run all router tests**

```
poetry run pytest tests/codemie/rest_api/routers/test_settings_transfer.py -v
```

Expected: all tests PASS.

- [ ] **Step 6: Run ruff**

```
make ruff
```

- [ ] **Step 7: Commit**

```bash
git add src/codemie/rest_api/routers/settings.py \
        tests/codemie/rest_api/routers/test_settings_transfer.py
git commit -m "EPMCDME-13982: update router to derive setting_types from type_of_integration and pass filters to service"
```

---

### Task 4: Full test suite verification

**Test-first: n/a — verification only**

**Files:** No code changes.

- [ ] **Step 1: Run the full test suite**

```
make test
```

Expected: all tests PASS, no regressions.

- [ ] **Step 2: Run ruff one final time**

```
make ruff
```

Expected: no issues.
