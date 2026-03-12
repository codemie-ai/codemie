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
from azure.devops.v7_0.test_plan.models import TestPlan, TestSuite, TestCase

from codemie_tools.azure_devops.test_plan.models import AzureDevOpsTestPlanConfig
from codemie_tools.azure_devops.test_plan.tools import (
    CreateTestPlanTool,
    GetTestPlanTool,
    CreateTestSuiteTool,
    AddTestCaseTool,
    GetTestCaseTool,
    GetTestCasesTool,
)


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
