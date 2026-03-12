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

from codemie_tools.core.project_management.jira.tools_vars import get_jira_tool_description, GENERIC_JIRA_TOOL


class TestGetJiraToolDescription:
    """Tests for get_jira_tool_description function."""

    def test_get_description_v2(self):
        """Test getting description for API v2."""
        description = get_jira_tool_description(api_version=2)

        assert "JIRA Tool for Official Atlassian JIRA REST API V2" in description
        assert "'/rest/api/2/...'" in description

    def test_get_description_v3(self):
        """Test getting description for API v3."""
        description = get_jira_tool_description(api_version=3)

        assert "JIRA Tool for Official Atlassian JIRA REST API V3" in description
        assert "'/rest/api/3/...'" in description
        assert "/rest/api/3/search/jql" in description

    def test_invalid_api_version(self):
        """Test with invalid API version."""
        with pytest.raises(ValueError) as excinfo:
            get_jira_tool_description(api_version=4)

        assert "Wrong API version" in str(excinfo.value)
        assert "required 2 or 3" in str(excinfo.value)


class TestGenericJiraTool:
    """Tests for GENERIC_JIRA_TOOL constant."""

    def test_generic_jira_tool_metadata(self):
        """Test GENERIC_JIRA_TOOL metadata."""
        assert GENERIC_JIRA_TOOL.name == "generic_jira_tool"
        assert "JIRA Tool for Official Atlassian JIRA REST API V2" in GENERIC_JIRA_TOOL.description
        assert GENERIC_JIRA_TOOL.label == "Generic Jira"
        assert "Provides access to the Jira API" in GENERIC_JIRA_TOOL.user_description
        assert GENERIC_JIRA_TOOL.settings_config is True
        assert GENERIC_JIRA_TOOL.config_class.__name__ == "JiraConfig"
