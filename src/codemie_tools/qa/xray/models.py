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

from typing import Any, Dict, Optional
from pydantic import BaseModel, Field, field_validator, model_validator
from codemie_tools.base.models import CodeMieToolConfig, CredentialTypes, RequiredField


class XrayConfig(CodeMieToolConfig):
    """Configuration for Xray Cloud integration.

    Xray is a comprehensive test management tool for Jira that supports
    manual and automated testing workflows with GraphQL API access.
    """

    credential_type: CredentialTypes = Field(default=CredentialTypes.XRAY, exclude=True, frozen=True)

    base_url: str = RequiredField(
        description="Xray Cloud base URL", json_schema_extra={"placeholder": "https://xray.cloud.getxray.app"}
    )

    client_id: str = RequiredField(
        description="Xray API client ID for authentication",
        json_schema_extra={
            "placeholder": "your_client_id",
            "help": "https://docs.getxray.app/display/XRAYCLOUD/Authentication+-+REST+v2",
        },
    )

    client_secret: str = RequiredField(
        description="Xray API client secret for authentication",
        json_schema_extra={
            "placeholder": "your_client_secret",
            "sensitive": True,
            "help": "https://docs.getxray.app/display/XRAYCLOUD/Authentication+-+REST+v2",
        },
    )

    limit: Optional[int] = Field(default=100, description="Maximum number of results to return per query")

    verify_ssl: Optional[bool] = Field(default=True, description="Verify SSL certificates for API requests")

    timeout: Optional[int] = Field(default=300, description="Read timeout in seconds for Xray API requests")

    @field_validator("limit", mode="before")
    @classmethod
    def empty_string_to_default(cls, v):
        """Convert empty strings to default value for limit field."""
        if v == "" or v is None:
            return 100  # Return default value instead of None
        return v

    @model_validator(mode="before")
    @classmethod
    def map_url_to_base_url(cls, values: Dict[str, Any]) -> Dict[str, Any]:
        """Map 'url' to 'base_url' for backwards compatibility.

        Handles old credentials stored with 'url' key instead of 'base_url'.
        """
        if not isinstance(values, dict):
            return values

        # If base_url is not provided but url is, map url to base_url
        if "base_url" not in values and "url" in values:
            values["base_url"] = values.pop("url")

        return values


class XrayGetTestsInput(BaseModel):
    """Input schema for getting tests from Xray."""

    jql: str = Field(
        ...,
        description="""
        JQL query to filter tests. Examples:
        - project = "CALC" AND type = Test
        - key in (CALC-1, CALC-2)
        - status = "To Do" AND assignee = currentUser()
        """.strip(),
    )

    max_results: Optional[int] = Field(
        default=None,
        description="""
        Maximum number of results to return. If not specified, all matching results will be fetched.
        Note: 'limit' from config controls page size, 'max_results' controls total results.
        Example: limit=100, max_results=250 will fetch 3 pages (100+100+50).
        """.strip(),
    )


class XrayCreateTestInput(BaseModel):
    """Input schema for creating a test in Xray."""

    graphql_mutation: str = Field(
        ...,
        description="""
        GraphQL mutation to create a new test in Xray.

        Example for Manual test:
        mutation {
            createTest(
                testType: { name: "Manual" },
                steps: [
                    { action: "Create first example step", result: "First step was created" },
                    { action: "Create second example step with data", data: "Data for the step", result: "Second step was created with data" }
                ],
                jira: {
                    fields: {
                        summary: "Exploratory Test",
                        project: { key: "CALC" }
                    }
                }
            ) {
                test {
                    issueId
                    testType { name }
                    steps { action data result }
                    jira(fields: ["key"])
                }
                warnings
            }
        }

        Example for Generic test:
        mutation {
            createTest(
                testType: { name: "Generic" },
                unstructured: "Perform exploratory tests on calculator.",
                jira: {
                    fields: {
                        summary: "Exploratory Test",
                        project: { key: "CALC" }
                    }
                }
            ) {
                test {
                    issueId
                    testType { name }
                    unstructured
                    jira(fields: ["key"])
                }
                warnings
            }
        }

        Mutation arguments:
        - testType: Test type (Manual, Generic, Cucumber, etc.)
        - steps: Step definitions for Manual tests
        - unstructured: Text definition for Generic tests
        - gherkin: Gherkin definition for Cucumber tests
        - preconditionIssueIds: Related precondition issue IDs
        - folderPath: Test repository folder path
        - jira: Jira issue fields (summary, project, description, etc.)
        """.strip(),
    )


class XrayExecuteGraphQLInput(BaseModel):
    """Input schema for executing custom GraphQL queries/mutations."""

    graphql: str = Field(
        ...,
        description="""
        Custom GraphQL query or mutation to execute against Xray API.

        Query example:
        query {
            getTests(jql: "project = CALC", limit: 10) {
                results {
                    issueId
                    jira(fields: ["key", "summary"])
                    testType { name }
                }
            }
        }

        Mutation example:
        mutation {
            updateTest(
                issueId: "12345",
                testType: { name: "Manual" }
            ) {
                test {
                    issueId
                    testType { name }
                }
            }
        }

        Refer to Xray GraphQL API documentation for available queries and mutations:
        https://docs.getxray.app/display/XRAYCLOUD/GraphQL+API
        """.strip(),
    )
