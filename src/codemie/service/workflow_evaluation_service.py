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

"""Service for evaluating workflows against Langfuse datasets."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from fastapi import BackgroundTasks, Request

if TYPE_CHECKING:
    from langfuse.experiment import ExperimentItem

from codemie.configs import logger
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import EvaluationResponse
from codemie.core.workflow_models import WorkflowConfig
from codemie.enterprise.langfuse import require_langfuse_client
from codemie.rest_api.security.user import User
from codemie.service.workflow_service import WorkflowService
from codemie.workflows.workflow import WorkflowExecutor


class WorkflowEvaluationService:
    """Service for evaluating workflows against Langfuse datasets."""

    @classmethod
    def evaluate_workflow(
        cls,
        workflow_config: WorkflowConfig,
        dataset_id: str,
        experiment_name: str,
        max_concurrency: int,
        background_tasks: BackgroundTasks,
        user: User,
        raw_request: Request,
    ) -> EvaluationResponse:
        """
        Start an evaluation of a workflow against a Langfuse dataset.

        Phase 1 (synchronous): eagerly validates that LangFuse is available and the
        dataset can be resolved before accepting the request, then schedules the
        item-by-item execution as a background task.

        Args:
            workflow_config: The workflow configuration to evaluate.
            dataset_id: ID or name of the Langfuse dataset to use.
            experiment_name: Name grouping all item traces in the Langfuse UI.
            max_concurrency: Number of items processed concurrently (range [1, 5]).
            background_tasks: FastAPI background tasks object for async processing.
            user: User performing the evaluation.
            raw_request: Original request object, used to resolve the LangFuse client.

        Returns:
            EvaluationResponse returned immediately before any items are processed.

        Raises:
            ExtendedHTTPException: 503 if LangFuse is unavailable, 400 if the dataset
                cannot be resolved.
        """
        # Validate LangFuse availability BEFORE queuing task.
        # Raises HTTP 503 immediately if enterprise features are not available.
        langfuse = require_langfuse_client(raw_request)

        # Validate dataset exists BEFORE queuing task.
        # Raises HTTP 400 immediately if dataset_id is invalid.
        try:
            dataset = langfuse.get_dataset(dataset_id)
            dataset_items_count = len(dataset.items)
            logger.info(
                f"Validation passed: Dataset {dataset_id} found with {dataset_items_count} items. "
                f"Queuing workflow evaluation experiment: {experiment_name}"
            )
        except Exception as e:
            logger.error(f"Error getting dataset: {str(e)}")
            raise ExtendedHTTPException(
                code=400,
                message=f"Cannot find dataset with id/name {dataset_id}",
                details="Please find and specify correct dataset details from Langfuse",
            ) from e

        background_tasks.add_task(
            cls._run_evaluation_task,
            workflow_config=workflow_config,
            dataset_id=dataset_id,
            experiment_name=experiment_name,
            max_concurrency=max_concurrency,
            user=user,
            raw_request=raw_request,
        )

        return EvaluationResponse(
            message=f"Evaluation for dataset {dataset_id} has been queued and will run in the background.",
            experiment_name=experiment_name,
        )

    @classmethod
    def _run_evaluation_task(
        cls,
        workflow_config: WorkflowConfig,
        dataset_id: str,
        experiment_name: str,
        max_concurrency: int,
        user: User,
        raw_request: Request,
    ) -> None:
        """
        Execute the evaluation experiment in the background.

        Phase 2 (background): drives the item-by-item experiment loop through the
        Langfuse SDK's ``run_experiment`` API. Runs AFTER the response has already
        been sent to the user; LangFuse availability and dataset existence were
        already validated in ``evaluate_workflow``.

        Args:
            Same as evaluate_workflow (without background_tasks).
        """
        # Re-fetch the LangFuse client and dataset (already validated in evaluate_workflow).
        langfuse = require_langfuse_client(raw_request)
        dataset = langfuse.get_dataset(dataset_id)

        def item_task(*, item: ExperimentItem) -> str | None:
            """Run a single dataset item through the workflow and return its output."""
            try:
                conversation_id = str(uuid.uuid4())
                user_input = item.input
                with langfuse.propagate_attributes(
                    session_id=conversation_id, metadata={"workflow_id": workflow_config.id}
                ):
                    execution = WorkflowService.create_workflow_execution(
                        workflow_config,
                        user=user.as_user_model(),
                        user_input=user_input,
                    )
                    executor = WorkflowExecutor.create_executor(
                        workflow_config=workflow_config,
                        user_input=user_input,
                        user=user,
                        execution_id=execution.execution_id,
                        session_id=conversation_id,
                    )
                    # Blocks until the workflow finishes.
                    executor.stream()

                completed = WorkflowService.find_workflow_execution_by_id(execution.execution_id)
                return completed.output
            except Exception as e:
                logger.error(f"Error processing evaluation item for dataset {dataset_id}: {str(e)}")
                # Reraise the exception to ensure the Langfuse SDK captures it and marks the item as failed.
                raise

        logger.info(f"Starting workflow evaluation experiment: {experiment_name}")
        execution_result = dataset.run_experiment(
            name=experiment_name,
            task=item_task,
            max_concurrency=max_concurrency,
        )
        logger.info(f"Completed workflow evaluation experiment: {experiment_name}")
        logger.debug(f"Langfuse experiment result: {execution_result.format(include_item_results=True)}")
