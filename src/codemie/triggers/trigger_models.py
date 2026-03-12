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

from pydantic import BaseModel, Field

from codemie.rest_api.models.index import (
    AzureDevOpsWikiIndexInfo,
    AzureDevOpsWorkItemIndexInfo,
    ConfluenceIndexInfo,
    IndexInfo,
)
from codemie.rest_api.security.user import User


class ReindexTaskPayload(BaseModel):
    """
    Base payload for a reindexing task, encapsulating common parameters.
    This model serves as the core protocol for actor tasks.
    """

    project_name: str = Field(..., description="The name of the project to which the datasource belongs.")
    resource_id: str = Field(..., description="The unique identifier for the reindexing job (e.g., job_id).")
    resource_name: str = Field(..., description="The name of the datasource resource to be reindexed.")
    user: User = Field(..., description="The user initiating the reindex operation.")
    index_info: IndexInfo = Field(..., description="The index information for the datasource.")


class CodeReindexTask(ReindexTaskPayload):
    repo_id: str = Field(..., description="The ID of the Git repository.")


class JiraReindexTask(ReindexTaskPayload):
    jql: str = Field(..., description="The JQL query to filter Jira issues for reindexing.")


class ConfluenceReindexTask(ReindexTaskPayload):
    confluence_index_info: ConfluenceIndexInfo = Field(..., description="The Confluence index information.")


class GoogleReindexTask(ReindexTaskPayload):
    google_doc_link: str = Field(..., description="The link to the Google document to be reindexed.")


class AzureDevOpsWikiReindexTask(ReindexTaskPayload):
    azure_devops_wiki_index_info: AzureDevOpsWikiIndexInfo = Field(
        ..., description="The Azure DevOps Wiki index information."
    )


class AzureDevOpsWorkItemReindexTask(ReindexTaskPayload):
    azure_devops_work_item_index_info: AzureDevOpsWorkItemIndexInfo = Field(
        ..., description="The Azure DevOps Work Items index information."
    )
