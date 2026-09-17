# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Tests for SchedulerSettingsService class methods."""

import pytest
from unittest.mock import Mock, patch

from codemie.rest_api.models.settings import Settings
from codemie.service.settings.scheduler_settings_service import (
    SchedulerSettingsService,
    RESOURCE_TYPE_DATASOURCE,
    DATASOURCE_SCHEDULE_ALIAS_PREFIX,
)
from codemie_tools.base.models import CredentialTypes


# ===================== handle_schedule tests =====================


@pytest.mark.parametrize(
    "cron_expression,should_create",
    [("0 9 * * *", True), (None, False), ("", False)],
)
@patch.object(SchedulerSettingsService, "create_or_update_schedule")
@patch.object(SchedulerSettingsService, "delete_schedule")
def test_handle_schedule_routes_correctly(mock_delete, mock_create_update, cron_expression, should_create):
    """Test that handle_schedule routes to correct method based on cron_expression."""
    SchedulerSettingsService.handle_schedule(
        user_id="user123",
        project_name="test-project",
        resource_id="res123",
        resource_name="test-resource",
        cron_expression=cron_expression,
    )

    if should_create:
        mock_create_update.assert_called_once()
        mock_delete.assert_not_called()
    else:
        mock_delete.assert_called_once()
        mock_create_update.assert_not_called()


# ===================== create_or_update_schedule tests =====================


@patch.object(SchedulerSettingsService, "_find_schedule_by_resource_id")
@patch.object(SchedulerSettingsService, "_create_new_schedule")
def test_create_or_update_schedule_creates_new(mock_create, mock_find):
    """Test creating a new schedule when none exists."""
    mock_find.return_value = None
    mock_new_schedule = Mock(spec=Settings)
    mock_new_schedule.save = Mock()
    mock_create.return_value = mock_new_schedule

    result = SchedulerSettingsService.create_or_update_schedule(
        user_id="user123",
        project_name="project",
        resource_type=RESOURCE_TYPE_DATASOURCE,
        resource_id="res123",
        resource_name="test-resource",
        cron_expression="0 9 * * *",
        is_enabled=True,
    )

    mock_new_schedule.save.assert_called_once()
    assert result == mock_new_schedule


@patch("codemie.service.settings.scheduler_settings_service.flag_modified")
@patch.object(SchedulerSettingsService, "_find_schedule_by_resource_id")
@patch.object(SchedulerSettingsService, "_update_schedule_values")
def test_create_or_update_schedule_updates_existing(mock_update_values, mock_find, mock_flag_modified):
    """Test updating an existing schedule."""
    mock_existing = Mock(spec=Settings)
    mock_existing.update = Mock()
    mock_find.return_value = mock_existing

    result = SchedulerSettingsService.create_or_update_schedule(
        user_id="user123",
        project_name="project",
        resource_type=RESOURCE_TYPE_DATASOURCE,
        resource_id="res123",
        resource_name="test-resource",
        cron_expression="0 9 * * *",
        is_enabled=True,
    )

    mock_update_values.assert_called_once()
    mock_existing.update.assert_called_once()
    assert result == mock_existing


# ===================== _find_schedule_by_resource_id tests =====================


@patch.object(Settings, "get_all_by_fields")
def test_find_schedule_by_resource_id_returns_matching(mock_get_all):
    """Test finding a schedule by resource_id with correct alias prefix."""
    mock_schedule = Mock(spec=Settings)
    mock_schedule.credential = Mock(return_value="res123")
    mock_schedule.alias = f"{DATASOURCE_SCHEDULE_ALIAS_PREFIX}test-resource"
    mock_get_all.return_value = [mock_schedule]

    result = SchedulerSettingsService._find_schedule_by_resource_id(
        user_id="user123", project_name="project", resource_id="res123"
    )

    assert result == mock_schedule


@patch.object(Settings, "get_all_by_fields")
def test_find_schedule_by_resource_id_ignores_wrong_prefix(mock_get_all):
    """Test that schedules without index router prefix are ignored."""
    mock_schedule = Mock(spec=Settings)
    mock_schedule.credential = Mock(return_value="res123")
    mock_schedule.alias = "OtherPrefix_test-resource"
    mock_get_all.return_value = [mock_schedule]

    result = SchedulerSettingsService._find_schedule_by_resource_id(
        user_id="user123", project_name="project", resource_id="res123"
    )

    assert result is None


# ===================== _create_new_schedule tests =====================


def test_create_new_schedule_structure():
    """Test that new schedule is created with correct structure."""
    result = SchedulerSettingsService._create_new_schedule(
        user_id="user123",
        project_name="test-project",
        resource_type=RESOURCE_TYPE_DATASOURCE,
        resource_id="res123",
        resource_name="test-resource",
        cron_expression="0 9 * * *",
        is_enabled=True,
    )

    assert isinstance(result, Settings)
    assert result.user_id == "user123"
    assert result.project_name == "test-project"
    assert result.alias == f"{DATASOURCE_SCHEDULE_ALIAS_PREFIX}test-resource"
    assert result.credential_type == CredentialTypes.SCHEDULER
    assert len(result.credential_values) == 4

    creds_dict = {cred.key: cred.value for cred in result.credential_values}
    assert creds_dict["schedule"] == "0 9 * * *"
    assert creds_dict["resource_id"] == "res123"
    assert creds_dict["is_enabled"] is True


# ===================== get_scheduler_settings_for_datasources tests =====================


@patch.object(Settings, "get_all_by_fields")
def test_get_scheduler_settings_returns_matching(mock_get_all):
    """Test getting scheduler settings for datasources."""

    def credential_mock(key):
        mapping = {"resource_id": "res1", "schedule": "0 9 * * *", "is_enabled": True, "timezone": None}
        return mapping.get(key)

    mock_schedule = Mock(spec=Settings)
    mock_schedule.credential = Mock(side_effect=credential_mock)
    mock_schedule.alias = f"{DATASOURCE_SCHEDULE_ALIAS_PREFIX}resource1"
    mock_get_all.return_value = [mock_schedule]

    result = SchedulerSettingsService.get_scheduler_settings_for_datasources(
        user_id="user123", datasource_ids=["res1", "res2"]
    )

    assert result == {"res1": {"cron_expression": "0 9 * * *", "timezone": "UTC"}}


@patch.object(Settings, "get_all_by_fields")
def test_get_scheduler_settings_ignores_disabled(mock_get_all):
    """Test that disabled schedules are excluded."""

    def credential_mock(key):
        mapping = {"resource_id": "res1", "schedule": "0 9 * * *", "is_enabled": False}
        return mapping.get(key)

    mock_schedule = Mock(spec=Settings)
    mock_schedule.credential = Mock(side_effect=credential_mock)
    mock_schedule.alias = f"{DATASOURCE_SCHEDULE_ALIAS_PREFIX}resource1"
    mock_get_all.return_value = [mock_schedule]

    result = SchedulerSettingsService.get_scheduler_settings_for_datasources(user_id="user123", datasource_ids=["res1"])

    assert result == {}


# ===================== delete_schedule tests =====================


@patch.object(Settings, "get_all_by_fields")
def test_delete_schedule_deletes_matching(mock_get_all):
    """Test that matching schedule is deleted successfully."""
    mock_schedule = Mock(spec=Settings)
    mock_schedule.credential = Mock(return_value="res123")
    mock_schedule.alias = f"{DATASOURCE_SCHEDULE_ALIAS_PREFIX}resource"
    mock_schedule.delete = Mock()
    mock_get_all.return_value = [mock_schedule]

    result = SchedulerSettingsService.delete_schedule(resource_id="res123", user_id="user123")

    mock_schedule.delete.assert_called_once()
    assert result is True


@patch.object(Settings, "get_all_by_fields")
def test_delete_schedule_returns_false_when_not_found(mock_get_all):
    """Test that False is returned when schedule not found."""
    mock_get_all.return_value = []

    result = SchedulerSettingsService.delete_schedule(resource_id="res123", user_id="user123")

    assert result is False


# ===================== timezone threading tests =====================


def test_create_new_schedule_stores_timezone_credential():
    result = SchedulerSettingsService._create_new_schedule(
        user_id="u1",
        project_name="p",
        resource_type=RESOURCE_TYPE_DATASOURCE,
        resource_id="r1",
        resource_name="name",
        cron_expression="0 9 * * *",
        is_enabled=True,
        timezone="Europe/Warsaw",
    )
    creds = {c.key: c.value for c in result.credential_values}
    assert creds["timezone"] == "Europe/Warsaw"
    assert len(result.credential_values) == 5


def test_create_new_schedule_without_timezone_has_4_credentials():
    result = SchedulerSettingsService._create_new_schedule(
        user_id="u1",
        project_name="p",
        resource_type=RESOURCE_TYPE_DATASOURCE,
        resource_id="r1",
        resource_name="name",
        cron_expression="0 9 * * *",
        is_enabled=True,
    )
    assert len(result.credential_values) == 4
    keys = {c.key for c in result.credential_values}
    assert "timezone" not in keys


def test_update_schedule_values_updates_existing_timezone():
    creds = [
        Mock(key="schedule", value="0 9 * * *"),
        Mock(key="is_enabled", value=True),
        Mock(key="timezone", value="UTC"),
    ]
    schedule = Mock(spec=Settings)
    schedule.credential_values = creds
    schedule.alias = f"{DATASOURCE_SCHEDULE_ALIAS_PREFIX}name"

    SchedulerSettingsService._update_schedule_values(schedule, "0 10 * * *", True, "name", timezone="Europe/Warsaw")

    tz_cred = next(c for c in creds if c.key == "timezone")
    assert tz_cred.value == "Europe/Warsaw"


def test_update_schedule_values_adds_timezone_when_absent():
    creds = [
        Mock(key="schedule", value="0 9 * * *"),
        Mock(key="is_enabled", value=True),
    ]
    schedule = Mock(spec=Settings)
    schedule.credential_values = creds
    schedule.alias = f"{DATASOURCE_SCHEDULE_ALIAS_PREFIX}name"

    SchedulerSettingsService._update_schedule_values(schedule, "0 9 * * *", True, "name", timezone="America/New_York")

    keys = [c.key for c in schedule.credential_values]
    assert "timezone" in keys
    tz_val = next(c.value for c in schedule.credential_values if c.key == "timezone")
    assert tz_val == "America/New_York"


def test_update_schedule_values_none_timezone_leaves_absent():
    creds = [
        Mock(key="schedule", value="0 9 * * *"),
        Mock(key="is_enabled", value=True),
    ]
    schedule = Mock(spec=Settings)
    schedule.credential_values = creds
    schedule.alias = f"{DATASOURCE_SCHEDULE_ALIAS_PREFIX}name"

    SchedulerSettingsService._update_schedule_values(schedule, "0 9 * * *", True, "name", timezone=None)

    keys = [c.key for c in schedule.credential_values]
    assert "timezone" not in keys


@patch.object(Settings, "get_all_by_fields")
def test_get_scheduler_settings_returns_dict_with_timezone(mock_get_all):
    def credential_mock(key):
        mapping = {"resource_id": "res1", "schedule": "0 9 * * *", "is_enabled": True, "timezone": "Asia/Tokyo"}
        return mapping.get(key)

    mock_schedule = Mock(spec=Settings)
    mock_schedule.credential = Mock(side_effect=credential_mock)
    mock_schedule.alias = f"{DATASOURCE_SCHEDULE_ALIAS_PREFIX}resource1"
    mock_get_all.return_value = [mock_schedule]

    result = SchedulerSettingsService.get_scheduler_settings_for_datasources(user_id="user123", datasource_ids=["res1"])

    assert result == {"res1": {"cron_expression": "0 9 * * *", "timezone": "Asia/Tokyo"}}


# ===================== _build_list_filters user-scoping tests =====================


def _make_user(is_admin=False, project_names=None):
    """Helper to create a mock User for scoping tests."""
    user = Mock()
    user.is_admin_or_maintainer = is_admin
    user.project_names = project_names or []
    return user


def test_build_list_filters_non_admin_adds_project_scope():
    """Non-admin user must see only their own projects."""
    user = _make_user(is_admin=False, project_names=["proj-a", "proj-b"])
    conditions, params = SchedulerSettingsService._build_list_filters(
        project_id=None,
        resource_type=None,
        resource_id=None,
        search=None,
        status=None,
        last_run_status=None,
        owner_type=None,
        user=user,
    )
    assert any("project_names" in c for c in conditions), "Expected project_names scope condition"
    assert params.get("project_names") == ["proj-a", "proj-b"]


def test_build_list_filters_admin_skips_project_scope():
    """Admin/maintainer must see all projects — no project_names condition added."""
    user = _make_user(is_admin=True, project_names=["proj-a"])
    conditions, params = SchedulerSettingsService._build_list_filters(
        project_id=None,
        resource_type=None,
        resource_id=None,
        search=None,
        status=None,
        last_run_status=None,
        owner_type=None,
        user=user,
    )
    assert not any("project_names" in c for c in conditions), "Admins must not be scoped to project_names"


def test_build_list_filters_explicit_project_id_adds_alongside_scope():
    """When project_id is given explicitly it is ANDed with the user's project_names scope."""
    user = _make_user(is_admin=False, project_names=["proj-a"])
    conditions, params = SchedulerSettingsService._build_list_filters(
        project_id="proj-a",
        resource_type=None,
        resource_id=None,
        search=None,
        status=None,
        last_run_status=None,
        owner_type=None,
        user=user,
    )
    assert any(":project_id" in c for c in conditions)
    assert params.get("project_id") == "proj-a"
    assert any("project_names" in c for c in conditions), "project_names scope must still be applied"


# ===================== get_filter_options user-scoping tests =====================


@patch.object(SchedulerSettingsService, "_fetch_all_scheduler_settings")
@patch.object(SchedulerSettingsService, "_build_resource_name_map")
def test_get_filter_options_scopes_to_user_projects(mock_name_map, mock_fetch):
    """get_filter_options must pass user to _fetch_all_scheduler_settings."""
    # Clear any cached value
    SchedulerSettingsService._filter_options_cache = {}

    user = _make_user(is_admin=False, project_names=["proj-a"])
    mock_fetch.return_value = []
    mock_name_map.return_value = {}

    SchedulerSettingsService.get_filter_options(user=user)

    mock_fetch.assert_called_once_with(user=user)


def test_build_list_filters_empty_project_names_returns_no_rows_condition():
    """Non-admin with empty project_names must produce a 'never match' condition, not a type error."""
    user = _make_user(is_admin=False, project_names=[])
    conditions, params = SchedulerSettingsService._build_list_filters(
        project_id=None,
        resource_type=None,
        resource_id=None,
        search=None,
        status=None,
        last_run_status=None,
        owner_type=None,
        user=user,
    )
    assert any("1=0" in c for c in conditions), "Empty project list must yield a no-match condition"
    assert "project_names" not in params


# ===================== ownerType=User user-scoping tests (EPMCDME-10682 fix) =====================


def _make_user_with_id(user_id="user-42", is_admin=False, project_names=None):
    user = _make_user(is_admin=is_admin, project_names=project_names or ["proj-a"])
    user.id = user_id
    return user


def test_build_list_filters_owner_type_user_adds_caller_id_for_non_admin():
    """ownerType=User must scope results to the caller's user_id for regular users."""
    user = _make_user_with_id(user_id="user-42", is_admin=False)
    conditions, params = SchedulerSettingsService._build_list_filters(
        project_id=None,
        resource_type=None,
        resource_id=None,
        search=None,
        status=None,
        last_run_status=None,
        owner_type="User",
        user=user,
    )
    assert any("s.setting_type" in c for c in conditions)
    assert params.get("owner_type") == "USER"
    assert any("s.user_id" in c for c in conditions), "caller_id filter must be present for ownerType=User"
    assert params.get("caller_id") == "user-42"


def test_build_list_filters_owner_type_user_adds_caller_id_for_admin():
    """ownerType=User must scope results to the caller's user_id even for admins — no bypass."""
    user = _make_user_with_id(user_id="admin-99", is_admin=True)
    conditions, params = SchedulerSettingsService._build_list_filters(
        project_id=None,
        resource_type=None,
        resource_id=None,
        search=None,
        status=None,
        last_run_status=None,
        owner_type="User",
        user=user,
    )
    assert params.get("owner_type") == "USER"
    assert any("s.user_id" in c for c in conditions), "Admin must also be scoped to own user_id when ownerType=User"
    assert params.get("caller_id") == "admin-99"


def test_build_list_filters_owner_type_project_does_not_add_caller_id():
    """ownerType=Project must NOT add a user_id constraint."""
    user = _make_user_with_id(user_id="user-42", is_admin=False)
    conditions, params = SchedulerSettingsService._build_list_filters(
        project_id=None,
        resource_type=None,
        resource_id=None,
        search=None,
        status=None,
        last_run_status=None,
        owner_type="Project",
        user=user,
    )
    assert params.get("owner_type") == "PROJECT"
    assert not any("s.user_id" in c for c in conditions), "user_id must not be added for ownerType=Project"
    assert "caller_id" not in params


def test_build_list_filters_no_owner_type_does_not_add_caller_id():
    """When ownerType is absent, no user_id constraint should be added."""
    user = _make_user_with_id(user_id="user-42", is_admin=False)
    conditions, params = SchedulerSettingsService._build_list_filters(
        project_id=None,
        resource_type=None,
        resource_id=None,
        search=None,
        status=None,
        last_run_status=None,
        owner_type=None,
        user=user,
    )
    assert not any("s.user_id" in c for c in conditions)
    assert "caller_id" not in params
