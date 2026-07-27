# Move/Copy Integrations Between Projects — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `POST /v1/settings/transfer`, an admin/maintainer-only endpoint that moves or copies every transferable integration from one project to another.

**Architecture:** A new scope-neutral router module (`routers/settings.py`) delegates to a new `SettingsTransferService`. The service selects candidates by `project_name`, drops subsystem-managed rows by alias, partitions out copy-blocked trigger types, validates projects and alias collisions (reusing `Settings.check_alias_unique`), then applies all writes inside a single session so the operation is all-or-nothing. Move mutates `project_name` in place preserving the row id; copy inserts a verbatim clone with a new id.

**Tech Stack:** FastAPI, SQLModel over PostgreSQL, Pydantic v2, pytest + pytest-asyncio (strict mode), httpx `AsyncClient` + `ASGITransport`, `unittest.mock`.

## Global Constraints

- Ruff `line-length = 120`, `indent-width = 4`.
- Every new `.py` file starts with the 14-line Apache-2.0 EPAM header (copy verbatim from `src/codemie/rest_api/routers/project_settings.py:1-13`). Enforced by `make license-check`.
- pytest-asyncio runs in **strict** mode — every async test needs an explicit marker. The settings suites use `@pytest.mark.anyio` plus a **module-local** `anyio_backend` fixture returning `'asyncio'`; there is no global one.
- Test secrets must be prefixed `test-fake-` / `TEST_FAKE_` to satisfy `make gitleaks`.
- There is no test database — `tests/conftest.py:34-49` mocks `PostgresClient.get_engine` for the whole session. All persistence must be mocked.
- Assert `status.HTTP_*` constants, never bare integers.
- In router tests, patch `codemie.rest_api.security.idp.local.LocalIdp.authenticate` as the **innermost (bottom)** decorator.
- Never call `Settings.save()` / `Settings.update()` in the transfer write path — each opens its own session and commits immediately (`src/codemie/rest_api/models/base.py:512-514`, `525-529`), which would break atomicity.
- Copy must read raw DB rows and duplicate `credential_values` **ciphertext byte-for-byte**; never route copy data through `SettingsService.get_settings` (masks) or `retrieve_setting` (decrypts).

---

## File Structure

| File | Responsibility |
|---|---|
| `src/codemie/rest_api/models/settings_transfer.py` | **Create.** `TransferMode`, `TransferSettingsRequest`, `TransferredIntegration`, `SkippedIntegration`, `TransferSettingsResponse`. Kept out of `models/settings.py` (476 lines, already holds the table model plus ~15 credential DTOs) so the new feature's contract is one focused file. |
| `src/codemie/service/settings/settings_transfer_service.py` | **Create.** `SettingsTransferService` — candidate selection, exclusion rules, validation, apply, cache invalidation. Kept out of `service/settings/settings.py` (~1600 lines). |
| `src/codemie/rest_api/routers/settings.py` | **Create.** Scope-neutral settings router holding the transfer route. |
| `src/codemie/rest_api/main.py` | **Modify.** Register the new router (import near line 66-67, `include_router` near line 824-825). |
| `tests/codemie/service/settings/test_settings_transfer_service.py` | **Create.** Service-level tests. |
| `tests/codemie/rest_api/routers/test_settings_transfer.py` | **Create.** Router-level tests. |

---

## Task 1: Transfer DTOs

**Files:**
- Create: `src/codemie/rest_api/models/settings_transfer.py`
- Test: `tests/codemie/service/settings/test_settings_transfer_service.py`

**Interfaces:**
- Consumes: `CredentialTypes` from `codemie_tools.base.models`.
- Produces: `TransferMode` (`.MOVE` / `.COPY`, str enum with values `"move"` / `"copy"`), `TransferSettingsRequest(source_project_name: str, target_project_name: str, mode: TransferMode)`, `TransferredIntegration(id: str, alias: str, credential_type: CredentialTypes)`, `SkippedIntegration(alias: str, credential_type: CredentialTypes)`, `TransferSettingsResponse(message, source_project_name, target_project_name, mode, transferred_count, transferred, skipped_count, skipped)`.

**Test-first: yes** — `test_transfer_request_rejects_unknown_mode` asserts `TransferSettingsRequest(..., mode="teleport")` raises `pydantic.ValidationError`; fails with `ModuleNotFoundError: No module named 'codemie.rest_api.models.settings_transfer'`.

- [ ] **Step 1: Write the failing test**

Create `tests/codemie/service/settings/test_settings_transfer_service.py` with the Apache header, then:

```python
import pytest
from pydantic import ValidationError

from codemie.rest_api.models.settings_transfer import TransferMode, TransferSettingsRequest


class TestTransferSettingsRequest:
    def test_accepts_move_and_copy(self):
        # Arrange / Act
        move = TransferSettingsRequest(source_project_name="x", target_project_name="y", mode="move")
        copy = TransferSettingsRequest(source_project_name="x", target_project_name="y", mode="copy")

        # Assert
        assert move.mode is TransferMode.MOVE
        assert copy.mode is TransferMode.COPY

    def test_rejects_unknown_mode(self):
        # Arrange / Act / Assert
        with pytest.raises(ValidationError):
            TransferSettingsRequest(source_project_name="x", target_project_name="y", mode="teleport")

    def test_rejects_missing_mode(self):
        with pytest.raises(ValidationError):
            TransferSettingsRequest(source_project_name="x", target_project_name="y")

    def test_rejects_blank_project_name(self):
        with pytest.raises(ValidationError):
            TransferSettingsRequest(source_project_name="", target_project_name="y", mode="move")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/codemie/service/settings/test_settings_transfer_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'codemie.rest_api.models.settings_transfer'`

- [ ] **Step 3: Write minimal implementation**

Create `src/codemie/rest_api/models/settings_transfer.py` (Apache header first), then:

```python
from enum import Enum
from typing import List

from pydantic import BaseModel, Field

from codemie_tools.base.models import CredentialTypes


class TransferMode(str, Enum):
    """Operation mode for a project integration transfer."""

    MOVE = "move"
    COPY = "copy"


class TransferSettingsRequest(BaseModel):
    source_project_name: str = Field(min_length=1)
    target_project_name: str = Field(min_length=1)
    mode: TransferMode


class TransferredIntegration(BaseModel):
    id: str
    alias: str
    credential_type: CredentialTypes


class SkippedIntegration(BaseModel):
    alias: str
    credential_type: CredentialTypes


class TransferSettingsResponse(BaseModel):
    message: str
    source_project_name: str
    target_project_name: str
    mode: TransferMode
    transferred_count: int
    transferred: List[TransferredIntegration] = Field(default_factory=list)
    skipped_count: int = 0
    skipped: List[SkippedIntegration] = Field(default_factory=list)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/codemie/service/settings/test_settings_transfer_service.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/codemie/rest_api/models/settings_transfer.py tests/codemie/service/settings/test_settings_transfer_service.py
git commit -m "EPMCDME-13666: Add transfer request and response models"
```

---

## Task 2: Candidate selection and subsystem-managed exclusions

**Files:**
- Create: `src/codemie/service/settings/settings_transfer_service.py`
- Modify: `tests/codemie/service/settings/test_settings_transfer_service.py`

**Interfaces:**
- Consumes: `TransferMode` from Task 1; `Settings`, `PROJECT_NAME_TERM` from `codemie.rest_api.models.settings`; `SettingsService.INTERNAL_PREFIX` and `SettingsService.ENFORCE_MEMBER_SPEND_LIMITS_ALIAS`; `DATASOURCE_SCHEDULE_ALIAS_PREFIX` from `codemie.service.settings.scheduler_settings_service`.
- Produces: `SettingsTransferService.LITELLM_PROJECT_KEY_ALIAS_PREFIX: str`, `SettingsTransferService._is_subsystem_managed(alias: str | None) -> bool`, `SettingsTransferService._select_candidates(source_project_name: str) -> list[Settings]`.

**Test-first: yes** — `test_select_candidates_drops_subsystem_managed_rows` asserts the four managed alias shapes are filtered out; fails with `ModuleNotFoundError: No module named 'codemie.service.settings.settings_transfer_service'`.

Note on the LiteLLM prefix: `budget_provider_adapter._PROJECT_KEY_ALIAS_PREFIX` is private, so it is **not** imported at runtime. The value is redeclared on the service and a test asserts the two stay equal, giving drift detection without importing a private name into a core service.

- [ ] **Step 1: Write the failing test**

Append to `tests/codemie/service/settings/test_settings_transfer_service.py`:

```python
from unittest.mock import patch

from codemie.rest_api.models.settings import Settings, SettingType
from codemie.service.settings.settings_transfer_service import SettingsTransferService
from codemie_tools.base.models import CredentialTypes


def _setting(alias, credential_type=CredentialTypes.JIRA, setting_type=SettingType.PROJECT, user_id="u1", id_=None):
    return Settings(
        id=id_ or f"id-{alias}",
        project_name="source",
        alias=alias,
        credential_type=credential_type,
        credential_values=[],
        user_id=user_id,
        setting_type=setting_type,
    )


class TestSelectCandidates:
    def test_litellm_prefix_matches_budget_provider_adapter(self):
        # Arrange
        from codemie.enterprise.litellm.budget_provider_adapter import _PROJECT_KEY_ALIAS_PREFIX

        # Assert
        assert SettingsTransferService.LITELLM_PROJECT_KEY_ALIAS_PREFIX == _PROJECT_KEY_ALIAS_PREFIX

    @patch("codemie.service.settings.settings_transfer_service.Settings.get_all_by_fields")
    def test_drops_subsystem_managed_rows(self, mock_get_all):
        # Arrange
        keep = _setting("jira-prod")
        mock_get_all.return_value = [
            keep,
            _setting("__internal__IDE_abc"),
            _setting("codemie:project:source:category:premium_models", CredentialTypes.LITE_LLM),
            _setting("Schedule_my-datasource", CredentialTypes.SCHEDULER),
            _setting("project_member_budget_tracking_enabled", CredentialTypes.ENVIRONMENT_VARS),
        ]

        # Act
        result = SettingsTransferService._select_candidates("source")

        # Assert
        assert [s.alias for s in result] == ["jira-prod"]
        mock_get_all.assert_called_once_with({"project_name.keyword": "source"})

    @patch("codemie.service.settings.settings_transfer_service.Settings.get_all_by_fields")
    def test_keeps_both_setting_types(self, mock_get_all):
        # Arrange
        mock_get_all.return_value = [
            _setting("project-cred", setting_type=SettingType.PROJECT),
            _setting("user-cred", setting_type=SettingType.USER),
        ]

        # Act
        result = SettingsTransferService._select_candidates("source")

        # Assert
        assert {s.alias for s in result} == {"project-cred", "user-cred"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/codemie/service/settings/test_settings_transfer_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'codemie.service.settings.settings_transfer_service'`

- [ ] **Step 3: Write minimal implementation**

Create `src/codemie/service/settings/settings_transfer_service.py` (Apache header first), then:

```python
from typing import List, Optional

from codemie.configs.logger import logger
from codemie.rest_api.models.settings import PROJECT_NAME_TERM, Settings
from codemie.service.settings.scheduler_settings_service import DATASOURCE_SCHEDULE_ALIAS_PREFIX
from codemie.service.settings.settings import SettingsService


class SettingsTransferService:
    """Move or copy every transferable integration from one project to another."""

    # Mirrors codemie.enterprise.litellm.budget_provider_adapter._PROJECT_KEY_ALIAS_PREFIX.
    # Redeclared rather than imported because that name is private to the enterprise package;
    # test_litellm_prefix_matches_budget_provider_adapter guards against drift.
    LITELLM_PROJECT_KEY_ALIAS_PREFIX = "codemie:project:"

    @classmethod
    def _subsystem_managed_prefixes(cls) -> tuple:
        return (
            SettingsService.INTERNAL_PREFIX,
            cls.LITELLM_PROJECT_KEY_ALIAS_PREFIX,
            DATASOURCE_SCHEDULE_ALIAS_PREFIX,
        )

    @classmethod
    def _is_subsystem_managed(cls, alias: Optional[str]) -> bool:
        """Rows written and owned by other subsystems are never transferred."""
        if not alias:
            return False
        if alias == SettingsService.ENFORCE_MEMBER_SPEND_LIMITS_ALIAS:
            return True
        return any(alias.startswith(prefix) for prefix in cls._subsystem_managed_prefixes())

    @classmethod
    def _select_candidates(cls, source_project_name: str) -> List[Settings]:
        """All integrations attached to the source project, both scopes, minus subsystem-managed rows."""
        rows = Settings.get_all_by_fields({PROJECT_NAME_TERM: source_project_name})
        candidates = [row for row in rows if not cls._is_subsystem_managed(row.alias)]
        logger.info(
            "settings_transfer: selected %s of %s rows in project %r",
            len(candidates),
            len(rows),
            source_project_name,
        )
        return candidates
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/codemie/service/settings/test_settings_transfer_service.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add src/codemie/service/settings/settings_transfer_service.py tests/codemie/service/settings/test_settings_transfer_service.py
git commit -m "EPMCDME-13666: Add transfer candidate selection and subsystem exclusions"
```

---

## Task 3: Copy-mode trigger-type partitioning

**Files:**
- Modify: `src/codemie/service/settings/settings_transfer_service.py`
- Modify: `tests/codemie/service/settings/test_settings_transfer_service.py`

**Interfaces:**
- Consumes: `TransferMode` from Task 1; `_select_candidates` from Task 2.
- Produces: `SettingsTransferService.COPY_BLOCKED_TYPES: tuple[CredentialTypes, ...]`, `SettingsTransferService._partition(candidates: list[Settings], mode: TransferMode) -> tuple[list[Settings], list[Settings]]` returning `(transferable, skipped)`.

**Test-first: yes** — `test_copy_skips_trigger_types` asserts `WEBHOOK` and `SCHEDULER` land in the skipped list under copy; fails with `AttributeError: type object 'SettingsTransferService' has no attribute '_partition'`.

Trigger types are skipped rather than rejected because the caller transfers a whole project and cannot exclude individual rows. Copying a `WEBHOOK` would duplicate a globally-unique `webhook_id` (`models/settings.py:317-327`); copying a `SCHEDULER` would produce two rows with the same `resource_id`, and cron discovery filters on credential type only with no project filter (`triggers/bindings/cron.py:701-713`), so both would fire.

- [ ] **Step 1: Write the failing test**

Append to the service test file:

```python
from codemie.rest_api.models.settings_transfer import TransferMode


class TestPartition:
    def test_copy_skips_trigger_types(self):
        # Arrange
        jira = _setting("jira-prod", CredentialTypes.JIRA)
        hook = _setting("gitlab-hook", CredentialTypes.WEBHOOK)
        cron = _setting("nightly", CredentialTypes.SCHEDULER)

        # Act
        transferable, skipped = SettingsTransferService._partition([jira, hook, cron], TransferMode.COPY)

        # Assert
        assert [s.alias for s in transferable] == ["jira-prod"]
        assert {s.alias for s in skipped} == {"gitlab-hook", "nightly"}

    def test_move_skips_nothing(self):
        # Arrange
        rows = [
            _setting("jira-prod", CredentialTypes.JIRA),
            _setting("gitlab-hook", CredentialTypes.WEBHOOK),
            _setting("nightly", CredentialTypes.SCHEDULER),
        ]

        # Act
        transferable, skipped = SettingsTransferService._partition(rows, TransferMode.MOVE)

        # Assert
        assert len(transferable) == 3
        assert skipped == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/codemie/service/settings/test_settings_transfer_service.py::TestPartition -v`
Expected: FAIL — `AttributeError: type object 'SettingsTransferService' has no attribute '_partition'`

- [ ] **Step 3: Write minimal implementation**

Add to the imports in `settings_transfer_service.py`:

```python
from codemie.rest_api.models.settings_transfer import TransferMode
from codemie_tools.base.models import CredentialTypes
```

Add to the class body, after `LITELLM_PROJECT_KEY_ALIAS_PREFIX`:

```python
    # Trigger bindings: copying one duplicates its firing, so they are move-only.
    COPY_BLOCKED_TYPES = (CredentialTypes.WEBHOOK, CredentialTypes.SCHEDULER)
```

Add the method:

```python
    @classmethod
    def _partition(cls, candidates: List[Settings], mode: TransferMode) -> tuple:
        """Split candidates into (transferable, skipped). Only copy mode ever skips."""
        if mode is not TransferMode.COPY:
            return list(candidates), []

        transferable = []
        skipped = []
        for row in candidates:
            if row.credential_type in cls.COPY_BLOCKED_TYPES:
                skipped.append(row)
            else:
                transferable.append(row)
        return transferable, skipped
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/codemie/service/settings/test_settings_transfer_service.py -v`
Expected: PASS (9 passed)

- [ ] **Step 5: Commit**

```bash
git add src/codemie/service/settings/settings_transfer_service.py tests/codemie/service/settings/test_settings_transfer_service.py
git commit -m "EPMCDME-13666: Skip trigger-type integrations in copy mode"
```

---

## Task 4: Validation — projects, aliases, collisions

**Files:**
- Modify: `src/codemie/service/settings/settings_transfer_service.py`
- Modify: `tests/codemie/service/settings/test_settings_transfer_service.py`

**Interfaces:**
- Consumes: `application_repository` from `codemie.repository.application_repository`; `get_session` from `codemie.clients.postgres`; `Settings.check_alias_unique`; `ExtendedHTTPException` from `codemie.core.exceptions`.
- Produces: `SettingsTransferService._validate_projects(source: str, target: str) -> None`, `SettingsTransferService._validate_aliases(candidates: list[Settings], target: str) -> None`.

**Test-first: yes** — `test_rejects_same_source_and_target` asserts a 422 `ExtendedHTTPException`; fails with `AttributeError: ... has no attribute '_validate_projects'`.

Project existence is checked via `application_repository.get_by_name` plus a `deleted_at` check — mirroring `routers/projects.py:1027-1034` — rather than `ensure_application_exists`, which would silently **create** a missing project (`settings.py:405-410`, `utils/default_applications.py:22`). `exists_by_name` alone is insufficient because it does not exclude soft-deleted projects.

Collision detection **reuses** `Settings.check_alias_unique` with `setting_id=None` in both modes: the query targets the destination project while the candidate still lives in the source, and `source == target` is already rejected, so the self-exclusion branch is unreachable.

- [ ] **Step 1: Write the failing test**

Append to the service test file:

```python
from unittest.mock import MagicMock

from fastapi import status

from codemie.core.exceptions import ExtendedHTTPException


class TestValidateProjects:
    def test_rejects_same_source_and_target(self):
        with pytest.raises(ExtendedHTTPException) as excinfo:
            SettingsTransferService._validate_projects("same", "same")

        assert excinfo.value.code == status.HTTP_422_UNPROCESSABLE_ENTITY

    @patch("codemie.service.settings.settings_transfer_service.application_repository")
    @patch("codemie.service.settings.settings_transfer_service.get_session")
    def test_rejects_missing_source_project(self, mock_get_session, mock_repo):
        # Arrange
        mock_get_session.return_value.__enter__.return_value = MagicMock()
        mock_repo.get_by_name.side_effect = [None, MagicMock(deleted_at=None)]

        # Act / Assert
        with pytest.raises(ExtendedHTTPException) as excinfo:
            SettingsTransferService._validate_projects("missing", "target")

        assert excinfo.value.code == status.HTTP_404_NOT_FOUND
        assert "missing" in excinfo.value.details

    @patch("codemie.service.settings.settings_transfer_service.application_repository")
    @patch("codemie.service.settings.settings_transfer_service.get_session")
    def test_rejects_soft_deleted_target_project(self, mock_get_session, mock_repo):
        # Arrange
        mock_get_session.return_value.__enter__.return_value = MagicMock()
        mock_repo.get_by_name.side_effect = [MagicMock(deleted_at=None), MagicMock(deleted_at="2026-01-01")]

        # Act / Assert
        with pytest.raises(ExtendedHTTPException) as excinfo:
            SettingsTransferService._validate_projects("source", "target")

        assert excinfo.value.code == status.HTTP_404_NOT_FOUND


class TestValidateAliases:
    @patch("codemie.service.settings.settings_transfer_service.Settings.check_alias_unique")
    def test_passes_when_no_collisions(self, mock_check):
        # Arrange
        mock_check.return_value = True
        rows = [_setting("jira-prod"), _setting("git-main")]

        # Act
        SettingsTransferService._validate_aliases(rows, "target")

        # Assert
        assert mock_check.call_count == 2
        assert mock_check.call_args_list[0].kwargs["setting_id"] is None
        assert mock_check.call_args_list[0].kwargs["project_name"] == "target"

    @patch("codemie.service.settings.settings_transfer_service.Settings.check_alias_unique")
    def test_reports_every_colliding_alias(self, mock_check):
        # Arrange
        mock_check.side_effect = [ValueError("dup"), True, ValueError("dup")]
        rows = [_setting("a"), _setting("b"), _setting("c")]

        # Act / Assert
        with pytest.raises(ExtendedHTTPException) as excinfo:
            SettingsTransferService._validate_aliases(rows, "target")

        assert excinfo.value.code == status.HTTP_409_CONFLICT
        assert "a" in excinfo.value.details
        assert "c" in excinfo.value.details

    def test_rejects_row_with_empty_alias(self):
        # Arrange
        row = _setting("placeholder")
        row.alias = None

        # Act / Assert
        with pytest.raises(ExtendedHTTPException) as excinfo:
            SettingsTransferService._validate_aliases([row], "target")

        assert excinfo.value.code == status.HTTP_422_UNPROCESSABLE_ENTITY
        assert row.id in excinfo.value.details
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/codemie/service/settings/test_settings_transfer_service.py::TestValidateProjects -v`
Expected: FAIL — `AttributeError: type object 'SettingsTransferService' has no attribute '_validate_projects'`

- [ ] **Step 3: Write minimal implementation**

Add to the imports in `settings_transfer_service.py`:

```python
from fastapi import status

from codemie.clients.postgres import get_session
from codemie.core.exceptions import ExtendedHTTPException
from codemie.repository.application_repository import application_repository
```

Add the methods:

```python
    @classmethod
    def _validate_projects(cls, source_project_name: str, target_project_name: str) -> None:
        """Both projects must exist and differ. Never auto-creates a missing project."""
        if source_project_name == target_project_name:
            raise ExtendedHTTPException(
                code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                message="Invalid transfer request",
                details="Source and target projects must be different.",
                help="Choose a target project other than the source project.",
            )

        with get_session() as session:
            for label, name in (("Source", source_project_name), ("Target", target_project_name)):
                project = application_repository.get_by_name(session, name)
                if project is None or project.deleted_at is not None:
                    raise ExtendedHTTPException(
                        code=status.HTTP_404_NOT_FOUND,
                        message="Project not found",
                        details=f"{label} project '{name}' does not exist.",
                        help="Check the project name and try again.",
                    )

    @classmethod
    def _validate_aliases(cls, candidates: List[Settings], target_project_name: str) -> None:
        """Reuse the create-path uniqueness rule so a transfer cannot produce a forbidden state."""
        blank = [row for row in candidates if not row.alias]
        if blank:
            details = ", ".join(f"{row.id} ({row.credential_type})" for row in blank)
            raise ExtendedHTTPException(
                code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                message="Integration without an alias",
                details=f"These integrations have no alias and cannot be transferred: {details}.",
                help="Give each integration an alias before transferring the project.",
            )

        colliding = []
        for row in candidates:
            try:
                Settings.check_alias_unique(
                    project_name=target_project_name,
                    alias=row.alias,
                    setting_id=None,
                    user_id=row.user_id,
                    setting_type=row.setting_type,
                )
            except ValueError:
                colliding.append(row.alias)

        if colliding:
            raise ExtendedHTTPException(
                code=status.HTTP_409_CONFLICT,
                message="Alias conflict in target project",
                details=(
                    f"Project '{target_project_name}' already has integrations with these aliases: "
                    f"{', '.join(colliding)}."
                ),
                help="Rename the conflicting integrations in the source project and retry.",
            )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/codemie/service/settings/test_settings_transfer_service.py -v`
Expected: PASS (15 passed)

- [ ] **Step 5: Commit**

```bash
git add src/codemie/service/settings/settings_transfer_service.py tests/codemie/service/settings/test_settings_transfer_service.py
git commit -m "EPMCDME-13666: Validate projects and alias collisions for transfer"
```

---

## Task 5: Apply the transfer atomically

**Files:**
- Modify: `src/codemie/service/settings/settings_transfer_service.py`
- Modify: `tests/codemie/service/settings/test_settings_transfer_service.py`

**Interfaces:**
- Consumes: everything from Tasks 1-4; `CredentialValues` from `codemie.rest_api.models.settings`; `clear_litellm_user_credentials_cache` from `codemie.enterprise.litellm.credentials`.
- Produces: `SettingsTransferService.transfer(source_project_name: str, target_project_name: str, mode: TransferMode) -> TransferSettingsResponse` — the single public entry point.

**Test-first: yes** — `test_move_updates_project_name_in_place` asserts the row's `project_name` changes and its `id` is unchanged with exactly one `commit()`; fails with `AttributeError: ... has no attribute 'transfer'`.

All writes go through one `get_session()` with a single `commit()`. `Settings.save()` / `update()` are deliberately avoided — each opens its own session and commits (`models/base.py:512-514`, `525-529`), which would make a mid-loop failure leave a partial transfer.

Copy rebuilds `credential_values` as fresh `CredentialValues` objects rather than reusing the source list, so the two rows never share mutable state through the JSONB column. Values are carried over **already encrypted**: encryption is not keyed by project, user, or row id (`base_settings.py:43-77`), and byte-identical ciphertext is required by the Google OAuth shared-token guard, which compares encrypted values (`google_oauth/token_manager.py:357-358`). `user_id` is preserved for the same guard, which searches via `Settings.get_by_user_id` (`token_manager.py:346-350`).

- [ ] **Step 1: Write the failing test**

Append to the service test file:

```python
class TestTransfer:
    def _session_mock(self, rows_by_id):
        session = MagicMock()
        session.get.side_effect = lambda model, id_: rows_by_id.get(id_)
        return session

    @patch("codemie.service.settings.settings_transfer_service.get_session")
    @patch.object(SettingsTransferService, "_validate_aliases")
    @patch.object(SettingsTransferService, "_validate_projects")
    @patch.object(SettingsTransferService, "_select_candidates")
    def test_move_updates_project_name_in_place(self, mock_select, _mock_projects, _mock_aliases, mock_get_session):
        # Arrange
        row = _setting("jira-prod", id_="row-1")
        mock_select.return_value = [row]
        session = self._session_mock({"row-1": row})
        mock_get_session.return_value.__enter__.return_value = session

        # Act
        response = SettingsTransferService.transfer("source", "target", TransferMode.MOVE)

        # Assert
        assert row.project_name == "target"
        assert row.id == "row-1"
        assert response.transferred_count == 1
        assert response.transferred[0].id == "row-1"
        assert response.skipped_count == 0
        session.commit.assert_called_once()

    @patch("codemie.service.settings.settings_transfer_service.get_session")
    @patch.object(SettingsTransferService, "_validate_aliases")
    @patch.object(SettingsTransferService, "_validate_projects")
    @patch.object(SettingsTransferService, "_select_candidates")
    def test_copy_creates_new_row_and_leaves_source(self, mock_select, _mp, _ma, mock_get_session):
        # Arrange
        row = _setting("jira-prod", id_="row-1")
        row.created_by = None
        row.credential_values = [CredentialValues(key="token", value="test-fake-cipher")]
        mock_select.return_value = [row]
        session = self._session_mock({"row-1": row})
        mock_get_session.return_value.__enter__.return_value = session

        # Act
        response = SettingsTransferService.transfer("source", "target", TransferMode.COPY)

        # Assert
        assert row.project_name == "source"
        added = session.add.call_args_list[-1].args[0]
        assert added.project_name == "target"
        assert added.id != "row-1"
        assert added.alias == "jira-prod"
        assert added.user_id == row.user_id
        assert added.credential_values[0].value == "test-fake-cipher"
        assert added.credential_values is not row.credential_values
        assert response.transferred[0].id == added.id
        session.commit.assert_called_once()

    @patch("codemie.enterprise.litellm.credentials.clear_litellm_user_credentials_cache")
    @patch("codemie.service.settings.settings_transfer_service.get_session")
    @patch.object(SettingsTransferService, "_validate_aliases")
    @patch.object(SettingsTransferService, "_validate_projects")
    @patch.object(SettingsTransferService, "_select_candidates")
    def test_clears_litellm_cache_only_when_litellm_transferred(
        self, mock_select, _mp, _ma, mock_get_session, mock_clear
    ):
        # Arrange
        row = _setting("llm-key", CredentialTypes.LITE_LLM, id_="row-1")
        mock_select.return_value = [row]
        mock_get_session.return_value.__enter__.return_value = self._session_mock({"row-1": row})

        # Act
        SettingsTransferService.transfer("source", "target", TransferMode.MOVE)

        # Assert
        mock_clear.assert_called_once_with(None)

    @patch("codemie.enterprise.litellm.credentials.clear_litellm_user_credentials_cache")
    @patch("codemie.service.settings.settings_transfer_service.get_session")
    @patch.object(SettingsTransferService, "_validate_aliases")
    @patch.object(SettingsTransferService, "_validate_projects")
    @patch.object(SettingsTransferService, "_select_candidates")
    def test_no_cache_clear_without_litellm(self, mock_select, _mp, _ma, mock_get_session, mock_clear):
        # Arrange
        row = _setting("jira-prod", id_="row-1")
        mock_select.return_value = [row]
        mock_get_session.return_value.__enter__.return_value = self._session_mock({"row-1": row})

        # Act
        SettingsTransferService.transfer("source", "target", TransferMode.MOVE)

        # Assert
        mock_clear.assert_not_called()

    @patch("codemie.service.settings.settings_transfer_service.get_session")
    @patch.object(SettingsTransferService, "_validate_aliases")
    @patch.object(SettingsTransferService, "_validate_projects")
    @patch.object(SettingsTransferService, "_select_candidates")
    def test_empty_source_returns_zero_counts_without_writing(self, mock_select, _mp, _ma, mock_get_session):
        # Arrange
        mock_select.return_value = []

        # Act
        response = SettingsTransferService.transfer("source", "target", TransferMode.MOVE)

        # Assert
        assert response.transferred_count == 0
        assert response.transferred == []
        assert "no transferable integrations" in response.message.lower()
        mock_get_session.assert_not_called()

    @patch("codemie.service.settings.settings_transfer_service.get_session")
    @patch.object(SettingsTransferService, "_validate_aliases")
    @patch.object(SettingsTransferService, "_validate_projects")
    @patch.object(SettingsTransferService, "_select_candidates")
    def test_copy_reports_skipped_trigger_types(self, mock_select, _mp, _ma, mock_get_session):
        # Arrange
        hook = _setting("gitlab-hook", CredentialTypes.WEBHOOK, id_="row-2")
        mock_select.return_value = [hook]

        # Act
        response = SettingsTransferService.transfer("source", "target", TransferMode.COPY)

        # Assert
        assert response.transferred_count == 0
        assert response.skipped_count == 1
        assert response.skipped[0].alias == "gitlab-hook"
        mock_get_session.assert_not_called()
```

Add `CredentialValues` to the test file's imports from `codemie.rest_api.models.settings`.

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/codemie/service/settings/test_settings_transfer_service.py::TestTransfer -v`
Expected: FAIL — `AttributeError: type object 'SettingsTransferService' has no attribute 'transfer'`

- [ ] **Step 3: Write minimal implementation**

Add to the imports in `settings_transfer_service.py`:

```python
from datetime import UTC, datetime
from uuid import uuid4

from codemie.rest_api.models.settings import CredentialValues
from codemie.rest_api.models.settings_transfer import (
    SkippedIntegration,
    TransferredIntegration,
    TransferSettingsResponse,
)
```

Add the methods:

```python
    @classmethod
    def _clone(cls, row: Settings, target_project_name: str, now: datetime) -> Settings:
        """Verbatim clone apart from id, project and timestamps. Ciphertext is carried over as-is."""
        return Settings(
            id=str(uuid4()),
            project_name=target_project_name,
            alias=row.alias,
            credential_type=row.credential_type,
            credential_values=[CredentialValues(key=cred.key, value=cred.value) for cred in row.credential_values],
            user_id=row.user_id,
            created_by=row.created_by,
            setting_type=row.setting_type,
            is_global=row.is_global,
            default=row.default,
            setting_hash=row.setting_hash,
            date=now,
            update_date=now,
        )

    @classmethod
    def _apply(cls, candidates: List[Settings], target_project_name: str, mode: TransferMode) -> List[Settings]:
        """Write every row inside one session so the operation is all-or-nothing."""
        now = datetime.now(UTC)
        written = []
        with get_session() as session:
            for candidate in candidates:
                row = session.get(Settings, candidate.id)
                if row is None:
                    raise ExtendedHTTPException(
                        code=status.HTTP_409_CONFLICT,
                        message="Integration changed during transfer",
                        details=f"Integration '{candidate.alias}' no longer exists. No changes were applied.",
                        help="Retry the transfer.",
                    )
                if mode is TransferMode.MOVE:
                    row.project_name = target_project_name
                    row.update_date = now
                    session.add(row)
                    written.append(row)
                else:
                    clone = cls._clone(row, target_project_name, now)
                    session.add(clone)
                    written.append(clone)
            session.commit()
        return written

    @classmethod
    def transfer(
        cls, source_project_name: str, target_project_name: str, mode: TransferMode
    ) -> TransferSettingsResponse:
        """Move or copy every transferable integration from the source project to the target project."""
        cls._validate_projects(source_project_name, target_project_name)

        candidates = cls._select_candidates(source_project_name)
        transferable, skipped = cls._partition(candidates, mode)

        skipped_payload = [
            SkippedIntegration(alias=row.alias, credential_type=row.credential_type) for row in skipped
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

        cls._validate_aliases(transferable, target_project_name)
        written = cls._apply(transferable, target_project_name, mode)

        if any(row.credential_type == CredentialTypes.LITE_LLM for row in written):
            from codemie.enterprise.litellm.credentials import clear_litellm_user_credentials_cache

            clear_litellm_user_credentials_cache(None)

        transferred_payload = [
            TransferredIntegration(id=row.id, alias=row.alias, credential_type=row.credential_type) for row in written
        ]
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

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/codemie/service/settings/test_settings_transfer_service.py -v`
Expected: PASS (21 passed)

- [ ] **Step 5: Commit**

```bash
git add src/codemie/service/settings/settings_transfer_service.py tests/codemie/service/settings/test_settings_transfer_service.py
git commit -m "EPMCDME-13666: Apply project integration transfer atomically"
```

---

## Task 6: Router, registration and OpenAPI documentation

**Files:**
- Create: `src/codemie/rest_api/routers/settings.py`
- Modify: `src/codemie/rest_api/main.py` (import block near line 66-67; `include_router` near line 824-825)
- Test: `tests/codemie/rest_api/routers/test_settings_transfer.py`

**Interfaces:**
- Consumes: `SettingsTransferService.transfer` from Task 5; the DTOs from Task 1; `authenticate` and `admin_or_maintainer_access_only` from `codemie.rest_api.security.authentication`.
- Produces: `POST /v1/settings/transfer`.

**Test-first: yes** — `test_transfer_requires_admin_or_maintainer` asserts a non-admin caller raises `ExtendedHTTPException` with 403 and that `SettingsTransferService.transfer` is never called; fails with `ModuleNotFoundError: No module named 'codemie.rest_api.routers.settings'`.

`admin_or_maintainer_access_only` (`security/authentication.py:173-183`) reads `request.state.user`, so `authenticate` must still be declared on the handler to supply the actor for logging.

- [ ] **Step 1: Write the failing test**

Create `tests/codemie/rest_api/routers/test_settings_transfer.py` with the Apache header, then:

```python
"""Tests for POST /v1/settings/transfer (move/copy project integrations)."""

from unittest.mock import patch

import pytest
from fastapi import FastAPI, status
from httpx import ASGITransport, AsyncClient

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.settings_transfer import TransferMode, TransferSettingsResponse
from codemie.rest_api.routers.settings import router
from codemie.rest_api.security.user import User

app = FastAPI()
app.include_router(router)


@pytest.fixture
def anyio_backend():
    return 'asyncio'


@pytest.fixture
def admin_user():
    return User(id="admin-1", username="admin@example.com", roles=["admin"], project_names=[], admin_project_names=[])


@pytest.fixture
def plain_user():
    return User(id="user-1", username="user@example.com", project_names=["source"], admin_project_names=["source"])


def _ok_response():
    return TransferSettingsResponse(
        message="Transferred 1 integration(s) from 'source' to 'target'.",
        source_project_name="source",
        target_project_name="target",
        mode=TransferMode.MOVE,
        transferred_count=1,
        transferred=[{"id": "row-1", "alias": "jira-prod", "credential_type": "Jira"}],
        skipped_count=0,
        skipped=[],
    )


@pytest.mark.anyio
@patch("codemie.rest_api.routers.settings.SettingsTransferService.transfer")
@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
async def test_transfer_move_success(mock_authenticate, mock_transfer, admin_user):
    # Arrange
    mock_authenticate.return_value = admin_user
    mock_transfer.return_value = _ok_response()

    # Act
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.post(
            "/v1/settings/transfer",
            headers={"user-id": "admin-1"},
            json={"source_project_name": "source", "target_project_name": "target", "mode": "move"},
        )

    # Assert
    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["transferred_count"] == 1
    assert body["mode"] == "move"
    mock_transfer.assert_called_once_with(
        source_project_name="source", target_project_name="target", mode=TransferMode.MOVE
    )


@pytest.mark.anyio
@patch("codemie.rest_api.routers.settings.SettingsTransferService.transfer")
@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
async def test_transfer_requires_admin_or_maintainer(mock_authenticate, mock_transfer, plain_user):
    # Arrange
    mock_authenticate.return_value = plain_user

    # Act / Assert
    transport = ASGITransport(app=app)
    with pytest.raises(ExtendedHTTPException) as excinfo:
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            await ac.post(
                "/v1/settings/transfer",
                headers={"user-id": "user-1"},
                json={"source_project_name": "source", "target_project_name": "target", "mode": "move"},
            )

    assert excinfo.value.code == status.HTTP_403_FORBIDDEN
    mock_transfer.assert_not_called()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "payload",
    [
        {"source_project_name": "source", "target_project_name": "target"},
        {"source_project_name": "source", "target_project_name": "target", "mode": "teleport"},
        {"source_project_name": "", "target_project_name": "target", "mode": "move"},
    ],
    ids=["missing-mode", "unsupported-mode", "blank-source"],
)
@patch("codemie.rest_api.routers.settings.SettingsTransferService.transfer")
@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
async def test_transfer_rejects_invalid_payload(mock_authenticate, mock_transfer, admin_user, payload):
    # Arrange
    mock_authenticate.return_value = admin_user

    # Act
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.post("/v1/settings/transfer", headers={"user-id": "admin-1"}, json=payload)

    # Assert
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    mock_transfer.assert_not_called()


@pytest.mark.anyio
@patch("codemie.rest_api.routers.settings.SettingsTransferService.transfer")
@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
async def test_transfer_propagates_service_errors(mock_authenticate, mock_transfer, admin_user):
    # Arrange
    mock_authenticate.return_value = admin_user
    mock_transfer.side_effect = ExtendedHTTPException(
        code=status.HTTP_409_CONFLICT, message="Alias conflict in target project", details="jira-prod"
    )

    # Act / Assert
    transport = ASGITransport(app=app)
    with pytest.raises(ExtendedHTTPException) as excinfo:
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            await ac.post(
                "/v1/settings/transfer",
                headers={"user-id": "admin-1"},
                json={"source_project_name": "source", "target_project_name": "target", "mode": "move"},
            )

    assert excinfo.value.code == status.HTTP_409_CONFLICT
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/codemie/rest_api/routers/test_settings_transfer.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'codemie.rest_api.routers.settings'`

- [ ] **Step 3: Write minimal implementation**

Create `src/codemie/rest_api/routers/settings.py` (Apache header first), then:

```python
from fastapi import APIRouter, Depends, status

from codemie.rest_api.models.settings_transfer import TransferSettingsRequest, TransferSettingsResponse
from codemie.rest_api.security.authentication import admin_or_maintainer_access_only, authenticate, User
from codemie.service.settings.settings_transfer_service import SettingsTransferService

router = APIRouter(
    tags=["Settings"],
    prefix="/v1",
    dependencies=[],
)


@router.post(
    "/settings/transfer",
    status_code=status.HTTP_200_OK,
    response_model=TransferSettingsResponse,
    dependencies=[Depends(admin_or_maintainer_access_only)],
)
def transfer_settings(request: TransferSettingsRequest, user: User = Depends(authenticate)):
    """
    Move or copy every transferable integration from one project to another.

    Requires administrator or maintainer privileges. Both project-scoped and user-scoped
    integrations are transferred; a user-scoped integration keeps its owner and only changes
    which project it is attached to.

    **`move`** updates each integration in place, so entities that reference an integration by
    id (datasources, assistants, Bedrock entities, A2A) keep working. References made *by alias*
    resolve against the referencing entity's own project and will fail after a move: workflow
    tool nodes raise an error, per-user MCP integration overrides are ignored, and re-saving an
    assistant-user mapping to a moved integration is rejected. This matches existing behaviour
    for integrations that become unavailable.

    **`copy`** duplicates each integration into the target project and leaves the source
    untouched. Webhook and scheduler integrations are **not** copied — copying a trigger would
    duplicate its firing — and are listed in `skipped`. They are transferred normally by `move`.

    Integrations managed by other subsystems are never transferred and are not reported: aliases
    beginning with `__internal__` (IDE plugin settings), `codemie:project:` (LiteLLM budget keys)
    or `Schedule_` (datasource schedules), and the `project_member_budget_tracking_enabled` flag.

    The operation is all-or-nothing: if any alias already exists in the target project, or either
    project is missing, nothing is changed.
    """
    return SettingsTransferService.transfer(
        source_project_name=request.source_project_name,
        target_project_name=request.target_project_name,
        mode=request.mode,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/codemie/rest_api/routers/test_settings_transfer.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: Register the router**

In `src/codemie/rest_api/main.py`, add `settings,` to the routers tuple immediately after `project_settings,` (line 67):

```python
    user_settings,
    project_settings,
    settings,
```

Then add the registration immediately after line 825:

```python
app.include_router(project_settings.router)
app.include_router(settings.router)
```

- [ ] **Step 6: Verify registration and run full gates**

Run: `poetry run python -c "from codemie.rest_api.main import app; print([r.path for r in app.routes if 'transfer' in r.path])"`
Expected: `['/v1/settings/transfer']`

Run: `make ruff`
Expected: format + `ruff check --fix` + `ruff check` all exit 0. `make ruff` also catches any name shadowing introduced by the bare `settings` import.

Run: `make license-check`
Expected: exits 0 (all three new files carry the Apache header).

Run: `poetry run pytest tests/codemie/service/settings/test_settings_transfer_service.py tests/codemie/rest_api/routers/test_settings_transfer.py tests/codemie/rest_api/routers/test_project_settings.py tests/codemie/rest_api/routers/test_user_settings.py tests/codemie/rest_api/routers/test_user_settings_crud.py -v`
Expected: all pass — the last three confirm existing integration create/edit behaviour is not regressed.

- [ ] **Step 7: Commit**

```bash
git add src/codemie/rest_api/routers/settings.py src/codemie/rest_api/main.py tests/codemie/rest_api/routers/test_settings_transfer.py
git commit -m "EPMCDME-13666: Add POST /v1/settings/transfer endpoint"
```

---

## Self-Review

**Spec coverage.** §2 endpoint contract → Tasks 1 and 6. §3.1 move → Task 5. §3.2 copy → Task 5 (`_clone`). §4.1 silent exclusions → Task 2. §4.2 reported skips → Task 3. §5 validation order → Tasks 4 and 5 (`transfer` sequences `_validate_projects` → `_select_candidates` → `_partition` → empty check → `_validate_aliases` → `_apply`). §5.1 collision reuse → Task 4. §6 atomicity → Task 5 (`_apply`). §7 cache invalidation → Task 5. §8 OpenAPI description → Task 6 docstring. §9 error catalogue → Tasks 4, 5 and 6. §10 tests → every task. No gaps.

**Placeholder scan.** No TBD/TODO, no "add error handling", no "similar to Task N". Every code step carries complete code.

**Type consistency.** `TransferMode`, `TransferSettingsRequest`, `TransferSettingsResponse`, `TransferredIntegration`, `SkippedIntegration` are defined in Task 1 and used with identical names and fields in Tasks 3, 5 and 6. `_select_candidates`, `_partition`, `_validate_projects`, `_validate_aliases`, `_clone`, `_apply` and `transfer` keep the same signatures where they are defined and where they are consumed. `transfer` is called with keyword arguments in Task 6 exactly as the Task 5 signature declares.

**One deviation worth flagging at review.** §5 of the spec lists project validation as steps 1-3 and the empty-source response as step 6. `transfer` runs `_validate_projects` before selection, so a missing project returns 404 even when the source has no integrations. This matches the spec's ordering and is the intended precedence.
