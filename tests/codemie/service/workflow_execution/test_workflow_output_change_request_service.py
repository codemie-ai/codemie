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

import unittest
from unittest.mock import MagicMock, patch

from codemie.core.models import TokensUsage
from codemie.service.monitoring.metrics_constants import MetricsAttributes
from codemie.service.request_summary_manager import RequestSummary
from codemie.service.spend.origin_context import current_origin
from codemie.service.spend.traffic_class import TrafficOrigin
from codemie.service.workflow_execution.workflow_output_change_request_service import WorkflowOutputChangeRequestService

_WF_MODULE = "codemie.service.workflow_execution.workflow_output_change_request_service"
_BASE_MON = "codemie.service.monitoring.base_monitoring_service"


@patch("codemie.chains.pure_chat_chain.PureChatChain.generate")
def test_run(mock_generate):
    mock_generate.return_value = MagicMock(generated="This is the changed output.")

    original_output = "This is the original output."
    changes_request = "Please change the output to be more concise."

    result = WorkflowOutputChangeRequestService.run(original_output, changes_request)

    assert result == "This is the changed output."


def _make_tokens_usage():
    return TokensUsage(
        input_tokens=80,
        output_tokens=40,
        cached_tokens=5,
        money_spent=0.003,
        cached_tokens_money_spent=0.0005,
        cached_tokens_creation_money_spent=0.0001,
    )


class TestWorkflowOutputChangeTokenMetrics(unittest.TestCase):
    def setUp(self):
        self.request_id = "req-wf-change-1"
        self.tokens = _make_tokens_usage()
        self.summary = RequestSummary(request_id=self.request_id, tokens_usage=self.tokens)

    @patch(f"{_WF_MODULE}.request_summary_manager")
    @patch(f"{_BASE_MON}.send_log_metric")
    @patch("codemie.service.request_summary_manager.request_summary_manager")
    @patch(f"{_WF_MODULE}.get_llm_by_credentials")
    @patch("codemie.chains.pure_chat_chain.PureChatChain.generate")
    def test_emits_token_attrs_on_success(
        self, mock_generate, mock_get_llm, mock_rsm_singleton, mock_send_metric, mock_rsm_svc
    ):
        mock_generate.return_value = MagicMock(generated="changed")
        mock_get_llm.return_value = MagicMock()
        mock_rsm_singleton.get_summary.return_value = self.summary

        WorkflowOutputChangeRequestService.run("original", "make shorter", request_id=self.request_id)

        mock_send_metric.assert_called_once()
        call_kwargs = mock_send_metric.call_args[1]
        attrs = call_kwargs["attributes"]
        self.assertEqual(attrs[MetricsAttributes.MONEY_SPENT], self.tokens.money_spent)
        self.assertEqual(attrs[MetricsAttributes.INPUT_TOKENS], self.tokens.input_tokens)

    @patch(f"{_WF_MODULE}.request_summary_manager")
    @patch(f"{_WF_MODULE}.emit_llm_token_metric")
    @patch(f"{_WF_MODULE}.get_llm_by_credentials")
    @patch("codemie.chains.pure_chat_chain.PureChatChain.generate")
    def test_clears_summary_in_finally_on_success(self, mock_generate, mock_get_llm, mock_emit, mock_rsm):
        mock_generate.return_value = MagicMock(generated="changed")
        mock_get_llm.return_value = MagicMock()

        WorkflowOutputChangeRequestService.run("original", "make shorter", request_id=self.request_id)

        mock_rsm.clear_summary.assert_called_once_with(self.request_id)

    @patch(f"{_WF_MODULE}.request_summary_manager")
    @patch(f"{_WF_MODULE}.emit_llm_token_metric")
    @patch(f"{_WF_MODULE}.get_llm_by_credentials")
    def test_clears_summary_in_finally_on_error(self, mock_get_llm, mock_emit, mock_rsm):
        mock_get_llm.side_effect = RuntimeError("LLM failure")

        with self.assertRaises(RuntimeError):
            WorkflowOutputChangeRequestService.run("original", "make shorter", request_id=self.request_id)

        mock_rsm.clear_summary.assert_called_once_with(self.request_id)

    @patch(f"{_WF_MODULE}.request_summary_manager")
    @patch(f"{_WF_MODULE}.emit_llm_token_metric")
    @patch(f"{_WF_MODULE}.get_llm_by_credentials")
    @patch("codemie.chains.pure_chat_chain.PureChatChain.generate")
    def test_no_clear_when_request_id_is_none(self, mock_generate, mock_get_llm, mock_emit, mock_rsm):
        mock_generate.return_value = MagicMock(generated="changed")
        mock_get_llm.return_value = MagicMock()

        WorkflowOutputChangeRequestService.run("original", "make shorter", request_id=None)

        mock_rsm.clear_summary.assert_not_called()


class TestWorkflowOutputChangeSpendOrigin(unittest.TestCase):
    """run() publishes its origin before the model it hands to PureChatChain is built."""

    @patch(f"{_WF_MODULE}.request_summary_manager")
    @patch(f"{_WF_MODULE}.emit_llm_token_metric")
    @patch(f"{_WF_MODULE}.get_llm_by_credentials")
    @patch("codemie.chains.pure_chat_chain.PureChatChain.generate")
    def test_publishes_workflow_output_change(self, mock_generate, mock_get_llm, _emit, _rsm):
        observed = []

        def record(*_args, **_kwargs):
            observed.append(current_origin())
            return MagicMock()

        mock_get_llm.side_effect = record
        mock_generate.return_value = MagicMock(generated="changed")

        WorkflowOutputChangeRequestService.run("original", "make it shorter", "req-origin-1")

        self.assertEqual(observed, [TrafficOrigin.WORKFLOW_OUTPUT_CHANGE])
        self.assertIsNone(current_origin())

    @patch(f"{_WF_MODULE}.request_summary_manager")
    @patch(f"{_WF_MODULE}.get_llm_by_credentials")
    def test_origin_is_restored_when_the_call_fails(self, mock_get_llm, _rsm):
        observed = []

        def record_and_fail(*_args, **_kwargs):
            observed.append(current_origin())
            raise RuntimeError("LLM failure")

        mock_get_llm.side_effect = record_and_fail

        with self.assertRaises(RuntimeError):
            WorkflowOutputChangeRequestService.run("original", "make it shorter", "req-origin-2")

        self.assertEqual(observed, [TrafficOrigin.WORKFLOW_OUTPUT_CHANGE])
        self.assertIsNone(current_origin())
