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

"""Service for evaluating assistants against datasets."""

import uuid
from typing import Optional

from fastapi import BackgroundTasks, Request

from codemie.configs import logger
from codemie.enterprise.langfuse import require_langfuse_client
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import AssistantChatRequest, EvaluationResponse
from codemie.rest_api.handlers.assistant_handlers import get_request_handler
from codemie.rest_api.models.assistant import Assistant
from codemie.rest_api.security.user import User


class AssistantEvaluationService:
    """Service for evaluating assistants against datasets."""

    @classmethod
    def evaluate_assistant(
        cls,
        assistant: Assistant,
        dataset_id: str,
        experiment_name: str,
        background_tasks: BackgroundTasks,
        llm_model: Optional[str] = None,
        user: Optional[User] = None,
        request_uuid: Optional[str] = None,
        raw_request: Optional[Request] = None,
        system_prompt: Optional[str] = None,
    ) -> EvaluationResponse:
        """
        Start an evaluation of an assistant against a dataset.

        Args:
            assistant: The assistant to evaluate
            dataset_id: ID of the Langfuse dataset to use
            experiment_name: Name for this evaluation experiment
            llm_model: Optional LLM model to override assistant's default
            user: User performing the evaluation
            request_uuid: UUID for the request
            background_tasks: Background tasks object for async processing
            raw_request: Original request object

        Returns:
            EvaluationResponse with status message

        Raises:
            ExtendedHTTPException: 503 if LangFuse not available, 400 if dataset not found
        """
        # Validate LangFuse availability BEFORE queuing task
        # Raises HTTP 503 immediately if enterprise features not available
        langfuse = require_langfuse_client(raw_request)

        # Validate dataset exists BEFORE queuing task
        # Raises HTTP 400 immediately if dataset_id is invalid
        try:
            dataset = langfuse.get_dataset(dataset_id)
            dataset_items_count = len(dataset.items)
            logger.info(
                f"Validation passed: Dataset {dataset_id} found with {dataset_items_count} items. "
                f"Queuing evaluation experiment: {experiment_name}"
            )
        except Exception as e:
            logger.error(f"Error getting dataset: {str(e)}")
            raise ExtendedHTTPException(
                code=400,
                message=f"Cannot find dataset with id/name {dataset_id}",
                details="Please find and specify correct dataset details from Langfuse",
            ) from e

        # Add the evaluation task to run asynchronously
        background_tasks.add_task(
            cls._run_evaluation_task,
            assistant=assistant,
            dataset_id=dataset_id,
            experiment_name=experiment_name,
            llm_model=llm_model,
            user=user,
            request_uuid=request_uuid,
            raw_request=raw_request,
            system_prompt=system_prompt,
        )

        return EvaluationResponse(
            message=f"Evaluation for dataset {dataset_id} has been queued and will run in the background.",
            experiment_name=experiment_name,
        )

    @classmethod
    def _run_evaluation_task(
        cls,
        assistant: Assistant,
        dataset_id: str,
        experiment_name: str,
        llm_model: Optional[str] = None,
        user: Optional[User] = None,
        request_uuid: Optional[str] = None,
        raw_request: Optional[Request] = None,
        system_prompt: Optional[str] = None,
    ) -> EvaluationResponse:
        """
        Execute the evaluation task in background.

        Note: This runs as a background task AFTER the response has been sent to the user.
        We've already validated LangFuse availability and dataset existence in evaluate_assistant,
        so LangFuse client and dataset retrieval should succeed.

        Args:
            Same as evaluate_assistant method
        """
        handler = get_request_handler(assistant, user, request_uuid)

        # Get LangFuse client (already validated in evaluate_assistant)
        langfuse = require_langfuse_client(raw_request)

        # Get dataset (already validated to exist in evaluate_assistant)
        dataset = langfuse.get_dataset(dataset_id)
        dataset_items_count = len(dataset.items)
        logger.info(f"Starting evaluation experiment: {experiment_name} with {dataset_items_count} items")

        # Define the task to run for each dataset item
        def run_dataset_item(*, item, **kwargs):
            try:
                conversation_id = str(uuid.uuid4())
                query = item.input
                extra = {"system_prompt": system_prompt} if system_prompt is not None else {}
                chat_request = AssistantChatRequest(
                    text=query, llm_model=llm_model, stream=False, conversation_id=conversation_id, **extra
                )
                with langfuse.propagate_attributes(session_id=conversation_id, metadata={"assistant_id": assistant.id}):
                    response = handler.process_request(chat_request, None, raw_request)
                return response.generated
            except Exception as e:
                logger.error(f"Error processing evaluation item for dataset {dataset_id}: {str(e)}")
                raise e

        experiment_result = dataset.run_experiment(name=experiment_name, task=run_dataset_item)

        logger.info(f"Completed experiment: {experiment_name} with {dataset_items_count} items")
        logger.debug(f"Experiment results: {experiment_result.format(include_item_results=True)}")

        return EvaluationResponse(
            message=f"Evaluation for dataset {dataset_id} has been completed successfully.",
            experiment_name=experiment_name,
        )
