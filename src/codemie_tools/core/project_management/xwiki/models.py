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

from typing import ClassVar, List, Optional

from pydantic import BaseModel, Field

from codemie_tools.base.models import (
    CodeMieToolConfig,
    CredentialTypes,
    FileConfigMixin,
    get_tool_default,
)


class XWikiConfig(CodeMieToolConfig, FileConfigMixin):
    TOOL_NAME: ClassVar[str] = "xwiki"

    credential_type: CredentialTypes = Field(default=CredentialTypes.XWIKI, exclude=True, frozen=True)
    url: str = Field(
        default=get_tool_default(TOOL_NAME, "url") or "",
        description="xWiki base URL, e.g. https://wiki.example.com",
        json_schema_extra={"placeholder": get_tool_default(TOOL_NAME, "url_placeholder") or ""},
    )
    username: Optional[str] = Field(default=None, description="xWiki username for Basic Auth")
    token: str = Field(
        default="",
        description="API token (Basic Auth password) or Bearer token",
        json_schema_extra={
            "placeholder": "Token/ApiKey",
            "sensitive": True,
        },
    )
    use_bearer: bool = Field(
        default=get_tool_default(TOOL_NAME, "use_bearer") or False,
        description="Use Authorization: Bearer header instead of Basic Auth",
    )


# ---------------------------------------------------------------------------
# Wiki input schemas
# ---------------------------------------------------------------------------


class ListWikisInput(BaseModel):
    number: int = Field(default=50, description="Maximum number of wikis to return (default: 50)")
    start: int = Field(default=0, description="Pagination offset (default: 0)")


class GetWikiInput(BaseModel):
    wiki: str = Field(default="xwiki", description="Wiki identifier (default: xwiki)")


# ---------------------------------------------------------------------------
# Space input schemas
# ---------------------------------------------------------------------------


class ListSpacesInput(BaseModel):
    wiki: str = Field(default="xwiki", description="Wiki identifier (default: xwiki)")
    number: int = Field(default=50, description="Maximum number of spaces to return (default: 50)")
    start: int = Field(default=0, description="Pagination offset (default: 0)")


class GetSpaceInput(BaseModel):
    wiki: str = Field(default="xwiki", description="Wiki identifier (default: xwiki)")
    space: str = Field(..., description="Space name — dot-separated for nested (e.g. Main.Sandbox)")


# ---------------------------------------------------------------------------
# Page input schemas
# ---------------------------------------------------------------------------


class ListPagesInput(BaseModel):
    wiki: str = Field(default="xwiki", description="Wiki identifier (default: xwiki)")
    space: str = Field(..., description="Space name — dot-separated for nested (e.g. Main.Sandbox)")
    number: int = Field(default=50, description="Maximum number of pages to return (default: 50)")
    start: int = Field(default=0, description="Pagination offset (default: 0)")


class ListWikiPagesInput(BaseModel):
    wiki: str = Field(default="xwiki", description="Wiki identifier (default: xwiki)")
    number: int = Field(default=50, description="Maximum number of pages to return (default: 50)")
    start: int = Field(default=0, description="Pagination offset (default: 0)")


class ListPageChildrenInput(BaseModel):
    wiki: str = Field(default="xwiki", description="Wiki identifier (default: xwiki)")
    space: str = Field(..., description="Space name — dot-separated for nested (e.g. Main.Sandbox)")
    page: str = Field(..., description="Parent page name")
    number: int = Field(default=50, description="Maximum number of child pages to return (default: 50)")
    start: int = Field(default=0, description="Pagination offset (default: 0)")


class GetPageInput(BaseModel):
    wiki: str = Field(default="xwiki", description="Wiki identifier (default: xwiki)")
    space: str = Field(..., description="Space name — dot-separated for nested (e.g. Main.Sandbox)")
    page: str = Field(..., description="Page name")
    is_markdown: bool = Field(default=False, description="Convert HTML response content to Markdown")


class CreatePageInput(BaseModel):
    wiki: str = Field(default="xwiki", description="Wiki identifier (default: xwiki)")
    space: str = Field(..., description="Space name — dot-separated for nested (e.g. Main.Sandbox)")
    page: str = Field(..., description="Page name (used as the page identifier in the URL)")
    title: str = Field(..., description="Human-readable page title")
    content: str = Field(..., description="Page content in wiki syntax")
    syntax: str = Field(default="xwiki/2.1", description="Wiki content syntax (default: xwiki/2.1)")


class ModifyPageInput(BaseModel):
    wiki: str = Field(default="xwiki", description="Wiki identifier (default: xwiki)")
    space: str = Field(..., description="Space name — dot-separated for nested (e.g. Main.Sandbox)")
    page: str = Field(..., description="Page name")
    content: str = Field(..., description="New page content in wiki syntax")
    title: Optional[str] = Field(
        default=None, description="New page title (optional — keeps existing title if omitted)"
    )
    syntax: str = Field(default="xwiki/2.1", description="Wiki content syntax (default: xwiki/2.1)")


class DeletePageInput(BaseModel):
    wiki: str = Field(default="xwiki", description="Wiki identifier (default: xwiki)")
    space: str = Field(..., description="Space name — dot-separated for nested (e.g. Main.Sandbox)")
    page: str = Field(..., description="Page name")


# ---------------------------------------------------------------------------
# Tag input schemas
# ---------------------------------------------------------------------------


class ListWikiTagsInput(BaseModel):
    wiki: str = Field(default="xwiki", description="Wiki identifier (default: xwiki)")


class ListPageTagsInput(BaseModel):
    wiki: str = Field(default="xwiki", description="Wiki identifier (default: xwiki)")
    space: str = Field(..., description="Space name — dot-separated for nested (e.g. Main.Sandbox)")
    page: str = Field(..., description="Page name")


class SetPageTagsInput(BaseModel):
    wiki: str = Field(default="xwiki", description="Wiki identifier (default: xwiki)")
    space: str = Field(..., description="Space name — dot-separated for nested (e.g. Main.Sandbox)")
    page: str = Field(..., description="Page name")
    tags: List[str] = Field(..., description="List of tag names to set on the page (replaces all existing tags)")


# ---------------------------------------------------------------------------
# Comment input schemas
# ---------------------------------------------------------------------------


class ListPageCommentsInput(BaseModel):
    wiki: str = Field(default="xwiki", description="Wiki identifier (default: xwiki)")
    space: str = Field(..., description="Space name — dot-separated for nested (e.g. Main.Sandbox)")
    page: str = Field(..., description="Page name")
    number: int = Field(default=20, description="Maximum number of comments to return (default: 20)")
    start: int = Field(default=0, description="Pagination offset (default: 0)")


class GetPageCommentInput(BaseModel):
    wiki: str = Field(default="xwiki", description="Wiki identifier (default: xwiki)")
    space: str = Field(..., description="Space name — dot-separated for nested (e.g. Main.Sandbox)")
    page: str = Field(..., description="Page name")
    comment_id: int = Field(..., description="Numeric comment ID")


class CreatePageCommentInput(BaseModel):
    wiki: str = Field(default="xwiki", description="Wiki identifier (default: xwiki)")
    space: str = Field(..., description="Space name — dot-separated for nested (e.g. Main.Sandbox)")
    page: str = Field(..., description="Page name")
    text: str = Field(..., description="Comment text content")


# ---------------------------------------------------------------------------
# Attachment input schemas
# ---------------------------------------------------------------------------


class ListPageAttachmentsInput(BaseModel):
    wiki: str = Field(default="xwiki", description="Wiki identifier (default: xwiki)")
    space: str = Field(..., description="Space name — dot-separated for nested (e.g. Main.Sandbox)")
    page: str = Field(..., description="Page name")


class GetPageAttachmentInput(BaseModel):
    wiki: str = Field(default="xwiki", description="Wiki identifier (default: xwiki)")
    space: str = Field(..., description="Space name — dot-separated for nested (e.g. Main.Sandbox)")
    page: str = Field(..., description="Page name")
    filename: str = Field(..., description="Attachment filename")


class ReadPageAttachmentContentInput(BaseModel):
    wiki: str = Field(default="xwiki", description="Wiki identifier (default: xwiki)")
    space: str = Field(..., description="Space name — dot-separated for nested (e.g. Main.Sandbox)")
    page: str = Field(..., description="Page name")
    filename: str = Field(..., description="Attachment filename to read (e.g. report.pdf)")


class CreatePageAttachmentInput(BaseModel):
    wiki: str = Field(default="xwiki", description="Wiki identifier (default: xwiki)")
    space: str = Field(..., description="Space name — dot-separated for nested (e.g. Main.Sandbox)")
    page: str = Field(..., description="Page name to attach the file to")


class DeletePageAttachmentInput(BaseModel):
    wiki: str = Field(default="xwiki", description="Wiki identifier (default: xwiki)")
    space: str = Field(..., description="Space name — dot-separated for nested (e.g. Main.Sandbox)")
    page: str = Field(..., description="Page name")
    filename: str = Field(..., description="Attachment filename to delete")


# ---------------------------------------------------------------------------
# Search input schemas
# ---------------------------------------------------------------------------


class SearchWikiInput(BaseModel):
    wiki: str = Field(default="xwiki", description="Wiki identifier (default: xwiki)")
    query: str = Field(..., description="Search terms or phrase")
    scope: str = Field(
        default="content",
        description="Search scope: content, name, title, spaces, wikis (default: content)",
    )
    space: Optional[str] = Field(
        default=None,
        description="Restrict search to this space — dot-separated for nested (optional)",
    )
    number: int = Field(default=10, description="Maximum number of results (default: 10)")
    start: int = Field(default=0, description="Pagination offset (default: 0)")
    is_markdown: bool = Field(default=False, description="Convert HTML response to Markdown")


class SearchSpaceInput(BaseModel):
    wiki: str = Field(default="xwiki", description="Wiki identifier (default: xwiki)")
    space: str = Field(..., description="Space to search within — dot-separated for nested")
    query: str = Field(..., description="Search terms or phrase")
    number: int = Field(default=10, description="Maximum number of results (default: 10)")
    start: int = Field(default=0, description="Pagination offset (default: 0)")
    is_markdown: bool = Field(default=False, description="Convert HTML response to Markdown")
