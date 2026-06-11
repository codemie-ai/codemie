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

import json

from sqlalchemy import text

from codemie.core.exceptions import NotFoundException
from codemie.core.workflow_models.workflow_config import WorkflowConfig


class WorkflowConfigRepository:
    """Repository for WorkflowConfig database access."""

    def set_publish_state(
        self,
        workflow_id: str,
        *,
        is_global: bool,
        categories: list[str] | None = None,
    ) -> None:
        """Update is_global and optionally categories without touching other columns."""
        params: dict[str, object] = {"is_global": is_global, "wf_id": workflow_id}
        set_clause = "is_global = :is_global"
        if categories is not None:
            set_clause += ", categories = CAST(:categories AS jsonb)"
            params["categories"] = json.dumps(categories)

        with WorkflowConfig.get_engine().begin() as conn:
            result = conn.execute(text(f"UPDATE workflows SET {set_clause} WHERE id = :wf_id").bindparams(**params))
        if result.rowcount == 0:
            raise NotFoundException(f"Workflow '{workflow_id}' not found")

    def recompute_unique_users_count(self, workflow_id: str) -> None:
        """Recompute unique_users_count from workflow_executions in a single atomic UPDATE."""
        with WorkflowConfig.get_engine().begin() as conn:
            result = conn.execute(
                text(
                    "UPDATE workflows"
                    " SET unique_users_count = ("
                    "   SELECT COUNT(DISTINCT created_by->>'user_id')"
                    "   FROM workflow_executions"
                    "   WHERE workflow_id = :wf_id"
                    " )"
                    " WHERE id = :wf_id"
                ).bindparams(wf_id=workflow_id),
            )
        if result.rowcount == 0:
            raise NotFoundException(f"Workflow '{workflow_id}' not found")
