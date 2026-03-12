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

from unittest.mock import patch, MagicMock

from codemie.core.workflow_models import WorkflowConfigTemplate
from external.deployment_scripts.preconfigured_workflows import (
    patch_template_with_real_assistant_ids,
    create_preconfigured_workflow,
)


class TestPatchTemplateWithRealAssistantIds:
    @patch('external.deployment_scripts.preconfigured_workflows.get_preconfigured_assistant_id_by_slug')
    def test_patch_template_with_no_assistants(self, mock_get_assistant_id):
        """Test patching a template with no assistants."""
        template = MagicMock(spec=WorkflowConfigTemplate)
        template.assistants = []
        template.yaml_config = "test config"

        patch_template_with_real_assistant_ids(template)

        # Verify get_preconfigured_assistant_id_by_slug was not called
        mock_get_assistant_id.assert_not_called()
        # Verify template was not modified
        assert template.yaml_config == "test config"

    @patch('external.deployment_scripts.preconfigured_workflows.get_preconfigured_assistant_id_by_slug')
    def test_patch_template_with_preconfigured_assistants(self, mock_get_assistant_id):
        """Test patching a template with preconfigured assistants."""
        # Create a mock assistant definition
        assistant_def = MagicMock()
        assistant_def.assistant_id = "PRECONFIGURED:test-assistant"

        # Create a mock template
        template = MagicMock(spec=WorkflowConfigTemplate)
        template.assistants = [assistant_def]
        template.yaml_config = "config with PRECONFIGURED:test-assistant reference"

        # Setup mock to return a UUID
        mock_get_assistant_id.return_value = "real-assistant-uuid"

        # Call the function
        patch_template_with_real_assistant_ids(template)

        # Verify get_preconfigured_assistant_id_by_slug was called with the correct slug
        mock_get_assistant_id.assert_called_once_with("test-assistant")

        # Verify assistant_id was updated
        assert assistant_def.assistant_id == "real-assistant-uuid"

        # Verify yaml_config was updated
        assert template.yaml_config == "config with real-assistant-uuid reference"

    @patch('external.deployment_scripts.preconfigured_workflows.get_preconfigured_assistant_id_by_slug')
    def test_patch_template_with_unresolvable_assistants(self, mock_get_assistant_id):
        """Test patching a template with assistants that can't be resolved."""
        # Create a mock assistant definition
        assistant_def = MagicMock()
        assistant_def.assistant_id = "PRECONFIGURED:missing-assistant"

        # Create a mock template
        template = MagicMock(spec=WorkflowConfigTemplate)
        template.assistants = [assistant_def]
        template.yaml_config = "config with PRECONFIGURED:missing-assistant reference"

        # Setup mock to return None (assistant not found)
        mock_get_assistant_id.return_value = None

        # Call the function
        patch_template_with_real_assistant_ids(template)

        # Verify get_preconfigured_assistant_id_by_slug was called with the correct slug
        mock_get_assistant_id.assert_called_once_with("missing-assistant")

        # Verify assistant_id was updated with NOT FOUND prefix
        assert assistant_def.assistant_id == "NOT FOUND:missing-assistant"

        # Verify yaml_config was updated
        assert template.yaml_config == "config with NOT FOUND:missing-assistant reference"

    @patch('external.deployment_scripts.preconfigured_workflows.get_preconfigured_assistant_id_by_slug')
    def test_patch_template_with_non_preconfigured_assistants(self, mock_get_assistant_id):
        """Test patching a template with assistants that don't need resolution."""
        # Create a mock assistant definition with a regular UUID
        assistant_def = MagicMock()
        assistant_def.assistant_id = "regular-uuid"

        # Create a mock template
        template = MagicMock(spec=WorkflowConfigTemplate)
        template.assistants = [assistant_def]
        template.yaml_config = "config with regular-uuid reference"

        # Call the function
        patch_template_with_real_assistant_ids(template)

        # Verify get_preconfigured_assistant_id_by_slug was not called
        mock_get_assistant_id.assert_not_called()

        # Verify assistant_id was not changed
        assert assistant_def.assistant_id == "regular-uuid"

        # Verify yaml_config was not changed
        assert template.yaml_config == "config with regular-uuid reference"


@patch('external.deployment_scripts.preconfigured_workflows.WorkflowConfigIndexService')
@patch('external.deployment_scripts.preconfigured_workflows.workflow_service')
class TestCreatePreconfiguredWorkflow:
    def test_create_workflow_already_exists(self, mock_workflow_service, mock_index_service):
        """Test creating a flow that already exists."""
        # Setup mock to return existing workflows
        mock_index_service.find_workflows_by_filters.return_value = ["existing-workflow"]

        # Call the function
        create_preconfigured_workflow("test-slug", "Test Flow", "test-project")

        # Verify find_workflows_by_filters was called with correct parameters
        mock_index_service.find_workflows_by_filters.assert_called_once()

        # Verify get_prebuilt_workflow_by_slug was not called
        mock_workflow_service.get_prebuilt_workflow_by_slug.assert_not_called()

    def test_create_workflow_template_not_found(self, mock_workflow_service, mock_index_service):
        """Test creating a flow when the template is not found."""
        # Setup mocks
        mock_index_service.find_workflows_by_filters.return_value = []
        mock_workflow_service.get_prebuilt_workflow_by_slug.return_value = None

        # Call the function
        create_preconfigured_workflow("missing-slug", "Missing Flow", "test-project")

        # Verify find_workflows_by_filters was called
        mock_index_service.find_workflows_by_filters.assert_called_once()

        # Verify get_prebuilt_workflow_by_slug was called with correct slug
        mock_workflow_service.get_prebuilt_workflow_by_slug.assert_called_once_with("missing-slug")

    @patch('external.deployment_scripts.preconfigured_workflows.patch_template_with_real_assistant_ids')
    @patch('external.deployment_scripts.preconfigured_workflows.UserEntity')
    def test_create_workflow_success(
        self, mock_user_entity, mock_patch_template, mock_workflow_service, mock_index_service
    ):
        """Test successfully creating a flow."""
        # Setup mocks
        mock_index_service.find_workflows_by_filters.return_value = []

        # Create a mock template
        mock_template = MagicMock(spec=WorkflowConfigTemplate)
        mock_workflow_service.get_prebuilt_workflow_by_slug.return_value = mock_template

        # Create a mock workflow config
        mock_workflow_config = MagicMock()

        # Setup mock to return the workflow config when model_dump is called on the template
        mock_template.model_dump.return_value = {}

        # Setup mock user entity
        mock_user = MagicMock()
        mock_user_entity.return_value = mock_user

        # Call the function
        with patch(
            'external.deployment_scripts.preconfigured_workflows.WorkflowConfig', return_value=mock_workflow_config
        ):
            create_preconfigured_workflow("test-slug", "Test Flow", "test-project")

        # Verify find_workflows_by_filters was called
        mock_index_service.find_workflows_by_filters.assert_called_once()

        # Verify get_prebuilt_workflow_by_slug was called with correct slug
        mock_workflow_service.get_prebuilt_workflow_by_slug.assert_called_once_with("test-slug")

        # Verify patch_template_with_real_assistant_ids was called
        mock_patch_template.assert_called_once_with(mock_template)

        # Verify UserEntity was created correctly
        mock_user_entity.assert_called_once_with(user_id="system", username="system", name="system")

        # Verify created_by was set on the workflow config
        assert mock_workflow_config.created_by == mock_user

        # Verify save was called on the workflow config
        mock_workflow_config.save.assert_called_once_with(refresh=True)
