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

"""Main analytics service for dashboard metrics.

This module orchestrates analytics queries by delegating to domain-specific handlers.
"""

from __future__ import annotations

import asyncio
from datetime import datetime

from codemie.repository.metrics_elastic_repository import MetricsElasticRepository
from codemie.rest_api.security.user import User
from codemie.service.analytics.handlers.ai_adoption_handler import AIAdoptionHandler
from codemie.service.analytics.handlers.assistant_handler import AssistantHandler
from codemie.service.analytics.handlers.budget_handler import BudgetHandler
from codemie.service.analytics.handlers.cli_handler import CLIHandler
from codemie.service.analytics.handlers.embeddings_handler import EmbeddingsHandler
from codemie.service.analytics.handlers.engagement_handler import EngagementHandler
from codemie.service.analytics.handlers.llm_handler import LLMHandler
from codemie.service.analytics.handlers.mcp_handler import MCPHandler
from codemie.service.analytics.handlers.project_handler import ProjectHandler
from codemie.service.analytics.handlers.summary_handler import SummaryHandler
from codemie.service.analytics.handlers.tools_handler import ToolsHandler
from codemie.service.analytics.handlers.user_handler import UserHandler
from codemie.service.analytics.handlers.webhook_handler import WebhookHandler
from codemie.service.analytics.handlers.workflow_handler import WorkflowHandler
from codemie.service.analytics.queries.ai_adoption_framework.config import AIAdoptionConfig


class AnalyticsService:
    """Main service for analytics dashboard operations.

    This service acts as a facade, delegating to specialized handlers for each analytics domain.
    """

    def __init__(self, user: User):
        """Initialize analytics service with user context.

        Args:
            user: Authenticated user for access control and filtering
        """
        self._user = user
        self._repository = MetricsElasticRepository()

        # Lazy-loaded handlers (created on first access)
        self._adoption_handler_instance: AIAdoptionHandler | None = None
        self._summary_handler_instance: SummaryHandler | None = None
        self._assistant_handler_instance: AssistantHandler | None = None
        self._workflow_handler_instance: WorkflowHandler | None = None
        self._tools_handler_instance: ToolsHandler | None = None
        self._user_handler_instance: UserHandler | None = None
        self._project_handler_instance: ProjectHandler | None = None
        self._cli_handler_instance: CLIHandler | None = None
        self._budget_handler_instance: BudgetHandler | None = None
        self._webhook_handler_instance: WebhookHandler | None = None
        self._mcp_handler_instance: MCPHandler | None = None
        self._llm_handler_instance: LLMHandler | None = None
        self._embeddings_handler_instance: EmbeddingsHandler | None = None
        self._engagement_handler_instance: EngagementHandler | None = None

    @property
    def _adoption_handler(self) -> AIAdoptionHandler:
        """Lazy-load adoption handler."""
        if self._adoption_handler_instance is None:
            self._adoption_handler_instance = AIAdoptionHandler(self._user)
        return self._adoption_handler_instance

    @property
    def _summary_handler(self) -> SummaryHandler:
        """Lazy-load summary handler."""
        if self._summary_handler_instance is None:
            self._summary_handler_instance = SummaryHandler(self._user, self._repository)
        return self._summary_handler_instance

    @property
    def _assistant_handler(self) -> AssistantHandler:
        """Lazy-load assistant handler."""
        if self._assistant_handler_instance is None:
            self._assistant_handler_instance = AssistantHandler(self._user, self._repository)
        return self._assistant_handler_instance

    @property
    def _workflow_handler(self) -> WorkflowHandler:
        """Lazy-load workflow handler."""
        if self._workflow_handler_instance is None:
            self._workflow_handler_instance = WorkflowHandler(self._user, self._repository)
        return self._workflow_handler_instance

    @property
    def _tools_handler(self) -> ToolsHandler:
        """Lazy-load tools handler."""
        if self._tools_handler_instance is None:
            self._tools_handler_instance = ToolsHandler(self._user, self._repository)
        return self._tools_handler_instance

    @property
    def _user_handler(self) -> UserHandler:
        """Lazy-load user handler."""
        if self._user_handler_instance is None:
            self._user_handler_instance = UserHandler(self._user, self._repository)
        return self._user_handler_instance

    @property
    def _project_handler(self) -> ProjectHandler:
        """Lazy-load project handler."""
        if self._project_handler_instance is None:
            self._project_handler_instance = ProjectHandler(self._user, self._repository)
        return self._project_handler_instance

    @property
    def _cli_handler(self) -> CLIHandler:
        """Lazy-load CLI handler."""
        if self._cli_handler_instance is None:
            self._cli_handler_instance = CLIHandler(self._user, self._repository)
        return self._cli_handler_instance

    @property
    def _budget_handler(self) -> BudgetHandler:
        """Lazy-load budget handler."""
        if self._budget_handler_instance is None:
            self._budget_handler_instance = BudgetHandler(self._user, self._repository)
        return self._budget_handler_instance

    @property
    def _webhook_handler(self) -> WebhookHandler:
        """Lazy-load webhook handler."""
        if self._webhook_handler_instance is None:
            self._webhook_handler_instance = WebhookHandler(self._user, self._repository)
        return self._webhook_handler_instance

    @property
    def _mcp_handler(self) -> MCPHandler:
        """Lazy-load MCP handler."""
        if self._mcp_handler_instance is None:
            self._mcp_handler_instance = MCPHandler(self._user, self._repository)
        return self._mcp_handler_instance

    @property
    def _llm_handler(self) -> LLMHandler:
        """Lazy-load LLM handler."""
        if self._llm_handler_instance is None:
            self._llm_handler_instance = LLMHandler(self._user, self._repository)
        return self._llm_handler_instance

    @property
    def _embeddings_handler(self) -> EmbeddingsHandler:
        """Lazy-load embeddings handler."""
        if self._embeddings_handler_instance is None:
            self._embeddings_handler_instance = EmbeddingsHandler(self._user, self._repository)
        return self._embeddings_handler_instance

    @property
    def _engagement_handler(self) -> EngagementHandler:
        """Lazy-load engagement handler."""
        if self._engagement_handler_instance is None:
            self._engagement_handler_instance = EngagementHandler(self._user, self._repository)
        return self._engagement_handler_instance

    # Summary endpoints
    async def get_summaries(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
    ) -> dict:
        """Get summary metrics: tokens, costs, usage statistics, DAU and MAU.

        DAU and MAU are fetched concurrently and appended to the base summaries.
        They always reflect all-time data (ignoring the time filter).
        """
        base, dau, mau = await asyncio.gather(
            self._summary_handler.get_summaries(
                time_period=time_period,
                start_date=start_date,
                end_date=end_date,
                users=users,
                projects=projects,
            ),
            self._engagement_handler.get_dau(users=users, projects=projects),
            self._engagement_handler.get_mau(users=users, projects=projects),
        )
        metrics = base["data"]["metrics"]
        metrics[8:8] = dau["data"]["metrics"] + mau["data"]["metrics"]
        return base

    # Assistant endpoints
    async def get_assistants_chats(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
    ) -> dict:
        """Get assistants/chats analytics with performance metrics."""
        return await self._assistant_handler.get_assistants_chats(
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
        )

    async def get_agents_usage(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 30,
    ) -> dict:
        """Get assistant and tool usage analytics."""
        return await self._assistant_handler.get_agents_usage(
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
        )

    # Workflow endpoints
    async def get_workflows(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
    ) -> dict:
        """Get workflow execution analytics."""
        return await self._workflow_handler.get_workflows(
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
        )

    # Tools endpoints
    async def get_tools_usage(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
    ) -> dict:
        """Get tools usage analytics."""
        return await self._tools_handler.get_tools_usage(
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
        )

    # User endpoints
    async def get_users_spending(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
    ) -> dict:
        """Get users spending analytics."""
        return await self._user_handler.get_users_spending(
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
        )

    async def get_users_platform_spending(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
    ) -> dict:
        """Get platform spending per user (Assistants + Workflows + Datasources, no CLI)."""
        return await self._user_handler.get_users_platform_spending(
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
        )

    async def get_users_cli_spending(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
    ) -> dict:
        """Get CLI-only spending per user (grouped by user_name)."""
        return await self._user_handler.get_users_cli_spending(
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
        )

    async def get_users_activity(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
    ) -> dict:
        """Get users activity analytics."""
        return await self._user_handler.get_users_activity(
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
        )

    async def get_users_unique_daily(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
    ) -> dict:
        """Get unique users per day analytics."""
        return await self._user_handler.get_users_unique_daily(
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
        )

    async def get_users_list(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
    ) -> dict:
        """Get list of unique users from metrics logs."""
        return await self._user_handler.get_users_list(
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
        )

    # Project endpoints
    async def get_projects_spending(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
    ) -> dict:
        """Get projects spending analytics."""
        return await self._project_handler.get_projects_spending(
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
        )

    async def get_projects_activity(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
    ) -> dict:
        """Get projects activity analytics."""
        return await self._project_handler.get_projects_activity(
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
        )

    async def get_projects_unique_daily(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
    ) -> dict:
        """Get unique projects per day analytics."""
        return await self._project_handler.get_projects_unique_daily(
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
        )

    # CLI endpoints
    async def get_cli_summary(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
    ) -> dict:
        """Get CLI summary metrics."""
        return await self._cli_handler.get_cli_summary(
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
        )

    async def get_cli_agents(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
    ) -> dict:
        """Get CLI agents usage analytics."""
        return await self._cli_handler.get_cli_agents(
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
        )

    async def get_cli_llms(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
    ) -> dict:
        """Get CLI LLMs usage analytics."""
        return await self._cli_handler.get_cli_llms(
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
        )

    async def get_cli_users(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
    ) -> dict:
        """Get CLI users activity analytics."""
        return await self._cli_handler.get_cli_users(
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
        )

    async def get_cli_errors(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
    ) -> dict:
        """Get CLI errors analytics."""
        return await self._cli_handler.get_cli_errors(
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
        )

    async def get_cli_repositories(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
    ) -> dict:
        """Get CLI repositories activity analytics."""
        return await self._cli_handler.get_cli_repositories(
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
        )

    async def get_cli_top_performers(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
    ) -> dict:
        """Get CLI top performers ranked by total lines added."""
        return await self._cli_handler.get_cli_top_performers(
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
        )

    async def get_cli_top_versions(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
    ) -> dict:
        """Get CLI top versions ranked by usage count."""
        return await self._cli_handler.get_cli_top_versions(
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
        )

    async def get_cli_top_proxy_endpoints(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
    ) -> dict:
        """Get CLI top proxy endpoints ranked by request count."""
        return await self._cli_handler.get_cli_top_proxy_endpoints(
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
        )

    async def get_cli_tools_usage(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
    ) -> dict:
        """Get CLI tool usage analytics."""
        return await self._cli_handler.get_cli_tools_usage(
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
        )

    # Budget endpoints
    async def get_budget_soft_limit(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
    ) -> dict:
        """Get budget soft limit warnings."""
        return await self._budget_handler.get_budget_soft_limit(
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
        )

    async def get_budget_hard_limit(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
    ) -> dict:
        """Get budget hard limit violations."""
        return await self._budget_handler.get_budget_hard_limit(
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
        )

    # Webhook endpoints
    async def get_webhooks_invocation(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
    ) -> dict:
        """Get webhooks invocation analytics."""
        return await self._webhook_handler.get_webhooks_invocation(
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
        )

    # MCP endpoints
    async def get_mcp_servers(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
    ) -> dict:
        """Get MCP servers usage analytics."""
        return await self._mcp_handler.get_mcp_servers(
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
        )

    async def get_mcp_servers_by_users(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
    ) -> dict:
        """Get MCP servers usage by users analytics."""
        return await self._mcp_handler.get_mcp_servers_by_users(
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
        )

    # LLM endpoints
    async def get_llms_usage(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
    ) -> dict:
        """Get LLMs usage analytics."""
        return await self._llm_handler.get_llms_usage(
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
        )

    # Embeddings endpoints
    async def get_embeddings_usage(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
    ) -> dict:
        """Get embedding model usage analytics."""
        return await self._embeddings_handler.get_embeddings_usage(
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
        )

    # Adoption endpoints
    async def get_ai_adoption_overview(
        self,
        projects: list[str] | None = None,
        config: AIAdoptionConfig | None = None,
    ) -> dict:
        """Get AI adoption overview metrics for dashboard widgets.

        Args:
            projects: Filter by specific projects (admin only for cross-project filtering)
            config: Optional custom configuration (uses default if None)

        Returns aggregate counts: total projects, users, assistants, workflows, datasources.
        """
        return await self._adoption_handler.get_ai_adoption_overview(projects=projects, config=config)

    async def get_ai_adoption_maturity(
        self,
        projects: list[str] | None = None,
        config: AIAdoptionConfig | None = None,
    ) -> dict:
        """Get AI adoption maturity single aggregated result for dashboard cards.

        Args:
            projects: Filter by specific projects (admin only)
            config: Optional custom configuration

        Returns:
            SummariesResponse with 6 aggregated metrics: adoption_index, maturity_level, d1-d4 scores
        """
        return await self._adoption_handler.get_ai_adoption_maturity(projects=projects, config=config)

    async def get_ai_adoption_user_engagement(
        self,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
        config: AIAdoptionConfig | None = None,
    ) -> dict:
        """Get AI Adoption Dimension 1 (Daily Active Users) project-level metrics.

        Args:
            projects: Optional project filter (admin only)
            page: Page number (0-indexed)
            per_page: Results per page
            config: Optional custom configuration

        Returns:
            TabularResponse with Dimension 1 columns
        """
        return await self._adoption_handler.get_ai_adoption_user_engagement(
            projects=projects,
            page=page,
            per_page=per_page,
            config=config,
        )

    async def get_user_engagement_users(
        self,
        project: str,
        page: int = 0,
        per_page: int = 20,
        user_type: str | None = None,
        activity_level: str | None = None,
        multi_assistant_only: bool | None = None,
        sort_by: str = 'engagement_score',
        sort_order: str = 'desc',
        config: AIAdoptionConfig | None = None,
    ) -> dict:
        """Get user-level drill-down for User Engagement dimension.

        Args:
            project: Single project identifier (required)
            page: Page number (zero-indexed)
            per_page: Items per page (1-100)
            user_type: Filter by user classification
            activity_level: Filter by activity recency
            multi_assistant_only: Filter for multi-assistant users
            sort_by: Sort column
            sort_order: Sort direction
            config: Optional configuration override

        Returns:
            Dict with TabularResponse structure (data, metadata, pagination)
        """
        return await self._adoption_handler.get_user_engagement_users(
            project=project,
            page=page,
            per_page=per_page,
            user_type=user_type,
            activity_level=activity_level,
            multi_assistant_only=multi_assistant_only,
            sort_by=sort_by,
            sort_order=sort_order,
            config=config,
        )

    async def get_assistant_reusability_detail(
        self,
        project: str,
        page: int = 0,
        per_page: int = 20,
        status: str | None = None,
        adoption: str | None = None,
        sort_by: str = 'total_usage',
        sort_order: str = 'desc',
        config: AIAdoptionConfig | None = None,
    ) -> dict:
        """Get assistant-level drill-down for Asset Reusability dimension.

        Args:
            project: Single project identifier (required)
            page: Page number (zero-indexed)
            per_page: Items per page (1-100)
            status: Filter by active/inactive status
            adoption: Filter by team-adopted/single-user
            sort_by: Sort column
            sort_order: Sort direction
            config: Optional configuration override

        Returns:
            Dict with TabularResponse structure (data, metadata, pagination)
        """
        return await self._adoption_handler.get_assistant_reusability_detail(
            project=project,
            page=page,
            per_page=per_page,
            status=status,
            adoption=adoption,
            sort_by=sort_by,
            sort_order=sort_order,
            config=config,
        )

    async def get_workflow_reusability_detail(
        self,
        project: str,
        page: int = 0,
        per_page: int = 20,
        status: str | None = None,
        reuse: str | None = None,
        sort_by: str = 'execution_count',
        sort_order: str = 'desc',
        config: AIAdoptionConfig | None = None,
    ) -> dict:
        """Get workflow-level drill-down for Asset Reusability dimension.

        Args:
            project: Single project identifier (required)
            page: Page number (zero-indexed)
            per_page: Items per page (1-100)
            status: Filter by active/inactive status
            reuse: Filter by multi-user/single-user
            sort_by: Sort column
            sort_order: Sort direction
            config: Optional configuration override

        Returns:
            Dict with TabularResponse structure (data, metadata, pagination)
        """
        return await self._adoption_handler.get_workflow_reusability_detail(
            project=project,
            page=page,
            per_page=per_page,
            status=status,
            reuse=reuse,
            sort_by=sort_by,
            sort_order=sort_order,
            config=config,
        )

    async def get_datasource_reusability_detail(
        self,
        project: str,
        page: int = 0,
        per_page: int = 20,
        status: str | None = None,
        shared: str | None = None,
        type: str | None = None,
        sort_by: str = 'assistant_count',
        sort_order: str = 'desc',
        config: AIAdoptionConfig | None = None,
    ) -> dict:
        """Get datasource-level drill-down for Asset Reusability dimension.

        Args:
            project: Single project identifier (required)
            page: Page number (zero-indexed)
            per_page: Items per page (1-100)
            status: Filter by active/inactive status
            shared: Filter by shared/single
            type: Filter by datasource type
            sort_by: Sort column
            sort_order: Sort direction
            config: Optional configuration override

        Returns:
            Dict with TabularResponse structure (data, metadata, pagination)
        """
        return await self._adoption_handler.get_datasource_reusability_detail(
            project=project,
            page=page,
            per_page=per_page,
            status=status,
            shared=shared,
            type=type,
            sort_by=sort_by,
            sort_order=sort_order,
            config=config,
        )

    async def get_ai_adoption_asset_reusability(
        self,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
        config: AIAdoptionConfig | None = None,
    ) -> dict:
        """Get AI Adoption Dimension 2 (Reusability) project-level metrics.

        Args:
            projects: Optional project filter (admin only)
            page: Page number (0-indexed)
            per_page: Results per page
            config: Optional custom configuration

        Returns:
            TabularResponse with Dimension 2 columns
        """
        return await self._adoption_handler.get_ai_adoption_asset_reusability(
            projects=projects,
            page=page,
            per_page=per_page,
            config=config,
        )

    async def get_ai_adoption_expertise_distribution(
        self,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
        config: AIAdoptionConfig | None = None,
    ) -> dict:
        """Get AI Adoption Dimension 3 (AI Champions) project-level metrics.

        Args:
            projects: Optional project filter (admin only)
            page: Page number (0-indexed)
            per_page: Results per page
            config: Optional custom configuration

        Returns:
            TabularResponse with Dimension 3 columns
        """
        return await self._adoption_handler.get_ai_adoption_expertise_distribution(
            projects=projects,
            page=page,
            per_page=per_page,
            config=config,
        )

    async def get_ai_adoption_feature_adoption(
        self,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
        config: AIAdoptionConfig | None = None,
    ) -> dict:
        """Get AI Adoption Dimension 4 (AI Capabilities) project-level metrics.

        Args:
            projects: Optional project filter (admin only)
            page: Page number (0-indexed)
            per_page: Results per page
            config: Optional custom configuration

        Returns:
            TabularResponse with Dimension 4 columns
        """
        return await self._adoption_handler.get_ai_adoption_feature_adoption(
            projects=projects,
            page=page,
            per_page=per_page,
            config=config,
        )

    async def get_ai_adoption_config(self) -> dict:
        """Get AI Adoption Framework configuration parameters.

        Returns:
            Dict with framework configuration
        """
        return await self._adoption_handler.get_ai_adoption_config()

    # Engagement endpoint: weekly histogram (ignores time filter)

    async def get_weekly_spending(
        self,
        users: list[str] | None = None,
        projects: list[str] | None = None,
    ) -> dict:
        """Get weekly spending histogram in 3h intervals, broken down by source — ignores dashboard time filter."""
        return await self._engagement_handler.get_weekly_spending(
            users=users,
            projects=projects,
        )

    # Money-spent drill-down endpoints

    async def get_agents_money_spent(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
    ) -> dict:
        """Get per-assistant money-spent drill-down table."""
        return await self._assistant_handler.get_agents_money_spent(
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
        )

    async def get_workflows_money_spent(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
    ) -> dict:
        """Get per-workflow money-spent drill-down table."""
        return await self._workflow_handler.get_workflows_money_spent(
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
        )
