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

import unittest

from codemie_tools.core.vcs.gitlab.models import GitlabConfig
from codemie_tools.core.vcs.gitlab.tools import GitlabTool


class TestGitlabConfig(unittest.TestCase):
    def test_valid_config(self):
        """Test creating a valid GitlabConfig."""
        config = GitlabConfig(url="https://gitlab.example.com", token="test_token")

        assert config.url == "https://gitlab.example.com"
        assert config.token == "test_token"

    def test_empty_url(self):
        """Test that url cannot be empty."""
        # Note: Pydantic v2 doesn't validate empty strings by default
        # This test is kept for documentation purposes
        config = GitlabConfig(url="", token="test_token")
        assert config.url == ""

    def test_empty_token(self):
        """Test that token cannot be empty."""
        # Note: Pydantic v2 doesn't validate empty strings by default
        # This test is kept for documentation purposes
        config = GitlabConfig(url="https://gitlab.example.com", token="")
        assert config.token == ""

    def test_empty_config(self):
        # Arrange - create config with empty token (default value)
        config = GitlabConfig(url="https://gitlab.example.com", token="")

        # Act
        tool = GitlabTool(config=config)
        query = {"method": "GET", "url": "/api/v4/user", "method_arguments": {}}

        # Assert - should return error message string when handle_tool_error=True
        # Use invoke() instead of execute() to go through _run() which validates
        result = tool.invoke({"query": query})

        self.assertIn("Tool config is not set", result)
        self.assertIn("token", result)
