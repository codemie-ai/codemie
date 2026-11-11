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

"""Tests for LLMProxyMonitoringService CLI usage tracking."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from codemie.core.constants import CODEMIE_CLI, CHROME_EXTENSION
from codemie.service.monitoring.llm_proxy_monitoring_service import (
    LLM_PROXY_USAGE,
    LLMProxyMonitoringService,
)


@pytest.fixture
def mock_user():
    user = MagicMock()
    user.id = "user-42"
    user.username = "test_user"
    user.email = "test_user@example.com"
    return user


@pytest.fixture
def cli_request_info():
    return {
        "client_type": "codemie-claude",
        "session_id": "session-123",
        "request_id": "request-456",
        "llm_model": "claude-sonnet-4-5",
        "user_agent": "codemie-code/1.2.0",
        CODEMIE_CLI: "codemie-claude/1.2.0",
    }


@pytest.fixture
def non_cli_request_info():
    return {
        "client_type": "web",
        "session_id": "session-789",
        "request_id": "request-012",
        "llm_model": "gpt-4",
        "user_agent": "Mozilla/5.0",
        CODEMIE_CLI: "",
    }


@pytest.fixture
def chrome_extension_request_info():
    return {
        "client_type": "codemie-chrome-extension",
        "session_id": "session-345",
        "request_id": "request-678",
        "llm_model": "gpt-4",
        "user_agent": "Mozilla/5.0",
        CODEMIE_CLI: "",
        CHROME_EXTENSION: "0.3.2",
    }


class TestIsCliRequest:
    """Tests for _is_cli_request helper."""

    def test_cli_header_truthy(self):
        assert LLMProxyMonitoringService._is_cli_request({CODEMIE_CLI: "codemie-claude/1.0"}) is True

    def test_cli_header_empty_string(self):
        assert LLMProxyMonitoringService._is_cli_request({CODEMIE_CLI: ""}) is False

    def test_cli_header_missing(self):
        assert LLMProxyMonitoringService._is_cli_request({}) is False

    def test_cli_header_none(self):
        assert LLMProxyMonitoringService._is_cli_request({CODEMIE_CLI: None}) is False


class TestTrackUsage:
    """Tests for track_usage metric emission."""

    @patch.object(LLMProxyMonitoringService, "send_count_metric")
    def test_emits_proxy_metric_name(self, mock_send, mock_user, non_cli_request_info):
        LLMProxyMonitoringService.track_usage(
            user=mock_user,
            endpoint="/v1/chat/completions",
            request_info=non_cli_request_info,
            llm_model="gpt-4",
            input_tokens=100,
            output_tokens=50,
            cached_tokens=0,
            money_spent=0.02,
            cached_tokens_money_spent=0.0,
            status_code=200,
        )

        mock_send.assert_called_once()
        metric_name = mock_send.call_args.kwargs["name"]
        assert metric_name == LLM_PROXY_USAGE
        assert metric_name == "codemie_litellm_proxy_usage"

    @patch.object(LLMProxyMonitoringService, "send_count_metric")
    def test_uses_standard_token_attribute_names(self, mock_send, mock_user, non_cli_request_info):
        LLMProxyMonitoringService.track_usage(
            user=mock_user,
            endpoint="/v1/chat/completions",
            request_info=non_cli_request_info,
            llm_model="gpt-4",
            input_tokens=100,
            output_tokens=50,
            cached_tokens=0,
            money_spent=0.02,
            cached_tokens_money_spent=0.0,
            status_code=200,
        )

        attributes = mock_send.call_args.kwargs["attributes"]
        assert attributes["input_tokens"] == 100
        assert attributes["output_tokens"] == 50
        assert "total_input_tokens" not in attributes
        assert "total_output_tokens" not in attributes

    @patch.object(LLMProxyMonitoringService, "send_count_metric")
    def test_includes_cache_creation_tokens_attribute(self, mock_send, mock_user, non_cli_request_info):
        LLMProxyMonitoringService.track_usage(
            user=mock_user,
            endpoint="/v1/chat/completions",
            request_info=non_cli_request_info,
            llm_model="claude-sonnet-4-5",
            input_tokens=100,
            output_tokens=50,
            cached_tokens=20,
            money_spent=0.01,
            cached_tokens_money_spent=0.001,
            status_code=200,
            cache_creation_tokens=15,
        )

        attributes = mock_send.call_args.kwargs["attributes"]
        assert attributes["cache_creation_tokens"] == 15

    @patch.object(LLMProxyMonitoringService, "send_count_metric")
    def test_cli_request_sets_cli_flag_true(self, mock_send, mock_user, cli_request_info):
        LLMProxyMonitoringService.track_usage(
            user=mock_user,
            endpoint="/v1/chat/completions",
            request_info=cli_request_info,
            llm_model="claude-sonnet-4-5",
            input_tokens=100,
            output_tokens=50,
            cached_tokens=20,
            money_spent=0.01,
            cached_tokens_money_spent=0.001,
            status_code=200,
            cache_creation_tokens=10,
        )

        attributes = mock_send.call_args.kwargs["attributes"]
        assert attributes["cli_request"] is True

    @patch.object(LLMProxyMonitoringService, "send_count_metric")
    def test_non_cli_request_sets_cli_flag_false(self, mock_send, mock_user, non_cli_request_info):
        LLMProxyMonitoringService.track_usage(
            user=mock_user,
            endpoint="/v1/chat/completions",
            request_info=non_cli_request_info,
            llm_model="gpt-4",
            input_tokens=100,
            output_tokens=50,
            cached_tokens=0,
            money_spent=0.02,
            cached_tokens_money_spent=0.0,
            status_code=200,
        )

        attributes = mock_send.call_args.kwargs["attributes"]
        assert attributes["cli_request"] is False

    @patch.object(LLMProxyMonitoringService, "send_count_metric")
    def test_emits_codemie_request_id_from_request_info(self, mock_send, mock_user, cli_request_info):
        LLMProxyMonitoringService.track_usage(
            user=mock_user,
            endpoint="/v1/chat/completions",
            request_info=cli_request_info,
            llm_model="claude-sonnet-4-5",
            input_tokens=100,
            output_tokens=50,
            cached_tokens=0,
            money_spent=0.01,
            cached_tokens_money_spent=0.0,
            status_code=200,
        )

        attributes = mock_send.call_args.kwargs["attributes"]
        # AD-29's two-stage join needs codemie_request_id on the metric side, matching
        # the ledger's codemie_request_id column.
        assert attributes["request_id"] == "request-456"

    @patch.object(LLMProxyMonitoringService, "send_count_metric")
    def test_emits_run_id_attribute_when_present_in_request_info(self, mock_send, mock_user, non_cli_request_info):
        request_info = {**non_cli_request_info, "run_id": "run-789"}

        LLMProxyMonitoringService.track_usage(
            user=mock_user,
            endpoint="/v1/chat/completions",
            request_info=request_info,
            llm_model="gpt-4",
            input_tokens=100,
            output_tokens=50,
            cached_tokens=0,
            money_spent=0.02,
            cached_tokens_money_spent=0.0,
            status_code=200,
        )

        attributes = mock_send.call_args.kwargs["attributes"]
        assert attributes["run_id"] == "run-789"

    @patch.object(LLMProxyMonitoringService, "send_count_metric")
    def test_run_id_absent_from_request_info_emits_none(self, mock_send, mock_user, non_cli_request_info):
        # request_info carries no run_id today - the attribute must still be present on
        # the metric (as None) so the key exists on the emission side ahead of the id
        # actually being populated upstream.
        LLMProxyMonitoringService.track_usage(
            user=mock_user,
            endpoint="/v1/chat/completions",
            request_info=non_cli_request_info,
            llm_model="gpt-4",
            input_tokens=100,
            output_tokens=50,
            cached_tokens=0,
            money_spent=0.02,
            cached_tokens_money_spent=0.0,
            status_code=200,
        )

        attributes = mock_send.call_args.kwargs["attributes"]
        assert "run_id" in attributes
        assert attributes["run_id"] is None

    @patch.object(LLMProxyMonitoringService, "send_count_metric")
    def test_settled_cost_is_emitted_unchanged(self, mock_send, mock_user, non_cli_request_info):
        LLMProxyMonitoringService.track_usage(
            user=mock_user,
            endpoint="/v1/chat/completions",
            request_info=non_cli_request_info,
            llm_model="gpt-4",
            input_tokens=100,
            output_tokens=50,
            cached_tokens=0,
            money_spent=0.0001474,
            cached_tokens_money_spent=0.0,
            status_code=200,
        )

        attributes = mock_send.call_args.kwargs["attributes"]
        # LiteLLM's own recorded cost, passed through - not rounded and not re-priced.
        assert attributes["money_spent"] == 0.0001474

    @patch.object(LLMProxyMonitoringService, "send_count_metric")
    def test_unsettled_cost_emits_no_money_attribute_at_all(self, mock_send, mock_user, non_cli_request_info):
        # Nothing re-emits this document once the ledger settles, so a zero written here
        # would freeze into metric history uncorrectably. The key must be absent instead.
        LLMProxyMonitoringService.track_usage(
            user=mock_user,
            endpoint="/v1/chat/completions",
            request_info=non_cli_request_info,
            llm_model="claude-sonnet-5",
            input_tokens=100,
            output_tokens=50,
            cached_tokens=0,
            money_spent=None,
            cached_tokens_money_spent=0.0,
            status_code=200,
        )

        attributes = mock_send.call_args.kwargs["attributes"]
        assert "money_spent" not in attributes
        # No zero was substituted for the missing figure under some other name either. A
        # blanket "no 0.0 anywhere" sweep cannot express this: the zero token counts are
        # legitimate measurements and compare equal to 0.0, and the cache breakdown is a
        # ratified zero on this path rather than a fabricated one.
        assert {key: value for key, value in attributes.items() if "money" in key} == {"cached_tokens_money_spent": 0.0}

    @patch.object(LLMProxyMonitoringService, "send_count_metric")
    def test_token_counts_survive_an_unsettled_cost(self, mock_send, mock_user, non_cli_request_info):
        # The counts are final at this point; only the money is provisional, and dropping
        # them along with the figure would lose measurements that will never be re-emitted.
        LLMProxyMonitoringService.track_usage(
            user=mock_user,
            endpoint="/v1/chat/completions",
            request_info=non_cli_request_info,
            llm_model="claude-sonnet-5",
            input_tokens=17,
            output_tokens=10,
            cached_tokens=3,
            money_spent=None,
            cached_tokens_money_spent=0.0,
            status_code=200,
            cache_creation_tokens=7,
        )

        attributes = mock_send.call_args.kwargs["attributes"]
        assert attributes["input_tokens"] == 17
        assert attributes["output_tokens"] == 10
        assert attributes["cache_read_input_tokens"] == 3
        assert attributes["cache_creation_tokens"] == 7

    @patch.object(LLMProxyMonitoringService, "send_count_metric")
    def test_cached_tokens_money_spent_is_emitted_unconditionally(self, mock_send, mock_user, non_cli_request_info):
        # This breakdown is identically zero on the LiteLLM path and this is its only
        # emitter: gating it too would break the historical Elasticsearch mapping.
        LLMProxyMonitoringService.track_usage(
            user=mock_user,
            endpoint="/v1/chat/completions",
            request_info=non_cli_request_info,
            llm_model="claude-sonnet-5",
            input_tokens=100,
            output_tokens=50,
            cached_tokens=20,
            money_spent=None,
            cached_tokens_money_spent=0.0,
            status_code=200,
        )

        attributes = mock_send.call_args.kwargs["attributes"]
        assert attributes["cached_tokens_money_spent"] == 0.0

    @patch.object(LLMProxyMonitoringService, "send_count_metric")
    @patch("codemie.service.monitoring.llm_proxy_monitoring_service.logger")
    def test_debug_log_does_not_raise_on_unsettled_cost(self, mock_logger, mock_send, mock_user, non_cli_request_info):
        # track_usage swallows its own exceptions, so a TypeError from formatting None would
        # silently degrade to a warning instead of emitting the metric.
        LLMProxyMonitoringService.track_usage(
            user=mock_user,
            endpoint="/v1/chat/completions",
            request_info=non_cli_request_info,
            llm_model="claude-sonnet-5",
            input_tokens=100,
            output_tokens=50,
            cached_tokens=0,
            money_spent=None,
            cached_tokens_money_spent=0.0,
            status_code=200,
        )

        mock_send.assert_called_once()
        mock_logger.warning.assert_not_called()
        assert "cost=unsettled" in mock_logger.debug.call_args.args[0]

    @patch.object(LLMProxyMonitoringService, "send_count_metric")
    def test_missing_codemie_cli_key_sets_cli_flag_false(self, mock_send, mock_user):
        LLMProxyMonitoringService.track_usage(
            user=mock_user,
            endpoint="/v1/embeddings",
            request_info={"client_type": "web", "session_id": "s-1"},
            llm_model="text-embedding-ada",
            input_tokens=50,
            output_tokens=0,
            cached_tokens=0,
            money_spent=0.0,
            cached_tokens_money_spent=0.0,
            status_code=200,
        )

        attributes = mock_send.call_args.kwargs["attributes"]
        assert attributes["cli_request"] is False

    @patch.object(LLMProxyMonitoringService, "send_count_metric")
    def test_chrome_extension_request_tags_attribute_and_is_not_cli(
        self, mock_send, mock_user, chrome_extension_request_info
    ):
        """Extension traffic is tagged chrome_extension=true, cli_request=false — billed as
        PLATFORM (see TestResolveNonPremiumTrackingIdentity) but still distinguishable in ES."""
        LLMProxyMonitoringService.track_usage(
            user=mock_user,
            endpoint="/v1/chat/completions",
            request_info=chrome_extension_request_info,
            llm_model="gpt-4",
            input_tokens=100,
            output_tokens=50,
            cached_tokens=0,
            money_spent=0.02,
            cached_tokens_money_spent=0.0,
            status_code=200,
        )

        attributes = mock_send.call_args.kwargs["attributes"]
        assert attributes["chrome_extension_request"] is True
        assert attributes[CHROME_EXTENSION] == "0.3.2"
        assert attributes["cli_request"] is False

    @patch.object(LLMProxyMonitoringService, "send_count_metric")
    def test_non_extension_request_tags_attribute_false(self, mock_send, mock_user, non_cli_request_info):
        LLMProxyMonitoringService.track_usage(
            user=mock_user,
            endpoint="/v1/chat/completions",
            request_info=non_cli_request_info,
            llm_model="gpt-4",
            input_tokens=100,
            output_tokens=50,
            cached_tokens=0,
            money_spent=0.02,
            cached_tokens_money_spent=0.0,
            status_code=200,
        )

        attributes = mock_send.call_args.kwargs["attributes"]
        assert attributes["chrome_extension_request"] is False

    @patch.object(LLMProxyMonitoringService, "send_count_metric", side_effect=RuntimeError("ES down"))
    def test_swallows_exceptions(self, mock_send, mock_user, non_cli_request_info):
        # Should not raise
        LLMProxyMonitoringService.track_usage(
            user=mock_user,
            endpoint="/v1/chat/completions",
            request_info=non_cli_request_info,
            llm_model="gpt-4",
            input_tokens=10,
            output_tokens=5,
            cached_tokens=0,
            money_spent=0.0,
            cached_tokens_money_spent=0.0,
            status_code=200,
        )


class TestTrackUsageBudgetFallback:
    """EPMCDME-15111 AC6/AC10: unfunded project → analytics show personal, flagged as fallback."""

    def _track(self, mock_user, request_info):
        LLMProxyMonitoringService.track_usage(
            user=mock_user,
            endpoint="/v1/chat/completions",
            request_info=request_info,
            llm_model="gpt-4",
            input_tokens=10,
            output_tokens=5,
            cached_tokens=0,
            money_spent=0.01,
            cached_tokens_money_spent=0.0,
            status_code=200,
        )

    @patch.object(LLMProxyMonitoringService, "send_count_metric")
    def test_fallback_reports_personal_project_with_marker(self, mock_send, mock_user, cli_request_info):
        cli_request_info.update({"project": "philips-hr", "budget_fallback_from": "philips-hr"})

        self._track(mock_user, cli_request_info)

        attributes = mock_send.call_args.kwargs["attributes"]
        assert attributes["project"] == "test_user@example.com"
        assert attributes["budget_fallback_from"] == "philips-hr"

    @patch.object(LLMProxyMonitoringService, "send_count_metric")
    def test_funded_project_is_reported_unchanged(self, mock_send, mock_user, cli_request_info):
        cli_request_info["project"] = "philips-hr"

        self._track(mock_user, cli_request_info)

        attributes = mock_send.call_args.kwargs["attributes"]
        assert attributes["project"] == "philips-hr"
        assert "budget_fallback_from" not in attributes


class TestTrackUsageFallbackKind:
    """EPMCDME-16625: analytics show the charged project and tell default-project and personal fallbacks apart."""

    _track = TestTrackUsageBudgetFallback._track

    @patch.object(LLMProxyMonitoringService, "send_count_metric")
    def test_default_project_fallback_reports_the_charged_default_project(self, mock_send, mock_user, cli_request_info):
        cli_request_info.update(
            {"project": "team-d", "budget_fallback_from": "team-a", "budget_fallback_kind": "default_project"}
        )

        self._track(mock_user, cli_request_info)

        attributes = mock_send.call_args.kwargs["attributes"]
        assert attributes["project"] == "team-d"
        assert attributes["budget_fallback_from"] == "team-a"
        assert attributes["budget_fallback_kind"] == "default_project"

    @patch.object(LLMProxyMonitoringService, "send_count_metric")
    def test_personal_fallback_reports_the_personal_identity(self, mock_send, mock_user, cli_request_info):
        cli_request_info.update(
            {"project": "test_user@example.com", "budget_fallback_from": "team-a", "budget_fallback_kind": "personal"}
        )

        self._track(mock_user, cli_request_info)

        attributes = mock_send.call_args.kwargs["attributes"]
        # The web path names the personal budget by the email-named personal project; CLI analytics match it.
        assert attributes["project"] == "test_user@example.com"
        assert attributes["budget_fallback_kind"] == "personal"

    @patch.object(LLMProxyMonitoringService, "send_count_metric")
    def test_personal_fallback_without_email_reports_the_username(self, mock_send, mock_user, cli_request_info):
        mock_user.email = None
        cli_request_info.update(
            {"project": "team-a", "budget_fallback_from": "team-a", "budget_fallback_kind": "personal"}
        )

        self._track(mock_user, cli_request_info)

        assert mock_send.call_args.kwargs["attributes"]["project"] == "test_user"

    @patch.object(LLMProxyMonitoringService, "send_count_metric")
    def test_ignored_header_project_is_not_reported(self, mock_send, mock_user, cli_request_info):
        cli_request_info.update({"project": "team-d", "cli_requested_project": "foreign"})

        self._track(mock_user, cli_request_info)

        attributes = mock_send.call_args.kwargs["attributes"]
        assert "foreign" not in attributes.values()
