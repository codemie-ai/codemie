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

import inspect
from unittest.mock import patch

import pytest

from codemie.rest_api import core_gating, main


_CORE_GATING_TESTS = (
    "test_startup_logs_missing_settings_and_core_off",
    "test_startup_logs_provider_check_passed_and_provider",
    "test_no_core_gating_check_or_log_when_standalone_off",
    "test_core_gating_lines_distinct_from_legacy_line",
)


@pytest.fixture
def caplog(caplog):
    """The project logger does not propagate to root, so attach caplog's handler to it directly."""
    main.logger.addHandler(caplog.handler)
    yield caplog
    main.logger.removeHandler(caplog.handler)


@pytest.fixture(autouse=True)
def _core_gating_quiet(request):
    """Keep the Core gating lines out of the logger-mock assertions of the older tests."""
    if request.node.name in _CORE_GATING_TESTS:
        yield
        return
    with patch.object(main, "_log_core_gating", create=True):
        yield


@pytest.fixture(autouse=True)
def _code_executor_available():
    """Keep the Code Executor warning out of the Mermaid/MCP warning-count assertions."""
    with patch("codemie.configs.code_executor_availability.code_executor_available", return_value=True):
        yield


def test_gate_is_noop_when_flag_off():
    with patch.object(main.config, "STANDALONE", False), patch.object(main, "logger") as log:
        main._run_standalone_startup()
    assert log.method_calls == []


def test_gate_logs_once_when_flag_on():
    with patch.object(main.config, "STANDALONE", True), patch.object(main, "logger") as log:
        main._run_standalone_startup()
    log.info.assert_called_once()


def test_lifespan_calls_gate_exactly_once():
    assert inspect.getsource(main.lifespan).count("_run_standalone_startup()") == 1


def test_warns_when_mermaid_not_configured():
    with (
        patch.object(main.config, "STANDALONE", True),
        patch.object(main.config, "MERMAID_SERVER_URL", ""),
        patch.object(main.config, "MERMAID_USE_MERMAID_INC", False),
        patch.object(main, "logger") as log,
    ):
        main._run_standalone_startup()
    log.warning.assert_called_once()
    msg = log.warning.call_args[0][0]
    assert "MERMAID_SERVER_URL" in msg
    assert "MERMAID_USE_MERMAID_INC" in msg
    for feature in (
        "chat diagrams",
        "workflow graph preview",
        "stored workflow diagram image",
        "conversation export diagrams",
    ):
        assert feature in msg


def test_no_warning_when_mermaid_configured():
    with (
        patch.object(main.config, "STANDALONE", True),
        patch.object(main.config, "MERMAID_SERVER_URL", "http://mermaid:8082"),
        patch.object(main.config, "MERMAID_USE_MERMAID_INC", False),
        patch.object(main, "logger") as log,
    ):
        main._run_standalone_startup()
    log.warning.assert_not_called()


def test_no_warning_when_only_mermaid_ink_enabled():
    with (
        patch.object(main.config, "STANDALONE", True),
        patch.object(main.config, "MERMAID_SERVER_URL", ""),
        patch.object(main.config, "MERMAID_USE_MERMAID_INC", True),
        patch.object(main, "logger") as log,
    ):
        main._run_standalone_startup()
    log.warning.assert_not_called()


def test_warns_when_mcp_url_empty_and_enabled():
    with (
        patch.object(main.config, "STANDALONE", True),
        patch.object(main.config, "MCP_CONNECT_ENABLED", True),
        patch.object(main.config, "MCP_CONNECT_URL", ""),
        patch.object(main, "logger") as log,
    ):
        main._run_standalone_startup()
    log.warning.assert_called_once()


def test_no_warning_when_mcp_deliberately_disabled():
    with (
        patch.object(main.config, "STANDALONE", True),
        patch.object(main.config, "MCP_CONNECT_ENABLED", False),
        patch.object(main.config, "MCP_CONNECT_URL", ""),
        patch.object(main, "logger") as log,
    ):
        main._run_standalone_startup()
    log.warning.assert_not_called()


def test_warns_google_oauth_disabled_default():
    """Default GOOGLE_OAUTH_ENABLED (False) + no Redis: warn that Redis is not configured."""
    with (
        patch.object(main.config, "STANDALONE", True),
        patch.object(main.config, "REDIS_HOST", ""),
        patch.object(main.config, "GOOGLE_OAUTH_ENABLED", False),
        patch.object(main.config, "WEBHOOK_RATE_LIMIT_ENABLED", False),
        patch.object(main.config, "MERMAID_SERVER_URL", "http://mermaid:8082"),
        patch.object(main.config, "MCP_CONNECT_ENABLED", False),
        patch.object(main, "logger") as log,
    ):
        main._run_standalone_startup()
    log.warning.assert_called_once()
    assert "Redis is not configured" in log.warning.call_args[0][0]


def test_warns_google_oauth_requires_redis():
    """GOOGLE_OAUTH_ENABLED=True + no Redis: different wording, citing Redis is required."""
    with (
        patch.object(main.config, "STANDALONE", True),
        patch.object(main.config, "REDIS_HOST", ""),
        patch.object(main.config, "GOOGLE_OAUTH_ENABLED", True),
        patch.object(main.config, "WEBHOOK_RATE_LIMIT_ENABLED", False),
        patch.object(main.config, "MERMAID_SERVER_URL", "http://mermaid:8082"),
        patch.object(main.config, "MCP_CONNECT_ENABLED", False),
        patch.object(main, "logger") as log,
    ):
        main._run_standalone_startup()
    log.warning.assert_called_once()
    assert "Redis is required" in log.warning.call_args[0][0]


def test_warns_webhook_rate_limit_disabled():
    """WEBHOOK_RATE_LIMIT_ENABLED=True + no Redis: a separate one-time warning fires too."""
    with (
        patch.object(main.config, "STANDALONE", True),
        patch.object(main.config, "REDIS_HOST", ""),
        patch.object(main.config, "GOOGLE_OAUTH_ENABLED", False),
        patch.object(main.config, "WEBHOOK_RATE_LIMIT_ENABLED", True),
        patch.object(main.config, "MERMAID_SERVER_URL", "http://mermaid:8082"),
        patch.object(main.config, "MCP_CONNECT_ENABLED", False),
        patch.object(main, "logger") as log,
    ):
        main._run_standalone_startup()
    assert log.warning.call_count == 2
    messages = [call.args[0] for call in log.warning.call_args_list]
    assert any("Redis is not configured" in msg for msg in messages)
    assert any("webhook rate limiting" in msg.lower() and "Redis is not configured" in msg for msg in messages)


def test_no_warning_when_mcp_url_configured():
    with (
        patch.object(main.config, "STANDALONE", True),
        patch.object(main.config, "MCP_CONNECT_ENABLED", True),
        patch.object(main.config, "MCP_CONNECT_URL", "http://mcp-connect:3000"),
        patch.object(main, "logger") as log,
    ):
        main._run_standalone_startup()
    log.warning.assert_not_called()


def _run_ce(standalone, available=False, flag=False, op=False, cluster=False):
    with (
        patch.object(main, "config") as cfg,
        patch.object(main, "logger"),
        patch("codemie.configs.code_executor_availability.logger") as log,
        patch("codemie.service.file_service.mermaid_service.MermaidService.is_configured", return_value=True),
        patch("codemie.configs.code_executor_availability.code_executor_available", return_value=available),
        patch("codemie.configs.customer_config.CustomerConfig.configured_feature_enabled", return_value=flag),
        patch(
            "codemie_tools.data_management.file_system.toolkit.FileSystemToolkit._is_code_executor_enabled",
            return_value=op,
        ),
        patch(
            "codemie_tools.data_management.code_executor.k8s_client_manager.cluster_connection_configured",
            return_value=cluster,
        ),
    ):
        cfg.STANDALONE = standalone
        cfg.MCP_CONNECT_ENABLED = False
        main._run_standalone_startup()
    return [c.args[0] for c in log.warning.call_args_list]


def test_warns_listing_every_missing_item():
    (msg,) = _run_ce(True)
    for text in (
        "chat Code Executor",
        "workspace script runner",
        "feature flag off",
        "operator not enabled",
        "no cluster",
    ):
        assert text in msg


def test_lists_only_missing():
    (msg,) = _run_ce(True, flag=True, op=True)
    assert "no cluster" in msg and "feature flag off" not in msg and "operator" not in msg


def test_silent_when_available_or_not_standalone():
    assert _run_ce(True, available=True) == []
    assert _run_ce(False) == []


def _gating_records(caplog):
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith("Core gating:")]


def _run_on(caplog, result):
    caplog.set_level("INFO")
    with (
        patch.object(main.config, "STANDALONE", True),
        patch.object(core_gating, "core_availability", return_value=result),
    ):
        main._run_standalone_startup()
    return _gating_records(caplog)


def test_startup_logs_missing_settings_and_core_off(caplog):
    msgs = _run_on(caplog, (False, None, ["AWS_REGION", "OPENAI_API_KEY"]))
    assert len(msgs) == 1
    assert "AWS_REGION" in msgs[0] and "OPENAI_API_KEY" in msgs[0]
    assert "Chats, Assistants and Workflows are off" in msgs[0]


def test_startup_logs_provider_check_passed_and_provider(caplog):
    msgs = _run_on(caplog, (True, "bedrock", []))
    assert len(msgs) == 1
    assert "check passed" in msgs[0] and "bedrock" in msgs[0]


def test_no_core_gating_check_or_log_when_standalone_off(caplog):
    caplog.set_level("INFO")
    with (
        patch.object(main.config, "STANDALONE", False),
        patch.object(core_gating, "core_availability") as ca,
    ):
        main._run_standalone_startup()
    ca.assert_not_called()
    assert _gating_records(caplog) == []


def test_core_gating_lines_distinct_from_legacy_line(caplog):
    msgs = _run_on(caplog, (False, None, ["X"]))
    assert msgs and all("Model provider readiness" not in m for m in msgs)
