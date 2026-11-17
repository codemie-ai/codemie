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

"""Per-run check that a scheduled target still lives in the scheduler's project (EPMCDME-13973)."""

from datetime import UTC, datetime

from codemie.configs import logger
from codemie.rest_api.models.settings import Settings
from codemie.triggers.bindings.webhook import ResourceType


def project_mismatch(scheduler_id: str, resource_type: str, resource_id: str | None) -> bool:
    """True when the scheduled target is not in the scheduler's project; fails closed, never raises.

    Read fresh at every run (no cache): a target moved to another project after the schedule was
    saved must stop running, and the 300s validation cache would keep it alive.
    """
    try:
        if not resource_id:
            raise ValueError("scheduler has no target id")
        project_name = Settings.get_by_id(scheduler_id).project_name
        # Lazy: the services import modules that import this one.
        if resource_type == ResourceType.ASSISTANT.value:
            from codemie.service.assistant.assistant_service import AssistantService

            belongs = AssistantService.belongs_to_project(resource_id, project_name)
        elif resource_type == ResourceType.WORKFLOW.value:
            from codemie.service.workflow_service import WorkflowService

            belongs = WorkflowService.belongs_to_project(resource_id, project_name)
        else:
            from codemie.service.index.index_service import IndexStatusService

            belongs = IndexStatusService.belongs_to_project(resource_id, project_name)
    except Exception as exc:
        logger.error(
            "Blocked scheduler %s: cannot verify %s %s project: %r", scheduler_id, resource_type, resource_id, exc
        )
        return True
    if belongs:
        return False
    logger.error(
        "Blocked scheduler %s: %s %s not found or not in scheduler project '%s'",
        scheduler_id,
        resource_type,
        resource_id,
        project_name,
    )
    return True


def skipped_for_project_mismatch(repo, run, started_at: datetime, resource_type: str, resource_id: str | None) -> bool:
    """Record the run as skipped when its target is not in the scheduler's project, checked at every run."""
    if not project_mismatch(run.scheduler_id, resource_type, resource_id):
        return False
    finished_at = datetime.now(UTC)
    repo.update(
        run.id,
        {
            "status": "skipped",
            "finished_at": finished_at,
            "duration_ms": int((finished_at - started_at).total_seconds() * 1000),
            "result_data": {"content": "The scheduled resource does not belong to the scheduler's project"},
        },
    )
    return True
