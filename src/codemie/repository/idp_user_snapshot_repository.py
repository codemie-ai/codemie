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

from datetime import UTC, datetime
from typing import Optional

from sqlalchemy import delete, func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import Session, select

from codemie.rest_api.models.user_management import IdpUserSnapshot


class IdpUserSnapshotRepository:
    """Last JWT claims per user in IDP-claims mode, for runs that start without a request."""

    def get(self, session: Session, user_id: str) -> Optional[IdpUserSnapshot]:
        return session.exec(select(IdpUserSnapshot).where(IdpUserSnapshot.id == user_id)).first()

    async def aget(self, session: AsyncSession, user_id: str) -> Optional[IdpUserSnapshot]:
        result = await session.execute(select(IdpUserSnapshot).where(IdpUserSnapshot.id == user_id))
        return result.scalars().first()

    async def aupsert(
        self,
        session: AsyncSession,
        *,
        user_id: str,
        username: str,
        email: str | None,
        name: str | None,
        project_names: list[str],
        admin_project_names: list[str],
    ) -> None:
        """INSERT ON CONFLICT (id) DO UPDATE; the caller commits."""
        now = datetime.now(UTC).replace(tzinfo=None)
        values = {
            "username": username,
            "email": email,
            "name": name,
            "project_names": project_names,
            "admin_project_names": admin_project_names,
            "update_date": now,
        }
        stmt = pg_insert(IdpUserSnapshot).values(id=user_id, date=now, **values)
        await session.execute(stmt.on_conflict_do_update(index_elements=["id"], set_=values))

    async def adelete_last_seen_before(self, session: AsyncSession, cutoff: datetime) -> int:
        """Delete snapshots whose last login is before ``cutoff``; returns the row count. The caller commits."""
        last_seen = func.coalesce(IdpUserSnapshot.update_date, IdpUserSnapshot.date)
        result = await session.execute(delete(IdpUserSnapshot).where(last_seen < cutoff))
        return result.rowcount or 0


idp_user_snapshot_repository = IdpUserSnapshotRepository()
