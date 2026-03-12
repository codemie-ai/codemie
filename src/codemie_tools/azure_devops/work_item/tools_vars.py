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
from codemie_tools.azure_devops.work_item.models import AzureDevOpsWorkItemConfig

SEARCH_WORK_ITEMS_TOOL = ToolMetadata(
    name="search_work_items",
    description="""
        Search for work items using a WIQL query and dynamically fetch fields based on the query.

        Arguments:
        - query (str): WIQL query for searching Azure DevOps work items
        - limit (int, optional): Number of items to return. If -1, all items are returned. If not provided, uses default limit.
        - fields (list[str], optional): List of requested fields
        """,
    label="Search Work Items",
    user_description="""
        Searches for work items in Azure DevOps using a WIQL (Work Item Query Language) query.
        Returns work items matching the query criteria with specified fields.
        Before using it, you need to provide:
        1. Azure DevOps organization URL
        2. Project name
        3. Personal Access Token with appropriate permissions
        """.strip(),
    config_class=AzureDevOpsWorkItemConfig,
)

CREATE_WORK_ITEM_TOOL = ToolMetadata(
    name="create_work_item",
    description="""
        Create a work item in Azure DevOps with optional file attachments.

        FILE ATTACHMENTS: If files are provided via input_files, they will be uploaded and automatically linked to the work item.
        Supported file types: All file types (PDF, images, documents, logs, etc.)

        Arguments:
        - work_item_json (str): JSON of the work item fields to create in Azure DevOps, i.e.
                                {
                                   "fields":{
                                      "System.Title":"Implement Registration Form Validation",
                                      "System.Description":"Add validation to the registration form",
                                      "System.AssignedTo":"user@example.com"
                                   }
                                }
        - wi_type (str, optional): Work item type, e.g. 'Task', 'Bug', 'Issue' or 'Epic'. Default is "Task"

        File Attachments:
        - Provide files via the config's input_files field
        - All attached files will be uploaded to Azure DevOps
        - Files are automatically linked to the work item as AttachedFile relations
        - Attachments are uploaded using the Azure DevOps Work Item Attachments API

        Example:
        - Create work item with attachments:
          work_item_json: '{"fields":{"System.Title":"Bug Report","System.State":"New"}}'
          wi_type: "Bug"
          [Provide files via input_files: screenshot.png, logs.txt]
          Result: Work item created with 2 file attachments
        """,
    label="Create Work Item",
    user_description="""
        Creates a new work item in Azure DevOps with the specified fields, work item type, and optional file attachments.
        The tool returns a confirmation message with the ID and URL of the created work item, and lists any attached files.

        Supports attaching files (screenshots, logs, documents, etc.) which will be uploaded and linked to the work item.

        Before using it, you need to provide:
        1. Azure DevOps organization URL
        2. Project name
        3. Personal Access Token with appropriate permissions
        """.strip(),
    config_class=AzureDevOpsWorkItemConfig,
)

UPDATE_WORK_ITEM_TOOL = ToolMetadata(
    name="update_work_item",
    description="""
        Update an existing work item in Azure DevOps with optional file attachments.

        FILE ATTACHMENTS: If files are provided via input_files, they will be uploaded and automatically linked to the work item.
        Supported file types: All file types (PDF, images, documents, logs, etc.)

        Note: Work item type cannot be changed after creation. Use create_work_item to create a new work item of a different type.

        Arguments:
        - id (int): ID of the work item to update
        - work_item_json (str): JSON of the work item fields to update in Azure DevOps, i.e.
                                {
                                   "fields":{
                                      "System.Title":"Updated Title",
                                      "System.State":"Active",
                                      "System.AssignedTo":"user@example.com"
                                   }
                                }

        File Attachments:
        - Provide files via the config's input_files field
        - All attached files will be uploaded to Azure DevOps
        - Files are automatically linked to the work item as AttachedFile relations
        - Attachments are uploaded using the Azure DevOps Work Item Attachments API

        Example:
        - Update work item with attachments:
          id: 12345
          work_item_json: '{"fields":{"System.State":"Resolved","System.AssignedTo":"user@example.com"}}'
          [Provide files via input_files: screenshot.png, logs.txt]
          Result: Work item updated with 2 file attachments
        """,
    label="Update Work Item",
    user_description="""
        Updates an existing work item in Azure DevOps with the specified fields and optional file attachments.
        The tool returns a confirmation message with the ID of the updated work item, and lists any attached files.

        Supports attaching files (screenshots, logs, documents, etc.) which will be uploaded and linked to the work item.

        Before using it, you need to provide:
        1. Azure DevOps organization URL
        2. Project name
        3. Personal Access Token with appropriate permissions
        """.strip(),
    config_class=AzureDevOpsWorkItemConfig,
)

GET_WORK_ITEM_TOOL = ToolMetadata(
    name="get_work_item",
    description="""
        Get a single work item by ID with optional attachment download.

        Arguments:
        - id (int): The work item ID
        - fields (list[str], optional): List of requested fields
        - as_of (str, optional): AsOf UTC date time string
        - expand (str, optional): The expand parameters for work item attributes.
                                  Possible options are { None, Relations, Fields, Links, All }.
        - include_attachments (bool, optional): Whether to download and return attachment content. Default: False.

        Return Format:
        - If include_attachments=False: Returns dict with work item data (backward compatible)
        - If include_attachments=True: Returns dict with work item data plus:
          - 'attachments': dict mapping filename to bytes content
          - 'attachment_count': int (number of attachments downloaded)

        Attachment Download:
        - When include_attachments=True, the tool parses work item relations for AttachedFile relations
        - Downloads all attached files from Azure DevOps
        - Returns file content as bytes for further processing
        - Failed downloads are logged but don't stop the operation

        Examples:
        - Get work item without attachments (default):
          id: 12345
          Result: {\"id\": 12345, \"url\": \"...\", \"fields\": {...}, \"relations\": [...]}
        - Get work item with attachments:
          id: 12345
          include_attachments: True
          Result: {\"id\": 12345, \"url\": \"...\", \"fields\": {...}, \"relations\": [...],
                  \"attachments\": {\"screenshot.png\": b\"...\", \"log.txt\": b\"...\"}, \"attachment_count\": 2}
        """,
    label="Get Work Item",
    user_description="""
        Retrieves a single work item from Azure DevOps by its ID with optional attachment download.
        Returns the work item details including requested fields and relations if specified.

        When include_attachments=True, also downloads and returns the content of all attached files.

        Before using it, you need to provide:
        1. Azure DevOps organization URL
        2. Project name
        3. Personal Access Token with appropriate permissions
        """.strip(),
    config_class=AzureDevOpsWorkItemConfig,
)

LINK_WORK_ITEMS_TOOL = ToolMetadata(
    name="link_work_items",
    description="""
        Add the relation to the source work item with an appropriate attributes if any.

        Arguments:
        - source_id (int): ID of the work item you plan to add link to
        - target_id (int): ID of the work item linked to source one
        - link_type (str): Link type: System.LinkTypes.Dependency-forward, etc.
        - attributes (dict, optional): Dict with attributes used for work items linking.
                                       Example: 'comment': 'Some linking comment'
        """,
    label="Link Work Items",
    user_description="""
        Creates a link between two work items in Azure DevOps.
        The tool establishes a relationship between a source and target work item with the specified link type.
        Before using it, you need to provide:
        1. Azure DevOps organization URL
        2. Project name
        3. Personal Access Token with appropriate permissions
        """.strip(),
    config_class=AzureDevOpsWorkItemConfig,
)

GET_RELATION_TYPES_TOOL = ToolMetadata(
    name="get_relation_types",
    description="""
        Returns dict of possible relation types per syntax: 'relation name': 'relation reference name'.
        NOTE: reference name is used for adding links to the work item
        """,
    label="Get Relation Types",
    user_description="""
        Retrieves all available relation types that can be used to link work items in Azure DevOps.
        Returns a dictionary mapping relation names to their reference names.
        Before using it, you need to provide:
        1. Azure DevOps organization URL
        2. Project name
        3. Personal Access Token with appropriate permissions
        """.strip(),
    config_class=AzureDevOpsWorkItemConfig,
)

GET_COMMENTS_TOOL = ToolMetadata(
    name="get_comments",
    description="""
        Get comments for work item by ID.

        Arguments:
        - work_item_id (int): The work item ID
        - limit_total (int, optional): Max number of total comments to return
        - include_deleted (bool, optional): Specify if the deleted comments should be retrieved
        - expand (str, optional): The expand parameters for comments.
                                  Possible options are { all, none, reactions, renderedText, renderedTextOnly }.
        - order (str, optional): Order in which the comments should be returned. Possible options are { asc, desc }
        """,
    label="Get Comments",
    user_description="""
        Retrieves comments for a specific work item in Azure DevOps.
        Returns a list of comments with their details based on the specified parameters.
        Before using it, you need to provide:
        1. Azure DevOps organization URL
        2. Project name
        3. Personal Access Token with appropriate permissions
        """.strip(),
    config_class=AzureDevOpsWorkItemConfig,
)
