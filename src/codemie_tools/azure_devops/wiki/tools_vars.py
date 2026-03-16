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
from codemie_tools.azure_devops.wiki.models import AzureDevOpsWikiConfig

GET_WIKI_TOOL = ToolMetadata(
    name="get_wiki",
    description="""
        Extract ADO wiki information. Takes a wiki identifier (name or ID) and returns detailed information about the wiki,
        including its ID, name, URL, remote URL, type, and associated project and repository IDs.

        Arguments:
        - wiki_identified (str): Wiki ID or wiki name to extract information about.
        Example: "MyWiki.wiki". Regularly, ".wiki" is essential.
        E.g. https://dev.azure.com/Organization/Project/_wiki/wikis/CodeMie.wiki/10/How-to-Create-Angular-Application
        "CodeMie.wiki" is the wiki identifier in this case.
        "How-to-Create-Angular-Application" is the page name.
        """,
    label="Get Wiki",
    user_description="""
        Retrieves information about a specific Azure DevOps wiki. The tool provides details about the wiki
        such as its ID, name, URL, and other metadata from the Azure DevOps project.
        Before using it, you need to provide:
        1. Azure DevOps organization URL
        2. Project name
        3. Personal Access Token with appropriate permissions
        """.strip(),
    config_class=AzureDevOpsWikiConfig,
)

GET_WIKI_PAGE_BY_PATH_TOOL = ToolMetadata(
    name="get_wiki_page_by_path",
    description="""
        Extract ADO wiki page content by path with optional attachment download. Retrieves the full content of a wiki page using the page path.
        The content is returned as Markdown text. Optionally downloads and returns attachment file content.

        IMPORTANT: When extracting from Azure DevOps wiki URLs, ALWAYS use the '/{page_id}/{page-slug}' format.
        The tool will automatically resolve nested pages by discovering the full hierarchical path using the page ID.

        Arguments:
        - wiki_identified (str): Wiki ID or wiki name. Example: "MyWiki.wiki". Regularly, ".wiki" is essential.
        - page_name (str): Wiki page path in one of these formats:
          1. FROM URL (RECOMMENDED): Extract the '/{page_id}/{page-slug}' portion from the URL
             Example URL: https://dev.azure.com/Org/Proj/_wiki/wikis/MyWiki.wiki/10/How-to-Create-App
             Use page_name: "/10/How-to-Create-App" (the tool will resolve full nested path automatically)
          2. FULL PATH: For direct path like "/Home" or "/Parent/Child/Page"
        - include_attachments (bool, optional): Whether to download and return attachment content. Default: False.

        Return Format:
        - If include_attachments=False: Returns str (page markdown content only) - BACKWARD COMPATIBLE
        - If include_attachments=True: Returns dict with:
          - 'content': str (page markdown content)
          - 'attachments': dict mapping filename to bytes content
          - 'attachment_count': int (number of attachments downloaded)

        Attachment Download:
        - When include_attachments=True, the tool parses markdown for attachment links
        - Downloads all files referenced in markdown links containing '/_apis/wit/attachments/'
        - Returns file content as bytes for further processing
        - Failed downloads are logged but don't stop the operation

        Examples:
        - URL: https://dev.azure.com/Organization/Project/_wiki/wikis/CodeMie.wiki/10330/This-is-sub-page
          wiki_identified: "CodeMie.wiki"
          page_name: "/10330/This-is-sub-page" (ALWAYS use this format from URLs)
        - Get page with attachments:
          wiki_identified: "CodeMie.wiki"
          page_name: "/Documentation"
          include_attachments: True
          Result: {"content": "...", "attachments": {"file.pdf": b"...", "image.png": b"..."}, "attachment_count": 2}
        """,
    label="Get Wiki Page By Path",
    user_description="""
        Retrieves the content of a wiki page by its path with optional attachment download. The tool returns the Markdown content
        of the specified wiki page in the Azure DevOps project. For wiki URLs, extract the page ID and slug portion (e.g., '/123/Page-Name')
        and the tool will automatically resolve nested page paths.

        When include_attachments=True, also downloads and returns the content of all attached files.

        Before using it, you need to provide:
        1. Azure DevOps organization URL
        2. Project name
        3. Personal Access Token with appropriate permissions
        """.strip(),
    config_class=AzureDevOpsWikiConfig,
)

GET_WIKI_PAGE_BY_ID_TOOL = ToolMetadata(
    name="get_wiki_page_by_id",
    description="""
        Extract ADO wiki page content by ID with optional attachment download. Retrieves the full content of a wiki page using the page ID.
        The content is returned as Markdown text. Optionally downloads and returns attachment file content.

        Arguments:
        - wiki_identified (str): Wiki ID or wiki name. Example: "MyWiki.wiki". Regularly, ".wiki" is essential.
        - page_id (int): Wiki page ID (numeric identifier)
        - include_attachments (bool, optional): Whether to download and return attachment content. Default: False.

        Return Format:
        - If include_attachments=False: Returns str (page markdown content only) - BACKWARD COMPATIBLE
        - If include_attachments=True: Returns dict with:
          - 'content': str (page markdown content)
          - 'attachments': dict mapping filename to bytes content
          - 'attachment_count': int (number of attachments downloaded)

        Attachment Download:
        - When include_attachments=True, the tool parses markdown for attachment links
        - Downloads all files referenced in markdown links containing '/_apis/wit/attachments/'
        - Returns file content as bytes for further processing
        - Failed downloads are logged but don't stop the operation

        Examples:
        - URL: https://dev.azure.com/Organization/Project/_wiki/wikis/CodeMie.wiki/10/How-to-Create-Angular-Application
          "CodeMie.wiki" is the wiki identifier
          "10" is the page id
        - Get page with attachments:
          wiki_identified: "CodeMie.wiki"
          page_id: 10
          include_attachments: True
          Result: {"content": "...", "attachments": {"file.pdf": b"...", "image.png": b"..."}, "attachment_count": 2}
        """,
    label="Get Wiki Page By ID",
    user_description="""
        Retrieves the content of a wiki page by its ID with optional attachment download. The tool returns the Markdown content
        of the specified wiki page in the Azure DevOps project.

        When include_attachments=True, also downloads and returns the content of all attached files.

        Before using it, you need to provide:
        1. Azure DevOps organization URL
        2. Project name
        3. Personal Access Token with appropriate permissions
        """.strip(),
    config_class=AzureDevOpsWikiConfig,
)

DELETE_PAGE_BY_PATH_TOOL = ToolMetadata(
    name="delete_page_by_path",
    description="""
        Delete a wiki page by its path. Permanently removes the specified wiki page from the project's wiki.

        IMPORTANT: When extracting from Azure DevOps wiki URLs, ALWAYS use the '/{page_id}/{page-slug}' format.
        The tool will automatically resolve nested pages by discovering the full hierarchical path using the page ID.

        Arguments:
        - wiki_identified (str): Wiki ID or wiki name. Example: "MyWiki.wiki". Regularly, ".wiki" is essential.
        - page_name (str): Wiki page path in one of these formats:
          1. FROM URL (RECOMMENDED): Extract the '/{page_id}/{page-slug}' portion from the URL
             Example URL: https://dev.azure.com/Org/Proj/_wiki/wikis/MyWiki.wiki/10/How-to-Create-App
             Use page_name: "/10/How-to-Create-App" (the tool will resolve full nested path automatically)
          2. FULL PATH: For direct path like "/Home" or "/Parent/Child/Page"

        Examples:
        - URL: https://dev.azure.com/Organization/Project/_wiki/wikis/CodeMie.wiki/10330/This-is-sub-page
          wiki_identified: "CodeMie.wiki"
          page_name: "/10330/This-is-sub-page" (ALWAYS use this format from URLs)
        """,
    label="Delete Wiki Page By Path",
    user_description="""
        Deletes a wiki page identified by its path. The tool removes the specified wiki page from the
        Azure DevOps project wiki. For wiki URLs, extract the page ID and slug portion (e.g., '/123/Page-Name')
        and the tool will automatically resolve nested page paths.
        Before using it, you need to provide:
        1. Azure DevOps organization URL
        2. Project name
        3. Personal Access Token with appropriate permissions
        """.strip(),
    config_class=AzureDevOpsWikiConfig,
)

DELETE_PAGE_BY_ID_TOOL = ToolMetadata(
    name="delete_page_by_id",
    description="""
        Delete a wiki page by its ID. Permanently removes the specified wiki page from the project's wiki.

        Arguments:
        - wiki_identified (str): Wiki ID or wiki name. Example: "MyWiki.wiki". Regularly, ".wiki" is essential.
        - page_id (int): Wiki page ID to delete (numeric identifier)
        """,
    label="Delete Wiki Page By ID",
    user_description="""
        Deletes a wiki page identified by its ID. The tool removes the specified wiki page from the
        Azure DevOps project wiki.
        Before using it, you need to provide:
        1. Azure DevOps organization URL
        2. Project name
        3. Personal Access Token with appropriate permissions
        """.strip(),
    config_class=AzureDevOpsWikiConfig,
)

RENAME_WIKI_PAGE_TOOL = ToolMetadata(
    name="rename_wiki_page",
    description="""
        Rename an existing wiki page in Azure DevOps. This tool ONLY renames existing pages and will fail if the page doesn't exist.

        IMPORTANT: When extracting from Azure DevOps wiki URLs, ALWAYS use the '/{page_id}/{page-slug}' format.
        The tool will automatically resolve nested pages by discovering the full hierarchical path using the page ID.

        Arguments:
        - wiki_identified (str): Wiki ID or wiki name. Example: "MyWiki.wiki". Regularly, ".wiki" is essential.
        - old_page_name (str): Current page path to be renamed. Supports:
          1. FROM URL (RECOMMENDED): Extract the '/{page_id}/{page-slug}' portion from the URL
             Example URL: https://dev.azure.com/Org/Proj/_wiki/wikis/MyWiki.wiki/10/How-to-Create-App
             Use old_page_name: "/10/How-to-Create-App" (the tool will resolve full nested path automatically)
          2. FULL PATH: For direct path like "/OldName" or "/Parent/Child/OldName"
        - new_page_name (str): New page name or full path:
          1. JUST NAME: "NewName" - keeps page in the same parent directory (rename in place)
          2. FULL PATH: "/New/Location/Page" - moves page to a different location
        - version_identifier (str): Version string identifier (name of tag/branch, SHA1 of commit)
        - version_type (str, optional): Version type (branch, tag, or commit). Default is "branch"

        Examples:
        - Rename in place:
          URL: https://dev.azure.com/Organization/Project/_wiki/wikis/CodeMie.wiki/10330/This-is-sub-page
          old_page_name: "/10330/This-is-sub-page" (resolves to "/Parent/Child/Old Page")
          new_page_name: "Renamed Page" (becomes "/Parent/Child/Renamed Page")
        - Move to different location:
          old_page_name: "/10330/This-is-sub-page"
          new_page_name: "/New Parent/Renamed Page"
        """,
    label="Rename Wiki Page",
    user_description="""
        Renames an existing wiki page. The page must already exist. For wiki URLs, extract the page ID
        and slug portion (e.g., '/123/Page-Name') and the tool will automatically resolve nested page paths.
        Before using it, you need to provide:
        1. Azure DevOps organization URL
        2. Project name
        3. Personal Access Token with appropriate permissions
        4. Version identifier (e.g., branch name or commit SHA)
        """.strip(),
    config_class=AzureDevOpsWikiConfig,
)

MOVE_WIKI_PAGE_TOOL = ToolMetadata(
    name="move_wiki_page",
    description="""
        Move a wiki page to a different location in the wiki structure. This tool uses the Azure DevOps page-moves
        endpoint to properly relocate pages while preserving metadata, history, and references.

        Use this tool when you need to reorganize wiki pages by moving them to different parent pages or locations
        within the wiki hierarchy. This is the proper way to re-arrange wiki pages - NOT by creating, copying, and
        deleting pages.

        IMPORTANT: When extracting from Azure DevOps wiki URLs, ALWAYS use the '/{page_id}/{page-slug}' format.
        The tool will automatically resolve nested pages by discovering the full hierarchical path using the page ID.

        Arguments:
        - wiki_identified (str): Wiki ID or wiki name. Example: "MyWiki.wiki". Regularly, ".wiki" is essential.
        - source_page_path (str): Current page path to move. Supports:
          1. FROM URL (RECOMMENDED): Extract the '/{page_id}/{page-slug}' portion from the URL
             Example URL: https://dev.azure.com/Org/Proj/_wiki/wikis/MyWiki.wiki/10/How-to-Create-App
             Use source_page_path: "/10/How-to-Create-App" (the tool will resolve full nested path automatically)
          2. FULL PATH: For direct path like "/OldLocation/Page" or "/Parent/Child/Page"
        - destination_page_path (str): Destination path where the page will be moved. Must be a full path.
          Examples: "/New-Parent/Moved-Page", "/Different-Section/Page", "/Moved-Page"
        - version_identifier (str): Version string identifier (name of tag/branch, SHA1 of commit)
        - version_type (str, optional): Version type (branch, tag, or commit). Default is "branch"

        Page Move Behavior:
        - Preserves all metadata, version history, and attachments
        - Updates all internal references automatically
        - Maintains page relationships and hierarchy
        - Creates proper audit trail in Azure DevOps

        Examples:
        - Move page to different parent:
          URL: https://dev.azure.com/Organization/Project/_wiki/wikis/CodeMie.wiki/10330/This-is-sub-page
          source_page_path: "/10330/This-is-sub-page" (resolves to "/OldParent/Child/Page")
          destination_page_path: "/NewParent/Child/Page"
          Result: Page moved with all metadata preserved
        - Reorganize wiki structure:
          source_page_path: "/Documentation/Old-Section/Guide"
          destination_page_path: "/Documentation/New-Section/Guide"
          Result: Page logically re-arranged in wiki hierarchy
        """,
    label="Move Wiki Page",
    user_description="""
        Moves a wiki page to a different location within the wiki structure. This tool properly relocates pages
        while preserving all metadata, version history, and references. This is the correct way to re-arrange
        wiki pages for better organization.

        For wiki URLs, extract the page ID and slug portion (e.g., '/123/Page-Name') and the tool will
        automatically resolve nested page paths.

        Before using it, you need to provide:
        1. Azure DevOps organization URL
        2. Project name
        3. Personal Access Token with appropriate permissions
        4. Version identifier (e.g., branch name or commit SHA)
        """.strip(),
    config_class=AzureDevOpsWikiConfig,
)

CREATE_WIKI_PAGE_TOOL = ToolMetadata(
    name="create_wiki_page",
    description="""
        Create a new ADO wiki page with optional file attachments. Creates a new page under the specified parent page path.
        If the wiki doesn't exist, it will be automatically created.

        FILE ATTACHMENTS: If files are provided via input_files, they will be uploaded and automatically linked at the end of the page.
        Supported file types: All file types (PDF, images, documents, etc.)

        IMPORTANT: When extracting parent page from Azure DevOps wiki URLs, ALWAYS use the '/{page_id}/{page-slug}' format.
        The tool will automatically resolve nested pages by discovering the full hierarchical path using the page ID.

        Arguments:
        - wiki_identified (str): Wiki ID or wiki name. Example: "MyWiki.wiki". Regularly, ".wiki" is essential.
        - parent_page_path (str): Parent page path where the new page will be created. Supports:
          1. FROM URL (RECOMMENDED): Extract the '/{page_id}/{page-slug}' portion from the URL
             Example URL: https://dev.azure.com/Org/Proj/_wiki/wikis/MyWiki.wiki/10/Parent-Page
             Use parent_page_path: "/10/Parent-Page" (the tool will resolve full nested path automatically)
          2. ROOT LEVEL: Use '/' for root level pages
          3. FULL PATH: For direct path like "/Parent Page" or "/Parent/Child"
        - new_page_name (str): Name of the new page to create (without path, just the name).
          Example: 'My New Page'
        - page_content (str): Markdown content for the new wiki page
        - version_identifier (str): Version string identifier (name of tag/branch, SHA1 of commit)
        - version_type (str, optional): Version type (branch, tag, or commit). Default is "branch".

        File Attachments:
        - Provide files via the config's input_files field
        - All attached files will be uploaded to Azure DevOps
        - Markdown links will be automatically appended to the page content under "## Attachments" section
        - Attachments are uploaded using the Azure DevOps Work Item Attachments API

        Examples:
        - Create under page from URL:
          URL: https://dev.azure.com/Org/Proj/_wiki/wikis/MyWiki.wiki/10395/Page-for-editing
          parent_page_path: "/10395/Page-for-editing" (ALWAYS use this format from URLs)
          new_page_name: "Created Page"
          Result: Resolves parent path and creates nested page
        - Create root level page:
          parent_page_path: "/"
          new_page_name: "My New Page"
          Result: Creates page at "/My New Page"
        - Create page with attachments:
          parent_page_path: "/"
          new_page_name: "Documentation"
          page_content: "# Documentation\\n\\nSee attached files."
          [Provide files via input_files]
          Result: Creates page with markdown links to uploaded files
        """,
    label="Create Wiki Page",
    user_description="""
        Creates a new wiki page under the specified parent page path with optional file attachments.
        If the wiki doesn't exist, it will be created. For wiki URLs, extract the page ID and slug
        portion (e.g., '/123/Page-Name') and the tool will automatically resolve nested page paths.

        Supports attaching files (PDF, images, documents, etc.) which will be uploaded and linked at
        the end of the page content.

        Before using it, you need to provide:
        1. Azure DevOps organization URL
        2. Project name
        3. Personal Access Token with appropriate permissions
        4. Version identifier (e.g., branch name or commit SHA)
        """.strip(),
    config_class=AzureDevOpsWikiConfig,
)

MODIFY_WIKI_PAGE_TOOL = ToolMetadata(
    name="modify_wiki_page",
    description="""
        Update existing ADO wiki page content. This tool ONLY updates existing pages and will fail if the page doesn't exist.
        Use 'create_wiki_page' tool to create new pages.

        IMPORTANT: When extracting from Azure DevOps wiki URLs, ALWAYS use the '/{page_id}/{page-slug}' format.
        The tool will automatically resolve nested pages by discovering the full hierarchical path using the page ID.

        Arguments:
        - wiki_identified (str): Wiki ID or wiki name. Example: "MyWiki.wiki". Regularly, ".wiki" is essential.
        - page_name (str): Wiki page path in one of these formats:
          1. FROM URL (RECOMMENDED): Extract the '/{page_id}/{page-slug}' portion from the URL
             Example URL: https://dev.azure.com/Org/Proj/_wiki/wikis/MyWiki.wiki/10/How-to-Create-App
             Use page_name: "/10/How-to-Create-App" (the tool will resolve full nested path automatically)
          2. FULL PATH: For direct path like "/Home" or "/Parent/Child/Page"
        - page_content (str): Markdown content for the wiki page
        - version_identifier (str): Version string identifier (name of tag/branch, SHA1 of commit)
        - version_type (str, optional): Version type (branch, tag, or commit). Default is "branch".

        Examples:
        - URL: https://dev.azure.com/Organization/Project/_wiki/wikis/CodeMie.wiki/10330/This-is-sub-page
          wiki_identified: "CodeMie.wiki"
          page_name: "/10330/This-is-sub-page" (ALWAYS use this format from URLs)
        """,
    label="Modify Wiki Page",
    user_description="""
        Updates an existing wiki page with the specified content. The page must already exist.
        For wiki URLs, extract the page ID and slug portion (e.g., '/123/Page-Name')
        and the tool will automatically resolve nested page paths.
        Before using it, you need to provide:
        1. Azure DevOps organization URL
        2. Project name
        3. Personal Access Token with appropriate permissions
        4. Version identifier (e.g., branch name or commit SHA)
        """.strip(),
    config_class=AzureDevOpsWikiConfig,
)

SEARCH_WIKI_PAGES_TOOL = ToolMetadata(
    name="search_wiki_pages",
    description="""
        Search for specific text content across all wiki pages. Performs full-text search and returns matching pages
        with content snippets showing where the text was found.

        Arguments:
        - wiki_identified (str): Wiki ID or wiki name. Example: "MyWiki.wiki". Regularly, ".wiki" is essential.
        - search_text (str): Text to search for (case-insensitive). Can be a word, phrase, or partial text.
        - include_context (bool, optional): Whether to include content snippets. Default is True.
        - max_results (int, optional): Maximum number of results to return (max 100). Default is 50.

        Returns:
        - List of matching pages with:
          - Full page URL
          - Page path
          - Page metadata (project, wiki, collection)
          - Content snippets (if include_context=True) showing where text was found

        Example:
        - Search all pages:
          wiki_identified: "CodeMie.wiki"
          search_text: "kubernetes deployment"
          Result: Finds all pages containing "kubernetes deployment" with clickable URLs
        """,
    label="Search Wiki Pages",
    user_description="""
        Searches for specific text content across all wiki pages. Results include page information with
        clickable URLs and content snippets showing where the search text was found.
        Before using it, you need to provide:
        1. Azure DevOps organization URL
        2. Project name
        3. Personal Access Token with appropriate permissions
        """.strip(),
    settings_config=False,
    config_class=AzureDevOpsWikiConfig,
)

GET_WIKI_PAGE_COMMENTS_BY_ID_TOOL = ToolMetadata(
    name="get_wiki_page_comments_by_id",
    description="""
        Retrieve comments from an Azure DevOps wiki page by page ID. Returns all comments with support for
        pagination, filtering, and sorting. Uses an undocumented Azure DevOps API endpoint.

        Arguments:
        - wiki_identified (str): Wiki ID or wiki name. Example: "MyWiki.wiki". Regularly, ".wiki" is essential.
        - page_id (int): Wiki page ID (numeric identifier)
        - limit_total (int, optional): Maximum number of comments to return. Default: None (all comments)
        - include_deleted (bool, optional): Include deleted comments. Default: False
        - expand (str, optional): Expand comment details. Options: { all, none, reactions, renderedText, renderedTextOnly }. Default: "none"
        - order (str, optional): Sort order. Options: { asc, desc }. Default: None

        Return Format:
        Returns dict with:
        - 'comments': List of comment objects with id, text, created_date, modified_date, created_by, modified_by
        - 'count': Number of comments returned
        - 'total_count': Total number of comments available
        - 'has_more': Boolean indicating if more comments are available

        Examples:
        - URL: https://dev.azure.com/Organization/Project/_wiki/wikis/CodeMie.wiki/10/How-to-Create-App
          wiki_identified: "CodeMie.wiki"
          page_id: 10
          Result: {"comments": [...], "count": 5, "total_count": 5, "has_more": False}
        - Get comments with pagination:
          wiki_identified: "CodeMie.wiki"
          page_id: 10
          limit_total: 10
          order: "desc"
          Result: Latest 10 comments in descending order
        """,
    label="Get Wiki Page Comments By ID",
    user_description="""
        Retrieves comments from a wiki page by its ID. Returns comment details including author, timestamps,
        and content. Supports pagination and filtering for large comment threads.

        Before using it, you need to provide:
        1. Azure DevOps organization URL
        2. Project name
        3. Personal Access Token with appropriate permissions
        """.strip(),
    config_class=AzureDevOpsWikiConfig,
)

GET_WIKI_PAGE_COMMENTS_BY_PATH_TOOL = ToolMetadata(
    name="get_wiki_page_comments_by_path",
    description="""
        Retrieve comments from an Azure DevOps wiki page by page path. Automatically resolves the page ID from the path.
        Returns all comments with support for pagination, filtering, and sorting. Uses an undocumented Azure DevOps API endpoint.

        IMPORTANT: When extracting from Azure DevOps wiki URLs, ALWAYS use the '/{page_id}/{page-slug}' format.
        The tool will automatically resolve nested pages by discovering the full hierarchical path using the page ID.

        Arguments:
        - wiki_identified (str): Wiki ID or wiki name. Example: "MyWiki.wiki". Regularly, ".wiki" is essential.
        - page_name (str): Wiki page path in one of these formats:
          1. FROM URL (RECOMMENDED): Extract the '/{page_id}/{page-slug}' portion from the URL
             Example URL: https://dev.azure.com/Org/Proj/_wiki/wikis/MyWiki.wiki/10/How-to-Create-App
             Use page_name: "/10/How-to-Create-App" (the tool will resolve page ID automatically)
          2. FULL PATH: For direct path like "/Home" or "/Parent/Child/Page"
        - limit_total (int, optional): Maximum number of comments to return. Default: None (all comments)
        - include_deleted (bool, optional): Include deleted comments. Default: False
        - expand (str, optional): Expand comment details. Options: { all, none, reactions, renderedText, renderedTextOnly }. Default: "none"
        - order (str, optional): Sort order. Options: { asc, desc }. Default: None

        Return Format:
        Returns dict with:
        - 'comments': List of comment objects with id, text, created_date, modified_date, created_by, modified_by
        - 'count': Number of comments returned
        - 'total_count': Total number of comments available
        - 'has_more': Boolean indicating if more comments are available

        Examples:
        - URL: https://dev.azure.com/Organization/Project/_wiki/wikis/CodeMie.wiki/10330/This-is-sub-page
          wiki_identified: "CodeMie.wiki"
          page_name: "/10330/This-is-sub-page" (ALWAYS use this format from URLs)
          Result: {"comments": [...], "count": 3, "total_count": 3, "has_more": False}
        - Get comments from full path:
          wiki_identified: "CodeMie.wiki"
          page_name: "/Documentation/API-Guide"
          limit_total: 20
          order: "asc"
          Result: Up to 20 comments in ascending order
        """,
    label="Get Wiki Page Comments By Path",
    user_description="""
        Retrieves comments from a wiki page by its path. Automatically resolves the page ID from the path.
        For wiki URLs, extract the page ID and slug portion (e.g., '/123/Page-Name') and the tool will
        automatically handle the resolution. Returns comment details including author, timestamps, and content.

        Before using it, you need to provide:
        1. Azure DevOps organization URL
        2. Project name
        3. Personal Access Token with appropriate permissions
        """.strip(),
    config_class=AzureDevOpsWikiConfig,
)

ADD_ATTACHMENT_TOOL = ToolMetadata(
    name="add_attachment_to_wiki_page",
    description="""
        Add file attachments to an existing Azure DevOps wiki page. Uploads files using the official Wiki Attachments API
        and automatically appends markdown links to the page content under an "## Attachments" section.

        IMPORTANT: This tool ONLY works with existing pages. The page must already exist. Use 'create_wiki_page' to create new pages.

        FILE ATTACHMENTS: If files are provided via input_files, they will be uploaded to the wiki and automatically
        linked at the end of the page. Maximum file size: 19MB per file (default limit).

        Arguments:
        - wiki_identified (str): Wiki ID or wiki name. Example: "MyWiki.wiki". Regularly, ".wiki" is essential.
        - page_name (str): Wiki page path in one of these formats:
          1. FROM URL (RECOMMENDED): Extract the '/{page_id}/{page-slug}' portion from the URL
             Example URL: https://dev.azure.com/Org/Proj/_wiki/wikis/MyWiki.wiki/10/How-to-Create-App
             Use page_name: "/10/How-to-Create-App" (the tool will resolve full nested path automatically)
          2. FULL PATH: For direct path like "/Home" or "/Parent/Child/Page"
        - version_identifier (str): Version string identifier (name of tag/branch, SHA1 of commit)
        - version_type (str, optional): Version type (branch, tag, or commit). Default is "branch".

        File Requirements:
        - Provide files via the config's input_files field
        - Maximum file size: 19MB per file
        - All file types supported (PDF, images, documents, logs, etc.)
        - Files are uploaded using Azure DevOps Wiki Attachments API (api-version: 7.2-preview.1)

        Return Format:
        - Returns dict with:
          - 'message': Success message
          - 'attachments_added': Number of files uploaded
          - 'page_url': Full URL to the updated wiki page

        Usage Scenarios:
        - Attach generated diagrams or architecture charts to documentation pages
        - Upload API response examples, screenshots, or error logs to wiki pages
        - Add test results, reports, or analytics files to project documentation
        - Attach meeting notes, presentations, or design documents to wiki pages
        - Upload bug reproduction files or log files to incident documentation

        Examples:
        - Attach files to page from URL:
          URL: https://dev.azure.com/Organization/Project/_wiki/wikis/CodeMie.wiki/10330/Documentation
          wiki_identified: "CodeMie.wiki"
          page_name: "/10330/Documentation" (ALWAYS use this format from URLs)
          [Provide PDF, image files via input_files]
          Result: Files uploaded, markdown links appended to page
        - Attach log files to troubleshooting page:
          wiki_identified: "MyWiki.wiki"
          page_name: "/Troubleshooting/Error-Investigation"
          [Provide log files via input_files]
          Result: Log files attached and linked in "## Attachments" section
        """,
    label="Add Attachment to Wiki Page",
    user_description="""
        Uploads files and attaches them to an existing wiki page. The tool adds markdown links for the
        uploaded files at the end of the page content under an "## Attachments" section.

        The page must already exist - use 'create_wiki_page' to create new pages first.
        Supports all file types with a maximum size of 10MB per file.

        Before using it, you need to provide:
        1. Azure DevOps organization URL
        2. Project name
        3. Personal Access Token with Wiki edit permissions
        4. Version identifier (e.g., branch name or commit SHA)
        5. Files to attach via input_files field
        """.strip(),
    config_class=AzureDevOpsWikiConfig,
)

GET_PAGE_STATS_BY_ID_TOOL = ToolMetadata(
    name="get_wiki_page_stats_by_id",
    description="""
        Retrieve view statistics for an Azure DevOps wiki page by its ID. Returns the number of page views
        per day over a configurable time window, allowing you to identify frequently visited or neglected pages.

        Arguments:
        - wiki_identified (str): Wiki ID or wiki name. Example: "MyWiki.wiki". Regularly, ".wiki" is essential.
        - page_id (int): Wiki page ID (numeric identifier)
        - page_views_for_days (int, optional): Number of last days to retrieve statistics for (1–30).
          Default is 30. Azure DevOps does not support more than 30 days.

        Return Format:
        Returns dict with:
        - 'page_id': The page ID
        - 'path': Full wiki page path
        - 'total_views': Total number of views in the requested period
        - 'days_with_views': Number of days the page was viewed at least once
        - 'view_stats': List of daily view records with 'day' (date) and 'count' (views on that day)
        - 'is_visited': Boolean indicating if the page was viewed at least once in the requested period
        - 'page_views_for_days': The number of days the statistics cover

        Examples:
        - URL: https://dev.azure.com/Organization/Project/_wiki/wikis/CodeMie.wiki/10/How-to-Create-App
          wiki_identified: "CodeMie.wiki"
          page_id: 10
          Result: {"page_id": 10, "path": "/How-to-Create-App", "total_views": 42, "days_with_views": 5,
                   "view_stats": [{"day": "2024-01-01", "count": 10}, ...], "is_visited": true, "page_views_for_days": 30}
        - Check if page was visited in the last 7 days:
          wiki_identified: "CodeMie.wiki", page_id: 10, page_views_for_days: 7
          Result: {"is_visited": false, "total_views": 0, "page_views_for_days": 7, ...}
        """,
    label="Get Wiki Page Stats By ID",
    user_description="""
        Retrieves view statistics for a wiki page by its ID. Returns daily view counts over a configurable
        time window (1–30 days, default 30). Use this to identify which pages are frequently visited or
        neglected, or to check recent activity within a specific time window.

        Before using it, you need to provide:
        1. Azure DevOps organization URL
        2. Project name
        3. Personal Access Token with appropriate permissions
        """.strip(),
    config_class=AzureDevOpsWikiConfig,
)

GET_PAGE_STATS_BY_PATH_TOOL = ToolMetadata(
    name="get_wiki_page_stats_by_path",
    description="""
        Retrieve view statistics for an Azure DevOps wiki page by its path. Automatically resolves the
        page ID from the path, then returns view counts per day over a configurable time window.

        IMPORTANT: When extracting from Azure DevOps wiki URLs, ALWAYS use the '/{page_id}/{page-slug}' format.
        The tool will automatically resolve nested pages by discovering the full hierarchical path using the page ID.

        Arguments:
        - wiki_identified (str): Wiki ID or wiki name. Example: "MyWiki.wiki". Regularly, ".wiki" is essential.
        - page_name (str): Wiki page path in one of these formats:
          1. FROM URL (RECOMMENDED): Extract the '/{page_id}/{page-slug}' portion from the URL
             Example URL: https://dev.azure.com/Org/Proj/_wiki/wikis/MyWiki.wiki/10/How-to-Create-App
             Use page_name: "/10/How-to-Create-App" (the tool will resolve page ID automatically)
          2. FULL PATH: For direct path like "/Home" or "/Parent/Child/Page"
        - page_views_for_days (int, optional): Number of last days to retrieve statistics for (1–30).
          Default is 30. Azure DevOps does not support more than 30 days.

        Return Format:
        Returns dict with:
        - 'page_id': The resolved page ID
        - 'path': Full wiki page path
        - 'total_views': Total number of views in the requested period
        - 'days_with_views': Number of days the page was viewed at least once
        - 'view_stats': List of daily view records with 'day' (date) and 'count' (views on that day)
        - 'is_visited': Boolean indicating if the page was viewed at least once in the requested period
        - 'page_views_for_days': The number of days the statistics cover

        Examples:
        - URL: https://dev.azure.com/Organization/Project/_wiki/wikis/CodeMie.wiki/10330/This-is-sub-page
          wiki_identified: "CodeMie.wiki"
          page_name: "/10330/This-is-sub-page" (ALWAYS use this format from URLs)
          Result: {"page_id": 10330, "path": "/Parent/This-is-sub-page", "total_views": 15,
                   "is_visited": true, "view_stats": [...], "page_views_for_days": 30}
        - Check if a page was visited in the last 7 days:
          wiki_identified: "CodeMie.wiki", page_name: "/Documentation/Old-Guide", page_views_for_days: 7
          Result: {"is_visited": false, "total_views": 0, "page_views_for_days": 7, ...}
        """,
    label="Get Wiki Page Stats By Path",
    user_description="""
        Retrieves view statistics for a wiki page by its path. Automatically resolves the page ID from the path.
        For wiki URLs, extract the page ID and slug portion (e.g., '/123/Page-Name') and the tool will
        automatically handle the resolution. Returns daily view counts over a configurable time window
        (1–30 days, default 30).

        Use this to identify which pages are frequently visited or neglected, or to check recent activity
        within a specific time window (e.g., last 7 days).

        Before using it, you need to provide:
        1. Azure DevOps organization URL
        2. Project name
        3. Personal Access Token with appropriate permissions
        """.strip(),
    config_class=AzureDevOpsWikiConfig,
)

LIST_WIKIS_TOOL = ToolMetadata(
    name="list_wikis",
    description="""
        List all wikis available in an Azure DevOps project. Automatically discovers and retrieves metadata
        for all wikis defined in the project, eliminating the need for manual wiki name configuration.

        This tool enables wiki discovery by calling the Azure DevOps REST API to retrieve all available wikis
        in the configured project. Use this when you need to:
        - Discover what wikis exist in a project
        - Find the correct wiki identifier before performing other wiki operations
        - List available wikis for selection or configuration
        - Verify wiki existence and availability

        Arguments:
        No arguments required - uses project configuration from tool settings.

        Return Format:
        Returns list of wiki objects, each containing:
        - 'id': Unique wiki identifier (UUID format)
        - 'name': Wiki name (typically ends with .wiki, e.g., "MyProject.wiki")
        - 'url': Full URL to access the wiki in Azure DevOps web interface
        - 'remoteUrl': Git repository URL for the wiki (if code wiki type)
        - 'type': Wiki type ("projectWiki" for project wikis, "codeWiki" for code wikis)
        - 'projectId': Project UUID this wiki belongs to
        - 'repositoryId': Repository UUID (for code wikis)
        - 'mappedPath': Mapped path in repository (for code wikis)
        - 'versions': List of available versions/branches

        Wiki Types:
        - **Project Wiki**: Standard wiki created directly in the project (type: "projectWiki")
        - **Code Wiki**: Wiki published from a Git repository folder (type: "codeWiki")

        Naming Convention:
        Most wikis follow the pattern: {ProjectName}.wiki
        Example: "MyProject.wiki", "Documentation.wiki"

        Examples:
        - Discover all wikis:
          Result: [
              {"id": "abc-123", "name": "MyProject.wiki", "type": "projectWiki", "url": "https://...", ...},
              {"id": "def-456", "name": "Documentation.wiki", "type": "projectWiki", "url": "https://...", ...}
          ]
        - Find wiki identifier for subsequent operations:
          Step 1: Call list_wikis to get all wikis
          Step 2: Extract desired wiki name (e.g., "MyProject.wiki")
          Step 3: Use name in other tools like get_wiki, get_wiki_page_by_path, etc.
        """,
    label="List Wikis",
    user_description="""
        Lists all wikis available in the Azure DevOps project. Returns metadata for each wiki including
        ID, name, type, and URLs. This eliminates the need to manually configure wiki names and enables
        automatic wiki discovery.

        Use this tool to:
        - Discover what wikis exist in your project
        - Find the correct wiki identifier before reading or modifying wiki pages
        - Verify wiki availability and configuration

        Before using it, you need to provide:
        1. Azure DevOps organization URL
        2. Project name
        3. Personal Access Token with Wiki read permissions
        """.strip(),
    config_class=AzureDevOpsWikiConfig,
)

LIST_PAGES_TOOL = ToolMetadata(
    name="list_pages",
    description="""
        List all pages within an Azure DevOps Wiki, returning a paginated flat list.
        Default page size is 20. Supports pagination for large wikis with 100+ pages.

        This tool retrieves pages from a wiki, allowing users to discover wiki content structure
        and navigate to specific pages.

        Use this tool when you need to:
        - Discover all available pages in a wiki
        - Find specific pages before reading or editing them
        - Plan content operations (creation, editing, moving)
        - Get an overview of wiki organization
        - Paginate through large wikis (100+ pages)

        Arguments:
        - wiki_identified (str): Wiki ID or wiki name. Example: "MyWiki.wiki". Regularly, ".wiki" is essential.
        - path (str, optional): Wiki path to retrieve pages from. Default is "/" (root - all pages).
          Use "/" for full wiki or specify a sub-path like "/Architecture/Design" to retrieve
          only pages under that path.
        - page_size (int, optional): Number of pages to return per request. Default is 20.
          Specify a custom value (e.g., 10, 25, 50, 100) for different page sizes.
          Returns flat paginated list. Range: 1-200.
        - skip (int, optional): Number of pages to skip for pagination. Default is 0.
          Example: page_size=20, skip=0 (first page), skip=20 (second page), skip=40 (third page).

        API Details:
        GET https://dev.azure.com/{organization}/{project}/_apis/wiki/wikis/{wikiIdentifier}/pages?path={path}&recursionLevel=full&api-version=7.1

        Note: Azure DevOps Wiki API doesn't support server-side pagination for the pages endpoint.
        The tool fetches the full hierarchy and applies client-side pagination by flattening the tree and slicing the results.

        Return Format:
        Returns paginated response with:
        - 'pages': Flat array of page objects. Each page contains:
          - 'id': Page ID (numeric identifier)
          - 'path': Full page path (e.g., "/Parent/Child/Page")
          - 'name': Page name/title
          - 'order': Page order in parent's child list
          - 'gitItemPath': Git repository path (for code wikis)
          - 'url': Full URL to access the page
        - 'pagination': Object with:
          - 'page_size': Number of pages requested (default: 20)
          - 'skip': Number of pages skipped
          - 'returned_count': Actual number of pages returned in this response
          - 'total_count': Total number of pages available across all pages
          - 'has_more': Boolean indicating if more pages are available

        Examples:
        - List first 20 pages (default):
          wiki_identified: "MyProject.wiki"
          Result: First 20 pages with pagination metadata

        - List pages from specific section:
          wiki_identified: "Documentation.wiki"
          path: "/Architecture/Design"
          Result: First 20 pages under /Architecture/Design with pagination metadata

        - List first 10 pages:
          wiki_identified: "LargeWiki.wiki"
          page_size: 10
          skip: 0
          Result: First 10 pages with pagination metadata

        - List second page (pages 11-20):
          wiki_identified: "LargeWiki.wiki"
          page_size: 10
          skip: 10
          Result: Next 10 pages with pagination metadata

        - List all pages using larger page size:
          wiki_identified: "CodeMie.wiki"
          page_size: 100
          Result: First 100 pages with pagination metadata
        """,
    label="List Pages",
    user_description="""
        Retrieves a paginated flat list of all pages within an Azure DevOps Wiki.
        Default page size is 20. Supports pagination for large wikis (100+ pages).

        Returns pages from a specific path with page metadata in a flat list format.

        Use this tool to:
        - Discover what pages exist in a wiki
        - Find specific pages before performing operations
        - Plan content creation, editing, or reorganization
        - Paginate through large wikis to retrieve pages in chunks (default 20, or 10, 25, 50, 100 per request)

        Before using it, you need to provide:
        1. Azure DevOps organization URL
        2. Project name
        3. Personal Access Token with Wiki read permissions
        """.strip(),
    config_class=AzureDevOpsWikiConfig,
)

ADD_WIKI_COMMENT_BY_ID_TOOL = ToolMetadata(
    name="add_wiki_comment_by_id",
    description="""
        Add a comment to an Azure DevOps wiki page by page ID. Supports top-level comments, threaded replies,
        comments with file attachments, and standalone file attachments.

        This tool enables AI agents to provide collaborative feedback, automated status updates, review notes,
        and other comment-based interactions directly within Azure DevOps Wiki pages.

        Arguments:
        - wiki_identified (str): Wiki ID or wiki name. Example: "MyWiki.wiki". Regularly, ".wiki" is essential.
        - page_id (int): Wiki page ID (numeric identifier) where the comment will be added
        - comment_text (str, optional): Text content of the comment in Markdown format. Default: empty string.
          Can be empty if an attachment is provided (standalone attachment comment).
        - parent_comment_id (int, optional): Parent comment ID for threading. When provided, the new comment
          will be added as a reply to the specified parent comment. Leave empty for top-level comments.

        FILE ATTACHMENTS: If files are provided via input_files, they will be uploaded and attached to the comment.
        Maximum file size: 19MB per file (default limit). Supports all file types.

        Return Format:
        Returns dict with:
        - 'comment_id': ID of the created comment
        - 'comment_text': The posted comment text
        - 'author': Comment author information
        - 'created_date': Comment creation timestamp
        - 'parent_comment_id': Parent comment ID if this is a reply, otherwise null
        - 'attachments': List of attachment metadata (filename, size, url) if files were attached
        - 'attachment_count': Number of files attached

        Usage Scenarios:
        - Automated feedback: Post review comments, suggestions, or status updates on wiki pages
        - Documentation notes: Add clarifications, corrections, or additional context to documentation
        - Threaded discussions: Reply to existing comments to create organized discussion threads
        - File sharing: Attach logs, screenshots, diagrams, or documents to wiki page comments
        - Standalone attachments: Upload files as comments without text (for quick file sharing)

        Examples:
        - Add top-level comment:
          wiki_identified: "CodeMie.wiki"
          page_id: 10
          comment_text: "This documentation needs to be updated with the latest API changes."
          Result: Top-level comment created with ID, timestamp, and author info

        - Reply to existing comment:
          wiki_identified: "ProjectWiki.wiki"
          page_id: 42
          comment_text: "I agree, I've updated the section with the new examples."
          parent_comment_id: 123
          Result: Reply comment created under parent comment thread

        - Comment with attachment:
          wiki_identified: "Docs.wiki"
          page_id: 15
          comment_text: "Attaching the updated architecture diagram."
          [Provide diagram file via input_files]
          Result: Comment created with attached file (filename, size, download URL returned)

        - Standalone attachment (no text):
          wiki_identified: "TechDocs.wiki"
          page_id: 99
          comment_text: ""
          [Provide log file via input_files]
          Result: Comment created with only the attachment
        """,
    label="Add Wiki Comment By ID",
    user_description="""
        Adds a comment to an Azure DevOps wiki page using the page ID. Supports top-level comments,
        threaded replies (via parent_comment_id), and file attachments.

        Use this tool to:
        - Post feedback, notes, or status updates on wiki pages
        - Reply to existing comment threads
        - Attach files (logs, diagrams, documents) to comments
        - Share files quickly via standalone attachment comments

        Before using it, you need to provide:
        1. Azure DevOps organization URL
        2. Project name
        3. Personal Access Token with Wiki comment/attachment permissions
        4. Optional: Files to attach via input_files field
        """.strip(),
    config_class=AzureDevOpsWikiConfig,
)

ADD_WIKI_COMMENT_BY_PATH_TOOL = ToolMetadata(
    name="add_wiki_comment_by_path",
    description="""
        Add a comment to an Azure DevOps wiki page by page path. Automatically resolves the page ID from the path,
        then posts the comment. Supports top-level comments, threaded replies, comments with file attachments,
        and standalone file attachments.

        This tool enables AI agents to provide collaborative feedback, automated status updates, review notes,
        and other comment-based interactions directly within Azure DevOps Wiki pages.

        IMPORTANT: When extracting from Azure DevOps wiki URLs, ALWAYS use the '/{page_id}/{page-slug}' format.
        The tool will automatically resolve nested pages by discovering the full hierarchical path using the page ID.

        Arguments:
        - wiki_identified (str): Wiki ID or wiki name. Example: "MyWiki.wiki". Regularly, ".wiki" is essential.
        - page_name (str): Wiki page path in one of these formats:
          1. FROM URL (RECOMMENDED): Extract the '/{page_id}/{page-slug}' portion from the URL
             Example URL: https://dev.azure.com/Org/Proj/_wiki/wikis/MyWiki.wiki/10/How-to-Create-App
             Use page_name: "/10/How-to-Create-App" (the tool will resolve full nested path automatically)
          2. FULL PATH: For direct path like "/Home" or "/Parent/Child/Page"
        - comment_text (str, optional): Text content of the comment in Markdown format. Default: empty string.
          Can be empty if an attachment is provided (standalone attachment comment).
        - parent_comment_id (int, optional): Parent comment ID for threading. When provided, the new comment
          will be added as a reply to the specified parent comment. Leave empty for top-level comments.

        FILE ATTACHMENTS: If files are provided via input_files, they will be uploaded and attached to the comment.
        Maximum file size: 19MB per file (default limit). Supports all file types.

        Return Format:
        Returns dict with:
        - 'comment_id': ID of the created comment
        - 'comment_text': The posted comment text
        - 'author': Comment author information
        - 'created_date': Comment creation timestamp
        - 'parent_comment_id': Parent comment ID if this is a reply, otherwise null
        - 'page_id': Resolved page ID
        - 'page_path': Full page path
        - 'attachments': List of attachment metadata (filename, size, url) if files were attached
        - 'attachment_count': Number of files attached

        Usage Scenarios:
        - Automated feedback: Post review comments, suggestions, or status updates on wiki pages
        - Documentation notes: Add clarifications, corrections, or additional context to documentation
        - Threaded discussions: Reply to existing comments to create organized discussion threads
        - File sharing: Attach logs, screenshots, diagrams, or documents to wiki page comments
        - Standalone attachments: Upload files as comments without text (for quick file sharing)

        Examples:
        - Add comment from URL:
          URL: https://dev.azure.com/Organization/Project/_wiki/wikis/CodeMie.wiki/10330/This-is-sub-page
          wiki_identified: "CodeMie.wiki"
          page_name: "/10330/This-is-sub-page" (ALWAYS use this format from URLs)
          comment_text: "This page needs more examples."
          Result: Top-level comment created with resolved page ID

        - Reply to existing comment thread:
          wiki_identified: "ProjectWiki.wiki"
          page_name: "/Documentation/API-Reference"
          comment_text: "Updated the examples section."
          parent_comment_id: 456
          Result: Reply comment added to thread

        - Comment with attachment from URL:
          URL: https://dev.azure.com/Org/Proj/_wiki/wikis/Docs.wiki/25/Architecture
          wiki_identified: "Docs.wiki"
          page_name: "/25/Architecture"
          comment_text: "Attaching updated system diagram."
          [Provide diagram file via input_files]
          Result: Comment with attached file created

        - Standalone attachment (no text):
          wiki_identified: "TechDocs.wiki"
          page_name: "/Troubleshooting/Common-Issues"
          comment_text: ""
          [Provide log file via input_files]
          Result: Attachment-only comment created
        """,
    label="Add Wiki Comment By Path",
    user_description="""
        Adds a comment to an Azure DevOps wiki page using the page path. Automatically resolves the page ID
        from the path. Supports top-level comments, threaded replies (via parent_comment_id), and file attachments.

        For wiki URLs, extract the page ID and slug portion (e.g., '/123/Page-Name') and the tool will
        automatically resolve nested page paths.

        Use this tool to:
        - Post feedback, notes, or status updates on wiki pages
        - Reply to existing comment threads
        - Attach files (logs, diagrams, documents) to comments
        - Share files quickly via standalone attachment comments

        Before using it, you need to provide:
        1. Azure DevOps organization URL
        2. Project name
        3. Personal Access Token with Wiki comment/attachment permissions
        4. Optional: Files to attach via input_files field
        """.strip(),
    config_class=AzureDevOpsWikiConfig,
)
