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

"""Unit tests for the ToolsInfoService retrieval-backend gating."""

from unittest.mock import patch

from codemie_tools.base.models import ToolSet

from codemie.service.tools.tools_info_service import ToolsInfoService


def _toolkit_names(toolkits):
    return {t.get("toolkit") for t in toolkits}


@patch("codemie.service.tools.tools_info_service.ToolsInfoService._provider_toolkits_info", return_value=[])
@patch("codemie.enterprise.plugin.get_plugin_toolkit_ui_info", return_value=None)
@patch(
    "codemie.service.tools.tools_info_service.toolkit_provider.get_available_toolkits_info",
    return_value=[],
)
class TestToolsInfoServiceRetrievalGating:
    """Tests that KB and code toolkits are hidden when retrieval backend is disabled."""

    def test_get_tools_info_excludes_kb_and_code_toolkits_when_retrieval_unavailable(
        self, mock_available, mock_plugin, mock_provider
    ):
        with patch(
            "codemie.service.tools.tools_info_service.ElasticSearchClient.is_configured",
            return_value=False,
        ):
            toolkits = ToolsInfoService.get_tools_info()

        names = _toolkit_names(toolkits)
        assert ToolSet.KB_TOOLS.value not in names
        assert ToolSet.CODEBASE_TOOLS.value not in names

    def test_get_tools_info_includes_them_by_default(self, mock_available, mock_plugin, mock_provider):
        with patch(
            "codemie.service.tools.tools_info_service.ElasticSearchClient.is_configured",
            return_value=True,
        ):
            toolkits = ToolsInfoService.get_tools_info()

        names = _toolkit_names(toolkits)
        assert ToolSet.KB_TOOLS.value in names
        assert ToolSet.CODEBASE_TOOLS.value in names
