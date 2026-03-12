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

from typing import List

from codemie_tools.base.base_toolkit import DiscoverableToolkit
from codemie_tools.base.models import ToolKit, ToolSet, Tool
from codemie_tools.azure_devops.wiki.tools import (
    GetWikiTool,
    ListWikisTool,
    ListPagesTool,
    GetWikiPageByPathTool,
    GetWikiPageByIdTool,
    DeletePageByPathTool,
    DeletePageByIdTool,
    CreateWikiPageTool,
    ModifyWikiPageTool,
    RenameWikiPageTool,
    MoveWikiPageTool,
    SearchWikiPagesTool,
    GetWikiPageCommentsByIdTool,
    GetWikiPageCommentsByPathTool,
    AddWikiAttachmentTool,
    GetPageStatsByIdTool,
    GetPageStatsByPathTool,
)
from codemie_tools.azure_devops.wiki.tools_vars import (
    GET_WIKI_TOOL,
    LIST_WIKIS_TOOL,
    LIST_PAGES_TOOL,
    GET_WIKI_PAGE_BY_PATH_TOOL,
    GET_WIKI_PAGE_BY_ID_TOOL,
    DELETE_PAGE_BY_PATH_TOOL,
    DELETE_PAGE_BY_ID_TOOL,
    CREATE_WIKI_PAGE_TOOL,
    MODIFY_WIKI_PAGE_TOOL,
    RENAME_WIKI_PAGE_TOOL,
    MOVE_WIKI_PAGE_TOOL,
    SEARCH_WIKI_PAGES_TOOL,
    GET_WIKI_PAGE_COMMENTS_BY_ID_TOOL,
    GET_WIKI_PAGE_COMMENTS_BY_PATH_TOOL,
    ADD_ATTACHMENT_TOOL,
    GET_PAGE_STATS_BY_ID_TOOL,
    GET_PAGE_STATS_BY_PATH_TOOL,
)


class AzureDevOpsWikiToolkitUI(ToolKit):
    toolkit: ToolSet = ToolSet.AZURE_DEVOPS_WIKI
    tools: List[Tool] = [
        Tool.from_metadata(GET_WIKI_TOOL, tool_class=GetWikiTool),
        Tool.from_metadata(LIST_WIKIS_TOOL, tool_class=ListWikisTool),
        Tool.from_metadata(LIST_PAGES_TOOL, tool_class=ListPagesTool),
        Tool.from_metadata(GET_WIKI_PAGE_BY_PATH_TOOL, tool_class=GetWikiPageByPathTool),
        Tool.from_metadata(GET_WIKI_PAGE_BY_ID_TOOL, tool_class=GetWikiPageByIdTool),
        Tool.from_metadata(DELETE_PAGE_BY_PATH_TOOL, tool_class=DeletePageByPathTool),
        Tool.from_metadata(DELETE_PAGE_BY_ID_TOOL, tool_class=DeletePageByIdTool),
        Tool.from_metadata(CREATE_WIKI_PAGE_TOOL, tool_class=CreateWikiPageTool),
        Tool.from_metadata(MODIFY_WIKI_PAGE_TOOL, tool_class=ModifyWikiPageTool),
        Tool.from_metadata(RENAME_WIKI_PAGE_TOOL, tool_class=RenameWikiPageTool),
        Tool.from_metadata(MOVE_WIKI_PAGE_TOOL, tool_class=MoveWikiPageTool),
        Tool.from_metadata(SEARCH_WIKI_PAGES_TOOL, tool_class=SearchWikiPagesTool),
        Tool.from_metadata(GET_WIKI_PAGE_COMMENTS_BY_ID_TOOL, tool_class=GetWikiPageCommentsByIdTool),
        Tool.from_metadata(GET_WIKI_PAGE_COMMENTS_BY_PATH_TOOL, tool_class=GetWikiPageCommentsByPathTool),
        Tool.from_metadata(ADD_ATTACHMENT_TOOL, tool_class=AddWikiAttachmentTool),
        Tool.from_metadata(GET_PAGE_STATS_BY_ID_TOOL, tool_class=GetPageStatsByIdTool),
        Tool.from_metadata(GET_PAGE_STATS_BY_PATH_TOOL, tool_class=GetPageStatsByPathTool),
    ]
    label: str = ToolSet.AZURE_DEVOPS_WIKI.value
    settings_config: bool = True


class AzureDevOpsWikiToolkit(DiscoverableToolkit):
    @classmethod
    def get_definition(cls):
        return AzureDevOpsWikiToolkitUI()
