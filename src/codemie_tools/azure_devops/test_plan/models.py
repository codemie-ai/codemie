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

from typing import Optional, Dict, Any

from pydantic import BaseModel, Field, model_validator, AliasChoices

from codemie_tools.base.models import CodeMieToolConfig, CredentialTypes, RequiredField

# Constants for repeated field descriptions
PROJECT_DESCRIPTION = "Project ID or project name"


class AzureDevOpsTestPlanConfig(CodeMieToolConfig):
    """Configuration for Azure DevOps Test Plan integration.

    Supports both direct configuration and mapping from separate fields:
    - Direct: organization_url, project, token
    - Mapped: url/base_url + organization -> organization_url, access_token -> token
    """

    credential_type: CredentialTypes = Field(default=CredentialTypes.AZURE_DEVOPS, exclude=True, frozen=True)

    organization_url: str = RequiredField(
        description="Azure DevOps organization URL",
        json_schema_extra={
            "placeholder": "https://dev.azure.com/your-organization",
            "help": "https://docs.microsoft.com/en-us/azure/devops/organizations/accounts/create-organization",
        },
    )

    project: str = RequiredField(
        description="Azure DevOps project name", json_schema_extra={"placeholder": "MyProject"}
    )

    token: str = RequiredField(
        description="Personal Access Token (PAT) for authentication",
        validation_alias=AliasChoices("token", "access_token"),
        json_schema_extra={
            "placeholder": "your_personal_access_token",
            "sensitive": True,
            "help": "https://docs.microsoft.com/en-us/azure/devops/organizations/accounts/use-personal-access-tokens-to-authenticate",
        },
    )

    limit: Optional[int] = Field(default=5, description="Default number of items to return in queries")

    @model_validator(mode="before")
    def build_organization_url(cls, values: Dict[str, Any]) -> Dict[str, Any]:
        """Build organization_url from url/base_url + organization if not provided directly.

        Supports legacy mapping:
        - url or base_url + organization -> organization_url
        - access_token -> token (handled by AliasChoices)
        """
        if not isinstance(values, dict):
            return values

        # If organization_url is not provided, try to build it from url + organization
        if "organization_url" not in values:
            url = values.get("url") or values.get("base_url")
            organization = values.get("organization")

            if url and organization:
                base_url = url.rstrip('/')
                values["organization_url"] = f"{base_url}/{organization}"

        return values


# Input models for Azure DevOps test plan operations
class CreateTestPlanInput(BaseModel):
    test_plan_create_params: str = Field(description="JSON of the test plan create parameters")
    project: Optional[str] = Field(description=PROJECT_DESCRIPTION, default=None)


class DeleteTestPlanInput(BaseModel):
    plan_id: int = Field(description="ID of the test plan to be deleted")
    project: Optional[str] = Field(description=PROJECT_DESCRIPTION, default=None)


class GetTestPlanInput(BaseModel):
    plan_id: Optional[int] = Field(description="ID of the test plan to get", default=None)
    project: Optional[str] = Field(description=PROJECT_DESCRIPTION, default=None)


class CreateTestSuiteInput(BaseModel):
    test_suite_create_params: str = Field(description="JSON of the test suite create parameters")
    plan_id: int = Field(description="ID of the test plan that contains the suites")
    project: Optional[str] = Field(description=PROJECT_DESCRIPTION, default=None)


class DeleteTestSuiteInput(BaseModel):
    plan_id: int = Field(description="ID of the test plan that contains the suite")
    suite_id: int = Field(description="ID of the test suite to delete")
    project: Optional[str] = Field(description=PROJECT_DESCRIPTION, default=None)


class GetTestSuiteInput(BaseModel):
    plan_id: int = Field(description="ID of the test plan that contains the suites")
    suite_id: Optional[int] = Field(description="ID of the suite to get", default=None)
    project: Optional[str] = Field(description=PROJECT_DESCRIPTION, default=None)


class AddTestCaseInput(BaseModel):
    suite_test_case_create_update_parameters: str = Field(
        description='JSON array of the suite test case create update parameters. Example: "[{"work_item":{"id":"23"}}]"'
    )
    plan_id: int = Field(description="ID of the test plan to which test cases are to be added")
    suite_id: int = Field(description="ID of the test suite to which test cases are to be added")
    project: Optional[str] = Field(description=PROJECT_DESCRIPTION, default=None)


class GetTestCaseInput(BaseModel):
    plan_id: int = Field(description="ID of the test plan for which test cases are requested")
    suite_id: int = Field(description="ID of the test suite for which test cases are requested")
    test_case_id: str = Field(description="Test Case Id to be fetched")
    project: Optional[str] = Field(description=PROJECT_DESCRIPTION, default=None)


class GetTestCasesInput(BaseModel):
    plan_id: int = Field(description="ID of the test plan for which test cases are requested")
    suite_id: int = Field(description="ID of the test suite for which test cases are requested")
    project: Optional[str] = Field(description=PROJECT_DESCRIPTION, default=None)
