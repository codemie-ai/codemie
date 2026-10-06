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

"""Reads the workspace script bridge settings once per run: is tool calling on, and with which limits."""

from __future__ import annotations

from codemie.configs import config
from codemie.configs.customer_config import customer_config
from codemie_tools.data_management.code_executor.models import CodeExecutorConfig, SandboxMode
from codemie_tools.data_management.code_executor.tool_calling_limits import (
    ToolCallingSettings,
    normalize_max_parallel_calls,
    normalize_run_timeout,
)

WORKSPACE_SCRIPT_BRIDGE_FEATURE = "workspaceScriptBridge"


def resolve_tool_calling_settings() -> ToolCallingSettings | None:
    """The settings of a script run with tool calling, or ``None`` when tool calling is off.

    It is on only when the component is enabled **and** the sandbox runs in jobs mode: the bridge exists only there
    (a pooled sandbox has none, and advertising an SDK that cannot work would only mislead the model). Customer config
    is read here and nowhere else. A missing or invalid value falls back to its default and an oversized one is
    clamped (see the ``normalize_*`` functions); neither turns the feature off.
    """
    if not customer_config.is_feature_enabled(WORKSPACE_SCRIPT_BRIDGE_FEATURE):
        return None
    if CodeExecutorConfig.from_env().sandbox_mode != SandboxMode.JOBS:
        return None
    return ToolCallingSettings(
        run_timeout_seconds=normalize_run_timeout(
            customer_config.get_feature_setting(WORKSPACE_SCRIPT_BRIDGE_FEATURE, "timeoutSeconds")
        ),
        max_parallel_calls=normalize_max_parallel_calls(
            customer_config.get_feature_setting(WORKSPACE_SCRIPT_BRIDGE_FEATURE, "maxParallelCalls"),
            config.WORKSPACE_SCRIPT_TOOL_CALLS_MAX_CONCURRENT,
        ),
    )
