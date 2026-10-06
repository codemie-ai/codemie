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

from unittest.mock import MagicMock, patch

import pytest

from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import EvaluationResponse
from codemie.service.workflow_evaluation_service import WorkflowEvaluationService

SERVICE_MODULE = "codemie.service.workflow_evaluation_service"


class TestEvaluateWorkflow:
    def test_returns_evaluation_response_with_experiment_name(self):
        mock_langfuse = MagicMock()
        mock_langfuse.get_dataset.return_value = MagicMock(items=[])
        background_tasks = MagicMock()

        with patch(
            f"{SERVICE_MODULE}.require_langfuse_client",
            return_value=mock_langfuse,
        ):
            response = WorkflowEvaluationService.evaluate_workflow(
                workflow_config=MagicMock(),
                dataset_id="ds-1",
                experiment_name="exp-1",
                max_concurrency=1,
                background_tasks=background_tasks,
                user=MagicMock(),
                raw_request=MagicMock(),
            )

        assert isinstance(response, EvaluationResponse)
        assert response.experiment_name == "exp-1"

    def test_raises_503_when_langfuse_unavailable(self):
        background_tasks = MagicMock()

        with patch(
            f"{SERVICE_MODULE}.require_langfuse_client",
            side_effect=ExtendedHTTPException(code=503, message="LangFuse not available"),
        ):
            with pytest.raises(ExtendedHTTPException) as exc_info:
                WorkflowEvaluationService.evaluate_workflow(
                    workflow_config=MagicMock(),
                    dataset_id="ds-1",
                    experiment_name="exp-1",
                    max_concurrency=1,
                    background_tasks=background_tasks,
                    user=MagicMock(),
                    raw_request=MagicMock(),
                )

        assert exc_info.value.code == 503
        background_tasks.add_task.assert_not_called()

    def test_raises_400_when_dataset_not_found(self):
        mock_langfuse = MagicMock()
        mock_langfuse.get_dataset.side_effect = Exception("no such dataset")
        background_tasks = MagicMock()

        with patch(
            f"{SERVICE_MODULE}.require_langfuse_client",
            return_value=mock_langfuse,
        ):
            with pytest.raises(ExtendedHTTPException) as exc_info:
                WorkflowEvaluationService.evaluate_workflow(
                    workflow_config=MagicMock(),
                    dataset_id="missing",
                    experiment_name="exp-1",
                    max_concurrency=1,
                    background_tasks=background_tasks,
                    user=MagicMock(),
                    raw_request=MagicMock(),
                )

        assert exc_info.value.code == 400
        background_tasks.add_task.assert_not_called()

    def test_schedules_background_task_with_arguments(self):
        mock_langfuse = MagicMock()
        mock_langfuse.get_dataset.return_value = MagicMock(items=[])
        background_tasks = MagicMock()
        workflow_config = MagicMock()
        user = MagicMock()
        raw_request = MagicMock()

        with patch(
            f"{SERVICE_MODULE}.require_langfuse_client",
            return_value=mock_langfuse,
        ):
            WorkflowEvaluationService.evaluate_workflow(
                workflow_config=workflow_config,
                dataset_id="ds-1",
                experiment_name="exp-1",
                max_concurrency=3,
                background_tasks=background_tasks,
                user=user,
                raw_request=raw_request,
            )

        background_tasks.add_task.assert_called_once()
        _, kwargs = background_tasks.add_task.call_args
        assert kwargs["dataset_id"] == "ds-1"
        assert kwargs["experiment_name"] == "exp-1"
        assert kwargs["max_concurrency"] == 3
        assert kwargs["workflow_config"] is workflow_config
        assert kwargs["user"] is user


class TestRunEvaluationTask:
    def _run(self, item_output="workflow output", stream_side_effect=None):
        """Invoke _run_evaluation_task with mocks; return the captured item_task."""
        item = MagicMock()
        item.input = "test input"

        dataset = MagicMock()

        captured = {}

        def fake_run_experiment(name, task, max_concurrency):
            captured["name"] = name
            captured["task"] = task
            captured["max_concurrency"] = max_concurrency
            # Mirror real SDK behavior: catch per-item exceptions so the experiment
            # continues; the SDK marks the item as failed rather than aborting.
            try:
                captured["result"] = task(item=item)
            except Exception as e:
                captured["item_exception"] = e
            return MagicMock()

        dataset.run_experiment.side_effect = fake_run_experiment

        mock_langfuse = MagicMock()
        mock_langfuse.get_dataset.return_value = dataset

        execution = MagicMock()
        execution.execution_id = "exec-1"

        completed = MagicMock()
        completed.output = item_output

        mock_executor = MagicMock()
        if stream_side_effect is not None:
            mock_executor.stream.side_effect = stream_side_effect

        with (
            patch(
                f"{SERVICE_MODULE}.require_langfuse_client",
                return_value=mock_langfuse,
            ),
            patch(
                f"{SERVICE_MODULE}.WorkflowService.create_workflow_execution",
                return_value=execution,
            ),
            patch(
                f"{SERVICE_MODULE}.WorkflowService.find_workflow_execution_by_id",
                return_value=completed,
            ),
            patch(
                f"{SERVICE_MODULE}.WorkflowExecutor.create_executor",
                return_value=mock_executor,
            ),
        ):
            WorkflowEvaluationService._run_evaluation_task(
                workflow_config=MagicMock(),
                dataset_id="ds-1",
                experiment_name="exp-1",
                max_concurrency=2,
                user=MagicMock(),
                raw_request=MagicMock(),
            )

        return dataset, captured, mock_executor

    def test_calls_run_experiment_with_name_and_concurrency(self):
        dataset, captured, _ = self._run()
        dataset.run_experiment.assert_called_once()
        assert captured["name"] == "exp-1"
        assert captured["max_concurrency"] == 2

    def test_item_task_returns_execution_output_on_success(self):
        _, captured, mock_executor = self._run(item_output="the answer")
        assert captured["result"] == "the answer"
        mock_executor.stream.assert_called_once()

    def test_item_task_reraises_exception_on_error(self):
        # item_task must re-raise so the SDK can capture it and mark the item as failed.
        # We verify the exception was raised at the item level, not that it propagates
        # past run_experiment (which the real SDK would catch internally).
        _, captured, _ = self._run(stream_side_effect=RuntimeError("boom"))
        assert isinstance(captured.get("item_exception"), RuntimeError)

    def test_item_task_passes_unique_uuid_as_session_id(self):
        """create_executor must receive a per-item UUID as session_id, not the experiment name."""
        import uuid as _uuid

        item = MagicMock()
        item.input = "q"
        dataset = MagicMock()

        def fake_run_experiment(name, task, max_concurrency):
            task(item=item)
            return MagicMock()

        dataset.run_experiment.side_effect = fake_run_experiment
        mock_langfuse = MagicMock()
        mock_langfuse.get_dataset.return_value = dataset

        execution = MagicMock()
        execution.execution_id = "exec-1"
        completed = MagicMock()
        completed.output = "out"

        with (
            patch(f"{SERVICE_MODULE}.require_langfuse_client", return_value=mock_langfuse),
            patch(f"{SERVICE_MODULE}.WorkflowService.create_workflow_execution", return_value=execution),
            patch(f"{SERVICE_MODULE}.WorkflowService.find_workflow_execution_by_id", return_value=completed),
            patch(
                f"{SERVICE_MODULE}.WorkflowExecutor.create_executor", return_value=MagicMock()
            ) as mock_create_executor,
        ):
            WorkflowEvaluationService._run_evaluation_task(
                workflow_config=MagicMock(),
                dataset_id="ds-1",
                experiment_name="exp-1",
                max_concurrency=1,
                user=MagicMock(),
                raw_request=MagicMock(),
            )

        _, kwargs = mock_create_executor.call_args
        session_id = kwargs["session_id"]
        _uuid.UUID(session_id)  # raises if not a valid UUID
        assert session_id != "exp-1"

        # propagate_attributes must be called with the same session_id passed to create_executor
        _, prop_kwargs = mock_langfuse.propagate_attributes.call_args
        assert prop_kwargs["session_id"] == session_id
