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

from codemie_tools.core.vcs.gitlab.models import GitlabConfig
from codemie_tools.core.vcs.gitlab.tools_vars import GITLAB_TOOL


class TestGitlabToolVars:
    def test_gitlab_tool_metadata(self):
        """Test GitLab tool metadata."""
        assert GITLAB_TOOL.name == "gitlab"
        assert "Advanced GitLab REST API client tool" in GITLAB_TOOL.description
        assert GITLAB_TOOL.label == "Gitlab"
        assert "Provides comprehensive access to the GitLab REST API" in GITLAB_TOOL.user_description
        assert GITLAB_TOOL.settings_config is True
        assert GITLAB_TOOL.config_class == GitlabConfig

    def test_gitlab_tool_description_content(self):
        """Test that the GitLab tool description contains important information."""
        description = GITLAB_TOOL.description

        # Check that the description contains important sections
        assert "INPUT FORMAT:" in description
        assert "REQUIREMENTS:" in description
        assert "FEATURES:" in description
        assert "RESPONSE FORMAT:" in description
        assert "SECURITY:" in description
        assert "EXAMPLES:" in description

        # Check that the description contains important details
        assert "method" in description
        assert "url" in description
        assert "method_arguments" in description
        assert "custom_headers" in description
        assert "/api/v4/" in description
        assert "Authorization headers are automatically managed" in description

    def test_gitlab_tool_user_description_content(self):
        """Test that the GitLab tool user description contains important information."""
        user_description = GITLAB_TOOL.user_description

        # Check that the user description contains important sections
        assert "Key Capabilities:" in user_description
        assert "Setup Requirements:" in user_description
        assert "Response Features:" in user_description

        # Check that the user description contains important details
        assert "Project and repository management" in user_description
        assert "Issue and merge request operations" in user_description
        assert "GitLab Server URL" in user_description
        assert "GitLab Personal Access Token" in user_description
        assert "Complete HTTP transaction details" in user_description
