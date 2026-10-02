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

from __future__ import annotations

import math

from codemie.configs import logger
from codemie.configs.customer_config import customer_config

WORKSPACE_SCRIPT_BRIDGE_FEATURE = "workspaceScriptBridge"
DEFAULT_TOOL_CALLING_TIMEOUT_SECONDS = 120.0


def parse_tool_calling_timeout(raw_value: object) -> float:
    """Turn the customer-config `timeoutSeconds` value into a positive, finite float.

    Never raises: a missing, non-numeric, non-finite or non-positive value must not fail a run,
    so it falls back to DEFAULT_TOOL_CALLING_TIMEOUT_SECONDS with a warning.
    """
    parsed: float | None = None
    # bool is an int subclass, but `true` is never a meaningful timeout
    if isinstance(raw_value, (int, float, str)) and not isinstance(raw_value, bool):
        try:
            parsed = float(raw_value)
        except (ValueError, OverflowError):
            # OverflowError: an int too large for float, e.g. 10**400 written in the YAML
            parsed = None
    if parsed is not None and math.isfinite(parsed) and parsed > 0:
        return parsed
    logger.warning(
        f"Invalid {WORKSPACE_SCRIPT_BRIDGE_FEATURE} timeoutSeconds (type={type(raw_value).__name__}), "
        f"using default {DEFAULT_TOOL_CALLING_TIMEOUT_SECONDS}s"
    )
    return DEFAULT_TOOL_CALLING_TIMEOUT_SECONDS


def resolve_tool_calling_timeout() -> float | None:
    """Timeout for script tool calls in this run, or None when the feature is off.

    Customer config is read on every call.
    """
    if not customer_config.is_feature_enabled(WORKSPACE_SCRIPT_BRIDGE_FEATURE):
        return None
    return parse_tool_calling_timeout(
        customer_config.get_feature_setting(WORKSPACE_SCRIPT_BRIDGE_FEATURE, "timeoutSeconds")
    )
