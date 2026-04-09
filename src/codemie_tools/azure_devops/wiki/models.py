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

from codemie_tools.base.models import CodeMieToolConfig, CredentialTypes, RequiredField, FileConfigMixin

# Constants for repeated field descriptions
WIKI_IDENTIFIER_DESCRIPTION = "Wiki ID or wiki name"
VERSION_IDENTIFIER_DESCRIPTION = "Version string identifier (name of tag/branch, SHA1 of commit)"
VERSION_TYPE_DESCRIPTION = "Version type (branch, tag, or commit). Determines how Id is interpreted"


class AzureDevOpsWikiConfig(CodeMieToolConfig, FileConfigMixin):
    """Configuration for Azure DevOps Wiki integration.

    Supports both direct configuration and mapping from separate fields:
    - Direct: organization_url, project, token
    - Mapped: url/base_url + organization -> organization_url, access_token -> token

    Includes file support via FileConfigMixin for attaching files to wiki pages.
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


# Input models for Azure DevOps wiki operations
class GetWikiInput(BaseModel):
    wiki_identified: str = Field(description=WIKI_IDENTIFIER_DESCRIPTION)


class GetPageByPathInput(BaseModel):
    wiki_identified: str = Field(description=WIKI_IDENTIFIER_DESCRIPTION)
    page_name: str = Field(
        description="Wiki page path. For URLs, extract the '/{page_id}/{page-slug}' portion. "
        "Example: from URL '...wikis/MyWiki.wiki/123/My-Page', use '/123/My-Page'"
    )
    include_attachments: bool = Field(
        default=False,
        description="Whether to download and return attachment content. "
        "If True, parses page content for attachment links and downloads files.",
    )


class GetPageByIdInput(BaseModel):
    wiki_identified: str = Field(description=WIKI_IDENTIFIER_DESCRIPTION)
    page_id: int = Field(description="Wiki page ID")
    include_attachments: bool = Field(
        default=False,
        description="Whether to download and return attachment content. "
        "If True, parses page content for attachment links and downloads files.",
    )


class DeletePageByPathInput(BaseModel):
    wiki_identified: str = Field(description=WIKI_IDENTIFIER_DESCRIPTION)
    page_name: str = Field(
        description="Wiki page path. For URLs, extract the '/{page_id}/{page-slug}' portion. "
        "Example: from URL '...wikis/MyWiki.wiki/123/My-Page', use '/123/My-Page'"
    )


class DeletePageByIdInput(BaseModel):
    wiki_identified: str = Field(description=WIKI_IDENTIFIER_DESCRIPTION)
    page_id: int = Field(description="Wiki page ID")


class ModifyPageInput(BaseModel):
    wiki_identified: str = Field(description=WIKI_IDENTIFIER_DESCRIPTION)
    page_name: str = Field(
        description="Wiki page path. For URLs, extract the '/{page_id}/{page-slug}' portion. "
        "Example: from URL '...wikis/MyWiki.wiki/123/My-Page', use '/123/My-Page'"
    )
    page_content: str = Field(description="Wiki page content")
    version_identifier: str = Field(description=VERSION_IDENTIFIER_DESCRIPTION)
    version_type: Optional[str] = Field(
        description=VERSION_TYPE_DESCRIPTION,
        default="branch",
    )


class CreatePageInput(BaseModel):
    wiki_identified: str = Field(description=WIKI_IDENTIFIER_DESCRIPTION)
    parent_page_path: str = Field(
        description="Parent page path where the new page will be created. "
        "For URLs, extract the '/{page_id}/{page-slug}' portion. "
        "Use '/' for root level pages. "
        "Examples: '/123/Parent-Page' (from URL) or '/Parent Page' (direct path)"
    )
    new_page_name: str = Field(
        description="Name of the new page to create (without path, just the name). " "Example: 'My New Page'"
    )
    page_content: str = Field(description="Markdown content for the new wiki page")
    version_identifier: str = Field(description=VERSION_IDENTIFIER_DESCRIPTION)
    version_type: Optional[str] = Field(
        description=VERSION_TYPE_DESCRIPTION,
        default="branch",
    )


class RenamePageInput(BaseModel):
    wiki_identified: str = Field(description=WIKI_IDENTIFIER_DESCRIPTION)
    old_page_name: str = Field(
        description="Wiki page path to rename. For URLs, extract the '/{page_id}/{page-slug}' portion. "
        "Example: from URL '...wikis/MyWiki.wiki/123/My-Page', use '/123/My-Page'",
        examples=["/123/TestPageName", "/TestPageName"],
    )
    new_page_name: str = Field(description="New Wiki page name", examples=["RenamedName", "/RenamedName"])
    version_identifier: str = Field(description=VERSION_IDENTIFIER_DESCRIPTION)
    version_type: Optional[str] = Field(
        description=VERSION_TYPE_DESCRIPTION,
        default="branch",
    )


class SearchWikiPagesInput(BaseModel):
    wiki_identified: str = Field(description=WIKI_IDENTIFIER_DESCRIPTION)
    search_text: str = Field(description="Text to search for across wiki pages (case-insensitive)")
    include_context: bool = Field(
        default=True,
        description="Whether to include content snippets showing where the search text was found",
    )
    max_results: int = Field(
        default=50,
        description="Maximum number of results to return (default: 50)",
    )


class GetPageCommentsByIdInput(BaseModel):
    wiki_identified: str = Field(description=WIKI_IDENTIFIER_DESCRIPTION)
    page_id: int = Field(description="Wiki page ID")
    limit_total: Optional[int] = Field(
        default=None, description="Maximum number of total comments to return. If None, returns all comments."
    )
    include_deleted: Optional[bool] = Field(
        default=False, description="Specify if deleted comments should be retrieved"
    )
    expand: Optional[str] = Field(
        default="none",
        description="Expand parameters for comments. Options: { all, none, reactions, renderedText, renderedTextOnly }",
    )
    order: Optional[str] = Field(
        default=None, description="Order in which comments should be returned. Options: { asc, desc }"
    )


class GetPageCommentsByPathInput(BaseModel):
    wiki_identified: str = Field(description=WIKI_IDENTIFIER_DESCRIPTION)
    page_name: str = Field(
        description="Wiki page path. For URLs, extract the '/{page_id}/{page-slug}' portion. "
        "Example: from URL '...wikis/MyWiki.wiki/123/My-Page', use '/123/My-Page'"
    )
    limit_total: Optional[int] = Field(
        default=None, description="Maximum number of total comments to return. If None, returns all comments."
    )
    include_deleted: Optional[bool] = Field(
        default=False, description="Specify if deleted comments should be retrieved"
    )
    expand: Optional[str] = Field(
        default="none",
        description="Expand parameters for comments. Options: { all, none, reactions, renderedText, renderedTextOnly }",
    )
    order: Optional[str] = Field(
        default=None, description="Order in which comments should be returned. Options: { asc, desc }"
    )


class MovePageInput(BaseModel):
    wiki_identified: str = Field(description=WIKI_IDENTIFIER_DESCRIPTION)
    source_page_path: str = Field(
        description="Source wiki page path to move. For URLs, extract the '/{page_id}/{page-slug}' portion. "
        "Example: from URL '...wikis/MyWiki.wiki/123/My-Page', use '/123/My-Page'",
        examples=["/123/TestPageName", "/Parent/TestPageName"],
    )
    destination_page_path: str = Field(
        description="Destination path where the page will be moved. Must be a full path. "
        "Example: '/New-Parent/Moved-Page' or '/New-Location'",
        examples=["/NewParent/MovedPage", "/Moved-Page"],
    )
    version_identifier: str = Field(description=VERSION_IDENTIFIER_DESCRIPTION)
    version_type: Optional[str] = Field(
        description=VERSION_TYPE_DESCRIPTION,
        default="branch",
    )


class AddAttachmentInput(BaseModel):
    wiki_identified: str = Field(description=WIKI_IDENTIFIER_DESCRIPTION)
    page_name: str = Field(
        description="Wiki page path. For URLs, extract the '/{page_id}/{page-slug}' portion. "
        "Example: from URL '...wikis/MyWiki.wiki/123/My-Page', use '/123/My-Page'"
    )
    version_identifier: str = Field(description=VERSION_IDENTIFIER_DESCRIPTION)
    version_type: Optional[str] = Field(
        description=VERSION_TYPE_DESCRIPTION,
        default="branch",
    )


class GetPageStatsByIdInput(BaseModel):
    wiki_identified: str = Field(description=WIKI_IDENTIFIER_DESCRIPTION)
    page_id: int = Field(description="Wiki page ID (numeric identifier)")
    page_views_for_days: Optional[int] = Field(
        default=30,
        description="Number of last days to retrieve page view statistics for (1–30). "
        "Default is 30. Azure DevOps does not support more than 30 days.",
    )


class GetPageStatsByPathInput(BaseModel):
    wiki_identified: str = Field(description=WIKI_IDENTIFIER_DESCRIPTION)
    page_name: str = Field(
        description="Wiki page path. For URLs, extract the '/{page_id}/{page-slug}' portion. "
        "Example: from URL '...wikis/MyWiki.wiki/123/My-Page', use '/123/My-Page'"
    )
    page_views_for_days: Optional[int] = Field(
        default=30,
        description="Number of last days to retrieve page view statistics for (1–30). "
        "Default is 30. Azure DevOps does not support more than 30 days.",
    )


class ListWikisInput(BaseModel):
    """Input model for listing all wikis in an Azure DevOps project.

    No parameters required - uses project configuration from AzureDevOpsWikiConfig.
    """

    pass


class ListPagesInput(BaseModel):
    """Input model for listing all pages in an Azure DevOps wiki.

    Retrieves pages with pagination support for large wikis (100+ pages).
    Default page size is 20.
    """

    wiki_identified: str = Field(description=WIKI_IDENTIFIER_DESCRIPTION)
    path: Optional[str] = Field(
        default="/",
        description="Wiki path to retrieve pages from. Use '/' for root (all pages) or specify a sub-path "
        "like '/Architecture/Design' to retrieve only pages under that path. Default is '/' (root).",
    )
    page_size: Optional[int] = Field(
        default=20,
        description="Number of pages to return per request. Default is 20. "
        "Useful for paginating through large wikis (e.g., 10, 25, 50, 100). Returns flat list of pages.",
        gt=0,
        le=200,
    )
    skip: Optional[int] = Field(
        default=0,
        description="Number of pages to skip. Used with page_size for pagination. "
        "For example: page_size=20, skip=0 (first page), skip=20 (second page), skip=40 (third page).",
        ge=0,
    )


class AddWikiCommentByIdInput(BaseModel):
    """Input model for adding a comment to a wiki page by page ID.

    Supports:
    - Top-level comments on a page
    - Replies to existing comment threads (via parent_comment_id)
    - Comments with file attachments
    - Standalone file attachments (empty comment text)
    """

    wiki_identified: str = Field(description=WIKI_IDENTIFIER_DESCRIPTION)
    page_id: int = Field(description="Wiki page ID (numeric identifier) where the comment will be added")
    comment_text: str = Field(
        default="",
        description="Text content of the comment in Markdown format. "
        "Can be empty if an attachment is provided (standalone attachment comment).",
    )
    parent_comment_id: Optional[int] = Field(
        default=None,
        description="Optional parent comment ID for threading. "
        "When provided, the new comment will be added as a reply to the specified parent comment. "
        "Leave empty for top-level comments.",
    )


class AddWikiCommentByPathInput(BaseModel):
    """Input model for adding a comment to a wiki page by page path.

    Supports:
    - Top-level comments on a page
    - Replies to existing comment threads (via parent_comment_id)
    - Comments with file attachments
    - Standalone file attachments (empty comment text)

    Automatically resolves page ID from path (supports both ID-prefixed paths and full paths).
    """

    wiki_identified: str = Field(description=WIKI_IDENTIFIER_DESCRIPTION)
    page_name: str = Field(
        description="Wiki page path. For URLs, extract the '/{page_id}/{page-slug}' portion. "
        "Example: from URL '...wikis/MyWiki.wiki/123/My-Page', use '/123/My-Page'"
    )
    comment_text: str = Field(
        default="",
        description="Text content of the comment in Markdown format. "
        "Can be empty if an attachment is provided (standalone attachment comment).",
    )
    parent_comment_id: Optional[int] = Field(
        default=None,
        description="Optional parent comment ID for threading. "
        "When provided, the new comment will be added as a reply to the specified parent comment. "
        "Leave empty for top-level comments.",
    )


class GetAttachmentContentInput(BaseModel):
    """Input model for retrieving the content of a wiki page attachment.

    Supports two identification strategies:
    1. Direct URL: provide attachment_url obtained from get_wiki_page_by_id/path actions.
    2. Discovery: provide wiki_identified + (page_id or page_name) + attachment_name
       to locate the attachment URL from the page markdown.

    At least one of attachment_url OR (page_id / page_name + attachment_name) must be provided.
    """

    wiki_identified: str = Field(description=WIKI_IDENTIFIER_DESCRIPTION)
    attachment_url: Optional[str] = Field(
        default=None,
        description="Direct URL to the attachment as returned by get_wiki_page_by_id or "
        "get_wiki_page_by_path actions (e.g. a URL containing '/_apis/wit/attachments/' "
        "or '/.attachments/'). When provided, the attachment is downloaded directly "
        "without fetching the wiki page.",
    )
    page_id: Optional[int] = Field(
        default=None,
        description="Wiki page ID. Used together with attachment_name to discover the "
        "attachment URL from the page markdown. Ignored when attachment_url is provided.",
    )
    page_name: Optional[str] = Field(
        default=None,
        description="Wiki page path. For URLs, extract the '/{page_id}/{page-slug}' portion. "
        "Example: from URL '...wikis/MyWiki.wiki/123/My-Page', use '/123/My-Page'. "
        "Used together with attachment_name to discover the attachment URL. "
        "Ignored when attachment_url is provided.",
    )
    attachment_name: Optional[str] = Field(
        default=None,
        description="Name of the specific attachment file to retrieve "
        "(e.g. 'architecture.pdf', 'screenshot.png'). "
        "Required when using page_id or page_name for discovery. "
        "Case-insensitive matching is applied.",
    )
