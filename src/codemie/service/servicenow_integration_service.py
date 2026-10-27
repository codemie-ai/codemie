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
import math
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import Session

from codemie.clients.postgres import get_async_session, get_session
from codemie.configs.logger import logger
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import Application
from codemie.repository.application_repository import application_repository
from codemie.repository.budget_repository import budget_repository
from codemie.repository.cost_center_repository import cost_center_repository
from codemie.repository.project_budget_repository import (
    project_budget_assignment_repository,
    project_budget_group_repository,
)
from codemie.repository.user_project_repository import user_project_repository
from codemie.repository.user_repository import user_repository
from codemie.rest_api.models.project_budget import (
    CategoryBudgetSpec,
    CategoryBudgetSpecUpdate,
    ProjectBudgetGroupCreateRequest,
    ProjectBudgetGroupUpdateRequest,
)
from codemie.rest_api.models.servicenow_integration import ServiceNowBudgetAction, ServiceNowBudgetRequest
from codemie.rest_api.security.user import User
from codemie.rest_api.security.user_type_validator import is_service_account
from codemie.service.budget.budget_enums import BudgetCategory
from codemie.service.budget.budget_models import Budget
from codemie.service.budget.project_budget_service import ProjectBudgetGroupFullResult, project_budget_service
from codemie.service.cost_center_service import CostCenterService, cost_center_service
from codemie.service.project.project_assignment_service import ProjectAssignmentService
from codemie.service.project.project_service import ProjectService, project_service
from codemie.service.user.authentication_service import invalidate_user_from_cache

PLATFORM_BUDGET_MUST_BE_POSITIVE = "Platform budget must be greater than 0"
PROJECT_DELETED_MESSAGE = "Project '{name}' has been deleted"
PROJECT_DELETED_HELP = "Restore the project or use a different CodeMie project name."
PERSONAL_PROJECT_BUDGET_FORBIDDEN_MESSAGE = "Budgets cannot be managed for personal projects"
PERSONAL_PROJECT_BUDGET_FORBIDDEN_HELP = "Use a shared project."
PROJECT_NOT_FOUND_MESSAGE = "Project '{name}' does not exist"
CANCEL_PROJECT_NOT_FOUND_HELP = "Check the CodeMie project name; there is no budget to cancel."
COST_CENTER_DELETED_MESSAGE = "Cost center '{name}' has been deleted"
COST_CENTER_DELETED_HELP = "Restore the cost center or use a different EPAM project code."
ASSIGN_ADMINS_ACTION = "POST /v1/integrations/servicenow/budgets"
DEFAULT_BUDGET_DURATION = "30d"
GROUP_NAME_MAX_LENGTH = 100
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


def _derived_amount(total: float, pct: float) -> float:
    return float(Decimal(str(total)) * Decimal(str(pct)) / Decimal("100"))


def _exact_pct(total: float, amount: float) -> float:
    if amount == 0:
        return 0.0
    pct = float(Decimal(str(amount)) * 100 / Decimal(str(total)))
    best, best_error = pct, math.inf
    # The budget service derives the amount back through Decimal(str(pct)), which can land one float
    # step off; walk neighbouring floats until the derivation matches exactly.
    for _ in range(_PCT_SEARCH_STEPS + 1):
        derived = _derived_amount(total, pct)
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


def _preserved_soft_budget(existing: Budget | None, amount: float) -> float | None:
    if existing is not None and abs(existing.max_budget - amount) < _AMOUNT_TOLERANCE:
        return None if existing.soft_budget is None else min(existing.soft_budget, amount)
    return None


@dataclass
class ServiceNowBudgetResult:
    project_name: str
    cost_center: str | None
    created_project: bool
    created_cost_center: bool
    budget_group: ProjectBudgetGroupFullResult | None
    admins_assigned: list[str]
    admins_pending: list[str]


@dataclass
class _UpsertPlan:
    project: Application | None
    project_name: str
    group_id: str | None
    total: float
    specs: dict[str, CategoryBudgetSpec]
    requested: dict[BudgetCategory, float]
    compute_amounts: _AmountsResolver


class ServiceNowIntegrationService:
    @classmethod
    async def apply_budget_request(cls, actor: User, request: ServiceNowBudgetRequest) -> ServiceNowBudgetResult:
        """Check, then apply the request's budget action. Caller must authorize."""
        logger.info(
            f"servicenow_budget_request_started: request_id={request.request_id!r}, action={request.action.value}, "
            f"project={request.codemie_project_name!r}, epam_project_code={request.epam_project_code!r}, "
            f"admin_emails={len(request.admin_emails)}, actor_user_id={actor.id}, domain=servicenow_integration"
        )
        handlers = {
            ServiceNowBudgetAction.SET: cls._handle_set,
            ServiceNowBudgetAction.ADJUST: cls._handle_adjust,
            ServiceNowBudgetAction.CANCEL: cls._handle_cancel,
        }
        try:
            result = await handlers[request.action](actor, request)
        except Exception as e:
            logger.error(
                f"servicenow_budget_request_failed: request_id={request.request_id!r}, "
                f"action={request.action.value}, {_failure_reason(e)}, domain=servicenow_integration"
            )
            raise

        logger.info(
            f"servicenow_budget_request_completed: request_id={request.request_id!r}, "
            f"action={request.action.value}, project={result.project_name!r}, "
            f"cost_center={result.cost_center!r}, created_project={result.created_project}, "
            f"created_cost_center={result.created_cost_center}, "
            f"admins_assigned={len(result.admins_assigned)}, admins_pending={len(result.admins_pending)}, "
            f"actor_user_id={actor.id}, domain=servicenow_integration"
        )
        return result

    @classmethod
    async def _handle_set(cls, actor: User, request: ServiceNowBudgetRequest) -> ServiceNowBudgetResult:
        return await cls._upsert(actor, request, lambda requested, _current: replace_amounts(requested))

    @classmethod
    async def _handle_adjust(cls, actor: User, request: ServiceNowBudgetRequest) -> ServiceNowBudgetResult:
        return await cls._upsert(actor, request, merge_amounts)

    @classmethod
    async def _handle_cancel(cls, actor: User, request: ServiceNowBudgetRequest) -> ServiceNowBudgetResult:
        project_name, cost_center = await asyncio.to_thread(cls._find_project_to_cancel, request.codemie_project_name)
        async with get_async_session() as session:
            group = await project_budget_group_repository.get_active_by_project(session, project_name)
            if group is not None:
                await cls._delete_group(session, actor, group.id)
        return ServiceNowBudgetResult(
            project_name=project_name,
            cost_center=cost_center,
            created_project=False,
            created_cost_center=False,
            budget_group=None,
            admins_assigned=[],
            admins_pending=[],
        )

    @classmethod
    async def _upsert(
        cls, actor: User, request: ServiceNowBudgetRequest, compute_amounts: _AmountsResolver
    ) -> ServiceNowBudgetResult:
        plan = await cls._check_upsert(request, compute_amounts)
        resolved = await asyncio.to_thread(
            cls._resolve_and_assign,
            actor,
            project=plan.project,
            project_name=request.codemie_project_name,
            epam_project_code=request.epam_project_code,
            admin_emails=request.admin_emails,
        )
        plan.project_name = resolved.project_name
        resolved.budget_group = await cls._write_group(actor, plan, request.request_id)
        return resolved

    @classmethod
    async def _check_upsert(cls, request: ServiceNowBudgetRequest, compute_amounts: _AmountsResolver) -> _UpsertPlan:
        project = await asyncio.to_thread(
            cls._find_project_to_upsert, request.codemie_project_name, request.epam_project_code
        )
        project_name = request.codemie_project_name if project is None else project.name
        group_id, current = (None, {}) if project is None else await cls._read_group(project_name)
        total, specs = _plan_amounts(request.amounts, compute_amounts, current)
        return _UpsertPlan(
            project=project,
            project_name=project_name,
            group_id=group_id,
            total=total,
            specs=specs,
            requested=request.amounts,
            compute_amounts=compute_amounts,
        )

    @classmethod
    def _find_project_to_upsert(cls, project_name: str, epam_project_code: str) -> Application | None:
        with get_session() as session:
            project = cls._find_project(session, project_name)
            if project is None:
                cls._validate_names(project_name, epam_project_code)
                if project_name != epam_project_code:
                    cls._check_cost_center(session, epam_project_code)
        return project

    @staticmethod
    def _check_cost_center(session: Session, epam_project_code: str) -> None:
        CostCenterService.ensure_feature_enabled()
        existing = cost_center_repository.get_by_name_case_insensitive(session, epam_project_code)
        if existing is not None and existing.deleted_at is not None:
            raise ExtendedHTTPException(
                code=409,
                message=COST_CENTER_DELETED_MESSAGE.format(name=existing.name),
                help=COST_CENTER_DELETED_HELP,
            )

    @classmethod
    def _find_project_to_cancel(cls, project_name: str) -> tuple[str, str | None]:
        with get_session() as session:
            project = cls._find_project(session, project_name)
            if project is None:
                raise ExtendedHTTPException(
                    code=404,
                    message=PROJECT_NOT_FOUND_MESSAGE.format(name=project_name),
                    help=CANCEL_PROJECT_NOT_FOUND_HELP,
                )
            return project.name, cls._cost_center_name(session, project)

    @classmethod
    def _find_project(cls, session: Session, project_name: str) -> Application | None:
        project = application_repository.get_by_name_case_insensitive(session, project_name)
        if project is None:
            return None
        if project.deleted_at is not None:
            raise ExtendedHTTPException(
                code=409, message=PROJECT_DELETED_MESSAGE.format(name=project_name), help=PROJECT_DELETED_HELP
            )
        if project.project_type == Application.ProjectType.PERSONAL:
            raise ExtendedHTTPException(
                code=403,
                message=PERSONAL_PROJECT_BUDGET_FORBIDDEN_MESSAGE,
                help=PERSONAL_PROJECT_BUDGET_FORBIDDEN_HELP,
            )
        return project

    @classmethod
    async def _read_group(cls, project_name: str) -> tuple[str | None, dict[BudgetCategory, Budget]]:
        async with get_async_session() as session:
            group = await project_budget_group_repository.get_active_by_project(session, project_name)
            if group is None:
                return None, {}
            return group.id, await cls._read_budgets(session, group.id)

    @classmethod
    async def _read_budgets(cls, session: AsyncSession, group_id: str) -> dict[BudgetCategory, Budget]:
        assignments = await project_budget_assignment_repository.get_active_by_group_id(session, group_id)
        budgets = await budget_repository.get_by_ids(session, [assignment.budget_id for assignment in assignments])
        return {
            BudgetCategory(assignment.budget_category): budgets[assignment.budget_id]
            for assignment in assignments
            if assignment.budget_id in budgets and budgets[assignment.budget_id].deleted_at is None
        }

    @classmethod
    async def _write_group(cls, actor: User, plan: _UpsertPlan, request_id: str) -> ProjectBudgetGroupFullResult:
        async with get_async_session() as session:
            if plan.group_id is None:
                group_id = await cls._create_group(session, actor, plan, request_id)
            else:
                group_id = await cls._update_group(session, actor, plan.group_id, plan)
            await session.commit()
            return await project_budget_service.get_project_budget_group(session, group_id)

    @classmethod
    async def _create_group(cls, session: AsyncSession, actor: User, plan: _UpsertPlan, request_id: str) -> str:
        data = ProjectBudgetGroupCreateRequest(
            project_name=plan.project_name,
            name=f"ServiceNow {request_id}"[:GROUP_NAME_MAX_LENGTH],
            total_amount=plan.total,
            budget_duration=DEFAULT_BUDGET_DURATION,
            categories=plan.specs,
        )
        try:
            created = await project_budget_service.create_project_budget_group(session, data, actor_id=actor.id)
        except (ExtendedHTTPException, IntegrityError) as conflict:
            if isinstance(conflict, ExtendedHTTPException) and conflict.code != 409:
                raise
            await session.rollback()
            group = await project_budget_group_repository.get_active_by_project(session, plan.project_name)
            if group is None:
                raise
            current = await cls._read_budgets(session, group.id)
            plan.total, plan.specs = _plan_amounts(plan.requested, plan.compute_amounts, current)
            return await cls._update_group(session, actor, group.id, plan)
        return created.group.id

    @classmethod
    async def _update_group(cls, session: AsyncSession, actor: User, group_id: str, plan: _UpsertPlan) -> str:
        data = ProjectBudgetGroupUpdateRequest(
            total_amount=plan.total,
            categories={
                category: CategoryBudgetSpecUpdate(pct=spec.pct, soft_budget=spec.soft_budget)
                for category, spec in plan.specs.items()
            },
        )
        await project_budget_service.update_project_budget_group(
            session, group_id=group_id, data=data, actor_id=actor.id
        )
        return group_id

    @classmethod
    async def _delete_group(cls, session: AsyncSession, actor: User, group_id: str) -> None:
        await project_budget_service.delete_project_budget_group(session, group_id=group_id, actor_id=actor.id)
        await session.commit()

    @classmethod
    async def list_projects(cls, emails: list[str]) -> list[Application]:
        """List shared projects of the users with the given emails. Caller must authorize."""
        return await asyncio.to_thread(cls._list_projects_sync, emails)

    @classmethod
    def _list_projects_sync(cls, emails: list[str]) -> list[Application]:
        with get_session() as session:
            users = user_repository.get_by_emails(session, emails, active_only=True)
            user_ids = [user.id for user in users.values() if not is_service_account(user)]
            if not user_ids:
                return []
            return application_repository.list_shared_projects_for_users(session, user_ids)

    @classmethod
    def _resolve_and_assign(
        cls,
        actor: User,
        *,
        project: Application | None,
        project_name: str,
        epam_project_code: str,
        admin_emails: list[str],
    ) -> ServiceNowBudgetResult:
        sync_args = {
            "project": project,
            "project_name": project_name,
            "epam_project_code": epam_project_code,
            "admin_emails": admin_emails,
        }
        try:
            return cls._sync_once(actor, **sync_args)
        except IntegrityError:
            logger.warning(
                f"servicenow_sync_conflict_retry: project_name={project_name!r}, domain=servicenow_integration"
            )
            return cls._sync_once(actor, **sync_args)

    @classmethod
    def _sync_once(
        cls,
        actor: User,
        *,
        project: Application | None,
        project_name: str,
        epam_project_code: str,
        admin_emails: list[str],
    ) -> ServiceNowBudgetResult:
        with get_session() as session:
            try:
                project, cost_center, created_project, created_cost_center = cls._resolve_project(
                    session, actor, project=project, project_name=project_name, epam_project_code=epam_project_code
                )
                assigned, pending, changed_user_ids = cls._assign_admins(session, actor, project, admin_emails)
                stored_name = project.name
                session.commit()
            except IntegrityError:
                session.rollback()
                raise

        for user_id in changed_user_ids:
            invalidate_user_from_cache(user_id)

        logger.info(
            f"servicenow_project_resolved: project_name={stored_name!r}, cost_center={cost_center!r}, "
            f"created_project={created_project}, created_cost_center={created_cost_center}, "
            f"admins_assigned={len(assigned)}, admins_pending={len(pending)}, domain=servicenow_integration"
        )
        return ServiceNowBudgetResult(
            project_name=stored_name,
            cost_center=cost_center,
            created_project=created_project,
            created_cost_center=created_cost_center,
            budget_group=None,
            admins_assigned=assigned,
            admins_pending=pending,
        )

    @classmethod
    def _resolve_project(
        cls,
        session: Session,
        actor: User,
        *,
        project: Application | None,
        project_name: str,
        epam_project_code: str,
    ) -> tuple[Application, str | None, bool, bool]:
        if project is None:
            project = cls._find_project(session, project_name)
        if project is not None:
            return project, cls._cost_center_name(session, project), False, False

        cost_center = None
        created_cost_center = False
        if project_name != epam_project_code:
            cost_center, created_cost_center = cost_center_service.get_or_create(
                session, name=epam_project_code, created_by=actor.id
            )
        project = project_service.create_shared_project_in_session(
            session,
            actor=actor,
            name=project_name,
            cost_center_id=cost_center.id if cost_center else None,
        )
        return project, cost_center.name if cost_center else None, True, created_cost_center

    @staticmethod
    def _validate_names(project_name: str, epam_project_code: str) -> None:
        ProjectService.validate_shared_project_name(project_name)
        if project_name != epam_project_code:
            CostCenterService.validate_name(epam_project_code)

    @classmethod
    def _cost_center_name(cls, session: Session, project: Application) -> str | None:
        if project.cost_center_id is None:
            return None
        cost_center = cost_center_repository.get_active_by_id(session, project.cost_center_id)
        return cost_center.name if cost_center else None

    @classmethod
    def _assign_admins(
        cls, session: Session, actor: User, project: Application, emails: list[str]
    ) -> tuple[list[str], list[str], list[str]]:
        if not emails:
            return [], [], []

        users_by_email = user_repository.get_by_emails(session, emails, active_only=True)
        assigned: list[str] = []
        pending: list[str] = []
        resolved_ids: list[str] = []
        for email in emails:
            user = users_by_email.get(email.lower())
            if user is None or is_service_account(user):
                pending.append(email)
            else:
                assigned.append(email)
                resolved_ids.append(user.id)

        memberships = user_project_repository.get_by_users_and_project(session, resolved_ids, project.name)
        changed_user_ids = [
            user_id
            for user_id in resolved_ids
            if user_id not in memberships or not memberships[user_id].is_project_admin
        ]
        if changed_user_ids:
            ProjectAssignmentService.bulk_assign_users_to_project(
                session,
                project,
                [{"user_id": user_id, "is_project_admin": True} for user_id in changed_user_ids],
                project.name,
                actor,
                ASSIGN_ADMINS_ACTION,
            )
        return assigned, pending, changed_user_ids


servicenow_integration_service = ServiceNowIntegrationService()
