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

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from codemie.configs import logger
from codemie.repository.application_repository import application_repository
from codemie.repository.budget_repository import budget_repository
from codemie.repository.project_enrichment_repository import project_enrichment_repository
from codemie.service.activity.activity_models import (
    ActivityDomain,
    ActivityEntityType,
    ActivityEventCreate,
    BudgetManagementEvent,
)
from codemie.service.activity.activity_repository import activity_event_repository
from codemie.service.budget.budget_resolution_service import clear_budget_resolution_cache


class ActiveProjectBudgetRestoreService:
    """Restores stopped budgets for projects re-flagged active in project_enrichment."""

    async def run(self, session: AsyncSession) -> None:
        restored_count = 0
        projects_to_notify: list[tuple[str, str | None]] = []

        # Direct-enrichment projects
        active_ids = await project_enrichment_repository.get_active_application_ids(session)
        for project_name in active_ids:
            count = await self._restore_project(session, project_name)
            if count:
                restored_count += count
                projects_to_notify.append((project_name, None))

        # Cost-center-enrichment projects
        active_cc_names = await project_enrichment_repository.get_active_cost_center_names(session)
        for cost_center_name in active_cc_names:
            project_names = await application_repository.aget_project_names_by_cost_center_name(
                session, cost_center_name
            )
            for project_name in project_names:
                count = await self._restore_project(session, project_name, cost_center_name=cost_center_name)
                if count:
                    restored_count += count
                    projects_to_notify.append((project_name, cost_center_name))

        if not projects_to_notify:
            logger.info("component=active_project_budget_restore event=no_stopped_budgets_found")
            return

        await session.commit()
        clear_budget_resolution_cache()

        for project_name, cc_name in projects_to_notify:
            await self._notify_project_admins(session, project_name, cost_center_name=cc_name)

        logger.info(
            f"component=active_project_budget_restore event=budgets_restored "
            f"direct_active_projects={len(active_ids)} active_cost_centers={len(active_cc_names)} "
            f"restored_budgets={restored_count}"
        )

    async def _restore_project(
        self,
        session: AsyncSession,
        project_name: str,
        *,
        cost_center_name: str | None = None,
    ) -> int:
        budgets = await budget_repository.list_stopped_project_budgets(session, project_name)
        if not budgets:
            return 0

        attributes = {"cost_center_name": cost_center_name} if cost_center_name else {}
        for budget in budgets:
            budget.is_active = True
            session.add(budget)
            await activity_event_repository.async_insert(
                ActivityEventCreate(
                    domain=ActivityDomain.BUDGET_MANAGEMENT,
                    event_type=BudgetManagementEvent.PROJECT_BUDGET_RESTORED,
                    entity_type=ActivityEntityType.BUDGET,
                    entity_id=budget.budget_id,
                    attributes=attributes if attributes else None,
                ),
                session,
            )
        return len(budgets)

    @staticmethod
    async def _notify_project_admins(
        session: AsyncSession,
        project_name: str,
        *,
        cost_center_name: str | None = None,
    ) -> None:
        from codemie.rest_api.models.user_management import UserDB, UserProject
        from codemie.service.email_service import email_service

        stmt = (
            select(UserDB.email)
            .join(UserProject, UserProject.user_id == UserDB.id)
            .where(
                UserProject.project_name == project_name,
                UserProject.is_project_admin.is_(True),
                UserDB.is_active.is_(True),
                UserDB.deleted_at.is_(None),
            )
        )
        result = await session.execute(stmt)
        admin_emails = result.scalars().all()

        if cost_center_name:
            reason = f"its cost center <strong>{cost_center_name}</strong> is now marked as active"
        else:
            reason = "the project is now marked as active"

        for email_addr in admin_emails:
            subject = f"Project budget restored: {project_name}"
            html_body = (
                f"<p>The budget for project <strong>{project_name}</strong> has been restored "
                f"because {reason}.</p>"
                f"<p>Resource spend will resume against the project budget.</p>"
            )
            try:
                await email_service.send_email(email_addr, subject, html_body)
            except Exception:
                logger.warning(
                    f"component=active_project_budget_restore event=admin_notify_failed "
                    f"project={project_name!r} email={email_addr!r}"
                )


active_project_budget_restore_service = ActiveProjectBudgetRestoreService()
