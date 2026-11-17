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

from codemie.configs.customer_config import CustomerConfig
from codemie_tools.base.models import ToolSet

from codemie.service.default_tool_names import DEFAULT_CONTEXT_TOOL_NAMES
from codemie.service.tools.tools_info_service import ToolsInfoService


def _toolkit_names(toolkits):
    return {t.get("toolkit") for t in toolkits}


def _all_tool_names(toolkits):
    return [tool["name"] for t in toolkits for tool in t.get("tools") or []]


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
        assert "search_kb" not in _all_tool_names(toolkits)

    def test_get_tools_info_includes_them_by_default(self, mock_available, mock_plugin, mock_provider):
        with patch(
            "codemie.service.tools.tools_info_service.ElasticSearchClient.is_configured",
            return_value=True,
        ):
            toolkits = ToolsInfoService.get_tools_info()

        names = _toolkit_names(toolkits)
        assert ToolSet.KB_TOOLS.value not in names
        assert ToolSet.CODEBASE_TOOLS.value in names

    def test_get_tools_info_lists_search_kb_for_ui_true_and_false(self, mock_available, mock_plugin, mock_provider):
        """EPMCDME-15114 Task 7: search_kb must be visible in the UI listing (show_for_ui=True)
        as well as the API listing (show_for_ui=False), so a user can deselect it."""
        with patch(
            "codemie.service.tools.tools_info_service.ElasticSearchClient.is_configured",
            return_value=True,
        ):
            ui_toolkits = ToolsInfoService.get_tools_info(show_for_ui=True)
            api_toolkits = ToolsInfoService.get_tools_info(show_for_ui=False)

        for toolkits in (ui_toolkits, api_toolkits):
            assert ToolSet.KB_TOOLS.value not in _toolkit_names(toolkits)
            codebase = next(t for t in toolkits if t.get("toolkit") == ToolSet.CODEBASE_TOOLS.value)
            assert [tool["name"] for tool in codebase["tools"]].count("search_kb") == 1
            assert _all_tool_names(toolkits).count("search_kb") == 1

    def test_get_tools_info_exclude_tools_removes_default_context_tools(
        self, mock_available, mock_plugin, mock_provider
    ):
        with patch(
            "codemie.service.tools.tools_info_service.ElasticSearchClient.is_configured",
            return_value=True,
        ):
            all_toolkits = ToolsInfoService.get_tools_info()
            filtered = ToolsInfoService.get_tools_info(exclude_tools=DEFAULT_CONTEXT_TOOL_NAMES)

        names = _all_tool_names(filtered)
        assert set(names) == set(_all_tool_names(all_toolkits)) - set(DEFAULT_CONTEXT_TOOL_NAMES)
        assert ToolSet.CODEBASE_TOOLS.value in _toolkit_names(filtered)

    def test_search_kb_created_with_codebase_group_when_group_absent(self, mock_available, mock_plugin, mock_provider):
        with patch(
            "codemie.service.tools.tools_info_service.ElasticSearchClient.is_configured",
            return_value=True,
        ):
            for show_for_ui in (True, False):
                toolkits = ToolsInfoService.get_tools_info(show_for_ui=show_for_ui)
                groups = [t for t in toolkits if t.get("toolkit") == ToolSet.CODEBASE_TOOLS.value]
                assert len(groups) == 1
                assert [tool["name"] for tool in groups[0]["tools"]].count("search_kb") == 1

    def test_existing_codebase_tools_unchanged_and_unduplicated(self, mock_available, mock_plugin, mock_provider):
        with patch(
            "codemie.service.tools.tools_info_service.ElasticSearchClient.is_configured",
            return_value=True,
        ):
            toolkits = ToolsInfoService.get_tools_info(show_for_ui=True)

        codebase = next(t for t in toolkits if t.get("toolkit") == ToolSet.CODEBASE_TOOLS.value)
        names = [tool["name"] for tool in codebase["tools"]]
        assert len(names) == len(set(names))
        assert len(names) > 1

    def test_find_toolkit_for_tool_search_kb_returns_codebase_tools(self, mock_available, mock_plugin, mock_provider):
        from codemie.rest_api.security.user import User
        from codemie.service.tools.tool_service import ToolsService

        user = User(id="u1", username="u", name="u")
        with patch(
            "codemie.service.tools.tools_info_service.ElasticSearchClient.is_configured",
            return_value=True,
        ):
            toolkit = ToolsService.find_toolkit_for_tool(user, "search_kb")

        assert toolkit["toolkit"] == ToolSet.CODEBASE_TOOLS.value


@patch("codemie.service.tools.tools_info_service.ToolsInfoService._provider_toolkits_info", return_value=[])
@patch("codemie.enterprise.plugin.get_plugin_toolkit_ui_info", return_value=None)
@patch("codemie.service.tools.tools_info_service.toolkit_provider.get_available_toolkits_info", return_value=[])
@patch("codemie.service.tools.tools_info_service.ElasticSearchClient.is_configured", return_value=False)
class TestOfferedOnly:
    @staticmethod
    def _research_tools(toolkits):
        return {t["name"] for tk in toolkits if tk.get("toolkit") == ToolSet.RESEARCH.value for t in tk["tools"]}

    def test_offered_only_hides_unusable_but_lookup_keeps_all(self, *_mocks):
        with (
            patch("codemie.configs.config.config.STANDALONE", True),
            patch("codemie.configs.config.config.GOOGLE_SEARCH_API_KEY", ""),
            patch("codemie.configs.config.config.TAVILY_API_KEY", ""),
            patch.object(CustomerConfig, "is_feature_enabled", return_value=True),
        ):
            lookup = self._research_tools(ToolsInfoService.get_tools_info())
            offered = self._research_tools(ToolsInfoService.get_tools_info(offered_only=True))
        assert len(lookup) == 6
        assert len(offered) == 2

    def test_web_search_flag_not_cached(self, *_mocks):
        with (
            patch("codemie.configs.config.config.STANDALONE", True),
            patch("codemie.configs.config.config.GOOGLE_SEARCH_API_KEY", "k"),
            patch("codemie.configs.config.config.TAVILY_API_KEY", "k"),
            patch.object(CustomerConfig, "is_feature_enabled", return_value=True) as flag,
        ):
            on = self._research_tools(ToolsInfoService.get_tools_info(offered_only=True))
            flag.return_value = False
            off = self._research_tools(ToolsInfoService.get_tools_info(offered_only=True))
        assert len(on) == 6
        assert len(off) == 2
