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

from math import ceil
from typing import Any, List, Optional

from sqlmodel import select, Session, func

from codemie.core.workflow_models import (
    WorkflowExecutionState,
    WorkflowExecutionStateThought,
    WorkflowExecutionStateWithThougths,
)


class WorkflowExecutionStatesIndexService:
    @classmethod
    def run(
        cls,
        execution_id: str,
        page: int = 0,
        per_page: int = 10,
        include_thoughts: bool = True,
        state_name_prefix: str = None,
        states_status_filter: Optional[List[str]] = None,
        retrieve_model: Any = WorkflowExecutionStateWithThougths,
    ) -> dict:
        with Session(WorkflowExecutionState.get_engine()) as session:
            query = select(WorkflowExecutionState)
            query = query.where(WorkflowExecutionState.execution_id == execution_id)

            if state_name_prefix:
                query = query.where(WorkflowExecutionState.name.startswith(state_name_prefix))

            if states_status_filter:
                query = query.where(WorkflowExecutionState.status.in_(states_status_filter))

            query = query.order_by(WorkflowExecutionState.date.asc())

            # Get total count for pagination
            total = session.exec(select(func.count()).select_from(query.subquery())).one()

            # Apply pagination
            query = query.offset(page * per_page).limit(per_page)

            states = session.exec(query).all()

        pages = ceil(total / per_page)
        meta = {"page": page, "per_page": per_page, "total": total, "pages": pages}

        if retrieve_model != WorkflowExecutionState:
            states = [retrieve_model(**state.model_dump()) for state in states]

        if include_thoughts:
            for state in states:
                state.thoughts = WorkflowExecutionStateThought.get_root(state_ids=[state.id])

        return {"data": states, "pagination": meta}
