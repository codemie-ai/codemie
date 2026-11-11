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

from unittest.mock import MagicMock

import pytest
from azure.devops.v7_0.test_plan.models import TestPlan, TestSuite, TestCase
from langchain_core.tools import ToolException

from codemie_tools.azure_devops.test_plan.models import AzureDevOpsTestPlanConfig
from codemie_tools.azure_devops.test_plan.tools import (
    CreateTestPlanTool,
    GetTestPlanTool,
    GetTestSuiteTool,
    CreateTestSuiteTool,
    AddTestCaseTool,
    GetTestCaseTool,
    GetTestCasesTool,
)


def _make_items(n, spec):
    items = []
    for i in range(n):
        item = MagicMock(spec=spec)
        item.as_dict.return_value = {"id": i}
        items.append(item)
    return items


def _make_tool(tool_cls, method, spec, count, config_limit=None):
    config_kwargs = {} if config_limit is None else {"limit": config_limit}
    config = AzureDevOpsTestPlanConfig(
        organization_url="https://dev.azure.com/org", token="fake-token", project="test-project", **config_kwargs
    )
    tool = tool_cls(config=config)
    mock_client = MagicMock()
    # getattr keeps one parametrized table for the three list tools, each with its own client method
    getattr(mock_client, method).return_value = _make_items(count, spec)
    tool._client = mock_client
    return tool


LIST_CASES = [
    (GetTestPlanTool, "get_test_plans", "test plans", TestPlan, {}),
    (GetTestSuiteTool, "get_test_suites_for_plan", "test suites", TestSuite, {"plan_id": 1}),
    (GetTestCasesTool, "get_test_case_list", "test cases", TestCase, {"plan_id": 1, "suite_id": 1}),
]
LIST_CASE_IDS = ["plans", "suites", "cases"]


class TestCreateTestPlanTool:
    def test_create_test_plan_success(self):
        config = AzureDevOpsTestPlanConfig(
            organization_url="https://dev.azure.com/org", token="fake-token", project="test-project", limit=5
        )
        tool = CreateTestPlanTool(config=config)

        mock_client = MagicMock()
        mock_test_plan = MagicMock(spec=TestPlan)
        mock_test_plan.id = 1
        mock_client.create_test_plan.return_value = mock_test_plan
        tool._client = mock_client

        result = tool.execute(test_plan_create_params='{"name": "New Test Plan"}', project="test-project")
        assert "Test plan 1 created successfully" in result


class TestGetTestPlanTool:
    def test_get_test_plan_by_id_success(self):
        config = AzureDevOpsTestPlanConfig(
            organization_url="https://dev.azure.com/org", token="fake-token", project="test-project", limit=5
        )
        tool = GetTestPlanTool(config=config)

        mock_client = MagicMock()
        mock_test_plan = MagicMock(spec=TestPlan)
        mock_test_plan.as_dict.return_value = {"id": 1, "name": "Test Plan 1"}
        mock_client.get_test_plan_by_id.return_value = mock_test_plan
        tool._client = mock_client

        result = tool.execute(plan_id=1, project="test-project")
        assert result == {"id": 1, "name": "Test Plan 1"}

    def test_get_test_plans_success(self):
        config = AzureDevOpsTestPlanConfig(
            organization_url="https://dev.azure.com/org", token="fake-token", project="test-project", limit=5
        )
        tool = GetTestPlanTool(config=config)

        mock_client = MagicMock()
        mock_test_plan = MagicMock(spec=TestPlan)
        mock_test_plan.as_dict.return_value = {"id": 1, "name": "Test Plan 1"}
        mock_client.get_test_plans.return_value = [mock_test_plan]
        tool._client = mock_client

        result = tool.execute(project="test-project")
        assert result == [{"id": 1, "name": "Test Plan 1"}]

    def test_get_test_plan_by_id_ignores_limit(self):
        config = AzureDevOpsTestPlanConfig(
            organization_url="https://dev.azure.com/org", token="fake-token", project="test-project", limit=5
        )
        tool = GetTestPlanTool(config=config)

        mock_client = MagicMock()
        mock_test_plan = MagicMock(spec=TestPlan)
        mock_test_plan.as_dict.return_value = {"id": 1, "name": "Test Plan 1"}
        mock_client.get_test_plan_by_id.return_value = mock_test_plan
        tool._client = mock_client

        assert tool.execute(plan_id=1, limit=1) == {"id": 1, "name": "Test Plan 1"}


class TestCreateTestSuiteTool:
    def test_create_test_suite_success(self):
        config = AzureDevOpsTestPlanConfig(
            organization_url="https://dev.azure.com/org", token="fake-token", project="test-project", limit=5
        )
        tool = CreateTestSuiteTool(config=config)

        mock_client = MagicMock()
        mock_test_suite = MagicMock(spec=TestSuite)
        mock_test_suite.id = 1
        mock_client.create_test_suite.return_value = mock_test_suite
        tool._client = mock_client

        result = tool.execute(test_suite_create_params='{"name": "New Test Suite"}', plan_id=1, project="test-project")
        assert "Test suite 1 created successfully" in result


class TestAddTestCaseTool:
    def test_add_test_case_success(self):
        config = AzureDevOpsTestPlanConfig(
            organization_url="https://dev.azure.com/org", token="fake-token", project="test-project", limit=5
        )
        tool = AddTestCaseTool(config=config)

        mock_client = MagicMock()
        mock_test_case = MagicMock(spec=TestCase)
        mock_test_case.id = 1
        mock_test_case.as_dict.return_value = {"id": 1, "name": "Test Case 1"}
        mock_client.add_test_cases_to_suite.return_value = [mock_test_case]
        tool._client = mock_client

        result = tool.execute(
            suite_test_case_create_update_parameters='[{"work_item": {"id": "1"}}]',
            plan_id=1,
            suite_id=1,
            project="test-project",
        )
        assert result == [{"id": 1, "name": "Test Case 1"}]


class TestGetTestCaseTool:
    def test_get_test_case_success(self):
        config = AzureDevOpsTestPlanConfig(
            organization_url="https://dev.azure.com/org", token="fake-token", project="test-project", limit=5
        )
        tool = GetTestCaseTool(config=config)

        mock_client = MagicMock()
        mock_test_case = MagicMock(spec=TestCase)
        mock_test_case.id = 1
        mock_test_case.as_dict.return_value = {"id": 1, "name": "Test Case 1"}
        mock_client.get_test_case.return_value = [mock_test_case]
        tool._client = mock_client

        result = tool.execute(plan_id=1, suite_id=1, test_case_id="1", project="test-project")
        assert result == {"id": 1, "name": "Test Case 1"}


class TestGetTestCasesTool:
    def test_get_test_cases_success(self):
        config = AzureDevOpsTestPlanConfig(
            organization_url="https://dev.azure.com/org", token="fake-token", project="test-project", limit=5
        )
        tool = GetTestCasesTool(config=config)

        mock_client = MagicMock()
        mock_test_case = MagicMock(spec=TestCase)
        mock_test_case.id = 1
        mock_test_case.as_dict.return_value = {"id": 1, "name": "Test Case 1"}
        mock_client.get_test_case_list.return_value = [mock_test_case]
        tool._client = mock_client

        result = tool.execute(plan_id=1, suite_id=1, project="test-project")
        assert result == [{"id": 1, "name": "Test Case 1"}]


@pytest.mark.parametrize("tool_cls, method, kind, spec, kwargs", LIST_CASES, ids=LIST_CASE_IDS)
class TestListLimit:
    def test_omitted_limit_uses_config_limit(self, tool_cls, method, kind, spec, kwargs):
        tool = _make_tool(tool_cls, method, spec, 8, config_limit=2)
        result = tool.execute(**kwargs)
        assert len(result["items"]) == 2
        assert result["notice"]

    def test_default_config_limit_is_100(self, tool_cls, method, kind, spec, kwargs):
        tool = _make_tool(tool_cls, method, spec, 150)
        result = tool.execute(**kwargs)
        assert len(result["items"]) == 100
        assert "100 of 150 " + kind in result["notice"]

    def test_explicit_limit_overrides_config(self, tool_cls, method, kind, spec, kwargs):
        tool = _make_tool(tool_cls, method, spec, 8, config_limit=2)
        assert len(tool.execute(**kwargs, limit=5)["items"]) == 5

    def test_minus_one_returns_all_without_notice(self, tool_cls, method, kind, spec, kwargs):
        tool = _make_tool(tool_cls, method, spec, 150, config_limit=2)
        result = tool.execute(**kwargs, limit=-1)
        assert isinstance(result, list)
        assert len(result) == 150

    @pytest.mark.parametrize("limit", [3, 10])
    def test_limit_equal_or_above_count_is_plain_list(self, tool_cls, method, kind, spec, kwargs, limit):
        tool = _make_tool(tool_cls, method, spec, 3)
        assert tool.execute(**kwargs, limit=limit) == [{"id": 0}, {"id": 1}, {"id": 2}]

    def test_limit_below_count_returns_notice(self, tool_cls, method, kind, spec, kwargs):
        tool = _make_tool(tool_cls, method, spec, 5)
        result = tool.execute(**kwargs, limit=3)
        assert result["items"] == [{"id": 0}, {"id": 1}, {"id": 2}]
        assert "3 of 5 " + kind in result["notice"]
        assert "limit=-1" in result["notice"]

    def test_notice_does_not_mention_integration(self, tool_cls, method, kind, spec, kwargs):
        notice = _make_tool(tool_cls, method, spec, 5).execute(**kwargs, limit=3)["notice"].lower()
        assert "integration" not in notice
        assert "setting" not in notice

    @pytest.mark.parametrize("limit", [0, -2])
    def test_invalid_limit_direct_call_rejected(self, tool_cls, method, kind, spec, kwargs, limit):
        tool = _make_tool(tool_cls, method, spec, 5)
        with pytest.raises(ToolException):
            tool.execute(**kwargs, limit=limit)

    @pytest.mark.parametrize("limit", [0, -2])
    def test_limit_helper_rejects_non_positive(self, tool_cls, method, kind, spec, kwargs, limit):
        tool = _make_tool(tool_cls, method, spec, 5)
        with pytest.raises(ValueError):
            tool._limit_results([], "x", limit)

    def test_invalid_config_limit_rejected(self, tool_cls, method, kind, spec, kwargs):
        tool = _make_tool(tool_cls, method, spec, 5, config_limit=0)
        with pytest.raises(ToolException):
            tool.execute(**kwargs)

    def test_description_documents_limit(self, tool_cls, method, kind, spec, kwargs):
        description = _make_tool(tool_cls, method, spec, 0).description
        for expected in ("limit (int, optional)", "-1", "integration default", "notice", "paginat"):
            assert expected in description
        assert "default 100" not in description
