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

import functools
import logging
from typing import Optional, Any

from codemie_tools.data_management.code_executor.code_executor_tool import CodeExecutorTool
from codemie_tools.data_management.code_executor.models import ExecutionMode
from codemie_tools.data_management.code_executor.tools_vars import PYTHON_RUN_CODE_TOOL

logger = logging.getLogger(__name__)


@functools.lru_cache(maxsize=None)
def warn_once() -> None:
    """Warn once about the dangers of PythonREPL."""
    logger.warning("Python REPL can execute arbitrary code. Use with caution.")


class LocalCodeExecutorTool(CodeExecutorTool):
    """
    Tool for executing Python code using local execution mode.

    This tool extends CodeExecutorTool and forces local execution mode
    for backward compatibility with the legacy PythonREPL implementation.
    It provides a simplified interface with the python_script parameter
    instead of the generic code parameter.

    The args_schema (PythonRunCodeInput) is automatically set by parent class
    when execution_mode is LOCAL.
    """

    def __init__(self, file_repository: Optional[Any] = None, user_id: Optional[str] = "test"):
        """
        Initialize LocalCodeExecutorTool with local execution mode.

        Args:
            file_repository: Optional file repository for storing generated files
            user_id: User ID for file ownership attribution
        """
        super().__init__(
            file_repository=file_repository,
            user_id=user_id,
            execution_mode=ExecutionMode.LOCAL,
        )
        self.name = PYTHON_RUN_CODE_TOOL.name
