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

from datetime import datetime

from sqlmodel import Session

from codemie.clients.postgres import PostgresClient
from codemie.rest_api.models.activity_event import ActivityEventFilterOptions, ActivityEventListItem
from codemie.service.activity.activity_repository import activity_event_repository


class ActivityEventService:
    def list_events(
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
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[ActivityEventListItem], int]:
        with Session(PostgresClient.get_engine()) as session:
            rows, total = activity_event_repository.find_all(
                actor_id=actor_id,
                domain=domain,
                event_type=event_type,
                entity_type=entity_type,
                entity_id=entity_id,
                from_dt=from_dt,
                to_dt=to_dt,
                sort_dir=sort_dir,
                limit=limit,
                offset=offset,
                session=session,
            )
        return [
            ActivityEventListItem(
                id=r.id,
                domain=r.domain,
                event_type=r.event_type,
                entity_type=r.entity_type,
                entity_id=r.entity_id,
                actor_id=r.actor_id,
                actor_email=r.actor_email,
                actor_name=r.actor_name,
                attributes=r.attributes,
                created_at=r.created_at,
            )
            for r in rows
        ], total

    def get_filter_options(self) -> ActivityEventFilterOptions:
        with Session(PostgresClient.get_engine()) as session:
            return ActivityEventFilterOptions(
                domains=activity_event_repository.get_distinct_domains(session),
                event_types=activity_event_repository.get_distinct_event_types(session),
                entity_types=activity_event_repository.get_distinct_entity_types(session),
                mapping=activity_event_repository.get_domain_mapping(session),
            )


activity_event_service = ActivityEventService()
