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

"""Tests for token metric emission in SkillService.generate_instructions."""

import unittest
from unittest.mock import MagicMock, patch

from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import TokensUsage
from codemie.service.monitoring.metrics_constants import (
    SKILL_GENERATOR_TOTAL_METRIC,
    MetricsAttributes,
)
from codemie.service.request_summary_manager import RequestSummary
from codemie.service.skill_service import SkillService
from codemie.service.spend.origin_context import current_origin
from codemie.service.spend.traffic_class import TrafficOrigin

_SKILL_SVC = "codemie.service.skill_service"


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


def _make_summary(request_id="req-inst-1", tokens_usage=None):
    return RequestSummary(
        request_id=request_id,
        tokens_usage=tokens_usage or _make_tokens_usage(),
    )


class TestGenerateInstructionsForwardsRequestId(unittest.TestCase):
    """request_id must reach get_llm_by_credentials so TokensCalculationCallback attaches."""

    def setUp(self):
        self.mock_user = MagicMock()
        self.mock_user.id = "user-1"
        self.request_id = "req-inst-1"
        self.tokens = _make_tokens_usage()
        self.summary = _make_summary(self.request_id, self.tokens)

    @patch(f"{_SKILL_SVC}.request_summary_manager")
    @patch(f"{_SKILL_SVC}.emit_llm_token_metric")
    @patch(f"{_SKILL_SVC}.SkillMonitoringService")
    @patch(f"{_SKILL_SVC}.SkillService._invoke_llm_and_validate")
    @patch(f"{_SKILL_SVC}.get_llm_by_credentials")
    def test_request_id_forwarded_to_get_llm_by_credentials(
        self,
        mock_get_llm,
        mock_invoke,
        mock_monitoring,
        mock_emit,
        mock_rsm,
    ):
        mock_get_llm.return_value = MagicMock()
        mock_invoke.return_value = "## Overview\n\nGenerated instructions"

        SkillService.generate_instructions(
            description="A skill for testing",
            user=self.mock_user,
            request_id=self.request_id,
        )

        _, kwargs = mock_get_llm.call_args
        self.assertEqual(kwargs.get("request_id"), self.request_id)


class TestGenerateInstructionsTokenMetrics(unittest.TestCase):
    def setUp(self):
        self.mock_user = MagicMock()
        self.mock_user.id = "user-1"
        self.request_id = "req-inst-1"
        self.tokens = _make_tokens_usage()
        self.summary = _make_summary(self.request_id, self.tokens)

    @patch(f"{_SKILL_SVC}.request_summary_manager")
    @patch(f"{_SKILL_SVC}.emit_llm_token_metric")
    @patch(f"{_SKILL_SVC}.SkillMonitoringService")
    @patch(f"{_SKILL_SVC}.SkillService._invoke_llm_and_validate")
    @patch(f"{_SKILL_SVC}.get_llm_by_credentials")
    def test_emits_token_metric_on_success(
        self,
        mock_get_llm,
        mock_invoke,
        mock_monitoring,
        mock_emit,
        mock_rsm,
    ):
        mock_get_llm.return_value = MagicMock()
        mock_invoke.return_value = "## Overview\n\nGenerated instructions"

        SkillService.generate_instructions(
            description="A skill for testing",
            user=self.mock_user,
            request_id=self.request_id,
        )

        mock_emit.assert_called_once()
        call_kwargs = mock_emit.call_args[1]
        self.assertEqual(call_kwargs["name"], SKILL_GENERATOR_TOTAL_METRIC)
        self.assertEqual(call_kwargs["request_id"], self.request_id)
        self.assertIn(MetricsAttributes.LLM_MODEL, call_kwargs["base_attributes"])

    @patch(f"{_SKILL_SVC}.request_summary_manager")
    @patch(f"{_SKILL_SVC}.emit_llm_token_metric")
    @patch(f"{_SKILL_SVC}.SkillMonitoringService")
    @patch(f"{_SKILL_SVC}.SkillService._invoke_llm_and_validate")
    @patch(f"{_SKILL_SVC}.get_llm_by_credentials")
    def test_clears_summary_in_finally_on_success(
        self,
        mock_get_llm,
        mock_invoke,
        mock_monitoring,
        mock_emit,
        mock_rsm,
    ):
        mock_get_llm.return_value = MagicMock()
        mock_invoke.return_value = "## Overview\n\nGenerated instructions"

        SkillService.generate_instructions(
            description="A skill for testing",
            user=self.mock_user,
            request_id=self.request_id,
        )

        mock_rsm.clear_summary.assert_called_once_with(self.request_id)

    @patch(f"{_SKILL_SVC}.request_summary_manager")
    @patch(f"{_SKILL_SVC}.emit_llm_token_metric")
    @patch(f"{_SKILL_SVC}.SkillMonitoringService")
    @patch(f"{_SKILL_SVC}.get_llm_by_credentials")
    def test_clears_summary_in_finally_on_error(
        self,
        mock_get_llm,
        mock_monitoring,
        mock_emit,
        mock_rsm,
    ):
        mock_get_llm.side_effect = RuntimeError("LLM failure")

        with self.assertRaises(ExtendedHTTPException):
            SkillService.generate_instructions(
                description="A skill for testing",
                user=self.mock_user,
                request_id=self.request_id,
            )

        mock_rsm.clear_summary.assert_called_once_with(self.request_id)

    @patch(f"{_SKILL_SVC}.request_summary_manager")
    @patch(f"{_SKILL_SVC}.emit_llm_token_metric")
    @patch(f"{_SKILL_SVC}.SkillMonitoringService")
    @patch(f"{_SKILL_SVC}.SkillService._invoke_llm_and_validate")
    @patch(f"{_SKILL_SVC}.get_llm_by_credentials")
    def test_no_clear_when_request_id_is_none(
        self,
        mock_get_llm,
        mock_invoke,
        mock_monitoring,
        mock_emit,
        mock_rsm,
    ):
        mock_get_llm.return_value = MagicMock()
        mock_invoke.return_value = "## Overview\n\nGenerated instructions"

        SkillService.generate_instructions(
            description="A skill for testing",
            user=self.mock_user,
            request_id=None,
        )

        mock_rsm.clear_summary.assert_not_called()


if __name__ == "__main__":
    unittest.main()


class TestGenerateInstructionsSpendOrigin(unittest.TestCase):
    """generate_instructions publishes the skill generator origin before the model is built."""

    def setUp(self):
        self.mock_user = MagicMock()
        self.mock_user.id = "user-1"
        self.observed = []

    @patch(f"{_SKILL_SVC}.request_summary_manager")
    @patch(f"{_SKILL_SVC}.emit_llm_token_metric")
    @patch(f"{_SKILL_SVC}.SkillMonitoringService")
    @patch(f"{_SKILL_SVC}.SkillService._invoke_llm_and_validate")
    @patch(f"{_SKILL_SVC}.get_llm_by_credentials")
    def test_publishes_skill_generator(self, mock_get_llm, mock_invoke, _monitoring, _emit, _rsm):
        def record(*_args, **_kwargs):
            self.observed.append(current_origin())
            return MagicMock()

        mock_get_llm.side_effect = record
        mock_invoke.return_value = "## Overview\n\nGenerated instructions"

        SkillService.generate_instructions(description="A skill for testing", user=self.mock_user)

        self.assertEqual(self.observed, [TrafficOrigin.SKILL_GENERATOR])
        self.assertIsNone(current_origin())

    @patch(f"{_SKILL_SVC}.request_summary_manager")
    @patch(f"{_SKILL_SVC}.SkillMonitoringService")
    @patch(f"{_SKILL_SVC}.get_llm_by_credentials")
    def test_origin_is_restored_when_generation_fails(self, mock_get_llm, _monitoring, _rsm):
        def record_and_fail(*_args, **_kwargs):
            self.observed.append(current_origin())
            raise RuntimeError("LLM failure")

        mock_get_llm.side_effect = record_and_fail

        with self.assertRaises(ExtendedHTTPException):
            SkillService.generate_instructions(description="A skill for testing", user=self.mock_user)

        self.assertEqual(self.observed, [TrafficOrigin.SKILL_GENERATOR])
        self.assertIsNone(current_origin())
