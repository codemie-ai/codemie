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

import logging
import re
from typing import Optional, Type, Any, Dict, Union

from atlassian import Confluence
from markdownify import markdownify
from pydantic import BaseModel, Field

from codemie_tools.base.codemie_tool import CodeMieTool
from codemie_tools.base.file_tool_mixin import FileToolMixin
from codemie_tools.core.project_management.confluence.models import ConfluenceConfig
from codemie_tools.core.project_management.confluence.tools_vars import GENERIC_CONFLUENCE_TOOL
from codemie_tools.core.project_management.confluence.utils import (
    validate_creds,
    prepare_page_payload,
    parse_payload_params,
)

logger = logging.getLogger(__name__)

# Url that is used for testing confluence integration
CONFLUENCE_TEST_URL: str = "/rest/api/user/current"
CONFLUENCE_TEST_RESPONSE: str = "HTTP: GET/rest/api/user/current -> 200"
CONFLUENCE_ERROR_MSG: str = "Access denied"


class ConfluenceInput(BaseModel):
    method: str = Field(
        ...,
        description="The HTTP method to use for the request (GET, POST, PUT, DELETE, etc.). Required parameter.",
    )
    relative_url: str = Field(
        ...,
        description="""
        Required parameter: The relative URI for Confluence API.
        URI must start with a forward slash and '/rest/...'.
        Do not include query parameters in the URL, they must be provided separately in 'params'.
        For search/read operations, you MUST always get minimum fields and set max results, until users ask explicitly for more fields.
        """.strip(),
    )
    params: Union[str, Dict[str, Any], None] = Field(
        default=None,
        description="""
        Optional parameters to be sent in request body or query params.

        RECOMMENDED: Provide as a dictionary (dict) - this avoids JSON escaping issues with quotes, newlines, and HTML.
        LEGACY: Can also accept a JSON string, but this is error-prone for complex content.

        For search/read operations, you MUST always get minimum fields and set max results, until users ask explicitly for more fields.
        For search/read operations you must generate CQL query string and pass it as params.
        For file attachments, specify the file name(s) to attach: {"file": "filename.ext"} for single file
        or {"files": ["file1.ext", "file2.ext"]} for multiple files.

        Dict format examples (RECOMMENDED):
        - Simple search: params={"cql": "space = DEV", "limit": 10}
        - Page creation: params={"title": "Page Title", "content": "Multi-line\\ntext with <p>HTML</p>"}

        JSON string format (LEGACY - only use if dict not available):
        - Must properly escape all quotes and special characters
        """.strip(),
    )
    is_markdown: bool = Field(
        default=False,
        description="""
        Optional boolean to indicate if the payload main content is in Markdown format.
        If true, the payload will be converted to HTML before sending to Confluence.
        """.strip(),
    )


class GenericConfluenceTool(CodeMieTool, FileToolMixin):
    config: ConfluenceConfig
    name: str = GENERIC_CONFLUENCE_TOOL.name
    description: str = GENERIC_CONFLUENCE_TOOL.description
    args_schema: Type[BaseModel] = ConfluenceInput
    page_search_pattern: str = r"/rest/api/content/\d+"
    throw_truncated_error: bool = False
    page_action_prefix: str = "/rest/api/content"

    def execute(
        self,
        method: str,
        relative_url: str,
        params: Optional[str] = "",
        is_markdown: bool = False,
        *args,
    ):
        confluence = Confluence(
            url=self.config.url,
            username=self.config.username if self.config.username else None,
            token=self.config.token if not self.config.cloud else None,
            password=self.config.token if self.config.cloud else None,
            cloud=self.config.cloud,
        )
        validate_creds(confluence)

        if self._is_attachment_operation(relative_url):
            all_files = self._resolve_files()
            if all_files:
                payload_params = parse_payload_params(params)
                requested_files = self._filter_requested_files(all_files, payload_params)
                if requested_files:
                    return self._handle_file_attachments(confluence, relative_url, params, requested_files)

        payload_params = parse_payload_params(params)
        if method == "GET":
            response = confluence.request(method=method, path=relative_url, params=payload_params, advanced_mode=True)
            response_text = self.process_search_response(relative_url, response)
        else:
            if relative_url.startswith(self.page_action_prefix) and is_markdown:
                payload_params = prepare_page_payload(payload_params)
            response = confluence.request(
                method=method,
                path=relative_url,
                data=payload_params,
                advanced_mode=True,
            )
            response_text = response.text
        response_string = f"HTTP: {method}{relative_url} -> {response.status_code}{response.reason}{response_text}"
        logger.debug(response_string)
        return response_string

    def process_search_response(self, relative_url: str, response) -> str:
        if re.match(self.page_search_pattern, relative_url):
            self.tokens_size_limit = 20000
            body = markdownify(response.text, heading_style="ATX")
            return body
        return response.text

    def _healthcheck(self):
        response = self.execute("GET", CONFLUENCE_TEST_URL)
        assert response.startswith(CONFLUENCE_TEST_RESPONSE), CONFLUENCE_ERROR_MSG

    def _is_attachment_operation(self, relative_url: str) -> bool:
        """Check if the operation is for file attachments."""
        return "/child/attachment" in relative_url or "/attachment" in relative_url

    def _handle_file_attachments(
        self,
        confluence: Confluence,
        relative_url: str,
        params: Optional[str],
        files_content: Dict[str, tuple],
    ) -> str:
        """
        Handle file attachment operations for Confluence pages.

        Args:
            confluence: Confluence client instance
            relative_url: The relative URL (used to extract page_id)
            params: Optional JSON params (can contain page_id)
            files_content: Dictionary mapping file names to (content, mime_type) tuples

        Returns:
            str: Response message indicating success or failure

        Raises:
            ToolException: If files cannot be loaded or attachment fails
        """
        from langchain_core.tools import ToolException

        page_id = self._extract_page_id(relative_url, params)

        try:
            results = []
            for file_name, (content, mime_type) in files_content.items():
                response = confluence.attach_content(
                    content=content,
                    name=file_name,
                    content_type=mime_type,
                    page_id=page_id,
                    comment=None,
                )

                if response:
                    attachment_id = response.get("id", "unknown")
                    attachment_name = response.get("title", file_name)
                    results.append(
                        f"Successfully attached file '{attachment_name}' (ID: {attachment_id}) to page {page_id}"
                    )
                else:
                    raise ToolException(f"Failed to attach file '{file_name}': No response from Confluence API")

            return "\n".join(results)

        except Exception as e:
            raise ToolException(f"Failed to attach files to page {page_id}: {str(e)}")

    def _extract_page_id(self, relative_url: str, params: Optional[str]) -> str:
        """
        Extract page_id from relative_url or params.

        Args:
            relative_url: The relative URL (may contain page_id)
            params: Optional JSON params (may contain page_id)

        Returns:
            str: The page_id

        Raises:
            ToolException: If page_id cannot be determined
        """
        from langchain_core.tools import ToolException

        # Try to extract from URL: /rest/api/content/{pageId}/child/attachment
        match = re.search(r"/content/(\d+)", relative_url)
        if match:
            return match.group(1)

        # Try to extract from params
        if params:
            payload_params = parse_payload_params(params)
            if "page_id" in payload_params:
                return payload_params["page_id"]
            if "pageId" in payload_params:
                return payload_params["pageId"]

        raise ToolException(
            "page_id is required for file attachment. "
            "Provide it either in the relative_url (e.g., /rest/api/content/{pageId}/child/attachment) "
            "or in params as 'page_id'"
        )
