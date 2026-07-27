# Timezone Selection for Scheduler Cron Expression — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Thread an optional IANA timezone field through both scheduler creation paths (user-settings and index router) so cron jobs fire at the correct local time, including across DST transitions.

**Architecture:** Add `timezone: Optional[str] = None` as a parameter from request models → service layer → trigger engine. Store as a new `CredentialValues(key="timezone")` entry in the existing JSONB column — no migration required. Fall back to `config.TIMEZONE` (`"UTC"`) when absent to preserve backward compatibility with all existing rows.

**Tech Stack:** Python 3.12, APScheduler 3.x (`CronTrigger`), Pydantic v2, SQLModel, FastAPI, `zoneinfo` (stdlib), `tzdata` (Python package for IANA data)

## Global Constraints

- IANA timezone names only (e.g. `"Europe/Warsaw"`, `"America/New_York"`, `"UTC"`). UTC offsets (`"UTC+2"`) are not accepted.
- `timezone` is always `Optional[str] = None`. Never required. Absent → falls back to `config.TIMEZONE`.
- Use `zoneinfo.ZoneInfo` from stdlib for validation. Do not use `pytz` for new code — it is an undeclared transitive dependency.
- All `ExtendedHTTPException` errors use `code=status.HTTP_422_UNPROCESSABLE_ENTITY`.
- Commit messages: `EPMCDME-11895: <description>`
- Tests: `pytest` with `unittest.mock.patch` and `MagicMock`. No `pytest-mock`.
- Run tests with: `poetry run pytest <test_file> -v` (requires `poetry shell` or `poetry run` — see `.ai-run/guides/development/setup-guide.md`).

---

## File Map

| Action | File |
|---|---|
| Modify | `pyproject.toml` |
| Modify | `src/codemie/service/settings/scheduler_settings_service.py` |
| Modify | `src/codemie/service/settings/settings_request_validator.py` |
| Modify | `src/codemie/rest_api/models/index.py` |
| Modify | `src/codemie/rest_api/routers/index.py` |
| Modify | `src/codemie/datasource/base_datasource_processor.py` |
| Modify | `src/codemie/service/index/index_service.py` |
| Modify | `src/codemie/triggers/bindings/cron.py` |
| Modify | `src/codemie/rest_api/models/settings.py` (doc-only) |
| Modify | `tests/codemie/service/settings/test_scheduler_settings_service.py` |
| Modify | `tests/codemie/service/settings/test_settings_request_validator.py` |
| Modify | `tests/codemie/rest_api/routers/test_settings_cron_validation.py` |
| Modify | `tests/codemie/triggers/bindings/test_cron.py` |
| Modify | `tests/codemie/service/index/test_index_service.py` |

---

### Task 1: `tzdata` dependency and `validate_timezone_string`

**Files:**
- Modify: `pyproject.toml` (dependencies section, near line 95)
- Modify: `src/codemie/service/settings/scheduler_settings_service.py` (after `validate_cron_expression` near line 392)
- Modify: `tests/codemie/service/settings/test_scheduler_settings_service.py`

**Interfaces:**
- Produces: `validate_timezone_string(timezone: Optional[str]) -> None` — raises `ExtendedHTTPException` (HTTP 422) for unrecognised IANA names; returns silently for `None` or valid names.

- [ ] **Step 1: Write the failing tests**

Add to `tests/codemie/service/settings/test_scheduler_settings_service.py`:

```python
import pytest
from fastapi import status
from codemie.core.exceptions import ExtendedHTTPException
from codemie.service.settings.scheduler_settings_service import validate_timezone_string


@pytest.mark.parametrize("tz", ["UTC", "Europe/Warsaw", "America/New_York", "Asia/Tokyo"])
def test_validate_timezone_string_valid(tz):
    validate_timezone_string(tz)  # must not raise


def test_validate_timezone_string_none_is_allowed():
    validate_timezone_string(None)  # must not raise


@pytest.mark.parametrize("tz", ["UTC+2", "Europe/Nowhere", "Bad/Zone", "not_a_timezone"])
def test_validate_timezone_string_invalid(tz):
    with pytest.raises(ExtendedHTTPException) as exc_info:
        validate_timezone_string(tz)
    assert exc_info.value.code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert "IANA" in exc_info.value.help
```

- [ ] **Step 2: Run to verify FAIL**

```
poetry run pytest tests/codemie/service/settings/test_scheduler_settings_service.py -k "timezone" -v
```

Expected: `ImportError` — `validate_timezone_string` not yet defined.

- [ ] **Step 3: Add `tzdata` to `pyproject.toml`**

In the `[tool.poetry.dependencies]` section, add after the `gitpython` line (~line 99):

```toml
tzdata = ">=2024.1"
```

Install:

```
poetry lock --no-update && poetry install
```

- [ ] **Step 4: Implement `validate_timezone_string`**

In `src/codemie/service/settings/scheduler_settings_service.py`, add these imports at the top of the file if not present:

```python
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
```

Then add `validate_timezone_string` directly after the `validate_cron_expression` function (around line 420):

```python
def validate_timezone_string(timezone: Optional[str]) -> None:
    """
    Validate an IANA timezone name.

    Args:
        timezone: IANA timezone string (e.g. "Europe/Warsaw") or None (accepted — means use default)

    Raises:
        ExtendedHTTPException: If timezone is a non-None, unrecognised IANA name
    """
    if timezone is None:
        return
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, KeyError):
        raise ExtendedHTTPException(
            code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            message="Invalid timezone",
            details=f"'{timezone}' is not a recognised IANA timezone name.",
            help=(
                "Provide an IANA timezone name such as 'Europe/Warsaw', "
                "'America/New_York', or 'UTC'."
            ),
        )
```

- [ ] **Step 5: Run to verify PASS**

```
poetry run pytest tests/codemie/service/settings/test_scheduler_settings_service.py -k "timezone" -v
```

Expected: all 9 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml poetry.lock \
    src/codemie/service/settings/scheduler_settings_service.py \
    tests/codemie/service/settings/test_scheduler_settings_service.py
git commit -m "EPMCDME-11895: Add tzdata dep and validate_timezone_string"
```

---

### Task 2: User-settings validation path

**Files:**
- Modify: `src/codemie/service/settings/settings_request_validator.py`
- Modify: `tests/codemie/service/settings/test_settings_request_validator.py`
- Modify: `tests/codemie/rest_api/routers/test_settings_cron_validation.py`

**Interfaces:**
- Consumes: `validate_timezone_string` from Task 1
- Produces: `validate_timezone_value(request: SettingRequest) -> None` — called inside `validate_scheduler_request` after cron validation.

- [ ] **Step 1: Write the failing tests**

Add to `tests/codemie/service/settings/test_settings_request_validator.py`:

```python
from unittest.mock import MagicMock, patch
from fastapi import status
import pytest
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.settings import CredentialValues, SettingRequest
from codemie.service.settings.settings_request_validator import validate_timezone_value


def _make_scheduler_request(schedule="0 9 * * *", timezone=None):
    creds = [
        CredentialValues(key="schedule", value=schedule),
        CredentialValues(key="resource_type", value="assistant"),
        CredentialValues(key="resource_id", value="abc123"),
        CredentialValues(key="is_enabled", value=True),
    ]
    if timezone is not None:
        creds.append(CredentialValues(key="timezone", value=timezone))
    req = MagicMock(spec=SettingRequest)
    req.credential_values = creds
    return req


def test_validate_timezone_value_absent_is_ok():
    """No timezone credential → should pass silently."""
    validate_timezone_value(_make_scheduler_request())


def test_validate_timezone_value_valid_iana():
    validate_timezone_value(_make_scheduler_request(timezone="Europe/Warsaw"))


def test_validate_timezone_value_invalid_raises_422():
    with pytest.raises(ExtendedHTTPException) as exc_info:
        validate_timezone_value(_make_scheduler_request(timezone="UTC+2"))
    assert exc_info.value.code == status.HTTP_422_UNPROCESSABLE_ENTITY
```

Add to `tests/codemie/rest_api/routers/test_settings_cron_validation.py`:

```python
from unittest.mock import patch, MagicMock
from fastapi import status
import pytest
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.settings import CredentialValues, SettingRequest
from codemie.service.settings.settings_request_validator import validate_scheduler_request


def _make_full_scheduler_request(timezone=None):
    creds = [
        CredentialValues(key="schedule", value="0 9 * * *"),
        CredentialValues(key="resource_type", value="assistant"),
        CredentialValues(key="resource_id", value="abc123"),
        CredentialValues(key="is_enabled", value=True),
    ]
    if timezone is not None:
        creds.append(CredentialValues(key="timezone", value=timezone))
    req = MagicMock(spec=SettingRequest)
    req.credential_values = creds
    req.project_name = "test_project"
    return req


@patch("codemie.service.settings.settings_request_validator.validate_resource_type", return_value="assistant")
@patch("codemie.service.settings.settings_request_validator.validate_resource_id", return_value="abc123")
@patch("codemie.service.settings.settings_request_validator.validate_resource_ownership", return_value=None)
def test_validate_scheduler_request_bad_timezone_rejected(mock_own, mock_rid, mock_rtype):
    with pytest.raises(ExtendedHTTPException) as exc_info:
        validate_scheduler_request(_make_full_scheduler_request(timezone="Not/Valid"))
    assert exc_info.value.code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert "IANA" in exc_info.value.help


@patch("codemie.service.settings.settings_request_validator.validate_resource_type", return_value="assistant")
@patch("codemie.service.settings.settings_request_validator.validate_resource_id", return_value="abc123")
@patch("codemie.service.settings.settings_request_validator.validate_resource_ownership", return_value=None)
def test_validate_scheduler_request_valid_timezone_passes(mock_own, mock_rid, mock_rtype):
    validate_scheduler_request(_make_full_scheduler_request(timezone="America/New_York"))
```

- [ ] **Step 2: Run to verify FAIL**

```
poetry run pytest tests/codemie/service/settings/test_settings_request_validator.py tests/codemie/rest_api/routers/test_settings_cron_validation.py -k "timezone" -v
```

Expected: `ImportError` — `validate_timezone_value` not yet defined.

- [ ] **Step 3: Add `validate_timezone_value` and wire into `validate_scheduler_request`**

In `src/codemie/service/settings/settings_request_validator.py`:

Add to imports at top:

```python
from codemie.service.settings.scheduler_settings_service import (
    _validate_minimum_hourly_frequency,
    INVALID_CRON_EXPRESSION_MESSAGE,
    validate_timezone_string,
)
```

Add the new function (after `validate_cron_expression` around line 490):

```python
def validate_timezone_value(request: SettingRequest) -> None:
    """
    Extract and validate the timezone credential value from the request.

    Raises:
        ExtendedHTTPException: If timezone is present and is not a valid IANA name
    """
    timezone_cred = next(
        (cred for cred in request.credential_values if cred.key == "timezone"), None
    )
    if timezone_cred and isinstance(timezone_cred.value, str):
        validate_timezone_string(timezone_cred.value)
```

In `validate_scheduler_request`, add a call to `validate_timezone_value(request)` after the `validate_cron_expression(request)` call:

```python
def validate_scheduler_request(request: SettingRequest) -> None:
    # ... existing body ...
    validate_cron_expression(request)
    validate_timezone_value(request)   # ← add this line
```

- [ ] **Step 4: Run to verify PASS**

```
poetry run pytest tests/codemie/service/settings/test_settings_request_validator.py tests/codemie/rest_api/routers/test_settings_cron_validation.py -k "timezone" -v
```

Expected: all new timezone tests PASS; pre-existing tests still PASS.

- [ ] **Step 5: Commit**

```bash
git add src/codemie/service/settings/settings_request_validator.py \
    tests/codemie/service/settings/test_settings_request_validator.py \
    tests/codemie/rest_api/routers/test_settings_cron_validation.py
git commit -m "EPMCDME-11895: Add timezone validation to user-settings scheduler path"
```

---

### Task 3: Service CRUD — `handle_schedule`, `create_or_update_schedule`, `_create_new_schedule`, `_update_schedule_values`, `get_scheduler_settings_for_datasources`

**Files:**
- Modify: `src/codemie/service/settings/scheduler_settings_service.py`
- Modify: `tests/codemie/service/settings/test_scheduler_settings_service_class.py`

**Interfaces:**
- Consumes: `validate_timezone_string` from Task 1
- Produces:
  - `handle_schedule(..., timezone: Optional[str] = None) -> Settings | None`
  - `create_or_update_schedule(..., timezone: Optional[str] = None) -> Settings | None`
  - `_create_new_schedule(..., timezone: Optional[str] = None) -> Settings`
  - `_update_schedule_values(schedule, cron_expression, is_enabled, resource_name, timezone: Optional[str] = None)`
  - `get_scheduler_settings_for_datasources(...) -> Dict[str, dict]`  where each value is `{"cron_expression": str, "timezone": Optional[str]}`

- [ ] **Step 1: Write the failing tests**

Add to `tests/codemie/service/settings/test_scheduler_settings_service_class.py`:

```python
from unittest.mock import MagicMock, patch
import pytest
from codemie.service.settings.scheduler_settings_service import SchedulerSettingsService
from codemie.rest_api.models.settings import Settings, CredentialValues, CredentialTypes, SettingType


def _make_settings_mock(resource_id="ds1", cron="0 9 * * *", timezone=None, alias="idx-schedule-ds1"):
    setting = MagicMock(spec=Settings)
    setting.id = "setting_1"
    creds = [
        CredentialValues(key="schedule", value=cron),
        CredentialValues(key="resource_type", value="datasource"),
        CredentialValues(key="resource_id", value=resource_id),
        CredentialValues(key="is_enabled", value=True),
    ]
    if timezone is not None:
        creds.append(CredentialValues(key="timezone", value=timezone))
    setting.credential_values = creds
    setting.alias = alias
    setting.credential = lambda key: next(
        (c.value for c in creds if c.key == key), None
    )
    return setting


class TestCreateNewSchedule:
    def test_stores_timezone_when_provided(self):
        result = SchedulerSettingsService._create_new_schedule(
            user_id="u1", project_name="proj", resource_type="datasource",
            resource_id="r1", resource_name="my_repo", cron_expression="0 9 * * *",
            is_enabled=True, timezone="Europe/Warsaw",
        )
        keys = {c.key: c.value for c in result.credential_values}
        assert keys["timezone"] == "Europe/Warsaw"

    def test_no_timezone_key_when_none(self):
        result = SchedulerSettingsService._create_new_schedule(
            user_id="u1", project_name="proj", resource_type="datasource",
            resource_id="r1", resource_name="my_repo", cron_expression="0 9 * * *",
            is_enabled=True, timezone=None,
        )
        keys = {c.key for c in result.credential_values}
        assert "timezone" not in keys


class TestUpdateScheduleValues:
    def test_updates_existing_timezone_credential(self):
        setting = _make_settings_mock(timezone="UTC")
        SchedulerSettingsService._update_schedule_values(
            setting, "0 10 * * *", True, "my_repo", timezone="America/New_York"
        )
        keys = {c.key: c.value for c in setting.credential_values}
        assert keys["timezone"] == "America/New_York"

    def test_adds_timezone_when_previously_absent(self):
        setting = _make_settings_mock(timezone=None)
        SchedulerSettingsService._update_schedule_values(
            setting, "0 10 * * *", True, "my_repo", timezone="Asia/Tokyo"
        )
        keys = {c.key: c.value for c in setting.credential_values}
        assert keys["timezone"] == "Asia/Tokyo"

    def test_leaves_timezone_absent_when_new_value_is_none(self):
        setting = _make_settings_mock(timezone=None)
        SchedulerSettingsService._update_schedule_values(
            setting, "0 10 * * *", True, "my_repo", timezone=None
        )
        keys = {c.key for c in setting.credential_values}
        assert "timezone" not in keys


class TestGetSchedulerSettingsForDatasources:
    @patch("codemie.service.settings.scheduler_settings_service.Settings.get_all_by_fields")
    def test_returns_dict_with_timezone(self, mock_get):
        mock_get.return_value = [_make_settings_mock(timezone="Europe/Warsaw")]
        result = SchedulerSettingsService.get_scheduler_settings_for_datasources("u1", ["ds1"])
        assert result["ds1"]["cron_expression"] == "0 9 * * *"
        assert result["ds1"]["timezone"] == "Europe/Warsaw"

    @patch("codemie.service.settings.scheduler_settings_service.Settings.get_all_by_fields")
    def test_returns_none_timezone_when_absent(self, mock_get):
        mock_get.return_value = [_make_settings_mock(timezone=None)]
        result = SchedulerSettingsService.get_scheduler_settings_for_datasources("u1", ["ds1"])
        assert result["ds1"]["timezone"] is None
```

- [ ] **Step 2: Run to verify FAIL**

```
poetry run pytest tests/codemie/service/settings/test_scheduler_settings_service_class.py -k "Timezone or timezone or Schedule" -v
```

Expected: `TypeError` — unexpected keyword `timezone`.

- [ ] **Step 3: Update `_create_new_schedule`**

Add `timezone: Optional[str] = None` parameter. Inside the method, append the `timezone` credential only when not `None`:

```python
@staticmethod
def _create_new_schedule(
    user_id: str,
    project_name: str,
    resource_type: str,
    resource_id: str,
    resource_name: str,
    cron_expression: str,
    is_enabled: bool,
    timezone: Optional[str] = None,
) -> Settings:
    credential_values = [
        CredentialValues(key="schedule", value=cron_expression),
        CredentialValues(key="resource_type", value=RESOURCE_TYPE_DATASOURCE),
        CredentialValues(key="resource_id", value=resource_id),
        CredentialValues(key="is_enabled", value=True),
    ]
    if timezone is not None:
        credential_values.append(CredentialValues(key="timezone", value=timezone))

    new_schedule = Settings(
        user_id=user_id,
        project_name=project_name,
        alias=f"{DATASOURCE_SCHEDULE_ALIAS_PREFIX}{resource_name}",
        credential_type=CredentialTypes.SCHEDULER,
        credential_values=credential_values,
        setting_type=SettingType.USER,
        is_global=False,
    )
    return new_schedule
```

- [ ] **Step 4: Update `_update_schedule_values`**

Add `timezone: Optional[str] = None` parameter. Handle all three cases (update existing, add when absent and new value given, leave absent when new value is None):

```python
@staticmethod
def _update_schedule_values(
    schedule: Settings,
    cron_expression: str,
    is_enabled: bool,
    resource_name: str,
    timezone: Optional[str] = None,
):
    for cred in schedule.credential_values:
        if cred.key == "schedule":
            cred.value = cron_expression
        elif cred.key == "is_enabled":
            cred.value = is_enabled
        elif cred.key == "timezone" and timezone is not None:
            cred.value = timezone

    # Add timezone entry if it doesn't exist yet and a value was provided
    if timezone is not None:
        existing_keys = {c.key for c in schedule.credential_values}
        if "timezone" not in existing_keys:
            schedule.credential_values.append(CredentialValues(key="timezone", value=timezone))

    schedule.alias = f"{DATASOURCE_SCHEDULE_ALIAS_PREFIX}{resource_name}"
```

- [ ] **Step 5: Update `create_or_update_schedule` to accept and forward `timezone`**

Add `timezone: Optional[str] = None` to the signature. Pass it to both `_update_schedule_values` and `_create_new_schedule`:

```python
@staticmethod
def create_or_update_schedule(
    user_id: str,
    project_name: str,
    resource_type: str,
    resource_id: str,
    resource_name: str,
    cron_expression: str,
    is_enabled: bool = True,
    timezone: Optional[str] = None,
) -> Settings | None:
    # ... existing guard ...
    if existing_schedule:
        SchedulerSettingsService._update_schedule_values(
            existing_schedule, cron_expression, is_enabled, resource_name, timezone=timezone
        )
        # ... rest unchanged ...
    else:
        new_schedule = SchedulerSettingsService._create_new_schedule(
            user_id=user_id,
            project_name=project_name,
            resource_type=resource_type,
            resource_id=resource_id,
            resource_name=resource_name,
            cron_expression=cron_expression,
            is_enabled=is_enabled,
            timezone=timezone,
        )
        # ... rest unchanged ...
```

- [ ] **Step 6: Update `handle_schedule` to accept and forward `timezone`**

Add `timezone: Optional[str] = None` to the signature. Pass it to `create_or_update_schedule`:

```python
@staticmethod
def handle_schedule(
    user_id: str,
    project_name: str,
    resource_id: str,
    resource_name: str,
    cron_expression: str | None,
    resource_type: str = RESOURCE_TYPE_DATASOURCE,
    timezone: Optional[str] = None,
) -> Settings | None:
    # Existing logic unchanged; update the create_or_update_schedule call:
    return SchedulerSettingsService.create_or_update_schedule(
        user_id=user_id,
        project_name=project_name,
        resource_type=resource_type,
        resource_id=resource_id,
        resource_name=resource_name,
        cron_expression=cron_expression,
        timezone=timezone,
    )
```

- [ ] **Step 7: Update `get_scheduler_settings_for_datasources` return type**

Change the return to `Dict[str, dict]` with both keys:

```python
@staticmethod
def get_scheduler_settings_for_datasources(user_id: str, datasource_ids: List[str]) -> Dict[str, dict]:
    # ...existing query logic unchanged...
    schedule_map = {}
    for setting in all_settings:
        resource_id = setting.credential("resource_id")
        schedule = setting.credential("schedule")
        is_enabled = setting.credential("is_enabled")
        timezone = setting.credential("timezone")  # None if absent — backward compat
        has_index_router_alias = setting.alias and setting.alias.startswith(DATASOURCE_SCHEDULE_ALIAS_PREFIX)

        if resource_id in datasource_ids and is_enabled and has_index_router_alias:
            schedule_map[resource_id] = {
                "cron_expression": schedule,
                "timezone": timezone,
            }

    return schedule_map
```

- [ ] **Step 8: Run to verify PASS**

```
poetry run pytest tests/codemie/service/settings/test_scheduler_settings_service_class.py -v
```

Expected: all tests PASS.

- [ ] **Step 9: Commit**

```bash
git add src/codemie/service/settings/scheduler_settings_service.py \
    tests/codemie/service/settings/test_scheduler_settings_service_class.py
git commit -m "EPMCDME-11895: Thread timezone through service CRUD and schedule-map return"
```

---

### Task 4: Index enrichment

**Files:**
- Modify: `src/codemie/service/index/index_service.py`
- Modify: `tests/codemie/service/index/test_index_service.py`

**Interfaces:**
- Consumes: `get_scheduler_settings_for_datasources` returning `Dict[str, dict]` from Task 3
- Produces: `enrich_index_with_schedule` returns dict with both `"cron_expression"` and `"timezone"` keys

- [ ] **Step 1: Write the failing test**

Add to `tests/codemie/service/index/test_index_service.py`:

```python
from unittest.mock import patch, MagicMock
from codemie.service.index.index_service import IndexStatusService


class TestEnrichIndexWithSchedule:
    def _make_index(self, index_id="ds1"):
        index = MagicMock()
        index.id = index_id
        index.model_dump.return_value = {"id": index_id, "repo_name": "test"}
        return index

    def _make_user(self, user_id="u1"):
        user = MagicMock()
        user.id = user_id
        return user

    @patch(
        "codemie.service.index.index_service.SchedulerSettingsService"
        ".get_scheduler_settings_for_datasources"
    )
    def test_enrich_includes_timezone(self, mock_get):
        mock_get.return_value = {
            "ds1": {"cron_expression": "0 9 * * *", "timezone": "Europe/Warsaw"}
        }
        result = IndexStatusService.enrich_index_with_schedule(
            self._make_index(), self._make_user()
        )
        assert result["cron_expression"] == "0 9 * * *"
        assert result["timezone"] == "Europe/Warsaw"

    @patch(
        "codemie.service.index.index_service.SchedulerSettingsService"
        ".get_scheduler_settings_for_datasources"
    )
    def test_enrich_timezone_none_when_absent(self, mock_get):
        mock_get.return_value = {
            "ds1": {"cron_expression": "0 9 * * *", "timezone": None}
        }
        result = IndexStatusService.enrich_index_with_schedule(
            self._make_index(), self._make_user()
        )
        assert result["timezone"] is None

    @patch(
        "codemie.service.index.index_service.SchedulerSettingsService"
        ".get_scheduler_settings_for_datasources"
    )
    def test_enrich_no_schedule_timezone_is_none(self, mock_get):
        mock_get.return_value = {}
        result = IndexStatusService.enrich_index_with_schedule(
            self._make_index(), self._make_user()
        )
        assert result["cron_expression"] is None
        assert result["timezone"] is None
```

- [ ] **Step 2: Run to verify FAIL**

```
poetry run pytest tests/codemie/service/index/test_index_service.py::TestEnrichIndexWithSchedule -v
```

Expected: `KeyError` or `AssertionError` — `timezone` key not yet present.

- [ ] **Step 3: Update `enrich_index_with_schedule`**

In `src/codemie/service/index/index_service.py`, replace the body of `enrich_index_with_schedule`:

```python
@classmethod
def enrich_index_with_schedule(cls, index: IndexInfo, user: User) -> Dict[str, Any]:
    from codemie.service.settings.scheduler_settings_service import SchedulerSettingsService

    cron_expression = None
    timezone = None
    try:
        scheduler_map = SchedulerSettingsService.get_scheduler_settings_for_datasources(
            user.id, [str(index.id)]
        )
        schedule_data = scheduler_map.get(str(index.id))
        if schedule_data:
            cron_expression = schedule_data.get("cron_expression")
            timezone = schedule_data.get("timezone")
    except Exception as e:
        logger.error(f"Failed to fetch scheduler settings for index {index.id}: {e}", exc_info=True)

    index_dict = index.model_dump()
    index_dict["cron_expression"] = cron_expression
    index_dict["timezone"] = timezone
    return index_dict
```

- [ ] **Step 4: Run to verify PASS**

```
poetry run pytest tests/codemie/service/index/test_index_service.py::TestEnrichIndexWithSchedule -v
```

Expected: all 3 new tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/codemie/service/index/index_service.py \
    tests/codemie/service/index/test_index_service.py
git commit -m "EPMCDME-11895: Return timezone from enrich_index_with_schedule"
```

---

### Task 5: Request model mixin (`CronExpressionValidatorMixin`)

**Files:**
- Modify: `src/codemie/rest_api/models/index.py`

**Interfaces:**
- Consumes: `validate_timezone_string` from Task 1
- Produces: `CronExpressionValidatorMixin` gains `timezone: Optional[str] = None`; all inheriting models automatically expose `request.timezone`

Note: No dedicated unit test file exists for `CronExpressionValidatorMixin`. The integration tests added in Task 2 for the router path cover the validation. The mixin change is verified in Task 6 when the router call sites pass `request.timezone`.

- [ ] **Step 1: Update `CronExpressionValidatorMixin`**

In `src/codemie/rest_api/models/index.py`, find `CronExpressionValidatorMixin` (~line 113) and add the `timezone` field and extend the model validator:

```python
from codemie.service.settings.scheduler_settings_service import (
    validate_cron_expression,
    validate_timezone_string,
)


class CronExpressionValidatorMixin:
    """Mixin to add cron_expression and timezone validation to request models."""

    timezone: Optional[str] = None

    @model_validator(mode='after')
    def validate_cron_expression_field(self):
        """Validate cron_expression and timezone if explicitly provided."""
        if 'cron_expression' in self.model_fields_set:
            validate_cron_expression(self.cron_expression)
        if 'timezone' in self.model_fields_set:
            validate_timezone_string(self.timezone)
        return self
```

- [ ] **Step 2: Also update the documentation `Scheduler` model in `settings.py`**

In `src/codemie/rest_api/models/settings.py`, find the `Scheduler` class (lines 213–217) and add:

```python
class Scheduler(BaseModel):
    schedule: str
    is_enabled: bool
    resource_type: str
    resource_id: str
    timezone: Optional[str] = None  # IANA timezone name, e.g. "Europe/Warsaw"
```

- [ ] **Step 3: Run existing mixin-related tests to verify no regression**

```
poetry run pytest tests/codemie/rest_api/ -v -x
```

Expected: all pre-existing router tests PASS.

- [ ] **Step 4: Commit**

```bash
git add src/codemie/rest_api/models/index.py \
    src/codemie/rest_api/models/settings.py
git commit -m "EPMCDME-11895: Add timezone field to CronExpressionValidatorMixin and Scheduler model"
```

---

### Task 6: Datasource processor and index router call sites

**Files:**
- Modify: `src/codemie/datasource/base_datasource_processor.py`
- Modify: `src/codemie/rest_api/routers/index.py`

**Interfaces:**
- Consumes: `request.timezone` from Task 5 mixin; `handle_schedule(timezone=)` from Task 3
- Produces: `_create_or_update_scheduler(cron_expression, timezone)` and `_update_datasource_scheduler(user_id, index_info, cron_expression, timezone)` carry timezone to the service layer

- [ ] **Step 1: Update `BaseDatasourceProcessor.__init__` and `_create_or_update_scheduler`**

In `src/codemie/datasource/base_datasource_processor.py`:

Add `timezone: Optional[str] = None` to `__init__` (line ~88):

```python
def __init__(
    self,
    datasource_name: str,
    user: User,
    index: Optional[IndexInfo] = None,
    callbacks: Optional[list[DatasourceProcessorCallback]] = None,
    request_uuid: Optional[str] = None,
    guardrail_assignments: Optional[List[GuardrailAssignmentItem]] = None,
    cron_expression: Optional[str] = None,
    timezone: Optional[str] = None,
):
    # ... existing assignments ...
    self.cron_expression = cron_expression
    self.timezone = timezone
```

Update `_create_or_update_scheduler` (line ~331):

```python
def _create_or_update_scheduler(
    self,
    cron_expression: Optional[str] = None,
    timezone: Optional[str] = None,
):
    cron_expr = cron_expression if cron_expression is not None else self.cron_expression
    tz = timezone if timezone is not None else self.timezone

    if cron_expr is None:
        return
    if not self.index or not self.index.id:
        return

    from codemie.service.settings.scheduler_settings_service import SchedulerSettingsService

    try:
        result = SchedulerSettingsService.handle_schedule(
            user_id=self.user.id,
            project_name=self.index.project_name,
            resource_id=self.index.id,
            resource_name=self.index.repo_name,
            cron_expression=cron_expr,
            timezone=tz,
        )
        if result:
            logger.info(f"Scheduler created/updated for datasource {self.index.id}")
        elif cron_expr is not None:
            logger.info(f"Scheduler deleted for datasource {self.index.id}")
    except Exception as e:
        logger.error(f"Failed to update scheduler for datasource {self.index.id}: {e}", exc_info=True)
```

- [ ] **Step 2: Update `_update_datasource_scheduler` in `routers/index.py`**

Find `_update_datasource_scheduler` (~line 2323) and add `timezone: Optional[str] = None`:

```python
def _update_datasource_scheduler(
    user_id: str,
    index_info: IndexInfo,
    cron_expression: str,
    timezone: Optional[str] = None,
) -> None:
    from codemie.service.settings.scheduler_settings_service import SchedulerSettingsService

    SchedulerSettingsService.handle_schedule(
        user_id=user_id,
        project_name=index_info.project_name,
        resource_id=index_info.id,
        resource_name=index_info.repo_name,
        cron_expression=cron_expression,
        timezone=timezone,
    )
```

- [ ] **Step 3: Update all `_update_datasource_scheduler` call sites in `routers/index.py`**

There are 10 call sites (lines ~699, 844, 942, 1424, 1491, 1562, 1642, 1722, 1832, 2007). Each currently reads:

```python
_update_datasource_scheduler(user.id, index_info, request.cron_expression)
```

Change each to:

```python
_update_datasource_scheduler(user.id, index_info, request.cron_expression, timezone=request.timezone)
```

Also update the `processor._create_or_update_scheduler(...)` direct calls (~lines 529, 602, 715, 857, 954, 1039, 1080, 1121, 1162, 1238, 1320, 1415) to pass timezone:

```python
processor._create_or_update_scheduler(
    request.cron_expression if cron_expression_provided else None,
    timezone=request.timezone if cron_expression_provided else None,
)
```

- [ ] **Step 4: Run existing router tests to verify no regression**

```
poetry run pytest tests/codemie/rest_api/routers/ -v -x
```

Expected: all pre-existing tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/codemie/datasource/base_datasource_processor.py \
    src/codemie/rest_api/routers/index.py
git commit -m "EPMCDME-11895: Thread timezone through datasource processor and index router call sites"
```

---

### Task 7: Trigger engine

**Files:**
- Modify: `src/codemie/triggers/bindings/cron.py`
- Modify: `tests/codemie/triggers/bindings/test_cron.py`

**Interfaces:**
- Consumes: `CredentialValues(key="timezone")` stored by Task 3; `config.TIMEZONE` as fallback
- Produces: `CronTrigger` receives `timezone=` argument on every job construction

- [ ] **Step 1: Write the failing tests**

Add to `tests/codemie/triggers/bindings/test_cron.py` (follow the pattern from `test_cron_prompt_extension.py`):

```python
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime
from codemie.triggers.bindings.cron import Cron
from codemie.rest_api.models.settings import Settings, CredentialValues


@pytest.fixture
def cron_instance():
    cron = Cron()
    cron.scheduler = MagicMock()
    cron.jobs = {}
    return cron


def _make_setting(timezone=None, resource_type="assistant"):
    setting = MagicMock(spec=Settings)
    setting.id = "test_setting_tz"
    setting.user_id = "test_user"
    setting.update_date = datetime.now()
    creds = [
        CredentialValues(key="schedule", value="0 9 * * 1-5"),
        CredentialValues(key="resource_type", value=resource_type),
        CredentialValues(key="resource_id", value="res_123"),
        CredentialValues(key="is_enabled", value=True),
    ]
    if timezone is not None:
        creds.append(CredentialValues(key="timezone", value=timezone))
    setting.credential_values = creds
    return setting


class TestCronTimezone:
    @patch('codemie.triggers.bindings.cron.validate_assistant')
    def test_valid_setting_extracts_timezone(self, mock_validate_assistant, cron_instance):
        mock_validate_assistant.return_value = MagicMock(name="Assistant")
        result = cron_instance._Cron__valid_setting(_make_setting(timezone="Europe/Warsaw"))
        assert result["timezone"] == "Europe/Warsaw"

    @patch('codemie.triggers.bindings.cron.validate_assistant')
    def test_valid_setting_timezone_none_when_absent(self, mock_validate_assistant, cron_instance):
        mock_validate_assistant.return_value = MagicMock(name="Assistant")
        result = cron_instance._Cron__valid_setting(_make_setting(timezone=None))
        assert result["timezone"] is None

    @patch('codemie.triggers.bindings.cron.CronTrigger')
    def test_create_cron_trigger_passes_timezone(self, mock_trigger):
        Cron._Cron__create_cron_trigger("0 9 * * 1-5", timezone="America/New_York")
        call_kwargs = mock_trigger.call_args.kwargs
        assert call_kwargs["timezone"] == "America/New_York"

    @patch('codemie.triggers.bindings.cron.CronTrigger')
    @patch('codemie.triggers.bindings.cron.config')
    def test_create_cron_trigger_falls_back_to_config_timezone(self, mock_config, mock_trigger):
        mock_config.TIMEZONE = "UTC"
        Cron._Cron__create_cron_trigger("0 9 * * 1-5", timezone=None)
        call_kwargs = mock_trigger.call_args.kwargs
        assert call_kwargs["timezone"] == "UTC"
```

- [ ] **Step 2: Run to verify FAIL**

```
poetry run pytest tests/codemie/triggers/bindings/test_cron.py::TestCronTimezone -v
```

Expected: `AssertionError` — `timezone` key not in result / wrong CronTrigger args.

- [ ] **Step 3: Update `__valid_setting` to read and return `timezone`**

In `src/codemie/triggers/bindings/cron.py`, in `__valid_setting` (~line 333), add the timezone read after the `prompt` line:

```python
def __valid_setting(self, setting):
    # ... existing logic unchanged until return ...
    timezone = self.__get_cred_value(setting, "timezone")  # None if absent

    return {
        "schedule": schedule,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "is_enabled": is_enabled,
        "prompt": prompt,
        "timezone": timezone,          # ← add this
        **resource_details,
    }
```

- [ ] **Step 4: Update `__actualize_jobs` to pass `timezone`**

In `__actualize_jobs` (~line 253), add `timezone=valid_setting.get("timezone")` to the `__actualize_cron_job` call:

```python
self.__actualize_cron_job(
    cron_expression=valid_setting.get("schedule"),
    resource_id=valid_setting.get("resource_id"),
    is_enabled=valid_setting.get("is_enabled"),
    resource_type=valid_setting.get("resource_type"),
    job_id=setting.id,
    user_id=setting.user_id,
    resource_name=valid_setting.get("resource_name"),
    project_name=valid_setting.get("project_name"),
    index_type=valid_setting.get("index_type"),
    jql=valid_setting.get("jql"),
    prompt=valid_setting.get("prompt"),
    timezone=valid_setting.get("timezone"),   # ← add this
)
```

- [ ] **Step 5: Update `__actualize_cron_job` signature to accept `timezone`**

Add `timezone: Optional[str] = None` parameter (~line 368) and pass it to `__create_cron_trigger`:

```python
def __actualize_cron_job(
    self,
    cron_expression,
    resource_id,
    resource_type,
    job_id,
    is_enabled,
    user_id,
    project_name=None,
    resource_name=None,
    index_type=None,
    jql=None,
    prompt=None,
    timezone=None,          # ← add this
):
    if not is_enabled:
        self.__remove_disabled_job(job_id)
        return

    cron_trigger = self.__create_cron_trigger(cron_expression, timezone=timezone)
    # ... rest unchanged ...
```

- [ ] **Step 6: Update `__create_cron_trigger` to apply timezone**

```python
@staticmethod
def __create_cron_trigger(cron_expression, timezone=None):
    from codemie.configs.config import config
    minute, hour, day_of_month, month, day_of_week = cron_expression.split()
    day_of_week = Cron.__normalize_day_of_week(day_of_week)
    return CronTrigger(
        minute=minute,
        hour=hour,
        day=day_of_month,
        month=month,
        day_of_week=day_of_week,
        timezone=timezone or config.TIMEZONE,
    )
```

- [ ] **Step 7: Run to verify PASS**

```
poetry run pytest tests/codemie/triggers/bindings/test_cron.py -v
```

Expected: all tests PASS, including the 4 new `TestCronTimezone` tests.

- [ ] **Step 8: Run full test suite for affected areas**

```
poetry run pytest tests/codemie/triggers/ tests/codemie/service/settings/ tests/codemie/rest_api/ tests/codemie/service/index/ -v
```

Expected: all PASS.

- [ ] **Step 9: Commit**

```bash
git add src/codemie/triggers/bindings/cron.py \
    tests/codemie/triggers/bindings/test_cron.py
git commit -m "EPMCDME-11895: Apply timezone in trigger engine CronTrigger construction"
```

---

## Self-Review

**Spec coverage:**

| Spec requirement | Task |
|---|---|
| Add `timezone: Optional[str] = None` to request models | Task 5 |
| IANA validation with `zoneinfo.ZoneInfo` | Task 1 |
| Validation in user-settings path | Task 2 |
| Validation in index router path (mixin) | Task 5 |
| Store timezone as `CredentialValues(key="timezone")` | Task 3 |
| `_update_schedule_values` handles all three cases | Task 3 |
| `get_scheduler_settings_for_datasources` returns `Dict[str, dict]` | Task 3 |
| `enrich_index_with_schedule` returns timezone | Task 4 |
| `BaseDatasourceProcessor` threads timezone | Task 6 |
| `_update_datasource_scheduler` threads timezone | Task 6 |
| All index router call sites pass timezone | Task 6 |
| Trigger engine reads and applies timezone | Task 7 |
| Fallback to `config.TIMEZONE` when absent | Task 7 |
| Backward compat: missing key → UTC | Task 7 |
| `tzdata` Python package dependency | Task 1 |
| `Scheduler` doc model updated | Task 5 |

**No placeholders found.** All steps contain complete code.

**Type consistency check:**
- `validate_timezone_string(timezone: Optional[str]) -> None` — defined Task 1, used Tasks 2 and 5 ✓
- `handle_schedule(..., timezone: Optional[str] = None)` — defined Task 3, called Task 6 ✓
- `get_scheduler_settings_for_datasources` → `Dict[str, dict]` — defined Task 3, consumed Task 4 ✓
- `enrich_index_with_schedule` returns `{"cron_expression": ..., "timezone": ...}` — Task 4 ✓
- `__create_cron_trigger(cron_expression, timezone=None)` — Task 7, called by `__actualize_cron_job` Task 7 ✓
