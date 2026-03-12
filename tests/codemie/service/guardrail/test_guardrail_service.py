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

from datetime import datetime
import pytest
from unittest.mock import Mock, patch

from codemie.rest_api.models.guardrail import (
    Guardrail,
    GuardrailAssignment,
    GuardrailAssignmentItem,
    GuardrailAssignmentRequestResponse,
    GuardrailEntity,
    GuardrailMode,
    GuardrailSource,
    EntityAssignmentConfig,
    EntityAssignmentItem,
    GuardrailSettings,
    BedrockGuardrailData,
    ProjectAssignmentConfig,
)
from codemie.rest_api.security.user import User
from codemie.service.guardrail.guardrail_service import GuardrailService
from codemie.service.guardrail.utils import EntityConfig
from codemie.core.models import CreatedByUser
from codemie.core.exceptions import ExtendedHTTPException
from sqlalchemy.exc import IntegrityError


@pytest.fixture
def mock_user():
    """Create a mock user for testing."""
    user = Mock(spec=User)
    user.id = "user-123"
    user.username = "testuser"
    user.name = "Test User"
    user.is_admin = False
    user.admin_project_names = ["test-project"]
    return user


@pytest.fixture
def mock_admin_user():
    """Create a mock admin user for testing."""
    user = Mock(spec=User)
    user.id = "admin-123"
    user.username = "adminuser"
    user.name = "Admin User"
    user.is_admin = True
    user.admin_project_names = []
    return user


@pytest.fixture
def mock_guardrail():
    """Create a mock guardrail for testing."""
    guardrail = Mock(spec=Guardrail)
    guardrail.id = "guardrail-123"
    guardrail.project_name = "test-project"
    guardrail.description = "Test guardrail"
    guardrail.created_by = CreatedByUser(id="user-123", username="testuser", name="Test User")
    guardrail.bedrock = BedrockGuardrailData(
        bedrock_guardrail_id="gr-123",
        bedrock_version="1",
        bedrock_name="Test Guardrail",
        bedrock_aws_settings_id="setting-123",
        bedrock_status="READY",
        bedrock_created_at=datetime.now(),
    )
    return guardrail


@pytest.fixture
def mock_assignment():
    """Create a mock guardrail assignment for testing."""
    assignment = Mock(spec=GuardrailAssignment)
    assignment.id = "assignment-123"
    assignment.guardrail_id = "guardrail-123"
    assignment.entity_type = GuardrailEntity.ASSISTANT
    assignment.entity_id = "assistant-123"
    assignment.source = GuardrailSource.INPUT
    assignment.mode = GuardrailMode.FILTERED
    assignment.scope = None
    assignment.project_name = "test-project"
    return assignment


class TestGetGuardrailAssignments:
    """Tests for get_guardrail_assignments method."""

    @patch("codemie.service.guardrail.guardrail_service.GuardrailService._load_entity_details")
    @patch("codemie.service.guardrail.guardrail_service.GuardrailRepository")
    def test_returns_empty_structure_when_no_assignments(self, mock_repo_class, mock_load_entities, mock_user):
        """Test that an empty structure is returned when there are no assignments."""
        mock_repo = Mock()
        mock_repo_class.return_value = mock_repo
        mock_repo.get_all_assignments_for_guardrail.return_value = []
        mock_load_entities.return_value = {
            GuardrailEntity.ASSISTANT: {},
            GuardrailEntity.WORKFLOW: {},
            GuardrailEntity.KNOWLEDGEBASE: {},
        }

        result = GuardrailService.get_guardrail_assignments(mock_user, "guardrail-123")

        assert result["project"]["settings"] == []
        assert result["assistants"]["settings"] == []
        assert result["assistants"]["items"] == []
        assert result["workflows"]["settings"] == []
        assert result["workflows"]["items"] == []
        assert result["datasources"]["settings"] == []
        assert result["datasources"]["items"] == []
        # Verify user was passed to _load_entity_details
        mock_load_entities.assert_called_once_with(
            mock_user,
            {
                GuardrailEntity.ASSISTANT: set(),
                GuardrailEntity.WORKFLOW: set(),
                GuardrailEntity.KNOWLEDGEBASE: set(),
            },
        )

    @patch("codemie.service.guardrail.guardrail_service.GuardrailService._load_entity_details")
    @patch("codemie.service.guardrail.guardrail_service.GuardrailRepository")
    def test_groups_project_level_assignments(self, mock_repo_class, mock_load_entities, mock_user):
        """Test that project-level assignments are grouped correctly."""
        mock_repo = Mock()
        mock_repo_class.return_value = mock_repo

        assignment1 = Mock(spec=GuardrailAssignment)
        assignment1.entity_type = GuardrailEntity.PROJECT
        assignment1.entity_id = "test-project"
        assignment1.scope = GuardrailEntity.PROJECT
        assignment1.source = GuardrailSource.INPUT
        assignment1.mode = GuardrailMode.FILTERED

        mock_repo.get_all_assignments_for_guardrail.return_value = [assignment1]
        mock_load_entities.return_value = {
            GuardrailEntity.ASSISTANT: {},
            GuardrailEntity.WORKFLOW: {},
            GuardrailEntity.KNOWLEDGEBASE: {},
        }

        result = GuardrailService.get_guardrail_assignments(mock_user, "guardrail-123")

        assert len(result["project"]["settings"]) == 1
        assert result["project"]["settings"][0]["mode"] == GuardrailMode.FILTERED
        assert result["project"]["settings"][0]["source"] == GuardrailSource.INPUT
        # Verify user was passed to _load_entity_details
        mock_load_entities.assert_called_once_with(
            mock_user,
            {
                GuardrailEntity.ASSISTANT: set(),
                GuardrailEntity.WORKFLOW: set(),
                GuardrailEntity.KNOWLEDGEBASE: set(),
            },
        )

    @patch("codemie.service.guardrail.guardrail_service.GuardrailService._build_entity_item")
    @patch("codemie.service.guardrail.guardrail_service.GuardrailService._load_entity_details")
    @patch("codemie.service.guardrail.guardrail_service.GuardrailRepository")
    def test_groups_entity_specific_assignments_by_id(
        self, mock_repo_class, mock_load_entities, mock_build_item, mock_user
    ):
        """Test that entity-specific assignments are grouped by entity_id and include entity details."""
        mock_repo = Mock()
        mock_repo_class.return_value = mock_repo

        assignment1 = Mock(spec=GuardrailAssignment)
        assignment1.entity_type = GuardrailEntity.ASSISTANT
        assignment1.entity_id = "assistant-1"
        assignment1.scope = None
        assignment1.source = GuardrailSource.INPUT
        assignment1.mode = GuardrailMode.FILTERED

        assignment2 = Mock(spec=GuardrailAssignment)
        assignment2.entity_type = GuardrailEntity.ASSISTANT
        assignment2.entity_id = "assistant-1"
        assignment2.scope = None
        assignment2.source = GuardrailSource.OUTPUT
        assignment2.mode = GuardrailMode.ALL

        mock_repo.get_all_assignments_for_guardrail.return_value = [assignment1, assignment2]

        # Mock entity details
        mock_load_entities.return_value = {
            GuardrailEntity.ASSISTANT: {
                "assistant-1": {
                    "id": "assistant-1",
                    "name": "Test Assistant",
                    "icon_url": "https://example.com/icon.png",
                }
            },
            GuardrailEntity.WORKFLOW: {},
            GuardrailEntity.KNOWLEDGEBASE: {},
        }

        # Mock the build entity item to return the expected structure
        mock_build_item.return_value = {
            "id": "assistant-1",
            "name": "Test Assistant",
            "icon_url": "https://example.com/icon.png",
            "settings": [
                {"mode": GuardrailMode.FILTERED, "source": GuardrailSource.INPUT},
                {"mode": GuardrailMode.ALL, "source": GuardrailSource.OUTPUT},
            ],
        }

        result = GuardrailService.get_guardrail_assignments(mock_user, "guardrail-123")

        assert len(result["assistants"]["items"]) == 1
        assert result["assistants"]["items"][0]["id"] == "assistant-1"
        assert result["assistants"]["items"][0]["name"] == "Test Assistant"
        assert result["assistants"]["items"][0]["icon_url"] == "https://example.com/icon.png"
        assert len(result["assistants"]["items"][0]["settings"]) == 2

        # Verify _build_entity_item was called correctly
        mock_build_item.assert_called_once()
        # Verify user was passed to _load_entity_details
        mock_load_entities.assert_called_once_with(
            mock_user,
            {
                GuardrailEntity.ASSISTANT: {"assistant-1"},
                GuardrailEntity.WORKFLOW: set(),
                GuardrailEntity.KNOWLEDGEBASE: set(),
            },
        )

    @patch("codemie.service.guardrail.guardrail_service.GuardrailService._build_entity_item")
    @patch("codemie.service.guardrail.guardrail_service.GuardrailService._load_entity_details")
    @patch("codemie.service.guardrail.guardrail_service.GuardrailRepository")
    def test_filters_none_items_from_result(self, mock_repo_class, mock_load_entities, mock_build_item, mock_user):
        """Test that None items (deleted entities) are not included in the response."""
        mock_repo = Mock()
        mock_repo_class.return_value = mock_repo

        assignment1 = Mock(spec=GuardrailAssignment)
        assignment1.entity_type = GuardrailEntity.ASSISTANT
        assignment1.entity_id = "assistant-1"
        assignment1.scope = None
        assignment1.source = GuardrailSource.INPUT
        assignment1.mode = GuardrailMode.FILTERED

        assignment2 = Mock(spec=GuardrailAssignment)
        assignment2.entity_type = GuardrailEntity.ASSISTANT
        assignment2.entity_id = "assistant-2"
        assignment2.scope = None
        assignment2.source = GuardrailSource.INPUT
        assignment2.mode = GuardrailMode.FILTERED

        mock_repo.get_all_assignments_for_guardrail.return_value = [assignment1, assignment2]

        # Mock entity details - assistant-1 not found (None), assistant-2 found
        mock_load_entities.return_value = {
            GuardrailEntity.ASSISTANT: {
                "assistant-2": {
                    "id": "assistant-2",
                    "name": "Test Assistant 2",
                    "icon_url": "https://example.com/icon2.png",
                }
            },
            GuardrailEntity.WORKFLOW: {},
            GuardrailEntity.KNOWLEDGEBASE: {},
        }

        # Mock build_entity_item - returns None for assistant-1 (not found), valid item for assistant-2
        def build_side_effect(entity_id, settings_list, entity_detail, entity_type):
            if entity_detail is None:
                return None
            return {
                "id": entity_id,
                "name": entity_detail["name"],
                "icon_url": entity_detail["icon_url"],
                "settings": settings_list,
            }

        mock_build_item.side_effect = build_side_effect

        result = GuardrailService.get_guardrail_assignments(mock_user, "guardrail-123")

        # Only assistant-2 should be in the result (assistant-1 was filtered out as None)
        assert len(result["assistants"]["items"]) == 1
        assert result["assistants"]["items"][0]["id"] == "assistant-2"
        assert result["assistants"]["items"][0]["name"] == "Test Assistant 2"


class TestLoadEntityDetails:
    """Tests for _load_entity_details method."""

    @patch("codemie.service.guardrail.guardrail_service.Ability")
    @patch("codemie.rest_api.models.index.IndexInfo.get_by_ids")
    @patch("codemie.core.workflow_models.workflow_config.WorkflowConfig.get_by_ids")
    @patch("codemie.rest_api.models.assistant.Assistant.get_by_ids_no_permission_check")
    def test_loads_all_entity_types_with_permissions(
        self,
        mock_assistant_get_by_ids,
        mock_workflow_get_by_ids,
        mock_index_get_by_ids,
        mock_ability_class,
        mock_user,
    ):
        """Test that all entity types are loaded correctly with permission checks."""
        # Mock Ability to return True for all entities
        mock_ability = Mock()
        mock_ability.can.return_value = True
        mock_ability_class.return_value = mock_ability

        # Mock Assistant loading
        mock_assistant1 = Mock()
        mock_assistant1.id = "assistant-1"
        mock_assistant1.name = "Assistant 1"
        mock_assistant1.icon_url = "https://example.com/assistant1.png"
        mock_assistant_get_by_ids.return_value = [mock_assistant1]

        # Mock Workflow loading
        mock_workflow1 = Mock()
        mock_workflow1.id = "workflow-1"
        mock_workflow1.name = "Workflow 1"
        mock_workflow1.icon_url = "https://example.com/workflow1.png"
        mock_workflow_get_by_ids.return_value = [mock_workflow1]

        # Mock IndexInfo loading
        mock_index1 = Mock()
        mock_index1.id = "datasource-1"
        mock_index1.repo_name = "Datasource 1"
        mock_index1.index_type = "knowledge_base_confluence"
        mock_index_get_by_ids.return_value = [mock_index1]

        entity_ids_to_load = {
            GuardrailEntity.ASSISTANT: {"assistant-1"},
            GuardrailEntity.WORKFLOW: {"workflow-1"},
            GuardrailEntity.KNOWLEDGEBASE: {"datasource-1"},
        }

        result = GuardrailService._load_entity_details(mock_user, entity_ids_to_load)

        # Verify assistants loaded with permission check
        assert "assistant-1" in result[GuardrailEntity.ASSISTANT]
        assert result[GuardrailEntity.ASSISTANT]["assistant-1"]["name"] == "Assistant 1"
        assert result[GuardrailEntity.ASSISTANT]["assistant-1"]["icon_url"] == "https://example.com/assistant1.png"

        # Verify workflows loaded with permission check
        assert "workflow-1" in result[GuardrailEntity.WORKFLOW]
        assert result[GuardrailEntity.WORKFLOW]["workflow-1"]["name"] == "Workflow 1"
        assert result[GuardrailEntity.WORKFLOW]["workflow-1"]["icon_url"] == "https://example.com/workflow1.png"

        # Verify datasources loaded with permission check
        assert "datasource-1" in result[GuardrailEntity.KNOWLEDGEBASE]
        assert result[GuardrailEntity.KNOWLEDGEBASE]["datasource-1"]["name"] == "Datasource 1"
        assert result[GuardrailEntity.KNOWLEDGEBASE]["datasource-1"]["index_type"] == "knowledge_base_confluence"

        # Verify Ability was checked for each entity
        assert mock_ability.can.call_count == 3

    @patch("codemie.service.guardrail.guardrail_service.Ability")
    @patch("codemie.rest_api.models.assistant.Assistant.get_by_ids_no_permission_check")
    def test_returns_empty_dict_when_user_lacks_permissions(
        self,
        mock_assistant_get_by_ids,
        mock_ability_class,
        mock_user,
    ):
        """Test that empty dict is returned for entities when user lacks READ permissions."""
        # Mock Ability to return False (no permission)
        mock_ability = Mock()
        mock_ability.can.return_value = False
        mock_ability_class.return_value = mock_ability

        # Mock Assistant loading
        mock_assistant1 = Mock()
        mock_assistant1.id = "assistant-1"
        mock_assistant1.name = "Assistant 1"
        mock_assistant1.icon_url = "https://example.com/assistant1.png"
        mock_assistant_get_by_ids.return_value = [mock_assistant1]

        entity_ids_to_load = {
            GuardrailEntity.ASSISTANT: {"assistant-1"},
            GuardrailEntity.WORKFLOW: set(),
            GuardrailEntity.KNOWLEDGEBASE: set(),
        }

        result = GuardrailService._load_entity_details(mock_user, entity_ids_to_load)

        # Verify assistant is in result but with empty dict (no permission)
        assert "assistant-1" in result[GuardrailEntity.ASSISTANT]
        assert result[GuardrailEntity.ASSISTANT]["assistant-1"] == {}

    @patch("codemie.service.guardrail.guardrail_service._get_assistant_class")
    def test_returns_empty_when_no_entities_to_load(self, mock_get_assistant, mock_user):
        """Test that empty dicts are returned when no entities need to be loaded."""
        entity_ids_to_load = {
            GuardrailEntity.ASSISTANT: set(),
            GuardrailEntity.WORKFLOW: set(),
            GuardrailEntity.KNOWLEDGEBASE: set(),
        }

        result = GuardrailService._load_entity_details(mock_user, entity_ids_to_load)

        assert result[GuardrailEntity.ASSISTANT] == {}
        assert result[GuardrailEntity.WORKFLOW] == {}
        assert result[GuardrailEntity.KNOWLEDGEBASE] == {}
        mock_get_assistant.assert_not_called()


class TestBuildEntityItem:
    """Tests for _build_entity_item method."""

    def test_builds_assistant_item_with_details(self):
        """Test that assistant item is built correctly with entity details."""
        entity_detail = {
            "id": "assistant-1",
            "name": "Test Assistant",
            "icon_url": "https://example.com/icon.png",
        }
        settings_list = [
            {"mode": GuardrailMode.FILTERED, "source": GuardrailSource.INPUT},
        ]

        result = GuardrailService._build_entity_item(
            entity_id="assistant-1",
            settings_list=settings_list,
            entity_detail=entity_detail,
            entity_type=GuardrailEntity.ASSISTANT,
        )

        assert result is not None
        assert result["id"] == "assistant-1"
        assert result["name"] == "Test Assistant"
        assert result["icon_url"] == "https://example.com/icon.png"
        assert result["settings"] == settings_list

    def test_builds_workflow_item_with_details(self):
        """Test that workflow item is built correctly with entity details."""
        entity_detail = {
            "id": "workflow-1",
            "name": "Test Workflow",
            "icon_url": "https://example.com/workflow.png",
        }
        settings_list = [
            {"mode": GuardrailMode.ALL, "source": GuardrailSource.OUTPUT},
        ]

        result = GuardrailService._build_entity_item(
            entity_id="workflow-1",
            settings_list=settings_list,
            entity_detail=entity_detail,
            entity_type=GuardrailEntity.WORKFLOW,
        )

        assert result is not None
        assert result["id"] == "workflow-1"
        assert result["name"] == "Test Workflow"
        assert result["icon_url"] == "https://example.com/workflow.png"
        assert result["settings"] == settings_list

    def test_builds_datasource_item_with_details(self):
        """Test that datasource item is built correctly with entity details."""
        entity_detail = {
            "id": "datasource-1",
            "name": "Test Datasource",
            "index_type": "knowledge_base_confluence",
        }
        settings_list = [
            {"mode": GuardrailMode.FILTERED, "source": GuardrailSource.INPUT},
        ]

        result = GuardrailService._build_entity_item(
            entity_id="datasource-1",
            settings_list=settings_list,
            entity_detail=entity_detail,
            entity_type=GuardrailEntity.KNOWLEDGEBASE,
        )

        assert result is not None
        assert result["id"] == "datasource-1"
        assert result["name"] == "Test Datasource"
        assert result["index_type"] == "knowledge_base_confluence"
        assert result["settings"] == settings_list

    def test_returns_minimal_item_when_user_lacks_permissions(self):
        """Test that minimal item (id + settings) is returned when user lacks permissions."""
        entity_detail = {}  # Empty dict indicates no permissions
        settings_list = [
            {"mode": GuardrailMode.FILTERED, "source": GuardrailSource.INPUT},
        ]

        result = GuardrailService._build_entity_item(
            entity_id="assistant-1",
            settings_list=settings_list,
            entity_detail=entity_detail,
            entity_type=GuardrailEntity.ASSISTANT,
        )

        assert result is not None
        assert result["id"] == "assistant-1"
        assert result["settings"] == settings_list
        # Should not have name or icon_url
        assert "name" not in result
        assert "icon_url" not in result

    @patch("codemie.service.guardrail.guardrail_service.GuardrailService.remove_guardrail_assignments_for_entity")
    def test_returns_none_and_cleans_up_when_entity_not_found(self, mock_remove_assignments):
        """Test that None is returned and cleanup is called when entity is not found."""
        settings_list = [
            {"mode": GuardrailMode.FILTERED, "source": GuardrailSource.INPUT},
        ]

        result = GuardrailService._build_entity_item(
            entity_id="nonexistent-assistant",
            settings_list=settings_list,
            entity_detail=None,
            entity_type=GuardrailEntity.ASSISTANT,
        )

        assert result is None
        mock_remove_assignments.assert_called_once_with(GuardrailEntity.ASSISTANT, "nonexistent-assistant")


class TestSyncGuardrailAssignmentsForEntity:
    """Tests for sync_guardrail_assignments_for_entity method."""

    @patch("codemie.service.guardrail.guardrail_service.GuardrailRepository")
    def test_no_changes_when_guardrail_assignments_is_none(self, mock_repo_class, mock_user):
        """Test that no changes are made when guardrail_assignments is None."""
        mock_repo = Mock()
        mock_repo_class.return_value = mock_repo

        GuardrailService.sync_guardrail_assignments_for_entity(
            user=mock_user,
            entity_type=GuardrailEntity.ASSISTANT,
            entity_id="assistant-123",
            entity_project_name="test-project",
            guardrail_assignments=None,
        )

        # Repository should not be called
        mock_repo.get_guardrail_assignments_for_entity.assert_not_called()

    @patch(
        "codemie.service.guardrail.guardrail_service.GuardrailService._validate_guardrail_user_and_project_permissions"
    )
    @patch("codemie.service.guardrail.guardrail_service.GuardrailRepository")
    def test_removes_all_assignments_when_empty_list(self, mock_repo_class, mock_validate, mock_user):
        """Test that all assignments are removed when an empty list is provided."""
        mock_repo = Mock()
        mock_repo_class.return_value = mock_repo

        existing_assignment = Mock(spec=GuardrailAssignment)
        existing_assignment.id = "assignment-1"
        existing_assignment.guardrail_id = "guardrail-1"
        existing_assignment.source = GuardrailSource.INPUT
        existing_assignment.mode = GuardrailMode.FILTERED
        existing_assignment.scope = None
        existing_assignment.project_name = "test-project"

        mock_repo.get_guardrail_assignments_for_entity.return_value = [existing_assignment]

        GuardrailService.sync_guardrail_assignments_for_entity(
            user=mock_user,
            entity_type=GuardrailEntity.ASSISTANT,
            entity_id="assistant-123",
            entity_project_name="test-project",
            guardrail_assignments=[],
        )

        # Should validate and delete
        mock_validate.assert_called_once_with(user=mock_user, guardrail_id="guardrail-1", project_name="test-project")
        mock_repo.remove_guardrails_assignments_by_ids.assert_called_once_with(["assignment-1"])

    @patch(
        "codemie.service.guardrail.guardrail_service.GuardrailService._validate_guardrail_user_and_project_permissions"
    )
    @patch("codemie.service.guardrail.guardrail_service.GuardrailRepository")
    def test_creates_new_assignments(self, mock_repo_class, mock_validate, mock_user):
        """Test that new assignments are created."""
        mock_repo = Mock()
        mock_repo_class.return_value = mock_repo
        mock_repo.get_guardrail_assignments_for_entity.return_value = []

        new_assignment = GuardrailAssignmentItem(
            guardrail_id="guardrail-1",
            source=GuardrailSource.INPUT,
            mode=GuardrailMode.FILTERED,
        )

        GuardrailService.sync_guardrail_assignments_for_entity(
            user=mock_user,
            entity_type=GuardrailEntity.ASSISTANT,
            entity_id="assistant-123",
            entity_project_name="test-project",
            guardrail_assignments=[new_assignment],
        )

        # Should validate and create
        mock_validate.assert_called_once()
        mock_repo.assign_guardrail_to_entity.assert_called_once()

    @patch(
        "codemie.service.guardrail.guardrail_service.GuardrailService._validate_guardrail_user_and_project_permissions"
    )
    @patch("codemie.service.guardrail.guardrail_service.GuardrailRepository")
    def test_handles_integrity_error_gracefully(self, mock_repo_class, mock_validate, mock_user):
        """Test that IntegrityError is handled gracefully (duplicate assignment)."""
        mock_repo = Mock()
        mock_repo_class.return_value = mock_repo
        mock_repo.get_guardrail_assignments_for_entity.return_value = []
        mock_repo.assign_guardrail_to_entity.side_effect = IntegrityError("", "", Exception())

        new_assignment = GuardrailAssignmentItem(
            guardrail_id="guardrail-1",
            source=GuardrailSource.INPUT,
            mode=GuardrailMode.FILTERED,
        )

        # Should not raise exception
        GuardrailService.sync_guardrail_assignments_for_entity(
            user=mock_user,
            entity_type=GuardrailEntity.ASSISTANT,
            entity_id="assistant-123",
            entity_project_name="test-project",
            guardrail_assignments=[new_assignment],
        )


class TestSyncGuardrailBulkAssignments:
    """Tests for sync_guardrail_bulk_assignments method."""

    @patch("codemie.service.guardrail.guardrail_service.GuardrailService._compute_bulk_assignment_changes")
    @patch("codemie.service.guardrail.guardrail_service.GuardrailRepository")
    def test_returns_success_count_when_no_assignments(self, mock_repo_class, mock_compute, mock_admin_user):
        """Test that success count is returned when there are no assignments to create."""
        mock_repo = Mock()
        mock_repo_class.return_value = mock_repo
        mock_repo.get_all_assignments_for_guardrail.return_value = []

        mock_compute.return_value = (set(), set(), {})

        request = GuardrailAssignmentRequestResponse()

        success, failed, errors = GuardrailService.sync_guardrail_bulk_assignments(
            guardrail_id="guardrail-123",
            guardrail_project_name="test-project",
            user=mock_admin_user,
            request=request,
        )

        assert success == 0
        assert failed == 0
        assert errors == []

    @patch(
        "codemie.service.guardrail.guardrail_service.GuardrailService._validate_and_create_assignment_in_bulk_assignments"
    )
    @patch("codemie.service.guardrail.guardrail_service.GuardrailService._compute_bulk_assignment_changes")
    @patch("codemie.service.guardrail.guardrail_service.GuardrailRepository")
    def test_creates_new_assignments_successfully(
        self, mock_repo_class, mock_compute, mock_validate_create, mock_admin_user
    ):
        """Test that new assignments are created successfully."""
        mock_repo = Mock()
        mock_repo_class.return_value = mock_repo
        mock_repo.get_all_assignments_for_guardrail.return_value = []

        keys_to_create = {
            (
                GuardrailEntity.ASSISTANT,
                "assistant-1",
                GuardrailSource.INPUT,
                GuardrailMode.FILTERED,
                None,
                "test-project",
            )
        }
        mock_compute.return_value = (keys_to_create, set(), {})

        request = GuardrailAssignmentRequestResponse()

        success, failed, errors = GuardrailService.sync_guardrail_bulk_assignments(
            guardrail_id="guardrail-123",
            guardrail_project_name="test-project",
            user=mock_admin_user,
            request=request,
        )

        assert success == 1
        assert failed == 0
        assert errors == []
        mock_validate_create.assert_called_once()

    @patch(
        "codemie.service.guardrail.guardrail_service.GuardrailService._validate_and_create_assignment_in_bulk_assignments"
    )
    @patch("codemie.service.guardrail.guardrail_service.GuardrailService._compute_bulk_assignment_changes")
    @patch("codemie.service.guardrail.guardrail_service.GuardrailRepository")
    def test_handles_integrity_error_in_bulk_assignment(
        self, mock_repo_class, mock_compute, mock_validate_create, mock_admin_user
    ):
        """Test that IntegrityError in bulk assignment increments success count."""
        mock_repo = Mock()
        mock_repo_class.return_value = mock_repo
        mock_repo.get_all_assignments_for_guardrail.return_value = []

        keys_to_create = {
            (
                GuardrailEntity.ASSISTANT,
                "assistant-1",
                GuardrailSource.INPUT,
                GuardrailMode.FILTERED,
                None,
                "test-project",
            )
        }
        mock_compute.return_value = (keys_to_create, set(), {})
        mock_validate_create.side_effect = IntegrityError("", "", Exception())

        request = GuardrailAssignmentRequestResponse()

        success, failed, errors = GuardrailService.sync_guardrail_bulk_assignments(
            guardrail_id="guardrail-123",
            guardrail_project_name="test-project",
            user=mock_admin_user,
            request=request,
        )

        assert success == 1
        assert failed == 0

    @patch(
        "codemie.service.guardrail.guardrail_service.GuardrailService._validate_and_create_assignment_in_bulk_assignments"
    )
    @patch("codemie.service.guardrail.guardrail_service.GuardrailService._compute_bulk_assignment_changes")
    @patch("codemie.service.guardrail.guardrail_service.GuardrailRepository")
    def test_handles_permission_error_in_bulk_assignment(
        self, mock_repo_class, mock_compute, mock_validate_create, mock_admin_user
    ):
        """Test that PermissionError is caught and added to errors list."""
        mock_repo = Mock()
        mock_repo_class.return_value = mock_repo
        mock_repo.get_all_assignments_for_guardrail.return_value = []

        keys_to_create = {
            (
                GuardrailEntity.ASSISTANT,
                "assistant-1",
                GuardrailSource.INPUT,
                GuardrailMode.FILTERED,
                None,
                "test-project",
            )
        }
        mock_compute.return_value = (keys_to_create, set(), {})
        mock_validate_create.side_effect = PermissionError("No permission")

        request = GuardrailAssignmentRequestResponse()

        success, failed, errors = GuardrailService.sync_guardrail_bulk_assignments(
            guardrail_id="guardrail-123",
            guardrail_project_name="test-project",
            user=mock_admin_user,
            request=request,
        )

        assert success == 0
        assert failed == 1
        assert "assignment failed" in errors[0]


class TestGetEntityGuardrailAssignments:
    """Tests for get_entity_guardrail_assignments method."""

    @patch("codemie.service.guardrail.guardrail_service.Ability")
    @patch("codemie.service.guardrail.guardrail_service.GuardrailRepository")
    def test_returns_empty_list_when_no_assignments(self, mock_repo_class, mock_ability_class, mock_user):
        """Test that an empty list is returned when there are no assignments."""
        mock_repo = Mock()
        mock_repo_class.return_value = mock_repo
        mock_repo.get_entity_guardrail_assignments.return_value = []

        result = GuardrailService.get_entity_guardrail_assignments(
            user=mock_user,
            entity_type=GuardrailEntity.ASSISTANT,
            entity_id="assistant-123",
        )

        assert result == []

    @patch("codemie.service.guardrail.guardrail_service.Ability")
    @patch("codemie.service.guardrail.guardrail_service.GuardrailRepository")
    def test_returns_assignments_with_editable_flag(
        self, mock_repo_class, mock_ability_class, mock_user, mock_assignment
    ):
        """Test that assignments are returned with editable flag."""
        mock_repo = Mock()
        mock_repo_class.return_value = mock_repo
        mock_repo.get_entity_guardrail_assignments.return_value = [mock_assignment]

        # Mock the guardrail returned by get_guardrails_by_ids
        mock_guardrail = Mock(spec=Guardrail)
        mock_guardrail.id = "guardrail-123"
        mock_guardrail.bedrock = BedrockGuardrailData(
            bedrock_guardrail_id="gr-123",
            bedrock_version="1",
            bedrock_name="Test Guardrail",
            bedrock_aws_settings_id="setting-123",
            bedrock_status="READY",
            bedrock_created_at=datetime.now(),
        )
        mock_repo.get_guardrails_by_ids.return_value = [mock_guardrail]

        mock_ability = Mock()
        mock_ability.can.return_value = True
        mock_ability_class.return_value = mock_ability

        result = GuardrailService.get_entity_guardrail_assignments(
            user=mock_user,
            entity_type=GuardrailEntity.ASSISTANT,
            entity_id="assistant-123",
        )

        assert len(result) == 1
        assert result[0].guardrail_id == "guardrail-123"
        assert result[0].editable is True


class TestApplyGuardrailsForEntity:
    """Tests for apply_guardrails_for_entity and apply_guardrails_for_entities methods."""

    @patch("codemie.service.guardrail.guardrail_service.GuardrailService.apply_guardrails_for_entities")
    def test_delegates_to_apply_guardrails_for_entities(self, mock_apply_entities):
        """Test that apply_guardrails_for_entity delegates to apply_guardrails_for_entities."""
        mock_apply_entities.return_value = ("processed text", None)

        result = GuardrailService.apply_guardrails_for_entity(
            entity_type=GuardrailEntity.ASSISTANT,
            entity_id="assistant-123",
            project_name="test-project",
            input="test input",
            source=GuardrailSource.INPUT,
        )

        assert result == ("processed text", None)
        mock_apply_entities.assert_called_once()

    @patch("codemie.service.guardrail.guardrail_service.GuardrailService.get_effective_guardrails")
    def test_returns_unchanged_when_no_guardrails(self, mock_get_guardrails):
        """Test that input is returned unchanged when no guardrails are found."""
        mock_get_guardrails.return_value = []

        entity_configs = [
            EntityConfig(entity_type=GuardrailEntity.ASSISTANT, entity_id="assistant-123", project_name="test-project")
        ]

        result, blocked = GuardrailService.apply_guardrails_for_entities(
            entity_configs=entity_configs,
            input="test input",
            source=GuardrailSource.INPUT,
        )

        assert result == "test input"
        assert blocked is None

    @patch("codemie.service.guardrail.guardrail_service.GuardrailService.get_effective_guardrails")
    def test_returns_unchanged_when_empty_input(self, mock_get_guardrails, mock_guardrail):
        """Test that empty input is returned unchanged."""
        mock_get_guardrails.return_value = [mock_guardrail]

        entity_configs = [
            EntityConfig(entity_type=GuardrailEntity.ASSISTANT, entity_id="assistant-123", project_name="test-project")
        ]

        result, blocked = GuardrailService.apply_guardrails_for_entities(
            entity_configs=entity_configs,
            input="",
            source=GuardrailSource.INPUT,
        )

        assert result == ""
        assert blocked is None

    @patch("codemie.service.guardrail.guardrail_service.GuardrailService._apply_single_guardrail_to_chunks")
    @patch("codemie.service.guardrail.guardrail_service.GuardrailService._deduplicate_guardrails")
    @patch("codemie.service.guardrail.guardrail_service.GuardrailService.get_effective_guardrails")
    def test_applies_guardrails_to_single_input(
        self, mock_get_guardrails, mock_deduplicate, mock_apply_single, mock_guardrail
    ):
        """Test that guardrails are applied to a single input string."""
        mock_get_guardrails.return_value = [mock_guardrail]
        mock_deduplicate.return_value = [mock_guardrail]
        mock_apply_single.return_value = ["modified text"]

        entity_configs = [
            EntityConfig(entity_type=GuardrailEntity.ASSISTANT, entity_id="assistant-123", project_name="test-project")
        ]

        result, blocked = GuardrailService.apply_guardrails_for_entities(
            entity_configs=entity_configs,
            input="test input",
            source=GuardrailSource.INPUT,
        )

        assert result == "modified text"
        assert blocked is None
        mock_apply_single.assert_called_once()

    @patch("codemie.service.guardrail.guardrail_service.GuardrailService._apply_single_guardrail_to_chunks")
    @patch("codemie.service.guardrail.guardrail_service.GuardrailService._deduplicate_guardrails")
    @patch("codemie.service.guardrail.guardrail_service.GuardrailService.get_effective_guardrails")
    def test_returns_blocked_when_content_blocked(
        self, mock_get_guardrails, mock_deduplicate, mock_apply_single, mock_guardrail
    ):
        """Test that blocked response is returned when content is blocked."""
        mock_get_guardrails.return_value = [mock_guardrail]
        mock_deduplicate.return_value = [mock_guardrail]
        blocked_reasons = [{"policy": "contentPolicy", "reason": "BLOCKED"}]
        mock_apply_single.return_value = ("BLOCKED", blocked_reasons)

        entity_configs = [
            EntityConfig(entity_type=GuardrailEntity.ASSISTANT, entity_id="assistant-123", project_name="test-project")
        ]

        result, blocked = GuardrailService.apply_guardrails_for_entities(
            entity_configs=entity_configs,
            input="test input",
            source=GuardrailSource.INPUT,
        )

        assert result == "BLOCKED"
        assert blocked == blocked_reasons


class TestGetEffectiveGuardrails:
    """Tests for get_effective_guardrails and get_effective_guardrails_for_entity methods."""

    @patch("codemie.service.guardrail.guardrail_service.Guardrail.get_by_ids")
    @patch("codemie.service.guardrail.guardrail_service.GuardrailRepository")
    def test_returns_empty_list_when_no_guardrails(self, mock_repo_class, mock_get_by_ids):
        """Test that an empty list is returned when no guardrails are found."""
        mock_repo = Mock()
        mock_repo_class.return_value = mock_repo
        mock_repo.get_all_effective_guardrail_ids_for_entity.return_value = []

        entity_configs = [
            EntityConfig(entity_type=GuardrailEntity.ASSISTANT, entity_id="assistant-123", project_name="test-project")
        ]

        result = GuardrailService.get_effective_guardrails(
            entity_configs=entity_configs,
            source=GuardrailSource.INPUT,
        )

        assert result == []
        mock_get_by_ids.assert_not_called()

    @patch("codemie.service.guardrail.guardrail_service.Guardrail.get_by_ids")
    @patch("codemie.service.guardrail.guardrail_service.GuardrailRepository")
    def test_returns_guardrails_for_entity(self, mock_repo_class, mock_get_by_ids, mock_guardrail):
        """Test that guardrails are returned for an entity."""
        mock_repo = Mock()
        mock_repo_class.return_value = mock_repo
        mock_repo.get_all_effective_guardrail_ids_for_entity.return_value = ["guardrail-123"]
        mock_get_by_ids.return_value = [mock_guardrail]

        entity_configs = [
            EntityConfig(entity_type=GuardrailEntity.ASSISTANT, entity_id="assistant-123", project_name="test-project")
        ]

        result = GuardrailService.get_effective_guardrails(
            entity_configs=entity_configs,
            source=GuardrailSource.INPUT,
        )

        assert len(result) == 1
        assert result[0].id == "guardrail-123"

    @patch("codemie.service.guardrail.guardrail_service.GuardrailService.get_effective_guardrails")
    def test_get_effective_guardrails_for_entity_delegates(self, mock_get_effective):
        """Test that get_effective_guardrails_for_entity delegates to get_effective_guardrails."""
        mock_get_effective.return_value = []

        result = GuardrailService.get_effective_guardrails_for_entity(
            entity_type=GuardrailEntity.ASSISTANT,
            entity_id="assistant-123",
            project_name="test-project",
            source=GuardrailSource.INPUT,
        )

        assert result == []
        mock_get_effective.assert_called_once()


class TestValidateGuardrailUserAndProjectPermissions:
    """Tests for _validate_guardrail_user_and_project_permissions method."""

    @patch("codemie.service.guardrail.guardrail_service.Guardrail.find_by_id")
    def test_raises_error_when_guardrail_not_found(self, mock_find_by_id, mock_user):
        """Test that an error is raised when guardrail is not found."""
        mock_find_by_id.return_value = None

        with pytest.raises(ExtendedHTTPException) as exc_info:
            GuardrailService._validate_guardrail_user_and_project_permissions(
                user=mock_user,
                guardrail_id="nonexistent-guardrail",
                project_name="test-project",
            )

        assert exc_info.value.code == 404
        assert "Guardrail not found" in exc_info.value.message

    @patch("codemie.service.guardrail.guardrail_service.Guardrail.find_by_id")
    def test_raises_error_on_cross_project_assignment(self, mock_find_by_id, mock_user, mock_guardrail):
        """Test that an error is raised on cross-project assignment."""
        mock_guardrail.project_name = "different-project"
        mock_find_by_id.return_value = mock_guardrail

        with pytest.raises(ExtendedHTTPException) as exc_info:
            GuardrailService._validate_guardrail_user_and_project_permissions(
                user=mock_user,
                guardrail_id="guardrail-123",
                project_name="test-project",
            )

        assert exc_info.value.code == 400
        assert "Cross-project assignment not allowed" in exc_info.value.message

    @patch("codemie.service.guardrail.guardrail_service.Ability")
    @patch("codemie.service.guardrail.guardrail_service.Guardrail.find_by_id")
    def test_raises_error_when_user_lacks_permission(
        self, mock_find_by_id, mock_ability_class, mock_user, mock_guardrail
    ):
        """Test that an error is raised when user lacks permission."""
        mock_find_by_id.return_value = mock_guardrail

        mock_ability = Mock()
        mock_ability.can.return_value = False
        mock_ability_class.return_value = mock_ability

        with pytest.raises(ExtendedHTTPException) as exc_info:
            GuardrailService._validate_guardrail_user_and_project_permissions(
                user=mock_user,
                guardrail_id="guardrail-123",
                project_name="test-project",
            )

        assert exc_info.value.code == 403
        assert "Permission denied" in exc_info.value.message


class TestDeduplicateGuardrails:
    """Tests for _deduplicate_guardrails method."""

    def test_returns_empty_list_for_empty_input(self):
        """Test that an empty list is returned for empty input."""
        result = GuardrailService._deduplicate_guardrails([])
        assert result == []

    def test_deduplicates_by_bedrock_id_and_version(self, mock_guardrail):
        """Test that guardrails are deduplicated by bedrock_guardrail_id and version."""
        guardrail1 = Mock(spec=Guardrail)
        guardrail1.bedrock = BedrockGuardrailData(
            bedrock_guardrail_id="gr-123",
            bedrock_version="1",
            bedrock_name="Test",
            bedrock_aws_settings_id="setting-123",
            bedrock_status="READY",
            bedrock_created_at=datetime.now(),
        )

        guardrail2 = Mock(spec=Guardrail)
        guardrail2.bedrock = BedrockGuardrailData(
            bedrock_guardrail_id="gr-123",
            bedrock_version="1",
            bedrock_name="Test Duplicate",
            bedrock_aws_settings_id="setting-123",
            bedrock_status="READY",
            bedrock_created_at=datetime.now(),
        )

        result = GuardrailService._deduplicate_guardrails([guardrail1, guardrail2])

        assert len(result) == 1

    def test_includes_non_bedrock_guardrails(self):
        """Test that guardrails without bedrock data are included."""
        guardrail = Mock(spec=Guardrail)
        guardrail.bedrock = None

        result = GuardrailService._deduplicate_guardrails([guardrail])

        assert len(result) == 1


class TestComputeBulkAssignmentChanges:
    """Tests for _compute_bulk_assignment_changes method."""

    @patch("codemie.service.guardrail.guardrail_service.GuardrailService._build_desired_bulk_assignment_keys")
    def test_returns_keys_to_create_when_no_existing(self, mock_build_desired):
        """Test that all desired keys are returned as keys_to_create when no existing assignments."""
        desired_keys = {
            (
                GuardrailEntity.ASSISTANT,
                "assistant-1",
                GuardrailSource.INPUT,
                GuardrailMode.FILTERED,
                None,
                "test-project",
            )
        }
        mock_build_desired.return_value = desired_keys

        request = GuardrailAssignmentRequestResponse()

        keys_to_create, keys_to_delete, assignments_map = GuardrailService._compute_bulk_assignment_changes(
            request=request,
            project_name="test-project",
            existing_assignments=[],
        )

        assert keys_to_create == desired_keys
        assert keys_to_delete == set()
        assert assignments_map == {}

    @patch("codemie.service.guardrail.guardrail_service.GuardrailService._build_desired_bulk_assignment_keys")
    def test_returns_keys_to_delete_when_not_in_request(self, mock_build_desired):
        """Test that existing keys not in request are returned as keys_to_delete."""
        mock_build_desired.return_value = set()

        existing_assignment = Mock(spec=GuardrailAssignment)
        existing_assignment.id = "assignment-1"
        existing_assignment.entity_type = GuardrailEntity.ASSISTANT
        existing_assignment.entity_id = "assistant-1"
        existing_assignment.source = GuardrailSource.INPUT
        existing_assignment.mode = GuardrailMode.FILTERED
        existing_assignment.scope = None
        existing_assignment.project_name = "test-project"

        request = GuardrailAssignmentRequestResponse()

        keys_to_create, keys_to_delete, assignments_map = GuardrailService._compute_bulk_assignment_changes(
            request=request,
            project_name="test-project",
            existing_assignments=[existing_assignment],
        )

        assert keys_to_create == set()
        assert len(keys_to_delete) == 1
        assert "assignment-1" in assignments_map.values()


class TestBuildDesiredBulkAssignmentKeys:
    """Tests for _build_desired_bulk_assignment_keys method."""

    def test_builds_project_level_keys(self):
        """Test that project-level assignment keys are built correctly."""
        request = GuardrailAssignmentRequestResponse(
            project=ProjectAssignmentConfig(
                settings=[GuardrailSettings(source=GuardrailSource.INPUT, mode=GuardrailMode.FILTERED)]
            )
        )

        result = GuardrailService._build_desired_bulk_assignment_keys(request, "test-project")

        assert len(result) == 1
        assert (
            GuardrailEntity.PROJECT,
            "test-project",
            GuardrailSource.INPUT,
            GuardrailMode.FILTERED,
            GuardrailEntity.PROJECT,
            "test-project",
        ) in result

    def test_builds_entity_type_project_level_keys(self):
        """Test that entity type project-level keys are built correctly."""
        request = GuardrailAssignmentRequestResponse(
            assistants=EntityAssignmentConfig(
                settings=[GuardrailSettings(source=GuardrailSource.INPUT, mode=GuardrailMode.FILTERED)]
            )
        )

        result = GuardrailService._build_desired_bulk_assignment_keys(request, "test-project")

        assert len(result) == 1
        assert (
            GuardrailEntity.PROJECT,
            "test-project",
            GuardrailSource.INPUT,
            GuardrailMode.FILTERED,
            GuardrailEntity.ASSISTANT,
            "test-project",
        ) in result

    def test_builds_individual_entity_keys(self):
        """Test that individual entity assignment keys are built correctly."""
        request = GuardrailAssignmentRequestResponse(
            assistants=EntityAssignmentConfig(
                items=[
                    EntityAssignmentItem(
                        id="assistant-1",
                        settings=[GuardrailSettings(source=GuardrailSource.INPUT, mode=GuardrailMode.FILTERED)],
                    )
                ]
            )
        )

        result = GuardrailService._build_desired_bulk_assignment_keys(request, "test-project")

        assert len(result) == 1
        assert (
            GuardrailEntity.ASSISTANT,
            "assistant-1",
            GuardrailSource.INPUT,
            GuardrailMode.FILTERED,
            None,
            "test-project",
        ) in result


class TestValidateAndCreateAssignmentInBulkAssignments:
    """Tests for _validate_and_create_assignment_in_bulk_assignments method."""

    @patch("codemie.service.guardrail.guardrail_service.GuardrailRepository")
    def test_raises_error_for_non_admin_project_assignment(self, mock_repo_class, mock_user):
        """Test that PermissionError is raised for non-admin user trying to assign to project."""
        mock_repo = Mock()
        mock_repo_class.return_value = mock_repo
        mock_user.is_admin = False
        mock_user.admin_project_names = []

        with pytest.raises(PermissionError) as exc_info:
            GuardrailService._validate_and_create_assignment_in_bulk_assignments(
                repo=mock_repo,
                user=mock_user,
                guardrail_id="guardrail-123",
                guardrail_project_name="test-project",
                entity_type=GuardrailEntity.PROJECT,
                entity_id="test-project",
                source=GuardrailSource.INPUT,
                mode=GuardrailMode.FILTERED,
                scope=None,
            )

        assert "don't have permission" in str(exc_info.value)

    @patch("codemie.service.guardrail.guardrail_service.Ability")
    @patch("codemie.service.guardrail.guardrail_service._get_assistant_class")
    @patch("codemie.service.guardrail.guardrail_service.GuardrailRepository")
    def test_raises_error_when_entity_not_found(
        self, mock_repo_class, mock_get_assistant, mock_ability_class, mock_user
    ):
        """Test that ValueError is raised when entity is not found."""
        mock_repo = Mock()
        mock_repo_class.return_value = mock_repo

        mock_assistant_class = Mock()
        mock_assistant_class.find_by_id.return_value = None
        mock_get_assistant.return_value = mock_assistant_class

        with pytest.raises(ValueError) as exc_info:
            GuardrailService._validate_and_create_assignment_in_bulk_assignments(
                repo=mock_repo,
                user=mock_user,
                guardrail_id="guardrail-123",
                guardrail_project_name="test-project",
                entity_type=GuardrailEntity.ASSISTANT,
                entity_id="nonexistent-assistant",
                source=GuardrailSource.INPUT,
                mode=GuardrailMode.FILTERED,
                scope=None,
            )

        assert "not found" in str(exc_info.value)

    @patch("codemie.service.guardrail.guardrail_service.Ability")
    @patch("codemie.service.guardrail.guardrail_service._get_assistant_class")
    @patch("codemie.service.guardrail.guardrail_service.GuardrailRepository")
    def test_creates_assignment_successfully(self, mock_repo_class, mock_get_assistant, mock_ability_class, mock_user):
        """Test that assignment is created successfully."""
        mock_repo = Mock()
        mock_repo_class.return_value = mock_repo

        mock_assistant = Mock()
        mock_assistant.project = "test-project"
        mock_assistant_class = Mock()
        mock_assistant_class.find_by_id.return_value = mock_assistant
        mock_get_assistant.return_value = mock_assistant_class

        mock_ability = Mock()
        mock_ability.can.return_value = True
        mock_ability_class.return_value = mock_ability

        GuardrailService._validate_and_create_assignment_in_bulk_assignments(
            repo=mock_repo,
            user=mock_user,
            guardrail_id="guardrail-123",
            guardrail_project_name="test-project",
            entity_type=GuardrailEntity.ASSISTANT,
            entity_id="assistant-123",
            source=GuardrailSource.INPUT,
            mode=GuardrailMode.FILTERED,
            scope=None,
        )

        mock_repo.assign_guardrail_to_entity.assert_called_once()


class TestApplySingleGuardrailToChunks:
    """Tests for _apply_single_guardrail_to_chunks method."""

    @patch("codemie.service.guardrail.guardrail_service.batch_content")
    def test_returns_processed_chunks_on_success(self, mock_batch, mock_guardrail):
        """Test that processed chunks are returned on successful guardrail application."""
        mock_batch.return_value = [[{"text": {"text": "chunk1"}}, {"text": {"text": "chunk2"}}]]

        mock_bedrock_service = Mock()
        mock_bedrock_service.apply_guardrail.return_value = {
            "action": "NONE",
            "outputs": [{"text": "modified1"}, {"text": "modified2"}],
        }

        result = GuardrailService._apply_single_guardrail_to_chunks(
            guardrail=mock_guardrail,
            chunks=["chunk1", "chunk2"],
            source=GuardrailSource.INPUT,
            output_scope="INTERVENTIONS",
            is_single_input=False,
            bedrock_service=mock_bedrock_service,
        )

        assert result == ["modified1", "modified2"]

    @patch("codemie.service.guardrail.guardrail_service.batch_content")
    def test_returns_blocked_response_when_blocked(self, mock_batch, mock_guardrail):
        """Test that blocked response is returned when content is blocked."""
        mock_batch.return_value = [[{"text": {"text": "chunk1"}}]]

        mock_bedrock_service = Mock()
        mock_bedrock_service.apply_guardrail.return_value = {
            "action": "GUARDRAIL_INTERVENED",
            "actionReason": "Content blocked due to policy violation",
            "outputs": [{"text": "BLOCKED"}],
            "assessments": [],
        }

        result = GuardrailService._apply_single_guardrail_to_chunks(
            guardrail=mock_guardrail,
            chunks=["chunk1"],
            source=GuardrailSource.INPUT,
            output_scope="INTERVENTIONS",
            is_single_input=True,
            bedrock_service=mock_bedrock_service,
        )

        assert isinstance(result, tuple)
        assert result[0] == "BLOCKED"
        assert isinstance(result[1], list)


class TestCheckForBlockedResponse:
    """Tests for _check_for_blocked_response method."""

    def test_returns_none_when_not_blocked(self):
        """Test that None is returned when content is not blocked."""
        response = {
            "action": "NONE",
            "actionReason": "",
        }

        result = GuardrailService._check_for_blocked_response(
            response=response,
            is_single_input=True,
            original_chunks_count=1,
        )

        assert result is None

    def test_returns_blocked_text_for_single_input(self):
        """Test that blocked text is returned for single input."""
        response = {
            "action": "GUARDRAIL_INTERVENED",
            "actionReason": "Content blocked",
            "outputs": [{"text": "BLOCKED"}],
            "assessments": [],
        }

        result = GuardrailService._check_for_blocked_response(
            response=response,
            is_single_input=True,
            original_chunks_count=1,
        )

        assert result is not None
        assert result[0] == "BLOCKED"

    def test_returns_blocked_list_for_multiple_inputs(self):
        """Test that blocked list is returned for multiple inputs."""
        response = {
            "action": "GUARDRAIL_INTERVENED",
            "actionReason": "Content blocked",
            "outputs": [{"text": "BLOCKED"}],
            "assessments": [],
        }

        result = GuardrailService._check_for_blocked_response(
            response=response,
            is_single_input=False,
            original_chunks_count=3,
        )

        assert result is not None
        assert result[0] == ["BLOCKED", "BLOCKED", "BLOCKED"]


class TestExtractBlockedReasons:
    """Tests for _extract_blocked_reasons and _extract_policy_blocked_reasons methods."""

    def test_extracts_topic_policy_blocks(self):
        """Test that topic policy blocks are extracted correctly."""
        response = {
            "assessments": [
                {
                    "topicPolicy": {
                        "topics": [
                            {
                                "action": "BLOCKED",
                                "type": "POLITICS",
                                "name": "Political Content",
                                "detected": True,
                            }
                        ]
                    }
                }
            ]
        }

        result = GuardrailService._extract_blocked_reasons(response)

        assert len(result) == 1
        assert result[0]["policy"] == "topicPolicy"
        assert result[0]["type"] == "POLITICS"

    def test_extracts_content_policy_blocks(self):
        """Test that content policy blocks are extracted correctly."""
        response = {
            "assessments": [
                {
                    "contentPolicy": {
                        "filters": [
                            {
                                "action": "BLOCKED",
                                "type": "HATE",
                                "detected": True,
                                "confidence": "HIGH",
                            }
                        ]
                    }
                }
            ]
        }

        result = GuardrailService._extract_blocked_reasons(response)

        assert len(result) == 1
        assert result[0]["policy"] == "contentPolicy"
        assert result[0]["type"] == "HATE"

    def test_extracts_multiple_policy_types(self):
        """Test that multiple policy types are extracted correctly."""
        response = {
            "assessments": [
                {
                    "topicPolicy": {
                        "topics": [
                            {
                                "action": "BLOCKED",
                                "type": "POLITICS",
                                "name": "Political",
                                "detected": True,
                            }
                        ]
                    },
                    "contentPolicy": {
                        "filters": [
                            {
                                "action": "BLOCKED",
                                "type": "HATE",
                                "detected": True,
                                "confidence": "HIGH",
                            }
                        ]
                    },
                }
            ]
        }

        result = GuardrailService._extract_blocked_reasons(response)

        assert len(result) == 2
