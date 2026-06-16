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

"""Tests for token metric emission in validate_assistant_for_publish (Gap E)."""

import unittest
from unittest.mock import MagicMock, patch

from codemie.core.models import TokensUsage
from codemie.service.analytics.metric_names import MetricName
from codemie.service.monitoring.metrics_constants import (
    MARKETPLACE_ASSISTANT_VALIDATION_TOTAL_METRIC,
    MetricsAttributes,
)
from codemie.service.request_summary_manager import RequestSummary

_SVC = "codemie.service.assistant_generator_service"
_BASE_MON = "codemie.service.monitoring.base_monitoring_service"


class TestMarketplaceValidationTokensPlatformLlmCost(unittest.TestCase):
    def test_metric_in_platform_llm_cost_terms_filter(self):
        import inspect
        import codemie.service.analytics.handlers.summary_handler as sh_module

        source = inspect.getsource(sh_module)
        # summary_handler uses PLATFORM_METRICS constant; verify the constant is referenced
        # and that it includes MARKETPLACE_ASSISTANT_VALIDATION_TOTAL
        self.assertIn("PLATFORM_METRICS", source)
        self.assertIn(
            MetricName.MARKETPLACE_ASSISTANT_VALIDATION_TOTAL,
            MetricName.PLATFORM_METRICS,
        )


def _make_tokens_usage():
    return TokensUsage(
        input_tokens=300,
        output_tokens=150,
        cached_tokens=30,
        money_spent=0.015,
        cached_tokens_money_spent=0.003,
        cached_tokens_creation_money_spent=0.0007,
    )


def _make_summary(request_id, tokens_usage=None):
    return RequestSummary(request_id=request_id, tokens_usage=tokens_usage or _make_tokens_usage())


class TestValidateAssistantForPublishTokenMetrics(unittest.TestCase):
    def setUp(self):
        self.request_id = "req-validate-1"
        self.tokens = _make_tokens_usage()
        self.summary = _make_summary(self.request_id, self.tokens)
        self.mock_user = MagicMock()
        self.mock_user.id = "user-1"
        self.mock_user.name = "Test User"
        self.mock_user.username = "testuser"
        self.mock_assistant = MagicMock()
        self.mock_assistant.id = "assistant-1"
        self.mock_assistant.name = "Test Assistant"

    @patch(f"{_SVC}.request_summary_manager")
    @patch(f"{_BASE_MON}.send_log_metric")
    @patch("codemie.service.request_summary_manager.request_summary_manager")
    @patch(f"{_SVC}.AssistantValidationWorkflow")
    def test_emits_token_attrs_on_success(self, mock_workflow_cls, mock_rsm_singleton, mock_send_metric, mock_rsm_svc):
        mock_rsm_singleton.get_summary.return_value = self.summary
        mock_workflow = MagicMock()
        mock_workflow.validate.return_value = ("accept", [])
        mock_workflow_cls.return_value = mock_workflow

        from codemie.service.assistant_generator_service import AssistantGeneratorService

        AssistantGeneratorService.validate_assistant_for_publish(
            assistant=self.mock_assistant,
            user=self.mock_user,
            request_id=self.request_id,
        )

        calls = [
            c
            for c in mock_send_metric.call_args_list
            if c[1].get("name") == MARKETPLACE_ASSISTANT_VALIDATION_TOTAL_METRIC
        ]
        self.assertEqual(len(calls), 1)
        attrs = calls[0][1]["attributes"]
        self.assertEqual(attrs[MetricsAttributes.MONEY_SPENT], self.tokens.money_spent)
        self.assertEqual(attrs[MetricsAttributes.INPUT_TOKENS], self.tokens.input_tokens)

    @patch(f"{_SVC}.request_summary_manager")
    @patch(f"{_SVC}.emit_llm_token_metric")
    @patch(f"{_SVC}.AssistantValidationWorkflow")
    def test_clears_summary_in_finally_on_success(self, mock_workflow_cls, mock_emit, mock_rsm):
        mock_workflow = MagicMock()
        mock_workflow.validate.return_value = ("accept", [])
        mock_workflow_cls.return_value = mock_workflow

        from codemie.service.assistant_generator_service import AssistantGeneratorService

        AssistantGeneratorService.validate_assistant_for_publish(
            assistant=self.mock_assistant,
            user=self.mock_user,
            request_id=self.request_id,
        )

        mock_rsm.clear_summary.assert_called_once_with(self.request_id)

    @patch(f"{_SVC}.request_summary_manager")
    @patch(f"{_SVC}.emit_llm_token_metric")
    @patch(f"{_SVC}.AssistantValidationWorkflow")
    def test_clears_summary_in_finally_on_error(self, mock_workflow_cls, mock_emit, mock_rsm):
        mock_workflow = MagicMock()
        mock_workflow.validate.side_effect = RuntimeError("validation failure")
        mock_workflow_cls.return_value = mock_workflow

        from codemie.service.assistant_generator_service import AssistantGeneratorService
        from codemie.core.exceptions import ExtendedHTTPException

        with self.assertRaises(ExtendedHTTPException):
            AssistantGeneratorService.validate_assistant_for_publish(
                assistant=self.mock_assistant,
                user=self.mock_user,
                request_id=self.request_id,
            )

        mock_rsm.clear_summary.assert_called_once_with(self.request_id)

    @patch(f"{_SVC}.request_summary_manager")
    @patch(f"{_SVC}.emit_llm_token_metric")
    @patch(f"{_SVC}.AssistantValidationWorkflow")
    def test_no_clear_when_request_id_none(self, mock_workflow_cls, mock_emit, mock_rsm):
        mock_workflow = MagicMock()
        mock_workflow.validate.return_value = ("accept", [])
        mock_workflow_cls.return_value = mock_workflow

        from codemie.service.assistant_generator_service import AssistantGeneratorService

        AssistantGeneratorService.validate_assistant_for_publish(
            assistant=self.mock_assistant,
            user=self.mock_user,
            request_id=None,
        )

        mock_rsm.clear_summary.assert_not_called()


class TestMarketplaceValidationTokensMetricConstant(unittest.TestCase):
    def test_constant_value(self):
        self.assertEqual(
            MARKETPLACE_ASSISTANT_VALIDATION_TOTAL_METRIC,
            "codemie_marketplace_assistant_validation_total",
        )

    def test_metric_name_in_spending_metrics(self):
        self.assertIn(
            MetricName.MARKETPLACE_ASSISTANT_VALIDATION_TOTAL,
            MetricName.SPENDING_METRICS,
        )
