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

"""Tests for token metric emission in SkillGeneratorService methods."""

import unittest
from unittest.mock import MagicMock, patch

from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import TokensUsage
from codemie.service.monitoring.metrics_constants import (
    SKILL_GENERATOR_TOTAL_METRIC,
    MetricsAttributes,
)
from codemie.service.request_summary_manager import RequestSummary
from codemie.service.skill_generator_service import SkillGeneratorService


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


def _make_summary(request_id="req-skill-1", tokens_usage=None):
    return RequestSummary(
        request_id=request_id,
        tokens_usage=tokens_usage or _make_tokens_usage(),
    )


class TestGenerateSkillDetailsTokenMetrics(unittest.TestCase):
    def setUp(self):
        self.mock_user = MagicMock()
        self.mock_user.id = "user-1"
        self.request_id = "req-skill-1"
        self.tokens = _make_tokens_usage()
        self.summary = _make_summary(self.request_id, self.tokens)

    def _mock_chain_response(self):
        mock_response = MagicMock()
        mock_response.name = "my-skill"
        mock_response.description = "desc"
        mock_response.instructions = "instructions"
        mock_response.categories = []
        mock_response.toolkits = []
        return mock_response

    @patch("codemie.service.skill_generator_service.get_project_for_metric", return_value="test-proj")
    @patch("codemie.service.skill_generator_service.request_summary_manager")
    @patch("codemie.service.monitoring.base_monitoring_service.send_log_metric")
    @patch("codemie.service.request_summary_manager.request_summary_manager")
    @patch("codemie.service.skill_generator_service.ToolsInfoService")
    @patch("codemie.service.skill_generator_service.get_llm_by_credentials")
    def test_emits_token_attrs_in_success_metric(
        self, mock_get_llm, mock_tools, mock_rsm_singleton, mock_send_metric, mock_rsm, mock_get_project
    ):
        mock_rsm_singleton.get_summary.return_value = self.summary

        mock_llm = MagicMock()
        mock_get_llm.return_value = mock_llm
        mock_tools.get_tools_info.return_value = []

        mock_chain = MagicMock()
        mock_llm.with_structured_output.return_value = mock_chain
        mock_chain.__ror__ = lambda self, other: mock_chain
        mock_chain.invoke.return_value = self._mock_chain_response()

        with patch("codemie.service.skill_generator_service.SKILL_GENERATOR_TEMPLATE") as mock_tmpl:
            mock_tmpl.__or__ = lambda s, other: mock_chain
            mock_chain.invoke.return_value = self._mock_chain_response()

            SkillGeneratorService.generate_skill_details(
                text="Build skill",
                user=self.mock_user,
                request_id=self.request_id,
            )

        success_call = mock_send_metric.call_args_list[0]
        attrs = success_call[1]["attributes"]
        self.assertEqual(attrs[MetricsAttributes.MONEY_SPENT], self.tokens.money_spent)
        self.assertEqual(attrs[MetricsAttributes.INPUT_TOKENS], self.tokens.input_tokens)
        self.assertEqual(attrs[MetricsAttributes.OUTPUT_TOKENS], self.tokens.output_tokens)
        self.assertEqual(attrs[MetricsAttributes.PROJECT], "test-proj")

    @patch("codemie.service.skill_generator_service.request_summary_manager")
    @patch("codemie.service.skill_generator_service.send_log_metric")
    @patch("codemie.service.skill_generator_service.ToolsInfoService")
    @patch("codemie.service.skill_generator_service.get_llm_by_credentials")
    def test_clears_summary_in_finally_on_success(self, mock_get_llm, mock_tools, mock_send_metric, mock_rsm):
        mock_rsm.get_summary.return_value = self.summary

        mock_llm = MagicMock()
        mock_get_llm.return_value = mock_llm
        mock_tools.get_tools_info.return_value = []

        with patch("codemie.service.skill_generator_service.SKILL_GENERATOR_TEMPLATE") as mock_tmpl:
            mock_chain = MagicMock()
            mock_tmpl.__or__ = lambda s, other: mock_chain
            mock_llm.with_structured_output.return_value = mock_chain
            mock_chain.__ror__ = lambda s, other: mock_chain
            mock_chain.invoke.return_value = self._mock_chain_response()

            SkillGeneratorService.generate_skill_details(
                text="Build skill",
                user=self.mock_user,
                request_id=self.request_id,
            )

        mock_rsm.clear_summary.assert_called_once_with(self.request_id)

    @patch("codemie.service.skill_generator_service.request_summary_manager")
    @patch("codemie.service.skill_generator_service.send_log_metric")
    @patch("codemie.service.skill_generator_service.get_llm_by_credentials")
    def test_clears_summary_in_finally_on_error(self, mock_get_llm, mock_send_metric, mock_rsm):
        mock_rsm.get_summary.return_value = self.summary
        mock_get_llm.side_effect = RuntimeError("LLM failure")

        with self.assertRaises(ExtendedHTTPException):
            SkillGeneratorService.generate_skill_details(
                text="Build skill",
                user=self.mock_user,
                request_id=self.request_id,
            )

        mock_rsm.clear_summary.assert_called_once_with(self.request_id)


class TestRefineSkillDetailsTokenMetrics(unittest.TestCase):
    def setUp(self):
        self.mock_user = MagicMock()
        self.mock_user.id = "user-1"
        self.request_id = "req-skill-2"
        self.tokens = _make_tokens_usage()
        self.summary = _make_summary(self.request_id, self.tokens)

    def _mock_refine_result(self):
        result = MagicMock()
        result.fields = []
        result.toolkits = []
        result.context = []
        return result

    @patch("codemie.service.skill_generator_service.get_project_for_metric", return_value="test-proj")
    @patch("codemie.service.skill_generator_service.request_summary_manager")
    @patch("codemie.service.monitoring.base_monitoring_service.send_log_metric")
    @patch("codemie.service.request_summary_manager.request_summary_manager")
    @patch("codemie.service.assistant_generator_service.PromptGeneratorChain")
    def test_emits_success_metric_with_token_attrs(
        self, mock_chain_cls, mock_rsm_singleton, mock_send_metric, mock_rsm, mock_get_project
    ):
        mock_rsm_singleton.get_summary.return_value = self.summary

        mock_chain = MagicMock()
        mock_chain_cls.from_prompt_template.return_value = mock_chain
        mock_chain.invoke_with_model.return_value = self._mock_refine_result()

        SkillGeneratorService.refine_skill_details(
            user=self.mock_user,
            request_id=self.request_id,
            name="my-skill",
        )

        # Must have a success metric call
        metric_names = [c[1]["name"] for c in mock_send_metric.call_args_list]
        self.assertIn(SKILL_GENERATOR_TOTAL_METRIC, metric_names)

        # That call must have token attrs
        success_call = next(c for c in mock_send_metric.call_args_list if c[1]["name"] == SKILL_GENERATOR_TOTAL_METRIC)
        attrs = success_call[1]["attributes"]
        self.assertEqual(attrs[MetricsAttributes.MONEY_SPENT], self.tokens.money_spent)
        self.assertEqual(attrs[MetricsAttributes.INPUT_TOKENS], self.tokens.input_tokens)
        self.assertEqual(attrs[MetricsAttributes.PROJECT], "test-proj")

    @patch("codemie.service.skill_generator_service.request_summary_manager")
    @patch("codemie.service.skill_generator_service.send_log_metric")
    @patch("codemie.service.assistant_generator_service.PromptGeneratorChain")
    def test_clears_summary_in_finally_on_success(self, mock_chain_cls, mock_send_metric, mock_rsm):
        mock_rsm.get_summary.return_value = self.summary

        mock_chain = MagicMock()
        mock_chain_cls.from_prompt_template.return_value = mock_chain
        mock_chain.invoke_with_model.return_value = self._mock_refine_result()

        SkillGeneratorService.refine_skill_details(
            user=self.mock_user,
            request_id=self.request_id,
        )

        mock_rsm.clear_summary.assert_called_once_with(self.request_id)

    @patch("codemie.service.skill_generator_service.request_summary_manager")
    @patch("codemie.service.skill_generator_service.send_log_metric")
    @patch("codemie.service.assistant_generator_service.PromptGeneratorChain")
    def test_clears_summary_in_finally_on_error(self, mock_chain_cls, mock_send_metric, mock_rsm):
        mock_rsm.get_summary.return_value = self.summary

        mock_chain = MagicMock()
        mock_chain_cls.from_prompt_template.return_value = mock_chain
        mock_chain.invoke_with_model.side_effect = RuntimeError("LLM failure")

        with self.assertRaises(ExtendedHTTPException):
            SkillGeneratorService.refine_skill_details(
                user=self.mock_user,
                request_id=self.request_id,
            )

        mock_rsm.clear_summary.assert_called_once_with(self.request_id)


if __name__ == "__main__":
    unittest.main()
