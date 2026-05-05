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

import asyncio
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional
from uuid import uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from cachetools import TTLCache

from codemie.configs import config, logger
from codemie.configs.budget_config import budget_config
from codemie.configs.config import PredefinedBudgetConfig
from codemie.core.exceptions import ExtendedHTTPException, ValidationException
from codemie.repository.budget_repository import budget_repository
from codemie.service.budget.budget_enums import AllocationMode, BudgetCategory, BudgetType
from codemie.service.budget.budget_models import Budget
from codemie.service.budget.provider_registry import get_active_provider

if TYPE_CHECKING:
    from codemie.service.budget.provider import BudgetEnforcementProvider, BudgetProviderState, GlobalBudgetState
    from codemie.service.budget.provider import PersonalBudgetEntry
    from codemie.rest_api.routers.budget_router import (
        BudgetAssignmentBackfillResult,
        BudgetCreateRequest,
        BudgetSyncResult,
        BudgetUpdateRequest,
    )

_DURATION_RE = re.compile(r"^\d+[dhm]$")

_CANONICAL_BUDGET_REF_RE = re.compile(r"^codemie:project:.+:category:(?P<category>[^:]+)$")

# Budget assignment cache: (user_id, category_value) → budget_id | None
# Eliminates per-request DB lookups for user→category→budget_id mappings.
# None is cached explicitly (negative cache) to avoid DB hits for unassigned users.
_budget_assignment_cache: TTLCache = TTLCache(  # type: tuple[str,str] → str | None
    maxsize=config.BUDGET_ASSIGNMENT_CACHE_MAX_SIZE,
    ttl=config.BUDGET_ASSIGNMENT_CACHE_TTL,
)


def clear_budget_assignment_cache() -> None:
    """Clear the budget assignment cache. Used in tests and admin operations."""
    _budget_assignment_cache.clear()


@dataclass
class ProjectBudgetBackfillResult:
    """Result of the project budget backfill migration from settings."""

    migrated: int
    migrated_members: int
    skipped_existing: int
    skipped_not_found: int
    failed: int
    total_settings: int


class BudgetService:
    """Service for budget CRUD and provider synchronisation."""

    @staticmethod
    def _provider_metadata(provider_budget_ref: str, sync_status: str = "ok") -> dict:
        """Build provider metadata for global budget rows."""
        from codemie.service.budget.provider_registry import get_active_provider

        return {
            "provider": get_active_provider().provider_name,
            "provider_budget_ref": provider_budget_ref,
            "sync_status": sync_status,
        }

    @staticmethod
    def _provider_ref_for_budget(budget: Budget) -> str:
        """Return provider ref with legacy fallback to Budget.budget_id."""
        metadata = budget.provider_metadata or {}
        provider_ref = metadata.get("provider_budget_ref")
        return provider_ref if provider_ref else budget.budget_id

    @staticmethod
    def _predefined_budget_needs_update(
        configured: PredefinedBudgetConfig,
        provider_state: "GlobalBudgetState",
    ) -> bool:
        return any(
            (
                provider_state.soft_budget != configured.soft_budget,
                provider_state.max_budget != configured.max_budget,
                provider_state.budget_duration != configured.budget_duration,
            )
        )

    @staticmethod
    def _predefined_budget_duration_changed(
        configured: PredefinedBudgetConfig,
        provider_state: "GlobalBudgetState",
    ) -> bool:
        return provider_state.budget_duration != configured.budget_duration

    # ==================== Validation ====================

    @staticmethod
    def _validate_constraints(
        soft_budget: float,
        max_budget: float,
        budget_duration: str,
        budget_category: str,
    ) -> None:
        if max_budget <= 0:
            raise ValidationException("max_budget must be > 0")
        if soft_budget < 0:
            raise ValidationException("soft_budget must be >= 0")
        if soft_budget > max_budget:
            raise ValidationException("soft_budget must be <= max_budget")
        if not _DURATION_RE.match(budget_duration):
            raise ValidationException(f"budget_duration must match r'^\\d+[dhm]$', got {budget_duration!r}")
        valid_categories = {c.value for c in BudgetCategory}
        if budget_category not in valid_categories:
            raise ValidationException(f"budget_category must be one of {sorted(valid_categories)}")

    @staticmethod
    def _validate_budget_matches_category(budget: Budget, category: BudgetCategory) -> None:
        """Reject assignments that attach a budget to a mismatched category."""
        if budget.budget_category != category.value:
            raise ValidationException(
                f"Budget '{budget.budget_id}' has category '{budget.budget_category}', "
                f"cannot assign it to '{category.value}'"
            )

    # ==================== CRUD ====================

    async def create_budget(
        self,
        session: AsyncSession,
        data: BudgetCreateRequest,
        actor_id: str,
        actor_name: str = "",
    ) -> Budget:
        """Validate, persist in DB, sync to provider, read back budget_reset_at."""
        logger.debug(
            f"budget_event=budget_create_started component=budget_service budget_id={data.budget_id!r} "
            f"budget_category={data.budget_category.value!r} max_budget={data.max_budget!r} "
            f"soft_budget={data.soft_budget!r} budget_duration={data.budget_duration!r} "
            f"actor_id={actor_id!r} actor_name={actor_name!r}"
        )
        try:
            self._validate_constraints(
                soft_budget=data.soft_budget,
                max_budget=data.max_budget,
                budget_duration=data.budget_duration,
                budget_category=data.budget_category.value,
            )
        except ValidationException as exc:
            raise ExtendedHTTPException(code=400, message=str(exc)) from exc

        if await budget_repository.get_by_id(session, data.budget_id) is not None:
            logger.debug(
                f"budget_event=budget_create_rejected component=budget_service budget_id={data.budget_id!r} "
                f"reason=duplicate_budget_id actor_id={actor_id!r} actor_name={actor_name!r}"
            )
            raise ExtendedHTTPException(code=409, message=f"Budget '{data.budget_id}' already exists")
        if await budget_repository.get_by_name(session, data.name) is not None:
            logger.debug(
                f"budget_event=budget_create_rejected component=budget_service budget_id={data.budget_id!r} "
                f"name={data.name!r} reason=duplicate_name actor_id={actor_id!r} actor_name={actor_name!r}"
            )
            raise ExtendedHTTPException(code=409, message=f"Budget name '{data.name}' already in use")

        budget = Budget(
            budget_id=data.budget_id,
            budget_type=BudgetType.GLOBAL,
            name=data.name,
            description=data.description,
            soft_budget=data.soft_budget,
            max_budget=data.max_budget,
            budget_duration=data.budget_duration,
            budget_category=data.budget_category.value,
            provider_metadata=self._provider_metadata(data.budget_id),
            created_by=actor_id,
        )

        try:
            budget = await budget_repository.insert(session, budget)
        except IntegrityError:
            await session.rollback()
            raise ExtendedHTTPException(code=409, message=f"Budget '{data.budget_id}' already exists")

        try:
            provider = get_active_provider()
            logger.debug(
                f"budget_event=provider_global_budget_sync_started component=budget_service "
                f"provider={provider.provider_name!r} operation=create budget_id={data.budget_id!r} "
                f"budget_category={data.budget_category.value!r}"
            )
            state = await provider.ensure_global_budget(
                budget_id=data.budget_id,
                budget_category=data.budget_category,
                soft_budget=data.soft_budget,
                max_budget=data.max_budget,
                budget_duration=data.budget_duration,
            )
            logger.debug(
                f"budget_event=provider_global_budget_sync_completed component=budget_service "
                f"provider={state.provider!r} operation=create budget_id={data.budget_id!r} "
                f"provider_budget_ref={state.provider_budget_ref!r} sync_status={state.sync_status!r} "
                f"budget_reset_at={state.budget_reset_at!r}"
            )
        except Exception as exc:
            logger.error(
                f"budget_event=provider_global_budget_sync_failed component=budget_service "
                f"operation=create budget_id={data.budget_id!r} "
                f"budget_category={data.budget_category.value!r} error={exc}",
                exc_info=True,
            )
            await session.rollback()
            raise ExtendedHTTPException(code=502, message="Failed to sync budget to enforcement provider") from exc

        fields = {"provider_metadata": self._provider_metadata(state.provider_budget_ref or data.budget_id)}
        if state.budget_reset_at is not None:
            fields["budget_reset_at"] = state.budget_reset_at
        budget = await budget_repository.update(session, data.budget_id, fields)

        logger.info(
            f"budget_event=budget_create_completed component=budget_service budget_id={data.budget_id!r} "
            f"name={data.name!r} budget_category={data.budget_category.value!r} "
            f"actor_id={actor_id!r} actor_name={actor_name or actor_id!r}"
        )
        return budget

    async def list_budgets(
        self,
        session: AsyncSession,
        page: int,
        per_page: int,
        category: Optional[str] = None,
    ) -> tuple[list[Budget], int]:
        """Return paginated global Budget rows, optionally filtered by budget_category.

        Always scoped to budget_type=global so project budgets are never mixed
        into the admin budget list response (backward-compatibility contract).
        """
        return await budget_repository.list_paginated(
            session,
            page=page,
            per_page=per_page,
            category=category,
            budget_type=BudgetType.GLOBAL,
        )

    async def get_budget(self, session: AsyncSession, budget_id: str) -> Budget:
        """Return single Budget row or raise NotFoundException."""
        budget = await budget_repository.get_by_id(session, budget_id)
        if budget is None:
            raise ExtendedHTTPException(code=404, message=f"Budget '{budget_id}' not found")
        return budget

    async def update_budget(
        self,
        session: AsyncSession,
        budget_id: str,
        data: BudgetUpdateRequest,
        actor_id: str,
        actor_name: str = "",
    ) -> Budget:
        """Apply partial update to DB row, then sync enforcement-owned fields to provider."""
        logger.debug(
            f"budget_event=budget_update_started component=budget_service budget_id={budget_id!r} "
            f"provided_fields={sorted(data.model_fields_set)!r} actor_id={actor_id!r} actor_name={actor_name!r}"
        )
        if any(b.budget_id == budget_id for b in budget_config.predefined_budgets):
            logger.debug(
                f"budget_event=budget_update_rejected component=budget_service budget_id={budget_id!r} "
                f"reason=predefined_budget actor_id={actor_id!r} actor_name={actor_name!r}"
            )
            raise ExtendedHTTPException(
                code=403,
                message=f"Budget '{budget_id}' is preconfigured and cannot be modified via API",
            )

        budget = await self.get_budget(session, budget_id)
        new_soft, new_max, new_duration, new_category, provided_fields = await self._validate_budget_update_request(
            session=session,
            budget_id=budget_id,
            budget=budget,
            data=data,
        )

        update_fields = self._build_update_fields(data, new_category)
        budget = await budget_repository.update(session, budget_id, update_fields)

        provider_owned_fields = {"soft_budget", "max_budget", "budget_duration"}
        if provided_fields & provider_owned_fields:
            budget = await self._sync_updated_global_budget(
                session=session,
                budget=budget,
                budget_id=budget_id,
                soft_budget=new_soft,
                max_budget=new_max,
                budget_duration=new_duration,
            )

        logger.info(
            f"budget_event=budget_update_completed component=budget_service budget_id={budget_id!r} "
            f"updated_fields={sorted(update_fields.keys())!r} actor_id={actor_id!r} "
            f"actor_name={actor_name or actor_id!r}"
        )
        return budget

    async def _validate_budget_update_request(
        self,
        *,
        session: AsyncSession,
        budget_id: str,
        budget: Budget,
        data: BudgetUpdateRequest,
    ) -> tuple[float, float, str, str, set]:
        new_soft = data.soft_budget if data.soft_budget is not None else budget.soft_budget
        new_max = data.max_budget if data.max_budget is not None else budget.max_budget
        new_duration = data.budget_duration if data.budget_duration is not None else budget.budget_duration
        new_category = data.budget_category.value if data.budget_category is not None else budget.budget_category
        try:
            self._validate_constraints(
                soft_budget=new_soft,
                max_budget=new_max,
                budget_duration=new_duration,
                budget_category=new_category,
            )
        except ValidationException as exc:
            raise ExtendedHTTPException(code=400, message=str(exc)) from exc

        provided_fields = data.model_fields_set
        if not provided_fields:
            raise ValidationException("At least one budget field must be provided")

        await self._check_name_uniqueness(session, data, budget_id, budget.name)
        await self._check_category_change_allowed(
            session, budget_id, provided_fields, new_category, budget.budget_category
        )
        return new_soft, new_max, new_duration, new_category, provided_fields

    async def _sync_updated_global_budget(
        self,
        *,
        session: AsyncSession,
        budget: Budget,
        budget_id: str,
        soft_budget: float,
        max_budget: float,
        budget_duration: str,
    ) -> Budget:
        try:
            provider = get_active_provider()
            provider_ref = self._provider_ref_for_budget(budget)
            logger.debug(
                f"budget_event=provider_global_budget_sync_started component=budget_service "
                f"provider={provider.provider_name!r} operation=update budget_id={budget_id!r} "
                f"provider_budget_ref={provider_ref!r} max_budget={max_budget!r} "
                f"soft_budget={soft_budget!r} budget_duration={budget_duration!r}"
            )
            state = await provider.update_global_budget(
                budget_id=provider_ref,
                soft_budget=soft_budget,
                max_budget=max_budget,
                budget_duration=budget_duration,
            )
            logger.debug(
                f"budget_event=provider_global_budget_sync_completed component=budget_service "
                f"provider={state.provider!r} operation=update budget_id={budget_id!r} "
                f"provider_budget_ref={state.provider_budget_ref!r} sync_status={state.sync_status!r} "
                f"budget_reset_at={state.budget_reset_at!r}"
            )
        except Exception as exc:
            logger.error(
                f"budget_event=provider_global_budget_sync_failed component=budget_service "
                f"operation=update budget_id={budget_id!r} error={exc}",
                exc_info=True,
            )
            await session.rollback()
            raise ExtendedHTTPException(
                code=502,
                message="Failed to sync budget update to enforcement provider",
            ) from exc

        fields = {"provider_metadata": self._provider_metadata(state.provider_budget_ref or budget_id)}
        if state.budget_reset_at is not None:
            fields["budget_reset_at"] = state.budget_reset_at
        return await budget_repository.update(session, budget_id, fields)

    @staticmethod
    def _build_update_fields(data: BudgetUpdateRequest, new_category: str) -> dict:
        """Build update dict from only the fields that were provided."""
        provided = data.model_fields_set
        fields: dict = {}
        for field in ("name", "description", "soft_budget", "max_budget", "budget_duration"):
            if field in provided:
                fields[field] = getattr(data, field)
        if "budget_category" in provided:
            fields["budget_category"] = new_category
        return fields

    async def _check_name_uniqueness(
        self, session: AsyncSession, data: BudgetUpdateRequest, budget_id: str, current_name: str
    ) -> None:
        """Raise 409 if the requested name is already taken by another budget."""
        if data.name is not None and data.name != current_name:
            existing = await budget_repository.get_by_name(session, data.name)
            if existing is not None and existing.budget_id != budget_id:
                raise ExtendedHTTPException(code=409, message=f"Budget name '{data.name}' already in use")

    async def _check_category_change_allowed(
        self,
        session: AsyncSession,
        budget_id: str,
        provided_fields: set,
        new_category: str,
        current_category: str,
    ) -> None:
        """Raise 409 if category change is requested while assignments exist."""
        if "budget_category" in provided_fields and new_category != current_category:
            assignment_count = await budget_repository.count_assignments(session, budget_id)
            if assignment_count > 0:
                raise ExtendedHTTPException(
                    code=409,
                    message=f"Budget '{budget_id}' category cannot be changed while it has assignments",
                )

    @staticmethod
    def _category_for_budget_id(budget_id: str) -> str:
        """Derive budget_category from a budget_id using predefined budgets config.

        Falls back to 'platform' for unrecognised budget IDs.
        """
        for b in budget_config.predefined_budgets:
            if b.budget_id == budget_id:
                return b.budget_category
        return BudgetCategory.PLATFORM

    @staticmethod
    def _default_budget_id_for_category(category: BudgetCategory) -> str | None:
        """Return the configured default budget_id for a category, or None."""
        for b in budget_config.predefined_budgets:
            if b.budget_category == category.value:
                return b.budget_id
        return None

    async def _upsert_predefined_budget_db(self, session: AsyncSession, bc: PredefinedBudgetConfig) -> None:
        existing = await budget_repository.get_by_id(session, bc.budget_id)
        logger.debug(
            f"budget_event=predefined_budget_db_decision component=budget_service budget_id={bc.budget_id!r} "
            f"budget_category={bc.budget_category!r} action={'create' if existing is None else 'update'}"
        )
        if existing is None:
            new_budget = Budget(
                budget_id=bc.budget_id,
                name=bc.name,
                description=bc.description,
                soft_budget=bc.soft_budget,
                max_budget=bc.max_budget,
                budget_duration=bc.budget_duration,
                budget_category=bc.budget_category,
                provider_metadata=self._provider_metadata(bc.budget_id),
                created_by="system",
            )
            await budget_repository.insert(session, new_budget)
            return

        fields = {
            "name": bc.name,
            "description": bc.description,
            "soft_budget": bc.soft_budget,
            "max_budget": bc.max_budget,
            "budget_duration": bc.budget_duration,
            "budget_category": bc.budget_category,
            "provider_metadata": self._provider_metadata(self._provider_ref_for_budget(existing)),
        }
        await budget_repository.update(session, bc.budget_id, fields)

    async def _sync_predefined_budget_provider(
        self,
        provider: "BudgetEnforcementProvider",
        bc: PredefinedBudgetConfig,
        provider_state: "GlobalBudgetState | None",
    ) -> "BudgetProviderState | None":
        if provider_state is None:
            logger.debug(
                f"budget_event=predefined_budget_provider_decision component=budget_service "
                f"budget_id={bc.budget_id!r} budget_category={bc.budget_category!r} action=create"
            )
            return await provider.ensure_global_budget(
                budget_id=bc.budget_id,
                budget_category=BudgetCategory(bc.budget_category),
                max_budget=bc.max_budget,
                soft_budget=bc.soft_budget,
                budget_duration=bc.budget_duration,
            )

        if not self._predefined_budget_needs_update(bc, provider_state):
            logger.debug(
                f"budget_event=predefined_budget_provider_decision component=budget_service "
                f"budget_id={bc.budget_id!r} budget_category={bc.budget_category!r} action=skip"
            )
            return None

        duration_changed = self._predefined_budget_duration_changed(bc, provider_state)
        logger.debug(
            f"budget_event=predefined_budget_provider_decision component=budget_service "
            f"budget_id={bc.budget_id!r} budget_category={bc.budget_category!r} "
            f"action=update duration_changed={duration_changed!r}"
        )
        return await provider.update_global_budget(
            budget_id=bc.budget_id,
            max_budget=bc.max_budget,
            soft_budget=bc.soft_budget,
            budget_duration=bc.budget_duration,
            budget_reset_at=None if duration_changed else provider_state.budget_reset_at,
        )

    async def _persist_predefined_budget_provider_state(
        self,
        session: AsyncSession,
        bc: PredefinedBudgetConfig,
        state: "BudgetProviderState | None",
    ) -> None:
        if state is None:
            return
        if state.sync_status == "failed":
            logger.error(
                f"budget_event=predefined_budget_sync_failed component=budget_service "
                f"budget_id={bc.budget_id!r} budget_category={bc.budget_category!r} "
                f"provider={state.provider!r} sync_status={state.sync_status!r}"
            )
            return
        await budget_repository.update(
            session,
            bc.budget_id,
            {
                "provider_metadata": self._provider_metadata(state.provider_budget_ref or bc.budget_id),
                "budget_reset_at": state.budget_reset_at,
            },
        )

    async def ensure_predefined_budgets(self, session: AsyncSession) -> None:
        """Force-create or update all predefined budgets at startup.

        Config is the source of truth — existing budgets are overwritten to match
        config values.  Provider and DB are kept in sync for each predefined budget.
        """
        configured_ids = [bc.budget_id for bc in budget_config.predefined_budgets]
        if not configured_ids:
            logger.info(
                "budget_event=predefined_budget_initialization_skipped component=budget_service "
                "reason=no_configured_budgets configured_count=0"
            )
            return

        logger.info(
            f"budget_event=predefined_budget_initialization_started component=budget_service "
            f"configured_budget_ids={configured_ids!r} configured_count={len(configured_ids)}"
        )

        provider = get_active_provider()
        existing_states = await provider.list_global_budget_states()
        provider_budget_states = {state.budget_id: state for state in (existing_states or [])}
        logger.debug(
            f"budget_event=provider_global_budget_list_completed component=budget_service "
            f"provider={provider.provider_name!r} provider_budget_ids={sorted(provider_budget_states)!r} "
            f"provider_budget_count={len(provider_budget_states)}"
        )

        for bc in budget_config.predefined_budgets:
            await self._upsert_predefined_budget_db(session, bc)
            state = await self._sync_predefined_budget_provider(provider, bc, provider_budget_states.get(bc.budget_id))
            await self._persist_predefined_budget_provider_state(session, bc, state)
            await session.commit()
            logger.info(
                f"budget_event=predefined_budget_synced component=budget_service budget_id={bc.budget_id!r} "
                f"budget_category={bc.budget_category!r} max_budget={bc.max_budget!r} "
                f"soft_budget={bc.soft_budget!r} budget_duration={bc.budget_duration!r} "
                f"sync_status={(state.sync_status if state is not None else 'skipped')!r}"
            )

        logger.info(
            f"budget_event=predefined_budget_initialization_completed component=budget_service "
            f"configured_count={len(configured_ids)}"
        )

    async def sync_budgets_from_provider(
        self,
        session: AsyncSession,
        actor_id: str,
    ) -> BudgetSyncResult:
        """Pull all global budgets from the provider, upsert differences and delete orphans."""
        from codemie.rest_api.routers.budget_router import BudgetSyncResult

        provider = get_active_provider()
        logger.debug(
            f"budget_event=provider_budget_pull_started component=budget_service provider={provider.provider_name!r} "
            f"actor_id={actor_id!r}"
        )
        provider_states = await provider.list_global_budget_states()
        if provider_states is None:
            logger.warning(
                f"budget_event=provider_budget_pull_failed component=budget_service "
                f"provider={provider.provider_name!r} actor_id={actor_id!r} reason=provider_unreachable"
            )
            raise ExtendedHTTPException(code=502, message="Enforcement provider unreachable during sync")

        created = updated = unchanged = deleted = 0

        provider_ids: set[str] = set()
        for state in provider_states:
            provider_ids.add(state.budget_id)
            fields = {
                "soft_budget": state.soft_budget,
                "max_budget": state.max_budget,
                "budget_duration": state.budget_duration,
                "budget_reset_at": state.budget_reset_at,
                "provider_metadata": self._provider_metadata(state.budget_id),
                "name": state.budget_id,
                "description": None,
                "budget_category": self._category_for_budget_id(state.budget_id),
                "created_by": actor_id,
            }
            _budget, status = await budget_repository.upsert_from_provider(session, state.budget_id, fields)
            logger.debug(
                f"budget_event=provider_budget_upserted component=budget_service budget_id={state.budget_id!r} "
                f"budget_category={fields['budget_category']!r} status={status!r} actor_id={actor_id!r}"
            )
            if status == "created":
                created += 1
            elif status == "updated":
                updated += 1
            else:
                unchanged += 1

        # Only delete global orphans — project budgets must never be deleted here.
        db_budgets = await budget_repository.get_all_keyed_by_id(session)
        global_db_ids = {bid for bid, b in db_budgets.items() if b.budget_type == BudgetType.GLOBAL}
        for budget_id in global_db_ids - provider_ids:
            logger.info(
                f"budget_event=provider_budget_orphan_deleted component=budget_service budget_id={budget_id!r} "
                f"reason=absent_from_provider actor_id={actor_id!r}"
            )
            await budget_repository.delete(session, budget_id)
            deleted += 1

        await session.commit()

        all_budgets, _ = await budget_repository.list_paginated(session, page=0, per_page=10000)
        logger.info(
            f"budget_event=provider_budget_pull_completed component=budget_service created={created} "
            f"updated={updated} unchanged={unchanged} deleted={deleted} total_in_provider={len(provider_states)} "
            f"actor_id={actor_id!r}"
        )
        return BudgetSyncResult(
            created=created,
            updated=updated,
            unchanged=unchanged,
            deleted=deleted,
            total_in_litellm=len(provider_states),
            budgets=all_budgets,
        )

    # Keep the old name as an alias so existing callers are not broken.
    sync_budgets_from_litellm = sync_budgets_from_provider

    @staticmethod
    def _serialize_datetime(value) -> str | None:
        """Store datetime values verbatim where possible."""
        if value is None:
            return None
        if hasattr(value, "isoformat"):
            return value.isoformat()
        return str(value)

    async def _ensure_backfilled_budget(
        self,
        session: AsyncSession,
        entry: PersonalBudgetEntry,
        actor_id: str,
    ) -> bool:
        """Ensure a provider customer budget exists locally before assignment insert."""
        if entry.budget_id is None:
            return False
        if await budget_repository.get_by_id(session, entry.budget_id) is not None:
            return False

        fields = {
            "soft_budget": float(entry.soft_budget) if entry.soft_budget is not None else 0.0,
            "max_budget": float(entry.max_budget) if entry.max_budget is not None else 0.0,
            "budget_duration": entry.budget_duration or "30d",
            "budget_reset_at": self._serialize_datetime(entry.budget_reset_at),
            "provider_metadata": self._provider_metadata(entry.budget_id),
            "name": entry.budget_id,
            "description": "Imported from provider customer backfill",
            "budget_category": entry.budget_category.value,
            "created_by": actor_id,
        }
        await budget_repository.upsert_from_provider(session, entry.budget_id, fields)
        return True

    async def _process_backfill_entry(
        self,
        session: AsyncSession,
        entry: PersonalBudgetEntry,
        actor_id: str,
    ) -> tuple[str, bool]:
        """Process one provider customer entry for backfill.

        Returns (status, budget_created) where status is one of
        'missing_user', 'skipped_no_budget', 'existing', or 'imported'.
        """
        async with session.begin_nested():
            user_id = await budget_repository.get_user_id_by_identifier(session, entry.user_identifier)
            if user_id is None:
                return "missing_user", False
            if entry.budget_id is None:
                return "skipped_no_budget", False
            if await budget_repository.get_user_category_budget_id(session, user_id, entry.budget_category) is not None:
                return "existing", False
            budget_created = await self._ensure_backfilled_budget(session, entry, actor_id)
            await budget_repository.upsert_user_category_assignment(
                session, user_id, entry.budget_category, entry.budget_id, assigned_by=actor_id
            )
            return "imported", budget_created

    async def _process_backfill_page(
        self,
        session: AsyncSession,
        entries: list[PersonalBudgetEntry],
        actor_id: str,
    ) -> tuple[int, int, int, int, int]:
        """Process one page of provider customer entries.

        Returns (imported, skipped_existing, skipped_missing_user, created_budgets, failed).
        """
        imported = skipped_existing = skipped_missing_user = created_budgets = failed = 0
        for entry in entries:
            try:
                status, budget_created = await self._process_backfill_entry(session, entry, actor_id)
                if status == "missing_user":
                    skipped_missing_user += 1
                elif status in ("existing", "skipped_no_budget"):
                    skipped_existing += 1
                else:
                    imported += 1
                    created_budgets += budget_created
            except Exception as exc:
                failed += 1
                logger.warning(f"Failed to backfill budget assignment for customer {entry.user_identifier!r}: {exc}")
        return imported, skipped_existing, skipped_missing_user, created_budgets, failed

    async def backfill_user_budget_assignments(
        self,
        session: AsyncSession,
        actor_id: str = "provider-backfill",
        page_size: int = 1000,
    ) -> BudgetAssignmentBackfillResult:
        """Import existing provider customer budget assignments into Codemie DB.

        The provider remains the runtime budget enforcement source. This method only
        mirrors missing user/category assignments into user_budget_assignments and
        creates missing budget rows required by the assignment FK.
        """
        from codemie.rest_api.routers.budget_router import BudgetAssignmentBackfillResult

        provider = get_active_provider()
        all_entries = await provider.list_personal_budget_assignments()
        if all_entries is None:
            raise ExtendedHTTPException(code=502, message="Enforcement provider unavailable during assignment backfill")

        imported = skipped_existing = skipped_missing_user = failed = created_budgets = 0
        total_in_litellm = len(all_entries)

        for offset in range(0, total_in_litellm, page_size):
            page_entries = all_entries[offset : offset + page_size]
            i, se, smu, cb, f = await self._process_backfill_page(session, page_entries, actor_id)
            imported += i
            skipped_existing += se
            skipped_missing_user += smu
            created_budgets += cb
            failed += f
            await session.commit()

        logger.info(
            f"Budget assignment backfill finished: imported={imported}, "
            f"skipped_existing={skipped_existing}, skipped_missing_user={skipped_missing_user}, "
            f"created_budgets={created_budgets}, failed={failed}, total_in_provider={total_in_litellm}"
        )
        return BudgetAssignmentBackfillResult(
            imported=imported,
            skipped_existing=skipped_existing,
            skipped_missing_user=skipped_missing_user,
            created_budgets=created_budgets,
            failed=failed,
            total_in_litellm=total_in_litellm,
        )

    backfill_user_budget_assignments_from_litellm = backfill_user_budget_assignments

    # ==================== Assignments ====================

    async def track_proxy_budget_assignment_for_request(
        self,
        user_id: str,
        category: BudgetCategory,
        budget_id: str | None,
        assigned_by: str = "system",
    ) -> None:
        """Best-effort mirror of a proxy budget assignment into Codemie DB."""
        logger.debug(
            f"budget_event=budget_assignment_mirror_started component=budget_service user_id={user_id!r} "
            f"budget_category={category.value!r} budget_id={budget_id!r} assigned_by={assigned_by!r}"
        )
        if not budget_id:
            logger.debug(
                f"budget_event=budget_assignment_mirror_skipped component=budget_service user_id={user_id!r} "
                f"budget_category={category.value!r} reason=missing_budget_id"
            )
            return

        cache_key = (user_id, category.value)
        # Skip DB entirely if the cache already has this exact assignment recorded
        if cache_key in _budget_assignment_cache and _budget_assignment_cache[cache_key] == budget_id:
            logger.debug(
                f"budget_event=budget_assignment_cache_hit component=budget_service user_id={user_id!r} "
                f"budget_category={category.value!r} budget_id={budget_id!r} "
                f"cache_value={_budget_assignment_cache[cache_key]!r}"
            )
            return

        from codemie.clients.postgres import get_async_session

        try:
            async with get_async_session() as session:
                existing = await budget_repository.get_user_category_budget_id(session, user_id, category)
                if existing is not None:
                    _budget_assignment_cache[cache_key] = existing
                    logger.debug(
                        f"budget_event=budget_assignment_mirror_skipped component=budget_service "
                        f"user_id={user_id!r} budget_category={category.value!r} budget_id={budget_id!r} "
                        f"existing_budget_id={existing!r}"
                    )
                    return
                await budget_repository.upsert_user_category_assignment(
                    session,
                    user_id,
                    category,
                    budget_id,
                    assigned_by=assigned_by,
                )
                await session.commit()
            _budget_assignment_cache[cache_key] = budget_id
            logger.debug(
                f"budget_event=budget_assignment_mirrored component=budget_service user_id={user_id!r} "
                f"budget_category={category.value!r} budget_id={budget_id!r} assigned_by={assigned_by!r}"
            )
        except Exception as exc:
            logger.warning(
                f"budget_event=budget_assignment_mirror_failed component=budget_service user_id={user_id!r} "
                f"budget_category={category.value!r} budget_id={budget_id!r} assigned_by={assigned_by!r} "
                f"error={exc}"
            )

    async def get_user_category_budget_id_for_request(self, user_id: str, category: BudgetCategory) -> str | None:
        """Best-effort lookup of the currently assigned budget for a proxy request."""
        cache_key = (user_id, category.value)
        if cache_key in _budget_assignment_cache:
            logger.debug(
                f"budget_event=budget_assignment_cache_hit component=budget_service user_id={user_id!r} "
                f"budget_category={category.value!r} budget_id={_budget_assignment_cache[cache_key]!r}"
            )
            return _budget_assignment_cache[cache_key]
        logger.debug(
            f"budget_event=budget_assignment_cache_miss component=budget_service user_id={user_id!r} "
            f"budget_category={category.value!r}"
        )

        from codemie.clients.postgres import get_async_session

        try:
            async with get_async_session() as session:
                result = await budget_repository.get_user_category_budget_id(session, user_id, category)
            _budget_assignment_cache[cache_key] = result
            logger.debug(
                f"budget_event=budget_assignment_resolved component=budget_service user_id={user_id!r} "
                f"budget_category={category.value!r} budget_id={result!r}"
            )
            return result
        except Exception as exc:
            logger.warning(
                f"budget_event=budget_assignment_resolve_failed component=budget_service user_id={user_id!r} "
                f"budget_category={category.value!r} error={exc}"
            )
            return None

    async def get_all_category_budget_ids_for_request(self, user_id: str) -> dict[str, str | None]:
        """Fetch all category→budget_id mappings in one query with per-category caching.

        Returns a dict keyed by category string value (e.g. "platform", "cli",
        "premium_models"). Populates _budget_assignment_cache for every category
        so subsequent per-category lookups are also served from cache.
        """
        from codemie.clients.postgres import get_async_session

        categories = [c.value for c in BudgetCategory]
        # Fast path: all categories already in cache
        if all((user_id, cat) in _budget_assignment_cache for cat in categories):
            logger.debug(
                f"budget_event=budget_assignment_batch_cache_hit component=budget_service user_id={user_id!r} "
                f"budget_categories={categories!r}"
            )
            return {cat: _budget_assignment_cache[(user_id, cat)] for cat in categories}

        # Cache miss for at least one category — fetch all in one query
        missing_categories = [cat for cat in categories if (user_id, cat) not in _budget_assignment_cache]
        logger.debug(
            f"budget_event=budget_assignment_batch_cache_miss component=budget_service user_id={user_id!r} "
            f"missing_categories={missing_categories!r}"
        )
        try:
            async with get_async_session() as session:
                assignments = await budget_repository.get_user_category_assignments(session, user_id)
            assignment_map: dict[str, str | None] = {a.category: a.budget_id for a in assignments}
            for cat in categories:
                _budget_assignment_cache[(user_id, cat)] = assignment_map.get(cat)
            logger.debug(
                f"budget_event=budget_assignment_batch_resolved component=budget_service user_id={user_id!r} "
                f"budget_categories={categories!r} resolved_assignments={assignment_map!r}"
            )
            return {cat: assignment_map.get(cat) for cat in categories}
        except Exception as exc:
            logger.warning(
                f"budget_event=budget_assignment_batch_resolve_failed component=budget_service "
                f"user_id={user_id!r} error={exc}"
            )
            return {cat: None for cat in categories}

    def get_all_category_budget_ids_for_request_sync(self, user_id: str) -> dict[str, str | None]:
        """Sync version of batch category→budget_id lookup with shared cache reuse."""
        from sqlmodel import Session, select

        from codemie.clients.postgres import PostgresClient
        from codemie.service.budget.budget_models import UserBudgetAssignment

        categories = [c.value for c in BudgetCategory]
        if all((user_id, cat) in _budget_assignment_cache for cat in categories):
            logger.debug(
                f"budget_event=budget_assignment_batch_cache_hit component=budget_service path=sync "
                f"user_id={user_id!r} budget_categories={categories!r}"
            )
            return {cat: _budget_assignment_cache[(user_id, cat)] for cat in categories}

        missing_categories = [cat for cat in categories if (user_id, cat) not in _budget_assignment_cache]
        logger.debug(
            f"budget_event=budget_assignment_batch_cache_miss component=budget_service path=sync "
            f"user_id={user_id!r} missing_categories={missing_categories!r}"
        )
        try:
            with Session(PostgresClient.get_engine()) as session:
                rows = session.exec(
                    select(UserBudgetAssignment.category, UserBudgetAssignment.budget_id).where(
                        UserBudgetAssignment.user_id == user_id
                    )
                ).all()
            assignment_map = dict(rows)
            for cat in categories:
                _budget_assignment_cache[(user_id, cat)] = assignment_map.get(cat)
            logger.debug(
                f"budget_event=budget_assignment_batch_resolved component=budget_service path=sync "
                f"user_id={user_id!r} budget_categories={categories!r} resolved_assignments={assignment_map!r}"
            )
            return {cat: assignment_map.get(cat) for cat in categories}
        except Exception as exc:
            logger.warning(
                f"budget_event=budget_assignment_batch_resolve_failed component=budget_service path=sync "
                f"user_id={user_id!r} error={exc}"
            )
            return {cat: None for cat in categories}

    async def validate_assignment_budget_categories(
        self,
        session: AsyncSession,
        assignments: dict[BudgetCategory, str | None],
    ) -> None:
        """Validate all non-null assignment budgets exist, are global, and match their category."""
        for category, budget_id in assignments.items():
            if budget_id is None:
                continue
            budget = await budget_repository.get_by_id(session, budget_id)
            if budget is None:
                raise ExtendedHTTPException(code=404, message=f"Budget '{budget_id}' not found")
            if getattr(budget, "budget_type", BudgetType.GLOBAL) != BudgetType.GLOBAL:
                raise ExtendedHTTPException(
                    code=400,
                    message=f"Budget '{budget_id}' is a project budget and cannot be assigned to users directly",
                )
            try:
                self._validate_budget_matches_category(budget, category)
            except ValidationException as exc:
                raise ExtendedHTTPException(code=400, message=str(exc)) from exc

    async def assign_budget_to_user(
        self,
        session: AsyncSession,
        user_id: str,
        assignments: dict[BudgetCategory, str | None],
        actor_id: str,
    ) -> None:
        """Set or clear per-category budget assignments for a user and propagate to provider.

        For each (category, budget_id) pair in *assignments*:
        - budget_id is not None → validate Budget row exists; upsert a row in
          user_budget_assignments(user_id, category, budget_id); then call
          provider.assign_user_budget.
        - budget_id is None → delete the row in user_budget_assignments for this
          (user_id, category); then call provider.clear_user_budget.

        Provider propagation errors are logged as warnings and do not abort the DB write
        (fail-open).
        """
        from codemie.rest_api.models.user_management import UserDB
        from sqlmodel import select

        result = await session.execute(select(UserDB).where(UserDB.id == user_id))
        db_user = result.scalars().first()
        if db_user is None:
            raise ExtendedHTTPException(code=404, message=f"User '{user_id}' not found")

        provider = get_active_provider()

        for category, budget_id in assignments.items():
            if budget_id is not None:
                await self.validate_assignment_budget_categories(session, {category: budget_id})
                await budget_repository.upsert_user_category_assignment(
                    session, user_id, category, budget_id, assigned_by=actor_id
                )
                _budget_assignment_cache.pop((user_id, category.value), None)
                try:
                    await provider.assign_user_budget(
                        user_email=db_user.email, budget_category=category, budget_id=budget_id
                    )
                except Exception as exc:
                    logger.warning(
                        f"Failed to propagate budget assignment for user {user_id!r} "
                        f"category {category.value!r}: {exc}"
                    )
            else:
                await budget_repository.delete_user_category_assignment(session, user_id, category)
                _budget_assignment_cache.pop((user_id, category.value), None)
                try:
                    await provider.clear_user_budget(user_email=db_user.email, budget_category=category)
                except Exception as exc:
                    logger.warning(
                        f"Failed to clear budget assignment for user {user_id!r} category {category.value!r}: {exc}"
                    )

    async def bulk_set_user_budgets(
        self,
        session: AsyncSession,
        user_ids: list[str],
        assignments: dict[BudgetCategory, str | None],
        actor_id: str,
    ) -> None:
        """Apply the same budget assignment map to multiple users and propagate to provider.

        Mirrors assign_budget_to_user but for N users at once. For each
        (category, budget_id) pair:
        - budget_id is not None → validate and upsert user_budget_assignments
        - budget_id is None → delete the row for that (user_id, category)

        Provider propagation is fail-open. Raises 404 if any user_id is not found.
        """
        from codemie.rest_api.models.user_management import UserDB
        from sqlmodel import select

        non_null = {cat: bid for cat, bid in assignments.items() if bid is not None}
        if non_null:
            await self.validate_assignment_budget_categories(session, non_null)

        db_users = await self._load_bulk_budget_users(session, user_ids, select, UserDB)
        await self._persist_bulk_budget_assignments(session, user_ids, assignments, actor_id)
        await self._propagate_bulk_budget_assignments(db_users, assignments)

    async def _load_bulk_budget_users(self, session: AsyncSession, user_ids: list[str], select, user_model) -> dict:
        result = await session.execute(select(user_model).where(user_model.id.in_(user_ids)))
        db_users = {u.id: u for u in result.scalars().all()}
        missing = [uid for uid in user_ids if uid not in db_users]
        if missing:
            raise ExtendedHTTPException(code=404, message=f"Users not found: {missing!r}")
        return db_users

    async def _persist_bulk_budget_assignments(
        self,
        session: AsyncSession,
        user_ids: list[str],
        assignments: dict[BudgetCategory, str | None],
        actor_id: str,
    ) -> None:
        for user_id in user_ids:
            for category, budget_id in assignments.items():
                if budget_id is not None:
                    await budget_repository.upsert_user_category_assignment(
                        session, user_id, category, budget_id, assigned_by=actor_id
                    )
                else:
                    await budget_repository.delete_user_category_assignment(session, user_id, category)
                _budget_assignment_cache.pop((user_id, category.value), None)

    async def _propagate_bulk_budget_assignments(
        self,
        db_users: dict,
        assignments: dict[BudgetCategory, str | None],
    ) -> None:
        provider = get_active_provider()
        for user_id, db_user in db_users.items():
            for category, budget_id in assignments.items():
                try:
                    if budget_id is not None:
                        await provider.assign_user_budget(
                            user_email=db_user.email, budget_category=category, budget_id=budget_id
                        )
                    else:
                        await provider.clear_user_budget(user_email=db_user.email, budget_category=category)
                except Exception as exc:
                    logger.warning(
                        f"Failed to propagate bulk budget update for user {user_id!r} "
                        f"category {category.value!r}: {exc}"
                    )

    async def reset_user_budget_spending(
        self,
        session: AsyncSession,
        user_id: str,
        actor_id: str,
        actor_name: str = "",
        categories: list | None = None,
    ) -> None:
        """Reset budget spending for a user by recreating their provider customer records.

        For each targeted budget category, deletes the provider customer record and
        recreates it with the same budget_id, resetting the spend counter to zero.
        This unblocks a user who has reached their budget limit.

        Args:
            categories: Budget categories to reset. Pass None or an empty list to
                reset all active categories for the user.

        Provider propagation failures are logged as warnings and do not abort the
        operation (fail-open).
        """
        from codemie.rest_api.models.user_management import UserDB
        from sqlmodel import select

        result = await session.execute(select(UserDB).where(UserDB.id == user_id))
        db_user = result.scalars().first()
        if db_user is None:
            raise ExtendedHTTPException(code=404, message=f"User '{user_id}' not found")

        target_categories: list[BudgetCategory] = categories if categories else list(BudgetCategory)

        provider = get_active_provider()

        for category in target_categories:
            budget_id = await budget_repository.get_user_category_budget_id(session, user_id, category)
            if budget_id is None:
                budget_id = self._default_budget_id_for_category(category)
            if not budget_id:
                continue

            try:
                await provider.reset_user_budget_spending(
                    user_email=db_user.email, budget_category=category, budget_id=budget_id
                )
            except Exception as exc:
                logger.warning(
                    f"Failed to reset budget spending for user {user_id!r} category {category.value!r}: {exc}"
                )

        reset_scope = [c.value for c in target_categories]
        logger.info(
            f"Budget spending reset for user '{user_id}' (categories={reset_scope}) by '{actor_name or actor_id}'"
        )

    async def backfill_project_budget_assignments_from_settings(
        self,
        session: AsyncSession,
        actor_id: str = "system",
    ) -> ProjectBudgetBackfillResult:
        """Migrate existing project LiteLLM keys stored in settings into full budget entity hierarchies.

        For each Settings row with credential_type=LITE_LLM and setting_type=PROJECT that does not
        yet have a corresponding ProjectBudgetAssignment, creates:
          - Budget (type=project)
          - ProjectBudgetAssignment
          - ProjectMemberBudgetAssignment for every active project member (equal distribution)

        Budget limits are fetched from the provider (source of truth). Member allocations are
        synced to the provider on creation. The migration is idempotent and non-fatal: every
        per-setting error is logged and that setting is skipped while others continue.
        """
        from codemie.repository.project_budget_repository import (
            project_budget_assignment_repository,
            project_member_budget_assignment_repository,
        )
        from codemie.rest_api.models.settings import Settings
        from codemie.service.budget.budget_models import ProjectBudgetAssignment
        from codemie.service.budget.project_budget_service import ProjectBudgetService

        settings_list = await asyncio.to_thread(Settings.get_all_project_litellm_settings)
        total = len(settings_list)
        logger.info(f"Starting project budget backfill from settings: {total} settings found")

        provider = get_active_provider()
        migrated = migrated_members = skipped_existing = skipped_not_found = failed = 0

        for setting in settings_list:
            try:
                status, member_count = await self._migrate_project_budget_setting(
                    session=session,
                    provider=provider,
                    setting=setting,
                    actor_id=actor_id,
                    project_budget_assignment_repository=project_budget_assignment_repository,
                    project_member_budget_assignment_repository=project_member_budget_assignment_repository,
                    project_budget_service=ProjectBudgetService,
                    project_budget_assignment_model=ProjectBudgetAssignment,
                )
            except Exception as exc:
                logger.warning(
                    f"Failed to migrate project budget {getattr(setting, 'alias', None)!r} "
                    f"for {getattr(setting, 'project_name', None)!r}: {exc}"
                )
                failed += 1
                continue

            if status == "migrated":
                migrated += 1
                migrated_members += member_count
            elif status == "skipped_existing":
                skipped_existing += 1
            elif status == "skipped_not_found":
                skipped_not_found += 1
            else:
                failed += 1

        logger.info(
            f"Project budget backfill complete: migrated={migrated}, members={migrated_members}, "
            f"skipped_existing={skipped_existing}, skipped_not_found={skipped_not_found}, "
            f"failed={failed}"
        )
        return ProjectBudgetBackfillResult(
            migrated=migrated,
            migrated_members=migrated_members,
            skipped_existing=skipped_existing,
            skipped_not_found=skipped_not_found,
            failed=failed,
            total_settings=total,
        )

    async def _resolve_backfill_category(self, provider, budget_ref: str) -> tuple[str, bool, object | None]:
        match = _CANONICAL_BUDGET_REF_RE.match(budget_ref)
        if match:
            return match.group("category"), False, None

        prefetched_state = await provider.get_project_budget_state_by_ref(provider_budget_ref=budget_ref)
        category = (prefetched_state.metadata if prefetched_state else {}).get("budget_category")
        if category:
            logger.info(f"Derived budget_category={category!r} from provider metadata for ref {budget_ref!r}")
            return category, True, prefetched_state

        logger.warning(f"Cannot derive budget_category for ref {budget_ref!r}; defaulting to 'platform'")
        return BudgetCategory.PLATFORM.value, True, prefetched_state

    async def _sync_backfilled_member_allocations(
        self,
        *,
        session: AsyncSession,
        provider,
        budget: Budget,
        allocations: list,
        project_name: str,
        category: str,
        project_member_budget_assignment_repository,
        project_budget_service,
    ) -> None:
        for alloc in allocations:
            try:
                member_state = await provider.sync_member_allocation(allocation=alloc, budget=budget)
                await project_member_budget_assignment_repository.update_provider_metadata(
                    session,
                    allocation_id=alloc.id,
                    provider_metadata=project_budget_service._build_provider_metadata(
                        provider=member_state.provider,
                        provider_member_ref=member_state.provider_member_ref,
                        sync_status=member_state.sync_status,
                        raw=member_state.metadata,
                    ),
                    sync_status=member_state.sync_status,
                    budget_reset_at=member_state.budget_reset_at,
                )
            except Exception as exc:
                logger.warning(
                    f"Member allocation sync failed for user {alloc.user_id!r} "
                    f"in {project_name!r}/{category}: {exc}"
                )

    async def _create_backfilled_project_budget(
        self,
        *,
        session: AsyncSession,
        provider,
        project_name: str,
        category: str,
        budget_ref: str,
        state,
        actor_id: str,
        project_budget_assignment_repository,
        project_member_budget_assignment_repository,
        project_budget_service,
        project_budget_assignment_model,
    ) -> tuple[str, int]:
        async with session.begin_nested():
            budget_id = f"{project_name}-{category}-{uuid4().hex[:8]}"
            budget = Budget(
                budget_id=budget_id,
                budget_type=BudgetType.PROJECT.value,
                name=f"{project_name}-{category}",
                description=budget_ref,
                budget_category=category,
                soft_budget=state.soft_budget,
                max_budget=state.max_budget,
                budget_duration=state.budget_duration,
                budget_reset_at=state.budget_reset_at,
                provider_metadata={
                    "provider": provider.provider_name,
                    "provider_budget_ref": budget_ref,
                    "sync_status": "ok",
                },
                created_by=actor_id,
            )
            budget = await budget_repository.insert(session, budget)
            await project_budget_assignment_repository.insert(
                session,
                project_budget_assignment_model(
                    project_name=project_name,
                    budget_category=category,
                    budget_id=budget_id,
                    allocation_mode=AllocationMode.EQUAL.value,
                    assigned_by=actor_id,
                ),
            )
            member_user_ids = await project_budget_service._get_active_member_user_ids(session, project_name)
            allocation_rows = project_budget_service._allocate_equal(
                user_ids=member_user_ids,
                max_budget=state.max_budget,
                soft_budget=state.soft_budget,
                budget_id=budget_id,
                project_name=project_name,
                budget_category=category,
                allocation_mode=AllocationMode.EQUAL.value,
                assigned_by=actor_id,
            )
            allocations = await project_member_budget_assignment_repository.insert_many(session, allocation_rows)
            await self._sync_backfilled_member_allocations(
                session=session,
                provider=provider,
                budget=budget,
                allocations=allocations,
                project_name=project_name,
                category=category,
                project_member_budget_assignment_repository=project_member_budget_assignment_repository,
                project_budget_service=project_budget_service,
            )
        return budget_id, len(allocation_rows)

    async def _migrate_project_budget_setting(
        self,
        *,
        session: AsyncSession,
        provider,
        setting,
        actor_id: str,
        project_budget_assignment_repository,
        project_member_budget_assignment_repository,
        project_budget_service,
        project_budget_assignment_model,
    ) -> tuple[str, int]:
        project_name: str = setting.project_name
        budget_ref: str | None = setting.alias
        if not budget_ref:
            logger.warning(f"Project LiteLLM setting for project {project_name!r} has no alias; skipping")
            return "failed", 0

        category, state_already_fetched, prefetched_state = await self._resolve_backfill_category(provider, budget_ref)
        existing_assignment = await project_budget_assignment_repository.get_active_by_project_category(
            session, project_name, category
        )
        if existing_assignment is not None:
            logger.debug(f"Project budget already exists for {project_name!r}/{category} — skipping")
            return "skipped_existing", 0

        state = (
            prefetched_state
            if state_already_fetched
            else await provider.get_project_budget_state_by_ref(provider_budget_ref=budget_ref)
        )
        if state is None:
            logger.warning(f"Project budget ref {budget_ref!r} not found in provider; skipping for {project_name!r}")
            return "skipped_not_found", 0

        budget_id, member_count = await self._create_backfilled_project_budget(
            session=session,
            provider=provider,
            project_name=project_name,
            category=category,
            budget_ref=budget_ref,
            state=state,
            actor_id=actor_id,
            project_budget_assignment_repository=project_budget_assignment_repository,
            project_member_budget_assignment_repository=project_member_budget_assignment_repository,
            project_budget_service=project_budget_service,
            project_budget_assignment_model=project_budget_assignment_model,
        )
        await session.commit()

        from codemie.service.budget.budget_resolution_service import clear_budget_resolution_cache

        clear_budget_resolution_cache()
        logger.info(
            f"Migrated project budget {budget_ref!r} → {budget_id!r} "
            f"for {project_name!r}/{category} ({member_count} member allocation(s) created)"
        )
        return "migrated", member_count


budget_service = BudgetService()
