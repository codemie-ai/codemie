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

from codemie_tools.core.vcs.github.models import GithubConfig
from codemie_tools.core.vcs.github.tools import GithubTool


class TestGithubConfig(unittest.TestCase):
    def test_valid_config(self):
        config = GithubConfig(token="ghp_123456")
        assert config.token == "ghp_123456"

    def test_empty_token(self):
        # Empty token is allowed in the current implementation
        config = GithubConfig(token="")
        assert config.token == ""

    def test_empty_config(self):
        # Arrange - create config with empty token (default value)
        config = GithubConfig(token="")

        # Act
        tool = GithubTool(config=config)
        query = {"method": "GET", "url": "https://api.github.com/user", "method_arguments": {}}

        # Assert - should return error message string when handle_tool_error=True
        # Use invoke() instead of execute() to go through _run() which validates
        result = tool.invoke({"query": query})

        # Empty token results in GitHub API authentication error
        self.assertIn("GitHub API request failed", result)
