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

"""Project budget service: lifecycle for project-scoped category budgets."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from codemie.configs import logger
from codemie.core.exceptions import ExtendedHTTPException, ValidationException
from codemie.repository.budget_repository import budget_repository
from codemie.repository.project_budget_repository import (
    project_budget_assignment_repository,
    project_member_budget_assignment_repository,
)
from codemie.service.budget.budget_enums import AllocationMode, BudgetCategory, BudgetType, SyncStatus
from codemie.service.budget.budget_models import (
    Budget,
    ProjectBudgetAssignment,
    ProjectMemberBudgetAssignment,
    build_override_project_budget_id,
    build_shared_project_budget_id,
)
from codemie.service.budget.provider import BudgetEnforcementProvider, BudgetProviderState
from codemie.service.budget.provider_registry import get_active_provider

if TYPE_CHECKING:
    from codemie.rest_api.routers.project_budget_router import ProjectBudgetCreateRequest, ProjectBudgetUpdateRequest

_DURATION_RE = re.compile(r"^\d+[smhd]$")
_CENT = Decimal("0.01")


class ProjectBudgetService:
    """Service for project-category budget CRUD and provider synchronisation."""

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
            raise ValidationException(f"budget_duration must match r'^\\d+[smhd]$', got {budget_duration!r}")
        valid_categories = {c.value for c in BudgetCategory}
        if budget_category not in valid_categories:
            raise ValidationException(f"budget_category must be one of {sorted(valid_categories)}")

    # ==================== Project membership ====================

    @staticmethod
    async def _get_active_member_user_ids(
        session: AsyncSession,
        project_name: str,
    ) -> list[str]:
        """Return user_ids of active (non-deleted) members of project_name, sorted."""
        from codemie.rest_api.models.user_management import UserDB, UserProject

        stmt = (
            select(UserProject.user_id)
            .join(UserDB, UserProject.user_id == UserDB.id)
            .where(
                UserProject.project_name == project_name,
                UserDB.is_active.is_(True),
                UserDB.deleted_at.is_(None),
            )
            .order_by(UserProject.user_id)
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    # ==================== Allocation ====================

    @staticmethod
    def _build_provider_metadata(
        *,
        provider: str,
        sync_status: str,
        provider_budget_ref: str | None = None,
        provider_member_ref: str | None = None,
        provider_budget_id: str | None = None,
        raw: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        raw_metadata = dict(raw or {})
        if provider_member_ref is not None:
            raw_metadata["provider_member_ref"] = provider_member_ref
        if provider_budget_id is not None:
            raw_metadata["provider_budget_id"] = provider_budget_id
        metadata = {
            "provider": provider,
            "last_synced_at": datetime.now(tz=timezone.utc).isoformat(),
            "sync_status": sync_status,
            "raw": raw_metadata,
        }
        if provider_budget_ref is not None:
            metadata["provider_budget_ref"] = provider_budget_ref
        return metadata

    @staticmethod
    def _metadata_value(metadata: dict[str, Any], key: str) -> Any:
        if key in metadata:
            return metadata[key]
        raw = metadata.get("raw")
        if isinstance(raw, dict):
            return raw.get(key)
        return None

    @staticmethod
    def _allocate_equal(
        user_ids: list[str],
        max_budget: float,
        soft_budget: float,
        budget_id: str,
        project_name: str,
        budget_category: str,
        allocation_mode: str,
        assigned_by: str,
    ) -> list[ProjectMemberBudgetAssignment]:
        """Distribute max_budget and soft_budget equally across user_ids.

        Uses Decimal arithmetic, rounds each share to cents, assigns any
        rounding remainder to the deterministic last member (user_ids must be
        sorted before calling this method).
        """
        n = len(user_ids)
        if n == 0:
            return []

        dec_max = Decimal(str(max_budget))
        dec_soft = Decimal(str(soft_budget))

        share_max = (dec_max / n).quantize(_CENT, rounding=ROUND_HALF_UP)
        share_soft = (dec_soft / n).quantize(_CENT, rounding=ROUND_HALF_UP)

        total_max = share_max * n
        total_soft = share_soft * n

        remainder_max = (dec_max - total_max).quantize(_CENT, rounding=ROUND_HALF_UP)
        remainder_soft = (dec_soft - total_soft).quantize(_CENT, rounding=ROUND_HALF_UP)

        rows: list[ProjectMemberBudgetAssignment] = []
        for i, uid in enumerate(user_ids):
            is_last = i == n - 1
            alloc_max = share_max + (remainder_max if is_last else Decimal("0"))
            alloc_soft = share_soft + (remainder_soft if is_last else Decimal("0"))
            rows.append(
                ProjectMemberBudgetAssignment(
                    project_name=project_name,
                    budget_category=budget_category,
                    project_budget_id=budget_id,
                    user_id=uid,
                    allocation_mode=allocation_mode,
                    allocated_soft_budget=float(alloc_soft),
                    allocated_max_budget=float(alloc_max),
                    assigned_by=assigned_by,
                    sync_status=SyncStatus.PENDING,
                )
            )
        return rows

    @staticmethod
    def _validate_member_amounts(allocated_max_budget: float, allocated_soft_budget: float) -> None:
        if allocated_max_budget < 0:
            raise ValidationException("allocated_max_budget must be >= 0")
        if allocated_soft_budget < 0:
            raise ValidationException("allocated_soft_budget must be >= 0")
        if allocated_soft_budget > allocated_max_budget:
            raise ValidationException("allocated_soft_budget must be <= allocated_max_budget")

    @staticmethod
    def _equal_amounts_with_fixed_overrides(
        allocations: list[ProjectMemberBudgetAssignment],
        max_budget: float,
        soft_budget: float,
    ) -> dict[str, tuple[float, float]]:
        fixed = [a for a in allocations if a.allocation_mode == AllocationMode.FIXED.value]
        equal = sorted(
            [a for a in allocations if a.allocation_mode != AllocationMode.FIXED.value],
            key=lambda a: a.user_id,
        )

        fixed_max = sum(Decimal(str(a.allocated_max_budget)) for a in fixed)
        fixed_soft = sum(Decimal(str(a.allocated_soft_budget)) for a in fixed)
        dec_max = Decimal(str(max_budget))
        dec_soft = Decimal(str(soft_budget))
        if fixed_max > dec_max or fixed_soft > dec_soft:
            raise ValidationException("fixed overrides exceed project budget")

        result = {a.user_id: (a.allocated_max_budget, a.allocated_soft_budget) for a in fixed}
        if not equal:
            return result

        remaining_max = dec_max - fixed_max
        remaining_soft = dec_soft - fixed_soft
        share_max = (remaining_max / len(equal)).quantize(_CENT, rounding=ROUND_HALF_UP)
        share_soft = (remaining_soft / len(equal)).quantize(_CENT, rounding=ROUND_HALF_UP)
        remainder_max = (remaining_max - (share_max * len(equal))).quantize(_CENT, rounding=ROUND_HALF_UP)
        remainder_soft = (remaining_soft - (share_soft * len(equal))).quantize(_CENT, rounding=ROUND_HALF_UP)

        for index, allocation in enumerate(equal):
            is_last = index == len(equal) - 1
            alloc_max = share_max + (remainder_max if is_last else Decimal("0"))
            alloc_soft = share_soft + (remainder_soft if is_last else Decimal("0"))
            result[allocation.user_id] = (float(alloc_max), float(alloc_soft))
        return result

    @staticmethod
    async def _ensure_project_exists(session: AsyncSession, project_name: str) -> None:
        from codemie.core.models import Application

        app_stmt = select(Application).where(
            Application.name == project_name,
            Application.deleted_at.is_(None),
        )
        app_result = await session.execute(app_stmt)
        if app_result.scalars().first() is None:
            raise ExtendedHTTPException(code=404, message=f"Project '{project_name}' not found or has been deleted")

    @staticmethod
    def _validate_allocation_mode(allocation_mode: str) -> None:
        if allocation_mode not in {mode.value for mode in AllocationMode}:
            raise ExtendedHTTPException(
                code=400,
                message=f"allocation_mode must be one of {sorted(mode.value for mode in AllocationMode)}",
            )

    @staticmethod
    def _invalidate_resolution_cache_for_project(project_name: str, budget_category: str) -> None:
        from codemie.service.budget.budget_resolution_service import _resolution_cache

        for key in list(_resolution_cache.keys()):
            if key[0] == project_name and key[1] == budget_category:
                _resolution_cache.pop(key, None)

    @staticmethod
    def _child_budget_name(main_budget: Budget, suffix: str) -> str:
        reserved = len(suffix) + 3
        base = main_budget.name[: max(1, 128 - reserved)]
        return f"{base} [{suffix}]"

    @staticmethod
    def _effective_member_budget_id(
        project_budget_id: str,
        allocation: ProjectMemberBudgetAssignment,
    ) -> str:
        effective_budget_id = getattr(allocation, "effective_budget_id", None)
        if effective_budget_id:
            return effective_budget_id

        override_budget_id = getattr(allocation, "override_budget_id", None)
        if override_budget_id:
            return override_budget_id

        shared_budget_id = getattr(allocation, "shared_budget_id", None)
        if shared_budget_id:
            return shared_budget_id

        if getattr(allocation, "allocation_mode", None) == AllocationMode.FIXED.value:
            return build_override_project_budget_id(project_budget_id, allocation.user_id)
        return build_shared_project_budget_id(project_budget_id)

    async def _upsert_child_budget(
        self,
        session: AsyncSession,
        *,
        child_budget_id: str,
        main_budget: Budget,
        project_name: str,
        actor_id: str,
        budget_origin_type: str,
        soft_budget: float,
        max_budget: float,
        owner_user_id: str | None = None,
    ) -> Budget:
        existing = await budget_repository.get_by_id(session, child_budget_id)
        action = "create" if existing is None else "update"
        logger.debug(
            f"budget_event=project_child_budget_upsert_started component=project_budget_service "
            f"budget_id={child_budget_id!r} parent_budget_id={main_budget.budget_id!r} "
            f"project_name={project_name!r} budget_category={main_budget.budget_category!r} "
            f"origin_type={budget_origin_type!r} owner_user_id={owner_user_id!r} action={action}"
        )
        fields = {
            "budget_type": BudgetType.PROJECT.value,
            "budget_origin_type": budget_origin_type,
            "parent_budget_id": main_budget.budget_id,
            "owner_user_id": owner_user_id,
            "project_name": project_name,
            "name": self._child_budget_name(
                main_budget,
                "shared" if owner_user_id is None else f"user:{owner_user_id[:8]}",
            ),
            "description": main_budget.description,
            "soft_budget": soft_budget,
            "max_budget": max_budget,
            "budget_duration": main_budget.budget_duration,
            "budget_category": main_budget.budget_category,
            "detached_at": None,
            "deleted_at": None,
        }
        if existing is None:
            budget = await budget_repository.insert(
                session,
                Budget(
                    budget_id=child_budget_id,
                    created_by=actor_id,
                    **fields,
                ),
            )
        else:
            budget = await budget_repository.update(session, child_budget_id, fields)
        logger.debug(
            f"budget_event=project_child_budget_upsert_completed component=project_budget_service "
            f"budget_id={child_budget_id!r} parent_budget_id={main_budget.budget_id!r} "
            f"project_name={project_name!r} budget_category={main_budget.budget_category!r} "
            f"origin_type={budget_origin_type!r} owner_user_id={owner_user_id!r} action={action}"
        )
        return budget

    async def _ensure_shared_child_budget(
        self,
        session: AsyncSession,
        *,
        main_budget: Budget,
        project_name: str,
        actor_id: str,
        per_member_soft_budget: float,
        per_member_max_budget: float,
    ) -> Budget:
        return await self._upsert_child_budget(
            session,
            child_budget_id=build_shared_project_budget_id(main_budget.budget_id),
            main_budget=main_budget,
            project_name=project_name,
            actor_id=actor_id,
            budget_origin_type="shared_default",
            soft_budget=per_member_soft_budget,
            max_budget=per_member_max_budget,
        )

    async def _ensure_override_child_budget(
        self,
        session: AsyncSession,
        *,
        main_budget: Budget,
        project_name: str,
        user_id: str,
        actor_id: str,
        allocated_soft_budget: float,
        allocated_max_budget: float,
    ) -> Budget:
        return await self._upsert_child_budget(
            session,
            child_budget_id=build_override_project_budget_id(main_budget.budget_id, user_id),
            main_budget=main_budget,
            project_name=project_name,
            actor_id=actor_id,
            budget_origin_type="member_override",
            soft_budget=allocated_soft_budget,
            max_budget=allocated_max_budget,
            owner_user_id=user_id,
        )

    async def _persist_child_budget_provider_state(
        self,
        session: AsyncSession,
        *,
        budget_id: str | None,
        member_state,
    ) -> None:
        if not budget_id:
            return
        child_budget = await budget_repository.get_by_id(session, budget_id)
        if child_budget is None:
            return
        await budget_repository.update(
            session,
            budget_id,
            {
                "budget_reset_at": member_state.budget_reset_at,
                "provider_metadata": self._build_provider_metadata(
                    provider=member_state.provider,
                    provider_budget_ref=budget_id,
                    sync_status=member_state.sync_status,
                    raw=member_state.metadata,
                ),
            },
        )

    async def _sync_created_member_allocations(
        self,
        *,
        session: AsyncSession,
        provider,
        budget: Budget,
        allocations: list[ProjectMemberBudgetAssignment],
    ) -> None:
        for alloc in allocations:
            provider_name = getattr(provider, "provider_name", "unknown")
            project_name = getattr(alloc, "project_name", None)
            budget_id = getattr(budget, "budget_id", None)
            budget_category = getattr(alloc, "budget_category", None)
            allocation_id = getattr(alloc, "id", None)
            user_id = getattr(alloc, "user_id", None)
            allocation_mode = getattr(alloc, "allocation_mode", None)
            try:
                effective_budget_id = self._effective_member_budget_id(budget_id, alloc) if budget_id else None
                logger.debug(
                    f"budget_event=provider_member_budget_sync_started component=project_budget_service "
                    f"provider={provider_name!r} project_name={project_name!r} "
                    f"budget_id={budget_id!r} effective_budget_id={effective_budget_id!r} "
                    f"budget_category={budget_category!r} allocation_id={allocation_id!r} "
                    f"user_id={user_id!r} allocation_mode={allocation_mode!r} "
                    f"allocated_max_budget={getattr(alloc, 'allocated_max_budget', None)!r} "
                    f"allocated_soft_budget={getattr(alloc, 'allocated_soft_budget', None)!r}"
                )
                member_state = await provider.sync_member_allocation(allocation=alloc, budget=budget)
                await self._persist_child_budget_provider_state(
                    session,
                    budget_id=effective_budget_id,
                    member_state=member_state,
                )
                await project_member_budget_assignment_repository.update_provider_metadata(
                    session,
                    allocation_id=alloc.id,
                    provider_metadata={
                        **self._build_provider_metadata(
                            provider=member_state.provider,
                            provider_member_ref=member_state.provider_member_ref,
                            provider_budget_id=member_state.provider_budget_id,
                            sync_status=member_state.sync_status,
                            raw=member_state.metadata,
                        )
                    },
                    sync_status=member_state.sync_status,
                    budget_reset_at=member_state.budget_reset_at,
                )
                logger.debug(
                    f"budget_event=provider_member_budget_sync_completed component=project_budget_service "
                    f"provider={member_state.provider!r} project_name={project_name!r} "
                    f"budget_id={budget_id!r} effective_budget_id={effective_budget_id!r} "
                    f"budget_category={budget_category!r} allocation_id={allocation_id!r} "
                    f"user_id={user_id!r} provider_member_ref={member_state.provider_member_ref!r} "
                    f"provider_budget_id={member_state.provider_budget_id!r} "
                    f"sync_status={member_state.sync_status!r} budget_reset_at={member_state.budget_reset_at!r}"
                )
            except Exception as exc:
                logger.warning(
                    f"budget_event=provider_member_budget_sync_failed component=project_budget_service "
                    f"provider={provider_name!r} project_name={project_name!r} budget_id={budget_id!r} "
                    f"budget_category={budget_category!r} allocation_id={allocation_id!r} "
                    f"user_id={user_id!r} error={exc}"
                )
                await project_member_budget_assignment_repository.update_provider_metadata(
                    session,
                    allocation_id=alloc.id,
                    provider_metadata={"sync_status": SyncStatus.FAILED},
                    sync_status=SyncStatus.FAILED,
                )

    async def _sync_created_project_budget(
        self,
        *,
        session: AsyncSession,
        provider,
        created_budget: Budget,
        budget_id: str,
        project_name: str,
        budget_category: BudgetCategory,
        soft_budget: float,
        max_budget: float,
        budget_duration: str,
        models: list[str] | None,
        allocations: list[ProjectMemberBudgetAssignment],
    ) -> Budget:
        try:
            provider_name = getattr(provider, "provider_name", "unknown")
            logger.debug(
                f"budget_event=provider_project_budget_sync_started component=project_budget_service "
                f"provider={provider_name!r} operation=create project_name={project_name!r} "
                f"budget_id={budget_id!r} budget_category={budget_category.value!r} "
                f"max_budget={max_budget!r} soft_budget={soft_budget!r} "
                f"budget_duration={budget_duration!r} model_count={len(models or [])}"
            )
            provider_state = await provider.ensure_project_budget(
                project_name=project_name,
                budget_category=budget_category,
                budget_id=budget_id,
                soft_budget=Decimal(str(soft_budget)),
                max_budget=Decimal(str(max_budget)),
                budget_duration=budget_duration,
                models=models,
            )
            if provider_state.sync_status not in {SyncStatus.OK, SyncStatus.NOOP}:
                raise RuntimeError(f"Project budget enforcement provider sync failed: {provider_state.sync_status}")
            logger.debug(
                f"budget_event=provider_project_budget_sync_completed component=project_budget_service "
                f"provider={provider_state.provider!r} operation=create project_name={project_name!r} "
                f"budget_id={budget_id!r} budget_category={budget_category.value!r} "
                f"provider_budget_ref={provider_state.provider_budget_ref!r} "
                f"sync_status={provider_state.sync_status!r} budget_reset_at={provider_state.budget_reset_at!r}"
            )
        except Exception as exc:
            logger.error(
                f"budget_event=provider_project_budget_sync_failed component=project_budget_service "
                f"provider={getattr(provider, 'provider_name', 'unknown')!r} operation=create "
                f"project_name={project_name!r} budget_id={budget_id!r} "
                f"budget_category={budget_category.value!r} error={exc}",
                exc_info=True,
            )
            await session.rollback()
            raise ExtendedHTTPException(
                code=502,
                message="Failed to sync project budget with enforcement provider",
            ) from exc

        _ = created_budget
        updated_budget = await budget_repository.update(
            session,
            budget_id,
            {
                "provider_metadata": {
                    **self._build_provider_metadata(
                        provider=provider_state.provider,
                        provider_budget_ref=provider_state.provider_budget_ref,
                        sync_status=provider_state.sync_status,
                        raw=provider_state.metadata,
                    )
                },
                "budget_reset_at": provider_state.budget_reset_at,
            },
        )
        await self._sync_created_member_allocations(
            session=session,
            provider=provider,
            budget=updated_budget,
            allocations=allocations,
        )
        return updated_budget

    @staticmethod
    def _changed_project_budget_fields(data: "ProjectBudgetUpdateRequest") -> dict[str, Any]:
        changed_fields: dict[str, Any] = {}
        if data.name is not None:
            changed_fields["name"] = data.name
        if data.description is not None:
            changed_fields["description"] = data.description
        if data.soft_budget is not None:
            changed_fields["soft_budget"] = data.soft_budget
        if data.max_budget is not None:
            changed_fields["max_budget"] = data.max_budget
        if data.budget_duration is not None:
            changed_fields["budget_duration"] = data.budget_duration
        return changed_fields

    @staticmethod
    def _effective_project_budget_values(
        budget: Budget,
        data: "ProjectBudgetUpdateRequest",
    ) -> tuple[float, float, str, bool]:
        eff_soft = data.soft_budget if data.soft_budget is not None else budget.soft_budget
        eff_max = data.max_budget if data.max_budget is not None else budget.max_budget
        eff_duration = data.budget_duration if data.budget_duration is not None else budget.budget_duration
        amounts_changed = (
            data.soft_budget is not None or data.max_budget is not None or data.budget_duration is not None
        )
        return eff_soft, eff_max, eff_duration, amounts_changed

    async def _sync_updated_project_budget(
        self,
        *,
        session: AsyncSession,
        budget: Budget,
        budget_id: str,
        eff_soft: float,
        eff_max: float,
        eff_duration: str,
        models: list[str] | None,
    ) -> tuple[Budget, ProjectBudgetAssignment | None, object]:
        from codemie.service.budget.provider import BudgetProviderState

        assignment = await project_budget_assignment_repository.get_active_by_budget_id(session, budget_id)
        provider = get_active_provider()
        provider_meta = budget.provider_metadata or {}
        budget_reset_at = budget.budget_reset_at
        if eff_duration != budget.budget_duration:
            budget_reset_at = None
        budget_state = BudgetProviderState(
            provider=provider_meta.get("provider", ""),
            provider_budget_ref=self._metadata_value(provider_meta, "provider_budget_ref"),
            budget_reset_at=budget_reset_at,
            sync_status=provider_meta.get("sync_status", SyncStatus.OK),
        )
        try:
            logger.debug(
                f"budget_event=provider_project_budget_sync_started component=project_budget_service "
                f"provider={provider.provider_name!r} operation=update "
                f"project_name={assignment.project_name if assignment else None!r} budget_id={budget_id!r} "
                f"budget_category={budget.budget_category!r} provider_budget_ref={budget_state.provider_budget_ref!r} "
                f"max_budget={eff_max!r} soft_budget={eff_soft!r} budget_duration={eff_duration!r} "
                f"model_count={len(models or [])}"
            )
            new_provider_state = await provider.update_project_budget(
                budget_state=budget_state,
                project_name=assignment.project_name if assignment else "",
                budget_category=BudgetCategory(budget.budget_category),
                budget_id=budget_id,
                soft_budget=Decimal(str(eff_soft)),
                max_budget=Decimal(str(eff_max)),
                budget_duration=eff_duration,
                models=models,
            )
            budget = await budget_repository.update(
                session,
                budget_id,
                {
                    "provider_metadata": {
                        **self._build_provider_metadata(
                            provider=new_provider_state.provider,
                            provider_budget_ref=new_provider_state.provider_budget_ref,
                            sync_status=new_provider_state.sync_status,
                            raw=new_provider_state.metadata,
                        )
                    },
                    "budget_reset_at": new_provider_state.budget_reset_at,
                },
            )
            logger.debug(
                f"budget_event=provider_project_budget_sync_completed component=project_budget_service "
                f"provider={new_provider_state.provider!r} operation=update "
                f"project_name={assignment.project_name if assignment else None!r} budget_id={budget_id!r} "
                f"budget_category={budget.budget_category!r} "
                f"provider_budget_ref={new_provider_state.provider_budget_ref!r} "
                f"sync_status={new_provider_state.sync_status!r} "
                f"budget_reset_at={new_provider_state.budget_reset_at!r}"
            )
        except Exception as exc:
            logger.warning(
                f"budget_event=provider_project_budget_sync_failed component=project_budget_service "
                f"provider={getattr(provider, 'provider_name', 'unknown')!r} operation=update "
                f"project_name={assignment.project_name if assignment else None!r} budget_id={budget_id!r} "
                f"budget_category={budget.budget_category!r} error={exc}"
            )
        return budget, assignment, provider

    # ==================== Create ====================

    async def create_project_budget(
        self,
        session: AsyncSession,
        data: "ProjectBudgetCreateRequest",
        actor_id: str,
        actor_name: str = "",
    ) -> Budget:
        """12-step project budget creation flow.

        1. Validate request constraints.
        2. Validate project exists and is not deleted.
        3. Validate no active assignment for (project_name, budget_category).
        4. Insert Budget(budget_type=project).
        5. Insert ProjectBudgetAssignment.
        6. Resolve active project members.
        7. Insert ProjectMemberBudgetAssignment rows.
        8. Call provider.ensure_project_budget → store provider state.
        9. Update Budget.provider_metadata.
        10. Call provider.sync_member_allocation for each member.
        11. Store member provider state in each allocation.
        12. Flush; caller commits.
        """
        from sqlalchemy.exc import IntegrityError

        logger.debug(
            f"budget_event=project_budget_create_started component=project_budget_service "
            f"project_name={data.project_name!r} requested_budget_id={data.budget_id!r} "
            f"budget_category={data.budget_category.value!r} allocation_mode={data.allocation_mode!r} "
            f"max_budget={data.max_budget!r} soft_budget={data.soft_budget!r} "
            f"budget_duration={data.budget_duration!r} actor_id={actor_id!r} actor_name={actor_name!r}"
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

        self._validate_allocation_mode(data.allocation_mode)
        await self._ensure_project_exists(session, data.project_name)

        # Append a UUID suffix so the stored budget_id is always unique,
        # allowing the same human-readable prefix to be reused after deletion.
        budget_id = f"{data.budget_id}-{uuid.uuid4().hex}"
        logger.debug(
            f"budget_event=project_budget_id_generated component=project_budget_service "
            f"project_name={data.project_name!r} requested_budget_id={data.budget_id!r} "
            f"budget_id={budget_id!r} budget_category={data.budget_category.value!r}"
        )

        # Validate name uniqueness — skip soft-deleted budgets (partial DB index handles the rest).
        existing_by_name = await budget_repository.get_by_name(session, data.name)
        if existing_by_name is not None and existing_by_name.deleted_at is None:
            raise ExtendedHTTPException(code=409, message=f"Budget name '{data.name}' already in use")

        # Step 3: validate no active assignment for this (project, category)
        existing_assignment = await project_budget_assignment_repository.get_active_by_project_category(
            session, data.project_name, data.budget_category.value
        )
        if existing_assignment is not None:
            logger.debug(
                f"budget_event=project_budget_create_rejected component=project_budget_service "
                f"project_name={data.project_name!r} budget_category={data.budget_category.value!r} "
                f"existing_budget_id={existing_assignment.budget_id!r} reason=active_assignment_exists"
            )
            raise ExtendedHTTPException(
                code=409,
                message=(
                    f"An active project budget for project '{data.project_name}' "
                    f"and category '{data.budget_category.value}' already exists"
                ),
            )

        # Step 4: insert Budget(budget_type=project)
        budget = Budget(
            budget_id=budget_id,
            budget_type=BudgetType.PROJECT.value,
            project_name=data.project_name,
            name=data.name,
            description=data.description,
            soft_budget=data.soft_budget,
            max_budget=data.max_budget,
            budget_duration=data.budget_duration,
            budget_category=data.budget_category.value,
            created_by=actor_id,
        )
        try:
            budget = await budget_repository.insert(session, budget)
        except IntegrityError:
            await session.rollback()
            raise ExtendedHTTPException(code=409, message=f"Budget '{budget_id}' already exists")

        # Step 5: insert ProjectBudgetAssignment
        assignment = ProjectBudgetAssignment(
            project_name=data.project_name,
            budget_category=data.budget_category.value,
            budget_id=budget_id,
            allocation_mode=data.allocation_mode,
            assigned_by=actor_id,
        )
        assignment = await project_budget_assignment_repository.insert(session, assignment)

        self._invalidate_resolution_cache_for_project(data.project_name, data.budget_category.value)

        # Step 6: resolve active project members
        member_user_ids = await self._get_active_member_user_ids(session, data.project_name)
        logger.debug(
            f"budget_event=project_budget_members_resolved component=project_budget_service "
            f"project_name={data.project_name!r} budget_id={budget_id!r} "
            f"budget_category={data.budget_category.value!r} member_count={len(member_user_ids)}"
        )

        # Step 7: create member allocation rows
        allocation_rows = self._allocate_equal(
            user_ids=member_user_ids,
            max_budget=data.max_budget,
            soft_budget=data.soft_budget,
            budget_id=budget_id,
            project_name=data.project_name,
            budget_category=data.budget_category.value,
            allocation_mode=data.allocation_mode,
            assigned_by=actor_id,
        )
        logger.debug(
            f"budget_event=project_budget_allocations_built component=project_budget_service "
            f"project_name={data.project_name!r} budget_id={budget_id!r} "
            f"budget_category={data.budget_category.value!r} allocation_count={len(allocation_rows)} "
            f"allocation_mode={data.allocation_mode!r}"
        )
        shared_budget = await self._ensure_shared_child_budget(
            session,
            main_budget=budget,
            project_name=data.project_name,
            actor_id=actor_id,
            per_member_soft_budget=allocation_rows[0].allocated_soft_budget if allocation_rows else budget.soft_budget,
            per_member_max_budget=allocation_rows[0].allocated_max_budget if allocation_rows else budget.max_budget,
        )
        logger.debug(
            f"budget_event=project_shared_child_budget_selected component=project_budget_service "
            f"project_name={data.project_name!r} budget_id={budget_id!r} "
            f"shared_budget_id={shared_budget.budget_id!r} budget_category={data.budget_category.value!r}"
        )
        for row in allocation_rows:
            row.shared_budget_id = shared_budget.budget_id
            row.effective_budget_id = shared_budget.budget_id
        allocations = await project_member_budget_assignment_repository.insert_many(session, allocation_rows)

        provider = get_active_provider()
        budget = await self._sync_created_project_budget(
            session=session,
            provider=provider,
            created_budget=budget,
            budget_id=budget_id,
            project_name=data.project_name,
            budget_category=data.budget_category,
            soft_budget=data.soft_budget,
            max_budget=data.max_budget,
            budget_duration=data.budget_duration,
            models=data.models,
            allocations=allocations,
        )

        member_count = len(allocations)
        logger.info(
            f"budget_event=project_budget_create_completed component=project_budget_service "
            f"project_name={data.project_name!r} budget_id={budget_id!r} "
            f"budget_category={data.budget_category.value!r} member_count={member_count} "
            f"actor_id={actor_id!r} actor_name={actor_name or actor_id!r}"
        )
        return budget

    # ==================== Read ====================

    async def get_project_budget(
        self,
        session: AsyncSession,
        budget_id: str,
    ) -> tuple[Budget, ProjectBudgetAssignment | None, list[ProjectMemberBudgetAssignment]]:
        """Fetch project budget with its assignment and member allocations. 404 if not found."""
        budget = await budget_repository.get_by_id(session, budget_id)
        if budget is None or budget.budget_type != BudgetType.PROJECT.value:
            raise ExtendedHTTPException(code=404, message=f"Project budget not found: {budget_id}")
        assignment = await project_budget_assignment_repository.get_active_by_budget_id(session, budget_id)
        if assignment is None:
            raise ExtendedHTTPException(code=404, message=f"Project budget not found: {budget_id}")
        allocations = await project_member_budget_assignment_repository.get_active_by_budget_id(session, budget_id)
        return budget, assignment, allocations

    async def list_project_budgets(
        self,
        session: AsyncSession,
        page: int,
        per_page: int,
        project_name: str | None = None,
        category: str | None = None,
        allowed_projects: list[str] | None = None,
    ) -> tuple[list[Budget], int]:
        """List project budgets with optional filters and pagination."""
        from sqlalchemy import func as sa_func

        base_stmt = (
            select(Budget)
            .join(ProjectBudgetAssignment, ProjectBudgetAssignment.budget_id == Budget.budget_id)
            .where(
                Budget.budget_type == BudgetType.PROJECT.value,
                ProjectBudgetAssignment.deleted_at.is_(None),
            )
        )
        if allowed_projects is not None:
            if not allowed_projects:
                return [], 0
            base_stmt = base_stmt.where(ProjectBudgetAssignment.project_name.in_(allowed_projects))
        if project_name is not None:
            base_stmt = base_stmt.where(ProjectBudgetAssignment.project_name == project_name)
        if category is not None:
            base_stmt = base_stmt.where(Budget.budget_category == category)

        count_stmt = select(sa_func.count()).select_from(base_stmt.subquery())
        total = int((await session.execute(count_stmt)).scalar_one())

        data_stmt = base_stmt.order_by(Budget.created_at.desc()).offset(page * per_page).limit(per_page)
        result = await session.execute(data_stmt)
        return list(result.scalars().all()), total

    # ==================== Update ====================

    async def _sync_fixed_allocation(
        self,
        session: AsyncSession,
        budget: Budget,
        alloc_id: str,
        updated: ProjectMemberBudgetAssignment,
        provider: BudgetEnforcementProvider,
    ) -> None:
        """Sync a single FIXED-mode member allocation with the provider."""
        await self._ensure_override_child_budget(
            session,
            main_budget=budget,
            project_name=updated.project_name,
            user_id=updated.user_id,
            actor_id=getattr(budget, "created_by", "system"),
            allocated_soft_budget=updated.allocated_soft_budget,
            allocated_max_budget=updated.allocated_max_budget,
        )
        member_state = await provider.sync_member_allocation(allocation=updated, budget=budget)
        await self._persist_child_budget_provider_state(
            session,
            budget_id=self._effective_member_budget_id(budget.budget_id, updated),
            member_state=member_state,
        )
        await project_member_budget_assignment_repository.update_provider_metadata(
            session,
            allocation_id=alloc_id,
            provider_metadata={
                **self._build_provider_metadata(
                    provider=member_state.provider,
                    provider_member_ref=member_state.provider_member_ref,
                    provider_budget_id=member_state.provider_budget_id,
                    sync_status=member_state.sync_status,
                    raw=member_state.metadata,
                )
            },
            sync_status=member_state.sync_status,
            budget_reset_at=member_state.budget_reset_at,
        )

    async def _resync_member_allocation(
        self,
        session: AsyncSession,
        *,
        budget_id: str,
        budget: Budget,
        alloc: ProjectMemberBudgetAssignment,
        new_amounts: tuple[float, float] | None,
        provider: BudgetEnforcementProvider,
    ) -> tuple[float, float] | None:
        if new_amounts is None:
            return None
        new_max, new_soft = new_amounts
        try:
            updated = await project_member_budget_assignment_repository.update_allocation(
                session,
                allocation_id=alloc.id,
                allocated_max_budget=new_max,
                allocated_soft_budget=new_soft,
            )
            if updated is None:
                return None
            if updated.allocation_mode == AllocationMode.FIXED.value:
                await self._sync_fixed_allocation(session, budget, alloc.id, updated, provider)
                return None
            return updated.allocated_max_budget, updated.allocated_soft_budget
        except Exception as exc:
            logger.warning(
                f"budget_event=provider_member_budget_sync_failed component=project_budget_service "
                f"provider={getattr(provider, 'provider_name', 'unknown')!r} "
                f"project_name={alloc.project_name!r} budget_id={budget_id!r} "
                f"budget_category={alloc.budget_category!r} allocation_id={alloc.id!r} "
                f"user_id={alloc.user_id!r} error={exc}"
            )
            return None

    async def _ensure_shared_child_budget_after_resync(
        self,
        session: AsyncSession,
        *,
        budget_id: str,
        budget: Budget,
        shared_sample: tuple[float, float] | None,
    ) -> None:
        if shared_sample is None:
            return
        assignment = await project_budget_assignment_repository.get_active_by_budget_id(session, budget_id)
        if assignment is None:
            return
        await self._ensure_shared_child_budget(
            session,
            main_budget=budget,
            project_name=assignment.project_name,
            actor_id=getattr(budget, "created_by", "system"),
            per_member_soft_budget=shared_sample[1],
            per_member_max_budget=shared_sample[0],
        )

    async def _resync_member_allocations(
        self,
        session: AsyncSession,
        budget_id: str,
        budget: Budget,
        eff_max: float,
        eff_soft: float,
        provider: BudgetEnforcementProvider,
    ) -> None:
        """Re-compute allocations and sync only budgets/customers that actually require provider changes."""
        allocations = await project_member_budget_assignment_repository.get_active_by_budget_id(session, budget_id)
        if not allocations:
            return
        try:
            new_alloc_map = self._equal_amounts_with_fixed_overrides(allocations, eff_max, eff_soft)
        except ValidationException as exc:
            raise ExtendedHTTPException(code=400, message=str(exc)) from exc
        shared_sample: tuple[float, float] | None = None
        for alloc in allocations:
            sample = await self._resync_member_allocation(
                session,
                budget_id=budget_id,
                budget=budget,
                alloc=alloc,
                new_amounts=new_alloc_map.get(alloc.user_id),
                provider=provider,
            )
            shared_sample = shared_sample or sample
        await self._ensure_shared_child_budget_after_resync(
            session,
            budget_id=budget_id,
            budget=budget,
            shared_sample=shared_sample,
        )

    async def update_project_budget(
        self,
        session: AsyncSession,
        budget_id: str,
        data: "ProjectBudgetUpdateRequest",
        actor_id: str,
    ) -> Budget:
        """Update a project budget and sync changed amounts with the provider."""
        budget = await budget_repository.get_by_id(session, budget_id)
        if budget is None or budget.budget_type != BudgetType.PROJECT.value:
            raise ExtendedHTTPException(code=404, message=f"Project budget not found: {budget_id}")

        changed_fields = self._changed_project_budget_fields(data)
        eff_soft, eff_max, eff_duration, amounts_changed = self._effective_project_budget_values(budget, data)
        if amounts_changed or data.models is not None:
            try:
                self._validate_constraints(
                    soft_budget=eff_soft,
                    max_budget=eff_max,
                    budget_duration=eff_duration,
                    budget_category=budget.budget_category,
                )
            except ValidationException as exc:
                raise ExtendedHTTPException(code=400, message=str(exc)) from exc

        if changed_fields:
            budget = await budget_repository.update(session, budget_id, changed_fields)

        if amounts_changed or data.models is not None:
            budget, _assignment, provider = await self._sync_updated_project_budget(
                session=session,
                budget=budget,
                budget_id=budget_id,
                eff_soft=eff_soft,
                eff_max=eff_max,
                eff_duration=eff_duration,
                models=data.models,
            )
            if amounts_changed:
                await self._resync_member_allocations(
                    session=session,
                    budget_id=budget_id,
                    budget=budget,
                    eff_max=eff_max,
                    eff_soft=eff_soft,
                    provider=provider,
                )

        logger.info(
            f"budget_event=project_budget_update_completed component=project_budget_service "
            f"project_name={_assignment.project_name if _assignment else None!r} budget_id={budget_id!r} "
            f"budget_category={budget.budget_category!r} updated_fields={sorted(changed_fields.keys())!r} "
            f"actor_id={actor_id!r}"
        )
        return budget

    async def rebalance_project_budget(
        self,
        session: AsyncSession,
        budget_id: str,
        actor_id: str,
    ) -> Budget:
        """Recalculate member allocations and sync them through the active provider."""
        budget, assignment, _allocations = await self.get_project_budget(session, budget_id)
        provider = get_active_provider()
        await self._resync_member_allocations(
            session=session,
            budget_id=budget_id,
            budget=budget,
            eff_max=budget.max_budget,
            eff_soft=budget.soft_budget,
            provider=provider,
        )

        # Invalidate resolution cache entries for all (project, category) entries after rebalance.
        if assignment is not None:
            from codemie.service.budget.budget_resolution_service import _resolution_cache

            proj = assignment.project_name
            cat = assignment.budget_category
            for key in list(_resolution_cache.keys()):
                if key[0] == proj and key[1] == cat:
                    _resolution_cache.pop(key, None)

        logger.info(
            f"budget_event=project_budget_rebalance_completed component=project_budget_service "
            f"project_name={assignment.project_name if assignment else None!r} budget_id={budget_id!r} "
            f"budget_category={budget.budget_category!r} actor_id={actor_id!r}"
        )
        return budget

    async def reset_project_budget(
        self,
        session: AsyncSession,
        budget_id: str,
        actor_id: str,
    ) -> Budget:
        """Reset project and member enforcement state through the active provider."""
        from codemie.service.budget.provider import BudgetProviderState

        budget, assignment, allocations = await self.get_project_budget(session, budget_id)
        provider = get_active_provider()
        provider_meta = budget.provider_metadata or {}
        budget_state = BudgetProviderState(
            provider=provider_meta.get("provider", ""),
            provider_budget_ref=self._metadata_value(provider_meta, "provider_budget_ref"),
            budget_reset_at=budget.budget_reset_at,
            sync_status=provider_meta.get("sync_status", SyncStatus.OK),
        )
        # Preserve the models allow-list stored in provider_metadata.raw on reset.
        existing_raw = self._metadata_value(provider_meta, "raw")
        existing_models = existing_raw.get("models") if isinstance(existing_raw, dict) else None
        # If no provider_budget_ref exists (budget was never synced or was seeded directly),
        # fall back to ensure_project_budget which creates the LiteLLM key from scratch.
        if budget_state.provider_budget_ref is None:
            state = await provider.ensure_project_budget(
                project_name=assignment.project_name,
                budget_category=BudgetCategory(budget.budget_category),
                budget_id=budget_id,
                soft_budget=Decimal(str(budget.soft_budget)),
                max_budget=Decimal(str(budget.max_budget)),
                budget_duration=budget.budget_duration,
                models=existing_models or None,
            )
        else:
            state = await provider.update_project_budget(
                budget_state=budget_state,
                project_name=assignment.project_name,
                budget_category=BudgetCategory(budget.budget_category),
                budget_id=budget_id,
                soft_budget=Decimal(str(budget.soft_budget)),
                max_budget=Decimal(str(budget.max_budget)),
                budget_duration=budget.budget_duration,
                models=existing_models or None,
            )
        budget = await budget_repository.update(
            session,
            budget_id,
            {
                "budget_reset_at": state.budget_reset_at,
                "provider_metadata": self._build_provider_metadata(
                    provider=state.provider,
                    provider_budget_ref=state.provider_budget_ref,
                    sync_status=state.sync_status,
                    raw=state.metadata,
                ),
            },
        )
        for allocation in allocations:
            member_state = await provider.sync_member_allocation(allocation=allocation, budget=budget)
            await self._persist_child_budget_provider_state(
                session,
                budget_id=self._effective_member_budget_id(budget.budget_id, allocation),
                member_state=member_state,
            )
            await project_member_budget_assignment_repository.update_provider_metadata(
                session,
                allocation_id=allocation.id,
                provider_metadata=self._build_provider_metadata(
                    provider=member_state.provider,
                    provider_member_ref=member_state.provider_member_ref,
                    provider_budget_id=member_state.provider_budget_id,
                    sync_status=member_state.sync_status,
                    raw=member_state.metadata,
                ),
                sync_status=member_state.sync_status,
                budget_reset_at=member_state.budget_reset_at,
            )
        logger.info(
            f"budget_event=project_budget_reset_completed component=project_budget_service "
            f"project_name={assignment.project_name!r} budget_id={budget_id!r} "
            f"budget_category={budget.budget_category!r} allocation_count={len(allocations)} "
            f"actor_id={actor_id!r}"
        )
        return budget

    async def override_member_allocation(
        self,
        session: AsyncSession,
        budget_id: str,
        user_id: str,
        allocated_max_budget: float,
        allocated_soft_budget: float,
        override_reason: str | None,
        actor_id: str,
    ) -> ProjectMemberBudgetAssignment:
        """Set a fixed member allocation override and rebalance remaining members."""
        budget, assignment, _allocations = await self.get_project_budget(session, budget_id)
        try:
            self._validate_member_amounts(allocated_max_budget, allocated_soft_budget)
        except ValidationException as exc:
            raise ExtendedHTTPException(code=400, message=str(exc)) from exc
        allocation = await project_member_budget_assignment_repository.update_member_override(
            session,
            budget_id=budget_id,
            user_id=user_id,
            allocated_max_budget=allocated_max_budget,
            allocated_soft_budget=allocated_soft_budget,
            override_reason=override_reason,
            assigned_by=actor_id,
        )
        if allocation is None:
            raise ExtendedHTTPException(code=404, message=f"Member allocation not found for user '{user_id}'")
        override_budget = await self._ensure_override_child_budget(
            session,
            main_budget=budget,
            project_name=assignment.project_name if assignment else "",
            user_id=user_id,
            actor_id=actor_id,
            allocated_soft_budget=allocated_soft_budget,
            allocated_max_budget=allocated_max_budget,
        )
        allocation = await project_member_budget_assignment_repository.update_member_budget_routing(
            session,
            allocation_id=allocation.id,
            shared_budget_id=allocation.shared_budget_id or build_shared_project_budget_id(budget_id),
            override_budget_id=override_budget.budget_id,
            effective_budget_id=override_budget.budget_id,
            allocation_mode=AllocationMode.FIXED.value,
            override_reason=override_reason,
        )
        await self.rebalance_project_budget(session, budget_id, actor_id)
        if allocation is None:
            raise ExtendedHTTPException(code=404, message=f"Member allocation not found for user '{user_id}'")
        return allocation

    async def clear_member_override(
        self,
        session: AsyncSession,
        budget_id: str,
        user_id: str,
        actor_id: str,
    ) -> ProjectMemberBudgetAssignment:
        """Clear a fixed member override and rebalance the category."""
        allocation = await project_member_budget_assignment_repository.clear_member_override(
            session,
            budget_id,
            user_id,
        )
        if allocation is None:
            budget = await budget_repository.get_by_id(session, budget_id)
            if budget is None or budget.budget_type != BudgetType.PROJECT.value:
                raise ExtendedHTTPException(code=404, message=f"Project budget not found: {budget_id}")
            raise ExtendedHTTPException(code=404, message=f"Member allocation not found for user '{user_id}'")
        budget, assignment, _allocations = await self.get_project_budget(session, budget_id)
        shared_budget = await self._ensure_shared_child_budget(
            session,
            main_budget=budget,
            project_name=assignment.project_name if assignment else "",
            actor_id=actor_id,
            per_member_soft_budget=allocation.allocated_soft_budget,
            per_member_max_budget=allocation.allocated_max_budget,
        )
        if allocation.override_budget_id:
            await budget_repository.detach_budget(session, allocation.override_budget_id)
        allocation = await project_member_budget_assignment_repository.update_member_budget_routing(
            session,
            allocation_id=allocation.id,
            shared_budget_id=shared_budget.budget_id,
            override_budget_id=allocation.override_budget_id,
            effective_budget_id=shared_budget.budget_id,
            allocation_mode=AllocationMode.EQUAL.value,
            override_reason=None,
        )
        provider = get_active_provider()
        member_state = await provider.sync_member_allocation(allocation=allocation, budget=budget)
        await self._persist_child_budget_provider_state(
            session,
            budget_id=self._effective_member_budget_id(budget.budget_id, allocation),
            member_state=member_state,
        )
        await project_member_budget_assignment_repository.update_provider_metadata(
            session,
            allocation_id=allocation.id,
            provider_metadata=self._build_provider_metadata(
                provider=member_state.provider,
                provider_member_ref=member_state.provider_member_ref,
                provider_budget_id=member_state.provider_budget_id,
                sync_status=member_state.sync_status,
                raw=member_state.metadata,
            ),
            sync_status=member_state.sync_status,
            budget_reset_at=member_state.budget_reset_at,
        )
        await self.rebalance_project_budget(session, budget_id, actor_id)
        return allocation

    # ==================== Delete ====================

    async def _delete_provider_member_allocations(
        self,
        provider: BudgetEnforcementProvider,
        *,
        allocations: list[ProjectMemberBudgetAssignment],
        budget_id: str,
    ) -> None:
        for alloc in allocations:
            try:
                await provider.delete_member_allocation(allocation=alloc)
            except Exception as exc:
                logger.warning(
                    f"budget_event=provider_member_budget_delete_failed component=project_budget_service "
                    f"provider={getattr(provider, 'provider_name', 'unknown')!r} "
                    f"project_name={alloc.project_name!r} budget_id={budget_id!r} "
                    f"budget_category={alloc.budget_category!r} allocation_id={alloc.id!r} "
                    f"user_id={alloc.user_id!r} error={exc}"
                )

    async def _delete_provider_project_budget(
        self,
        provider: BudgetEnforcementProvider,
        *,
        budget: Budget,
        budget_id: str,
        assignment: ProjectBudgetAssignment | None,
        provider_meta: dict[str, Any],
    ) -> None:
        budget_state = BudgetProviderState(
            provider=provider_meta.get("provider", ""),
            provider_budget_ref=self._metadata_value(provider_meta, "provider_budget_ref"),
            budget_reset_at=budget.budget_reset_at,
            sync_status=provider_meta.get("sync_status", SyncStatus.OK),
        )
        try:
            await provider.delete_project_budget(
                budget_state=budget_state,
                project_name=assignment.project_name if assignment else None,
            )
        except Exception as exc:
            logger.warning(
                f"budget_event=provider_project_budget_delete_failed component=project_budget_service "
                f"provider={getattr(provider, 'provider_name', 'unknown')!r} operation=delete "
                f"project_name={assignment.project_name if assignment else None!r} budget_id={budget_id!r} "
                f"budget_category={budget.budget_category!r} error={exc}"
            )

    async def _soft_delete_project_budget_rows(
        self,
        session: AsyncSession,
        *,
        budget_id: str,
        assignment: ProjectBudgetAssignment | None,
        allocations: list[ProjectMemberBudgetAssignment],
        child_budgets: list[Budget],
        provider_meta: dict[str, Any],
    ) -> None:
        await project_member_budget_assignment_repository.soft_delete_all_by_budget_id(session, budget_id)

        if assignment is not None:
            await project_budget_assignment_repository.soft_delete(session, assignment.id)

        from codemie.service.budget.budget_resolution_service import _resolution_cache

        if assignment is not None:
            for alloc in allocations:
                key = (assignment.project_name, assignment.budget_category, alloc.user_id)
                _resolution_cache.pop(key, None)

        now = datetime.now(tz=timezone.utc)
        for child_budget in child_budgets:
            await budget_repository.update(
                session,
                child_budget.budget_id,
                {
                    "deleted_at": now,
                    "detached_at": now,
                    "provider_metadata": {
                        **(child_budget.provider_metadata or {}),
                        "sync_status": "deleted",
                        "deleted_at": now.isoformat(),
                    },
                },
            )
        await budget_repository.update(
            session,
            budget_id,
            {
                "deleted_at": now,
                "provider_metadata": {
                    **provider_meta,
                    "sync_status": "deleted",
                    "deleted_at": now.isoformat(),
                },
            },
        )

    async def delete_project_budget(
        self,
        session: AsyncSession,
        budget_id: str,
        actor_id: str,
    ) -> None:
        """Delete a project budget by soft-deleting active assignment/allocation rows."""
        budget = await budget_repository.get_by_id(session, budget_id)
        if budget is None or budget.budget_type != BudgetType.PROJECT.value:
            raise ExtendedHTTPException(code=404, message=f"Project budget not found: {budget_id}")

        assignment = await project_budget_assignment_repository.get_active_by_budget_id(session, budget_id)
        allocations = await project_member_budget_assignment_repository.get_active_by_budget_id(session, budget_id)
        child_budgets = await budget_repository.list_active_child_budgets(session, parent_budget_id=budget_id)

        provider = get_active_provider()
        await self._delete_provider_member_allocations(provider, allocations=allocations, budget_id=budget_id)

        provider_meta = budget.provider_metadata or {}
        await self._delete_provider_project_budget(
            provider,
            budget=budget,
            budget_id=budget_id,
            assignment=assignment,
            provider_meta=provider_meta,
        )
        await self._soft_delete_project_budget_rows(
            session,
            budget_id=budget_id,
            assignment=assignment,
            allocations=allocations,
            child_budgets=child_budgets,
            provider_meta=provider_meta,
        )
        logger.info(
            f"budget_event=project_budget_delete_completed component=project_budget_service "
            f"project_name={assignment.project_name if assignment else None!r} budget_id={budget_id!r} "
            f"budget_category={budget.budget_category!r} allocation_count={len(allocations)} "
            f"child_budget_count={len(child_budgets)} actor_id={actor_id!r}"
        )


project_budget_service = ProjectBudgetService()
