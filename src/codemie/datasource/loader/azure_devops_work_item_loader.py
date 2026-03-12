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

from __future__ import annotations

import re
from typing import Any, Iterator

from azure.devops.connection import Connection
from azure.devops.v7_1.work_item_tracking import WorkItemTrackingClient, Wiql, TeamContext
from langchain_core.document_loaders import BaseLoader
from langchain_core.documents import Document
from msrest.authentication import BasicAuthentication
from pydantic import AnyHttpUrl

from codemie.configs import logger
from codemie.datasource.exceptions import (
    InvalidQueryException,
    MissingIntegrationException,
    UnauthorizedException,
)
from codemie.datasource.loader.base_datasource_loader import BaseDatasourceLoader


def _strip_html(html: str) -> str:
    """Remove HTML tags and collapse whitespace."""
    clean = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", clean).strip()


# Fields fetched for each work item
_DEFAULT_FIELDS = [
    "System.Id",
    "System.Title",
    "System.Description",
    "System.WorkItemType",
    "System.State",
    "System.AssignedTo",
    "System.AreaPath",
    "System.IterationPath",
    "System.Tags",
    "System.CreatedDate",
    "System.ChangedDate",
    "Microsoft.VSTS.Common.Priority",
]


class AzureDevOpsWorkItemLoader(BaseLoader, BaseDatasourceLoader):
    """
    A LangChain loader for Azure DevOps Work Items using the official Azure DevOps SDK.

    Example::

        loader = AzureDevOpsWorkItemLoader(
            base_url="https://dev.azure.com/organization",
            wiql_query="SELECT [System.Id] FROM WorkItems WHERE [System.TeamProject] = @project",
            access_token="<personal_access_token>",
            organization="organization",
            project="project",
        )
    """

    DOCUMENTS_COUNT_KEY = "documents_count_key"

    def __init__(
        self,
        base_url: AnyHttpUrl,
        wiql_query: str,
        access_token: str,
        organization: str,
        project: str,
        batch_size: int = 50,
    ):
        base_url_str = str(base_url).rstrip("/")
        if not base_url_str.endswith(organization):
            self.base_url = f"{base_url_str}/{organization}"
        else:
            self.base_url = base_url_str

        self.wiql_query = (
            wiql_query if wiql_query else "SELECT [System.Id] FROM WorkItems WHERE [System.TeamProject] = @project"
        )
        self.access_token = access_token
        self.organization = organization
        self.project = project
        self.batch_size = batch_size
        self._connection = None
        self._work_item_client: WorkItemTrackingClient | None = None

    def _init_client(self):
        """Initialize Azure DevOps connection and work item tracking client."""
        credentials = BasicAuthentication("", self.access_token)
        self._connection = Connection(base_url=self.base_url, creds=credentials)
        self._work_item_client = self._connection.clients_v7_1.get_work_item_tracking_client()

    def _validate_creds(self):
        """Validate that credentials are correct by querying the project."""
        if not self.access_token:
            logger.error("Missing Access Token for Azure DevOps Work Items integration")
            raise MissingIntegrationException("AzureDevOps Work Items")

        try:
            self._run_wiql("SELECT [System.Id] FROM WorkItems WHERE [System.TeamProject] = @project", top=1)
        except InvalidQueryException:
            raise
        except Exception as e:
            logger.error(f"Cannot authenticate user for Azure DevOps Work Items. Failed with error {e}")
            raise UnauthorizedException(datasource_type="AzureDevOps Work Items")

    def _run_wiql(self, query: str, top: int | None = None) -> list[Any]:
        """Execute a WIQL query and return work item references."""
        try:
            wiql = Wiql(query=query)
            result = self._work_item_client.query_by_wiql(
                wiql,
                top=top,
                team_context=TeamContext(project=self.project),
            )
            return result.work_items or []
        except Exception as e:
            error_str = str(e).lower()
            if any(kw in error_str for kw in ("invalid", "syntax", "tf51005", "wiql")):
                raise InvalidQueryException("WIQL", str(e))
            raise

    def _fetch_work_item(self, work_item_id: int) -> dict[str, Any] | None:
        """Fetch full details of a single work item."""
        try:
            item = self._work_item_client.get_work_item(
                id=work_item_id,
                project=self.project,
                fields=_DEFAULT_FIELDS,
            )
            fields = item.fields or {}
            return {
                "id": item.id,
                "url": item.url,
                "fields": fields,
            }
        except Exception as e:
            logger.warning(f"Failed to fetch work item {work_item_id}: {e}")
            return None

    def _transform_to_doc(self, work_item: dict[str, Any]) -> Document:
        """Transform an Azure DevOps Work Item dict into a LangChain Document."""
        fields = work_item.get("fields", {})
        work_item_id = work_item.get("id")

        title = fields.get("System.Title", "")
        description = _strip_html(fields.get("System.Description") or "")
        wi_type = fields.get("System.WorkItemType", "")
        state = fields.get("System.State", "")
        area_path = fields.get("System.AreaPath", "")
        iteration_path = fields.get("System.IterationPath", "")
        tags = fields.get("System.Tags", "")
        priority = fields.get("Microsoft.VSTS.Common.Priority", "")
        assigned_to = fields.get("System.AssignedTo", {})
        if isinstance(assigned_to, dict):
            assigned_to = assigned_to.get("displayName", "")

        # Compose human-readable content from all fields
        content_parts = [f"# {title}"]
        if description:
            content_parts.append(description)
        if wi_type:
            content_parts.append(f"Type: {wi_type}")
        if state:
            content_parts.append(f"State: {state}")
        if assigned_to:
            content_parts.append(f"Assigned To: {assigned_to}")
        if area_path:
            content_parts.append(f"Area: {area_path}")
        if iteration_path:
            content_parts.append(f"Iteration: {iteration_path}")
        if tags:
            content_parts.append(f"Tags: {tags}")
        if priority:
            content_parts.append(f"Priority: {priority}")

        page_content = "\n".join(content_parts)

        source_url = f"{self.base_url}/{self.project}/_workitems/edit/{work_item_id}"

        metadata = {
            "source": source_url,
            "work_item_id": work_item_id,
            "work_item_type": wi_type,
            "state": state,
            "title": title,
            "area_path": area_path,
            "iteration_path": iteration_path,
        }

        return Document(page_content=page_content, metadata=metadata)

    def lazy_load(self) -> Iterator[Document]:
        """Load work items matching the WIQL query and yield LangChain Documents."""
        self._init_client()
        self._validate_creds()

        work_item_refs = self._run_wiql(self.wiql_query)
        logger.info(f"Found {len(work_item_refs)} work items for query in project {self.project}")

        for batch_start in range(0, len(work_item_refs), self.batch_size):
            batch = work_item_refs[batch_start : batch_start + self.batch_size]
            logger.info(
                f"Processing work items batch {batch_start // self.batch_size + 1}: "
                f"items {batch_start + 1}-{batch_start + len(batch)} of {len(work_item_refs)}"
            )
            for ref in batch:
                item = self._fetch_work_item(ref.id)
                if item:
                    yield self._transform_to_doc(item)

    def _create_stats_response(self, total: int) -> dict[str, Any]:
        return {
            self.DOCUMENTS_COUNT_KEY: total,
            self.TOTAL_DOCUMENTS_KEY: total,
            self.SKIPPED_DOCUMENTS_KEY: 0,
        }

    def fetch_remote_stats(self) -> dict[str, Any]:
        """Count work items matching the WIQL query without fetching full details."""
        self._init_client()
        self._validate_creds()

        try:
            refs = self._run_wiql(self.wiql_query)
            total = len(refs)
            logger.info(f"Health check: {total} work items found in project {self.project}")
            return self._create_stats_response(total)
        except Exception as e:
            logger.error(f"Failed to fetch work item remote stats: {e}")
            return self._create_stats_response(0)
