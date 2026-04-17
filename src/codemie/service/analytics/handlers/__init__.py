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

"""Analytics handlers for domain-specific operations."""

from __future__ import annotations

from codemie.service.analytics.handlers.assistant_handler import AssistantHandler
from codemie.service.analytics.handlers.budget_handler import BudgetHandler
from codemie.service.analytics.handlers.cli import CLIHandler, CLIInsightsHandler
from codemie.service.analytics.handlers.llm_handler import LLMHandler
from codemie.service.analytics.handlers.mcp_handler import MCPHandler
from codemie.service.analytics.handlers.project_handler import ProjectHandler
from codemie.service.analytics.handlers.summary_handler import SummaryHandler
from codemie.service.analytics.handlers.tools_handler import ToolsHandler
from codemie.service.analytics.handlers.user_handler import UserHandler
from codemie.service.analytics.handlers.webhook_handler import WebhookHandler
from codemie.service.analytics.handlers.workflow_handler import WorkflowHandler

__all__ = [
    "AssistantHandler",
    "BudgetHandler",
    "CLIHandler",
    "CLIInsightsHandler",
    "LLMHandler",
    "MCPHandler",
    "ProjectHandler",
    "SummaryHandler",
    "ToolsHandler",
    "UserHandler",
    "WebhookHandler",
    "WorkflowHandler",
]
