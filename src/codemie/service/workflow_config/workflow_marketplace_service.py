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

from codemie.configs import logger
from codemie.core.exceptions import NotFoundException, ValidationException
from codemie.core.workflow_models.workflow_config import WorkflowConfig
from codemie.rest_api.models.workflow_marketplace import (
    PublishWorkflowToMarketplaceRequest,
    WorkflowPublishValidationResponse,
)
from codemie.rest_api.security.user import User
from codemie.repository.category_repository import CategoryRepository
from codemie.service.inline_credentials_collector import InlineCredentialsCollector
from codemie.repository.workflow_config_repository import WorkflowConfigRepository
from codemie.service.external_entities_collector import WorkflowExternalEntitiesCollector

_credentials_collector = InlineCredentialsCollector()
_entities_collector = WorkflowExternalEntitiesCollector()
_category_repository = CategoryRepository()
_workflow_config_repository = WorkflowConfigRepository()


class WorkflowMarketplaceService:
    """Service for managing workflow marketplace publish/unpublish operations."""

    def _apply_publish(self, workflow: WorkflowConfig, category_ids: list[str]) -> WorkflowConfig:
        _workflow_config_repository.set_publish_state(str(workflow.id), is_global=True, categories=category_ids)
        workflow.is_global = True
        workflow.categories = category_ids
        return workflow

    def _validate_categories(self, category_ids: list[str]) -> None:
        found_categories = _category_repository.get_by_ids(category_ids)
        found_ids = {cat.id for cat in found_categories}
        invalid_ids = [cid for cid in category_ids if cid not in found_ids]
        if invalid_ids:
            raise ValidationException(f"Invalid category IDs: {invalid_ids}")

    async def validate(self, workflow: WorkflowConfig, user: User) -> WorkflowPublishValidationResponse:
        """
        Validate a workflow for marketplace publication.

        Detects external references (assistants, skills) which block publication, and
        collects inline (hardcoded) credentials which require user confirmation.
        """
        logger.info(f"Validating workflow '{workflow.id}' for marketplace publication by user '{user.id}'")

        try:
            assistants, skills = _entities_collector.collect_for_workflow(workflow, user)
        except NotFoundException as exc:
            raise ValidationException(str(exc)) from exc
        if assistants or skills:
            raise ValidationException("Workflow contains external assistants or skills")

        inline_credentials = _credentials_collector.collect_for_workflow(workflow)

        if inline_credentials:
            message = (
                "Workflow contains inline credentials in MCP servers or assistant toolkits "
                "that will be shared with all users. Please confirm that you want to publish it."
            )
        else:
            message = f"Workflow {workflow.id} is ready to be published to marketplace"

        logger.info(f"Workflow '{workflow.id}' validation complete: inline_credentials={len(inline_credentials)}")

        return WorkflowPublishValidationResponse(
            message=message,
            inline_credentials=inline_credentials,
            workflow_id=str(workflow.id),
        )

    async def publish(
        self,
        workflow: WorkflowConfig,
        request: PublishWorkflowToMarketplaceRequest,
        user: User,
    ) -> WorkflowConfig:
        """
        Publish a workflow to the marketplace.

        Blocks if external assistant or skill references are found.
        Validates categories and optionally checks inline credentials.
        """
        await self.validate(workflow, user)
        self._validate_categories(request.categories)

        logger.info(f"Publishing workflow '{workflow.id}' to marketplace by user '{user.id}'")

        return self._apply_publish(workflow, request.categories)

    def track_usage(self, workflow_id: str, user_id: str) -> None:
        """Recompute unique_users_count from workflow_executions after each use.

        Intended to be called as a background task after a workflow execution is created.
        """
        try:
            _workflow_config_repository.recompute_unique_users_count(workflow_id)
        except Exception:
            logger.exception(f"Failed to track usage for workflow '{workflow_id}' by user '{user_id}'")

    async def unpublish(self, workflow: WorkflowConfig, user: User) -> WorkflowConfig:
        """Remove a workflow from the marketplace by setting is_global=False."""
        logger.info(f"Unpublishing workflow '{workflow.id}' from marketplace by user '{user.id}'")

        _workflow_config_repository.set_publish_state(str(workflow.id), is_global=False)
        workflow.is_global = False
        return workflow
