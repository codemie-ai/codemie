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

from typing import List, Any, Optional

from codemie_tools.base.base_toolkit import BaseToolkit
from codemie_tools.base.models import ToolKit, Tool, ToolSet
from langchain_core.tools import BaseTool

from codemie.agents.tools.code.read_files_tools import ReadFileFromStorageTool, ReadFileFromStorageWithSummaryTool
from codemie.agents.tools.code.tools import (
    SearchCodeRepoTool,
    GetRepoFileTreeTool,
    GetRepoFileTreeToolV2,
    SearchCodeRepoByPathsTool,
)
from codemie.agents.tools.code.tools_vars import (
    CODE_SEARCH_TOOL,
    REPO_TREE_TOOL,
    CODE_SEARCH_TOOL_V2,
    REPO_TREE_TOOL_V2,
    READ_FILES_TOOL,
    READ_FILES_WITH_SUMMARY_TOOL,
    CODE_SEARCH_BY_PATHS_TOOL,
)
from codemie.agents.utils import adapt_tool_name
from codemie.core.models import CodeFields, ChatMessage
from codemie.core.thread import ThreadedGenerator
from codemie.rest_api.models.index import IndexInfo


class CodeToolkitUI(ToolKit):
    toolkit: ToolSet = ToolSet.CODEBASE_TOOLS
    tools: List[Tool] = [
        Tool.from_metadata(REPO_TREE_TOOL_V2),
        Tool.from_metadata(CODE_SEARCH_TOOL_V2),
        Tool.from_metadata(READ_FILES_TOOL),
        Tool.from_metadata(READ_FILES_WITH_SUMMARY_TOOL),
        Tool.from_metadata(CODE_SEARCH_BY_PATHS_TOOL),
    ]


class CodeToolkit(BaseToolkit):
    @classmethod
    def get_tools_ui_info(cls, *args, **kwargs):
        return CodeToolkitUI().model_dump()

    @classmethod
    def get_tools_api_info(cls, *args, **kwargs):
        # This method contains complete tools list including basic context search tools
        # that are hidden from UI but required for functionality.
        # UI-visible tools are managed separately in get_tools_ui_info()
        data = CodeToolkitUI()
        data.tools.extend([Tool.from_metadata(REPO_TREE_TOOL), Tool.from_metadata(CODE_SEARCH_TOOL)])
        return data.model_dump()

    def get_tools(
        self,
        code_fields: CodeFields,
        llm_model: Any,
        history: List[ChatMessage],
        top_k: int,
        is_react: bool = True,
        thread_generator: ThreadedGenerator = None,
    ) -> List[BaseTool]:
        return [
            CodeToolkit.search_code_tool(code_fields=code_fields, top_k=top_k, is_react=is_react),
            CodeToolkit.get_repo_tree_tool(code_fields=code_fields, is_react=is_react),
        ]

    @classmethod
    def get_toolkit(cls, *args, **kwargs):
        # No need to create a toolkit object right now, will be replaced to tools
        pass

    @staticmethod
    def search_code_tool(
        code_fields: CodeFields,
        top_k: int,
        is_react: bool = True,
        user_input: str = None,
        with_filtering: Optional[bool] = False,
    ):
        tool_metadata = CODE_SEARCH_TOOL_V2 if with_filtering else CODE_SEARCH_TOOL
        name = CodeToolkit._tool_name(tool_metadata, code_fields)
        description = CodeToolkit._tool_description(tool_metadata, code_fields, is_react)

        return SearchCodeRepoTool(
            name=name,
            description=description,
            is_react=True,
            code_fields=code_fields,
            top_k=top_k,
            user_input=user_input,
            with_filtering=with_filtering,
        )

    @staticmethod
    def search_code_by_path_tool(code_fields: CodeFields, top_k: int, is_react: bool = True, user_input: str = None):
        name = CodeToolkit._tool_name(CODE_SEARCH_BY_PATHS_TOOL, code_fields)
        description = CodeToolkit._tool_description(CODE_SEARCH_BY_PATHS_TOOL, code_fields, is_react)

        return SearchCodeRepoByPathsTool(
            name=name,
            description=description,
            is_react=True,
            code_fields=code_fields,
            top_k=top_k,
            user_input=user_input,
        )

    @staticmethod
    def get_repo_tree_tool(
        code_fields: CodeFields, is_react: bool = True, user_input: str = None, with_filtering: Optional[bool] = False
    ):
        if with_filtering:
            name = CodeToolkit._tool_name(REPO_TREE_TOOL_V2, code_fields)
            description = CodeToolkit._tool_description(REPO_TREE_TOOL_V2, code_fields, is_react)

            return GetRepoFileTreeToolV2(
                code_fields=code_fields,
                name=name,
                description=description,
                with_filtering=with_filtering,
                user_input=user_input,
            )
        else:
            name = CodeToolkit._tool_name(REPO_TREE_TOOL, code_fields)
            description = CodeToolkit._tool_description(REPO_TREE_TOOL, code_fields, is_react)

            return GetRepoFileTreeTool(
                code_fields=code_fields, name=name, description=description, with_filtering=with_filtering
            )

    @staticmethod
    def read_files_tool(code_fields: CodeFields, is_react: bool = True):
        name = CodeToolkit._tool_name(READ_FILES_TOOL, code_fields)
        description = CodeToolkit._tool_description(READ_FILES_TOOL, code_fields, is_react)

        return ReadFileFromStorageTool(
            code_fields=code_fields,
            name=name,
            description=description,
        )

    @staticmethod
    def read_files_with_summary_tool(code_fields: CodeFields, is_react: bool = True):
        name = CodeToolkit._tool_name(READ_FILES_WITH_SUMMARY_TOOL, code_fields)
        description = CodeToolkit._tool_description(READ_FILES_WITH_SUMMARY_TOOL, code_fields, is_react)

        return ReadFileFromStorageWithSummaryTool(
            code_fields=code_fields,
            name=name,
            description=description,
        )

    @staticmethod
    def _tool_name(tool: BaseTool, code_fields: CodeFields):
        template = tool.name + "_{}"
        return adapt_tool_name(template, code_fields.repo_name)

    @staticmethod
    def _tool_description(tool: BaseTool, code_fields: CodeFields, is_react: bool):
        index_info = IndexInfo.filter_by_project_and_repo(
            project_name=code_fields.app_name, repo_name=code_fields.repo_name
        )[0]

        base_description = tool.description
        if is_react and hasattr(tool, "react_description") and tool.react_description:
            base_description = tool.react_description

        repo_description = f"\n\tName: {code_fields.repo_name}\n \t{index_info.description}\n"

        return base_description.format(repo_description)
