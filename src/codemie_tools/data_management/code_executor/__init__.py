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

"""Code executor package for secure Python code execution.

The public names are imported on first use, not when the package is imported: a module that only needs a small part
of the package (the tool-call protocol, the SDK constants) must not load the sandbox stack, including ``kubernetes``,
as a side effect of importing its parent package.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from codemie_tools.data_management.code_executor.code_executor_tool import CodeExecutorTool
    from codemie_tools.data_management.code_executor.llm_sandbox import apply_llm_sandbox_patch
    from codemie_tools.data_management.code_executor.models import CodeExecutorConfig, ExecutionMode

__all__ = ["CodeExecutorConfig", "CodeExecutorTool", "ExecutionMode", "apply_llm_sandbox_patch"]

_EXPORTS: dict[str, str] = {
    "CodeExecutorConfig": "models",
    "CodeExecutorTool": "code_executor_tool",
    "ExecutionMode": "models",
    "apply_llm_sandbox_patch": "llm_sandbox",
}


def __getattr__(name: str) -> object:
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(import_module(f"{__name__}.{module_name}"), name)
