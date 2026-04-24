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

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from codemie.service.budget.provider import BudgetRuntimeContext, BudgetRuntimeProviderResult

from cachetools import TTLCache
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from codemie.configs import config
from codemie.repository.project_budget_repository import (
    project_budget_assignment_repository,
)
from codemie.service.budget.budget_enums import BudgetCategory, BudgetScope

# Resolution cache: (project_name, budget_category_value, user_id) → ResolvedBudgetContext | None
# None means no project budget found for this triple → caller uses global scope.
# TTL=60s matches the staleness tolerance for admin budget changes.
_resolution_cache: TTLCache = TTLCache(  # type: tuple[str,str,str] → ResolvedBudgetContext | None
    maxsize=config.BUDGET_RESOLUTION_CACHE_MAX_SIZE,
    ttl=config.BUDGET_RESOLUTION_CACHE_TTL,
)


def clear_budget_resolution_cache() -> None:
    """Clear the resolution cache. Used in tests and admin operations."""
    _resolution_cache.clear()


class ResolvedBudgetContext(BaseModel):
    """Provider-neutral budget context selected for a runtime request."""

    scope: BudgetScope
    project_name: str | None
    budget_category: BudgetCategory
    budget_id: str | None
    effective_budget_id: str | None = None
    shared_budget_id: str | None = None
    override_budget_id: str | None = None
    member_allocation_id: str | None = None
    provider_metadata: dict[str, Any] = Field(default_factory=dict)
    member_provider_metadata: dict[str, Any] = Field(default_factory=dict)


class BudgetResolutionService:
    """Resolve Codemie-owned budget scope before provider runtime handling."""

    async def resolve(
        self,
        session: AsyncSession,
        *,
        user_id: str,
        project_name: str | None,
        budget_category: BudgetCategory,
    ) -> ResolvedBudgetContext:
        if not project_name:
            return self._global_context(budget_category)

        cache_key = (project_name, budget_category.value, user_id)
        if cache_key in _resolution_cache:
            cached = _resolution_cache[cache_key]
            return cached if cached is not None else self._global_context(budget_category)

        ctx = await project_budget_assignment_repository.get_project_budget_context(
            session,
            project_name=project_name,
            budget_category=budget_category.value,
            user_id=user_id,
        )
        if ctx is None:
            _resolution_cache[cache_key] = None
            return self._global_context(budget_category)

        resolved = ResolvedBudgetContext(
            scope=BudgetScope.PROJECT,
            project_name=project_name,
            budget_category=budget_category,
            budget_id=ctx.budget_id,
            effective_budget_id=ctx.effective_budget_id,
            shared_budget_id=ctx.shared_budget_id,
            override_budget_id=ctx.override_budget_id,
            member_allocation_id=ctx.allocation_id,
            provider_metadata=ctx.budget_provider_metadata,
            member_provider_metadata=ctx.member_provider_metadata,
        )
        _resolution_cache[cache_key] = resolved
        return resolved

    def resolve_sync(
        self,
        *,
        user_id: str,
        project_name: str | None,
        budget_category: BudgetCategory,
    ) -> ResolvedBudgetContext:
        """Synchronous version of resolve() for sync contexts (e.g. LLM factory).

        Checks the in-process TTL cache first — cache hits return immediately
        without any DB access. Only on cache miss does it open a synchronous
        SQLModel Session. Always call from a thread pool context when running
        inside an async event loop (e.g. via asyncio.to_thread) to avoid
        blocking the event loop on cache-miss DB access.
        """
        if not project_name:
            return self._global_context(budget_category)

        cache_key = (project_name, budget_category.value, user_id)
        if cache_key in _resolution_cache:
            cached = _resolution_cache[cache_key]
            return cached if cached is not None else self._global_context(budget_category)

        from sqlalchemy import text
        from sqlmodel import Session

        from codemie.clients.postgres import PostgresClient

        with Session(PostgresClient.get_engine()) as session:
            result = session.execute(
                text(
                    """
                    SELECT pba.budget_id,
                           pmba.id                     AS allocation_id,
                           pmba.effective_budget_id    AS effective_budget_id,
                           pmba.shared_budget_id       AS shared_budget_id,
                           pmba.override_budget_id     AS override_budget_id,
                           b.provider_metadata         AS budget_meta,
                           pmba.pmba_provider_metadata AS member_meta
                    FROM   project_budget_assignments pba
                    JOIN   project_member_budget_assignments pmba
                             ON  pmba.project_name    = pba.project_name
                             AND pmba.budget_category = pba.budget_category
                             AND pmba.user_id         = :user_id
                             AND pmba.pmba_deleted_at IS NULL
                    JOIN   budgets b ON b.budget_id = pba.budget_id
                    WHERE  pba.project_name    = :project_name
                      AND  pba.budget_category = :budget_category
                      AND  pba.deleted_at IS NULL
                    LIMIT 1
                    """
                ),
                {"project_name": project_name, "budget_category": budget_category.value, "user_id": user_id},
            )
            row = result.mappings().first()

        if row is None:
            _resolution_cache[cache_key] = None
            return self._global_context(budget_category)

        resolved = ResolvedBudgetContext(
            scope=BudgetScope.PROJECT,
            project_name=project_name,
            budget_category=budget_category,
            budget_id=row["budget_id"],
            effective_budget_id=row.get("effective_budget_id"),
            shared_budget_id=row.get("shared_budget_id"),
            override_budget_id=row.get("override_budget_id"),
            member_allocation_id=row["allocation_id"],
            provider_metadata=row["budget_meta"] or {},
            member_provider_metadata=row["member_meta"] or {},
        )
        _resolution_cache[cache_key] = resolved
        return resolved

    @staticmethod
    def build_runtime_context(
        resolved: ResolvedBudgetContext,
        *,
        user_id: str,
        user_email: str | None,
        model: str | None = None,
    ) -> "BudgetRuntimeContext | None":
        """Build a BudgetRuntimeContext when scope is PROJECT; return None otherwise.

        Centralises the scope check and context construction shared between the
        async proxy path (proxy_router) and the sync direct-LLM path (llm_factory).
        """
        if resolved.scope != BudgetScope.PROJECT:
            return None

        from codemie.service.budget.provider import BudgetRuntimeContext

        return BudgetRuntimeContext(
            scope=resolved.scope,
            project_name=resolved.project_name,
            budget_category=resolved.budget_category,
            budget_id=resolved.budget_id,
            user_id=user_id,
            user_email=user_email,
            model=model,
            provider_metadata=resolved.provider_metadata,
            member_provider_metadata=resolved.member_provider_metadata,
        )

    async def dispatch_runtime(
        self,
        resolved: ResolvedBudgetContext,
        *,
        user_id: str,
        user_email: str | None,
        model: str | None,
    ) -> "BudgetRuntimeProviderResult | None":
        """Build context and dispatch to the active provider (async path)."""
        context = self.build_runtime_context(resolved, user_id=user_id, user_email=user_email, model=model)
        if context is None:
            return None
        from codemie.service.budget.provider_registry import get_active_provider

        return await get_active_provider().resolve_runtime(context=context)

    def dispatch_runtime_sync(
        self,
        resolved: ResolvedBudgetContext,
        *,
        user_id: str,
        user_email: str | None,
        model: str | None,
    ) -> "BudgetRuntimeProviderResult | None":
        """Build context and dispatch to the active provider (sync path)."""
        context = self.build_runtime_context(resolved, user_id=user_id, user_email=user_email, model=model)
        if context is None:
            return None
        from codemie.service.budget.provider_registry import get_active_provider

        return get_active_provider().resolve_runtime_sync(context=context)

    @staticmethod
    def _global_context(budget_category: BudgetCategory) -> ResolvedBudgetContext:
        return ResolvedBudgetContext(
            scope=BudgetScope.GLOBAL,
            project_name=None,
            budget_category=budget_category,
            budget_id=None,
        )


budget_resolution_service = BudgetResolutionService()
