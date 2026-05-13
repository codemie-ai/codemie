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

"""Repository for `codemie skill *` lifecycle events.

Persistent record (Postgres) of every wrapper-emitted event so install /
popularity counts survive Elastic retention. Read methods are intentionally
minimal in v1 — analytics handlers can be added in a follow-up PR.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional

from sqlalchemy import case, func
from sqlmodel import Session, select

from codemie.configs import logger
from codemie.rest_api.models.skill_event import SkillEvent

# Commands that represent install/uninstall lifecycle — used for filtering in
# analytics queries. "update", "list", and "find" are excluded because they
# do not change the install state of a skill.
_INSTALL_COMMANDS = ("add", "remove")


class SkillEventRepository(ABC):
    """Abstract interface for skill event persistence."""

    @abstractmethod
    def insert(self, event: SkillEvent) -> SkillEvent:
        """Persist a single skill event row.

        The row is expected to be fully prepared by the service layer
        (slug/id derivation, sanitization, user context attachment).
        """

    @abstractmethod
    def find_by_id(self, event_id: str) -> Optional[SkillEvent]:
        """Return the event with `event_id` or None."""

    @abstractmethod
    def get_events(
        self,
        *,
        user_id: str | None,
        from_dt: datetime | None,
        to_dt: datetime | None,
        limit: int,
        offset: int,
    ) -> tuple[list[SkillEvent], int]:
        """Return a paginated, filtered event log and total count.

        Only 'add' and 'remove' commands are included.
        Pass ``user_id=None`` to retrieve events for all users (admin view).
        """

    @abstractmethod
    def get_all_skills_aggregated_stats(
        self,
        *,
        user_id: str | None,
        from_dt: datetime | None,
        to_dt: datetime | None,
        limit: int,
        offset: int,
    ) -> tuple[list[dict], int]:
        """Return paginated per-skill aggregated install/removal counts.

        Each entry: ``{skill_slug, installs, removals, by_agent, by_source}``.
        Results are ordered by install count descending, with slug as a tie-breaker.
        Pass ``user_id=None`` to retrieve stats for all users.
        """

    @abstractmethod
    def get_skill_aggregated_stats(self, *, skill_slug: str) -> dict | None:
        """Return per-skill aggregated install/removal counts.

        Returns ``None`` when no rows match ``skill_slug`` (caller raises 404).
        """


class SQLSkillEventRepository(SkillEventRepository):
    """Postgres-backed implementation."""

    def insert(self, event: SkillEvent) -> SkillEvent:
        with Session(SkillEvent.get_engine()) as session:
            session.add(event)
            session.commit()
            session.refresh(event)
            logger.debug(
                f"[skill_events] inserted id={event.id!r} command={event.command!r} "
                f"status={event.status!r} skill_id={event.skill_id!r}"
            )
            return event

    def find_by_id(self, event_id: str) -> Optional[SkillEvent]:
        with Session(SkillEvent.get_engine()) as session:
            return session.get(SkillEvent, event_id)

    def get_events(
        self,
        *,
        user_id: str | None,
        from_dt: datetime | None,
        to_dt: datetime | None,
        limit: int,
        offset: int,
    ) -> tuple[list[SkillEvent], int]:
        """Return paginated install/remove events and their total count."""
        with Session(SkillEvent.get_engine()) as session:
            base_stmt = select(SkillEvent).where(SkillEvent.command.in_(_INSTALL_COMMANDS))

            if user_id is not None:
                base_stmt = base_stmt.where(SkillEvent.user_id == user_id)
            if from_dt is not None:
                base_stmt = base_stmt.where(SkillEvent.created_at >= from_dt)
            if to_dt is not None:
                base_stmt = base_stmt.where(SkillEvent.created_at <= to_dt)

            # Count query — same filters, no ordering/pagination.
            count_stmt = select(func.count()).select_from(base_stmt.subquery())
            total: int = session.exec(count_stmt).one()

            # Data query — ordered + paginated.
            data_stmt = base_stmt.order_by(SkillEvent.created_at.desc()).offset(offset).limit(limit)
            rows: list[SkillEvent] = list(session.exec(data_stmt).all())

        logger.debug(f"[skill_events] get_events user_id={user_id!r} total={total} returned={len(rows)}")
        return rows, total

    def get_all_skills_aggregated_stats(
        self,
        *,
        user_id: str | None,
        from_dt: datetime | None,
        to_dt: datetime | None,
        limit: int,
        offset: int,
    ) -> tuple[list[dict], int]:
        """Return paginated per-skill aggregated install/removal counts, ordered by installs descending."""
        with Session(SkillEvent.get_engine()) as session:

            def _apply_filters(stmt):
                stmt = stmt.where(SkillEvent.command.in_(_INSTALL_COMMANDS))
                stmt = stmt.where(SkillEvent.skill_slug.isnot(None))
                if user_id is not None:
                    stmt = stmt.where(SkillEvent.user_id == user_id)
                if from_dt is not None:
                    stmt = stmt.where(SkillEvent.created_at >= from_dt)
                if to_dt is not None:
                    stmt = stmt.where(SkillEvent.created_at <= to_dt)
                return stmt

            total: int = session.exec(_apply_filters(select(func.count(func.distinct(SkillEvent.skill_slug))))).one()

            _install_count = func.sum(case((SkillEvent.command == "add", 1), else_=0))
            slugs: list[str | None] = list(
                session.exec(
                    _apply_filters(
                        select(SkillEvent.skill_slug)
                        .group_by(SkillEvent.skill_slug)
                        .order_by(_install_count.desc(), SkillEvent.skill_slug.asc())
                    )
                    .offset(offset)
                    .limit(limit)
                ).all()
            )

            if not slugs:
                return [], total

            rows = session.exec(
                _apply_filters(
                    select(
                        SkillEvent.skill_slug,
                        SkillEvent.command,
                        SkillEvent.agent,
                        SkillEvent.source,
                        func.count().label("cnt"),
                    )
                    .where(SkillEvent.skill_slug.in_(slugs))
                    .group_by(SkillEvent.skill_slug, SkillEvent.command, SkillEvent.agent, SkillEvent.source)
                )
            ).all()

        stats_map: dict[str | None, dict] = {
            s: {"skill_slug": s, "installs": 0, "removals": 0, "by_agent": {}, "by_source": {}} for s in slugs
        }
        for slug, command, agent, source, cnt in rows:
            entry = stats_map.get(slug)
            if entry is None:
                continue
            if command == "add":
                entry["installs"] += cnt
                key = agent or "unknown"
                entry["by_agent"][key] = entry["by_agent"].get(key, 0) + cnt
                src_key = source or "unknown"
                entry["by_source"][src_key] = entry["by_source"].get(src_key, 0) + cnt
            elif command == "remove":
                entry["removals"] += cnt

        logger.debug(f"[skill_events] get_all_skills_aggregated_stats total={total} returned={len(slugs)}")
        return [stats_map[s] for s in slugs], total

    def get_skill_aggregated_stats(self, *, skill_slug: str) -> dict | None:
        """Return install/removal counts and per-agent/source breakdown for a skill."""
        with Session(SkillEvent.get_engine()) as session:
            agg_stmt = (
                select(
                    SkillEvent.command,
                    SkillEvent.agent,
                    SkillEvent.source,
                    func.count().label("cnt"),
                )
                .where(SkillEvent.skill_slug == skill_slug)
                .where(SkillEvent.command.in_(_INSTALL_COMMANDS))
                .group_by(SkillEvent.command, SkillEvent.agent, SkillEvent.source)
            )
            rows = session.exec(agg_stmt).all()

        if not rows:
            return None

        installs = 0
        removals = 0
        by_agent: dict[str, int] = {}
        by_source: dict[str, int] = {}

        for command, agent, source, cnt in rows:
            if command == "add":
                installs += cnt
                key = agent or "unknown"
                by_agent[key] = by_agent.get(key, 0) + cnt
                src_key = source or "unknown"
                by_source[src_key] = by_source.get(src_key, 0) + cnt
            elif command == "remove":
                removals += cnt

        return {"installs": installs, "removals": removals, "by_agent": by_agent, "by_source": by_source}


# Default implementation (mirrors KataUsageRepositoryImpl convention)
SkillEventRepositoryImpl = SQLSkillEventRepository
