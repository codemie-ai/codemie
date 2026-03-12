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

from __future__ import annotations

# Service management (from dependencies.py)
from .dependencies import (
    is_langfuse_enabled,
    initialize_langfuse_from_config,
    get_global_langfuse_service,
    set_global_langfuse_service,
    get_langfuse_service,
    get_langfuse_callback_handler,
    require_langfuse_client,
    get_langfuse_client_or_none,
)

# Workflow traces (from workflows.py)
from .workflows import (
    create_workflow_trace_context,
    get_workflow_trace_context,
    clear_workflow_trace_context,
    build_agent_metadata_with_workflow_context,
)

__all__ = [
    # Service management
    "is_langfuse_enabled",
    "initialize_langfuse_from_config",
    "get_global_langfuse_service",
    "set_global_langfuse_service",
    "get_langfuse_service",
    "get_langfuse_callback_handler",
    "require_langfuse_client",
    "get_langfuse_client_or_none",
    # Workflow traces
    "create_workflow_trace_context",
    "get_workflow_trace_context",
    "clear_workflow_trace_context",
    "build_agent_metadata_with_workflow_context",
]
