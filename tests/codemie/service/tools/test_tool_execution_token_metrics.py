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

"""Tests for token metric emission and spend-origin binding in ToolExecutionService LLM call sites."""

import unittest
from unittest.mock import MagicMock, patch

from codemie.core.models import TokensUsage
from codemie.rest_api.models.index import IndexInfo
from codemie.rest_api.models.tool import DatasourceSearchInvokeRequest, ToolInvokeRequest
from codemie.service.monitoring.metrics_constants import MetricsAttributes
from codemie.service.request_summary_manager import RequestSummary
from codemie.service.spend.origin_context import current_origin
from codemie.service.spend.traffic_class import TrafficOrigin
from codemie.service.tools.tool_execution_service import ToolExecutionService

_SVC = "codemie.service.tools.tool_execution_service"
_BASE_MON = "codemie.service.monitoring.base_monitoring_service"


def _make_tokens_usage():
    return TokensUsage(
        input_tokens=200,
        output_tokens=100,
        cached_tokens=20,
        money_spent=0.01,
        cached_tokens_money_spent=0.002,
        cached_tokens_creation_money_spent=0.0005,
    )


def _make_summary(request_id, tokens_usage=None):
    return RequestSummary(request_id=request_id, tokens_usage=tokens_usage or _make_tokens_usage())


class TestInvokeFileAnalysisToolTokenMetrics(unittest.TestCase):
    def setUp(self):
        self.request_id = "req-file-tool-1"
        self.tokens = _make_tokens_usage()
        self.summary = _make_summary(self.request_id, self.tokens)
        self.request = ToolInvokeRequest(
            project="test-project",
            request_id=self.request_id,
            tool_args={"file_names": ["https://example.com/file.pdf"]},
        )

    @patch(f"{_SVC}.request_summary_manager")
    @patch(f"{_BASE_MON}.send_log_metric")
    @patch("codemie.service.request_summary_manager.request_summary_manager")
    @patch(f"{_SVC}.get_llm_by_credentials")
    @patch(f"{_SVC}.build_unique_file_objects_list")
    @patch(f"{_SVC}.FileAnalysisToolkit")
    def test_emits_token_attrs_on_success(
        self, mock_fa_toolkit, mock_build_files, mock_get_llm, mock_rsm_singleton, mock_send_metric, mock_rsm_svc
    ):
        mock_rsm_singleton.get_summary.return_value = self.summary
        mock_get_llm.return_value = MagicMock()
        mock_build_files.return_value = [MagicMock()]

        mock_toolkit_instance = MagicMock()
        mock_fa_toolkit.get_toolkit.return_value = mock_toolkit_instance
        mock_tool = MagicMock()
        mock_tool.execute.return_value = "file analysis result"
        mock_toolkit_instance.get_tools.return_value = [mock_tool]

        with patch("codemie.service.tools.tool_service.ToolsService.find_tool", return_value=mock_tool):
            with patch(f"{_SVC}.ToolExecutionService.validate_tool_args"):
                ToolExecutionService.invoke_file_analysis_tool(self.request, "file_analysis")

        mock_send_metric.assert_called_once()
        attrs = mock_send_metric.call_args[1]["attributes"]
        self.assertEqual(attrs[MetricsAttributes.MONEY_SPENT], self.tokens.money_spent)
        self.assertEqual(attrs[MetricsAttributes.INPUT_TOKENS], self.tokens.input_tokens)

    @patch(f"{_SVC}.request_summary_manager")
    @patch(f"{_SVC}.emit_llm_token_metric")
    @patch(f"{_SVC}.get_llm_by_credentials")
    @patch(f"{_SVC}.build_unique_file_objects_list")
    @patch(f"{_SVC}.FileAnalysisToolkit")
    def test_clears_summary_in_finally_on_success(
        self, mock_fa_toolkit, mock_build_files, mock_get_llm, mock_emit, mock_rsm
    ):
        mock_get_llm.return_value = MagicMock()
        mock_build_files.return_value = [MagicMock()]

        mock_toolkit_instance = MagicMock()
        mock_fa_toolkit.get_toolkit.return_value = mock_toolkit_instance
        mock_tool = MagicMock()
        mock_tool.execute.return_value = "result"
        mock_toolkit_instance.get_tools.return_value = [mock_tool]

        with patch("codemie.service.tools.tool_service.ToolsService.find_tool", return_value=mock_tool):
            with patch(f"{_SVC}.ToolExecutionService.validate_tool_args"):
                ToolExecutionService.invoke_file_analysis_tool(self.request, "file_analysis")

        mock_rsm.clear_summary.assert_called_once_with(self.request_id)

    @patch(f"{_SVC}.request_summary_manager")
    @patch(f"{_SVC}.emit_llm_token_metric")
    @patch(f"{_SVC}.get_llm_by_credentials")
    def test_clears_summary_in_finally_on_error(self, mock_get_llm, mock_emit, mock_rsm):
        mock_get_llm.side_effect = RuntimeError("LLM failure")

        request = ToolInvokeRequest(
            project="test-project",
            request_id=self.request_id,
            tool_args={"file_names": ["https://example.com/file.pdf"]},
        )

        with self.assertRaises(RuntimeError):
            ToolExecutionService.invoke_file_analysis_tool(request, "file_analysis")

        mock_rsm.clear_summary.assert_called_once_with(self.request_id)


class TestInvokeToolWithDirectCredsGitTokenMetrics(unittest.TestCase):
    """Token tracking for Git toolkit tools via invoke_tool_with_direct_creds."""

    def setUp(self):
        self.request_id = "req-git-tool-1"
        self.tokens = _make_tokens_usage()
        self.summary = _make_summary(self.request_id, self.tokens)

    @patch(f"{_SVC}.request_summary_manager")
    @patch(f"{_SVC}.ToolExecutionService._create_toolkit_instance")
    @patch(f"{_SVC}.ToolExecutionService.validate_tool_args")
    @patch(f"{_SVC}.ToolDiscoveryService")
    def test_clears_summary_in_finally_on_success(self, mock_discovery, mock_validate, mock_create_toolkit, mock_rsm):
        mock_tool = MagicMock()
        mock_tool.execute.return_value = "git result"

        mock_toolkit = MagicMock()
        mock_toolkit.get_tools.return_value = [mock_tool]
        mock_create_toolkit.return_value = mock_toolkit

        mock_discovery.find_tool_by_name.return_value = MagicMock()

        with patch("codemie.service.tools.tool_service.ToolsService.find_tool", return_value=mock_tool):
            request = ToolInvokeRequest(
                project="test",
                request_id=self.request_id,
                tool_creds={"token": "abc"},
            )
            ToolExecutionService.invoke_tool_with_direct_creds(request, "git_push")

        mock_rsm.clear_summary.assert_called_once_with(self.request_id)

    @patch(f"{_SVC}.request_summary_manager")
    @patch(f"{_SVC}.ToolExecutionService._create_toolkit_instance")
    @patch(f"{_SVC}.ToolExecutionService.validate_tool_args")
    @patch(f"{_SVC}.ToolDiscoveryService")
    def test_clears_summary_in_finally_on_error(self, mock_discovery, mock_validate, mock_create_toolkit, mock_rsm):
        mock_tool = MagicMock()
        mock_tool.execute.side_effect = RuntimeError("git failure")

        mock_toolkit = MagicMock()
        mock_toolkit.get_tools.return_value = [mock_tool]
        mock_create_toolkit.return_value = mock_toolkit

        mock_discovery.find_tool_by_name.return_value = MagicMock()

        with patch("codemie.service.tools.tool_service.ToolsService.find_tool", return_value=mock_tool):
            request = ToolInvokeRequest(
                project="test",
                request_id=self.request_id,
                tool_creds={"token": "abc"},
            )
            with self.assertRaises(RuntimeError):
                ToolExecutionService.invoke_tool_with_direct_creds(request, "git_push")

        mock_rsm.clear_summary.assert_called_once_with(self.request_id)

    @patch(f"{_SVC}.request_summary_manager")
    @patch(f"{_BASE_MON}.send_log_metric")
    @patch("codemie.service.request_summary_manager.request_summary_manager")
    @patch(f"{_SVC}.ToolExecutionService._create_toolkit_instance")
    @patch(f"{_SVC}.ToolExecutionService.validate_tool_args")
    @patch(f"{_SVC}.ToolDiscoveryService")
    def test_emits_token_metric_on_success(
        self, mock_discovery, mock_validate, mock_create_toolkit, mock_rsm_singleton, mock_send_metric, mock_rsm_svc
    ):
        mock_rsm_singleton.get_summary.return_value = self.summary
        mock_tool = MagicMock()
        mock_tool.execute.return_value = "git result"
        mock_toolkit = MagicMock()
        mock_toolkit.get_tools.return_value = [mock_tool]
        mock_create_toolkit.return_value = mock_toolkit
        mock_discovery.find_tool_by_name.return_value = MagicMock()

        with patch("codemie.service.tools.tool_service.ToolsService.find_tool", return_value=mock_tool):
            request = ToolInvokeRequest(
                project="test",
                request_id=self.request_id,
                tool_creds={"token": "abc"},
            )
            ToolExecutionService.invoke_tool_with_direct_creds(request, "git_push")

        calls = [c for c in mock_send_metric.call_args_list if c[1].get("name") == "codemie_tools_usage_tokens"]
        self.assertEqual(len(calls), 1)
        attrs = calls[0][1]["attributes"]
        self.assertEqual(attrs[MetricsAttributes.MONEY_SPENT], self.tokens.money_spent)
        self.assertEqual(attrs[MetricsAttributes.INPUT_TOKENS], self.tokens.input_tokens)


class TestGetGitToolsPassesRequestUuid(unittest.TestCase):
    """Gap 2: _get_git_tools must forward request.request_id, not hardcode ''."""

    @patch(f"{_SVC}.ToolkitSettingService.get_git_tools_with_creds")
    @patch(f"{_SVC}.ToolsService.find_tool")
    def test_passes_request_uuid_from_request(self, mock_find_tool, mock_get_git_tools):
        from codemie.core.models import CodeFields

        request_id = "req-git-123"
        mock_request = MagicMock()
        mock_request.request_id = request_id
        mock_request.project = "proj"
        mock_request.llm_model = "gpt-4"
        mock_assistant = MagicMock()
        mock_user = MagicMock()
        mock_get_git_tools.return_value = []
        mock_find_tool.return_value = MagicMock()

        ToolExecutionService._get_git_tools(
            code_fields=CodeFields(app_name="app", repo_name="repo", index_type="code"),
            assistant=mock_assistant,
            request=mock_request,
            tool_name="git_push",
            user=mock_user,
        )

        _, kwargs = mock_get_git_tools.call_args
        self.assertEqual(kwargs["request_uuid"], request_id)


class TestInvokeToolWithSystemIntegrationTokenMetrics(unittest.TestCase):
    """Gap 3: invoke_tool_with_system_integration must inject REQUEST_ID + emit/clear."""

    def setUp(self):
        self.request_id = "req-sysint-1"
        self.tokens = _make_tokens_usage()
        self.summary = _make_summary(self.request_id, self.tokens)

    @patch(f"{_SVC}.VirtualAssistantService")
    @patch(f"{_SVC}.ToolExecutionService._get_context_tools")
    @patch(f"{_SVC}.ToolkitService.get_toolkit_methods")
    def test_injects_request_id_in_tool_metadata(self, mock_toolkit_methods, mock_get_context, mock_vas):
        from codemie.core.constants import REQUEST_ID

        mock_assistant = MagicMock()
        mock_vas.create_from_tool_invocation.return_value = mock_assistant
        mock_tool = MagicMock()
        mock_get_context.return_value = mock_tool
        mock_tool.execute.return_value = "result"

        request = ToolInvokeRequest(
            project="test",
            request_id=self.request_id,
            tool_args={},
        )

        with patch(f"{_SVC}.ToolExecutionService.validate_tool_args"):
            ToolExecutionService.invoke_tool_with_system_integration(request, "git_push", MagicMock())

        self.assertIn(REQUEST_ID, mock_tool.metadata)
        self.assertEqual(mock_tool.metadata[REQUEST_ID], self.request_id)

    @patch(f"{_SVC}.request_summary_manager")
    @patch(f"{_BASE_MON}.send_log_metric")
    @patch("codemie.service.request_summary_manager.request_summary_manager")
    @patch(f"{_SVC}.VirtualAssistantService")
    @patch(f"{_SVC}.ToolExecutionService._get_context_tools")
    @patch(f"{_SVC}.ToolkitService.get_toolkit_methods")
    def test_emits_and_clears_in_finally_on_success(
        self, mock_toolkit_methods, mock_get_context, mock_vas, mock_rsm_singleton, mock_send_metric, mock_rsm_svc
    ):
        mock_rsm_singleton.get_summary.return_value = self.summary
        mock_assistant = MagicMock()
        mock_vas.create_from_tool_invocation.return_value = mock_assistant
        mock_tool = MagicMock()
        mock_get_context.return_value = mock_tool
        mock_tool.execute.return_value = "result"

        request = ToolInvokeRequest(
            project="test",
            request_id=self.request_id,
            tool_args={},
        )

        with patch(f"{_SVC}.ToolExecutionService.validate_tool_args"):
            ToolExecutionService.invoke_tool_with_system_integration(request, "git_push", MagicMock())

        calls = [c for c in mock_send_metric.call_args_list if c[1].get("name") == "codemie_tools_usage_tokens"]
        self.assertEqual(len(calls), 1)
        mock_rsm_svc.clear_summary.assert_called_once_with(self.request_id)

    @patch(f"{_SVC}.request_summary_manager")
    @patch(f"{_SVC}.emit_llm_token_metric")
    @patch(f"{_SVC}.VirtualAssistantService")
    @patch(f"{_SVC}.ToolExecutionService._get_context_tools")
    @patch(f"{_SVC}.ToolkitService.get_toolkit_methods")
    def test_clears_in_finally_on_error(self, mock_toolkit_methods, mock_get_context, mock_vas, mock_emit, mock_rsm):
        mock_assistant = MagicMock()
        mock_vas.create_from_tool_invocation.return_value = mock_assistant
        mock_tool = MagicMock()
        mock_get_context.return_value = mock_tool
        mock_tool.execute.side_effect = RuntimeError("tool failure")

        request = ToolInvokeRequest(
            project="test",
            request_id=self.request_id,
            tool_args={},
        )

        with patch(f"{_SVC}.ToolExecutionService.validate_tool_args"):
            with self.assertRaises(RuntimeError):
                ToolExecutionService.invoke_tool_with_system_integration(request, "git_push", MagicMock())

        mock_rsm.clear_summary.assert_called_once_with(self.request_id)


if __name__ == "__main__":
    unittest.main()


class TestToolExecutionSpendOrigin(unittest.TestCase):
    """Every direct tool-invocation entry publishes the tools origin around the work."""

    def setUp(self):
        self.observed = []
        self.request = ToolInvokeRequest(
            project="test-project",
            request_id="req-origin-1",
            tool_creds={"token": "abc"},
            tool_args={"file_names": ["https://example.com/file.pdf"]},
        )

    def _record(self, return_value):
        def record(*_args, **_kwargs):
            self.observed.append(current_origin())
            return return_value

        return record

    @patch(f"{_SVC}.request_summary_manager")
    @patch(f"{_SVC}.emit_llm_token_metric")
    @patch(f"{_SVC}.get_llm_by_credentials")
    @patch(f"{_SVC}.build_unique_file_objects_list")
    @patch(f"{_SVC}.FileAnalysisToolkit")
    def test_invoke_file_analysis_tool_publishes_tools(
        self, mock_fa_toolkit, mock_build_files, mock_get_llm, _emit, _rsm
    ):
        mock_get_llm.side_effect = self._record(MagicMock())
        mock_build_files.return_value = [MagicMock()]

        mock_tool = MagicMock()
        mock_tool.execute.return_value = "result"
        mock_fa_toolkit.get_toolkit.return_value.get_tools.return_value = [mock_tool]

        with patch("codemie.service.tools.tool_service.ToolsService.find_tool", return_value=mock_tool):
            with patch(f"{_SVC}.ToolExecutionService.validate_tool_args"):
                ToolExecutionService.invoke_file_analysis_tool(self.request, "file_analysis")

        self.assertEqual(self.observed, [TrafficOrigin.TOOLS])
        self.assertIsNone(current_origin())

    @patch(f"{_SVC}.request_summary_manager")
    @patch(f"{_SVC}.emit_llm_token_metric")
    @patch(f"{_SVC}.ToolExecutionService._create_toolkit_instance")
    @patch(f"{_SVC}.ToolExecutionService.validate_tool_args")
    @patch(f"{_SVC}.ToolDiscoveryService")
    def test_invoke_tool_with_direct_creds_publishes_tools(
        self, mock_discovery, _validate, mock_create_toolkit, _emit, _rsm
    ):
        mock_tool = MagicMock()
        mock_tool.execute.return_value = "git result"
        toolkit = MagicMock()
        toolkit.get_tools.return_value = [mock_tool]
        mock_create_toolkit.side_effect = self._record(toolkit)
        mock_discovery.find_tool_by_name.return_value = MagicMock()

        with patch("codemie.service.tools.tool_service.ToolsService.find_tool", return_value=mock_tool):
            ToolExecutionService.invoke_tool_with_direct_creds(self.request, "git_push")

        self.assertEqual(self.observed, [TrafficOrigin.TOOLS])
        self.assertIsNone(current_origin())

    @patch(f"{_SVC}.request_summary_manager")
    @patch(f"{_SVC}.emit_llm_token_metric")
    @patch(f"{_SVC}.ToolExecutionService.get_tool_with_system_integration")
    @patch(f"{_SVC}.ToolExecutionService.validate_tool_args")
    @patch(f"{_SVC}.VirtualAssistantService")
    def test_invoke_tool_with_system_integration_publishes_tools(
        self, mock_virtual_assistant, _validate, mock_get_tool, _emit, _rsm
    ):
        mock_virtual_assistant.create_from_tool_invocation.side_effect = self._record(MagicMock())
        mock_tool = MagicMock()
        mock_tool.execute.return_value = "result"
        mock_get_tool.return_value = mock_tool

        ToolExecutionService.invoke_tool_with_system_integration(self.request, "jira_tool", MagicMock())

        self.assertEqual(self.observed, [TrafficOrigin.TOOLS])
        self.assertIsNone(current_origin())

    @patch(f"{_SVC}.request_summary_manager")
    @patch(f"{_SVC}.emit_llm_token_metric")
    def test_invoke_datasource_search_publishes_tools(self, _emit, _rsm):
        datasource = MagicMock(spec=IndexInfo)
        request = MagicMock(spec=DatasourceSearchInvokeRequest)
        request.query = "test query"
        request.llm_model = "gpt-4"
        request.request_id = "req-origin-1"

        search_tool = MagicMock()
        search_tool.metadata = {}
        search_tool.execute.return_value = "search results"

        with patch.object(ToolExecutionService, "get_search_tool", side_effect=self._record(search_tool)):
            ToolExecutionService.invoke_datasource_search(datasource, request)

        self.assertEqual(self.observed, [TrafficOrigin.TOOLS])
        self.assertIsNone(current_origin())

    @patch(f"{_SVC}.request_summary_manager")
    @patch(f"{_SVC}.emit_llm_token_metric")
    @patch(f"{_SVC}.get_llm_by_credentials")
    def test_origin_is_restored_when_the_tool_fails(self, mock_get_llm, _emit, _rsm):
        def record_and_fail(*_args, **_kwargs):
            self.observed.append(current_origin())
            raise RuntimeError("LLM failure")

        mock_get_llm.side_effect = record_and_fail

        with self.assertRaises(RuntimeError):
            ToolExecutionService.invoke_file_analysis_tool(self.request, "file_analysis")

        self.assertEqual(self.observed, [TrafficOrigin.TOOLS])
        self.assertIsNone(current_origin())
