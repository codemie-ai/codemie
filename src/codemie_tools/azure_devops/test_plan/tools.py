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

import json
import os
from typing import Type, Optional

from azure.devops.connection import Connection
from azure.devops.v7_0.test_plan.models import (
    TestPlanCreateParams,
    TestSuiteCreateParams,
    SuiteTestCaseCreateUpdateParameters,
)
from azure.devops.v7_0.test_plan.test_plan_client import TestPlanClient
from langchain_core.tools import ToolException
from msrest.authentication import BasicAuthentication
from pydantic import BaseModel

from codemie_tools.azure_devops.test_plan.models import (
    AzureDevOpsTestPlanConfig,
    CreateTestPlanInput,
    DeleteTestPlanInput,
    GetTestPlanInput,
    CreateTestSuiteInput,
    DeleteTestSuiteInput,
    GetTestSuiteInput,
    AddTestCaseInput,
    GetTestCaseInput,
    GetTestCasesInput,
)
from codemie_tools.azure_devops.test_plan.tools_vars import (
    CREATE_TEST_PLAN_TOOL,
    DELETE_TEST_PLAN_TOOL,
    GET_TEST_PLAN_TOOL,
    CREATE_TEST_SUITE_TOOL,
    DELETE_TEST_SUITE_TOOL,
    GET_TEST_SUITE_TOOL,
    ADD_TEST_CASE_TOOL,
    GET_TEST_CASE_TOOL,
    GET_TEST_CASES_TOOL,
)
from codemie_tools.base.codemie_tool import CodeMieTool, logger

# Ensure Azure DevOps cache directory is set
if not os.environ.get("AZURE_DEVOPS_CACHE_DIR", None):
    os.environ["AZURE_DEVOPS_CACHE_DIR"] = ""


class BaseAzureDevOpsTestPlanTool(CodeMieTool):
    """Base class for Azure DevOps Test Plan tools."""

    config: AzureDevOpsTestPlanConfig
    __client: Optional[TestPlanClient] = None

    @property
    def _client(self) -> TestPlanClient:
        """Get or create Azure DevOps client (lazy initialization)."""
        if self.__client is None:
            try:
                # Set up connection to Azure DevOps using Personal Access Token (PAT)
                credentials = BasicAuthentication("", self.config.token)
                connection = Connection(base_url=self.config.organization_url, creds=credentials)
                # Retrieve the test plan client
                self.__client = connection.clients.get_test_plan_client()
            except Exception as e:
                logger.error(f"Failed to connect to Azure DevOps: {e}")
                raise ToolException(f"Failed to connect to Azure DevOps: {e}")
        return self.__client

    @_client.setter
    def _client(self, value: TestPlanClient) -> None:
        """Set the Azure DevOps client (useful for testing)."""
        self.__client = value


class CreateTestPlanTool(BaseAzureDevOpsTestPlanTool):
    """Tool to create a test plan in Azure DevOps."""

    name: str = CREATE_TEST_PLAN_TOOL.name
    description: str = CREATE_TEST_PLAN_TOOL.description
    args_schema: Type[BaseModel] = CreateTestPlanInput

    def execute(self, test_plan_create_params: str, project: Optional[str] = None):
        """Create a test plan in Azure DevOps."""
        # Use provided project or default from config if not provided
        project_to_use = project or self.config.project

        try:
            params = json.loads(test_plan_create_params)
            test_plan_create_params_obj = TestPlanCreateParams(**params)
            test_plan = self._client.create_test_plan(test_plan_create_params_obj, project_to_use)
            return f"Test plan {test_plan.id} created successfully."
        except Exception as e:
            logger.error(f"Error creating test plan: {e}")
            raise ToolException(f"Error creating test plan: {e}")


class DeleteTestPlanTool(BaseAzureDevOpsTestPlanTool):
    """Tool to delete a test plan in Azure DevOps."""

    name: str = DELETE_TEST_PLAN_TOOL.name
    description: str = DELETE_TEST_PLAN_TOOL.description
    args_schema: Type[BaseModel] = DeleteTestPlanInput

    def execute(self, plan_id: int, project: Optional[str] = None):
        """Delete a test plan in Azure DevOps."""
        # Use provided project or default from config if not provided
        project_to_use = project or self.config.project

        try:
            self._client.delete_test_plan(project_to_use, plan_id)
            return f"Test plan {plan_id} deleted successfully."
        except Exception as e:
            logger.error(f"Error deleting test plan: {e}")
            raise ToolException(f"Error deleting test plan: {e}")


class GetTestPlanTool(BaseAzureDevOpsTestPlanTool):
    """Tool to get a test plan or list test plans in Azure DevOps."""

    name: str = GET_TEST_PLAN_TOOL.name
    description: str = GET_TEST_PLAN_TOOL.description
    args_schema: Type[BaseModel] = GetTestPlanInput

    def execute(self, plan_id: Optional[int] = None, project: Optional[str] = None):
        """Get a test plan or list of test plans in Azure DevOps."""
        # Use provided project or default from config if not provided
        project_to_use = project or self.config.project

        try:
            if plan_id:
                test_plan = self._client.get_test_plan_by_id(project_to_use, plan_id)
                return test_plan.as_dict()
            else:
                test_plans = self._client.get_test_plans(project_to_use)
                return [plan.as_dict() for plan in test_plans[: self.config.limit]]
        except Exception as e:
            logger.error(f"Error getting test plan(s): {e}")
            raise ToolException(f"Error getting test plan(s): {e}")


class CreateTestSuiteTool(BaseAzureDevOpsTestPlanTool):
    """Tool to create a test suite in Azure DevOps."""

    name: str = CREATE_TEST_SUITE_TOOL.name
    description: str = CREATE_TEST_SUITE_TOOL.description
    args_schema: Type[BaseModel] = CreateTestSuiteInput

    def execute(self, test_suite_create_params: str, plan_id: int, project: Optional[str] = None):
        """Create a test suite in Azure DevOps."""
        # Use provided project or default from config if not provided
        project_to_use = project or self.config.project

        try:
            params = json.loads(test_suite_create_params)
            test_suite_create_params_obj = TestSuiteCreateParams(**params)
            test_suite = self._client.create_test_suite(test_suite_create_params_obj, project_to_use, plan_id)
            return f"Test suite {test_suite.id} created successfully."
        except Exception as e:
            logger.error(f"Error creating test suite: {e}")
            raise ToolException(f"Error creating test suite: {e}")


class DeleteTestSuiteTool(BaseAzureDevOpsTestPlanTool):
    """Tool to delete a test suite in Azure DevOps."""

    name: str = DELETE_TEST_SUITE_TOOL.name
    description: str = DELETE_TEST_SUITE_TOOL.description
    args_schema: Type[BaseModel] = DeleteTestSuiteInput

    def execute(self, plan_id: int, suite_id: int, project: Optional[str] = None):
        """Delete a test suite in Azure DevOps."""
        # Use provided project or default from config if not provided
        project_to_use = project or self.config.project

        try:
            self._client.delete_test_suite(project_to_use, plan_id, suite_id)
            return f"Test suite {suite_id} deleted successfully."
        except Exception as e:
            logger.error(f"Error deleting test suite: {e}")
            raise ToolException(f"Error deleting test suite: {e}")


class GetTestSuiteTool(BaseAzureDevOpsTestPlanTool):
    """Tool to get a test suite or list test suites in Azure DevOps."""

    name: str = GET_TEST_SUITE_TOOL.name
    description: str = GET_TEST_SUITE_TOOL.description
    args_schema: Type[BaseModel] = GetTestSuiteInput

    def execute(self, plan_id: int, suite_id: Optional[int] = None, project: Optional[str] = None):
        """Get a test suite or list of test suites in Azure DevOps."""
        # Use provided project or default from config if not provided
        project_to_use = project or self.config.project

        try:
            if suite_id:
                test_suite = self._client.get_test_suite_by_id(project_to_use, plan_id, suite_id)
                return test_suite.as_dict()
            else:
                test_suites = self._client.get_test_suites_for_plan(project_to_use, plan_id)
                return [suite.as_dict() for suite in test_suites[: self.config.limit]]
        except Exception as e:
            logger.error(f"Error getting test suite(s): {e}")
            raise ToolException(f"Error getting test suite(s): {e}")


class AddTestCaseTool(BaseAzureDevOpsTestPlanTool):
    """Tool to add a test case to a suite in Azure DevOps."""

    name: str = ADD_TEST_CASE_TOOL.name
    description: str = ADD_TEST_CASE_TOOL.description
    args_schema: Type[BaseModel] = AddTestCaseInput

    def execute(
        self,
        suite_test_case_create_update_parameters: str,
        plan_id: int,
        suite_id: int,
        project: Optional[str] = None,
    ):
        """Add a test case to a suite in Azure DevOps."""
        # Use provided project or default from config if not provided
        project_to_use = project or self.config.project

        try:
            params = json.loads(suite_test_case_create_update_parameters)
            suite_test_case_params_list = []

            # Handle both array and single object scenarios
            if isinstance(params, list):
                for param in params:
                    suite_test_case_params_list.append(SuiteTestCaseCreateUpdateParameters(**param))
            else:
                suite_test_case_params_list.append(SuiteTestCaseCreateUpdateParameters(**params))

            test_cases = self._client.add_test_cases_to_suite(
                suite_test_case_params_list, project_to_use, plan_id, suite_id
            )
            return [test_case.as_dict() for test_case in test_cases]
        except Exception as e:
            logger.error(f"Error adding test case: {e}")
            raise ToolException(f"Error adding test case: {e}")


class GetTestCaseTool(BaseAzureDevOpsTestPlanTool):
    """Tool to get a test case from a suite in Azure DevOps."""

    name: str = GET_TEST_CASE_TOOL.name
    description: str = GET_TEST_CASE_TOOL.description
    args_schema: Type[BaseModel] = GetTestCaseInput

    def execute(self, plan_id: int, suite_id: int, test_case_id: str, project: Optional[str] = None):
        """Get a test case from a suite in Azure DevOps."""
        # Use provided project or default from config if not provided
        project_to_use = project or self.config.project

        try:
            test_cases = self._client.get_test_case(project_to_use, plan_id, suite_id, test_case_id)
            if test_cases:  # Check if the list is not empty
                test_case = test_cases[0]
                return test_case.as_dict()
            else:
                return f"No test cases found with criteria: project {project_to_use}, plan {plan_id}, suite {suite_id}, test case id {test_case_id}"
        except Exception as e:
            logger.error(f"Error getting test case: {e}")
            raise ToolException(f"Error getting test case: {e}")


class GetTestCasesTool(BaseAzureDevOpsTestPlanTool):
    """Tool to get test cases from a suite in Azure DevOps."""

    name: str = GET_TEST_CASES_TOOL.name
    description: str = GET_TEST_CASES_TOOL.description
    args_schema: Type[BaseModel] = GetTestCasesInput

    def execute(self, plan_id: int, suite_id: int, project: Optional[str] = None):
        """Get test cases from a suite in Azure DevOps."""
        # Use provided project or default from config if not provided
        project_to_use = project or self.config.project

        try:
            test_cases = self._client.get_test_case_list(project_to_use, plan_id, suite_id)
            return [test_case.as_dict() for test_case in test_cases[: self.config.limit]]
        except Exception as e:
            logger.error(f"Error getting test cases: {e}")
            raise ToolException(f"Error getting test cases: {e}")
