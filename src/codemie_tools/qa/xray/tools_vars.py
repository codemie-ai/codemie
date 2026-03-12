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
from codemie_tools.qa.xray.models import XrayConfig


XRAY_GET_TESTS_TOOL = ToolMetadata(
    name="XrayGetTests",
    description="""
    Retrieve test cases from Xray Cloud using JQL (Jira Query Language) queries.

    This tool allows you to search and filter test cases in Xray using JQL syntax,
    similar to searching issues in Jira. It automatically handles pagination to
    retrieve all matching tests.

    Required arguments:
    - jql: JQL query string to filter tests

    JQL Query Examples:
    - Get all tests in a project: project = "CALC" AND type = Test
    - Get specific tests by key: key in (CALC-1, CALC-2, CALC-3)
    - Filter by status: status = "To Do" AND assignee = currentUser()
    - Filter by test type: testType = "Manual" AND project = "CALC"
    - Complex query: project = "CALC" AND status IN ("To Do", "In Progress") AND created >= -7d
    """.strip(),
    label="Xray Get Tests",
    user_description="""
    Retrieve test cases from Xray Cloud using JQL queries.

    Use this tool to search and filter test cases based on various criteria
    such as project, status, assignee, test type, and more. The tool supports
    full JQL syntax and automatically handles pagination.

    Before using this tool, you need to configure:
    1. Xray Cloud Base URL (typically https://xray.cloud.getxray.app)
    2. API Client ID - obtained from Xray API Keys settings
    3. API Client Secret - obtained from Xray API Keys settings

    To obtain API credentials:
    1. Navigate to Xray Cloud settings
    2. Go to API Keys section
    3. Create a new API key
    4. Copy the Client ID and Client Secret

    Refer to: https://docs.getxray.app/display/XRAYCLOUD/Authentication+-+REST+v2
    """.strip(),
    settings_config=True,
    config_class=XrayConfig,
)


XRAY_CREATE_TEST_TOOL = ToolMetadata(
    name="XrayCreateTest",
    description="""
    Create a new test case in Xray Cloud using GraphQL mutations.
    This tool allows you to create different types of test cases in Xray by
    providing a GraphQL mutation with test details. Supports creating Manual,
    Generic, Cucumber, and other test types.
    # Required arguments:
    - graphql_mutation: GraphQL mutation string to create the test
    # testType: the Test Type of the Test.
    # steps: the Step definition of the test.
    # unstructured: the unstructured definition of the Test.
    # gherkin: the gherkin definition of the Test.
    # preconditionIssueIds: the Precondition ids that be associated with the Test.
    # folderPath: the Test repository folder for the Test.
    # jira: the Jira object that will be used to create the Test.

    Supported test types:
    - Manual: Tests with defined steps (action, data, result)
    - Generic: Tests with unstructured text description
    - Cucumber: Tests with Gherkin syntax (Given/When/Then)
    Generic Test Example:
    mutation {
        createTest(
            testType: { name: "Generic" },
            unstructured: "Perform exploratory testing on calculator application",
            jira: {
                fields: {
                    summary: "Exploratory calculator test",
                    project: { key: "CALC" }
                }
            }
        ) {
            test {
                issueId
                testType { name }
                unstructured
                jira(fields: ["key", "summary"])
            }
            warnings
        }
    }
    """.strip(),
    label="Xray Create Test",
    user_description="""
    Create new test cases in Xray Cloud using GraphQL mutations.

    Use this tool to programmatically create test cases with different test types:
    - Manual tests with step-by-step instructions
    - Generic tests with unstructured descriptions
    - Cucumber tests with Gherkin scenarios

    The tool uses GraphQL mutations to create tests with full control over:
    - Test type and structure
    - Jira issue fields (summary, description, labels, etc.)
    - Test steps and data
    - Preconditions and folder organization

    Same API credentials as Xray Get Tests tool are required.

    Refer to: https://docs.getxray.app/display/XRAYCLOUD/GraphQL+API
    """.strip(),
    settings_config=True,
    config_class=XrayConfig,
)


XRAY_EXECUTE_GRAPHQL_TOOL = ToolMetadata(
    name="XrayExecuteGraphQL",
    description="""
    Execute custom GraphQL queries or mutations against Xray Cloud API.
    This tool provides direct access to Xray's GraphQL API, allowing you to
    perform any supported operation including advanced queries, mutations,
    and operations not covered by the specialized tools.
    Required arguments:
    - graphql: Custom GraphQL query or mutation string

    Use cases:
    - Query test execution results and status
    - Update existing test cases
    - Manage test plans and test sets
    - Query test runs and their results
    - Create and manage preconditions
    - Retrieve test repository structure
    - Advanced filtering and aggregation

    Query Example (Get test executions):
    query {
        getTestExecutions(
            testIssueIds: ["12345"],
            limit: 10
        ) {
            total
            results {
                issueId
                jira(fields: ["key", "summary"])
                status {
                    name
                    color
                }
                testRuns {
                    status {
                        name
                    }
                    finishedOn
                }
            }
        }
    }

    Mutation Example (Update test):
    mutation {
        updateTest(
            issueId: "12345",
            testType: { name: "Manual" },
            steps: [
                { action: "Updated step", result: "Updated result" }
            ]
        ) {
            test {
                issueId
                jira(fields: ["key"])
                testType { name }
            }
            warnings
        }
    }
    """.strip(),
    label="Xray Execute GraphQL",
    user_description="""
    Execute custom GraphQL queries and mutations against Xray Cloud API.

    Use this tool for advanced Xray operations and custom queries that go
    beyond the basic get/create test functionality. This tool provides full
    access to Xray's GraphQL API capabilities.

    Common use cases:
    - Query test execution results and history
    - Update existing tests and test executions
    - Manage test plans, test sets, and folders
    - Create and query preconditions
    - Retrieve test coverage and traceability
    - Custom reporting and analytics queries

    Same API credentials as other Xray tools are required.

    Requires knowledge of Xray's GraphQL schema and operations.

    Refer to: https://docs.getxray.app/display/XRAYCLOUD/GraphQL+API
    """.strip(),
    settings_config=True,
    config_class=XrayConfig,
)
