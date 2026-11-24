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

from fastapi import status
from sqlalchemy.exc import SQLAlchemyError

from codemie.configs.logger import logger
from codemie.core.exceptions import ExtendedHTTPException, NotFoundException, ValidationException
from codemie.core.workflow_models import WorkflowErrorFormat
from codemie.core.workflow_models.workflow_config import WorkflowConfig, YamlConfigHistory
from codemie.rest_api.models.mcp_restore import DisabledMcpServer
from codemie.rest_api.models.workflow_version import (
    DEFAULT_VERSIONS_PER_PAGE,
    WorkflowRevisionEntry,
    WorkflowVersionHistoryResponse,
)
from codemie.rest_api.security.user import User
from codemie.service.mcp.access_control import (
    CatalogLookupError,
    after_workflow_links_changed,
    catalog_unavailable_error,
)
from codemie.service.mcp.workflow_yaml_editor import WorkflowYamlEditError, disable_dead_links_in_yaml
from codemie.service.workflow.save_validation import WORKFLOW_CONFIGURATION_ERROR, validate


class WorkflowVersionService:
    @classmethod
    def get_version_history(
        cls,
        workflow: WorkflowConfig,
        page: int = 0,
        per_page: int = DEFAULT_VERSIONS_PER_PAGE,
    ) -> WorkflowVersionHistoryResponse:
        history = workflow.yaml_config_history or []
        start = page * per_page
        return WorkflowVersionHistoryResponse(
            revisions=[
                WorkflowRevisionEntry(**entry.model_dump(), revision_index=start + offset)
                for offset, entry in enumerate(history[start : start + per_page])
            ],
            total_revisions=len(history),
            workflow_name=workflow.name,
            workflow_id=workflow.id,
        )

    @classmethod
    def rollback_to_version(
        cls,
        workflow: WorkflowConfig,
        revision_index: int,
        user: User,
        change_notes: str | None = None,
    ) -> tuple[WorkflowConfig, list[DisabledMcpServer]]:
        """Restore a prior revision; MCP links to unavailable catalogue entries come back disabled and are reported."""
        history = workflow.yaml_config_history or []
        if not history:
            raise ExtendedHTTPException(
                code=status.HTTP_400_BAD_REQUEST,
                message="No revision history available for this workflow.",
                details="The workflow has no prior revisions to roll back to.",
                help="Save the workflow at least once to create revision history.",
            )
        if revision_index < 0 or revision_index >= len(history):
            raise ExtendedHTTPException(
                code=status.HTTP_400_BAD_REQUEST,
                message="Invalid revision index.",
                details=f"Valid range: 0 to {len(history) - 1}. Received: {revision_index}.",
                help="Use GET /v1/workflows/{id}/versions to list available revisions.",
            )
        target = history[revision_index]
        if not (target.yaml_config or "").strip():
            raise ExtendedHTTPException(
                code=status.HTTP_400_BAD_REQUEST,
                message="Revision has empty yaml_config.",
                details="The selected revision does not contain a workflow configuration to restore.",
                help="Choose a revision that includes a non-empty yaml_config.",
            )
        # Edit a copy of the text first: on failure nothing on the workflow has been touched.
        try:
            restored_yaml, disabled_mcp = disable_dead_links_in_yaml(target.yaml_config)
        except WorkflowYamlEditError as exc:
            raise ExtendedHTTPException(
                code=status.HTTP_400_BAD_REQUEST,
                message=WORKFLOW_CONFIGURATION_ERROR,
                details=str(exc),
                help="The revision's YAML cannot be parsed or edited. Choose another revision.",
            ) from exc
        except CatalogLookupError as exc:  # the catalogue read failed; nothing on the workflow was touched
            logger.error(f"MCP catalogue lookup failed during rollback of workflow {workflow.id}: {exc}")
            raise catalog_unavailable_error() from exc
        except Exception:
            logger.exception(f"Unexpected error while disabling unavailable MCP links, workflow {workflow.id}")
            raise
        new_entry = YamlConfigHistory(
            yaml_config=workflow.yaml_config or "",
            date=datetime.now(),
            created_by=user.as_user_model(),
            change_notes=change_notes,
        )
        logger.info(
            f"Rolling back workflow {workflow.id} to revision index {revision_index}. "
            f"Prepending current config as new history entry."
        )
        original_yaml = workflow.yaml_config
        workflow.yaml_config = restored_yaml
        # parse_execution_config only replaces states when the yaml key is present;
        # clear first so a restored snapshot that omits states cannot keep the live graph.
        workflow.states = []
        try:
            workflow.parse_execution_config()
            workflow.updated_by = user.as_user_model()
            cls._validate_restored_workflow(workflow, user)
        except (ExtendedHTTPException, ValidationException, NotFoundException, SQLAlchemyError):
            workflow.yaml_config = original_yaml
            raise
        except Exception as exc:
            workflow.yaml_config = original_yaml
            details = exc.args[0] if exc.args and isinstance(exc.args[0], dict) else str(exc).strip()
            raise ExtendedHTTPException(
                code=status.HTTP_400_BAD_REQUEST,
                message=WORKFLOW_CONFIGURATION_ERROR,
                details=details,
                help="",
            ) from exc
        workflow.update(yaml_config_history=[new_entry, *history])
        after_workflow_links_changed(workflow)
        return workflow, disabled_mcp

    @staticmethod
    def _validate_restored_workflow(workflow: WorkflowConfig, user: User) -> None:
        validate(
            workflow,
            None,
            user,
            WorkflowErrorFormat.STRING,
            stored_workflow=workflow,
            link_contracts=False,
        )
