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

from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from codemie.core.models import CostCenter, ProjectEnrichment


class ProjectEnrichmentRepository:
    """Async read-only repository for project enrichment data."""

    async def get_inactive_application_ids(self, session: AsyncSession) -> list[str]:
        """Return application_ids where is_active is False."""
        stmt = (
            select(ProjectEnrichment.application_id)
            .where(ProjectEnrichment.application_id.is_not(None))
            .where(ProjectEnrichment.is_active.is_(False))
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    async def get_inactive_cost_center_names(self, session: AsyncSession) -> list[str]:
        """Return cost_center names (resolved via join) where is_active is False."""
        stmt = (
            select(CostCenter.name)
            .join(ProjectEnrichment, ProjectEnrichment.cost_center_id == CostCenter.id)
            .where(ProjectEnrichment.cost_center_id.is_not(None))
            .where(ProjectEnrichment.is_active.is_(False))
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    async def get_active_application_ids(self, session: AsyncSession) -> list[str]:
        """Return application_ids where is_active is True."""
        stmt = (
            select(ProjectEnrichment.application_id)
            .where(ProjectEnrichment.application_id.is_not(None))
            .where(ProjectEnrichment.is_active.is_(True))
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    async def get_active_cost_center_names(self, session: AsyncSession) -> list[str]:
        """Return cost_center names (resolved via join) where is_active is True."""
        stmt = (
            select(CostCenter.name)
            .join(ProjectEnrichment, ProjectEnrichment.cost_center_id == CostCenter.id)
            .where(ProjectEnrichment.cost_center_id.is_not(None))
            .where(ProjectEnrichment.is_active.is_(True))
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    async def get_is_active_for_project(self, session: AsyncSession, project_name: str) -> Optional[bool]:
        """Return is_active for a project, or None if no enrichment found.

        Checks direct application enrichment first; falls back to cost-center
        enrichment via the project's cost_center_id → cost_centers.id join.
        Returns None when neither record exists (no enrichment at all).
        """
        from codemie.core.models import Application

        direct_stmt = select(ProjectEnrichment.is_active).where(ProjectEnrichment.application_id == project_name)
        direct_result = await session.execute(direct_stmt)
        row = direct_result.scalars().first()
        if row is not None:
            return bool(row)

        cc_stmt = (
            select(ProjectEnrichment.is_active)
            .join(CostCenter, CostCenter.id == ProjectEnrichment.cost_center_id)
            .join(Application, Application.cost_center_id == CostCenter.id)
            .where(Application.name == project_name)
            .where(Application.deleted_at.is_(None))
            .where(ProjectEnrichment.cost_center_id.is_not(None))
        )
        cc_result = await session.execute(cc_stmt)
        cc_row = cc_result.scalars().first()
        if cc_row is not None:
            return bool(cc_row)

        return None


project_enrichment_repository = ProjectEnrichmentRepository()
