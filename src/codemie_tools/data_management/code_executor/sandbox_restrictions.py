# Copyright 2026 EPAM Systems, Inc. ("EPAM")
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

"""The model-facing statement of what a sandboxed script may import and do, shown in the script tool's description.

It is built from the same security policy the executor enforces, so the text cannot drift from the behaviour. It does
not depend on the tool-calling bridge: a script is bound by these rules whether or not that bridge is on.
"""

from __future__ import annotations

from pathlib import Path

from codemie_tools.data_management.code_executor.models import CodeExecutorConfig
from codemie_tools.data_management.code_executor.security_policies import get_restricted_module_names
from codemie_tools.data_management.code_executor.tools_vars import (
    COMMON_SANDBOX_LIBRARIES,
    COMMON_SANDBOX_SYSTEM_TOOLS,
    SAFE_STDLIB_MODULES,
)


def read_sandbox_restrictions(config: CodeExecutorConfig | None = None) -> str:
    """The restrictions section for the script tool's description, from ``config`` (default: the environment's)."""
    effective = config or CodeExecutorConfig.from_env()
    yaml_path = Path(effective.yaml_policy_path) if effective.yaml_policy_path else None
    blocked = get_restricted_module_names(severity_threshold=effective.security_threshold, yaml_config_path=yaml_path)
    blocked_text = ", ".join(blocked) if blocked else "None (unrestricted mode)"
    return (
        "## Script restrictions\n\n"
        "The script runs in an isolated sandbox and is checked before it starts; a script that breaks these rules "
        "fails, so remove the restricted operation instead of retrying it.\n\n"
        "- Python only. Use only the pre-installed libraries or the standard library; any other import fails, and "
        "packages cannot be installed at run time.\n"
        f"- Pre-installed libraries: {', '.join(COMMON_SANDBOX_LIBRARIES)}\n"
        f"- Safe standard library modules: {SAFE_STDLIB_MODULES}\n"
        f"- Blocked modules and calls: {blocked_text}\n"
        f"- System tools present in the sandbox: {', '.join(COMMON_SANDBOX_SYSTEM_TOOLS)}\n"
        "- File access is limited to the workspace directory; paths outside it are denied.\n"
        f"- A run is stopped after {effective.execution_timeout:g} seconds."
    )
