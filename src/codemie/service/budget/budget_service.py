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
from typing import TYPE_CHECKING, Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from codemie.configs.budget_config import budget_config
from codemie.configs import logger
from codemie.core.exceptions import ExtendedHTTPException, ValidationException
from codemie.repository.budget_repository import budget_repository
from codemie.service.budget.budget_models import Budget

if TYPE_CHECKING:
    from codemie.enterprise.litellm.budget_categories import BudgetCategory
    from codemie.rest_api.routers.budget_router import (
        BudgetAssignmentBackfillResult,
        BudgetCreateRequest,
        BudgetSyncResult,
        BudgetUpdateRequest,
    )

_DURATION_RE = re.compile(r"^\d+[dhm]$")


class BudgetService:
    """Service for budget CRUD and LiteLLM synchronisation."""

    # ==================== Validation ====================

    @staticmethod
    def _validate_constraints(
        soft_budget: float,
        max_budget: float,
        budget_duration: str,
        budget_category: str,
    ) -> None:
        from codemie.enterprise.litellm.budget_categories import BudgetCategory

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
    def _validate_budget_matches_category(budget: Budget, category: "BudgetCategory") -> None:
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
        """Validate, persist in DB, sync to LiteLLM, read back budget_reset_at."""
        from codemie.enterprise.litellm import create_budget_in_litellm

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
            raise ExtendedHTTPException(code=409, message=f"Budget '{data.budget_id}' already exists")
        if await budget_repository.get_by_name(session, data.name) is not None:
            raise ExtendedHTTPException(code=409, message=f"Budget name '{data.name}' already in use")

        budget = Budget(
            budget_id=data.budget_id,
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
            raise ExtendedHTTPException(code=409, message=f"Budget '{data.budget_id}' already exists")

        try:
            result = await asyncio.to_thread(
                create_budget_in_litellm,
                data.budget_id,
                data.max_budget,
                data.soft_budget,
                data.budget_duration,
            )
        except Exception as exc:
            logger.error(f"LiteLLM create_budget failed for '{data.budget_id}': {exc}")
            await session.rollback()
            raise ExtendedHTTPException(code=502, message="Failed to sync budget to LiteLLM proxy") from exc
        if result is None:
            await session.rollback()
            raise ExtendedHTTPException(code=502, message="Failed to sync budget to LiteLLM proxy")

        reset_at = getattr(result, "budget_reset_at", None)
        if reset_at is not None:
            budget = await budget_repository.update(session, data.budget_id, {"budget_reset_at": reset_at})

        logger.info(
            f"Budget '{data.budget_id}' created by '{actor_name or actor_id}' "
            f"(name='{data.name}', category='{data.budget_category.value}')"
        )
        return budget

    async def list_budgets(
        self,
        session: AsyncSession,
        page: int,
        per_page: int,
        category: Optional[str] = None,
    ) -> tuple[list[Budget], int]:
        """Return paginated Budget rows, optionally filtered by budget_category."""
        return await budget_repository.list_paginated(session, page=page, per_page=per_page, category=category)

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
        """Apply partial update to DB row, then delete+recreate budget in LiteLLM."""
        from codemie.enterprise.litellm import update_budget_in_litellm

        if any(b.budget_id == budget_id for b in budget_config.predefined_budgets):
            raise ExtendedHTTPException(
                code=403,
                message=f"Budget '{budget_id}' is preconfigured and cannot be modified via API",
            )

        budget = await self.get_budget(session, budget_id)

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

        if data.name is not None and data.name != budget.name:
            existing = await budget_repository.get_by_name(session, data.name)
            if existing is not None and existing.budget_id != budget_id:
                raise ExtendedHTTPException(code=409, message=f"Budget name '{data.name}' already in use")

        provided_fields = data.model_fields_set
        if not provided_fields:
            raise ValidationException("At least one budget field must be provided")

        await self._check_name_uniqueness(session, data, budget_id, budget.name)
        await self._check_category_change_allowed(
            session, budget_id, provided_fields, new_category, budget.budget_category
        )

        update_fields = self._build_update_fields(data, new_category)
        budget = await budget_repository.update(session, budget_id, update_fields)

        litellm_owned_fields = {"soft_budget", "max_budget", "budget_duration"}
        if provided_fields & litellm_owned_fields:
            budget = await self._sync_update_to_litellm(
                session, budget_id, budget, new_max, new_soft, new_duration, update_budget_in_litellm
            )

        logger.info(
            f"Budget '{budget_id}' updated by '{actor_name or actor_id}' (fields={sorted(update_fields.keys())})"
        )
        return budget

    @staticmethod
    def _build_update_fields(data: "BudgetUpdateRequest", new_category: str) -> dict:
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
        self, session: AsyncSession, data: "BudgetUpdateRequest", budget_id: str, current_name: str
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

    async def _sync_update_to_litellm(
        self,
        session: AsyncSession,
        budget_id: str,
        budget: Budget,
        new_max: float,
        new_soft: float,
        new_duration: str,
        update_fn,
    ) -> Budget:
        """Call LiteLLM update and patch budget_reset_at; roll back and raise 502 on failure."""
        try:
            result = await asyncio.to_thread(update_fn, budget_id, new_max, new_soft, new_duration)
        except Exception as exc:
            logger.error(f"LiteLLM update_budget failed for '{budget_id}': {exc}")
            await session.rollback()
            raise ExtendedHTTPException(code=502, message="Failed to sync budget update to LiteLLM proxy") from exc
        if result is None:
            await session.rollback()
            raise ExtendedHTTPException(code=502, message="Failed to sync budget update to LiteLLM proxy")
        reset_at = getattr(result, "budget_reset_at", None)
        if reset_at is not None:
            budget = await budget_repository.update(session, budget_id, {"budget_reset_at": reset_at})
        return budget

    @staticmethod
    def _category_for_budget_id(budget_id: str) -> str:
        """Derive budget_category from a LiteLLM budget_id using predefined budgets config.

        Falls back to 'platform' for unrecognised budget IDs.
        """
        for b in budget_config.predefined_budgets:
            if b.budget_id == budget_id:
                return b.budget_category
        return "platform"

    async def ensure_predefined_budgets(self, session: AsyncSession) -> None:
        """Force-create or update all predefined budgets at startup.

        Config is the source of truth — existing budgets are overwritten to match config values.
        LiteLLM and DB are kept in sync for each predefined budget.
        """
        from codemie.enterprise.litellm import (
            create_budget_in_litellm,
            list_budgets_from_litellm,
            update_budget_in_litellm,
        )

        configured_ids = [bc.budget_id for bc in budget_config.predefined_budgets]
        if not configured_ids:
            logger.info("No predefined budgets configured, skipping startup budget initialization")
            return

        logger.info(f"Starting predefined budget initialization: {configured_ids}")

        litellm_budgets = await asyncio.to_thread(list_budgets_from_litellm)
        litellm_budget_ids: set[str] = {b.budget_id for b in (litellm_budgets or [])}
        logger.info(f"Budgets found in LiteLLM: {sorted(litellm_budget_ids)}")

        for bc in budget_config.predefined_budgets:
            existing = await budget_repository.get_by_id(session, bc.budget_id)
            if existing is None:
                logger.info(f"Budget '{bc.budget_id}' not found in DB — creating")
                budget = Budget(
                    budget_id=bc.budget_id,
                    name=bc.name,
                    description=bc.description,
                    soft_budget=bc.soft_budget,
                    max_budget=bc.max_budget,
                    budget_duration=bc.budget_duration,
                    budget_category=bc.budget_category,
                    created_by="system",
                )
                await budget_repository.insert(session, budget)
            else:
                logger.info(f"Budget '{bc.budget_id}' already exists in DB — updating to match config")
                fields = {
                    "name": bc.name,
                    "description": bc.description,
                    "soft_budget": bc.soft_budget,
                    "max_budget": bc.max_budget,
                    "budget_duration": bc.budget_duration,
                    "budget_category": bc.budget_category,
                }
                await budget_repository.update(session, bc.budget_id, fields)

            if bc.budget_id in litellm_budget_ids:
                logger.info(f"Budget '{bc.budget_id}' found in LiteLLM — updating")
                result = await asyncio.to_thread(
                    update_budget_in_litellm,
                    bc.budget_id,
                    bc.max_budget,
                    bc.soft_budget,
                    bc.budget_duration,
                )
                if result is None:
                    logger.error(f"Failed to update predefined budget '{bc.budget_id}' in LiteLLM")
            else:
                logger.info(f"Budget '{bc.budget_id}' not found in LiteLLM — creating")
                result = await asyncio.to_thread(
                    create_budget_in_litellm,
                    bc.budget_id,
                    bc.max_budget,
                    bc.soft_budget,
                    bc.budget_duration,
                )
                if result is None:
                    logger.error(f"Failed to create predefined budget '{bc.budget_id}' in LiteLLM")

            await session.commit()
            logger.info(
                f"Predefined budget '{bc.budget_id}' synced "
                f"(category={bc.budget_category}, max={bc.max_budget}, "
                f"soft={bc.soft_budget}, duration={bc.budget_duration})"
            )

        logger.info(f"Predefined budget initialization complete: {len(configured_ids)} budget(s) processed")

    async def sync_budgets_from_litellm(
        self,
        session: AsyncSession,
        actor_id: str,
    ) -> BudgetSyncResult:
        """Pull all budgets from LiteLLM, compare with DB, upsert differences and delete orphans."""
        from codemie.enterprise.litellm import list_budgets_from_litellm
        from codemie.rest_api.routers.budget_router import BudgetSyncResult

        litellm_budgets = await asyncio.to_thread(list_budgets_from_litellm)
        if litellm_budgets is None:
            raise ExtendedHTTPException(code=502, message="LiteLLM proxy unreachable during sync")

        created = updated = unchanged = deleted = 0

        litellm_ids: set[str] = set()
        for lb in litellm_budgets:
            if lb.budget_id is None:
                continue
            litellm_ids.add(lb.budget_id)

            fields = {
                "soft_budget": lb.soft_budget or 0.0,
                "max_budget": lb.max_budget or 0.0,
                "budget_duration": lb.budget_duration or "30d",
                "budget_reset_at": lb.budget_reset_at,
                "name": lb.budget_id,
                "description": None,
                "budget_category": self._category_for_budget_id(lb.budget_id),
                "created_by": actor_id,
            }

            _budget, status = await budget_repository.upsert_from_litellm(session, lb.budget_id, fields)
            if status == "created":
                created += 1
            elif status == "updated":
                updated += 1
            else:
                unchanged += 1

        db_budgets = await budget_repository.get_all_keyed_by_id(session)
        for budget_id in set(db_budgets) - litellm_ids:
            logger.info(f"sync_budgets: deleting orphan budget {budget_id!r} absent from LiteLLM")
            await budget_repository.delete(session, budget_id)
            deleted += 1

        await session.commit()

        all_budgets, _ = await budget_repository.list_paginated(session, page=0, per_page=10000)
        return BudgetSyncResult(
            created=created,
            updated=updated,
            unchanged=unchanged,
            deleted=deleted,
            total_in_litellm=len(litellm_budgets),
            budgets=all_budgets,
        )

    @staticmethod
    def _base_identifier_from_litellm_user_id(user_id: str, category: "BudgetCategory") -> str:
        """Strip the category suffix from a LiteLLM customer id."""
        from codemie.enterprise.litellm.budget_categories import BudgetCategory

        if category == BudgetCategory.PLATFORM:
            return user_id
        suffix = f"_codemie_{category.value}"
        if user_id.endswith(suffix):
            return user_id[: -len(suffix)]
        return user_id

    @staticmethod
    def _serialize_litellm_datetime(value) -> str | None:
        """Store LiteLLM datetime values verbatim where possible."""
        if value is None:
            return None
        if hasattr(value, "isoformat"):
            return value.isoformat()
        return str(value)

    async def _ensure_backfilled_budget(
        self,
        session: AsyncSession,
        entry,
        category: "BudgetCategory",
        actor_id: str,
    ) -> bool:
        """Ensure a LiteLLM customer budget exists locally before assignment insert."""
        if await budget_repository.get_by_id(session, entry.budget_id) is not None:
            return False

        fields = {
            "soft_budget": float(entry.soft_budget) if entry.soft_budget is not None else 0.0,
            "max_budget": float(entry.max_budget) if entry.max_budget is not None else 0.0,
            "budget_duration": entry.budget_duration or "30d",
            "budget_reset_at": self._serialize_litellm_datetime(entry.budget_reset_at),
            "name": entry.budget_id,
            "description": "Imported from LiteLLM customer backfill",
            "budget_category": category.value,
            "created_by": actor_id,
        }
        await budget_repository.upsert_from_litellm(session, entry.budget_id, fields)
        return True

    async def _process_backfill_entry(
        self,
        session: AsyncSession,
        entry,
        actor_id: str,
    ) -> tuple[str, bool]:
        """Process one LiteLLM customer entry for backfill.

        Returns (status, budget_created) where status is one of
        'missing_user', 'existing', or 'imported'.
        """
        from codemie.enterprise.litellm.budget_categories import derive_category_from_user_id

        async with session.begin_nested():
            category = derive_category_from_user_id(entry.user_id)
            identifier = self._base_identifier_from_litellm_user_id(entry.user_id, category)
            user_id = await budget_repository.get_user_id_by_identifier(session, identifier)
            if user_id is None:
                return "missing_user", False
            if await budget_repository.get_user_category_budget_id(session, user_id, category) is not None:
                return "existing", False
            budget_created = await self._ensure_backfilled_budget(session, entry, category, actor_id)
            await budget_repository.upsert_user_category_assignment(
                session, user_id, category, entry.budget_id, assigned_by=actor_id
            )
            return "imported", budget_created

    async def _process_backfill_page(
        self,
        session: AsyncSession,
        entries: list,
        actor_id: str,
    ) -> tuple[int, int, int, int, int]:
        """Process one page of LiteLLM customer entries.

        Returns (imported, skipped_existing, skipped_missing_user, created_budgets, failed).
        """
        imported = skipped_existing = skipped_missing_user = created_budgets = failed = 0
        for entry in entries:
            try:
                status, budget_created = await self._process_backfill_entry(session, entry, actor_id)
                if status == "missing_user":
                    skipped_missing_user += 1
                elif status == "existing":
                    skipped_existing += 1
                else:
                    imported += 1
                    created_budgets += budget_created
            except Exception as exc:
                failed += 1
                logger.warning(f"Failed to backfill LiteLLM budget assignment for customer {entry.user_id!r}: {exc}")
        return imported, skipped_existing, skipped_missing_user, created_budgets, failed

    async def backfill_user_budget_assignments_from_litellm(
        self,
        session: AsyncSession,
        actor_id: str = "litellm-backfill",
        page_size: int = 100,
    ) -> BudgetAssignmentBackfillResult:
        """Import existing LiteLLM customer budget assignments into Codemie DB.

        LiteLLM remains the runtime budget enforcement source. This method only
        mirrors missing user/category assignments into user_budget_assignments and
        creates missing budget rows required by the assignment FK.
        """
        from codemie.enterprise.litellm import get_litellm_service_or_none
        from codemie.rest_api.routers.budget_router import BudgetAssignmentBackfillResult

        litellm = get_litellm_service_or_none()
        if litellm is None:
            raise ExtendedHTTPException(code=502, message="LiteLLM proxy unavailable during assignment backfill")

        imported = skipped_existing = skipped_missing_user = failed = created_budgets = 0
        total_in_litellm = 0
        page = 1

        while True:
            entries = await asyncio.to_thread(litellm.get_customer_list, page=page, size=page_size)
            if not entries:
                break

            total_in_litellm += len(entries)
            i, se, smu, cb, f = await self._process_backfill_page(session, entries, actor_id)
            imported += i
            skipped_existing += se
            skipped_missing_user += smu
            created_budgets += cb
            failed += f

            await session.commit()
            if len(entries) < page_size:
                break
            page += 1

        logger.info(
            f"LiteLLM budget assignment backfill finished: imported={imported}, "
            f"skipped_existing={skipped_existing}, skipped_missing_user={skipped_missing_user}, "
            f"created_budgets={created_budgets}, failed={failed}, total_in_litellm={total_in_litellm}"
        )
        return BudgetAssignmentBackfillResult(
            imported=imported,
            skipped_existing=skipped_existing,
            skipped_missing_user=skipped_missing_user,
            created_budgets=created_budgets,
            failed=failed,
            total_in_litellm=total_in_litellm,
        )

    # ==================== Assignments ====================

    async def track_proxy_budget_assignment_for_request(
        self,
        user_id: str,
        category: "BudgetCategory",
        budget_id: str | None,
        assigned_by: str = "system",
    ) -> None:
        """Best-effort mirror of a proxy budget assignment into Codemie DB."""
        if not budget_id:
            return

        from codemie.clients.postgres import get_async_session

        try:
            async with get_async_session() as session:
                existing = await budget_repository.get_user_category_budget_id(session, user_id, category)
                if existing is not None:
                    return
                await budget_repository.upsert_user_category_assignment(
                    session,
                    user_id,
                    category,
                    budget_id,
                    assigned_by=assigned_by,
                )
                await session.commit()
        except Exception as exc:
            logger.warning(
                f"Failed to mirror proxy budget assignment for user {user_id!r}, "
                f"category {category.value!r}, budget {budget_id!r}: {exc}"
            )

    async def validate_assignment_budget_categories(
        self,
        session: AsyncSession,
        assignments: dict[BudgetCategory, str | None],
    ) -> None:
        """Validate all non-null assignment budgets exist and match their requested category."""
        for category, budget_id in assignments.items():
            if budget_id is None:
                continue
            budget = await budget_repository.get_by_id(session, budget_id)
            if budget is None:
                raise ExtendedHTTPException(code=404, message=f"Budget '{budget_id}' not found")
            self._validate_budget_matches_category(budget, category)

    async def assign_budget_to_user(
        self,
        session: AsyncSession,
        user_id: str,
        assignments: dict[BudgetCategory, str | None],
        actor_id: str,
    ) -> None:
        """Set or clear per-category budget assignments for a user and propagate to LiteLLM.

        For each (category, budget_id) pair in *assignments*:
        - budget_id is not None → validate Budget row exists; upsert a row in
          user_budget_assignments(user_id, category, budget_id); then call
          update_customer_budget_in_litellm(build_user_id(email, category), budget_id).
        - budget_id is None → delete the row in user_budget_assignments for this
          (user_id, category); then call update_customer_budget_in_litellm(
          build_user_id(email, category), None) to clear the LiteLLM assignment.

        LiteLLM propagation errors are logged as warnings and do not abort the DB write
        (fail-open).
        """
        from codemie.enterprise.litellm import update_customer_budget_in_litellm
        from codemie.enterprise.litellm.budget_categories import build_user_id
        from codemie.rest_api.models.user_management import UserDB
        from sqlmodel import select

        result = await session.execute(select(UserDB).where(UserDB.id == user_id))
        db_user = result.scalars().first()
        if db_user is None:
            raise ExtendedHTTPException(code=404, message=f"User '{user_id}' not found")

        for category, budget_id in assignments.items():
            if budget_id is not None:
                await self.validate_assignment_budget_categories(session, {category: budget_id})
                await budget_repository.upsert_user_category_assignment(
                    session, user_id, category, budget_id, assigned_by=actor_id
                )
            else:
                await budget_repository.delete_user_category_assignment(session, user_id, category)
            litellm_user_id = build_user_id(db_user.email, category)
            success = await asyncio.to_thread(update_customer_budget_in_litellm, litellm_user_id, budget_id)
            if not success:
                logger.warning(
                    f"Failed to propagate budget assignment for user {user_id!r} "
                    f"category {category.value!r} in LiteLLM"
                )

    async def bulk_set_user_budgets(
        self,
        session: AsyncSession,
        user_ids: list[str],
        assignments: dict[BudgetCategory, str | None],
        actor_id: str,
    ) -> None:
        """Apply the same budget assignment map to multiple users and propagate to LiteLLM.

        Mirrors assign_budget_to_user but for N users at once. For each
        (category, budget_id) pair:
        - budget_id is not None → validate and upsert user_budget_assignments
        - budget_id is None → delete the row for that (user_id, category)

        LiteLLM propagation is fail-open. Raises 404 if any user_id is not found.
        """
        from codemie.enterprise.litellm import update_customer_budget_in_litellm
        from codemie.enterprise.litellm.budget_categories import build_user_id
        from codemie.rest_api.models.user_management import UserDB
        from sqlmodel import select

        non_null = {cat: bid for cat, bid in assignments.items() if bid is not None}
        if non_null:
            await self.validate_assignment_budget_categories(session, non_null)

        result = await session.execute(select(UserDB).where(UserDB.id.in_(user_ids)))
        db_users = {u.id: u for u in result.scalars().all()}

        missing = [uid for uid in user_ids if uid not in db_users]
        if missing:
            raise ExtendedHTTPException(code=404, message=f"Users not found: {missing!r}")

        for user_id in user_ids:
            for category, budget_id in assignments.items():
                if budget_id is not None:
                    await budget_repository.upsert_user_category_assignment(
                        session, user_id, category, budget_id, assigned_by=actor_id
                    )
                else:
                    await budget_repository.delete_user_category_assignment(session, user_id, category)
        for user_id, db_user in db_users.items():
            for category, budget_id in assignments.items():
                litellm_user_id = build_user_id(db_user.email, category)
                success = await asyncio.to_thread(update_customer_budget_in_litellm, litellm_user_id, budget_id)
                if not success:
                    logger.warning(
                        f"Failed to propagate bulk budget update for user {user_id!r} "
                        f"category {category.value!r} in LiteLLM"
                    )

    async def reset_user_budget_spending(
        self,
        session: AsyncSession,
        user_id: str,
        actor_id: str,
        actor_name: str = "",
        categories: list | None = None,
    ) -> None:
        """Reset budget spending for a user by recreating their LiteLLM customer records.

        For each targeted budget category, deletes the LiteLLM customer record and
        recreates it with the same budget_id, resetting the spend counter to zero.
        This unblocks a user who has reached their budget limit.

        Args:
            categories: Budget categories to reset. Pass None or an empty list to
                reset all active categories for the user.

        LiteLLM propagation failures are logged as warnings and do not abort the
        operation (fail-open). The resolution cache is cleared on completion.
        """
        from codemie.enterprise.litellm import get_category_budget_id, reset_customer_spending_in_litellm
        from codemie.enterprise.litellm.budget_categories import BudgetCategory, build_user_id
        from codemie.rest_api.models.user_management import UserDB
        from sqlmodel import select

        result = await session.execute(select(UserDB).where(UserDB.id == user_id))
        db_user = result.scalars().first()
        if db_user is None:
            raise ExtendedHTTPException(code=404, message=f"User '{user_id}' not found")

        target_categories: list[BudgetCategory] = categories if categories else list(BudgetCategory)

        category_defaults: dict[BudgetCategory, str | None] = {
            category: get_category_budget_id(category) for category in BudgetCategory
        }

        for category in target_categories:
            budget_id = await budget_repository.get_user_category_budget_id(session, user_id, category)
            if budget_id is None:
                budget_id = category_defaults.get(category)
            if not budget_id:
                continue

            litellm_user_id = build_user_id(db_user.email, category)
            success = await asyncio.to_thread(reset_customer_spending_in_litellm, litellm_user_id, budget_id)
            if not success:
                logger.warning(
                    f"Failed to reset budget spending for user {user_id!r} category {category.value!r} in LiteLLM"
                )

        reset_scope = [c.value for c in target_categories]
        logger.info(
            f"Budget spending reset for user '{user_id}' (categories={reset_scope}) by '{actor_name or actor_id}'"
        )


budget_service = BudgetService()
