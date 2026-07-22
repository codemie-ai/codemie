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

"""Repository for activity events.

insert() and async_insert() accept a caller-provided session so the event row
is committed in the same transaction as the business mutation.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import asc, delete, desc, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import Session, select

from codemie.configs import config, logger
from codemie.rest_api.models.user_management import UserDB
from codemie.service.activity.activity_models import ActivityEvent, ActivityEventCreate


@dataclass
class ActivityEventRow:
    id: str
    domain: str
    event_type: str
    entity_type: str | None
    entity_id: str | None
    actor_id: str | None
    actor_email: str | None
    actor_name: str | None
    attributes: dict[str, Any] | None
    created_at: datetime


class ActivityEventRepository(ABC):
    """Abstract interface for activity event persistence."""

    @abstractmethod
    def insert(self, event: ActivityEventCreate, session: Session) -> ActivityEvent | None:
        """Persist a new event within the caller's sync transaction."""

    @abstractmethod
    async def async_insert(self, event: ActivityEventCreate, session: AsyncSession) -> ActivityEvent | None:
        """Persist a new event within the caller's async transaction."""

    @abstractmethod
    def find_by_entity(
        self,
        entity_type: str,
        entity_id: str,
        *,
        limit: int,
        offset: int,
        session: Session,
    ) -> list[ActivityEvent]:
        """Return events for a specific entity, most recent first."""

    @abstractmethod
    def find_by_actor(
        self,
        actor_id: str,
        *,
        limit: int,
        offset: int,
        session: Session,
    ) -> list[ActivityEvent]:
        """Return events performed by a specific actor, most recent first."""

    @abstractmethod
    def find_by_domain(
        self,
        domain: str,
        *,
        from_dt: datetime,
        to_dt: datetime,
        limit: int,
        offset: int,
        session: Session,
    ) -> list[ActivityEvent]:
        """Return events in a domain within a time range, most recent first."""

    @abstractmethod
    def find_all(
        self,
        *,
        actor_id: str | None = None,
        domain: list[str] | None = None,
        event_type: list[str] | None = None,
        entity_type: list[str] | None = None,
        entity_id: str | None = None,
        from_dt: datetime | None = None,
        to_dt: datetime | None = None,
        sort_dir: str = "desc",
        limit: int,
        offset: int,
        session: Session,
    ) -> tuple[list[ActivityEventRow], int]:
        """Return enriched events matching filters plus total count for pagination."""

    @abstractmethod
    def get_distinct_domains(self, session: Session) -> list[str]:
        """Return sorted non-null distinct domain values."""

    @abstractmethod
    def get_distinct_event_types(self, session: Session) -> list[str]:
        """Return sorted non-null distinct event_type values."""

    @abstractmethod
    def get_distinct_entity_types(self, session: Session) -> list[str]:
        """Return sorted non-null distinct entity_type values."""

    @abstractmethod
    def delete_older_than(self, cutoff: datetime, session: Session) -> int:
        """Delete events created before cutoff. Returns the count of deleted rows."""


class SQLActivityEventRepository(ActivityEventRepository):
    """Postgres-backed implementation."""

    def insert(self, event: ActivityEventCreate, session: Session) -> ActivityEvent | None:
        if not config.ACTIVITY_EVENTS_ENABLED:
            return None
        try:
            with session.begin_nested():
                row = ActivityEvent(**event.model_dump())
                session.add(row)
                session.flush()
            logger.debug(
                f"[activity_events] insert domain={row.domain!r} event_type={row.event_type!r} "
                f"entity_id={row.entity_id!r} actor_id={row.actor_id!r}"
            )
            return row
        except Exception as exc:
            logger.error(
                f"[activity_events] insert failed domain={event.domain!r} event_type={event.event_type!r}: {exc}"
            )
            return None

    async def async_insert(self, event: ActivityEventCreate, session: AsyncSession) -> ActivityEvent | None:
        """Persist a new event within the caller's async transaction."""
        if not config.ACTIVITY_EVENTS_ENABLED:
            return None
        try:
            async with session.begin_nested():
                row = ActivityEvent(**event.model_dump())
                session.add(row)
                await session.flush()
            logger.debug(
                f"[activity_events] async_insert domain={row.domain!r} event_type={row.event_type!r} "
                f"entity_id={row.entity_id!r} actor_id={row.actor_id!r}"
            )
            return row
        except Exception as exc:
            logger.error(
                f"[activity_events] async_insert failed domain={event.domain!r} event_type={event.event_type!r}: {exc}"
            )
            return None

    def find_by_entity(
        self,
        entity_type: str,
        entity_id: str,
        *,
        limit: int,
        offset: int,
        session: Session,
    ) -> list[ActivityEvent]:
        stmt = (
            select(ActivityEvent)
            .where(ActivityEvent.entity_type == entity_type)
            .where(ActivityEvent.entity_id == entity_id)
            .order_by(ActivityEvent.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(session.exec(stmt).all())

    def find_by_actor(
        self,
        actor_id: str,
        *,
        limit: int,
        offset: int,
        session: Session,
    ) -> list[ActivityEvent]:
        stmt = (
            select(ActivityEvent)
            .where(ActivityEvent.actor_id == actor_id)
            .order_by(ActivityEvent.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(session.exec(stmt).all())

    def find_by_domain(
        self,
        domain: str,
        *,
        from_dt: datetime,
        to_dt: datetime,
        limit: int,
        offset: int,
        session: Session,
    ) -> list[ActivityEvent]:
        stmt = (
            select(ActivityEvent)
            .where(ActivityEvent.domain == domain)
            .where(ActivityEvent.created_at >= from_dt)
            .where(ActivityEvent.created_at <= to_dt)
            .order_by(ActivityEvent.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(session.exec(stmt).all())

    def find_all(
        self,
        *,
        actor_id: str | None = None,
        domain: list[str] | None = None,
        event_type: list[str] | None = None,
        entity_type: list[str] | None = None,
        entity_id: str | None = None,
        from_dt: datetime | None = None,
        to_dt: datetime | None = None,
        sort_dir: str = "desc",
        limit: int,
        offset: int,
        session: Session,
    ) -> tuple[list[ActivityEventRow], int]:
        conditions = []
        if actor_id is not None:
            conditions.append(ActivityEvent.actor_id == actor_id)
        if domain:
            conditions.append(ActivityEvent.domain.in_(domain))
        if event_type:
            conditions.append(ActivityEvent.event_type.in_(event_type))
        if entity_type:
            conditions.append(ActivityEvent.entity_type.in_(entity_type))
        if entity_id is not None:
            conditions.append(ActivityEvent.entity_id == entity_id)
        if from_dt is not None:
            conditions.append(ActivityEvent.created_at >= from_dt)
        if to_dt is not None:
            conditions.append(ActivityEvent.created_at <= to_dt)

        count_stmt = select(func.count(ActivityEvent.id))
        for c in conditions:
            count_stmt = count_stmt.where(c)
        total: int = session.execute(count_stmt).scalar_one()

        order = desc(ActivityEvent.created_at) if sort_dir == "desc" else asc(ActivityEvent.created_at)
        data_stmt = select(
            ActivityEvent,
            UserDB.email.label("actor_email"),
            UserDB.name.label("actor_name"),
        ).outerjoin(UserDB, ActivityEvent.actor_id == UserDB.id)
        for c in conditions:
            data_stmt = data_stmt.where(c)
        data_stmt = data_stmt.order_by(order).offset(offset).limit(limit)

        rows = session.execute(data_stmt).all()
        result = [
            ActivityEventRow(
                id=event.id,
                domain=event.domain,
                event_type=event.event_type,
                entity_type=event.entity_type,
                entity_id=event.entity_id,
                actor_id=event.actor_id,
                actor_email=actor_email,
                actor_name=actor_name,
                attributes=event.attributes,
                created_at=event.created_at,
            )
            for event, actor_email, actor_name in rows
        ]
        return result, total

    def get_distinct_domains(self, session: Session) -> list[str]:
        stmt = select(ActivityEvent.domain).distinct().order_by(ActivityEvent.domain)
        return [r for (r,) in session.execute(stmt).all()]

    def get_distinct_event_types(self, session: Session) -> list[str]:
        stmt = select(ActivityEvent.event_type).distinct().order_by(ActivityEvent.event_type)
        return [r for (r,) in session.execute(stmt).all()]

    def get_distinct_entity_types(self, session: Session) -> list[str]:
        stmt = (
            select(ActivityEvent.entity_type)
            .distinct()
            .where(ActivityEvent.entity_type.is_not(None))
            .order_by(ActivityEvent.entity_type)
        )
        return [r for (r,) in session.execute(stmt).all()]

    def get_domain_mapping(self, session: Session) -> dict:
        from codemie.rest_api.models.activity_event import DomainFilterEntry

        stmt = (
            select(ActivityEvent.domain, ActivityEvent.event_type, ActivityEvent.entity_type)
            .distinct()
            .order_by(ActivityEvent.domain, ActivityEvent.event_type)
        )
        rows = session.execute(stmt).all()

        raw: dict[str, dict[str, set]] = {}
        for domain, event_type, entity_type in rows:
            if domain not in raw:
                raw[domain] = {'event_types': set(), 'entity_types': set()}
            raw[domain]['event_types'].add(event_type)
            if entity_type is not None:
                raw[domain]['entity_types'].add(entity_type)

        return {
            domain: DomainFilterEntry(
                event_types=sorted(data['event_types']),
                entity_types=sorted(data['entity_types']),
            )
            for domain, data in raw.items()
        }

    def delete_older_than(self, cutoff: datetime, session: Session) -> int:
        stmt = delete(ActivityEvent).where(ActivityEvent.created_at < cutoff)
        result = session.execute(stmt)
        return result.rowcount


activity_event_repository: ActivityEventRepository = SQLActivityEventRepository()
