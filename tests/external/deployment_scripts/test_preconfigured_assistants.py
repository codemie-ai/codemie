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
from unittest.mock import ANY, patch, MagicMock, call
from codemie.rest_api.models.assistant import Assistant, Context
from codemie.rest_api.models.index import IndexInfo
from codemie.service.llm_service.llm_service import LLMService
from external.deployment_scripts.preconfigured_assistants import (
    create_context_from_index,
    create_preconfigured_assistant,
    update_assistant_content,
    manage_preconfigured_assistants,
    delete_disabled_assistant,
    get_all_contexts,
    get_index_info,
    REPO_NAME_FIELD,
    PROJECT_NAME_FIELD,
)


@pytest.fixture
def mock_assistant():
    context = MagicMock(spec=Context)
    context.context_type = "knowledge_base"
    context.name = "codemie-onboarding"

    assistant = MagicMock(spec=Assistant)
    assistant.name = "Codemie Onboarding"
    assistant.description = "Onboarding assistant for Codemie"
    assistant.system_prompt = "Help users onboard Codemie."
    assistant.conversation_starters = ["What is CodeMie?"]
    assistant.is_react = False
    assistant.toolkits = []
    assistant.context = [context]
    assistant.icon_url = "https://example.com/icon.png"
    assistant.slug = "codemie-onboarding"
    assistant.categories = []
    assistant.llm_model_type = LLMService.BASE_NAME_GPT_41
    assistant.mcp_servers = []
    return assistant


@pytest.fixture
def mock_assistant_template(mock_assistant):
    """Create a mock assistant template based on the mock_assistant fixture."""
    template = MagicMock()
    template.name = mock_assistant.name
    template.description = mock_assistant.description
    template.system_prompt = mock_assistant.system_prompt
    template.conversation_starters = mock_assistant.conversation_starters
    template.is_react = mock_assistant.is_react
    template.toolkits = mock_assistant.toolkits
    template.icon_url = mock_assistant.icon_url
    template.slug = mock_assistant.slug
    template.llm_model_type = mock_assistant.llm_model_type
    template.categories = mock_assistant.categories
    template.mcp_servers = mock_assistant.mcp_servers
    template.context = None  # Default to no context from template
    return template


@pytest.fixture
def mock_index_info():
    index_info = MagicMock(spec=IndexInfo)
    index_info.repo_name = "codemie-onboarding"
    return index_info


@pytest.fixture
def mock_context():
    context = MagicMock(spec=Context)
    context.context_type = "knowledge_base"
    context.name = "codemie-onboarding"
    return context


@patch('codemie.rest_api.models.index.IndexInfo.get_by_fields')
def test_get_index_info_found(mock_get_by_fields, mock_index_info):
    """Test get_index_info returns IndexInfo when found."""
    mock_get_by_fields.return_value = mock_index_info

    result = get_index_info('codemie-onboarding')

    assert result == mock_index_info
    mock_get_by_fields.assert_called_once_with({REPO_NAME_FIELD: 'codemie-onboarding', PROJECT_NAME_FIELD: 'codemie'})


@patch('codemie.rest_api.models.index.IndexInfo.get_by_fields')
def test_get_index_info_not_found(mock_get_by_fields):
    """Test get_index_info returns None when not found."""
    mock_get_by_fields.return_value = None

    result = get_index_info('non-existent')

    assert result is None
    mock_get_by_fields.assert_called_once_with({REPO_NAME_FIELD: 'non-existent', PROJECT_NAME_FIELD: 'codemie'})


@patch('external.deployment_scripts.preconfigured_assistants.get_index_info')
@patch('codemie.rest_api.models.assistant.Context.index_info_type')
@patch('external.deployment_scripts.preconfigured_assistants.create_index_from_dump')
def test_create_context_from_index(
    mock_create_index_from_dump, mock_index_info_type, mock_get_index_info, mock_index_info, mock_context
):
    mock_get_index_info.return_value = mock_index_info
    mock_index_info_type.return_value = mock_context.context_type

    with patch('external.deployment_scripts.preconfigured_assistants.Context') as mock_context_class:
        mock_context_class.return_value = mock_context
        context = create_context_from_index('codemie-onboarding')

    assert context.context_type == mock_context.context_type
    assert context.name == mock_context.name

    mock_get_index_info.assert_called_once_with('codemie-onboarding')
    mock_create_index_from_dump.assert_not_called()


@patch('external.deployment_scripts.preconfigured_assistants.get_index_info')
@patch('external.deployment_scripts.preconfigured_assistants.create_index_from_dump')
@patch('codemie.rest_api.models.assistant.Context.index_info_type')
def test_create_context_from_index_missing_index(
    mock_index_info_type, mock_create_index_from_dump, mock_get_index_info, mock_index_info, mock_context
):
    mock_get_index_info.return_value = None
    mock_create_index_from_dump.return_value = mock_index_info
    mock_index_info_type.return_value = mock_context.context_type

    with patch('external.deployment_scripts.preconfigured_assistants.Context') as mock_context_class:
        mock_context_class.return_value = mock_context
        context = create_context_from_index('codemie-onboarding')

    assert context.context_type == mock_context.context_type
    assert context.name == mock_context.name

    mock_get_index_info.assert_called_with('codemie-onboarding')
    mock_create_index_from_dump.assert_called_once_with("codemie", "codemie-onboarding")


@patch('codemie.rest_api.models.assistant.Assistant.get_by_fields')
@patch('external.deployment_scripts.preconfigured_assistants.assistant_service.get_assistant_template_by_slug')
@patch('external.deployment_scripts.preconfigured_assistants.get_all_contexts')
@patch('external.deployment_scripts.preconfigured_assistants.update_assistant_content')
@patch('external.deployment_scripts.preconfigured_assistants.logger')
def test_create_preconfigured_assistant_existing(
    mock_logger,
    mock_update_assistant_content,
    mock_get_all_contexts,
    mock_get_template,
    mock_get_by_fields,
    mock_assistant,
    mock_assistant_template,
    mock_context,
):
    mock_get_by_fields.return_value = mock_assistant
    mock_get_template.return_value = mock_assistant_template
    mock_get_all_contexts.return_value = [mock_context]

    create_preconfigured_assistant(mock_assistant.slug)

    mock_get_by_fields.assert_called_once_with({"slug.keyword": mock_assistant.slug})
    mock_get_template.assert_called_once_with(mock_assistant.slug)
    mock_get_all_contexts.assert_called_once_with(mock_assistant.slug, mock_assistant_template)
    mock_logger.info.assert_has_calls([call(f"Assistant '{mock_assistant.slug}' already exists.")], any_order=False)

    mock_update_assistant_content.assert_called_once_with(mock_assistant, mock_assistant_template, [mock_context])


@patch('codemie.rest_api.models.assistant.Assistant.get_by_fields')
@patch('external.deployment_scripts.preconfigured_assistants.assistant_service.get_assistant_template_by_slug')
@patch('external.deployment_scripts.preconfigured_assistants.get_all_contexts')
@patch('codemie.rest_api.models.assistant.Assistant.save')
@patch('external.deployment_scripts.preconfigured_assistants.logger')
@patch('external.deployment_scripts.preconfigured_assistants.llm_service')
def test_create_preconfigured_assistant_new(
    mock_llm_service,
    mock_logger,
    mock_save,
    mock_get_all_contexts,
    mock_get_template,
    mock_get_by_fields,
    mock_assistant,
    mock_context,
    mock_assistant_template,
):
    mock_llm_service.default_llm_model = LLMService.BASE_NAME_GPT_41
    mock_get_by_fields.return_value = None
    mock_get_template.return_value = mock_assistant_template
    mock_get_all_contexts.return_value = [mock_context]

    create_preconfigured_assistant(mock_assistant.slug)

    mock_get_by_fields.assert_called_once_with({"slug.keyword": mock_assistant.slug})
    mock_get_template.assert_called_once_with(mock_assistant.slug)
    mock_get_all_contexts.assert_called_once_with(mock_assistant.slug, mock_assistant_template)
    mock_save.assert_called_once()
    mock_logger.info.assert_called_with(f"Assistant '{mock_assistant.slug}' created successfully.")


@patch('external.deployment_scripts.preconfigured_assistants.get_assistant_index_name')
@patch('external.deployment_scripts.preconfigured_assistants.create_context_from_index')
@patch('external.deployment_scripts.preconfigured_assistants.get_index_info')
def test_get_all_contexts_with_static_and_dynamic(
    mock_get_index_info,
    mock_create_context_from_index,
    mock_get_assistant_index_name,
    mock_assistant_template,
    mock_index_info,
):
    """Test get_all_contexts merges static and dynamic contexts correctly."""
    # Setup static context
    static_context = MagicMock(spec=Context)
    static_context.name = "static-context"
    mock_get_assistant_index_name.return_value = "static-index"
    mock_create_context_from_index.return_value = static_context

    # Setup dynamic contexts
    template_ctx1 = MagicMock(spec=Context)
    template_ctx1.name = "dynamic-context-1"
    template_ctx2 = MagicMock(spec=Context)
    template_ctx2.name = "dynamic-context-2"
    mock_assistant_template.context = [template_ctx1, template_ctx2]

    # First context exists (returns IndexInfo), second doesn't (returns None)
    mock_get_index_info.side_effect = [mock_index_info, None]

    # Execute
    result = get_all_contexts("test-assistant", mock_assistant_template)

    # Verify
    assert len(result) == 2
    assert static_context in result
    assert template_ctx1 in result  # Template context is used directly when index exists
    mock_get_assistant_index_name.assert_called_once_with("test-assistant")
    mock_create_context_from_index.assert_called_once_with("static-index")
    assert mock_get_index_info.call_count == 2


@patch('external.deployment_scripts.preconfigured_assistants.get_assistant_index_name')
@patch('external.deployment_scripts.preconfigured_assistants.get_index_info')
def test_get_all_contexts_avoids_duplicates(
    mock_get_index_info, mock_get_assistant_index_name, mock_assistant_template, mock_index_info
):
    """Test get_all_contexts avoids duplicate context names."""
    # Setup static context with name "duplicate-context"
    mock_get_assistant_index_name.return_value = None  # No static context

    # Setup template contexts with duplicate name
    template_ctx1 = MagicMock(spec=Context)
    template_ctx1.name = "duplicate-context"
    template_ctx2 = MagicMock(spec=Context)
    template_ctx2.name = "duplicate-context"  # Same name
    mock_assistant_template.context = [template_ctx1, template_ctx2]

    mock_get_index_info.return_value = mock_index_info

    # Execute
    result = get_all_contexts("test-assistant", mock_assistant_template)

    # Verify - should only have one context despite duplicate names
    assert len(result) == 1
    assert mock_get_index_info.call_count == 1  # Second call skipped due to duplicate


@patch('external.deployment_scripts.preconfigured_assistants.get_assistant_index_name')
def test_get_all_contexts_no_contexts(mock_get_assistant_index_name, mock_assistant_template):
    """Test get_all_contexts returns empty list when no contexts configured."""
    mock_get_assistant_index_name.return_value = None
    mock_assistant_template.context = None

    result = get_all_contexts("test-assistant", mock_assistant_template)

    assert result == []


@patch('external.deployment_scripts.preconfigured_assistants.logger')
def test_update_assistant_content_no_changes(mock_logger, mock_assistant, mock_assistant_template):
    same_conversation_starters = ["Same Prompt"]
    same_toolkits = []
    same_categories = []

    mock_assistant.conversation_starters = same_conversation_starters
    mock_assistant.toolkits = same_toolkits
    mock_assistant_template.conversation_starters = same_conversation_starters
    mock_assistant_template.toolkits = same_toolkits
    mock_assistant_template.categories = same_categories

    with patch('external.deployment_scripts.preconfigured_assistants.llm_service') as mock_llm_service:
        mock_llm_service.default_llm_model = LLMService.BASE_NAME_GPT_41
        result = update_assistant_content(mock_assistant, mock_assistant_template)

    assert result is False
    mock_assistant.save.assert_not_called()


@patch('external.deployment_scripts.preconfigured_assistants.logger')
def test_update_assistant_content_with_changes(mock_logger, mock_assistant, mock_assistant_template):
    mock_assistant.conversation_starters = ["Original Prompt"]
    mock_assistant_template.conversation_starters = ["Updated Prompt"]

    result = update_assistant_content(mock_assistant, mock_assistant_template)

    assert result is True
    assert mock_assistant.conversation_starters == ["Updated Prompt"]
    mock_assistant.save.assert_called_once()
    mock_logger.info.assert_called_with(f"Assistant '{mock_assistant.slug}' updated successfully.")


@patch('external.deployment_scripts.preconfigured_assistants.logger')
def test_update_assistant_content_with_context_change(
    mock_logger, mock_assistant, mock_assistant_template, mock_context
):
    new_context = [MagicMock(spec=Context)]
    result = update_assistant_content(mock_assistant, mock_assistant_template, new_context)

    assert result is True
    assert mock_assistant.context == new_context
    mock_assistant.save.assert_called_once()
    mock_logger.info.assert_called_with(f"Assistant '{mock_assistant.slug}' updated successfully.")


@patch('external.deployment_scripts.preconfigured_assistants.logger')
def test_update_assistant_content_icon_url(mock_logger, mock_assistant, mock_assistant_template):
    mock_assistant.icon_url = "https://example.com/old-icon.png"
    mock_assistant_template.icon_url = "https://example.com/new-icon.png"

    result = update_assistant_content(mock_assistant, mock_assistant_template)

    assert result is True
    assert mock_assistant.icon_url == "https://example.com/new-icon.png"
    mock_assistant.save.assert_called_once()
    mock_logger.info.assert_any_call(f"Updating icon_url for assistant '{mock_assistant.slug}'")
    mock_logger.info.assert_any_call(f"Assistant '{mock_assistant.slug}' updated successfully.")


@patch('external.deployment_scripts.preconfigured_assistants.customer_config')
@patch('external.deployment_scripts.preconfigured_assistants.assistant_service')
@patch('external.deployment_scripts.preconfigured_assistants.create_preconfigured_assistant')
@patch('external.deployment_scripts.preconfigured_assistants.delete_disabled_assistant')
@patch('external.deployment_scripts.preconfigured_assistants.logger')
def test_manage_preconfigured_assistants(
    mock_logger,
    mock_delete_disabled_assistant,
    mock_create_preconfigured_assistant,
    mock_assistant_service,
    mock_customer_config,
):
    # Setup mocks
    mock_customer_config.get_all_configured_assistant_slugs.return_value = ['assistant1', 'assistant2', 'assistant3']
    mock_customer_config.is_assistant_enabled.side_effect = lambda slug: slug in ['assistant1', 'assistant2']

    # Mock available templates
    mock_template1 = MagicMock()
    mock_template1.slug = 'assistant1'
    mock_template2 = MagicMock()
    mock_template2.slug = 'assistant2'
    mock_template3 = MagicMock()
    mock_template3.slug = 'assistant3'

    mock_assistant_service.get_all_assistant_templates.return_value = [
        mock_template1,
        mock_template2,
        mock_template3,
    ]

    # Call the function
    manage_preconfigured_assistants()

    # Verify customer config calls
    mock_customer_config.get_all_configured_assistant_slugs.assert_called_once()
    mock_customer_config.is_assistant_enabled.assert_any_call('assistant1')
    mock_customer_config.is_assistant_enabled.assert_any_call('assistant2')
    mock_customer_config.is_assistant_enabled.assert_any_call('assistant3')

    # Verify create calls for enabled assistants
    mock_create_preconfigured_assistant.assert_any_call('assistant1', ANY)
    mock_create_preconfigured_assistant.assert_any_call('assistant2', ANY)

    # Verify delete call for disabled assistant
    mock_delete_disabled_assistant.assert_called_once_with('assistant3')

    # Verify total calls
    assert mock_create_preconfigured_assistant.call_count == 2
    assert mock_delete_disabled_assistant.call_count == 1


@patch('codemie.rest_api.models.assistant.Assistant.get_by_fields')
@patch('external.deployment_scripts.preconfigured_assistants.preconfigured_assistant_ids')
@patch('external.deployment_scripts.preconfigured_assistants.logger')
@patch("codemie.service.guardrail.guardrail_service.GuardrailService.remove_guardrail_assignments_for_entity")
def test_delete_disabled_assistant_exists(mock_remove_guardrails, mock_logger, mock_assistant_ids, mock_get_by_fields):
    mock_remove_guardrails.return_value = None

    # Setup mock existing assistant
    mock_assistant = MagicMock()
    mock_assistant.delete = MagicMock()
    mock_get_by_fields.return_value = mock_assistant

    # Setup assistant_ids dict
    mock_assistant_ids.__contains__ = MagicMock(return_value=True)
    mock_assistant_ids.__delitem__ = MagicMock()

    # Call function
    result = delete_disabled_assistant('test-assistant')

    # Verify
    assert result is True
    mock_get_by_fields.assert_called_once_with({"slug.keyword": "test-assistant"})
    mock_assistant.delete.assert_called_once()
    mock_logger.info.assert_called_once_with("Deleting disabled assistant 'test-assistant'")
    mock_assistant_ids.__delitem__.assert_called_once_with('test-assistant')


@patch('codemie.rest_api.models.assistant.Assistant.get_by_fields')
@patch('external.deployment_scripts.preconfigured_assistants.logger')
def test_delete_disabled_assistant_not_exists(mock_logger, mock_get_by_fields):
    # Setup mock - no existing assistant
    mock_get_by_fields.return_value = None

    # Call function
    result = delete_disabled_assistant('test-assistant')

    # Verify
    assert result is False
    mock_get_by_fields.assert_called_once_with({"slug.keyword": "test-assistant"})
