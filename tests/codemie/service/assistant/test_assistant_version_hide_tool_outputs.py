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

"""Versioning of the assistant-level hide_tool_outputs flag."""

from unittest.mock import patch

from codemie.rest_api.models.assistant import Assistant, AssistantConfiguration, AssistantRequest
from codemie.rest_api.security.user import User
from codemie.service.assistant.assistant_version_compare_service import AssistantVersionCompareService
from codemie.service.assistant.assistant_version_service import AssistantVersionService

VERSION_SERVICE = 'codemie.service.assistant.assistant_version_service'
COMPARE_SERVICE = 'codemie.service.assistant.assistant_version_compare_service'


def _config(hide: bool, version: int = 1) -> AssistantConfiguration:
    return AssistantConfiguration(
        assistant_id="assistant-123",
        version_number=version,
        description="d",
        system_prompt="p",
        llm_model_type="gpt-4o",
        hide_tool_outputs=hide,
    )


def _user() -> User:
    return User(id="u", username="u", name="U", project_names=["demo"])


def test_flag_only_edit_counts_as_configuration_change():
    request = AssistantRequest(name="a", system_prompt="p", llm_model_type="gpt-4o", hide_tool_outputs=True)
    with patch(f'{COMPARE_SERVICE}.AssistantConfiguration') as config_cls:
        config_cls.get_current_version.return_value = _config(hide=False)
        assert AssistantVersionCompareService.has_configuration_changes("assistant-123", request) is True


def test_unchanged_flag_is_not_a_configuration_change():
    request = AssistantRequest(name="a", system_prompt="p", llm_model_type="gpt-4o", hide_tool_outputs=True)
    with patch(f'{COMPARE_SERVICE}.AssistantConfiguration') as config_cls:
        config_cls.get_current_version.return_value = _config(hide=True)
        assert AssistantVersionCompareService.has_configuration_changes("assistant-123", request) is False


def _create_new_version(assistant: Assistant, request: AssistantRequest) -> AssistantConfiguration:
    with (
        patch.object(AssistantConfiguration, 'get_latest_version_number', return_value=1),
        patch.object(AssistantConfiguration, 'save'),
        patch.object(Assistant, 'update'),
    ):
        return AssistantVersionService.create_new_version(assistant=assistant, request=request, user=_user())


def test_new_version_carries_flag_and_partial_update_keeps_it():
    assistant = Assistant(id="assistant-123", name="a", description="d", system_prompt="p", hide_tool_outputs=True)

    omitted = AssistantRequest(name="a", system_prompt="p", llm_model_type="gpt-4o")
    assert "hide_tool_outputs" not in omitted.model_fields_set
    assert _create_new_version(assistant, omitted).hide_tool_outputs is True

    explicit_off = AssistantRequest(name="a", system_prompt="p", llm_model_type="gpt-4o", hide_tool_outputs=False)
    assert _create_new_version(assistant, explicit_off).hide_tool_outputs is False

    assistant.hide_tool_outputs = False
    explicit_on = AssistantRequest(name="a", system_prompt="p", llm_model_type="gpt-4o", hide_tool_outputs=True)
    assert _create_new_version(assistant, explicit_on).hide_tool_outputs is True


def test_initial_version_carries_flag():
    assistant = Assistant(id="assistant-123", name="a", description="d", system_prompt="p")
    request = AssistantRequest(name="a", system_prompt="p", llm_model_type="gpt-4o", hide_tool_outputs=True)
    with patch.object(AssistantConfiguration, 'save'):
        config = AssistantVersionService.create_initial_version(assistant=assistant, request=request, user=_user())
    assert config.hide_tool_outputs is True


def test_rollback_restores_flag_from_target_config():
    assistant = Assistant(id="assistant-123", name="a", description="d", system_prompt="p", hide_tool_outputs=False)
    assistant.version_count = 3
    target = _config(hide=True, version=1)
    with (
        patch.object(AssistantVersionService, 'get_version', return_value=target),
        patch.object(AssistantConfiguration, 'save'),
        patch.object(Assistant, 'update'),
    ):
        new_config = AssistantVersionService.rollback_to_version(
            assistant=assistant, target_version_number=1, user=_user()
        )
    assert new_config.hide_tool_outputs is True
    assert assistant.hide_tool_outputs is True
