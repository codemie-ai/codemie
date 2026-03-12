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

from azure.devops.v7_0.git.git_client import GitClient
from msrest.authentication import BasicAuthentication
from pydantic import BaseModel, ConfigDict

from codemie_tools.base.codemie_tool import logger
from codemie_tools.git.azure_devops.utils import parse_azure_devops_url


class AzureDevOpsCredentials(BaseModel):
    """
    Azure DevOps Credentials model with required fields
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    organization_url: str
    project: str
    repository_id: str
    token: str
    base_branch: str = "main"
    active_branch: Optional[str] = None


class AzureDevOpsClient:
    """Azure DevOps client class for repository operations"""

    def __init__(self, credentials: AzureDevOpsCredentials):
        self.organization_url = credentials.organization_url
        self.project = credentials.project
        self.repository_id = credentials.repository_id
        self.base_branch = credentials.base_branch
        self.active_branch = credentials.active_branch if credentials.active_branch else credentials.base_branch

        # Initialize the client
        auth = BasicAuthentication("", credentials.token)
        self.client = GitClient(base_url=self.organization_url, creds=auth)

        # Verify connection
        try:
            self.verify_connection()
        except Exception as e:
            logger.error(f"Failed to connect to Azure DevOps: {e}")

    def branch_exists(self, branch_name):
        """
        Check if a branch exists in the repository
        """
        try:
            branch = self.client.get_branch(repository_id=self.repository_id, name=branch_name, project=self.project)
            return branch is not None
        except Exception as e:
            logger.error(f"Failed to check if branch exists: {branch_name}, {str(e)}")
            return False

    def verify_connection(self):
        self.client.get_repository(repository_id=self.repository_id, project=self.project)

    @staticmethod
    def init_credentials(*, configs: Dict[str, Any]) -> AzureDevOpsCredentials:
        parsed = parse_azure_devops_url(configs.get("repo_link"))

        credentials = AzureDevOpsCredentials(
            organization_url=configs.get("organization_url") or parsed.get("base_url"),
            project=configs.get("project") or parsed.get("project"),
            token=configs.get("token"),
            base_branch=configs.get("base_branch", "main"),
            repository_id=configs.get("repository_id") or parsed.get("repository"),
        )
        return credentials
