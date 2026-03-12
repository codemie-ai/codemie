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

import json
from unittest.mock import patch, Mock

import pytest

from codemie_tools.core.vcs.gitlab.models import GitlabConfig
from codemie_tools.core.vcs.gitlab.tools import GitlabTool


class TestGitlabIntegration:
    """Integration tests for GitlabTool."""

    @pytest.fixture
    def gitlab_tool(self):
        """Create a GitlabTool instance with mock config."""
        config = GitlabConfig(url="https://gitlab.example.com", token="test_token")
        return GitlabTool(config=config)

    @patch("requests.request")
    def test_get_user_profile(self, mock_request, gitlab_tool):
        """Test getting user profile."""
        # Mock response for user profile
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.reason = "OK"
        mock_response.text = json.dumps(
            {"id": 1, "username": "test_user", "name": "Test User", "email": "test@example.com", "state": "active"}
        )
        mock_request.return_value = mock_response

        query = {"method": "GET", "url": "/api/v4/user", "method_arguments": {}}

        result = gitlab_tool.execute(query)

        mock_request.assert_called_once()
        assert "HTTP: GET" in result
        assert "200 OK" in result
        assert "test_user" in result
        assert "Test User" in result

    @patch("requests.request")
    def test_list_projects(self, mock_request, gitlab_tool):
        """Test listing projects."""
        # Mock response for projects list
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.reason = "OK"
        mock_response.text = json.dumps(
            [
                {"id": 1, "name": "project1", "description": "Test Project 1", "visibility": "private"},
                {"id": 2, "name": "project2", "description": "Test Project 2", "visibility": "public"},
            ]
        )
        mock_request.return_value = mock_response

        query = {"method": "GET", "url": "/api/v4/projects", "method_arguments": {"visibility": "public"}}

        result = gitlab_tool.execute(query)

        mock_request.assert_called_once()
        assert "HTTP: GET" in result
        assert "200 OK" in result
        assert "project1" in result
        assert "project2" in result

    @patch("requests.request")
    def test_create_project(self, mock_request, gitlab_tool):
        """Test creating a project."""
        # Mock response for project creation
        mock_response = Mock()
        mock_response.status_code = 201
        mock_response.reason = "Created"
        mock_response.text = json.dumps(
            {"id": 3, "name": "new_project", "description": "A new test project", "visibility": "private"}
        )
        mock_request.return_value = mock_response

        query = {
            "method": "POST",
            "url": "/api/v4/projects",
            "method_arguments": {"name": "new_project", "description": "A new test project", "visibility": "private"},
        }

        result = gitlab_tool.execute(query)

        mock_request.assert_called_once()
        assert "HTTP: POST" in result
        assert "201 Created" in result
        assert "new_project" in result

    @patch("requests.request")
    def test_get_project_issues(self, mock_request, gitlab_tool):
        """Test getting project issues."""
        # Mock response for project issues
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.reason = "OK"
        mock_response.text = json.dumps(
            [
                {"id": 1, "iid": 1, "title": "Bug in login", "description": "Users cannot login", "state": "opened"},
                {
                    "id": 2,
                    "iid": 2,
                    "title": "Improve performance",
                    "description": "Application is slow",
                    "state": "closed",
                },
            ]
        )
        mock_request.return_value = mock_response

        query = {"method": "GET", "url": "/api/v4/projects/1/issues", "method_arguments": {"state": "opened"}}

        result = gitlab_tool.execute(query)

        mock_request.assert_called_once()
        assert "HTTP: GET" in result
        assert "200 OK" in result
        assert "Bug in login" in result
        assert "Improve performance" in result

    @patch("requests.request")
    def test_create_merge_request(self, mock_request, gitlab_tool):
        """Test creating a merge request."""
        # Mock response for merge request creation
        mock_response = Mock()
        mock_response.status_code = 201
        mock_response.reason = "Created"
        mock_response.text = json.dumps(
            {
                "id": 1,
                "iid": 1,
                "title": "Feature implementation",
                "description": "Implementing new feature",
                "source_branch": "feature-branch",
                "target_branch": "main",
                "state": "opened",
            }
        )
        mock_request.return_value = mock_response

        query = {
            "method": "POST",
            "url": "/api/v4/projects/1/merge_requests",
            "method_arguments": {
                "source_branch": "feature-branch",
                "target_branch": "main",
                "title": "Feature implementation",
                "description": "Implementing new feature",
            },
        }

        result = gitlab_tool.execute(query)

        mock_request.assert_called_once()
        assert "HTTP: POST" in result
        assert "201 Created" in result
        assert "Feature implementation" in result
        assert "feature-branch" in result

    @patch("requests.request")
    def test_error_handling_workflow(self, mock_request, gitlab_tool):
        """Test error handling in a workflow."""
        # First request fails with 404
        not_found_response = Mock()
        not_found_response.status_code = 404
        not_found_response.reason = "Not Found"
        not_found_response.text = json.dumps({"message": "Project not found"})

        # Second request succeeds with 201
        success_response = Mock()
        success_response.status_code = 201
        success_response.reason = "Created"
        success_response.text = json.dumps({"id": 1, "name": "new_project"})

        # Set up the mock to return different responses for different calls
        mock_request.side_effect = [not_found_response, success_response]

        # First request - should fail with 404
        query1 = {"method": "GET", "url": "/api/v4/projects/999", "method_arguments": {}}

        result1 = gitlab_tool.execute(query1)

        assert "HTTP: GET" in result1
        assert "404 Not Found" in result1
        assert "Project not found" in result1

        # Second request - should succeed with 201
        query2 = {"method": "POST", "url": "/api/v4/projects", "method_arguments": {"name": "new_project"}}

        result2 = gitlab_tool.execute(query2)

        assert "HTTP: POST" in result2
        assert "201 Created" in result2
        assert "new_project" in result2
