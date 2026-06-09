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

"""Tests for token metric emission in AssistantGeneratorService methods."""

import unittest
from unittest.mock import MagicMock, patch

from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import TokensUsage
from codemie.service.assistant_generator_service import AssistantGeneratorService
from codemie.service.monitoring.metrics_constants import (
    MetricsAttributes,
)
from codemie.service.request_summary_manager import RequestSummary


def _make_tokens_usage(
    input_tokens=100,
    output_tokens=50,
    cached_tokens=10,
    money_spent=0.005,
    cached_tokens_money_spent=0.001,
    cached_tokens_creation_money_spent=0.0002,
):
    return TokensUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_tokens=cached_tokens,
        money_spent=money_spent,
        cached_tokens_money_spent=cached_tokens_money_spent,
        cached_tokens_creation_money_spent=cached_tokens_creation_money_spent,
    )


def _make_summary(request_id="req-123", tokens_usage=None):
    summary = RequestSummary(
        request_id=request_id,
        tokens_usage=tokens_usage or _make_tokens_usage(),
    )
    return summary


class TestGenerateAssistantDetailsTokenMetrics(unittest.TestCase):
    def setUp(self):
        self.mock_user = MagicMock()
        self.mock_user.id = "user-1"
        self.mock_user.username = "test@example.com"
        self.mock_user.current_project = "proj"
        self.request_id = "req-123"
        self.tokens = _make_tokens_usage()
        self.summary = _make_summary(self.request_id, self.tokens)

    @patch("codemie.service.assistant_generator_service.get_project_for_metric", return_value="test-proj")
    @patch("codemie.service.assistant_generator_service.request_summary_manager")
    @patch("codemie.service.monitoring.base_monitoring_service.send_log_metric")
    @patch("codemie.service.request_summary_manager.request_summary_manager")
    @patch("codemie.service.assistant_generator_service.PromptGeneratorChain")
    def test_emits_token_attrs_in_success_metric(
        self, mock_chain_cls, mock_rsm_singleton, mock_send_metric, mock_rsm, mock_get_project
    ):
        mock_rsm_singleton.get_summary.return_value = self.summary

        mock_chain = MagicMock()
        mock_chain_cls.from_prompt_template.return_value = mock_chain
        mock_response = MagicMock()
        mock_response.name = "Bot"
        mock_response.description = "desc"
        mock_response.categories = []
        mock_response.conversation_starters = ["a", "b", "c", "d"]
        mock_response.system_prompt = "prompt"
        mock_response.toolkits = []
        mock_chain.invoke_with_model.return_value = mock_response

        AssistantGeneratorService.generate_assistant_details(
            text="Build assistant",
            user=self.mock_user,
            request_id=self.request_id,
        )

        success_call = mock_send_metric.call_args_list[0]
        attrs = success_call[1]["attributes"]
        self.assertEqual(attrs[MetricsAttributes.INPUT_TOKENS], self.tokens.input_tokens)
        self.assertEqual(attrs[MetricsAttributes.OUTPUT_TOKENS], self.tokens.output_tokens)
        self.assertEqual(attrs[MetricsAttributes.CACHE_READ_INPUT_TOKENS], self.tokens.cached_tokens)
        self.assertEqual(attrs[MetricsAttributes.MONEY_SPENT], self.tokens.money_spent)
        self.assertEqual(attrs[MetricsAttributes.CACHED_TOKENS_MONEY_SPENT], self.tokens.cached_tokens_money_spent)
        self.assertEqual(
            attrs[MetricsAttributes.CACHE_CREATION_TOKENS_MONEY_SPENT],
            self.tokens.cached_tokens_creation_money_spent,
        )
        self.assertEqual(attrs[MetricsAttributes.PROJECT], "test-proj")

    @patch("codemie.service.assistant_generator_service.request_summary_manager")
    @patch("codemie.service.assistant_generator_service.send_log_metric")
    @patch("codemie.service.assistant_generator_service.PromptGeneratorChain")
    def test_clears_summary_in_finally_on_success(self, mock_chain_cls, mock_send_metric, mock_rsm):
        mock_rsm.get_summary.return_value = self.summary

        mock_chain = MagicMock()
        mock_chain_cls.from_prompt_template.return_value = mock_chain
        mock_response = MagicMock()
        mock_response.name = "Bot"
        mock_response.description = "desc"
        mock_response.categories = []
        mock_response.conversation_starters = ["a", "b", "c", "d"]
        mock_response.system_prompt = "prompt"
        mock_response.toolkits = []
        mock_chain.invoke_with_model.return_value = mock_response

        AssistantGeneratorService.generate_assistant_details(
            text="Build assistant",
            user=self.mock_user,
            request_id=self.request_id,
        )

        mock_rsm.clear_summary.assert_called_once_with(self.request_id)

    @patch("codemie.service.assistant_generator_service.request_summary_manager")
    @patch("codemie.service.assistant_generator_service.send_log_metric")
    @patch("codemie.service.assistant_generator_service.PromptGeneratorChain")
    def test_clears_summary_in_finally_on_error(self, mock_chain_cls, mock_send_metric, mock_rsm):
        mock_rsm.get_summary.return_value = self.summary

        mock_chain = MagicMock()
        mock_chain_cls.from_prompt_template.return_value = mock_chain
        mock_chain.invoke_with_model.side_effect = RuntimeError("LLM failure")

        with self.assertRaises(ExtendedHTTPException):
            AssistantGeneratorService.generate_assistant_details(
                text="Build assistant",
                user=self.mock_user,
                request_id=self.request_id,
            )

        mock_rsm.clear_summary.assert_called_once_with(self.request_id)


class TestGenerateAssistantPromptTokenMetrics(unittest.TestCase):
    def setUp(self):
        self.mock_user = MagicMock()
        self.mock_user.id = "user-1"
        self.mock_user.username = "test@example.com"
        self.mock_user.current_project = "proj"
        self.request_id = "req-456"
        self.tokens = _make_tokens_usage()
        self.summary = _make_summary(self.request_id, self.tokens)

    @patch("codemie.service.assistant_generator_service.get_project_for_metric", return_value="test-proj")
    @patch("codemie.service.assistant_generator_service.request_summary_manager")
    @patch("codemie.service.monitoring.base_monitoring_service.send_log_metric")
    @patch("codemie.service.request_summary_manager.request_summary_manager")
    @patch("codemie.service.assistant_generator_service.PromptGeneratorChain")
    def test_emits_token_attrs_in_success_metric(
        self, mock_chain_cls, mock_rsm_singleton, mock_send_metric, mock_rsm, mock_get_project
    ):
        mock_rsm_singleton.get_summary.return_value = self.summary

        mock_chain = MagicMock()
        mock_chain_cls.from_prompt_template.return_value = mock_chain
        mock_chain.invoke_with_model.return_value = MagicMock(system_prompt="Generated prompt")

        AssistantGeneratorService.generate_assistant_prompt(
            user=self.mock_user,
            text="Create coding assistant",
            request_id=self.request_id,
        )

        success_call = mock_send_metric.call_args_list[0]
        attrs = success_call[1]["attributes"]
        self.assertEqual(attrs[MetricsAttributes.MONEY_SPENT], self.tokens.money_spent)
        self.assertEqual(attrs[MetricsAttributes.INPUT_TOKENS], self.tokens.input_tokens)
        self.assertEqual(attrs[MetricsAttributes.PROJECT], "test-proj")

    @patch("codemie.service.assistant_generator_service.request_summary_manager")
    @patch("codemie.service.assistant_generator_service.send_log_metric")
    @patch("codemie.service.assistant_generator_service.PromptGeneratorChain")
    def test_clears_summary_in_finally_on_success(self, mock_chain_cls, mock_send_metric, mock_rsm):
        mock_rsm.get_summary.return_value = self.summary

        mock_chain = MagicMock()
        mock_chain_cls.from_prompt_template.return_value = mock_chain
        mock_chain.invoke_with_model.return_value = MagicMock(system_prompt="Generated prompt")

        AssistantGeneratorService.generate_assistant_prompt(
            user=self.mock_user,
            text="Create coding assistant",
            request_id=self.request_id,
        )

        mock_rsm.clear_summary.assert_called_once_with(self.request_id)

    @patch("codemie.service.assistant_generator_service.request_summary_manager")
    @patch("codemie.service.assistant_generator_service.send_log_metric")
    @patch("codemie.service.assistant_generator_service.PromptGeneratorChain")
    def test_clears_summary_in_finally_on_error(self, mock_chain_cls, mock_send_metric, mock_rsm):
        mock_rsm.get_summary.return_value = self.summary

        mock_chain = MagicMock()
        mock_chain_cls.from_prompt_template.return_value = mock_chain
        mock_chain.invoke_with_model.side_effect = RuntimeError("LLM failure")

        with self.assertRaises(ExtendedHTTPException):
            AssistantGeneratorService.generate_assistant_prompt(
                user=self.mock_user,
                text="Create coding assistant",
                request_id=self.request_id,
            )

        mock_rsm.clear_summary.assert_called_once_with(self.request_id)


class TestGenerateRefinePromptTokenMetrics(unittest.TestCase):
    def setUp(self):
        self.mock_user = MagicMock()
        self.mock_user.id = "user-1"
        self.mock_user.username = "test@example.com"
        self.mock_user.current_project = "proj"
        self.request_id = "req-789"
        self.tokens = _make_tokens_usage()
        self.summary = _make_summary(self.request_id, self.tokens)

    def _make_refine_details(self):
        from codemie.service.assistant_generator_service import RefinePromptDetails

        return RefinePromptDetails(
            name="Test",
            description="Test desc",
            categories=[],
            system_prompt="Old prompt",
            conversation_starters=["q1", "q2", "q3", "q4"],
            toolkits=[],
            context=[],
        )

    @patch("codemie.service.assistant_generator_service.get_project_for_metric", return_value="test-proj")
    @patch("codemie.service.assistant_generator_service.request_summary_manager")
    @patch("codemie.service.monitoring.base_monitoring_service.send_log_metric")
    @patch("codemie.service.request_summary_manager.request_summary_manager")
    @patch("codemie.service.assistant_generator_service.PromptGeneratorChain")
    def test_emits_token_attrs_in_success_metric(
        self, mock_chain_cls, mock_rsm_singleton, mock_send_metric, mock_rsm, mock_get_project
    ):
        mock_rsm_singleton.get_summary.return_value = self.summary

        mock_chain = MagicMock()
        mock_chain_cls.from_prompt_template.return_value = mock_chain
        mock_result = MagicMock()
        mock_result.fields = []
        mock_result.toolkits = []
        mock_result.context = []
        mock_chain.invoke_with_model.return_value = mock_result

        AssistantGeneratorService.generate_refine_prompt(
            user=self.mock_user,
            request_id=self.request_id,
            refine_details=self._make_refine_details(),
        )

        success_call = mock_send_metric.call_args_list[0]
        attrs = success_call[1]["attributes"]
        self.assertEqual(attrs[MetricsAttributes.MONEY_SPENT], self.tokens.money_spent)
        self.assertEqual(attrs[MetricsAttributes.INPUT_TOKENS], self.tokens.input_tokens)
        self.assertEqual(attrs[MetricsAttributes.OUTPUT_TOKENS], self.tokens.output_tokens)
        self.assertEqual(attrs[MetricsAttributes.PROJECT], "test-proj")

    @patch("codemie.service.assistant_generator_service.request_summary_manager")
    @patch("codemie.service.assistant_generator_service.send_log_metric")
    @patch("codemie.service.assistant_generator_service.PromptGeneratorChain")
    def test_clears_summary_in_finally_on_success(self, mock_chain_cls, mock_send_metric, mock_rsm):
        mock_rsm.get_summary.return_value = self.summary

        mock_chain = MagicMock()
        mock_chain_cls.from_prompt_template.return_value = mock_chain
        mock_result = MagicMock()
        mock_result.fields = []
        mock_result.toolkits = []
        mock_result.context = []
        mock_chain.invoke_with_model.return_value = mock_result

        AssistantGeneratorService.generate_refine_prompt(
            user=self.mock_user,
            request_id=self.request_id,
            refine_details=self._make_refine_details(),
        )

        mock_rsm.clear_summary.assert_called_once_with(self.request_id)

    @patch("codemie.service.assistant_generator_service.request_summary_manager")
    @patch("codemie.service.assistant_generator_service.send_log_metric")
    @patch("codemie.service.assistant_generator_service.PromptGeneratorChain")
    def test_clears_summary_in_finally_on_error(self, mock_chain_cls, mock_send_metric, mock_rsm):
        mock_rsm.get_summary.return_value = self.summary

        mock_chain = MagicMock()
        mock_chain_cls.from_prompt_template.return_value = mock_chain
        mock_chain.invoke_with_model.side_effect = RuntimeError("LLM failure")

        with self.assertRaises(ExtendedHTTPException):
            AssistantGeneratorService.generate_refine_prompt(
                user=self.mock_user,
                request_id=self.request_id,
                refine_details=self._make_refine_details(),
            )

        mock_rsm.clear_summary.assert_called_once_with(self.request_id)


if __name__ == "__main__":
    unittest.main()
