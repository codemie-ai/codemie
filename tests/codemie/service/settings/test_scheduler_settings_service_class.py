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
        mapping = {"resource_id": "res1", "schedule": "0 9 * * *", "is_enabled": True}
        return mapping.get(key)

    mock_schedule = Mock(spec=Settings)
    mock_schedule.credential = Mock(side_effect=credential_mock)
    mock_schedule.alias = f"{DATASOURCE_SCHEDULE_ALIAS_PREFIX}resource1"
    mock_get_all.return_value = [mock_schedule]

    result = SchedulerSettingsService.get_scheduler_settings_for_datasources(
        user_id="user123", datasource_ids=["res1", "res2"]
    )

    assert result == {"res1": "0 9 * * *"}


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
