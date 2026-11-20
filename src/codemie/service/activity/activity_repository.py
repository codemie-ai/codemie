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
is committed in the same transaction as the business mutation. They swallow a
failure of the event row only; a failure of the caller's own pending writes propagates.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import asc, delete, desc, func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import Session, select

from codemie.configs import config, logger
from codemie.core.models import Application
from codemie.rest_api.models.user_management import UserDB
from codemie.service.activity.activity_models import SYSTEM_ACTOR_ID, ActivityEvent, ActivityEventCreate
from codemie.service.activity.activity_taxonomy import CATEGORY_EVENT_TYPES


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
    project_name: str | None = None
    entity_label: str | None = None


class ActivityEventRepository(ABC):
    """Abstract interface for activity event persistence."""

    @abstractmethod
    def insert(self, event: ActivityEventCreate, session: Session) -> ActivityEvent | None:
        """Persist a new event within the caller's sync transaction."""

    @abstractmethod
    async def async_insert(self, event: ActivityEventCreate, session: AsyncSession) -> ActivityEvent | None:
        """Persist a new event within the caller's async transaction."""

    @abstractmethod
    async def async_insert_strict(self, event: ActivityEventCreate, session: AsyncSession) -> ActivityEvent | None:
        """Async twin of insert_strict(); unlike async_insert(), propagates failures."""

    @abstractmethod
    def insert_many_strict(self, events: list[ActivityEventCreate], session: Session) -> list[ActivityEvent]:
        """Persist several events in the caller's transaction with a single flush; propagates failures."""

    @abstractmethod
    async def async_insert_many_strict(
        self, events: list[ActivityEventCreate], session: AsyncSession
    ) -> list[ActivityEvent]:
        """Async twin of insert_many_strict()."""

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
    def insert_strict(self, event: ActivityEventCreate, session: Session) -> ActivityEvent | None:
        """Persist an event in the caller's transaction; unlike insert(), propagates failures."""

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
    def find_by_project_instance_id(  # noqa: PLR0913 - flat filters mirror the query params
        self,
        project_instance_id: UUID,
        *,
        domain: list[str] | None = None,
        event_type: list[str] | None = None,
        category: list[str] | None = None,
        actor_id: str | None = None,
        from_dt: datetime | None = None,
        to_dt: datetime | None = None,
        sort_dir: str = "desc",
        limit: int,
        offset: int,
        session: Session,
    ) -> tuple[list[ActivityEventRow], int]:
        """Return enriched events for one project generation plus total count."""

    @abstractmethod
    def has_project_instance_events(self, project_instance_id: UUID, session: Session) -> bool:
        """Return whether retained events are attributed to a project generation."""

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
    def get_domain_mapping(self, session: Session) -> dict:
        """Return the domain-to-event-type and entity-type mapping used by the filter options."""

    @abstractmethod
    def get_distinct_event_types_by_project_instance_id(self, project_instance_id: UUID, session: Session) -> list[str]:
        """Return sorted distinct event_type values recorded for a project generation."""

    @abstractmethod
    def get_distinct_domains_by_project_instance_id(self, project_instance_id: UUID, session: Session) -> list[str]:
        """Return sorted distinct domain values recorded for a project generation."""

    @abstractmethod
    def delete_older_than(self, cutoff: datetime, session: Session) -> int:
        """Delete events created before cutoff. Returns the count of deleted rows."""


class SQLActivityEventRepository(ActivityEventRepository):
    """Postgres-backed implementation."""

    @staticmethod
    def _event_values(events: list[ActivityEventCreate], project_ids: dict[str, UUID]) -> list[dict[str, Any]]:
        values = []
        for event in events:
            data = event.model_dump()
            if data["project_instance_id"] is None and event.project_name is not None:
                data["project_instance_id"] = project_ids.get(event.project_name.lower())
            values.append(data)
        return values

    @staticmethod
    def _project_ids_for_events(events: list[ActivityEventCreate], session: Session) -> dict[str, UUID]:
        names = {
            event.project_name.lower() for event in events if event.project_instance_id is None and event.project_name
        }
        if not names:
            return {}
        rows = session.exec(
            select(func.lower(Application.name), Application.project_instance_id).where(
                func.lower(Application.name).in_(names),
                Application.deleted_at.is_(None),
            )
        ).all()
        return dict(rows)

    @staticmethod
    async def _async_project_ids_for_events(
        events: list[ActivityEventCreate], session: AsyncSession
    ) -> dict[str, UUID]:
        names = {
            event.project_name.lower() for event in events if event.project_instance_id is None and event.project_name
        }
        if not names:
            return {}
        result = await session.execute(
            select(func.lower(Application.name), Application.project_instance_id).where(
                func.lower(Application.name).in_(names),
                Application.deleted_at.is_(None),
            )
        )
        return dict(result.all())

    def insert(self, event: ActivityEventCreate, session: Session) -> ActivityEvent | None:
        if not config.ACTIVITY_EVENTS_ENABLED:
            return None
        # begin_nested() flushes the caller's pending writes first; flushing them here, outside the
        # catch-all, lets their failure reach the caller instead of poisoning its transaction silently.
        session.flush()
        try:
            with session.begin_nested():
                project_ids = self._project_ids_for_events([event], session)
                row = ActivityEvent(**self._event_values([event], project_ids)[0])
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

    def insert_strict(self, event: ActivityEventCreate, session: Session) -> ActivityEvent | None:
        if not config.ACTIVITY_EVENTS_ENABLED:
            return None
        project_ids = self._project_ids_for_events([event], session)
        row = ActivityEvent(**self._event_values([event], project_ids)[0])
        session.add(row)
        session.flush()
        return row

    async def async_insert(self, event: ActivityEventCreate, session: AsyncSession) -> ActivityEvent | None:
        """Persist a new event within the caller's async transaction."""
        if not config.ACTIVITY_EVENTS_ENABLED:
            return None
        # Same as insert(): the caller's pending writes fail into the caller, not the catch-all below.
        await session.flush()
        try:
            async with session.begin_nested():
                project_ids = await self._async_project_ids_for_events([event], session)
                row = ActivityEvent(**self._event_values([event], project_ids)[0])
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

    async def async_insert_strict(self, event: ActivityEventCreate, session: AsyncSession) -> ActivityEvent | None:
        if not config.ACTIVITY_EVENTS_ENABLED:
            return None
        project_ids = await self._async_project_ids_for_events([event], session)
        row = ActivityEvent(**self._event_values([event], project_ids)[0])
        session.add(row)
        await session.flush()
        return row

    def insert_many_strict(self, events: list[ActivityEventCreate], session: Session) -> list[ActivityEvent]:
        if not config.ACTIVITY_EVENTS_ENABLED or not events:
            return []
        project_ids = self._project_ids_for_events(events, session)
        rows = [ActivityEvent(**values) for values in self._event_values(events, project_ids)]
        session.add_all(rows)
        session.flush()
        return rows

    async def async_insert_many_strict(
        self, events: list[ActivityEventCreate], session: AsyncSession
    ) -> list[ActivityEvent]:
        if not config.ACTIVITY_EVENTS_ENABLED or not events:
            return []
        project_ids = await self._async_project_ids_for_events(events, session)
        rows = [ActivityEvent(**values) for values in self._event_values(events, project_ids)]
        session.add_all(rows)
        await session.flush()
        return rows

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
        actor_id: list[str] | None = None,
        include_system: bool = False,
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
        actor_conditions = []
        if actor_id:
            actor_conditions.append(ActivityEvent.actor_id.in_(actor_id))
        if include_system:
            actor_conditions.append(ActivityEvent.actor_id.is_(None))
            actor_conditions.append(ActivityEvent.actor_id == SYSTEM_ACTOR_ID)
        if actor_conditions:
            conditions.append(or_(*actor_conditions))
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
            # Live user only, no snapshot fallback: this endpoint must keep returning what it returned before the
            # snapshot columns existed. A deleted actor gets null name/email here, while the project trail shows the
            # snapshot. Do not coalesce with ActivityEvent.actor_email / actor_display_name.
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
                entity_label=event.entity_label,
                project_name=event.project_name,
                actor_id=event.actor_id,
                actor_email=actor_email,
                actor_name=actor_name,
                attributes=event.attributes,
                created_at=event.created_at,
            )
            for event, actor_email, actor_name in rows
        ]
        return result, total

    def find_by_project_instance_id(  # noqa: PLR0913 - flat filters mirror the query params
        self,
        project_instance_id: UUID,
        *,
        domain: list[str] | None = None,
        event_type: list[str] | None = None,
        category: list[str] | None = None,
        actor_id: str | None = None,
        from_dt: datetime | None = None,
        to_dt: datetime | None = None,
        sort_dir: str = "desc",
        limit: int,
        offset: int,
        session: Session,
    ) -> tuple[list[ActivityEventRow], int]:
        if not config.ACTIVITY_EVENTS_ENABLED:
            return [], 0

        conditions = [ActivityEvent.project_instance_id == project_instance_id]
        if domain:
            conditions.append(ActivityEvent.domain.in_(domain))
        if event_type:
            conditions.append(ActivityEvent.event_type.in_(event_type))
        if category:
            unknown = sorted(set(category) - CATEGORY_EVENT_TYPES.keys())
            if unknown:
                raise ValueError(f"Unknown activity event categories: {', '.join(unknown)}")
            resolved_event_types: set[str] = set().union(*(CATEGORY_EVENT_TYPES[c] for c in category))
            conditions.append(ActivityEvent.event_type.in_(resolved_event_types))
        if actor_id is not None:
            conditions.append(ActivityEvent.actor_id == actor_id)
        if from_dt is not None:
            conditions.append(ActivityEvent.created_at >= from_dt)
        if to_dt is not None:
            conditions.append(ActivityEvent.created_at <= to_dt)

        count_stmt = select(func.count(ActivityEvent.id))
        for c in conditions:
            count_stmt = count_stmt.where(c)
        total: int = session.execute(count_stmt).scalar_one()

        if sort_dir == "desc":
            order = (desc(ActivityEvent.created_at), desc(ActivityEvent.id))
        else:
            order = (asc(ActivityEvent.created_at), asc(ActivityEvent.id))

        # Live user first, so a renamed user shows the current name, as in the global list. The write-time snapshot
        # only fills in when the user no longer exists (the join finds nothing).
        data_stmt = select(
            ActivityEvent,
            func.coalesce(UserDB.email, ActivityEvent.actor_email).label("actor_email"),
            func.coalesce(UserDB.name, ActivityEvent.actor_display_name).label("actor_name"),
        ).outerjoin(UserDB, ActivityEvent.actor_id == UserDB.id)
        data_stmt = data_stmt.where(*conditions).order_by(*order).offset(offset).limit(limit)

        rows = session.execute(data_stmt).all()
        result = [
            ActivityEventRow(
                id=event.id,
                domain=event.domain,
                event_type=event.event_type,
                entity_type=event.entity_type,
                entity_id=event.entity_id,
                entity_label=event.entity_label,
                project_name=event.project_name,
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

    def get_distinct_event_types_by_project_instance_id(self, project_instance_id: UUID, session: Session) -> list[str]:
        stmt = (
            select(ActivityEvent.event_type).distinct().where(ActivityEvent.project_instance_id == project_instance_id)
        )
        stmt = stmt.order_by(ActivityEvent.event_type)
        return [r for (r,) in session.execute(stmt).all()]

    def get_distinct_domains_by_project_instance_id(self, project_instance_id: UUID, session: Session) -> list[str]:
        stmt = select(ActivityEvent.domain).distinct().where(ActivityEvent.project_instance_id == project_instance_id)
        stmt = stmt.order_by(ActivityEvent.domain)
        return [r for (r,) in session.execute(stmt).all()]

    def has_project_instance_events(self, project_instance_id: UUID, session: Session) -> bool:
        return (
            session.exec(
                select(ActivityEvent.id).where(ActivityEvent.project_instance_id == project_instance_id).limit(1)
            ).first()
            is not None
        )

    def delete_older_than(self, cutoff: datetime, session: Session) -> int:
        stmt = delete(ActivityEvent).where(ActivityEvent.created_at < cutoff)
        result = session.execute(stmt)
        return result.rowcount


activity_event_repository: ActivityEventRepository = SQLActivityEventRepository()
