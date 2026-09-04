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

from codemie.core.constants import CLIENT_TYPE, CODEMIE_CLI
from codemie.service.monitoring.llm_proxy_monitoring_service import (
    LLM_PROXY_USAGE,
    LLMProxyMonitoringService,
)


@pytest.fixture
def mock_user():
    user = MagicMock()
    user.id = "user-42"
    user.username = "test_user"
    return user


@pytest.fixture
def cli_request_info():
    return {
        CLIENT_TYPE: "codemie-cli",
        "session_id": "session-123",
        "request_id": "request-456",
        "llm_model": "claude-sonnet-4-5",
        "user_agent": "codemie-code/1.2.0",
        CODEMIE_CLI: "codemie-cli/1.2.0",
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


class TestIsCliRequest:
    """Tests for LLMProxyMonitoringService._is_cli_request predicate."""

    def test_codemie_cli_client_type_returns_true(self):
        assert LLMProxyMonitoringService._is_cli_request({CLIENT_TYPE: "codemie-cli"}) is True

    def test_codemie_cli_underscore_client_type_returns_true(self):
        assert LLMProxyMonitoringService._is_cli_request({CLIENT_TYPE: "codemie_cli"}) is True

    def test_cli_client_type_case_insensitive(self):
        assert LLMProxyMonitoringService._is_cli_request({CLIENT_TYPE: "CODEMIE-CLI"}) is True

    @pytest.mark.parametrize(
        "client_type",
        [
            "codemie-code",
            "codemie-claude",
            "codemie-claude-acp",
            "codemie-codex",
            "codemie-gemini",
            "codemie-opencode",
            "codemie-pi",
            "codemie-kimi",
            "codemie-kimi-acp",
            "codemie-copilot",
            "codemie-daemon",
        ],
    )
    def test_agent_client_types_with_cli_header_classify_as_cli(self, client_type):
        assert (
            LLMProxyMonitoringService._is_cli_request({CODEMIE_CLI: "codemie-cli/1.0", CLIENT_TYPE: client_type})
            is True
        )

    def test_chrome_extension_client_type_returns_false(self):
        assert LLMProxyMonitoringService._is_cli_request({CLIENT_TYPE: "codemie-chrome-extension"}) is False

    def test_nonempty_cli_header_with_non_cli_client_type_returns_false(self):
        assert (
            LLMProxyMonitoringService._is_cli_request(
                {CODEMIE_CLI: "codemie-chrome-extension/1.0", CLIENT_TYPE: "codemie-chrome-extension"}
            )
            is False
        )

    def test_unrecognized_client_type_with_cli_header_classifies_as_cli(self):
        assert (
            LLMProxyMonitoringService._is_cli_request({CODEMIE_CLI: "codemie-cli/1.0", CLIENT_TYPE: "some-other-tool"})
            is True
        )

    def test_missing_client_type_returns_false(self):
        assert LLMProxyMonitoringService._is_cli_request({}) is False

    def test_none_client_type_returns_false(self):
        assert LLMProxyMonitoringService._is_cli_request({CLIENT_TYPE: None}) is False

    def test_empty_client_type_returns_false(self):
        assert LLMProxyMonitoringService._is_cli_request({CLIENT_TYPE: ""}) is False


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
