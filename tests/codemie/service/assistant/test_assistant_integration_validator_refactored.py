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

"""Unit tests for AssistantIntegrationValidator service (refactored with parametrization)."""

from unittest.mock import Mock, patch

import pytest
from codemie_tools.base.models import Tool

from codemie.rest_api.models.assistant import (
    Assistant,
    AssistantRequest,
    IntegrationValidationResult,
    MissingIntegration,
    MissingIntegrationsByCredentialType,
    ToolKitDetails,
)
from codemie.rest_api.security.user import User
from codemie.service.assistant.assistant_integration_validator import AssistantIntegrationValidator
from codemie.service.assistant.credential_validator import ValidationResult


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def mock_user():
    """Fixture for mocking User."""
    user = Mock(spec=User)
    user.id = "test-user-id"
    user.name = "Test User"
    return user


@pytest.fixture
def minimal_assistant_request():
    """Fixture for minimal assistant request."""
    return AssistantRequest(
        name="Test Assistant",
        description="Test description",
        project="test-project",
        system_prompt="Test prompt",
        llm_model_type="gpt-4",
        toolkits=[],
        assistant_ids=[],
    )


def create_mock_toolkit(toolkit_name, tool_names, is_external=False):
    """Helper to create mock toolkit with tools."""
    toolkit = Mock(spec=ToolKitDetails)
    toolkit.toolkit = toolkit_name
    toolkit.is_external = is_external
    toolkit.tools = []

    for tool_name in tool_names:
        tool = Mock(spec=Tool)
        tool.name = tool_name
        tool.settings = None
        tool.settings_config = False  # Tool uses toolkit-level settings
        toolkit.tools.append(tool)

    return toolkit


def create_mock_assistant(assistant_id, name, toolkits):
    """Helper to create mock assistant."""
    assistant = Mock(spec=Assistant)
    assistant.id = assistant_id
    assistant.name = name
    assistant.icon_url = f"https://example.com/{assistant_id}.png"
    assistant.toolkits = toolkits
    assistant.is_global = False
    # Mock created_by for ownership checking
    created_by = Mock()
    created_by.id = "assistant-creator-id"
    assistant.created_by = created_by
    return assistant


# ============================================================================
# Parametrized Tests for Main Validation Flow
# ============================================================================


@pytest.mark.parametrize(
    "toolkit_count,tools_per_toolkit,credentials_valid,expected_has_missing",
    [
        (0, 0, True, False),  # No toolkits
        (1, 2, True, False),  # All valid
        (1, 2, False, True),  # All missing
        (2, 3, False, True),  # Multiple toolkits with missing
    ],
    ids=[
        "no_toolkits",
        "all_credentials_valid",
        "all_credentials_missing",
        "multiple_toolkits_missing",
    ],
)
@patch("codemie.service.assistant.assistant_integration_validator.CredentialValidator")
def test_validate_integrations_main_flow(
    mock_validator,
    mock_user,
    minimal_assistant_request,
    toolkit_count,
    tools_per_toolkit,
    credentials_valid,
    expected_has_missing,
):
    """Test main validation flow with different scenarios."""
    # Setup
    for i in range(toolkit_count):
        toolkit = create_mock_toolkit(f"Toolkit{i}", [f"tool{j}" for j in range(tools_per_toolkit)])
        minimal_assistant_request.toolkits.append(toolkit)

    mock_validator.validate_tool_credentials.return_value = ValidationResult(
        is_valid=credentials_valid,
        credential_type=None if credentials_valid else "AWS",
    )

    # Execute
    result = AssistantIntegrationValidator.validate_integrations(
        assistant_request=minimal_assistant_request,
        user=mock_user,
        project_name="test-project",
    )

    # Assert
    assert isinstance(result, IntegrationValidationResult)
    assert result.has_missing_integrations == expected_has_missing

    if expected_has_missing:
        assert len(result.missing_by_credential_type) > 0
        assert result.message is not None
    else:
        assert len(result.missing_by_credential_type) == 0
        assert result.message is None


@pytest.mark.parametrize(
    "sub_assistant_count,tools_per_sub,credentials_valid,expected_sub_missing_count",
    [
        (0, 0, True, 0),  # No sub-assistants
        (1, 2, True, 0),  # One sub with valid creds
        (1, 2, False, 1),  # One sub with missing creds
        (3, 1, False, 3),  # Multiple subs with missing creds
    ],
    ids=[
        "no_sub_assistants",
        "one_sub_all_valid",
        "one_sub_missing",
        "multiple_subs_missing",
    ],
)
@patch("codemie.service.assistant.assistant_integration_validator.Assistant")
@patch("codemie.service.assistant.assistant_integration_validator.CredentialValidator")
def test_validate_integrations_with_sub_assistants(
    mock_validator,
    mock_assistant_class,
    mock_user,
    minimal_assistant_request,
    sub_assistant_count,
    tools_per_sub,
    credentials_valid,
    expected_sub_missing_count,
):
    """Test validation with sub-assistants."""
    # Setup
    sub_assistants = []
    for i in range(sub_assistant_count):
        toolkit = create_mock_toolkit(f"SubToolkit{i}", [f"subtool{j}" for j in range(tools_per_sub)])
        sub = create_mock_assistant(f"sub-{i}", f"Sub {i}", [toolkit])
        sub_assistants.append(sub)
        minimal_assistant_request.assistant_ids.append(f"sub-{i}")

    def get_by_ids_side_effect(user, ids, parent_assistant):
        return [sub for sub in sub_assistants if sub.id in ids]

    mock_assistant_class.get_by_ids.side_effect = get_by_ids_side_effect
    mock_validator.validate_tool_credentials.return_value = ValidationResult(
        is_valid=credentials_valid,
        credential_type=None if credentials_valid else "Jira",
    )

    # Execute
    result = AssistantIntegrationValidator.validate_integrations(
        assistant_request=minimal_assistant_request,
        user=mock_user,
        project_name="test-project",
    )

    # Assert
    assert len(result.sub_assistants_missing) == expected_sub_missing_count


# ============================================================================
# Parametrized Tests for Toolkit Validation
# ============================================================================


@pytest.mark.parametrize(
    "is_external,should_validate",
    [
        (False, True),  # Internal toolkit - should validate
        (True, False),  # External toolkit - should skip
    ],
    ids=["internal_toolkit", "external_toolkit"],
)
@patch("codemie.service.assistant.assistant_integration_validator.CredentialValidator")
def test_validate_toolkits_external_handling(
    mock_validator,
    mock_user,
    is_external,
    should_validate,
):
    """Test that external toolkits are skipped."""
    # Setup
    toolkit = create_mock_toolkit("TestToolkit", ["tool1"], is_external=is_external)

    # Execute
    result = AssistantIntegrationValidator._validate_toolkits(
        toolkits=[toolkit],
        user=mock_user,
        project_name="test-project",
    )

    # Assert
    if should_validate:
        mock_validator.validate_tool_credentials.assert_called()
    else:
        mock_validator.validate_tool_credentials.assert_not_called()
        assert result == []


# ============================================================================
# Parametrized Tests for Grouping
# ============================================================================


@pytest.mark.parametrize(
    "missing_tools,expected_groups",
    [
        ([], 0),  # Empty
        (
            [
                MissingIntegration(toolkit="AWS", tool="s3", label="S3", credential_type="AWS"),
                MissingIntegration(toolkit="AWS", tool="lambda", label="Lambda", credential_type="AWS"),
            ],
            1,
        ),  # Single type
        (
            [
                MissingIntegration(toolkit="AWS", tool="s3", label="S3", credential_type="AWS"),
                MissingIntegration(toolkit="Jira", tool="search", label="Search", credential_type="Jira"),
                MissingIntegration(toolkit="Confluence", tool="read", label="Read", credential_type="Confluence"),
            ],
            3,
        ),  # Multiple types
    ],
    ids=["empty", "single_credential_type", "multiple_credential_types"],
)
def test_group_by_credential_type(missing_tools, expected_groups):
    """Test grouping missing tools by credential type."""
    # Execute
    result = AssistantIntegrationValidator._group_by_credential_type(missing_tools)

    # Assert
    assert len(result) == expected_groups
    if expected_groups > 0:
        # Verify sorted
        cred_types = [item.credential_type for item in result]
        assert cred_types == sorted(cred_types)


def test_group_by_credential_type_deduplication():
    """Test that duplicate tools are deduplicated."""
    # Setup - Same tool appears twice
    missing_tools = [
        MissingIntegration(toolkit="AWS", tool="s3_upload", label="S3", credential_type="AWS"),
        MissingIntegration(toolkit="AWS", tool="s3_upload", label="S3", credential_type="AWS"),
    ]

    # Execute
    result = AssistantIntegrationValidator._group_by_credential_type(missing_tools)

    # Assert - Should only have 1 tool after deduplication
    assert len(result) == 1
    assert len(result[0].missing_tools) == 1


@pytest.mark.parametrize(
    "tools_with_context,expected_groups",
    [
        ([], 0),  # Empty
        (
            [
                (
                    MissingIntegration(toolkit="AWS", tool="s3", label="S3", credential_type="AWS"),
                    "sub-1",
                    "Sub 1",
                    "icon1.png",
                ),
            ],
            1,
        ),  # Single assistant
        (
            [
                (
                    MissingIntegration(toolkit="AWS", tool="s3", label="S3", credential_type="AWS"),
                    "sub-1",
                    "Sub 1",
                    "icon1.png",
                ),
                (
                    MissingIntegration(toolkit="Jira", tool="search", label="Search", credential_type="Jira"),
                    "sub-2",
                    "Sub 2",
                    "icon2.png",
                ),
            ],
            2,
        ),  # Multiple assistants
    ],
    ids=["empty", "single_assistant", "multiple_assistants"],
)
def test_group_by_credential_type_with_context(tools_with_context, expected_groups):
    """Test grouping with sub-assistant context."""
    # Execute
    result = AssistantIntegrationValidator._group_by_credential_type_with_context(tools_with_context)

    # Assert
    assert len(result) == expected_groups


# ============================================================================
# Parametrized Tests for Result Building
# ============================================================================


@pytest.mark.parametrize(
    "main_count,sub_count,expected_has_missing,expected_total",
    [
        (0, 0, False, 0),  # Nothing missing
        (1, 0, True, 1),  # Only main missing
        (0, 2, True, 2),  # Only sub missing
        (2, 3, True, 5),  # Both missing
    ],
    ids=["nothing_missing", "only_main_missing", "only_sub_missing", "both_missing"],
)
def test_build_validation_result(main_count, sub_count, expected_has_missing, expected_total):
    """Test building validation result with different scenarios."""
    # Setup
    main_grouped = [
        MissingIntegrationsByCredentialType(
            credential_type=f"Type{i}",
            missing_tools=[MissingIntegration(toolkit="T", tool=f"tool{i}", label="Label", credential_type=f"Type{i}")],
        )
        for i in range(main_count)
    ]

    sub_grouped = [
        MissingIntegrationsByCredentialType(
            credential_type=f"SubType{i}",
            missing_tools=[
                MissingIntegration(toolkit="T", tool=f"subtool{i}", label="Label", credential_type=f"SubType{i}")
            ],
            assistant_id=f"sub-{i}",
            assistant_name=f"Sub {i}",
        )
        for i in range(sub_count)
    ]

    # Execute
    result = AssistantIntegrationValidator._build_validation_result(main_grouped, sub_grouped)

    # Assert
    assert result.has_missing_integrations == expected_has_missing
    assert len(result.missing_by_credential_type) == main_count
    assert len(result.sub_assistants_missing) == sub_count

    if expected_has_missing:
        assert f"{expected_total} tool(s)" in result.message
    else:
        assert result.message is None


# ============================================================================
# Sub-assistant Validation Error Handling
# ============================================================================


@pytest.mark.parametrize(
    "error_type,expected_result_length",
    [
        (Exception("Database error"), 0),  # Generic exception
        (KeyError("Missing key"), 0),  # Specific exception
        (None, 1),  # No error
    ],
    ids=["generic_exception", "specific_exception", "no_error"],
)
@patch("codemie.service.assistant.assistant_integration_validator.CredentialValidator")
@patch("codemie.service.assistant.assistant_integration_validator.Assistant")
def test_validate_sub_assistants_exception_handling(
    mock_assistant_class,
    mock_validator,
    mock_user,
    error_type,
    expected_result_length,
):
    """Test exception handling during sub-assistant validation."""
    # Setup
    if error_type:
        mock_assistant_class.get_by_ids.side_effect = error_type
    else:
        toolkit = create_mock_toolkit("TestToolkit", ["tool1"])
        sub = create_mock_assistant("sub-1", "Sub 1", [toolkit])
        mock_assistant_class.get_by_ids.return_value = [sub]
        mock_validator.validate_tool_credentials.return_value = ValidationResult(is_valid=False, credential_type="AWS")

    # Execute - Should not raise exception
    result = AssistantIntegrationValidator._validate_sub_assistants(
        assistant_ids=["sub-1"],
        user=mock_user,
        project_name="test-project",
    )

    # Assert
    assert len(result) == expected_result_length
