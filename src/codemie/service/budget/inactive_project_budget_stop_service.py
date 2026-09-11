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
from codemie.repository.project_enrichment_repository import project_enrichment_repository
from codemie.repository.budget_repository import budget_repository
from codemie.service.activity.activity_models import (
    ActivityDomain,
    ActivityEntityType,
    ActivityEventCreate,
    BudgetManagementEvent,
)
from codemie.service.activity.activity_repository import activity_event_repository
from codemie.service.budget.budget_resolution_service import clear_budget_resolution_cache


class InactiveProjectBudgetStopService:
    """Stops budgets for projects flagged inactive in project_enrichment."""

    async def run(self, session: AsyncSession) -> None:
        inactive_ids = await project_enrichment_repository.get_inactive_application_ids(session)
        if not inactive_ids:
            logger.info("component=inactive_project_budget_stop event=no_inactive_projects")
            return

        stopped_count = 0
        projects_to_notify: list[str] = []
        for project_name in inactive_ids:
            budgets = await budget_repository.list_active_project_budgets(session, project_name)
            if not budgets:
                continue

            for budget in budgets:
                budget.is_active = False
                session.add(budget)
                await activity_event_repository.async_insert(
                    ActivityEventCreate(
                        domain=ActivityDomain.BUDGET_MANAGEMENT,
                        event_type=BudgetManagementEvent.PROJECT_BUDGET_STOPPED,
                        entity_type=ActivityEntityType.BUDGET,
                        entity_id=budget.budget_id,
                    ),
                    session,
                )
                stopped_count += 1

            projects_to_notify.append(project_name)

        if not projects_to_notify:
            logger.info("component=inactive_project_budget_stop event=no_active_budgets_found")
            return

        await session.commit()
        clear_budget_resolution_cache()

        for project_name in projects_to_notify:
            await self._notify_project_admins(session, project_name)

        logger.info(
            f"component=inactive_project_budget_stop event=budgets_stopped "
            f"inactive_projects={len(inactive_ids)} stopped_budgets={stopped_count}"
        )

    @staticmethod
    async def _notify_project_admins(session: AsyncSession, project_name: str) -> None:
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

        for email_addr in admin_emails:
            subject = f"Project budget stopped: {project_name}"
            html_body = (
                f"<p>The budget for project <strong>{project_name}</strong> has been stopped "
                f"because the project is marked as inactive.</p>"
                f"<p>All resource spend will now be redirected to personal budgets until "
                f"the project is reactivated.</p>"
            )
            try:
                await email_service.send_email(email_addr, subject, html_body)
            except Exception:
                logger.warning(
                    f"component=inactive_project_budget_stop event=admin_notify_failed "
                    f"project={project_name!r} email={email_addr!r}"
                )


inactive_project_budget_stop_service = InactiveProjectBudgetStopService()
