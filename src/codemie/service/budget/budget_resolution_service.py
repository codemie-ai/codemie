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

from codemie.configs import config, logger
from codemie.repository.project_budget_repository import (
    PROJECT_BUDGET_FUNDED_SQL,
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


_PROBED_CATEGORIES = (BudgetCategory.PLATFORM, BudgetCategory.CLI, BudgetCategory.PREMIUM_MODELS)


_CACHE_MISS = object()


def _cached_project_budget_scopes(project_name: str, user_id: str) -> set[BudgetCategory] | None:
    # One read per key: a TTL entry can expire between a membership check and the read (CR-009).
    values = {
        category: _resolution_cache.get((project_name, category.value, user_id), _CACHE_MISS)
        for category in _PROBED_CATEGORIES
    }
    if any(value is _CACHE_MISS for value in values.values()):
        return None
    return {category for category, value in values.items() if value is not None}


def _load_project_budget_rows_sync(
    project_name: str, user_id: str, categories: list[str] | None = None
) -> dict[str, dict[str, Any]]:
    """Active project budget + this user's allocation, per category (the 'funded' definition, EPMCDME-16625 D6)."""
    from sqlalchemy import text
    from sqlmodel import Session

    from codemie.clients.postgres import PostgresClient

    with Session(PostgresClient.get_engine()) as session:
        result = session.execute(
            text(PROJECT_BUDGET_FUNDED_SQL),
            {
                "project_name": project_name,
                "user_id": user_id,
                "categories": categories or [category.value for category in _PROBED_CATEGORIES],
            },
        )
        return {row["budget_category"]: dict(row) for row in result.mappings().all()}


async def _load_project_budget_rows(project_name: str, user_id: str) -> dict[str, dict[str, Any]]:
    """Async twin of :func:`_load_project_budget_rows_sync` returning the same normalized rows."""
    from codemie.clients.postgres import get_async_session

    async with get_async_session() as session:
        rows = await project_budget_assignment_repository.get_project_budget_categories_batch(
            session=session,
            project_name=project_name,
            user_id=user_id,
            categories=[category.value for category in _PROBED_CATEGORIES],
        )

    normalized: dict[str, dict[str, Any]] = {}
    for category in _PROBED_CATEGORIES:
        row = rows.get(category) or rows.get(category.value)
        if row is None:
            continue
        if isinstance(row, ResolvedBudgetContext):
            normalized[category.value] = {
                "budget_id": row.budget_id,
                "allocation_id": row.member_allocation_id,
                "effective_budget_id": row.effective_budget_id,
                "shared_budget_id": row.shared_budget_id,
                "override_budget_id": row.override_budget_id,
                "budget_meta": row.provider_metadata,
                "member_meta": row.member_provider_metadata,
            }
            continue
        normalized[category.value] = {
            "budget_id": row.budget_id,
            "allocation_id": row.allocation_id,
            "effective_budget_id": row.effective_budget_id,
            "shared_budget_id": row.shared_budget_id,
            "override_budget_id": row.override_budget_id,
            "budget_meta": row.budget_provider_metadata,
            "member_meta": row.member_provider_metadata,
        }
    return normalized


def _store_project_budget_scopes(
    project_name: str, user_id: str, rows: dict[str, dict[str, Any]]
) -> set[BudgetCategory]:
    scopes: set[BudgetCategory] = set()
    for category in _PROBED_CATEGORIES:
        cache_key = (project_name, category.value, user_id)
        row = rows.get(category.value)
        if row is None:
            _resolution_cache[cache_key] = None
            continue
        scopes.add(category)
        _resolution_cache[cache_key] = ResolvedBudgetContext(
            scope=BudgetScope.PROJECT,
            project_name=project_name,
            budget_category=category,
            budget_id=row["budget_id"],
            effective_budget_id=row.get("effective_budget_id"),
            shared_budget_id=row.get("shared_budget_id"),
            override_budget_id=row.get("override_budget_id"),
            member_allocation_id=row["allocation_id"],
            provider_metadata=row.get("budget_meta") or {},
            member_provider_metadata=row.get("member_meta") or {},
        )
    logger.info(
        f"budget_event=project_budget_availability_probed component=budget_resolution "
        f"user_id={user_id!r} project_name={project_name!r} project_scopes={sorted(s.value for s in scopes)!r}"
    )
    return scopes


def probe_project_budget_scopes_sync(project_name: str | None, user_id: str) -> set[BudgetCategory]:
    """Categories for which the project is funded for this user (active budget + allocation); cached 60 s.

    Single source of the 'funded' decision shared by the web and CLI attribution paths (EPMCDME-16625).
    """
    if not project_name:
        return set()
    cached = _cached_project_budget_scopes(project_name, user_id)
    if cached is not None:
        return cached
    return _store_project_budget_scopes(project_name, user_id, _load_project_budget_rows_sync(project_name, user_id))


async def probe_project_budget_scopes(project_name: str | None, user_id: str) -> set[BudgetCategory]:
    """Async twin of :func:`probe_project_budget_scopes_sync` sharing the same cache."""
    if not project_name:
        return set()
    cached = _cached_project_budget_scopes(project_name, user_id)
    if cached is not None:
        return cached
    return _store_project_budget_scopes(project_name, user_id, await _load_project_budget_rows(project_name, user_id))


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
            logger.debug(
                f"budget_event=budget_resolution_global_fallback component=budget_resolution_service "
                f"user_id={user_id!r} project_name={project_name!r} "
                f"budget_category={budget_category.value!r} reason=missing_project_name"
            )
            return self._global_context(budget_category)

        cache_key = (project_name, budget_category.value, user_id)
        if cache_key in _resolution_cache:
            cached = _resolution_cache[cache_key]
            logger.debug(
                f"budget_event=budget_resolution_cache_hit component=budget_resolution_service "
                f"user_id={user_id!r} project_name={project_name!r} "
                f"budget_category={budget_category.value!r} "
                f"scope={(cached.scope.value if cached is not None else 'global')!r} "
                f"budget_id={(cached.budget_id if cached is not None else None)!r} negative={cached is None}"
            )
            return cached if cached is not None else self._global_context(budget_category)

        logger.debug(
            f"budget_event=budget_resolution_cache_miss component=budget_resolution_service "
            f"user_id={user_id!r} project_name={project_name!r} budget_category={budget_category.value!r}"
        )
        ctx = await project_budget_assignment_repository.get_project_budget_context(
            session,
            project_name=project_name,
            budget_category=budget_category.value,
            user_id=user_id,
        )
        if ctx is None:
            _resolution_cache[cache_key] = None
            logger.info(
                f"budget_event=budget_resolution_global_fallback component=budget_resolution_service "
                f"user_id={user_id!r} project_name={project_name!r} "
                f"budget_category={budget_category.value!r} reason=project_budget_not_found"
            )
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
        logger.info(
            f"budget_event=project_budget_resolved component=budget_resolution_service "
            f"user_id={user_id!r} project_name={project_name!r} "
            f"budget_category={budget_category.value!r} budget_id={resolved.budget_id!r} "
            f"effective_budget_id={resolved.effective_budget_id!r} shared_budget_id={resolved.shared_budget_id!r} "
            f"override_budget_id={resolved.override_budget_id!r} allocation_id={resolved.member_allocation_id!r} "
            f"scope={resolved.scope.value!r} cache_hit=false"
        )
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
            logger.debug(
                f"budget_event=budget_resolution_global_fallback component=budget_resolution_service path=sync "
                f"user_id={user_id!r} project_name={project_name!r} "
                f"budget_category={budget_category.value!r} reason=missing_project_name"
            )
            return self._global_context(budget_category)

        cache_key = (project_name, budget_category.value, user_id)
        cached = _resolution_cache.get(cache_key, _CACHE_MISS)
        if cached is not _CACHE_MISS:
            logger.debug(
                f"budget_event=budget_resolution_cache_hit component=budget_resolution_service path=sync "
                f"user_id={user_id!r} project_name={project_name!r} "
                f"budget_category={budget_category.value!r} "
                f"scope={(cached.scope.value if cached is not None else 'global')!r} "
                f"budget_id={(cached.budget_id if cached is not None else None)!r} negative={cached is None}"
            )
            return cached if cached is not None else self._global_context(budget_category)

        logger.debug(
            f"budget_event=budget_resolution_cache_miss component=budget_resolution_service path=sync "
            f"user_id={user_id!r} project_name={project_name!r} budget_category={budget_category.value!r}"
        )
        row = _load_project_budget_rows_sync(project_name, user_id, categories=[budget_category.value]).get(
            budget_category.value
        )

        if row is None:
            _resolution_cache[cache_key] = None
            logger.info(
                f"budget_event=budget_resolution_global_fallback component=budget_resolution_service path=sync "
                f"user_id={user_id!r} project_name={project_name!r} "
                f"budget_category={budget_category.value!r} reason=project_budget_not_found"
            )
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
            provider_metadata=row.get("budget_meta") or {},
            member_provider_metadata=row.get("member_meta") or {},
        )
        _resolution_cache[cache_key] = resolved
        logger.info(
            f"budget_event=project_budget_resolved component=budget_resolution_service path=sync "
            f"user_id={user_id!r} project_name={project_name!r} "
            f"budget_category={budget_category.value!r} budget_id={resolved.budget_id!r} "
            f"effective_budget_id={resolved.effective_budget_id!r} shared_budget_id={resolved.shared_budget_id!r} "
            f"override_budget_id={resolved.override_budget_id!r} allocation_id={resolved.member_allocation_id!r} "
            f"scope={resolved.scope.value!r} cache_hit=false"
        )
        return resolved

    @staticmethod
    def build_runtime_context(
        resolved: ResolvedBudgetContext,
        *,
        user_id: str,
        username: str | None,
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
            username=username,
            model=model,
            provider_metadata=resolved.provider_metadata,
            member_provider_metadata=resolved.member_provider_metadata,
        )

    async def dispatch_runtime(
        self,
        resolved: ResolvedBudgetContext,
        *,
        user_id: str,
        username: str | None,
        model: str | None,
    ) -> "BudgetRuntimeProviderResult | None":
        """Build context and dispatch to the active provider (async path)."""
        context = self.build_runtime_context(resolved, user_id=user_id, username=username, model=model)
        if context is None:
            logger.debug(
                f"budget_event=runtime_provider_dispatch_skipped component=budget_resolution_service "
                f"user_id={user_id!r} username={username!r} project_name={resolved.project_name!r} "
                f"budget_category={resolved.budget_category.value!r} scope={resolved.scope.value!r} "
                f"reason=global_scope"
            )
            return None
        from codemie.service.budget.provider_registry import get_active_provider

        provider = get_active_provider()
        logger.debug(
            f"budget_event=runtime_provider_dispatch_started component=budget_resolution_service "
            f"user_id={user_id!r} username={username!r} project_name={resolved.project_name!r} "
            f"budget_id={resolved.budget_id!r} budget_category={resolved.budget_category.value!r} "
            f"model={model!r} provider={provider.provider_name!r}"
        )
        result = await provider.resolve_runtime(context=context)
        logger.debug(
            f"budget_event=runtime_provider_dispatch_completed component=budget_resolution_service "
            f"user_id={user_id!r} username={username!r} project_name={resolved.project_name!r} "
            f"budget_id={resolved.budget_id!r} budget_category={resolved.budget_category.value!r} "
            f"provider={result.provider!r} api_key_present={result.api_key is not None} "
            f"base_url_present={result.base_url is not None} headers_applied={bool(result.headers)}"
        )
        return result

    def dispatch_runtime_sync(
        self,
        resolved: ResolvedBudgetContext,
        *,
        user_id: str,
        username: str | None,
        model: str | None,
    ) -> "BudgetRuntimeProviderResult | None":
        """Build context and dispatch to the active provider (sync path)."""
        context = self.build_runtime_context(resolved, user_id=user_id, username=username, model=model)
        if context is None:
            logger.debug(
                f"budget_event=runtime_provider_dispatch_skipped component=budget_resolution_service path=sync "
                f"user_id={user_id!r} username={username!r} project_name={resolved.project_name!r} "
                f"budget_category={resolved.budget_category.value!r} scope={resolved.scope.value!r} "
                f"reason=global_scope"
            )
            return None
        from codemie.service.budget.provider_registry import get_active_provider

        provider = get_active_provider()
        logger.debug(
            f"budget_event=runtime_provider_dispatch_started component=budget_resolution_service path=sync "
            f"user_id={user_id!r} username={username!r} project_name={resolved.project_name!r} "
            f"budget_id={resolved.budget_id!r} budget_category={resolved.budget_category.value!r} "
            f"model={model!r} provider={provider.provider_name!r}"
        )
        result = provider.resolve_runtime_sync(context=context)
        logger.debug(
            f"budget_event=runtime_provider_dispatch_completed component=budget_resolution_service path=sync "
            f"user_id={user_id!r} username={username!r} project_name={resolved.project_name!r} "
            f"budget_id={resolved.budget_id!r} budget_category={resolved.budget_category.value!r} "
            f"provider={result.provider!r} api_key_present={result.api_key is not None} "
            f"base_url_present={result.base_url is not None} headers_applied={bool(result.headers)}"
        )
        return result

    @staticmethod
    def _global_context(budget_category: BudgetCategory) -> ResolvedBudgetContext:
        return ResolvedBudgetContext(
            scope=BudgetScope.GLOBAL,
            project_name=None,
            budget_category=budget_category,
            budget_id=None,
        )


budget_resolution_service = BudgetResolutionService()
