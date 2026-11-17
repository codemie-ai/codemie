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

"""CodeToolkitUI must list the standard repo-tree and search-code tools for the UI
(EPMCDME-15114 Task 7), so a user can see them in order to deselect them."""

from unittest.mock import Mock, patch

import pytest

from codemie.agents.tools.code.code_toolkit import CodeToolkit
from codemie.agents.tools.code.tools_vars import (
    CODE_SEARCH_BY_PATHS_TOOL,
    CODE_SEARCH_TOOL_NO_REPO_TREE,
    CODE_SEARCH_TOOL_V2_NO_REPO_TREE,
    READ_FILES_TOOL,
    READ_FILES_WITH_SUMMARY_TOOL,
)

TREE_REFERENCES = ("repository tree", "repo tree", "file tree", "get_repository_file_tree", "first tool")


def _assert_no_tree_reference(text: str) -> None:
    lowered = text.lower()
    for reference in TREE_REFERENCES:
        assert reference not in lowered, f"found '{reference}' in: {text}"


class TestRemainingCodeToolsWithoutRepoTree:
    @pytest.mark.parametrize(
        "metadata",
        [
            CODE_SEARCH_TOOL_NO_REPO_TREE,
            CODE_SEARCH_TOOL_V2_NO_REPO_TREE,
            CODE_SEARCH_BY_PATHS_TOOL,
            READ_FILES_TOOL,
            READ_FILES_WITH_SUMMARY_TOOL,
        ],
        ids=lambda metadata: metadata.name,
    )
    def test_metadata_does_not_reference_repo_tree(self, metadata):
        _assert_no_tree_reference(metadata.description)
        _assert_no_tree_reference(metadata.react_description)

    @pytest.mark.parametrize("with_filtering", [False, True], ids=["search", "search_v2"])
    def test_search_tool_built_without_repo_tree_has_no_tree_reference(self, with_filtering):
        code_fields = Mock(app_name="app", repo_name="repo")
        with (
            patch.object(CodeToolkit, "_get_index_info", return_value=None),
            patch("codemie.agents.tools.code.code_toolkit.SearchCodeRepoTool") as tool_cls,
        ):
            CodeToolkit.search_code_tool(
                code_fields=code_fields,
                top_k=5,
                with_filtering=with_filtering,
                standard_tree_deselected=True,
            )

        _assert_no_tree_reference(tool_cls.call_args.kwargs["description"])

    def test_standard_search_built_with_repo_tree_keeps_tree_reference(self):
        code_fields = Mock(app_name="app", repo_name="repo")
        with (
            patch.object(CodeToolkit, "_get_index_info", return_value=None),
            patch("codemie.agents.tools.code.code_toolkit.SearchCodeRepoTool") as tool_cls,
        ):
            CodeToolkit.search_code_tool(code_fields=code_fields, top_k=5, standard_tree_deselected=False)

        assert "from repository tree" in tool_cls.call_args.kwargs["description"]

    def test_repo_tree_v2_drops_first_tool_instruction_only_when_standard_tree_deselected(self):
        code_fields = Mock(app_name="app", repo_name="repo")
        with (
            patch.object(CodeToolkit, "_get_index_info", return_value=None),
            patch("codemie.agents.tools.code.code_toolkit.GetRepoFileTreeToolV2") as tree_cls,
        ):
            CodeToolkit.get_repo_tree_tool(code_fields=code_fields, with_filtering=True, standard_tree_deselected=True)
            deselected_description = tree_cls.call_args.kwargs["description"]
            CodeToolkit.get_repo_tree_tool(code_fields=code_fields, with_filtering=True, standard_tree_deselected=False)
            kept_description = tree_cls.call_args.kwargs["description"]

        assert "first tool" not in deselected_description.lower()
        assert "first tool" in kept_description.lower()
        assert "Then, you must uses" in deselected_description


class TestCodeToolkitUIListsStandardTools:
    def test_code_toolkit_ui_lists_standard_repo_tree_and_search(self):
        ui = CodeToolkit.get_tools_ui_info()
        names = {t["name"] for t in ui["tools"]}
        assert "get_repository_file_tree" in names
        assert "search_code_repo" in names

    def test_api_info_does_not_duplicate_the_standard_tools(self):
        api_info = CodeToolkit.get_tools_api_info()
        names = [t["name"] for t in api_info["tools"]]
        assert names.count("get_repository_file_tree") == 1
        assert names.count("search_code_repo") == 1
