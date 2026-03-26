# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
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

from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import Session, func, select

from codemie.core.db_utils import escape_like_wildcards
from codemie.core.models import CostCenter


class CostCenterRepository:
    """Repository for cost center management."""

    def get_by_id(self, session: Session, cost_center_id: UUID) -> Optional[CostCenter]:
        statement = select(CostCenter).where(CostCenter.id == cost_center_id)
        return session.exec(statement).first()

    def get_active_by_id(self, session: Session, cost_center_id: UUID) -> Optional[CostCenter]:
        statement = select(CostCenter).where(CostCenter.id == cost_center_id, CostCenter.deleted_at.is_(None))
        return session.exec(statement).first()

    def get_by_name_case_insensitive(self, session: Session, name: str) -> Optional[CostCenter]:
        statement = select(CostCenter).where(func.lower(CostCenter.name) == name.lower())
        return session.exec(statement).first()

    def create(self, session: Session, *, name: str, description: str | None, created_by: str) -> CostCenter:
        now = datetime.now()
        cost_center = CostCenter(
            name=name,
            description=description,
            created_by=created_by,
            date=now,
            update_date=now,
        )
        session.add(cost_center)
        session.flush()
        session.refresh(cost_center)
        return cost_center

    def update(self, session: Session, cost_center: CostCenter, *, description: str | None) -> CostCenter:
        cost_center.description = description
        cost_center.update_date = datetime.now()
        session.add(cost_center)
        session.flush()
        session.refresh(cost_center)
        return cost_center

    def list_paginated(
        self,
        session: Session,
        *,
        search: str | None = None,
        page: int = 0,
        per_page: int = 20,
    ) -> tuple[list[CostCenter], int]:
        base_statement = select(CostCenter).where(CostCenter.deleted_at.is_(None))
        if search:
            escaped = escape_like_wildcards(search)
            base_statement = base_statement.where(CostCenter.name.ilike(f"%{escaped}%", escape="\\")).order_by(
                (CostCenter.name == search).desc()
            )

        total = int(session.exec(select(func.count()).select_from(base_statement.subquery())).one())
        data = list(
            session.exec(
                base_statement.order_by(CostCenter.date.desc(), CostCenter.name.asc())
                .offset(page * per_page)
                .limit(per_page)
            ).all()
        )
        return data, total

    def get_by_ids(self, session: Session, cost_center_ids: list[UUID]) -> dict[UUID, CostCenter]:
        if not cost_center_ids:
            return {}
        statement = select(CostCenter).where(CostCenter.id.in_(cost_center_ids))
        results = session.exec(statement).all()
        return {item.id: item for item in results}

    async def aget_by_ids(self, session: AsyncSession, cost_center_ids: list[UUID]) -> dict[UUID, CostCenter]:
        if not cost_center_ids:
            return {}
        statement = select(CostCenter).where(CostCenter.id.in_(cost_center_ids))
        result = await session.execute(statement)
        rows = result.scalars().all()
        return {item.id: item for item in rows}


cost_center_repository = CostCenterRepository()
