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

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, Request, status

from codemie.core.models import BaseResponse
from codemie.service.settings.settings import SettingsService
from codemie.service.workflow_service import WorkflowService
from codemie.triggers.bindings.webhook import ResourceType, WebhookService


@pytest.fixture
def mock_request():
    request = MagicMock(Request)
    request.headers = {}
    request.body = AsyncMock(return_value=b'{}')
    return request


@pytest.fixture
def mock_background_tasks():
    return MagicMock()


@pytest.mark.asyncio
async def test_invoke_webhook_logic_webhook_not_found(mock_request, mock_background_tasks):
    mock_request.headers = {}
    webhook_id = "test_webhook"
    with patch.object(SettingsService, 'retrieve_setting', return_value=None):
        with pytest.raises(HTTPException) as exc_info:
            await WebhookService.invoke_webhook_logic(mock_request, webhook_id, mock_background_tasks)
        assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
        assert exc_info.value.detail == WebhookService.WEBHOOK_NOT_FOUND_OR_NOT_ENABLED.format(webhook_id)


@pytest.mark.asyncio
async def test_invoke_webhook_logic_invalid_security_header(mock_request, mock_background_tasks):
    webhook_id = "test_webhook"
    setting = MagicMock()
    setting.credential.side_effect = lambda key: {
        "webhook_id": webhook_id,
        WebhookService.SECURE_HEADER_NAME: "X-Secure-Header",
        WebhookService.SECURE_HEADER_VALUE: "secure_value",
        WebhookService.GITHUB_WEBHOOK_SECRET: None,
        WebhookService.RESOURCE_TYPE: ResourceType.ASSISTANT.value,
        WebhookService.RESOURCE_ID: "assistant_id",
        WebhookService.IS_ENABLED: True,
    }.get(key)
    setting.project_name = "test_project"
    setting.user_id = "test_user"
    setting.alias = "test_alias"

    mock_request.headers = {"X-Secure-Header": "invalid_value"}

    with patch.object(SettingsService, 'retrieve_setting', return_value=setting):
        with pytest.raises(HTTPException) as exc_info:
            await WebhookService.invoke_webhook_logic(mock_request, webhook_id, mock_background_tasks)
        assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
        assert exc_info.value.detail == WebhookService.INVALID_SECURITY_HEADER


@pytest.mark.asyncio
async def test_invoke_webhook_logic_assistant_not_found(mock_request, mock_background_tasks):
    webhook_id = "test_webhook"
    setting = MagicMock()
    setting.credential.side_effect = lambda key: {
        "webhook_id": webhook_id,
        WebhookService.SECURE_HEADER_NAME: None,
        WebhookService.SECURE_HEADER_VALUE: None,
        WebhookService.GITHUB_WEBHOOK_SECRET: None,
        WebhookService.RESOURCE_TYPE: ResourceType.ASSISTANT.value,
        WebhookService.RESOURCE_ID: "assistant_id",
        WebhookService.IS_ENABLED: True,
    }.get(key)
    setting.project_name = "test_project"
    setting.user_id = "test_user"
    setting.alias = "test_alias"

    with patch.object(SettingsService, 'retrieve_setting', return_value=setting):
        with patch('codemie.triggers.bindings.webhook.validate_assistant', return_value=None):
            with pytest.raises(HTTPException) as exc_info:
                await WebhookService.invoke_webhook_logic(mock_request, webhook_id, mock_background_tasks)
            assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND
            assert exc_info.value.detail == WebhookService.ASSISTANT_NOT_FOUND.format("assistant_id")


@pytest.mark.asyncio
async def test_invoke_webhook_logic_handle_workflow(mock_request, mock_background_tasks):
    webhook_id = "test_webhook"
    setting = MagicMock()
    setting.credential.side_effect = lambda key: {
        "webhook_id": webhook_id,
        WebhookService.SECURE_HEADER_NAME: None,
        WebhookService.SECURE_HEADER_VALUE: None,
        WebhookService.GITHUB_WEBHOOK_SECRET: None,
        WebhookService.RESOURCE_TYPE: ResourceType.WORKFLOW.value,
        WebhookService.RESOURCE_ID: "workflow_id",
        WebhookService.IS_ENABLED: True,
    }.get(key)
    setting.project_name = "test_project"
    setting.user_id = "test_user"
    setting.alias = "test_alias"

    workflow = MagicMock()
    workflow.created_by.user_id = "user_id"
    workflow.created_by.name = "user_name"
    workflow.created_by.username = "user_username"

    with patch.object(SettingsService, 'retrieve_setting', return_value=setting):
        with patch.object(WorkflowService, 'get_workflow', return_value=workflow):
            with patch('codemie.rest_api.routers.utils.run_in_thread_pool'):
                response = await WebhookService.invoke_webhook_logic(mock_request, webhook_id, mock_background_tasks)
                assert isinstance(response, BaseResponse)
                assert response.message == WebhookService.WEBHOOK_INVOKED_SUCCESSFULLY


@pytest.fixture
def datasource_fixture():
    from codemie.rest_api.models.index import IndexInfo

    mock = MagicMock(spec=IndexInfo)
    mock.app_name = "app_name"
    mock.user_id = "user_id"
    mock.repo_name = "repo_name"
    mock.project_name = "project_name"
    setattr(mock, WebhookService.INDEX_TYPE, "code")
    # Ensure created_by.id is a string for Pydantic validation
    creator = MagicMock()
    creator.id = "real_user_id"
    mock.created_by = creator
    return mock


@pytest.fixture
def setting_fixture():
    setting = MagicMock()
    setting.credential.side_effect = lambda key: {
        "webhook_id": "test_webhook",
        WebhookService.SECURE_HEADER_NAME: None,
        WebhookService.SECURE_HEADER_VALUE: None,
        WebhookService.GITHUB_WEBHOOK_SECRET: None,
        WebhookService.RESOURCE_TYPE: ResourceType.DATASOURCE.value,
        WebhookService.RESOURCE_ID: "datasource_id",
        WebhookService.IS_ENABLED: True,
    }.get(key)
    setting.project_name = "test_project"
    setting.user_id = "test_user"
    setting.alias = "test_alias"
    return setting


@pytest.fixture(autouse=True)
def patch_services(setting_fixture, datasource_fixture):
    with patch.object(SettingsService, 'retrieve_setting', return_value=setting_fixture):
        with patch('codemie.triggers.bindings.webhook.validate_datasource', return_value=datasource_fixture):
            with patch('codemie.triggers.bindings.webhook.run_in_thread_pool'):
                with patch('codemie.core.models.GitRepo.get_by_id', return_value=MagicMock(name="MockGitRepo")):
                    yield


@pytest.mark.asyncio
async def test_invoke_webhook_logic_handle_datasource(mock_request, mock_background_tasks):
    webhook_id = "test_webhook"
    response = await WebhookService.invoke_webhook_logic(mock_request, webhook_id, mock_background_tasks)
    assert isinstance(response, BaseResponse)
    assert response.message == WebhookService.WEBHOOK_INVOKED_SUCCESSFULLY


@pytest.mark.asyncio
async def test_invoke_webhook_logic_unsupported_resource_type(mock_request, mock_background_tasks):
    webhook_id = "test_webhook"
    setting = MagicMock()
    setting.credential.side_effect = lambda key: {
        "webhook_id": webhook_id,
        WebhookService.SECURE_HEADER_NAME: None,
        WebhookService.SECURE_HEADER_VALUE: None,
        WebhookService.GITHUB_WEBHOOK_SECRET: None,
        WebhookService.RESOURCE_TYPE: "unsupported_type",
        WebhookService.RESOURCE_ID: "resource_id",
        WebhookService.IS_ENABLED: True,
    }.get(key)
    setting.project_name = "test_project"
    setting.user_id = "test_user"
    setting.alias = "test_alias"

    with patch.object(SettingsService, 'retrieve_setting', return_value=setting):
        with pytest.raises(HTTPException) as exc_info:
            await WebhookService.invoke_webhook_logic(mock_request, webhook_id, mock_background_tasks)
        assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
        assert exc_info.value.detail == WebhookService.UNSUPPORTED_RESOURCE_TYPE.format("unsupported_type")
