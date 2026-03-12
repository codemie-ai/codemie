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

"""Shared fixtures for validation node tests."""

import pytest
from unittest.mock import Mock

from codemie.rest_api.models.assistant import Assistant, Context, ContextType, ToolKitDetails, ToolDetails
from codemie.rest_api.security.user import User
from codemie.workflows.assistant_generator.models.validation_state import AssistantValidationState
from codemie_tools.base.models import ToolKit, Tool


@pytest.fixture
def sample_user():
    """Create a sample user for testing."""
    return User(
        id="test-user-123",
        username="testuser",
        name="Test User",
        roles=["user"],
        project_names=["demo"],
    )


@pytest.fixture
def sample_assistant():
    """Create a sample assistant for testing."""
    return Assistant(
        id="test-assistant-id",
        name="Python Code Assistant",
        description="An assistant that helps with Python coding tasks",
        categories=["programming", "python"],
        system_prompt="You are a helpful Python programming assistant. Help users write clean, efficient Python code.",
        conversation_starters=["Help me debug Python code", "Explain Python best practices"],
        toolkits=[
            ToolKitDetails(
                toolkit="test_toolkit",
                tools=[
                    ToolDetails(name="test_tool_1", label="Test Tool 1"),
                    ToolDetails(name="test_tool_2", label="Test Tool 2"),
                ],
            )
        ],
        context=[
            Context(name="test_repo_1", context_type=ContextType.CODE),
            Context(name="test_repo_2", context_type=ContextType.KNOWLEDGE_BASE),
        ],
        project="test-project",
    )


@pytest.fixture
def sample_state(sample_assistant, sample_user):
    """Create a sample validation state."""
    return AssistantValidationState(
        assistant=sample_assistant,
        user=sample_user,
        request_id="test-request-123",
        current_phase="validate_tools",
    )


@pytest.fixture
def sample_toolkits():
    """Create sample toolkits returned by RAG."""
    return [
        ToolKit(
            toolkit="python_toolkit",
            tools=[
                Tool(name="python_execute", label="Execute Python code"),
                Tool(name="python_format", label="Format Python code"),
            ],
        ),
        ToolKit(
            toolkit="database_toolkit",
            tools=[
                Tool(name="sql_query", label="Run SQL query"),
                Tool(name="db_migrate", label="Database migration"),
            ],
        ),
    ]


@pytest.fixture
def mock_llm():
    """Create a mock LLM instance for testing."""
    llm = Mock()
    llm.with_structured_output = Mock(return_value=Mock())
    return llm
