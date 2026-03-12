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
import re
import subprocess
import time
from pathlib import Path
from typing import List, Literal, Type, Optional, Any

from langchain_community.tools.file_management.utils import FileValidationError, INVALID_PATH_TEMPLATE
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import ToolException
from openhands_aci.editor.editor import OHEditor
from openhands_aci.editor.exceptions import ToolError
from pydantic import BaseModel, Field

from codemie_tools.base.codemie_tool import CodeMieTool
from codemie_tools.code.coder.diff_update_coder import update_content_by_task
from codemie_tools.data_management.file_system.tools_vars import (
    READ_FILE_TOOL,
    LIST_DIRECTORY_TOOL,
    WRITE_FILE_TOOL,
    COMMAND_LINE_TOOL,
    DIFF_UPDATE_FILE_TOOL,
    REPLACE_STRING_TOOL,
)
from codemie_tools.data_management.file_system.utils import get_relative_path, create_folders

logger = logging.getLogger(__name__)


class ReadFileInput(BaseModel):
    file_path: str = Field(..., description="File path to read from file system")


class ReadFileTool(CodeMieTool):
    name: str = READ_FILE_TOOL.name
    args_schema: Type[BaseModel] = ReadFileInput
    description: str = READ_FILE_TOOL.description
    root_dir: Optional[str] = "."

    def execute(self, file_path: str, raise_error=False, *args, **kwargs) -> str:
        try:
            read_path = get_relative_path(self.root_dir, file_path)
        except FileValidationError:
            return INVALID_PATH_TEMPLATE.format(arg_name="file_path", value=file_path)
        if not read_path.exists():
            if raise_error:
                raise FileNotFoundError(f"Error: no such file or directory: {file_path}")
            else:
                return f"Error: no such file or directory: {file_path}"
        try:
            with read_path.open("r", encoding="utf-8") as f:
                content = f.read()
            return content
        except Exception as e:
            return f"Error: {e}"


class DirectoryListingInput(BaseModel):
    """Input for ListDirectoryTool."""

    dir_path: str = Field(default=".", description="Subdirectory to list.")


class ListDirectoryTool(CodeMieTool):
    """Tool that lists files and directories in a specified folder."""

    name: str = LIST_DIRECTORY_TOOL.name
    args_schema: Type[BaseModel] = DirectoryListingInput
    description: str = LIST_DIRECTORY_TOOL.description
    root_dir: Optional[str] = "."

    def execute(self, dir_path: str = ".", *args, **kwargs) -> str:
        try:
            dir_path_ = get_relative_path(self.root_dir, dir_path)
        except FileValidationError:
            return INVALID_PATH_TEMPLATE.format(arg_name="dir_path", value=dir_path)
        try:
            entries = os.listdir(dir_path_)
            if entries:
                return "\n".join(entries)
            else:
                return f"No files found in directory {dir_path}"
        except Exception as e:
            return f"Error: {e}"


class WriteFileInput(BaseModel):
    file_path: str = Field(..., description="File path to write to file system")
    text: str = Field(..., description="Content or text to write to file.")


class WriteFileTool(CodeMieTool):
    name: str = WRITE_FILE_TOOL.name
    args_schema: Type[BaseModel] = WriteFileInput
    description: str = WRITE_FILE_TOOL.description
    root_dir: Optional[str] = "."

    def execute(self, file_path: str, text: str) -> str:
        try:
            write_path = get_relative_path(self.root_dir, file_path)
        except FileValidationError:
            return INVALID_PATH_TEMPLATE.format(arg_name="file_path", value=file_path)
        try:
            create_folders(write_path)
            write_path.parent.mkdir(exist_ok=True, parents=False)
            mode = "w"
            with write_path.open(mode, encoding="utf-8") as f:
                f.write(text)

            return f"File written successfully to {file_path}"
        except Exception as e:
            return f"Error: {e}"


class CommandLineInput(BaseModel):
    command: str = Field(description="Command to execute in the CLI.")


class DiffUpdateFileToolInput(BaseModel):
    task_details: str = Field(
        description="""String. Specify detailed task description for file which must be updated
    or provide detailed generated reference what should be done"""
    )
    file_path: str = Field(..., description="File path of the file that should be updated by task details")
    should_create: bool = Field(default=False, description="Whether the file should be created if it does not exist.")


class CommandLineTool(CodeMieTool):
    name: str = COMMAND_LINE_TOOL.name
    description: str = COMMAND_LINE_TOOL.description
    args_schema: Type[BaseModel] = CommandLineInput
    root_dir: Optional[str] = "."
    activate_command: Optional[str] = ""
    timeout: int = 60

    def __init__(self, root_dir: str = ".", activate_command: str = ""):
        super().__init__()
        self.root_dir = root_dir
        self.activate_command = activate_command

    def execute(self, command: str, *args, **kwargs) -> Any:
        work_dir = Path(self.root_dir)
        work_dir.mkdir(exist_ok=True)

        command_start_time = int(round(time.time() * 1000))

        # Add activate command before the command
        activate_command = self.activate_command.strip()
        full_command = f"{activate_command} && {command}" if activate_command else command

        CommandLineTool.sanitize_command(full_command)

        result = subprocess.run(
            full_command, cwd=work_dir, shell=True, text=True, capture_output=True, timeout=float(self.timeout)
        )

        # Return a dictionary with the desired information
        return result.stdout, result.stderr, result.returncode, command_start_time

    @staticmethod
    def sanitize_command(command: str) -> None:
        """
        Sanitize the code block to prevent dangerous commands.
        This approach acknowledges that while Docker or similar
        containerization/sandboxing technologies provide a robust layer of security,
        not all users may have Docker installed or may choose not to use it.
        Therefore, having a baseline level of protection helps mitigate risks for users who,
        either out of choice or necessity, run code outside of a sandboxed environment.
        """
        dangerous_patterns = [
            (r"\brm\s+-rf\b", "Use of 'rm -rf' command is not allowed."),
            (r"\bmv\b.*?\s+/dev/null", "Moving files to /dev/null is not allowed."),
            (r"\bdd\b", "Use of 'dd' command is not allowed."),
            (r">\s*/dev/sd[a-z][1-9]?", "Overwriting disk blocks directly is not allowed."),
            (r":\(\)\{\s*:\|\:&\s*\};:", "Fork bombs are not allowed."),
        ]
        for pattern, message in dangerous_patterns:
            if re.search(pattern, command):
                logger.error(f"Potentially dangerous command detected: {message}")
                raise ToolException(f"Potentially dangerous command detected: {message}")


class DiffUpdateFileTool(CodeMieTool):
    name: str = DIFF_UPDATE_FILE_TOOL.name
    args_schema: Type[BaseModel] = DiffUpdateFileToolInput
    description: str = DIFF_UPDATE_FILE_TOOL.description
    root_dir: Optional[str] = "."
    llm_model: Optional[BaseChatModel] = Field(exclude=True)
    handle_validation_error: bool = True

    def execute(self, file_path: str, task_details: str, should_create: bool = False) -> str:
        read_path = get_relative_path(self.root_dir, file_path)
        if not read_path.exists():
            if should_create:
                create_folders(read_path)
                read_path.touch()
            else:
                return f"Error: no such file or directory: {file_path}"
        with read_path.open("r", encoding="utf-8") as f:
            content = f.read()

        new_content, edits = update_content_by_task(content, task_details, self.llm_model)

        with read_path.open("w", encoding="utf-8") as f:
            f.write(new_content)

        return f"Changes have been successfully applied to the file {file_path}:\n{edits}"


class ReplaceStringInput(BaseModel):
    command: Literal['view', 'create', 'str_replace', 'insert', 'undo_edit'] = Field(
        ...,
        description="The commands to run. Allowed options are: `view`, `create`, `str_replace`, `insert`, `undo_edit`.",
    )

    path: str = Field(..., description="Relative path to file or directory, e.g. `src/file.py` or `src`.")

    file_text: Optional[str] = Field(
        None, description="Required parameter of `create` command, with the content of the file to be created."
    )

    old_str: Optional[str] = Field(
        None, description="Required parameter of `str_replace` command containing the string in `path` to replace."
    )

    new_str: Optional[str] = Field(
        None,
        description="Optional parameter of `str_replace` command containing the new string (if not given, no string will be added). Required parameter of `insert` command containing the string to insert.",
    )

    insert_line: Optional[int] = Field(
        None,
        description="Required parameter of `insert` command. The `new_str` will be inserted AFTER the line `insert_line` of `path`.",
    )

    view_range: Optional[List[int]] = Field(
        None,
        description="Optional parameter of `view` command when `path` points to a file. Ifnone is given, the full file is shown. If provided, the file will be shown in the indicated line number range, e.g. [11, 12] will show lines 11 and 12. Indexing at 1 to start. Setting `[start_line, -1]` shows all lines from `start_line` to the end of the file.",
    )

    model_config = {
        "extra": "forbid"  # This ensures no extra fields are allowed
    }


Command = Literal[
    'view',
    'create',
    'str_replace',
    'insert',
    'undo_edit',
]


class ReplaceStringTool(CodeMieTool):
    name: str = REPLACE_STRING_TOOL.name
    args_schema: Type[BaseModel] = ReplaceStringInput
    description: str = REPLACE_STRING_TOOL.description
    root_dir: Optional[str] = "."
    handle_validation_error: bool = True
    editor: Optional[OHEditor] = Field(default_factory=OHEditor)

    def execute(
        self,
        command: Command,
        path: str,
        file_text: str | None = None,
        view_range: list[int] | None = None,
        old_str: str | None = None,
        new_str: str | None = None,
        insert_line: int | None = None,
    ) -> str:
        try:
            full_path = Path(self.root_dir) / path
            result = self.editor(
                command=command,
                path=full_path,
                file_text=file_text,
                view_range=view_range,
                old_str=old_str,
                new_str=new_str,
                insert_line=insert_line,
                enable_linting=False,
            )
            tool_result = result.output
        except ToolError as e:
            tool_result = f'ERROR:\n{e.message}'
        tool_result = tool_result.replace(self.root_dir + '/', '')
        return tool_result
