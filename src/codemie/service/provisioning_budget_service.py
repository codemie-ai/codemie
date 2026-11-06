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

"""Budget operations of the provisioning API: set, adjust and cancel a project's budget.

A write first runs a read-only check phase that raises every deterministic 4xx, so a 4xx means nothing
changed. Commit 1 then stores the project, its cost center and admins; commit 2 stores the budget group
and syncs it with the enforcement provider. A failure after commit 1 is a 5xx, and sending the same
request again completes it, because every write is find-or-create.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from codemie.clients.postgres import get_async_session, get_session
from codemie.configs.logger import logger
from codemie.configs.provisioning_clients_config import ProvisioningClient, ProvisioningPermission
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import Application
from codemie.repository.budget_repository import budget_repository
from codemie.repository.project_budget_repository import (
    project_budget_assignment_repository,
    project_budget_group_repository,
)
from codemie.rest_api.models.project_budget import (
    CategoryBudgetSpec,
    CategoryBudgetSpecUpdate,
    ProjectBudgetGroupCreateRequest,
    ProjectBudgetGroupUpdateRequest,
)
from codemie.rest_api.models.provisioning import (
    ProvisioningBudgetRequest,
    ProvisioningBudgetSetRequest,
    ProvisioningProjectBlock,
)
from codemie.rest_api.security.provisioning_access import require_permission
from codemie.rest_api.security.user import User
from codemie.service.budget.budget_enums import BudgetCategory, SyncStatus
from codemie.service.budget.budget_models import Budget
from codemie.service.budget.project_budget_service import (
    ProjectBudgetGroupCategoryResult,
    ProjectBudgetGroupFullResult,
    ProjectBudgetService,
    project_budget_service,
)
from codemie.service.budget.provider import BudgetEnforcementProvider
from codemie.service.budget.provider_registry import get_active_provider, is_sync_current
from codemie.service.provisioning_service import AdminAssignment, ProvisioningService
from codemie.service.user.authentication_service import invalidate_user_from_cache

PLATFORM_BUDGET_MUST_BE_POSITIVE = "Platform budget must be greater than 0"
INVALID_BUDGET_MESSAGE = "Invalid budget request"
PROJECT_NOT_FOUND_MESSAGE = "Project '{name}' does not exist"
CANCEL_PROJECT_NOT_FOUND_HELP = "Check the CodeMie project name; there is no budget to cancel."
PARTLY_APPLIED_MESSAGE = "partly applied; send the same request again"
SET_BUDGET_ROUTE = "PUT /v1/provisioning/projects/{project_name}/budget"
ADJUST_BUDGET_ROUTE = "PATCH /v1/provisioning/projects/{project_name}/budget"
DEFAULT_BUDGET_DURATION = "30d"
_AMOUNT_TOLERANCE = 1e-6
_PCT_SEARCH_STEPS = 8

_AmountsResolver = Callable[[dict[BudgetCategory, float], dict[BudgetCategory, float]], dict[BudgetCategory, float]]


def replace_amounts(requested: dict[BudgetCategory, float]) -> dict[BudgetCategory, float]:
    return {category: requested.get(category, 0.0) for category in BudgetCategory}


def merge_amounts(
    requested: dict[BudgetCategory, float], current: dict[BudgetCategory, float]
) -> dict[BudgetCategory, float]:
    return {category: requested.get(category, current.get(category, 0.0)) for category in BudgetCategory}


def build_category_specs(
    amounts: dict[BudgetCategory, float],
    current: dict[BudgetCategory, Budget],
) -> tuple[float, dict[str, CategoryBudgetSpec]]:
    if amounts[BudgetCategory.PLATFORM] <= 0:
        raise ExtendedHTTPException(code=400, message=PLATFORM_BUDGET_MUST_BE_POSITIVE)

    total = float(sum(Decimal(str(amounts[category])) for category in BudgetCategory))
    pcts = {category: _exact_pct(total, amounts[category]) for category in BudgetCategory}

    specs = {
        category.value: CategoryBudgetSpec(
            pct=pcts[category],
            soft_budget=_preserved_soft_budget(current.get(category), amounts[category]),
        )
        for category in BudgetCategory
    }
    return total, specs


def _exact_pct(total: float, amount: float) -> float:
    if amount == 0:
        return 0.0
    pct = float(Decimal(str(amount)) * 100 / Decimal(str(total)))
    best, best_error = pct, math.inf
    # The budget service derives the amount back through Decimal(str(pct)), which can land one float
    # step off; walk neighbouring floats until the derivation matches exactly.
    for _ in range(_PCT_SEARCH_STEPS + 1):
        derived = ProjectBudgetService.category_amount(total, pct)
        if derived == amount:
            return pct
        if abs(derived - amount) < best_error:
            best, best_error = pct, abs(derived - amount)
        pct = math.nextafter(pct, math.inf if derived < amount else -math.inf)
    return best


def _plan_amounts(
    requested: dict[BudgetCategory, float],
    compute_amounts: _AmountsResolver,
    current: dict[BudgetCategory, Budget],
) -> tuple[float, dict[str, CategoryBudgetSpec]]:
    amounts = compute_amounts(requested, {category: budget.max_budget for category, budget in current.items()})
    return build_category_specs(amounts, current)


def _failure_reason(error: Exception) -> str:
    if isinstance(error, ExtendedHTTPException):
        return f"status={error.code}, message={error.message!r}"
    return f"error_type={type(error).__name__}, error={str(error)!r}"


def _is_synced(category: ProjectBudgetGroupCategoryResult, provider: BudgetEnforcementProvider) -> bool:
    # A pending member is pushed lazily by the runtime sync, so only a failed push counts against the call.
    return is_sync_current(category.budget.provider_metadata, provider) and not any(
        allocation.sync_status == SyncStatus.FAILED for allocation in category.allocations
    )


def _preserved_soft_budget(existing: Budget | None, amount: float) -> float | None:
    if existing is not None and abs(existing.max_budget - amount) < _AMOUNT_TOLERANCE:
        return None if existing.soft_budget is None else min(existing.soft_budget, amount)
    return None


@dataclass
class ProvisioningBudgetResult:
    project_name: str
    cost_center: str | None
    created_project: bool
    created_cost_center: bool
    created_budget: bool
    budget_group: ProjectBudgetGroupFullResult
    admins_assigned: list[str]
    admins_not_assigned: list[str]


@dataclass(frozen=True)
class _Call:
    """One budget call: who sends it, under which change reference, for which project and operation."""

    actor: User
    client: ProvisioningClient
    reference: str
    project_name: str
    operation: str

    @property
    def log_context(self) -> str:
        return f"client={self.client.name}, change_reference={self.reference!r}, operation={self.operation}"


@dataclass(frozen=True)
class _CommittedProject:
    """What commit 1 stored, read before the commit expires the project row."""

    project_name: str
    cost_center: str | None
    created_project: bool
    created_cost_center: bool
    admins: AdminAssignment


def _create_request(
    call: _Call, project_name: str, total: float, specs: dict[str, CategoryBudgetSpec]
) -> ProjectBudgetGroupCreateRequest:
    return ProjectBudgetGroupCreateRequest(
        project_name=project_name,
        name=f"{call.client.name} {call.reference}",
        total_amount=total,
        budget_duration=DEFAULT_BUDGET_DURATION,
        categories=specs,
    )


def _update_request(total: float, specs: dict[str, CategoryBudgetSpec]) -> ProjectBudgetGroupUpdateRequest:
    return ProjectBudgetGroupUpdateRequest(
        total_amount=total,
        categories={
            category: CategoryBudgetSpecUpdate(pct=spec.pct, soft_budget=spec.soft_budget)
            for category, spec in specs.items()
        },
    )


@contextmanager
def _failure_logged(call: _Call) -> Iterator[None]:
    try:
        yield
    except Exception as e:
        # A 4xx is the caller's mistake, not ours; keep it out of error-level alerting.
        is_client_error = isinstance(e, ExtendedHTTPException) and 400 <= e.code < 500
        log = logger.warning if is_client_error else logger.error
        log(
            f"provisioning_budget_request_failed: {call.log_context}, project={call.project_name!r}, "
            f"{_failure_reason(e)}, domain=provisioning"
        )
        raise


def _log_partly_applied(call: _Call, project_name: str, cause: str) -> None:
    logger.error(
        f"provisioning_budget_partly_applied: {call.log_context}, project={project_name!r}, {cause}, "
        f"domain=provisioning"
    )


class ProvisioningBudgetService:
    @classmethod
    async def set_budget(
        cls,
        actor: User,
        client: ProvisioningClient,
        reference: str,
        project_name: str,
        request: ProvisioningBudgetSetRequest,
    ) -> ProvisioningBudgetResult:
        """Replace the project's budget, creating the project and the budget when missing.

        The caller must hold manage-budgets; manage-projects is checked here when the call would create the
        project or names a cost center or admins.
        """
        call = _Call(actor=actor, client=client, reference=reference, project_name=project_name, operation="set")
        return await cls._upsert(
            call, request, lambda requested, _current: replace_amounts(requested), SET_BUDGET_ROUTE
        )

    @classmethod
    async def adjust_budget(
        cls,
        actor: User,
        client: ProvisioningClient,
        reference: str,
        project_name: str,
        request: ProvisioningBudgetRequest,
    ) -> ProvisioningBudgetResult:
        """Merge the amounts into the project's budget (0 removes a category), creating what is missing.

        Permissions as for set_budget.
        """
        call = _Call(actor=actor, client=client, reference=reference, project_name=project_name, operation="adjust")
        return await cls._upsert(call, request, merge_amounts, ADJUST_BUDGET_ROUTE)

    @classmethod
    async def cancel_budget(cls, actor: User, client: ProvisioningClient, reference: str, project_name: str) -> None:
        """Delete the project's budget; no budget is a no-op, a missing project 404. Caller must hold manage-budgets."""
        call = _Call(actor=actor, client=client, reference=reference, project_name=project_name, operation="cancel")
        logger.info(
            f"provisioning_budget_request_started: {call.log_context}, project={project_name!r}, "
            f"actor_user_id={actor.id}, domain=provisioning"
        )
        with _failure_logged(call):
            stored_name = await asyncio.to_thread(cls._find_project_to_cancel, project_name)
            deleted_budget = await cls._delete_group(actor, stored_name)
        logger.info(
            f"provisioning_budget_request_completed: {call.log_context}, project={stored_name!r}, "
            f"deleted_budget={deleted_budget}, actor_user_id={actor.id}, domain=provisioning"
        )

    @classmethod
    async def _upsert(
        cls, call: _Call, request: ProvisioningBudgetRequest, compute_amounts: _AmountsResolver, route: str
    ) -> ProvisioningBudgetResult:
        block = request.project or ProvisioningProjectBlock()
        logger.info(
            f"provisioning_budget_request_started: {call.log_context}, project={call.project_name!r}, "
            f"cost_center={block.cost_center!r}, admin_emails={len(block.admin_emails)}, "
            f"actor_user_id={call.actor.id}, domain=provisioning"
        )
        with _failure_logged(call):
            await cls._check(call, block, request.amounts, compute_amounts)
            project = await asyncio.to_thread(cls._commit_project, call, block, route)
            budget_group, created_budget = await cls._commit_budget_after_project(
                call, project.project_name, request.amounts, compute_amounts
            )

        result = ProvisioningBudgetResult(
            project_name=project.project_name,
            cost_center=project.cost_center,
            created_project=project.created_project,
            created_cost_center=project.created_cost_center,
            created_budget=created_budget,
            budget_group=budget_group,
            admins_assigned=project.admins.assigned,
            admins_not_assigned=project.admins.not_assigned,
        )
        logger.info(
            f"provisioning_budget_request_completed: {call.log_context}, project={result.project_name!r}, "
            f"cost_center={result.cost_center!r}, created_project={result.created_project}, "
            f"created_cost_center={result.created_cost_center}, created_budget={result.created_budget}, "
            f"admins_assigned={len(result.admins_assigned)}, admins_not_assigned={len(result.admins_not_assigned)}, "
            f"actor_user_id={call.actor.id}, domain=provisioning"
        )
        return result

    @classmethod
    async def _check(
        cls,
        call: _Call,
        block: ProvisioningProjectBlock,
        amounts: dict[BudgetCategory, float],
        compute_amounts: _AmountsResolver,
    ) -> None:
        """Raise every deterministic 4xx of the call before anything is written."""
        project = await asyncio.to_thread(cls._check_project, call, block)
        project_name = call.project_name if project is None else project.name
        async with get_async_session() as session:
            group = None
            if project is not None:
                group = await project_budget_group_repository.get_active_by_project(session, project_name)
            current = {} if group is None else await cls._read_budgets(session, group.id)
            try:
                total, specs = _plan_amounts(amounts, compute_amounts, current)
                data = (
                    _create_request(call, project_name, total, specs)
                    if group is None
                    else _update_request(total, specs)
                )
            except ValidationError as e:
                raise ExtendedHTTPException(
                    code=400,
                    message=INVALID_BUDGET_MESSAGE,
                    details="; ".join(error["msg"] for error in e.errors()),
                ) from e
            await project_budget_service.check_group_request(session, project_name, data)

    @staticmethod
    def _check_project(call: _Call, block: ProvisioningProjectBlock) -> Application | None:
        with get_session() as session:
            project = ProvisioningService.find_project(session, call.project_name)
            if project is None or block.cost_center is not None or block.admin_emails:
                require_permission(call.client, ProvisioningPermission.MANAGE_PROJECTS, call.actor.id)
            ProvisioningService.check_project_prerequisites(session, project, call.project_name, block.cost_center)
        return project

    @classmethod
    def _commit_project(cls, call: _Call, block: ProvisioningProjectBlock, route: str) -> _CommittedProject:
        """Commit 1: the project, its cost center and admins, retried once when a concurrent write wins."""
        try:
            return cls._commit_project_once(call, block, route)
        except IntegrityError:
            logger.warning(
                f"provisioning_sync_conflict_retry: {call.log_context}, project={call.project_name!r}, "
                f"domain=provisioning"
            )
            return cls._commit_project_once(call, block, route)

    @staticmethod
    def _commit_project_once(call: _Call, block: ProvisioningProjectBlock, route: str) -> _CommittedProject:
        with get_session() as session:
            try:
                resolved = ProvisioningService.resolve_project(
                    session, call.actor, call.project_name, block.cost_center
                )
                if resolved.created_project:
                    # The project can vanish after the check phase saw it; nothing is committed yet, so 403 holds.
                    require_permission(call.client, ProvisioningPermission.MANAGE_PROJECTS, call.actor.id)
                admins = ProvisioningService.assign_admins(
                    session, call.actor, resolved.project, block.admin_emails, route
                )
                committed = _CommittedProject(
                    project_name=resolved.project.name,
                    cost_center=resolved.cost_center,
                    created_project=resolved.created_project,
                    created_cost_center=resolved.created_cost_center,
                    admins=admins,
                )
                session.commit()
            except IntegrityError:
                session.rollback()
                raise

        for user_id in admins.changed_user_ids:
            invalidate_user_from_cache(user_id)

        logger.info(
            f"provisioning_project_resolved: {call.log_context}, project={committed.project_name!r}, "
            f"cost_center={committed.cost_center!r}, created_project={committed.created_project}, "
            f"created_cost_center={committed.created_cost_center}, admins_assigned={len(admins.assigned)}, "
            f"admins_not_assigned={len(admins.not_assigned)}, domain=provisioning"
        )
        return committed

    @classmethod
    async def _commit_budget_after_project(
        cls,
        call: _Call,
        project_name: str,
        amounts: dict[BudgetCategory, float],
        compute_amounts: _AmountsResolver,
    ) -> tuple[ProjectBudgetGroupFullResult, bool]:
        # Commit 1 is stored by now: a 4xx would claim that nothing changed and an unexpected error would drop the
        # resend hint, so answer 500 and ask for a resend. An HTTP 5xx (the provider's 502) passes through.
        try:
            return await cls._commit_budget(call, project_name, amounts, compute_amounts)
        except Exception as e:
            if isinstance(e, ExtendedHTTPException) and not 400 <= e.code < 500:
                raise
            _log_partly_applied(call, project_name, _failure_reason(e))
            raise ExtendedHTTPException(code=500, message=PARTLY_APPLIED_MESSAGE) from e

    @classmethod
    async def _commit_budget(
        cls,
        call: _Call,
        project_name: str,
        amounts: dict[BudgetCategory, float],
        compute_amounts: _AmountsResolver,
    ) -> tuple[ProjectBudgetGroupFullResult, bool]:
        """Commit 2: the budget group, planned against the locked group's current amounts."""
        async with get_async_session() as session:
            group = await project_budget_group_repository.get_active_by_project(session, project_name, for_update=True)
            if group is None:
                group_id, created_budget = await cls._create_group(
                    session, call, project_name, amounts, compute_amounts
                )
            else:
                group_id, created_budget = group.id, False
                await cls._update_group(session, call.actor, group_id, amounts, compute_amounts)
            await session.commit()
            budget_group = await project_budget_service.get_project_budget_group(session, group_id)

        provider = get_active_provider()
        unsynced = [
            category.assignment.budget_category
            for category in budget_group.categories
            if not _is_synced(category, provider)
        ]
        if unsynced:
            _log_partly_applied(call, project_name, f"unsynced_categories={unsynced}")
            raise ExtendedHTTPException(code=502, message=PARTLY_APPLIED_MESSAGE)
        return budget_group, created_budget

    @classmethod
    async def _create_group(
        cls,
        session: AsyncSession,
        call: _Call,
        project_name: str,
        amounts: dict[BudgetCategory, float],
        compute_amounts: _AmountsResolver,
    ) -> tuple[str, bool]:
        total, specs = _plan_amounts(amounts, compute_amounts, {})
        data = _create_request(call, project_name, total, specs)
        try:
            created = await project_budget_service.create_project_budget_group(session, data, actor_id=call.actor.id)
        except (ExtendedHTTPException, IntegrityError) as conflict:
            if isinstance(conflict, ExtendedHTTPException) and conflict.code != 409:
                raise
            await session.rollback()
            group = await project_budget_group_repository.get_active_by_project(session, project_name, for_update=True)
            if group is None:
                raise
            await cls._update_group(session, call.actor, group.id, amounts, compute_amounts)
            return group.id, False
        return created.group.id, True

    @classmethod
    async def _update_group(
        cls,
        session: AsyncSession,
        actor: User,
        group_id: str,
        amounts: dict[BudgetCategory, float],
        compute_amounts: _AmountsResolver,
    ) -> None:
        current = await cls._read_budgets(session, group_id)
        total, specs = _plan_amounts(amounts, compute_amounts, current)
        await project_budget_service.update_project_budget_group(
            session, group_id=group_id, data=_update_request(total, specs), actor_id=actor.id
        )

    @classmethod
    async def _read_budgets(cls, session: AsyncSession, group_id: str) -> dict[BudgetCategory, Budget]:
        assignments = await project_budget_assignment_repository.get_active_by_group_id(session, group_id)
        budgets = await budget_repository.get_by_ids(session, [assignment.budget_id for assignment in assignments])
        return {
            BudgetCategory(assignment.budget_category): budgets[assignment.budget_id]
            for assignment in assignments
            if assignment.budget_id in budgets and budgets[assignment.budget_id].deleted_at is None
        }

    @staticmethod
    def _find_project_to_cancel(project_name: str) -> str:
        with get_session() as session:
            project = ProvisioningService.find_project(session, project_name)
            if project is None:
                raise ExtendedHTTPException(
                    code=404,
                    message=PROJECT_NOT_FOUND_MESSAGE.format(name=project_name),
                    help=CANCEL_PROJECT_NOT_FOUND_HELP,
                )
            return project.name

    @staticmethod
    async def _delete_group(actor: User, project_name: str) -> bool:
        async with get_async_session() as session:
            group = await project_budget_group_repository.get_active_by_project(session, project_name)
            if group is None:
                return False
            await project_budget_service.delete_project_budget_group(session, group_id=group.id, actor_id=actor.id)
            await session.commit()
        return True


provisioning_budget_service = ProvisioningBudgetService()
