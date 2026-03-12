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
from typing import Type, Optional, List, Dict, Any

from azure.devops.connection import Connection
from azure.devops.v7_1.work_item_tracking import TeamContext, Wiql, WorkItemTrackingClient
from langchain_core.tools import ToolException
from msrest.authentication import BasicAuthentication
from pydantic import BaseModel

from codemie_tools.azure_devops.work_item.models import (
    AzureDevOpsWorkItemConfig,
    SearchWorkItemsInput,
    CreateWorkItemInput,
    UpdateWorkItemInput,
    GetWorkItemInput,
    LinkWorkItemsInput,
    GetRelationTypesInput,
    GetCommentsInput,
)
from codemie_tools.azure_devops.work_item.tools_vars import (
    SEARCH_WORK_ITEMS_TOOL,
    CREATE_WORK_ITEM_TOOL,
    UPDATE_WORK_ITEM_TOOL,
    GET_WORK_ITEM_TOOL,
    LINK_WORK_ITEMS_TOOL,
    GET_RELATION_TYPES_TOOL,
    GET_COMMENTS_TOOL,
)
from codemie_tools.base.codemie_tool import CodeMieTool, logger
from codemie_tools.base.file_tool_mixin import FileToolMixin
from codemie_tools.azure_devops.attachment_mixin import AzureDevOpsAttachmentMixin

# Ensure Azure DevOps cache directory is set
if not os.environ.get("AZURE_DEVOPS_CACHE_DIR", None):
    os.environ["AZURE_DEVOPS_CACHE_DIR"] = ""


class BaseAzureDevOpsWorkItemTool(CodeMieTool, AzureDevOpsAttachmentMixin):
    """Base class for Azure DevOps Work Item tools with attachment support."""

    config: AzureDevOpsWorkItemConfig
    __client: Optional[WorkItemTrackingClient] = None
    _relation_types: Dict = {}  # Track actual relation types for instance

    @property
    def _client(self) -> WorkItemTrackingClient:
        """Get or create Azure DevOps client (lazy initialization)."""
        if self.__client is None:
            try:
                # Set up connection to Azure DevOps using Personal Access Token (PAT)
                credentials = BasicAuthentication("", self.config.token)
                connection = Connection(base_url=self.config.organization_url, creds=credentials)
                # Retrieve the work item tracking client
                self.__client = connection.clients_v7_1.get_work_item_tracking_client()
            except Exception as e:
                logger.error(f"Failed to connect to Azure DevOps: {e}")
                raise ToolException(f"Failed to connect to Azure DevOps: {e}")
        return self.__client

    @_client.setter
    def _client(self, value: WorkItemTrackingClient) -> None:
        """Set the Azure DevOps client (useful for testing)."""
        self.__client = value

    def _parse_work_items(self, work_items, fields=None):
        """Parse work items dynamically based on the fields requested."""
        parsed_items = []

        # If no specific fields are provided, default to the basic ones
        if fields is None:
            fields = [
                "System.Title",
                "System.State",
                "System.AssignedTo",
                "System.WorkItemType",
                "System.CreatedDate",
                "System.ChangedDate",
            ]

        fields = [field for field in fields if "System.Id" not in field]
        fields = [field for field in fields if "System.WorkItemType" not in field]
        for item in work_items:
            # Fetch full details of the work item, including the requested fields
            full_item = self._client.get_work_item(id=item.id, project=self.config.project, fields=fields)
            fields_data = full_item.fields

            # Parse the fields dynamically
            parsed_item = {
                "id": full_item.id,
                "url": f"{self.config.organization_url}/_workitems/edit/{full_item.id}",
            }

            # Iterate through the requested fields and add them to the parsed result
            for field in fields:
                parsed_item[field] = fields_data.get(field, "N/A")

            parsed_items.append(parsed_item)

        return parsed_items

    def _transform_work_item(self, work_item_json: str) -> list[dict]:
        try:
            params = json.loads(work_item_json)
        except ValueError as e:
            raise ToolException(f"Issues during attempt to parse work_item_json: {str(e)}")

        if "fields" not in params:
            raise ToolException("The 'fields' property is missing from the work_item_json.")

        return [{"op": "add", "path": f"/fields/{field}", "value": value} for field, value in params["fields"].items()]


class BaseAzureDevOpsFileWorkItemTool(BaseAzureDevOpsWorkItemTool, FileToolMixin):
    """Base class for Azure DevOps Work Item tools with file attachment support."""

    def _process_attachments(self, work_item_id: int) -> list[str]:
        """
        Process and attach all files to the work item.

        Args:
            work_item_id: ID of the work item to attach files to

        Returns:
            List of attachment filenames that were successfully attached
        """
        files = self._resolve_files()

        if not files:
            return []

        attached_files = []
        logger.info(f"Processing {len(files)} attachments for work item {work_item_id}...")

        for filename, (content, _) in files.items():
            try:
                attachment_url = self._upload_attachment(filename, content)

                patch_doc = [
                    {
                        "op": "add",
                        "path": "/relations/-",
                        "value": {
                            "rel": "AttachedFile",
                            "url": attachment_url,
                            "attributes": {"comment": f"Attached file: {filename}"},
                        },
                    }
                ]

                self._client.update_work_item(document=patch_doc, id=work_item_id, project=self.config.project)

                attached_files.append(filename)
                logger.info(f"Attached file '{filename}' to work item {work_item_id}")

            except Exception as e:
                logger.warning(f"Skipping attachment '{filename}' due to error: {str(e)}")

        return attached_files


class SearchWorkItemsTool(BaseAzureDevOpsWorkItemTool):
    """Tool to search work items in Azure DevOps using WIQL queries."""

    name: str = SEARCH_WORK_ITEMS_TOOL.name
    description: str = SEARCH_WORK_ITEMS_TOOL.description
    args_schema: Type[BaseModel] = SearchWorkItemsInput

    def execute(self, query: str, limit: Optional[int] = None, fields: Optional[List[str]] = None):
        """Search for work items using a WIQL query and dynamically fetch fields based on the query."""
        try:
            # Create a Wiql object with the query
            wiql = Wiql(query=query)

            logger.info(f"Search for work items using {query}")
            # Execute the WIQL query
            if not limit:
                limit = self.config.limit
            work_items = self._client.query_by_wiql(
                wiql,
                top=None if limit and limit < 0 else limit,
                team_context=TeamContext(project=self.config.project),
            ).work_items

            if not work_items:
                return "No work items found."

            # Parse the work items and fetch the fields dynamically
            parsed_work_items = self._parse_work_items(work_items, fields)

            # Return the parsed work items
            return parsed_work_items
        except ValueError as ve:
            logger.error(f"Invalid WIQL query: {ve}")
            return f"Invalid WIQL query: {ve}"
        except Exception as e:
            logger.error(f"Error searching work items: {e}")
            return f"Error searching work items: {str(e)}"


class CreateWorkItemTool(BaseAzureDevOpsFileWorkItemTool):
    """Tool to create a work item in Azure DevOps with optional file attachments."""

    name: str = CREATE_WORK_ITEM_TOOL.name
    description: str = CREATE_WORK_ITEM_TOOL.description
    args_schema: Type[BaseModel] = CreateWorkItemInput

    def execute(self, work_item_json: str, wi_type: str = "Task") -> str:
        """Create a work item in Azure DevOps with optional file attachments."""
        try:
            patch_document = self._transform_work_item(work_item_json)
        except Exception as e:
            raise ToolException(f"Issues during attempt to parse work_item_json: {str(e)}")

        try:
            # Use the transformed patch_document to create the work item
            work_item = self._client.create_work_item(
                document=patch_document, project=self.config.project, type=wi_type
            )

            # Process attachments if any
            attached_files = self._process_attachments(work_item.id)

            result_message = f"Work item {work_item.id} created successfully. View it at {work_item.url}."
            if attached_files:
                result_message += f" Attached {len(attached_files)} file(s): {', '.join(attached_files)}"

            return result_message

        except Exception as e:
            logger.error(f"Error creating work item: {e}")
            raise ToolException(f"Error creating work item: {str(e)}")


class UpdateWorkItemTool(BaseAzureDevOpsFileWorkItemTool):
    """Tool to update an existing work item in Azure DevOps with optional file attachments."""

    name: str = UPDATE_WORK_ITEM_TOOL.name
    description: str = UPDATE_WORK_ITEM_TOOL.description
    args_schema: Type[BaseModel] = UpdateWorkItemInput

    def execute(self, id: int, work_item_json: str) -> str:
        """Updates existing work item per defined data with optional file attachments."""
        try:
            patch_document = self._transform_work_item(work_item_json)
        except Exception as e:
            raise ToolException(f"Issues during attempt to parse work_item_json: {str(e)}")

        try:
            work_item = self._client.update_work_item(id=id, document=patch_document, project=self.config.project)

            attached_files = self._process_attachments(work_item.id)

            result_message = f"Work item ({work_item.id}) was updated."
            if attached_files:
                result_message += f" Attached {len(attached_files)} file(s): {', '.join(attached_files)}"

            return result_message
        except Exception as e:
            logger.error(f"Error updating work item: {e}")
            raise ToolException(f"Issues during attempt to update work item: {str(e)}")


class GetWorkItemTool(BaseAzureDevOpsWorkItemTool):
    """Tool to get a single work item by ID from Azure DevOps with optional attachment download."""

    name: str = GET_WORK_ITEM_TOOL.name
    description: str = GET_WORK_ITEM_TOOL.description
    args_schema: Type[BaseModel] = GetWorkItemInput

    def _is_attachment_relation(self, relation_dict: Dict[str, Any]) -> bool:
        """Check if a relation is an AttachedFile relation."""
        return relation_dict.get("rel") == "AttachedFile"

    def _get_filename_from_relation(self, relation_dict: Dict[str, Any]) -> Optional[str]:
        """
        Extract filename from relation attributes or URL.

        Args:
            relation_dict: Relation dictionary

        Returns:
            Filename or None if not found
        """
        attributes = relation_dict.get("attributes", {})
        filename = attributes.get("name", "")

        if filename:
            return filename

        # Try to extract from URL if name not in attributes
        attachment_url = relation_dict.get("url", "")
        if attachment_url and "/" in attachment_url:
            return attachment_url.split("/")[-1]

        return "unknown" if attachment_url else None

    def _download_attachment_safely(self, attachment_url: str, filename: str) -> Optional[bytes]:
        """
        Download attachment with error handling.

        Args:
            attachment_url: URL to download from
            filename: Filename for logging

        Returns:
            Attachment content or None if download fails
        """
        try:
            return self._download_attachment(attachment_url, filename)
        except Exception as e:
            logger.warning(f"Skipping attachment '{filename}' due to error: {str(e)}")
            return None

    def _extract_attachments_from_relations(self, relations_data) -> Dict[str, bytes]:
        """
        Extract and download attachments from work item relations.

        Args:
            relations_data: Work item relations data

        Returns:
            Dict mapping filename to attachment content (bytes)
        """
        if not relations_data:
            return {}

        attachments = {}

        for relation in relations_data:
            relation_dict = relation.as_dict()

            if not self._is_attachment_relation(relation_dict):
                continue

            attachment_url = relation_dict.get("url")
            if not attachment_url:
                continue

            filename = self._get_filename_from_relation(relation_dict)
            if not filename:
                continue

            content = self._download_attachment_safely(attachment_url, filename)
            if content:
                attachments[filename] = content

        return attachments

    def execute(
        self,
        id: int,
        fields: Optional[List[str]] = None,
        as_of: Optional[str] = None,
        expand: Optional[str] = None,
        include_attachments: bool = False,
    ):
        """
        Get a single work item by ID with optional attachment download.

        Args:
            id: Work item ID
            fields: List of requested fields
            as_of: AsOf UTC date time string
            expand: Expand parameters for work item attributes
            include_attachments: Whether to download and return attachment content

        Returns:
            If include_attachments=False: dict with work item data (backward compatible)
            If include_attachments=True: dict with work item data plus 'attachments' key
        """
        try:
            # Determine expand parameter: use provided value, or 'Relations' if attachments needed
            if expand:
                actual_expand = expand
            elif include_attachments:
                actual_expand = "Relations"
            else:
                actual_expand = None

            work_item = self._client.get_work_item(
                id=id, project=self.config.project, fields=fields, as_of=as_of, expand=actual_expand
            )

            # Parse the fields dynamically
            fields_data = work_item.fields
            parsed_item = {
                "id": work_item.id,
                "url": f"{self.config.organization_url}/_workitems/edit/{work_item.id}",
            }

            # Iterate through the requested fields and add them to the parsed result
            if fields:
                for field in fields:
                    parsed_item[field] = fields_data.get(field, "N/A")
            else:
                parsed_item.update(fields_data)

            # extract relations if any
            relations_data = work_item.relations
            if relations_data:
                parsed_item["relations"] = []
                for relation in relations_data:
                    parsed_item["relations"].append(relation.as_dict())

            # Download attachments if requested
            if include_attachments and relations_data:
                attachments = self._extract_attachments_from_relations(relations_data)
                parsed_item["attachments"] = attachments
                parsed_item["attachment_count"] = len(attachments)
                logger.info(f"Downloaded {len(attachments)} attachments for work item {id}")

            return parsed_item
        except Exception as e:
            logger.error(f"Error getting work item: {e}")
            raise ToolException(f"Error getting work item: {str(e)}")


class LinkWorkItemsTool(BaseAzureDevOpsWorkItemTool):
    """Tool to link two work items in Azure DevOps with a specified relationship type."""

    name: str = LINK_WORK_ITEMS_TOOL.name
    description: str = LINK_WORK_ITEMS_TOOL.description
    args_schema: Type[BaseModel] = LinkWorkItemsInput

    def execute(self, source_id: int, target_id: int, link_type: str, attributes: Dict[str, Any] = None):
        """Add the relation to the source work item with an appropriate attributes if any."""
        if not self._relation_types:
            # check cached relation types and trigger its collection if it is empty by that moment
            relation_types = self._client.get_relation_types()
            for relation in relation_types:
                self._relation_types[relation.name] = relation.reference_name

        if link_type not in self._relation_types.values():
            raise ToolException(
                f"Link type is incorrect. You have to use proper "
                f"relation's reference name NOT relation's name: {self._relation_types}"
            )

        try:
            relation = {
                "rel": link_type,
                "url": f"{self.config.organization_url}/_apis/wit/workItems/{target_id}",
            }

            if attributes:
                relation.update({"attributes": attributes})

            self._client.update_work_item(
                document=[{"op": "add", "path": "/relations/-", "value": relation}], id=source_id
            )
            return f"Work item {source_id} linked to {target_id} with link type {link_type}"
        except Exception as e:
            logger.error(f"Error linking work items: {e}")
            raise ToolException(f"Error linking work items: {str(e)}")


class GetRelationTypesTool(BaseAzureDevOpsWorkItemTool):
    """Tool to get all available relation types for work items in Azure DevOps."""

    name: str = GET_RELATION_TYPES_TOOL.name
    description: str = GET_RELATION_TYPES_TOOL.description
    args_schema: Type[BaseModel] = GetRelationTypesInput

    def execute(self):
        """Returns dict of possible relation types per syntax: 'relation name': 'relation reference name'."""
        try:
            if not self._relation_types:
                # have to be called only once for session
                relations = self._client.get_relation_types()
                for relation in relations:
                    self._relation_types[relation.name] = relation.reference_name
            return self._relation_types
        except Exception as e:
            logger.error(f"Error getting relation types: {e}")
            raise ToolException(f"Error getting relation types: {str(e)}")


class GetCommentsTool(BaseAzureDevOpsWorkItemTool):
    """Tool to get comments for a work item by ID from Azure DevOps."""

    name: str = GET_COMMENTS_TOOL.name
    description: str = GET_COMMENTS_TOOL.description
    args_schema: Type[BaseModel] = GetCommentsInput

    def execute(
        self,
        work_item_id: int,
        limit_total: Optional[int] = None,
        include_deleted: Optional[bool] = None,
        expand: Optional[str] = None,
        order: Optional[str] = None,
    ):
        """Get comments for work item by ID."""
        try:
            # Resolve limits to extract in single portion and for whole set of comment
            limit_portion = self.config.limit
            limit_all = limit_total if limit_total else self.config.limit

            # Fetch the work item comments
            comments_portion = self._client.get_comments(
                project=self.config.project,
                work_item_id=work_item_id,
                top=limit_portion,
                include_deleted=include_deleted,
                expand=expand,
                order=order,
            )
            comments_all = []

            while True:
                comments_all += [comment.as_dict() for comment in comments_portion.comments]

                if not comments_portion.continuation_token or len(comments_all) >= limit_all:
                    return comments_all[:limit_all]
                else:
                    comments_portion = self._client.get_comments(
                        continuation_token=comments_portion.continuation_token,
                        project=self.config.project,
                        work_item_id=int(work_item_id),
                        top=3,
                        include_deleted=include_deleted,
                        expand=expand,
                        order=order,
                    )
        except Exception as e:
            logger.error(f"Error getting work item comments: {e}")
            raise ToolException(f"Error getting work item comments: {str(e)}")
