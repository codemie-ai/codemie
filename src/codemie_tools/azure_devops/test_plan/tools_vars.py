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

from codemie_tools.base.models import ToolMetadata
from codemie_tools.azure_devops.test_plan.models import AzureDevOpsTestPlanConfig

CREATE_TEST_PLAN_TOOL = ToolMetadata(
    name="create_test_plan",
    description="""
        Create a test plan in Azure DevOps. It uses azure.devops.v7_0.test_plan Python package SDK.

        Arguments:
        - test_plan_create_params (str): JSON of the test plan create parameters
        - project (str, optional): Project ID or project name. If not provided, uses the default project configured in the toolkit
        """,
    label="Create Test Plan",
    user_description="""
        Creates a new test plan in Azure DevOps with the specified parameters.
        The tool requires test plan creation parameters in JSON format and optionally the project where the test plan should be created.
        If project is not provided, it will use the default project configured in the toolkit.
        Before using it, you need to provide:
        1. Azure DevOps organization URL
        2. Personal Access Token with appropriate permissions
        3. Optionally, a default project name in the toolkit settings
        """.strip(),
    config_class=AzureDevOpsTestPlanConfig,
)

DELETE_TEST_PLAN_TOOL = ToolMetadata(
    name="delete_test_plan",
    description="""
        Delete a test plan in Azure DevOps. It uses azure.devops.v7_0.test_plan Python package SDK.

        Arguments:
        - plan_id (int): ID of the test plan to be deleted
        - project (str, optional): Project ID or project name. If not provided, uses the default project configured in the toolkit
        """,
    label="Delete Test Plan",
    user_description="""
        Deletes an existing test plan from Azure DevOps using the plan ID and optionally the project name.
        If project is not provided, it will use the default project configured in the toolkit.
        Before using it, you need to provide:
        1. Azure DevOps organization URL
        2. Personal Access Token with appropriate permissions
        3. Optionally, a default project name in the toolkit settings
        """.strip(),
    config_class=AzureDevOpsTestPlanConfig,
)

GET_TEST_PLAN_TOOL = ToolMetadata(
    name="get_test_plan",
    description="""
        Get a test plan or list of test plans in Azure DevOps. It uses azure.devops.v7_0.test_plan Python package SDK.

        Arguments:
        - plan_id (int, optional): ID of the test plan to get. If not provided, returns all test plans
        - project (str, optional): Project ID or project name. If not provided, uses the default project configured in the toolkit
        """,
    label="Get Test Plan",
    user_description="""
        Retrieves information about a specific test plan by ID or lists all test plans in the project
        if no ID is provided. The tool returns detailed information about test plans from Azure DevOps.
        If project is not provided, it will use the default project configured in the toolkit.
        Before using it, you need to provide:
        1. Azure DevOps organization URL
        2. Personal Access Token with appropriate permissions
        3. Optionally, a default project name in the toolkit settings
        """.strip(),
    config_class=AzureDevOpsTestPlanConfig,
)

CREATE_TEST_SUITE_TOOL = ToolMetadata(
    name="create_test_suite",
    description="""
        Create a test suite in Azure DevOps. It uses azure.devops.v7_0.test_plan Python package SDK.

        Arguments:
        - test_suite_create_params (str): JSON of the test suite create parameters.
        -- Example: {"name": "New Test Suite", "parent_suite": {"id": 6}, "suite_type": "staticTestSuite"}
        -- Important: Attributes map for test_suite_create_params JSON object:
                'parent_suite': {'key': 'parentSuite', 'type': 'TestSuiteReference'}
                'query_string': {'key': 'queryString', 'type': 'str'}
                'requirement_id': {'key': 'requirementId', 'type': 'int'}
                'suite_type': {'key': 'suiteType', 'type': 'object'}
        - plan_id (int): ID of the test plan that contains the suites
        - project (str, optional): Project ID or project name. If not provided, uses the default project configured in the toolkit
        """,
    label="Create Test Suite",
    user_description="""
        Creates a new test suite within an existing test plan in Azure DevOps.
        The tool requires test suite creation parameters in JSON format, the plan ID, and optionally the project name.
        If project is not provided, it will use the default project configured in the toolkit.
        Before using it, you need to provide:
        1. Azure DevOps organization URL
        2. Personal Access Token with appropriate permissions
        3. Optionally, a default project name in the toolkit settings
        """.strip(),
    config_class=AzureDevOpsTestPlanConfig,
)

DELETE_TEST_SUITE_TOOL = ToolMetadata(
    name="delete_test_suite",
    description="""
        Delete a test suite in Azure DevOps. It uses azure.devops.v7_0.test_plan Python package SDK.

        Arguments:
        - plan_id (int): ID of the test plan that contains the suite
        - suite_id (int): ID of the test suite to delete
        - project (str, optional): Project ID or project name. If not provided, uses the default project configured in the toolkit
        """,
    label="Delete Test Suite",
    user_description="""
        Deletes an existing test suite from a test plan in Azure DevOps using the plan ID, suite ID, and optionally the project name.
        If project is not provided, it will use the default project configured in the toolkit.
        Before using it, you need to provide:
        1. Azure DevOps organization URL
        2. Personal Access Token with appropriate permissions
        3. Optionally, a default project name in the toolkit settings
        """.strip(),
    config_class=AzureDevOpsTestPlanConfig,
)

GET_TEST_SUITE_TOOL = ToolMetadata(
    name="get_test_suite",
    description="""
        Get a test suite or list of test suites in Azure DevOps. It uses azure.devops.v7_0.test_plan Python package SDK.

        Arguments:
        - plan_id (int): ID of the test plan that contains the suites
        - suite_id (int, optional): ID of the suite to get. If not provided, returns all test suites in the plan
        - project (str, optional): Project ID or project name. If not provided, uses the default project configured in the toolkit
        """,
    label="Get Test Suite",
    user_description="""
        Retrieves information about a specific test suite by ID or lists all test suites in the specified
        test plan if no suite ID is provided. The tool returns detailed information about test suites from Azure DevOps.
        If project is not provided, it will use the default project configured in the toolkit.
        Before using it, you need to provide:
        1. Azure DevOps organization URL
        2. Personal Access Token with appropriate permissions
        3. Optionally, a default project name in the toolkit settings
        """.strip(),
    config_class=AzureDevOpsTestPlanConfig,
)

ADD_TEST_CASE_TOOL = ToolMetadata(
    name="add_test_case",
    description="""
        Add a test case to a suite in Azure DevOps. It uses azure.devops.v7_0.test_plan Python package SDK.

        Arguments:
        - suite_test_case_create_update_parameters (str): JSON array of the suite test case create update parameters. Example: "[{"work_item":{"id":"23"}}]"
        - plan_id (int): ID of the test plan to which test cases are to be added
        - suite_id (int): ID of the test suite to which test cases are to be added
        - project (str, optional): Project ID or project name. If not provided, uses the default project configured in the toolkit
        """,
    label="Add Test Case",
    user_description="""
        Adds one or more test cases to an existing test suite within a test plan in Azure DevOps.
        The tool requires test case parameters in JSON format, the plan ID, suite ID, and optionally the project name.
        If project is not provided, it will use the default project configured in the toolkit.
        Before using it, you need to provide:
        1. Azure DevOps organization URL
        2. Personal Access Token with appropriate permissions
        3. Optionally, a default project name in the toolkit settings
        """.strip(),
    config_class=AzureDevOpsTestPlanConfig,
)

GET_TEST_CASE_TOOL = ToolMetadata(
    name="get_test_case",
    description="""
        Get a test case from a suite in Azure DevOps. It uses azure.devops.v7_0.test_plan Python package SDK.

        Arguments:
        - plan_id (int): ID of the test plan for which test cases are requested
        - suite_id (int): ID of the test suite for which test cases are requested
        - test_case_id (str): Test Case Id to be fetched
        - project (str, optional): Project ID or project name. If not provided, uses the default project configured in the toolkit
        """,
    label="Get Test Case",
    user_description="""
        Retrieves detailed information about a specific test case within a test suite in Azure DevOps.
        The tool requires the plan ID, suite ID, test case ID, and optionally the project name to fetch the specific test case.
        If project is not provided, it will use the default project configured in the toolkit.
        Before using it, you need to provide:
        1. Azure DevOps organization URL
        2. Personal Access Token with appropriate permissions
        3. Optionally, a default project name in the toolkit settings
        """.strip(),
    config_class=AzureDevOpsTestPlanConfig,
)

GET_TEST_CASES_TOOL = ToolMetadata(
    name="get_test_cases",
    description="""
        Get test cases from a suite in Azure DevOps. It uses azure.devops.v7_0.test_plan Python package SDK.

        Arguments:
        - plan_id (int): ID of the test plan for which test cases are requested
        - suite_id (int): ID of the test suite for which test cases are requested
        - project (str, optional): Project ID or project name. If not provided, uses the default project configured in the toolkit
        """,
    label="Get Test Cases",
    user_description="""
        Retrieves a list of all test cases within a test suite in Azure DevOps.
        The tool requires the plan ID, suite ID, and optionally the project name to fetch all test cases in the specified suite.
        If project is not provided, it will use the default project configured in the toolkit.
        Before using it, you need to provide:
        1. Azure DevOps organization URL
        2. Personal Access Token with appropriate permissions
        3. Optionally, a default project name in the toolkit settings
        """.strip(),
    config_class=AzureDevOpsTestPlanConfig,
)
