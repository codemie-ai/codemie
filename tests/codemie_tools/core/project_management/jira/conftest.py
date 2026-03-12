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

from codemie_tools.core.project_management.jira.models import JiraConfig


@pytest.fixture
def jira_config():
    """Fixture for JiraConfig."""
    return JiraConfig(url="https://jira.example.com", token="abc123")


@pytest.fixture
def jira_cloud_config():
    """Fixture for JiraConfig with cloud=True."""
    return JiraConfig(url="https://jira.example.com", token="abc123", username="user@example.com", cloud=True)


@pytest.fixture
def mock_jira():
    """Fixture for mocked Jira client."""
    with patch("codemie_tools.core.project_management.jira.tools.Jira") as mock_jira_class:
        mock_jira_instance = MagicMock()
        mock_jira_class.return_value = mock_jira_instance
        yield mock_jira_instance


@pytest.fixture
def sample_issue():
    """Fixture for a sample Jira issue."""
    return {
        "key": "TEST-123",
        "fields": {
            "summary": "Test Issue",
            "description": "This is a test issue",
            "assignee": {"displayName": "John Doe"},
            "status": {"name": "In Progress"},
            "issuetype": {"name": "Bug"},
            "priority": {"name": "High"},
            "components": [{"name": "Frontend"}, {"name": "Backend"}],
        },
    }


@pytest.fixture
def sample_search_response():
    """Fixture for a sample Jira search response."""
    return {
        "issues": [
            {
                "key": "TEST-123",
                "fields": {
                    "summary": "Test Issue 1",
                    "assignee": {"displayName": "John Doe"},
                    "status": {"name": "In Progress"},
                    "issuetype": {"name": "Bug"},
                },
            },
            {
                "key": "TEST-124",
                "fields": {
                    "summary": "Test Issue 2",
                    "assignee": {"displayName": "Jane Smith"},
                    "status": {"name": "Done"},
                    "issuetype": {"name": "Task"},
                },
            },
        ],
        "total": 2,
    }
