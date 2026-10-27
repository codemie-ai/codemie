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

"""
Repository for logging and counting assistant clone events.
"""

from typing import Optional
from uuid import uuid4

from sqlmodel import Session, select, func

from codemie.rest_api.models.usage.assistant_clone_event import AssistantCloneEventSQL


class AssistantCloneEventRepository:
    """Repository for the assistant_clone_event table. No dedup — every clone action counts."""

    def log_clone_event(
        self,
        assistant_id: str,
        user_id: str,
        clone_assistant_id: Optional[str] = None,
    ) -> None:
        """Insert a clone event row. Deliberately no dedup check.

        `clone_assistant_id` is the assistant the clone produced. Passing it
        turns the row from a counter tick into a provenance edge, which is what
        marketplace duplicate detection consumes (EXECUTION-PLAN 9.1, F-70).
        It stays optional so callers that genuinely do not know the child id
        can still record the event rather than dropping it.
        """
        event = AssistantCloneEventSQL(
            id=str(uuid4()),
            assistant_id=assistant_id,
            user_id=user_id,
            clone_assistant_id=clone_assistant_id,
        )
        with Session(AssistantCloneEventSQL.get_engine()) as session:
            session.add(event)
            session.commit()

    def list_clone_edges(self) -> list[tuple[str, str]]:
        """Return every recorded (source_assistant_id, clone_assistant_id) edge.

        One query for the whole corpus rather than one per assistant: the audit
        scores every pair, so it needs the full edge set up front. Rows written
        before `c1o2n3e4e5d6` have a NULL child and are excluded — the partial
        index `ix_assistant_clone_event_clone_assistant_id` serves exactly this
        predicate.

        Self-edges are filtered out. They should not exist, but a row where the
        clone and the source are the same assistant would mark that assistant a
        CRITICAL duplicate of itself, and a data glitch must not be able to
        produce a cleanup verdict.
        """
        with Session(AssistantCloneEventSQL.get_engine()) as session:
            rows = session.exec(
                select(
                    AssistantCloneEventSQL.assistant_id,
                    AssistantCloneEventSQL.clone_assistant_id,
                ).where(AssistantCloneEventSQL.clone_assistant_id.is_not(None))
            ).all()
        return [(source, clone) for source, clone in rows if source and clone and source != clone]

    def get_clone_count(self, assistant_id: str) -> int:
        """Return the total number of clone events for an assistant."""
        with Session(AssistantCloneEventSQL.get_engine()) as session:
            query = select(func.count()).select_from(
                select(AssistantCloneEventSQL).where(AssistantCloneEventSQL.assistant_id == assistant_id).subquery()
            )
            return session.exec(query).one()
