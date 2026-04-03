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

import logging
import os
from typing import List, Optional, Any, Dict

from langchain_core.language_models import BaseChatModel

from codemie_tools.base.base_toolkit import BaseToolkit
from codemie_tools.base.file_object import FileObject
from codemie_tools.base.models import ToolKit, ToolSet, Tool
from codemie_tools.data_management.code_executor.code_executor_tool import CodeExecutorTool
from codemie_tools.data_management.code_executor.local_code_executor_tool import LocalCodeExecutorTool
from codemie_tools.data_management.code_executor.tools_vars import CODE_EXECUTOR_TOOL, PYTHON_RUN_CODE_TOOL
from codemie_tools.data_management.file_system.generate_image_tool import GenerateImageTool
from codemie_tools.data_management.file_system.tools import (
    ListDirectoryTool,
    ReadFileTool,
    WriteFileTool,
    CommandLineTool,
    DiffUpdateFileTool,
    ReplaceStringTool,
)
from codemie_tools.data_management.file_system.tools_vars import (
    READ_FILE_TOOL,
    WRITE_FILE_TOOL,
    LIST_DIRECTORY_TOOL,
    COMMAND_LINE_TOOL,
    GENERATE_IMAGE_TOOL,
    DIFF_UPDATE_FILE_TOOL,
    REPLACE_STRING_TOOL,
)

logger = logging.getLogger(__name__)


class FileSystemToolkitUI(ToolKit):
    toolkit: ToolSet = ToolSet.FILE_SYSTEM
    settings_config: bool = True
    tools: List[Tool] = [
        Tool.from_metadata(READ_FILE_TOOL),
        Tool.from_metadata(WRITE_FILE_TOOL),
        Tool.from_metadata(LIST_DIRECTORY_TOOL),
        Tool.from_metadata(COMMAND_LINE_TOOL),
        Tool.from_metadata(PYTHON_RUN_CODE_TOOL),
        Tool.from_metadata(GENERATE_IMAGE_TOOL),
        Tool.from_metadata(DIFF_UPDATE_FILE_TOOL),
        Tool.from_metadata(REPLACE_STRING_TOOL),
        Tool.from_metadata(CODE_EXECUTOR_TOOL),
    ]
    label: str = ToolSet.FILE_MANAGEMENT_LABEL.value


class FileSystemToolkit(BaseToolkit):
    root_directory: Optional[str] = "."
    activate_command: Optional[str] = ""
    user_id: Optional[str] = ""
    file_repository: Optional[Any] = None
    image_generator: Optional[Any] = None
    chat_model: Optional[Any] = None
    code_isolation: bool = False
    input_files: Optional[List[Any]] = None

    @staticmethod
    def _is_file_system_tools_enabled() -> bool:
        """
        Check if file system tools should be enabled.

        Returns:
            bool: True if FILE_SYSTEM_TOOLS_ENABLED env var is set to "true"
        """
        return os.getenv("FILE_SYSTEM_TOOLS_ENABLED", "false").lower() == "true"

    @classmethod
    def get_tools_ui_info(cls, is_admin: bool = False):
        # Only show all tools if user is admin AND environment variable is enabled
        if is_admin and cls._is_file_system_tools_enabled():
            return FileSystemToolkitUI().model_dump()

        # Otherwise, return only safe tools
        return ToolKit(
            toolkit=ToolSet.FILE_SYSTEM,
            tools=[
                Tool.from_metadata(PYTHON_RUN_CODE_TOOL),
                Tool.from_metadata(GENERATE_IMAGE_TOOL),
                Tool.from_metadata(CODE_EXECUTOR_TOOL),
            ],
        ).model_dump()

    def get_tools(self) -> list:
        # Always include these safe tools
        tools = [
            LocalCodeExecutorTool(
                file_repository=self.file_repository,
                user_id=self.user_id,
            ),
            GenerateImageTool(
                image_generator=self.image_generator,
                file_repository=self.file_repository,
                user_id=self.user_id or "",
            ),
            CodeExecutorTool(
                file_repository=self.file_repository,
                user_id=self.user_id,
                input_files=self.input_files,
            ),
        ]

        # Only add file system tools if user is admin AND environment variable is enabled
        if self._is_file_system_tools_enabled():
            admin_tools = [
                ReadFileTool(root_dir=self.root_directory),
                ListDirectoryTool(root_dir=self.root_directory),
                WriteFileTool(root_dir=self.root_directory),
                CommandLineTool(root_dir=self.root_directory, activate_command=self.activate_command),
                DiffUpdateFileTool(root_dir=self.root_directory, llm_model=self.chat_model),
                ReplaceStringTool(root_dir=self.root_directory),
            ]
            tools.extend(admin_tools)

        return tools

    @classmethod
    def get_toolkit(
        cls,
        configs: Dict[str, Any],
        file_repository: Optional[Any] = None,
        chat_model: Optional[BaseChatModel] = None,
        image_generator: Optional[Any] = None,
        input_files: Optional[List[FileObject]] = None,
    ):
        root_directory = configs.get("root_directory", ".")
        activate_command = configs.get("activate_command", "")
        user_id = configs.get("user_id", "")

        return FileSystemToolkit(
            root_directory=root_directory,
            activate_command=activate_command,
            file_repository=file_repository,
            user_id=user_id,
            image_generator=image_generator,
            chat_model=chat_model,
            input_files=input_files,
        )
