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
from unittest.mock import patch

from pydantic import ValidationError

from codemie.rest_api.models.assistant import (
    ToolKitDetails,
    ToolDetails,
    MCPServerDetails,
    Assistant,
    AssistantRequest,
    AssistantListResponse,
)
from codemie.rest_api.models.category import Category
from codemie.rest_api.models.settings import SettingsBase
from codemie.rest_api.security.user import User


@pytest.fixture
def empty_tools():
    return []


@pytest.fixture
def tools_without_settings():
    return [ToolDetails(name="tool1"), ToolDetails(name="tool2")]


@pytest.fixture
def tools_with_none_settings():
    return [ToolDetails(name="tool1", settings=None), ToolDetails(name="tool2", settings=None)]


@pytest.fixture
def single_tool_git():
    settings = SettingsBase(id="settings_id_1", credential_type="Git", project_name="test_project", user_id="user1")
    return [ToolDetails(name="tool1", settings=settings)]


@pytest.fixture
def multiple_different_types():
    return [
        ToolDetails(
            name="tool1",
            settings=SettingsBase(
                id="settings_id_1", credential_type="Jira", project_name="test_project", user_id="user1"
            ),
        ),
        ToolDetails(
            name="tool2",
            settings=SettingsBase(
                id="settings_id_2", credential_type="Git", project_name="test_project", user_id="user2"
            ),
        ),
    ]


@pytest.fixture
def same_credential_types():
    return [
        ToolDetails(
            name="tool1",
            settings=SettingsBase(
                id="settings_id_1", credential_type="Git", project_name="test_project", user_id="user1"
            ),
        ),
        ToolDetails(
            name="tool2",
            settings=SettingsBase(
                id="settings_id_2", credential_type="Git", project_name="test_project", user_id="user2"
            ),
        ),
    ]


@pytest.fixture
def mixed_tools():
    settings = SettingsBase(id="settings_id", credential_type="Git", project_name="test_project", user_id="user1")
    return [ToolDetails(name="tool_with_settings", settings=settings), ToolDetails(name="tool_without_settings")]


test_cases = [
    # (fixture_name, expected_tool_configs)
    ("empty_tools", []),
    ("tools_without_settings", []),
    ("tools_with_none_settings", []),
    ("single_tool_git", [{"name": "Git", "integration_id": "settings_id_1"}]),
    (
        "multiple_different_types",
        [{"name": "Jira", "integration_id": "settings_id_1"}, {"name": "Git", "integration_id": "settings_id_2"}],
    ),
    (
        "same_credential_types",
        [{"name": "Git", "integration_id": "settings_id_1"}, {"name": "Git", "integration_id": "settings_id_2"}],
    ),
    ("mixed_tools", [{"name": "Git", "integration_id": "settings_id"}]),
]


@pytest.mark.parametrize("fixture_name,expected_configs", test_cases)
def test_get_tool_configs(request, fixture_name, expected_configs):
    """Test get_tool_configs with various tool configurations."""
    tools_input = request.getfixturevalue(fixture_name)

    toolkit = ToolKitDetails(toolkit="test_toolkit", tools=tools_input, label="Test Toolkit")
    result = toolkit.get_tool_configs()

    assert len(result) == len(expected_configs)

    for i, expected in enumerate(expected_configs):
        if expected_configs:  # Only check if we expect configs
            assert result[i].name == expected["name"]
            assert result[i].integration_id == expected["integration_id"]


class TestMCPServerDetails:
    """Tests for MCPServerDetails model and its configuration fields."""

    def test_mcp_server_details_basic_creation(self):
        """Test basic MCPServerDetails creation with required fields."""
        server = MCPServerDetails(name="test_server", command="python", arguments="-m test_module")

        assert server.name == "test_server"
        assert server.command == "python"
        assert server.arguments == "-m test_module"
        # Check default values
        assert server.resolve_dynamic_values_in_arguments is False
        assert server.settings is None
        assert server.integration_alias is None
        assert server.mcp_connect_auth_token is None

    def test_mcp_server_details_resolve_dynamic_values_in_arguments_default(self):
        """Test that resolve_dynamic_values_in_arguments defaults to False."""
        server = MCPServerDetails(name="test_server", command="python", arguments="--help")

        assert server.resolve_dynamic_values_in_arguments is False

    def test_mcp_server_details_resolve_dynamic_values_in_arguments_explicit_true(self):
        """Test setting resolve_dynamic_values_in_arguments to True."""
        server = MCPServerDetails(
            name="test_server", command="python", arguments="--help", resolve_dynamic_values_in_arguments=True
        )

        assert server.resolve_dynamic_values_in_arguments is True

    def test_mcp_server_details_resolve_dynamic_values_in_arguments_explicit_false(self):
        """Test setting resolve_dynamic_values_in_arguments to False."""
        server = MCPServerDetails(
            name="test_server", command="python", arguments="--help", resolve_dynamic_values_in_arguments=False
        )

        assert server.resolve_dynamic_values_in_arguments is False

    def test_mcp_server_details_serialization_includes_new_field(self):
        """Test that serialization includes the new field."""
        server = MCPServerDetails(
            name="test_server", command="python", arguments="--help", resolve_dynamic_values_in_arguments=True
        )

        # Test model_dump includes the field
        data = server.model_dump()
        assert "resolve_dynamic_values_in_arguments" in data
        assert data["resolve_dynamic_values_in_arguments"] is True

    def test_mcp_server_details_deserialization_handles_new_field(self):
        """Test that deserialization handles the new field correctly."""
        # Test with field present
        data_with_field = {
            "name": "test_server",
            "command": "python",
            "arguments": "--help",
            "resolve_dynamic_values_in_arguments": True,
        }

        server = MCPServerDetails(**data_with_field)
        assert server.resolve_dynamic_values_in_arguments is True

        # Test without field (should use default)
        data_without_field = {"name": "test_server", "command": "python", "arguments": "--help"}

        server = MCPServerDetails(**data_without_field)
        assert server.resolve_dynamic_values_in_arguments is False

    def test_mcp_server_details_all_optional_fields(self):
        """Test MCPServerDetails with all optional fields set."""
        from codemie.rest_api.models.settings import CredentialTypes

        settings = SettingsBase(project_name="test_project", credential_type=CredentialTypes.GIT)
        auth_token = SettingsBase(project_name="test_project", credential_type=CredentialTypes.PLUGIN)

        server = MCPServerDetails(
            name="test_server",
            description="Test server description",
            command="python",
            arguments="-m test_module --config={{config_path}}",
            settings=settings,
            integration_alias="test_alias",
            mcp_connect_auth_token=auth_token,
            resolve_dynamic_values_in_arguments=True,
        )

        assert server.name == "test_server"
        assert server.description == "Test server description"
        assert server.command == "python"
        assert server.arguments == "-m test_module --config={{config_path}}"
        assert server.settings is not None
        assert server.integration_alias == "test_alias"
        assert server.mcp_connect_auth_token is not None
        assert server.resolve_dynamic_values_in_arguments is True

    def test_mcp_server_details_with_dynamic_args(self):
        """Test MCPServerDetails with dynamic arguments for template resolution."""
        server = MCPServerDetails(
            name="dynamic_server",
            command="node",
            arguments="server.js --port={{port}} --env={{environment}}",
            resolve_dynamic_values_in_arguments=True,
        )

        assert server.name == "dynamic_server"
        assert server.command == "node"
        assert server.arguments == "server.js --port={{port}} --env={{environment}}"
        assert server.resolve_dynamic_values_in_arguments is True


@pytest.mark.parametrize(
    "id_,name,desc,desc_kw",
    [
        ("engineering", "Engineering", "For supporting developers and engineering workflows", "developers"),
        ("data-analytics", "Data Analytics", "For data analysis and visualization tasks", "data analysis"),
        ("business-analysis", "Business Analysis", "For analyzing business needs and requirements", "business needs"),
    ],
)
def test_assistant_category_creation_and_serialization(id_, name, desc, desc_kw):
    category = Category(id=id_, name=name, description=desc)
    data = category.model_dump()
    assert category.id == id_ and category.name == name and desc_kw in category.description
    assert data["id"] == id_ and data["name"] == name and desc_kw in data["description"]


@pytest.mark.parametrize(
    "category_id,category_name,description",
    [
        ("engineering", "Engineering", "Software development support"),
        ("data-analytics", "Data Analytics", "Data analysis and insights"),
        ("ui-ux-design", "UI/UX Design", "User interface and experience design"),
        ("migration-modernization", "Migration & Modernization", "Cloud migrations and legacy updates"),
    ],
)
def test_assistant_category_parametrized(category_id, category_name, description):
    category = Category(id=category_id, name=category_name, description=description)
    assert category.id == category_id
    assert category.name == category_name
    assert category.description == description


@pytest.fixture
def sample_user():
    return User(id="123", username="testuser", name="Test User", project_names=["demo"])


@pytest.fixture
def assistant_request_with_categories():
    return AssistantRequest(
        name="Test Assistant",
        description="Test Description",
        system_prompt="Test Prompt",
        project="demo",
        llm_model_type="gpt-4",
        categories=["engineering", "data-analytics"],
    )


@pytest.fixture
def assistant_request_without_categories():
    return AssistantRequest(
        name="Test Assistant",
        description="Test Description",
        system_prompt="Test Prompt",
        project="demo",
        llm_model_type="gpt-4",
    )


@pytest.mark.parametrize(
    "categories,expected",
    [([], []), (["engineering", "data-analytics"], ["engineering", "data-analytics"])],
)
def test_assistant_categories_assignment(categories, expected):
    assistant = Assistant(
        name="Test Assistant",
        description="Test Description",
        system_prompt="Test Prompt",
        project="demo",
        categories=categories,
    )
    assert assistant.categories == expected


def test_assistant_categories_max_length_validation():
    categories = ["engineering", "data-analytics", "business-analysis", "ui-ux-design"]
    with pytest.raises(ValidationError):
        AssistantRequest(
            name="Test Assistant",
            description="Test Description",
            system_prompt="Test Prompt",
            project="demo",
            llm_model_type="gpt-4",
            categories=categories,
        )


def test_assistant_categories_serialization():
    categories = ["engineering", "data-analytics"]

    # Mock the category service to return the categories as valid
    with patch('codemie.service.assistant.category_service.category_service.filter_valid_category_ids') as mock_filter:
        mock_filter.return_value = categories

        assistant = Assistant(
            name="Test Assistant",
            description="Test Description",
            system_prompt="Test Prompt",
            project="demo",
            categories=categories,
        )
        data = assistant.model_dump()
        assert data["categories"] == categories
        mock_filter.assert_called_once_with(categories)


def test_assistant_mapping_from_request(assistant_request_with_categories):
    assistant = Assistant(
        name="Test Assistant", description="Test Description", system_prompt="Test Prompt", project="demo"
    )
    assistant._map_assistant_request(assistant_request_with_categories)
    assert assistant.categories == ["engineering", "data-analytics"]

    with patch('codemie.service.assistant.category_service.category_service.validate_category_ids') as mock_validate:
        mock_validate.return_value = assistant.categories
        result = assistant._check_categories()
        assert result == ""
        mock_validate.assert_called_once_with(["engineering", "data-analytics"])


def test_assistant_mapping_without_categories(assistant_request_without_categories):
    assistant = Assistant(
        name="Test Assistant",
        description="Test Description",
        system_prompt="Test Prompt",
        project="demo",
        categories=["existing-category"],
    )
    assistant._map_assistant_request(assistant_request_without_categories)
    assert assistant.categories == ["existing-category"]


@pytest.mark.parametrize(
    "categories,side_effect,expected_in_result",
    [
        (["engineering", "data-analytics"], None, ""),
        (
            ["engineering", "invalid-category"],
            ValueError("Invalid category IDs: ['invalid-category']"),
            "Invalid category IDs",
        ),
    ],
)
def test_check_categories_validation(categories, side_effect, expected_in_result):
    with patch('codemie.service.assistant.category_service.category_service.validate_category_ids') as mock_validate:
        if side_effect:
            mock_validate.side_effect = side_effect
        else:
            mock_validate.return_value = categories
        assistant = Assistant(
            name="Test Assistant",
            description="Test Description",
            system_prompt="Test Prompt",
            project="demo",
            categories=categories,
        )
        result = assistant._check_categories()
        assert expected_in_result in (result or "")


@pytest.mark.parametrize("categories", [[], None])
def test_check_categories_empty_none(categories):
    assistant = Assistant(
        name="Test Assistant",
        description="Test Description",
        system_prompt="Test Prompt",
        project="demo",
        categories=categories or [],
    )
    result = assistant._check_categories()
    assert result is None


@pytest.mark.parametrize(
    "categories,should_validate",
    [
        ([], False),
        (["engineering"], True),
        (["engineering", "data-analytics"], True),
        (["engineering", "data-analytics", "business-analysis"], True),
    ],
)
def test_check_categories_parametrized(categories, should_validate):
    with patch('codemie.service.assistant.category_service.category_service.validate_category_ids') as mock_validate:
        mock_validate.return_value = categories if categories else []
        assistant = Assistant(
            name="Test Assistant",
            description="Test Description",
            system_prompt="Test Prompt",
            project="demo",
            categories=categories,
        )
        result = assistant._check_categories()
        if should_validate:
            assert result == ""
            mock_validate.assert_called_once_with(categories)
        else:
            assert result is None
            mock_validate.assert_not_called()


def test_validate_assistant_ids_empty_list():
    """Test that validation passes when assistant_ids is empty."""
    parent_assistant = Assistant(
        name="Parent Assistant",
        description="Parent Description",
        system_prompt="Parent Prompt",
        project="demo",
        assistant_ids=[],
    )
    parent_assistant.id = "parent-id"

    error = parent_assistant._validate_assistant_ids()
    assert error is None


def test_validate_assistant_ids_no_user_context():
    """Test that validation fails when user context is not available."""
    with patch('codemie.rest_api.security.user_context.get_current_user') as mock_get_user:
        mock_get_user.return_value = None

        parent_assistant = Assistant(
            name="Parent Assistant",
            description="Parent Description",
            system_prompt="Parent Prompt",
            project="demo",
            assistant_ids=["sub-assistant-id"],
        )
        parent_assistant.id = "parent-id"

        error = parent_assistant._validate_assistant_ids()
        assert error == "User context is required for assistant validation"


def test_validate_assistant_ids_valid_same_project():
    """Test that validation passes when sub-assistant is in the same project."""
    with (
        patch.object(Assistant, 'find_by_id') as mock_find,
        patch('codemie.rest_api.security.user_context.get_current_user') as mock_get_user,
    ):
        # Mock user
        mock_user = User(id="user-1", username="testuser", name="Test User", project_names=["demo"])
        mock_get_user.return_value = mock_user

        # Create parent assistant
        parent_assistant = Assistant(
            name="Parent Assistant",
            description="Parent Description",
            system_prompt="Parent Prompt",
            project="demo",
            assistant_ids=["sub-assistant-id"],
        )
        parent_assistant.id = "parent-id"

        # Create sub-assistant with same project
        sub_assistant = Assistant(
            name="Sub Assistant",
            description="Sub Description",
            system_prompt="Sub Prompt",
            project="demo",
        )
        sub_assistant.id = "sub-assistant-id"

        mock_find.return_value = sub_assistant

        error = parent_assistant._validate_assistant_ids()
        assert error is None


def test_validate_assistant_ids_valid_marketplace_assistant():
    """Test that validation passes when sub-assistant is a marketplace assistant (is_global=True)."""
    with (
        patch.object(Assistant, 'find_by_id') as mock_find,
        patch('codemie.rest_api.security.user_context.get_current_user') as mock_get_user,
    ):
        # Mock user
        mock_user = User(id="user-1", username="testuser", name="Test User", project_names=["demo"])
        mock_get_user.return_value = mock_user

        # Create parent assistant
        parent_assistant = Assistant(
            name="Parent Assistant",
            description="Parent Description",
            system_prompt="Parent Prompt",
            project="demo",
            assistant_ids=["sub-assistant-id"],
        )
        parent_assistant.id = "parent-id"

        # Create marketplace sub-assistant with different project but is_global=True
        sub_assistant = Assistant(
            name="Marketplace Assistant",
            description="Sub Description",
            system_prompt="Sub Prompt",
            project="other-project",
            is_global=True,
        )
        sub_assistant.id = "sub-assistant-id"

        mock_find.return_value = sub_assistant

        error = parent_assistant._validate_assistant_ids()
        assert error is None


def test_validate_assistant_ids_admin_bypass_project_mismatch():
    """Test that admin users can use sub-assistants from any project."""
    with (
        patch.object(Assistant, 'find_by_id') as mock_find,
        patch('codemie.rest_api.security.user_context.get_current_user') as mock_get_user,
    ):
        # Mock admin user (add 'admin' role to make user an admin)
        mock_user = User(id="admin-1", username="admin", name="Admin User", project_names=["demo"], roles=["admin"])
        mock_get_user.return_value = mock_user

        # Create parent assistant
        parent_assistant = Assistant(
            name="Parent Assistant",
            description="Parent Description",
            system_prompt="Parent Prompt",
            project="demo",
            assistant_ids=["sub-assistant-id"],
        )
        parent_assistant.id = "parent-id"

        # Create sub-assistant with different project
        sub_assistant = Assistant(
            name="Sub Assistant",
            description="Sub Description",
            system_prompt="Sub Prompt",
            project="other-project",
            is_global=False,
        )
        sub_assistant.id = "sub-assistant-id"

        mock_find.return_value = sub_assistant

        # Admin should bypass project mismatch validation
        error = parent_assistant._validate_assistant_ids()
        assert error is None


def test_validate_assistant_ids_circular_reference():
    """Test that validation fails when assistant tries to include itself."""
    with (
        patch.object(Assistant, 'find_by_id') as mock_find,
        patch('codemie.rest_api.security.user_context.get_current_user') as mock_get_user,
    ):
        # Mock user
        mock_user = User(id="user-1", username="testuser", name="Test User", project_names=["demo"])
        mock_get_user.return_value = mock_user

        # Create parent assistant that includes itself
        parent_assistant = Assistant(
            name="Parent Assistant",
            description="Parent Description",
            system_prompt="Parent Prompt",
            project="demo",
            assistant_ids=["parent-id"],
        )
        parent_assistant.id = "parent-id"

        # Mock find_by_id to return the parent itself
        mock_find.return_value = parent_assistant

        error = parent_assistant._validate_assistant_ids()
        assert error is not None
        assert "Circular reference detected" in error
        assert "cannot include itself as an inner assistant" in error


def test_validate_assistant_ids_nesting_violation_single():
    """Test that validation fails when sub-assistant has its own sub-assistants (singular form)."""
    with (
        patch.object(Assistant, 'find_by_id') as mock_find,
        patch('codemie.rest_api.security.user_context.get_current_user') as mock_get_user,
    ):
        # Mock user
        mock_user = User(id="user-1", username="testuser", name="Test User", project_names=["demo"])
        mock_get_user.return_value = mock_user

        # Create parent assistant
        parent_assistant = Assistant(
            name="Parent Assistant",
            description="Parent Description",
            system_prompt="Parent Prompt",
            project="demo",
            assistant_ids=["sub-assistant-id"],
        )
        parent_assistant.id = "parent-id"

        # Create sub-assistant that has its own sub-assistants (nesting violation)
        sub_assistant = Assistant(
            name="Nested Assistant",
            description="Sub Description",
            system_prompt="Sub Prompt",
            project="demo",
            assistant_ids=["nested-sub-id"],
        )
        sub_assistant.id = "sub-assistant-id"

        mock_find.return_value = sub_assistant

        error = parent_assistant._validate_assistant_ids()
        assert error is not None
        assert "Nested assistants" in error
        assert "Nested Assistant" in error
        assert "can't have its own sub-assistants" in error


def test_validate_assistant_ids_nesting_violation_multiple():
    """Test that validation reports all sub-assistants with nesting violations (plural form)."""
    with (
        patch.object(Assistant, 'find_by_id') as mock_find,
        patch('codemie.rest_api.security.user_context.get_current_user') as mock_get_user,
    ):
        # Mock user
        mock_user = User(id="user-1", username="testuser", name="Test User", project_names=["demo"])
        mock_get_user.return_value = mock_user

        # Create parent assistant with multiple sub-assistants
        parent_assistant = Assistant(
            name="Parent Assistant",
            description="Parent Description",
            system_prompt="Parent Prompt",
            project="demo",
            assistant_ids=["sub-1", "sub-2"],
        )
        parent_assistant.id = "parent-id"

        # Create two sub-assistants that both have nesting violations
        sub_assistant_1 = Assistant(
            name="Nested Assistant 1",
            description="Sub Description 1",
            system_prompt="Sub Prompt 1",
            project="demo",
            assistant_ids=["nested-sub-1"],
        )
        sub_assistant_1.id = "sub-1"

        sub_assistant_2 = Assistant(
            name="Nested Assistant 2",
            description="Sub Description 2",
            system_prompt="Sub Prompt 2",
            project="demo",
            assistant_ids=["nested-sub-2"],
        )
        sub_assistant_2.id = "sub-2"

        def mock_find_side_effect(assistant_id):
            if assistant_id == "sub-1":
                return sub_assistant_1
            elif assistant_id == "sub-2":
                return sub_assistant_2
            return None

        mock_find.side_effect = mock_find_side_effect

        error = parent_assistant._validate_assistant_ids()
        assert error is not None
        assert "Nested assistants not supported" in error
        assert "Nested Assistant 1" in error
        assert "Nested Assistant 2" in error
        assert "cannot contain their own inner assistants" in error


def test_validate_assistant_ids_not_found():
    """Test that validation fails when sub-assistant does not exist."""
    with (
        patch.object(Assistant, 'find_by_id') as mock_find,
        patch('codemie.rest_api.security.user_context.get_current_user') as mock_get_user,
    ):
        # Mock user
        mock_user = User(id="user-1", username="testuser", name="Test User", project_names=["demo"])
        mock_get_user.return_value = mock_user

        # Create parent assistant
        parent_assistant = Assistant(
            name="Parent Assistant",
            description="Parent Description",
            system_prompt="Parent Prompt",
            project="demo",
            assistant_ids=["non-existent-id"],
        )
        parent_assistant.id = "parent-id"

        # Mock find_by_id to return None (not found)
        mock_find.return_value = None

        error = parent_assistant._validate_assistant_ids()
        assert error is not None
        assert "Invalid reference" in error
        assert "non-existent-id" in error
        assert "does not exist in the system" in error


def test_validate_assistant_ids_single_project_mismatch():
    """Test that validation fails when one sub-assistant has different project (singular form)."""
    with (
        patch.object(Assistant, 'find_by_id') as mock_find,
        patch('codemie.rest_api.security.user_context.get_current_user') as mock_get_user,
        patch('codemie.configs.config.config.ENV', 'production'),  # Ensure ENV is not LOCAL
    ):
        # Mock user (non-admin)
        mock_user = User(id="user-1", username="testuser", name="Test User", project_names=["project-a"])
        mock_get_user.return_value = mock_user

        # Create parent assistant with project "project-a"
        # Don't set id to ensure validation runs (new object always validates)
        parent_assistant = Assistant(
            name="Parent Assistant",
            description="Parent Description",
            system_prompt="Parent Prompt",
            project="project-a",
            assistant_ids=["sub-assistant-id"],
        )

        # Create sub-assistant with different project "project-b"
        sub_assistant = Assistant(
            name="Sub Assistant",
            description="Sub Description",
            system_prompt="Sub Prompt",
            project="project-b",
            is_global=False,
        )
        sub_assistant.id = "sub-assistant-id"

        # Mock find_by_id to return the sub-assistant
        mock_find.return_value = sub_assistant

        # Validate should fail with project mismatch error (singular)
        error = parent_assistant._validate_assistant_ids()

        assert error is not None
        assert "Sub-assistant" in error
        assert "Sub Assistant" in error
        assert "project: 'project-b'" in error
        assert "the assistant 'Parent Assistant'" in error
        assert "'project-a'" in error
        assert "is associated with a different project" in error


def test_validate_assistant_ids_multiple_project_mismatches():
    """Test that validation reports all sub-assistants with different projects."""
    with (
        patch.object(Assistant, 'find_by_id') as mock_find,
        patch('codemie.rest_api.security.user_context.get_current_user') as mock_get_user,
        patch('codemie.configs.config.config.ENV', 'production'),  # Ensure ENV is not LOCAL
    ):
        # Mock user (non-admin)
        mock_user = User(id="user-1", username="testuser", name="Test User", project_names=["demo"])
        mock_get_user.return_value = mock_user

        # Create parent assistant with project "demo"
        # Don't set id to ensure validation runs (new object always validates)
        parent_assistant = Assistant(
            name="Parent Assistant",
            description="Parent Description",
            system_prompt="Parent Prompt",
            project="demo",
            assistant_ids=["sub-1", "sub-2"],
        )

        # Create two sub-assistants with different projects
        sub_assistant_1 = Assistant(
            name="Test Assistant",
            description="Sub Description 1",
            system_prompt="Sub Prompt 1",
            project="codemie",
            is_global=False,
        )
        sub_assistant_1.id = "sub-1"

        sub_assistant_2 = Assistant(
            name="Repository Info Assistant",
            description="Sub Description 2",
            system_prompt="Sub Prompt 2",
            project="codemie",
            is_global=False,
        )
        sub_assistant_2.id = "sub-2"

        # Mock find_by_id to return different assistants
        def mock_find_side_effect(assistant_id):
            if assistant_id == "sub-1":
                return sub_assistant_1
            elif assistant_id == "sub-2":
                return sub_assistant_2
            return None

        mock_find.side_effect = mock_find_side_effect

        # Validate should fail with project mismatch error for both (plural)
        error = parent_assistant._validate_assistant_ids()

        assert error is not None
        assert "Sub-assistants" in error
        assert "Test Assistant" in error
        assert "Repository Info Assistant" in error
        assert "project: 'codemie'" in error
        assert "the assistant 'Parent Assistant'" in error
        assert "'demo'" in error
        assert "are associated with different projects" in error


def test_validate_assistant_ids_unchanged_skips_validation():
    """Test that validation is skipped when no new assistant_ids were added."""
    with patch.object(Assistant, '_get_changed_assistant_ids') as mock_get_changed:
        # Mock that no new assistant IDs were added (empty set)
        mock_get_changed.return_value = set()

        parent_assistant = Assistant(
            name="Parent Assistant",
            description="Parent Description",
            system_prompt="Parent Prompt",
            project="demo",
            assistant_ids=["sub-assistant-id"],
        )
        parent_assistant.id = "parent-id"

        # Validation should be skipped
        error = parent_assistant._validate_assistant_ids()
        assert error is None
        mock_get_changed.assert_called_once()


class TestAssistantListResponse:
    """Tests for AssistantListResponse model to ensure it includes required fields for minimal response."""

    def test_assistant_list_response_with_shared_true(self):
        """Test that AssistantListResponse includes all fields with shared=True."""
        response = AssistantListResponse(
            id="test-id",
            name="Test Assistant",
            slug="test-assistant",
            type="codemie",
            description="Test Description",
            icon_url="http://example.com/icon.png",
            shared=True,
        )

        # Verify all fields are present
        assert response.id == "test-id"
        assert response.name == "Test Assistant"
        assert response.slug == "test-assistant"
        assert response.type == "codemie"
        assert response.description == "Test Description"
        assert response.icon_url == "http://example.com/icon.png"
        assert response.shared is True

        # Verify serialization includes shared field
        data = response.model_dump()
        assert 'shared' in data
        assert data['shared'] is True

    def test_assistant_list_response_with_shared_false(self):
        """Test that AssistantListResponse includes all fields with shared=False."""
        response = AssistantListResponse(
            id="test-id-2",
            name="Private Assistant",
            slug="private-assistant",
            type="codemie",
            description="Private Description",
            shared=False,
        )

        # Verify all fields are present
        assert response.id == "test-id-2"
        assert response.name == "Private Assistant"
        assert response.slug == "private-assistant"
        assert response.type == "codemie"
        assert response.description == "Private Description"
        assert response.shared is False

        # Verify serialization includes shared field
        data = response.model_dump()
        assert 'shared' in data
        assert data['shared'] is False
