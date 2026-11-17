# Copyright 2026 EPAM Systems, Inc. ("EPAM")
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

from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException, status

from codemie.service.workflow_service import WorkflowService
from codemie.triggers.bindings.webhook import WebhookService

_WEBHOOK = "codemie.triggers.bindings.webhook"


@pytest.fixture
def mock_background_tasks():
    return MagicMock()


@pytest.fixture
def setting_proj_a():
    setting = MagicMock()
    setting.project_name = "project-a"
    setting.user_id = "test_user"
    setting.alias = "hook-1"
    setting.credential.return_value = "hook-id"
    return setting


@pytest.fixture
def monitoring():
    with patch(f"{_WEBHOOK}.WebhookMonitoringService") as mock_monitoring:
        yield mock_monitoring


@pytest.fixture
def log():
    with patch(f"{_WEBHOOK}.logger") as mock_logger:
        yield mock_logger


def _assert_audited(log, monitoring, resource_type, resource_id):
    log.error.assert_called_once()
    metric = monitoring.send_webhook_invocation_metric.call_args.kwargs
    assert metric["success"] is False
    assert metric["resource_type"] == resource_type
    assert metric["resource_id"] == resource_id
    assert metric["additional_attributes"] == {"error_cause": "project_mismatch"}


def test_handle_datasource_rejects_when_datasource_project_differs_from_webhook_project(
    mock_background_tasks, setting_proj_a, monitoring, log
):
    datasource = MagicMock()
    datasource.project_name = "project-b"
    datasource.created_by = MagicMock()

    with patch(f"{_WEBHOOK}.validate_datasource", return_value=datasource):
        with pytest.raises(HTTPException) as exc_info:
            WebhookService.handle_datasource("ds-1", mock_background_tasks, setting_proj_a)

    assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
    _assert_audited(log, monitoring, "datasource", "ds-1")


def test_handle_assistant_rejects_when_assistant_project_differs_from_webhook_project(
    mock_background_tasks, setting_proj_a, monitoring, log
):
    assistant = MagicMock()
    assistant.project = "project-b"

    with patch(f"{_WEBHOOK}.validate_assistant", return_value=assistant):
        with pytest.raises(HTTPException) as exc_info:
            WebhookService.handle_assistant("asst-1", b'{}', mock_background_tasks, setting_proj_a)

    assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
    _assert_audited(log, monitoring, "assistant", "asst-1")


def test_handle_workflow_rejects_when_workflow_project_differs_from_webhook_project(
    mock_background_tasks, setting_proj_a, monitoring, log
):
    workflow = MagicMock()
    workflow.project = "project-b"

    with patch.object(WorkflowService, 'get_workflow', return_value=workflow):
        with pytest.raises(HTTPException) as exc_info:
            WebhookService.handle_workflow("wf-1", b'{}', mock_background_tasks, setting_proj_a)

    assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
    _assert_audited(log, monitoring, "workflow", "wf-1")


def test_handle_assistant_passes_guard_when_projects_match(mock_background_tasks, setting_proj_a, monitoring, log):
    assistant = MagicMock()
    assistant.project = "project-a"

    with patch(f"{_WEBHOOK}.validate_assistant", return_value=assistant):
        WebhookService.handle_assistant("asst-1", b'{}', mock_background_tasks, setting_proj_a)

    mock_background_tasks.add_task.assert_called_once()
    log.error.assert_not_called()
    monitoring.send_webhook_invocation_metric.assert_not_called()


def test_handle_workflow_passes_guard_when_projects_match(mock_background_tasks, setting_proj_a, monitoring, log):
    workflow = MagicMock()
    workflow.project = "project-a"

    with patch.object(WorkflowService, 'get_workflow', return_value=workflow):
        WebhookService.handle_workflow("wf-1", b'{}', mock_background_tasks, setting_proj_a)

    mock_background_tasks.add_task.assert_called_once()
    log.error.assert_not_called()
    monitoring.send_webhook_invocation_metric.assert_not_called()


def test_handle_datasource_passes_guard_when_projects_match(mock_background_tasks, setting_proj_a, monitoring, log):
    datasource = MagicMock()
    datasource.project_name = "project-a"
    datasource.created_by = MagicMock()

    with (
        patch(f"{_WEBHOOK}.validate_datasource", return_value=datasource),
        patch(f"{_WEBHOOK}.resolve_trigger_user", return_value=None) as resolve_user,
    ):
        WebhookService.handle_datasource("ds-1", mock_background_tasks, setting_proj_a)

    resolve_user.assert_called_once_with("test_user")
    log.error.assert_not_called()
    monitoring.send_webhook_invocation_metric.assert_not_called()
