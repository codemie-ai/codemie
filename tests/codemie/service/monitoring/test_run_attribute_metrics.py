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

"""Run attributes reach the metric log record (and so Elasticsearch) but never the OpenTelemetry counter."""

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from codemie.core.models import TokensUsage
from codemie.core.workflow_models import WorkflowExecutionStatusEnum
from codemie.rest_api.models.assistant import Assistant
from codemie.rest_api.models.base import ConversationStatus
from codemie.rest_api.security.user import User
from codemie.service.monitoring.base_monitoring_service import BaseMonitoringService
from codemie.service.monitoring.conversation_monitoring_service import ConversationMonitoringService
from codemie.service.monitoring.workflow_monitoring_service import WorkflowMonitoringService

ATTRIBUTES = {"team": "core", "com.epam.gen_ai.service.work_item.id": "ABC-123"}
RECORD_FIELDS = {
    "run_attribute_keys": ["com.epam.gen_ai.service.work_item.id", "team"],
    "run_attribute_pairs": ["com.epam.gen_ai.service.work_item.id=ABC-123", "team=core"],
}


@pytest.fixture
def sinks():
    counter = MagicMock()
    with (
        patch("codemie.service.monitoring.base_monitoring_service.send_log_metric") as send_log_metric,
        patch("codemie.service.monitoring.base_monitoring_service.metrics.get_meter") as get_meter,
    ):
        get_meter.return_value.create_counter.return_value = counter
        yield SimpleNamespace(log=send_log_metric, counter=counter)


def _logged(sinks) -> list[dict]:
    return [call.args[1] for call in sinks.log.call_args_list]


def _counted(sinks) -> list[dict]:
    return [call.args[1] for call in sinks.counter.add.call_args_list]


@pytest.fixture
def user():
    return User(name="User One", username="user@example.com", id="user-1")


def test_send_count_metric_logs_extra_fields_without_counting_them(sinks):
    BaseMonitoringService.send_count_metric(
        name="some_metric", attributes={"user_id": "user-1"}, extra_log_fields=RECORD_FIELDS
    )

    assert _logged(sinks)[0]["run_attribute_pairs"] == RECORD_FIELDS["run_attribute_pairs"]
    assert not RECORD_FIELDS.keys() & _counted(sinks)[0].keys()


class TestConversationUsageMetric:
    @pytest.fixture
    def assistant(self):
        return Assistant(
            name="Helper",
            description="",
            project="proj",
            toolkits=[],
            system_prompt="",
            llm_model_type="test_model",
            slug="helper",
        )

    def _send(self, user, assistant, run_attributes):
        ConversationMonitoringService.send_conversation_metric(
            user=user,
            assistant=assistant,
            tokens_usage=TokensUsage(input_tokens=10, output_tokens=20, money_spent=0.01),
            time_elapsed=1.5,
            conversation_id="conv-1",
            llm_model="test-model",
            status=ConversationStatus.SUCCESS,
            run_attributes=run_attributes,
        )

    def test_record_carries_sorted_keys_and_pairs(self, sinks, user, assistant):
        self._send(user, assistant, ATTRIBUTES)

        logged = _logged(sinks)[0]
        assert {key: logged[key] for key in RECORD_FIELDS} == RECORD_FIELDS
        assert not RECORD_FIELDS.keys() & _counted(sinks)[0].keys()

    def test_record_without_attributes_is_unchanged(self, sinks, user, assistant):
        self._send(user, assistant, None)
        without = _logged(sinks)[0]
        self._send(user, assistant, ATTRIBUTES)
        with_attributes = _logged(sinks)[1]

        assert not RECORD_FIELDS.keys() & without.keys()
        assert {key: value for key, value in with_attributes.items() if key not in RECORD_FIELDS} == without


class TestWorkflowExecutionMetric:
    @pytest.fixture
    def workflow_config(self):
        return MagicMock(project="proj", mode="sequential")

    @pytest.fixture
    def execution(self):
        start = datetime(2026, 11, 2, 10, 0, 0)
        return MagicMock(
            overall_status=WorkflowExecutionStatusEnum.SUCCEEDED,
            execution_id="exec-1",
            date=start,
            update_date=start + timedelta(seconds=3),
            tokens_usage=TokensUsage(input_tokens=10, output_tokens=20, money_spent=0.01),
        )

    def test_aggregated_record_carries_attributes(self, sinks, user, workflow_config, execution):
        WorkflowMonitoringService.send_workflow_execution_metric(
            workflow_execution_config=execution,
            workflow_config=workflow_config,
            user=user,
            run_attributes=ATTRIBUTES,
        )

        logged = _logged(sinks)[0]
        assert {key: logged[key] for key in RECORD_FIELDS} == RECORD_FIELDS
        assert not RECORD_FIELDS.keys() & _counted(sinks)[0].keys()

    def test_every_per_llm_run_record_carries_attributes_after_the_row_is_gone(self, sinks, user, workflow_config):
        llm_run = SimpleNamespace(
            llm_model="gpt-5",
            input_tokens=5,
            output_tokens=7,
            cached_tokens=0,
            money_spent=0.001,
            cached_tokens_money_spent=0.0,
        )
        summary = SimpleNamespace(llm_runs=[llm_run, llm_run])

        with patch("codemie.service.request_summary_manager.request_summary_manager.get_summary", return_value=summary):
            WorkflowMonitoringService.send_workflow_execution_metric(
                workflow_execution_config=None,
                workflow_config=workflow_config,
                user=user,
                request_id="exec-1",
                status=WorkflowExecutionStatusEnum.FAILED,
                run_attributes=ATTRIBUTES,
            )

        logged = _logged(sinks)
        assert len(logged) == 2
        assert all({key: record[key] for key in RECORD_FIELDS} == RECORD_FIELDS for record in logged)
        assert all(not RECORD_FIELDS.keys() & counted.keys() for counted in _counted(sinks))
