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

"""Tests for WorkflowMonitoringService execution metric emission (AD-29)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from codemie.core.workflow_models import WorkflowExecutionStatusEnum, WorkflowMode
from codemie.service.monitoring.metrics_constants import MetricsAttributes
from codemie.service.monitoring.workflow_monitoring_service import WorkflowMonitoringService
from codemie.service.request_summary_manager import LLMRun, RequestSummary


@pytest.fixture
def mock_user():
    user = MagicMock()
    user.id = "user-1"
    user.name = "Test User"
    user.username = "test_user"
    return user


@pytest.fixture
def workflow_config():
    config = MagicMock()
    config.id = "workflow-42"
    config.name = "My Workflow"
    config.project = "demo"
    config.mode = WorkflowMode.SEQUENTIAL
    return config


class TestBuildExecutionBaseAttributes:
    """AD-29 obligation (a): codemie_workflow_id must be emitted."""

    def test_includes_workflow_id_attribute(self, mock_user, workflow_config):
        attributes = WorkflowMonitoringService._build_execution_base_attributes(
            workflow_config, mock_user, WorkflowExecutionStatusEnum.SUCCEEDED, "exec-1"
        )

        assert attributes[MetricsAttributes.WORKFLOW_ID] == "workflow-42"
        # WORKFLOW_NAME stays - AD-29 does not remove it, it adds the stable id alongside it.
        assert attributes[MetricsAttributes.WORKFLOW_NAME] == "My Workflow"


class TestSendPerRunMetrics:
    """AD-28/AD-29: a metric emitted while cost is pending carries no money attribute."""

    @patch.object(WorkflowMonitoringService, "send_count_metric")
    def test_omits_money_attribute_for_unsettled_run(self, mock_send, mock_user, workflow_config):
        summary = RequestSummary(
            request_id="req-1",
            llm_runs=[
                LLMRun(
                    run_id="run-1",
                    input_tokens=10,
                    output_tokens=5,
                    money_spent=None,
                    llm_model="gpt-4",
                    created_at=datetime.now(timezone.utc),
                )
            ],
        )
        with patch(
            "codemie.service.request_summary_manager.request_summary_manager.get_summary",
            return_value=summary,
        ):
            sent = WorkflowMonitoringService._send_per_run_metrics(
                "req-1", workflow_config, mock_user, WorkflowExecutionStatusEnum.IN_PROGRESS, "exec-1", 1.5, None
            )

        assert sent is True
        attributes = mock_send.call_args.kwargs["attributes"]
        assert MetricsAttributes.MONEY_SPENT not in attributes

    @patch.object(WorkflowMonitoringService, "send_count_metric")
    def test_includes_money_attribute_for_settled_run(self, mock_send, mock_user, workflow_config):
        summary = RequestSummary(
            request_id="req-2",
            llm_runs=[
                LLMRun(
                    run_id="run-2",
                    input_tokens=10,
                    output_tokens=5,
                    money_spent=0.05,
                    llm_model="gpt-4",
                    created_at=datetime.now(timezone.utc),
                )
            ],
        )
        with patch(
            "codemie.service.request_summary_manager.request_summary_manager.get_summary",
            return_value=summary,
        ):
            WorkflowMonitoringService._send_per_run_metrics(
                "req-2", workflow_config, mock_user, WorkflowExecutionStatusEnum.SUCCEEDED, "exec-2", 1.5, None
            )

        attributes = mock_send.call_args.kwargs["attributes"]
        assert attributes[MetricsAttributes.MONEY_SPENT] == 0.05

    @patch.object(WorkflowMonitoringService, "send_count_metric")
    def test_per_run_metric_includes_workflow_id(self, mock_send, mock_user, workflow_config):
        summary = RequestSummary(
            request_id="req-3",
            llm_runs=[
                LLMRun(
                    run_id="run-3",
                    input_tokens=1,
                    output_tokens=1,
                    money_spent=0.01,
                    llm_model="gpt-4",
                    created_at=datetime.now(timezone.utc),
                )
            ],
        )
        with patch(
            "codemie.service.request_summary_manager.request_summary_manager.get_summary",
            return_value=summary,
        ):
            WorkflowMonitoringService._send_per_run_metrics(
                "req-3", workflow_config, mock_user, WorkflowExecutionStatusEnum.SUCCEEDED, "exec-3", None, None
            )

        attributes = mock_send.call_args.kwargs["attributes"]
        assert attributes[MetricsAttributes.WORKFLOW_ID] == "workflow-42"
