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

from codemie_tools.core.vcs.github.tools_vars import GITHUB_TOOL


class TestGithubToolVars:
    def test_github_tool_metadata(self):
        assert GITHUB_TOOL.name == "github"
        assert "GitHub API" in GITHUB_TOOL.description
        assert GITHUB_TOOL.label == "Github"
        assert GITHUB_TOOL.settings_config is True
        assert GITHUB_TOOL.config_class.__name__ == "GithubConfig"
