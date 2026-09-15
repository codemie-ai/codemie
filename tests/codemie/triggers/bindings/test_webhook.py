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

from unittest.mock import MagicMock, patch

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
            WebhookService.invoke_webhook_logic(mock_request, webhook_id, mock_background_tasks, b'{}')
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
            WebhookService.invoke_webhook_logic(mock_request, webhook_id, mock_background_tasks, b'{}')
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
                WebhookService.invoke_webhook_logic(mock_request, webhook_id, mock_background_tasks, b'{}')
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
    workflow.project = "test_project"

    with patch.object(SettingsService, 'retrieve_setting', return_value=setting):
        with patch.object(WorkflowService, 'get_workflow', return_value=workflow):
            response = WebhookService.invoke_webhook_logic(mock_request, webhook_id, mock_background_tasks, b'{}')
            assert isinstance(response, BaseResponse)
            assert response.message == WebhookService.WEBHOOK_INVOKED_SUCCESSFULLY


@pytest.fixture
def datasource_fixture():
    from codemie.rest_api.models.index import IndexInfo

    mock = MagicMock(spec=IndexInfo)
    mock.app_name = "app_name"
    mock.user_id = "user_id"
    mock.repo_name = "repo_name"
    mock.project_name = "test_project"
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
            with patch('codemie.core.models.GitRepo.get_by_id', return_value=MagicMock(name="MockGitRepo")):
                yield


@pytest.mark.asyncio
async def test_invoke_webhook_logic_handle_datasource(mock_request, mock_background_tasks):
    webhook_id = "test_webhook"
    response = WebhookService.invoke_webhook_logic(mock_request, webhook_id, mock_background_tasks, b'{}')
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
            WebhookService.invoke_webhook_logic(mock_request, webhook_id, mock_background_tasks, b'{}')
        assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
        assert exc_info.value.detail == WebhookService.UNSUPPORTED_RESOURCE_TYPE.format("unsupported_type")


# ---------------------------------------------------------------------------
# Guard tests for datasource config presence
# ---------------------------------------------------------------------------


def test_handle_datasource_confluence_raises_400_when_confluence_config_missing(mock_background_tasks, setting_fixture):
    """handle_datasource raises 400 when a Confluence datasource has no confluence config."""
    from codemie.rest_api.models.index import IndexInfo
    from codemie.rest_api.security.user import User
    from codemie.service.constants import FullDatasourceTypes

    mock_user = User(id="test-user", username="testuser")
    confluence_ds = MagicMock(spec=IndexInfo)
    confluence_ds.project_name = "test_project"
    confluence_ds.repo_name = "confluence-repo"
    confluence_ds.index_type = FullDatasourceTypes.CONFLUENCE.value
    confluence_ds.is_code_index.return_value = False
    creator = MagicMock()
    creator.id = "real_user_id"
    confluence_ds.created_by = creator
    confluence_ds.confluence = None

    with (
        patch("codemie.triggers.bindings.webhook.validate_datasource", return_value=confluence_ds),
        patch("codemie.triggers.bindings.webhook.resolve_trigger_user", return_value=mock_user),
    ):
        with pytest.raises(HTTPException) as exc_info:
            WebhookService.handle_datasource(
                resource_id="datasource_id",
                background_tasks=mock_background_tasks,
                setting=setting_fixture,
            )
        assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
        assert "missing space keys" in exc_info.value.detail


# ---------------------------------------------------------------------------
# Tests for xWiki webhook dispatch (EPMCDME-14794)
# ---------------------------------------------------------------------------


def test_handle_datasource_xwiki_raises_400_when_xwiki_config_missing(mock_background_tasks, setting_fixture):
    """handle_datasource must raise 400 when xWiki datasource has no xwiki config (EPMCDME-14794)."""
    from codemie.rest_api.models.index import IndexInfo
    from codemie.rest_api.security.user import User
    from codemie.service.constants import FullDatasourceTypes

    mock_user = User(id="test-user", username="testuser")
    xwiki_ds = MagicMock(spec=IndexInfo)
    xwiki_ds.project_name = "test_project"
    xwiki_ds.repo_name = "xwiki-repo"
    xwiki_ds.index_type = FullDatasourceTypes.XWIKI.value
    xwiki_ds.is_code_index.return_value = False
    creator = MagicMock()
    creator.id = "real_user_id"
    xwiki_ds.created_by = creator
    xwiki_ds.xwiki = None

    with (
        patch("codemie.triggers.bindings.webhook.validate_datasource", return_value=xwiki_ds),
        patch("codemie.triggers.bindings.webhook.resolve_trigger_user", return_value=mock_user),
    ):
        with pytest.raises(HTTPException) as exc_info:
            WebhookService.handle_datasource(
                resource_id="datasource_id",
                background_tasks=mock_background_tasks,
                setting=setting_fixture,
            )
        assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
        assert "missing xWiki configuration" in exc_info.value.detail


def test_handle_datasource_xwiki_schedules_reindex_xwiki(mock_background_tasks, setting_fixture):
    """handle_datasource must schedule reindex_xwiki with an XWikiReindexTask (EPMCDME-14794)."""
    from codemie.rest_api.models.index import IndexInfo, XWikiIndexInfo
    from codemie.rest_api.security.user import User
    from codemie.service.constants import FullDatasourceTypes
    from codemie.triggers.trigger_models import XWikiReindexTask

    xwiki_info = XWikiIndexInfo(space="KB")
    mock_user = User(id="test-user", username="testuser")

    xwiki_ds = MagicMock(spec=IndexInfo)
    xwiki_ds.project_name = "test_project"
    xwiki_ds.repo_name = "xwiki-repo"
    xwiki_ds.index_type = FullDatasourceTypes.XWIKI.value
    xwiki_ds.is_code_index.return_value = False
    creator = MagicMock()
    creator.id = "real_user_id"
    xwiki_ds.created_by = creator
    xwiki_ds.xwiki = xwiki_info

    with (
        patch("codemie.triggers.bindings.webhook.validate_datasource", return_value=xwiki_ds),
        patch("codemie.triggers.bindings.webhook.resolve_trigger_user", return_value=mock_user),
        patch("codemie.triggers.bindings.webhook.reindex_xwiki") as mock_reindex_xwiki,
        patch("codemie.triggers.bindings.webhook.XWikiReindexTask", wraps=XWikiReindexTask) as mock_task_cls,
    ):
        WebhookService.handle_datasource(
            resource_id="datasource_id",
            background_tasks=mock_background_tasks,
            setting=setting_fixture,
        )

    mock_task_cls.assert_called_once_with(
        resource_id="datasource_id",
        project_name="test_project",
        resource_name="xwiki-repo",
        user=mock_user,
        index_info=xwiki_ds,
        xwiki_index_info=xwiki_info,
    )
    mock_background_tasks.add_task.assert_called_once()
    call_args = mock_background_tasks.add_task.call_args
    assert call_args[0][0] is mock_reindex_xwiki
    assert isinstance(call_args[0][1], XWikiReindexTask)
