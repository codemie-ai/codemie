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

"""Tests for SkillMonitoringService."""

from unittest.mock import patch

import pytest

from codemie.rest_api.models.skill import Skill, SkillVisibility
from codemie.rest_api.security.user import User
from codemie.service.monitoring.skill_monitoring_service import SkillMonitoringService
from codemie.service.monitoring.metrics_constants import (
    SKILL_MANAGEMENT_METRIC,
    SKILL_ATTACHED_METRIC,
    SKILL_TOOL_INVOKED_METRIC,
    SKILL_EXPORTED_METRIC,
    MetricsAttributes,
)
from codemie.core.models import CreatedByUser


@pytest.fixture
def mock_skill():
    """Create a mock skill for testing."""
    return Skill(
        id="test-skill-id",
        name="test-skill",
        description="Test skill description",
        content="Test skill content",
        project="test-project",
        visibility=SkillVisibility.PROJECT,
        categories=["testing", "example"],
        created_by=CreatedByUser(
            id="user-123",
            name="Test User",
            username="test@example.com",
        ),
    )


@pytest.fixture
def mock_user():
    """Create a mock user for testing."""
    return User(
        id="user-123",
        name="Test User",
        username="test@example.com",
        applications=["test-project"],
    )


@patch.object(SkillMonitoringService, "send_count_metric")
def test_send_skill_management_metric_success(mock_send_count_metric, mock_skill, mock_user):
    """Test sending successful skill management metrics."""
    metric_name = "create"
    additional_attributes = {"extra": "attribute"}

    expected_attributes = {
        MetricsAttributes.SKILL_ID: mock_skill.id,
        MetricsAttributes.SKILL_NAME: mock_skill.name,
        MetricsAttributes.SKILL_VISIBILITY: mock_skill.visibility.value,
        MetricsAttributes.SKILL_CATEGORIES: "testing,example",
        MetricsAttributes.PROJECT: mock_skill.project,
        MetricsAttributes.USER_ID: mock_user.id,
        MetricsAttributes.USER_NAME: mock_user.name,
        MetricsAttributes.USER_EMAIL: mock_user.username,
        **additional_attributes,
    }

    SkillMonitoringService.send_skill_management_metric(
        metric_name=metric_name,
        skill=mock_skill,
        success=True,
        user=mock_user,
        additional_attributes=additional_attributes,
    )

    mock_send_count_metric.assert_called_once_with(
        name=f"{SKILL_MANAGEMENT_METRIC}_{metric_name}",
        attributes=expected_attributes,
    )


@patch.object(SkillMonitoringService, "send_count_metric")
def test_send_skill_management_metric_error(mock_send_count_metric, mock_skill, mock_user):
    """Test sending failed skill management metrics."""
    metric_name = "update"
    additional_attributes = {"error": "Test error"}

    expected_attributes = {
        MetricsAttributes.SKILL_ID: mock_skill.id,
        MetricsAttributes.SKILL_NAME: mock_skill.name,
        MetricsAttributes.SKILL_VISIBILITY: mock_skill.visibility.value,
        MetricsAttributes.SKILL_CATEGORIES: "testing,example",
        MetricsAttributes.PROJECT: mock_skill.project,
        MetricsAttributes.USER_ID: mock_user.id,
        MetricsAttributes.USER_NAME: mock_user.name,
        MetricsAttributes.USER_EMAIL: mock_user.username,
        **additional_attributes,
    }

    SkillMonitoringService.send_skill_management_metric(
        metric_name=metric_name,
        skill=mock_skill,
        success=False,
        user=mock_user,
        additional_attributes=additional_attributes,
    )

    mock_send_count_metric.assert_called_once_with(
        name=f"{SKILL_MANAGEMENT_METRIC}_{metric_name}_error",
        attributes=expected_attributes,
    )


@patch.object(SkillMonitoringService, "send_count_metric")
def test_send_skill_attached_metric_success(mock_send_count_metric, mock_skill, mock_user):
    """Test sending successful skill attached metrics."""
    assistant_id = "assistant-123"
    assistant_name = "Test Assistant"

    expected_attributes = {
        MetricsAttributes.SKILL_ID: mock_skill.id,
        MetricsAttributes.SKILL_NAME: mock_skill.name,
        MetricsAttributes.ASSISTANT_ID: assistant_id,
        MetricsAttributes.ASSISTANT_NAME: assistant_name,
        MetricsAttributes.PROJECT: mock_skill.project,
        MetricsAttributes.USER_ID: mock_user.id,
        MetricsAttributes.USER_NAME: mock_user.name,
        MetricsAttributes.OPERATION: "attach",
    }

    SkillMonitoringService.send_skill_attached_metric(
        skill=mock_skill,
        assistant_id=assistant_id,
        assistant_name=assistant_name,
        user=mock_user,
        success=True,
        operation="attach",
    )

    mock_send_count_metric.assert_called_once_with(
        name=SKILL_ATTACHED_METRIC,
        attributes=expected_attributes,
    )


@patch.object(SkillMonitoringService, "send_count_metric")
def test_send_skill_attached_metric_detach(mock_send_count_metric, mock_skill, mock_user):
    """Test sending detach skill metrics."""
    assistant_id = "assistant-123"
    assistant_name = "Test Assistant"

    expected_attributes = {
        MetricsAttributes.SKILL_ID: mock_skill.id,
        MetricsAttributes.SKILL_NAME: mock_skill.name,
        MetricsAttributes.ASSISTANT_ID: assistant_id,
        MetricsAttributes.ASSISTANT_NAME: assistant_name,
        MetricsAttributes.PROJECT: mock_skill.project,
        MetricsAttributes.USER_ID: mock_user.id,
        MetricsAttributes.USER_NAME: mock_user.name,
        MetricsAttributes.OPERATION: "detach",
    }

    SkillMonitoringService.send_skill_attached_metric(
        skill=mock_skill,
        assistant_id=assistant_id,
        assistant_name=assistant_name,
        user=mock_user,
        success=True,
        operation="detach",
    )

    mock_send_count_metric.assert_called_once_with(
        name=SKILL_ATTACHED_METRIC,
        attributes=expected_attributes,
    )


@patch.object(SkillMonitoringService, "send_count_metric")
def test_send_skill_tool_invoked_metric_success(mock_send_count_metric):
    """Test sending successful skill tool invoked metrics."""
    skill_id = "test-skill-id"
    skill_name = "test-skill"
    assistant_id = "assistant-123"
    user_id = "user-123"
    user_name = "Test User"
    project = "test-project"

    expected_attributes = {
        MetricsAttributes.SKILL_ID: skill_id,
        MetricsAttributes.SKILL_NAME: skill_name,
        MetricsAttributes.ASSISTANT_ID: assistant_id,
        MetricsAttributes.PROJECT: project,
        MetricsAttributes.USER_ID: user_id,
        MetricsAttributes.USER_NAME: user_name,
    }

    SkillMonitoringService.send_skill_tool_invoked_metric(
        skill_id=skill_id,
        skill_name=skill_name,
        assistant_id=assistant_id,
        user_id=user_id,
        user_name=user_name,
        project=project,
        success=True,
    )

    mock_send_count_metric.assert_called_once_with(
        name=SKILL_TOOL_INVOKED_METRIC,
        attributes=expected_attributes,
    )


@patch.object(SkillMonitoringService, "send_count_metric")
def test_send_skill_tool_invoked_metric_error(mock_send_count_metric):
    """Test sending failed skill tool invoked metrics."""
    skill_id = "test-skill-id"
    skill_name = "test-skill"
    assistant_id = "assistant-123"
    user_id = "user-123"
    user_name = "Test User"
    project = "test-project"
    additional_attributes = {"error": "Skill not found"}

    expected_attributes = {
        MetricsAttributes.SKILL_ID: skill_id,
        MetricsAttributes.SKILL_NAME: skill_name,
        MetricsAttributes.ASSISTANT_ID: assistant_id,
        MetricsAttributes.PROJECT: project,
        MetricsAttributes.USER_ID: user_id,
        MetricsAttributes.USER_NAME: user_name,
        **additional_attributes,
    }

    SkillMonitoringService.send_skill_tool_invoked_metric(
        skill_id=skill_id,
        skill_name=skill_name,
        assistant_id=assistant_id,
        user_id=user_id,
        user_name=user_name,
        project=project,
        success=False,
        additional_attributes=additional_attributes,
    )

    mock_send_count_metric.assert_called_once_with(
        name=f"{SKILL_TOOL_INVOKED_METRIC}_error",
        attributes=expected_attributes,
    )


@patch.object(SkillMonitoringService, "send_count_metric")
def test_send_skill_exported_metric_success(mock_send_count_metric, mock_skill, mock_user):
    """Test sending successful skill exported metrics."""
    expected_attributes = {
        MetricsAttributes.SKILL_ID: mock_skill.id,
        MetricsAttributes.SKILL_NAME: mock_skill.name,
        MetricsAttributes.PROJECT: mock_skill.project,
        MetricsAttributes.USER_ID: mock_user.id,
        MetricsAttributes.USER_NAME: mock_user.name,
    }

    SkillMonitoringService.send_skill_exported_metric(
        skill=mock_skill,
        user=mock_user,
        success=True,
    )

    mock_send_count_metric.assert_called_once_with(
        name=SKILL_EXPORTED_METRIC,
        attributes=expected_attributes,
    )


@patch.object(SkillMonitoringService, "send_count_metric")
def test_send_skill_exported_metric_error(mock_send_count_metric, mock_skill, mock_user):
    """Test sending failed skill exported metrics."""
    additional_attributes = {"error": "Export failed"}

    expected_attributes = {
        MetricsAttributes.SKILL_ID: mock_skill.id,
        MetricsAttributes.SKILL_NAME: mock_skill.name,
        MetricsAttributes.PROJECT: mock_skill.project,
        MetricsAttributes.USER_ID: mock_user.id,
        MetricsAttributes.USER_NAME: mock_user.name,
        **additional_attributes,
    }

    SkillMonitoringService.send_skill_exported_metric(
        skill=mock_skill,
        user=mock_user,
        success=False,
        additional_attributes=additional_attributes,
    )

    mock_send_count_metric.assert_called_once_with(
        name=f"{SKILL_EXPORTED_METRIC}_error",
        attributes=expected_attributes,
    )


@patch.object(SkillMonitoringService, "send_count_metric")
def test_send_skill_management_metric_empty_categories(mock_send_count_metric, mock_user):
    """Test handling of skills with empty categories."""
    skill_no_categories = Skill(
        id="test-skill-id",
        name="test-skill",
        description="Test skill description",
        content="Test skill content",
        project="test-project",
        visibility=SkillVisibility.PRIVATE,
        categories=[],
        created_by=CreatedByUser(
            id="user-123",
            name="Test User",
            username="test@example.com",
        ),
    )

    expected_attributes = {
        MetricsAttributes.SKILL_ID: skill_no_categories.id,
        MetricsAttributes.SKILL_NAME: skill_no_categories.name,
        MetricsAttributes.SKILL_VISIBILITY: skill_no_categories.visibility.value,
        MetricsAttributes.SKILL_CATEGORIES: "",
        MetricsAttributes.PROJECT: skill_no_categories.project,
        MetricsAttributes.USER_ID: mock_user.id,
        MetricsAttributes.USER_NAME: mock_user.name,
        MetricsAttributes.USER_EMAIL: mock_user.username,
    }

    SkillMonitoringService.send_skill_management_metric(
        metric_name="create",
        skill=skill_no_categories,
        success=True,
        user=mock_user,
    )

    mock_send_count_metric.assert_called_once_with(
        name=f"{SKILL_MANAGEMENT_METRIC}_create",
        attributes=expected_attributes,
    )
