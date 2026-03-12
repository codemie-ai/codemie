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

from math import ceil

from fastapi import status
from sqlmodel import select, Session, func

from codemie.core.workflow_models import (
    WorkflowExecutionTransition,
    WorkflowExecutionTransitionResponse,
)
from codemie.core.exceptions import ExtendedHTTPException


class WorkflowExecutionTransitionsIndexService:
    """Service for querying and paginating workflow execution transitions."""

    @classmethod
    def run(
        cls,
        execution_id: str,
        page: int = 0,
        per_page: int = 10,
    ) -> dict:
        """Retrieve paginated list of workflow execution transitions.

        Args:
            execution_id: The workflow execution ID to filter by
            page: Zero-based page index (default: 0)
            per_page: Number of items per page (default: 10, max: 100)

        Returns:
            dict: Contains 'data' (list of WorkflowExecutionTransitionResponse)
                  and 'pagination' (metadata with page, per_page, total, pages)

        Raises:
            DatabaseException: On database query failure
        """
        with Session(WorkflowExecutionTransition.get_engine()) as session:
            query = select(WorkflowExecutionTransition)
            query = query.where(WorkflowExecutionTransition.execution_id == execution_id)
            query = query.order_by(WorkflowExecutionTransition.date.asc())

            total = session.exec(select(func.count()).select_from(query.subquery())).one()
            query = query.offset(page * per_page).limit(per_page)
            transitions = session.exec(query).all()

        pages = ceil(total / per_page) if per_page > 0 else 0
        meta = {"page": page, "per_page": per_page, "total": total, "pages": pages}

        transition_responses = [
            WorkflowExecutionTransitionResponse(**transition.model_dump()) for transition in transitions
        ]

        return {"data": transition_responses, "pagination": meta}

    @classmethod
    def get_by_from_state(cls, execution_id: str, from_state_id: str) -> WorkflowExecutionTransitionResponse:
        """Retrieve single transition by source state ID.

        Args:
            execution_id: The workflow execution ID
            from_state_id: The source state ID

        Returns:
            WorkflowExecutionTransitionResponse: The transition record

        Raises:
            ExtendedHTTPException: If transition not found (404)
        """
        with Session(WorkflowExecutionTransition.get_engine()) as session:
            query = select(WorkflowExecutionTransition)
            query = query.where(WorkflowExecutionTransition.execution_id == execution_id)
            query = query.where(WorkflowExecutionTransition.from_state_id == from_state_id)

            transition = session.exec(query).first()

            if not transition:
                raise ExtendedHTTPException(
                    code=status.HTTP_404_NOT_FOUND,
                    message="Transition Not Found",
                    details=f"No transition found originating from state {from_state_id} in execution {execution_id}",
                    help="Please ensure the state ID is correct and the transition exists.",
                )

            return WorkflowExecutionTransitionResponse(**transition.model_dump())

    @classmethod
    def get_by_to_state(cls, execution_id: str, to_state_id: str) -> WorkflowExecutionTransitionResponse:
        """Retrieve single transition by target state ID.

        Args:
            execution_id: The workflow execution ID
            to_state_id: The target state ID

        Returns:
            WorkflowExecutionTransitionResponse: The transition record

        Raises:
            ExtendedHTTPException: If transition not found (404)
        """
        with Session(WorkflowExecutionTransition.get_engine()) as session:
            query = select(WorkflowExecutionTransition)
            query = query.where(WorkflowExecutionTransition.execution_id == execution_id)
            query = query.where(WorkflowExecutionTransition.to_state_id == to_state_id)

            transition = session.exec(query).first()

            if not transition:
                raise ExtendedHTTPException(
                    code=status.HTTP_404_NOT_FOUND,
                    message="Transition Not Found",
                    details=f"No transition found targeting state {to_state_id} in execution {execution_id}",
                    help="Please ensure the state ID is correct and the transition exists.",
                )

            return WorkflowExecutionTransitionResponse(**transition.model_dump())
