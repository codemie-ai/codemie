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
from abc import abstractmethod
from typing import Tuple

from codemie_tools.base.codemie_tool import CodeMieTool
from codemie_tools.git.utils import GitCredentials

logger = logging.getLogger(__name__)


class UpdateFileGitTool(CodeMieTool):
    credentials: GitCredentials
    llm_model: str
    handle_tool_error: bool = True

    def execute(self, file_path: str, task_details: str, commit_message: str, *args):
        try:
            legacy_content = self.read_file(file_path)
            result_content, edits = self.update_content(legacy_content, task_details)
            self.update_file(file_path, result_content, commit_message)
        except Exception as e:
            logger.error(f"Error during updating file {file_path}: {e}")
            return f"Error: {str(e)}"
        return f"Changes have been successfully applied to the file {file_path}:\n{edits}"

    @abstractmethod
    def read_file(self, file_path):
        pass

    @abstractmethod
    def update_content(self, legacy_content: str, task_details: str) -> Tuple[str, str]:
        """
        Abstract method to update the content of a file based on the given task details.

        Parameters:
        legacy_content (str): The current content of the file where changes should be made.
        task_details (str): The task that should be implemented in the file content.

        Returns:
        Tuple[str, str]: A tuple containing:
            - The new file content that has the task implemented.
            - A list of edits that were applied during the update.
        """
        pass

    @abstractmethod
    def update_file(self, file_path, new_content, commit_message):
        pass
