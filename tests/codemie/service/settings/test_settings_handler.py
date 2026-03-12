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

import pytest
from unittest.mock import MagicMock, PropertyMock, patch

from codemie.service.settings.settings_handler import (
    SettingsHandler,
    GlobalUserSettingsHandler,
    AssistantUserMappingSettingsHandler,
    BySettingIDSettingsHandler,
    GlobalAssistantSettingsHandler,
    AssistantSettingsHandler,
    DefaultSettingsHandler,
    UserSettingsHandler,
    ProjectSettingsHandler,
    build_settings_handlers,
)

from codemie.service.settings.base_settings import SearchFields


@pytest.fixture
def mock_handler():
    mock = MagicMock()
    mock.handle.return_value = "next_setting"
    return mock


class MockSettingsHandlerPass(SettingsHandler):
    def handle(*args, **kwargs):
        return super().handle(*args, **kwargs)


class MockSettingsHandler(SettingsHandler):
    def handle(*args, **kwargs):
        return "setting"


class TestSettingsHandler:
    def test_pipe(self):
        chain = MockSettingsHandlerPass() | MockSettingsHandler()

        result = chain.handle()
        assert result == "setting"


class TestAssistantUserMappingSettingsHandler:
    @patch("codemie.service.settings.settings_handler.search_assistant")
    @patch("codemie.rest_api.models.settings.Settings.get_by_fields")
    @patch(
        'codemie.repository.assistants.assistant_user_mapping_repository.AssistantUserMappingRepositoryImpl.get_mapping'
    )
    def test_handle_found(self, mock_mapping, mock_get_settings, mock_search_asst):
        """Test when settings are found through the assistant-user mapping."""
        mock_search_asst.return_value = PropertyMock(is_global=False)
        mock_config = MagicMock()
        mock_config.integration_id = "test_integration_id"
        mock_mapping.tools_config = [mock_config]
        mock_mapping.return_value = mock_mapping

        mock_settings_instance = MagicMock()
        mock_get_settings.return_value = mock_settings_instance

        next_handler = MagicMock(spec=SettingsHandler)
        handler = AssistantUserMappingSettingsHandler()
        handler | next_handler

        search_fields = {SearchFields.USER_ID: "test_user_id", SearchFields.CREDENTIAL_TYPE: "test_credential_type"}
        assistant_id = "test_assistant_id"

        result = handler.handle(search_fields, assistant_id=assistant_id)

        mock_mapping.assert_called_once_with(assistant_id=assistant_id, user_id=search_fields[SearchFields.USER_ID])
        mock_get_settings.assert_called_once_with(
            {"id": "test_integration_id", "credential_type": search_fields[SearchFields.CREDENTIAL_TYPE]}
        )
        assert result == mock_settings_instance
        next_handler.handle.assert_not_called()

    @patch("codemie.service.settings.settings_handler.search_assistant")
    @patch("codemie.rest_api.models.settings.Settings.get_by_fields")
    @patch(
        'codemie.repository.assistants.assistant_user_mapping_repository.AssistantUserMappingRepositoryImpl.get_mapping'
    )
    def test_handle_not_found(self, mock_get_mapping, mock_get_settings, mock_search_asst):
        """Test when settings are not found and request is passed to next handler."""
        mock_search_asst.return_value = PropertyMock(is_global=False)
        mock_config = MagicMock()
        mock_config.integration_id = "test_integration_id"

        mapping = MagicMock()
        mapping.tools_config = [mock_config]
        mock_get_mapping.return_value = mapping

        mock_get_settings.return_value = None

        next_handler = MagicMock(spec=SettingsHandler)
        mock_next_result = MagicMock()
        next_handler.handle.return_value = mock_next_result
        handler = AssistantUserMappingSettingsHandler()
        handler | next_handler

        search_fields = {SearchFields.USER_ID: "test_user_id", SearchFields.CREDENTIAL_TYPE: "test_credential_type"}
        assistant_id = "test_assistant_id"

        result = handler.handle(search_fields, assistant_id)

        mock_get_settings.assert_called_once()
        next_handler.handle.assert_called_once_with(search_fields, assistant_id=assistant_id)
        assert result == mock_next_result

    def test_handle_no_assistant_id(self):
        """Test when no assistant_id is provided."""
        next_handler = MagicMock(spec=SettingsHandler)
        mock_next_result = MagicMock()
        next_handler.handle.return_value = mock_next_result
        handler = AssistantUserMappingSettingsHandler()
        handler | next_handler

        search_fields = {SearchFields.USER_ID: "test_user_id"}
        assistant_id = None

        result = handler.handle(search_fields, assistant_id=assistant_id)

        next_handler.handle.assert_called_once_with(search_fields, assistant_id=assistant_id)
        assert result == mock_next_result

    @patch("codemie.service.settings.settings_handler.search_assistant")
    @patch(
        'codemie.repository.assistants.assistant_user_mapping_repository.AssistantUserMappingRepositoryImpl.get_mapping'
    )
    def test_handle_no_mapping_found(self, mock_get_mapping, mock_search_asst):
        """Test when no mapping is found for the assistant-user pair."""
        mock_search_asst.return_value = PropertyMock(is_global=False)
        mock_get_mapping.return_value = None

        next_handler = MagicMock(spec=SettingsHandler)
        mock_next_result = MagicMock()
        next_handler.handle.return_value = mock_next_result
        handler = AssistantUserMappingSettingsHandler()
        handler | next_handler

        search_fields = {SearchFields.USER_ID: "test_user_id"}
        assistant_id = "test_assistant_id"

        result = handler.handle(search_fields, assistant_id=assistant_id)

        next_handler.handle.assert_called_once_with(search_fields, assistant_id=assistant_id)

        assert result == mock_next_result


class TestGlobalUserSettingsHandler:
    @patch("codemie.rest_api.models.settings.Settings.get_by_fields")
    def test_handle_found(self, mock_get_settings, mock_handler):
        mock_get_settings.return_value = "settings"
        search_fields = {
            SearchFields.PROJECT_NAME: "test-project",
            SearchFields.CREDENTIAL_TYPE: "jira",
            SearchFields.USER_ID: "test-user",
        }

        handler = GlobalUserSettingsHandler().set_next(mock_handler)
        result = handler.handle(search_fields)

        assert result == "settings"
        # PROJECT_NAME should be removed from search fields before calling get_by_fields
        mock_get_settings.assert_called_once_with(
            {
                SearchFields.CREDENTIAL_TYPE: "jira",
                SearchFields.USER_ID: "test-user",
                SearchFields.SETTING_TYPE: 'user',
                SearchFields.IS_GLOBAL: True,
            }
        )

    @patch("codemie.rest_api.models.settings.Settings.get_by_fields")
    def test_handle_not_found(self, mock_get_settings, mock_handler):
        mock_get_settings.return_value = None
        search_fields = {
            SearchFields.PROJECT_NAME: "test-project",
            SearchFields.CREDENTIAL_TYPE: "jira",
            SearchFields.USER_ID: "test-user",
        }

        handler = GlobalUserSettingsHandler().set_next(mock_handler)
        result = handler.handle(search_fields)

        assert result == "next_setting"


class TestBySettingIDSettingsHandler:
    @patch("codemie.service.settings.settings_handler.search_settings_by_id")
    def test_handle_found(self, mock_search_settings_by_id, mock_handler):
        mock_search_settings_by_id.return_value = "setting"
        handler = BySettingIDSettingsHandler().set_next(mock_handler)

        result = handler.handle(search_fields={"key": "val"}, setting_id="test_id")

        assert result == "setting"

    @patch("codemie.service.settings.settings_handler.search_settings_by_id")
    def test_handle_not_found(self, mock_search_settings_by_id, mock_handler):
        mock_search_settings_by_id.return_value = None
        handler = BySettingIDSettingsHandler().set_next(mock_handler)

        handler.handle(search_fields={"key": "val"}, setting_id="test_id")

        mock_handler.handle.assert_called_once_with({"key": "val"}, setting_id="test_id")


class TestGlobalAssistantSettingsHandler:
    @patch("codemie.service.settings.settings_handler.search_assistant_settings")
    @patch("codemie.service.settings.settings_handler.search_assistant")
    def test_handle_global_assistant_with_settings(
        self, mock_search_assistant, mock_search_assistant_settings, mock_handler
    ):
        """Test when assistant is global and has settings."""
        mock_assistant = MagicMock()
        mock_assistant.is_global = True
        mock_search_assistant.return_value = mock_assistant

        mock_assistant_setting = MagicMock()
        mock_search_assistant_settings.return_value = mock_assistant_setting

        handler = GlobalAssistantSettingsHandler().set_next(mock_handler)

        search_fields = {"key": "val"}
        assistant_id = "test_assistant_id"

        result = handler.handle(search_fields=search_fields, assistant_id=assistant_id)

        mock_search_assistant.assert_called_once_with(assistant_id)
        mock_search_assistant_settings.assert_called_once_with(mock_assistant, search_fields, None)
        assert result == mock_assistant_setting
        mock_handler.handle.assert_not_called()

    @patch("codemie.service.settings.settings_handler.search_assistant_settings")
    @patch("codemie.service.settings.settings_handler.search_assistant")
    def test_handle_global_assistant_no_settings(
        self, mock_search_assistant, mock_search_assistant_settings, mock_handler
    ):
        """Test when assistant is global but has no settings."""
        mock_assistant = MagicMock()
        mock_assistant.is_global = True
        mock_search_assistant.return_value = mock_assistant

        mock_search_assistant_settings.return_value = None

        handler = GlobalAssistantSettingsHandler().set_next(mock_handler)

        search_fields = {"key": "val"}
        assistant_id = "test_assistant_id"

        handler.handle(search_fields=search_fields, assistant_id=assistant_id)

        mock_search_assistant.assert_called_once_with(assistant_id)
        mock_search_assistant_settings.assert_called_once_with(mock_assistant, search_fields, None)
        mock_handler.handle.assert_called_once_with(search_fields, assistant_id=assistant_id)

    @patch("codemie.service.settings.settings_handler.search_assistant_settings")
    @patch("codemie.service.settings.settings_handler.search_assistant")
    def test_handle_non_global_assistant(self, mock_search_assistant, mock_search_assistant_settings, mock_handler):
        """Test when assistant is not global."""
        mock_assistant = MagicMock()
        mock_assistant.is_global = False
        mock_search_assistant.return_value = mock_assistant

        mock_assistant_setting = MagicMock()
        mock_search_assistant_settings.return_value = mock_assistant_setting

        handler = GlobalAssistantSettingsHandler().set_next(mock_handler)

        search_fields = {"key": "val"}
        assistant_id = "test_assistant_id"

        handler.handle(search_fields=search_fields, assistant_id=assistant_id)

        mock_search_assistant.assert_called_once_with(assistant_id)
        mock_search_assistant_settings.assert_called_once_with(mock_assistant, search_fields, None)
        mock_handler.handle.assert_called_once_with(search_fields, assistant_id=assistant_id)


def test_handlers_order():
    handlers = []
    current = build_settings_handlers()

    while current:
        handlers.append(current)
        current = current._next_handler

    assert len(handlers) == 8
    assert isinstance(handlers[0], AssistantUserMappingSettingsHandler)
    assert isinstance(handlers[1], BySettingIDSettingsHandler)
    assert isinstance(handlers[2], GlobalAssistantSettingsHandler)
    assert isinstance(handlers[3], AssistantSettingsHandler)
    assert isinstance(handlers[4], DefaultSettingsHandler)
    assert isinstance(handlers[5], UserSettingsHandler)
    assert isinstance(handlers[6], GlobalUserSettingsHandler)
    assert isinstance(handlers[7], ProjectSettingsHandler)
