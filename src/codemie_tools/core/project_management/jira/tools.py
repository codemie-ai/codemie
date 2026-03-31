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

import base64
import json
import logging
import re
from typing import Type, Optional, Any, Dict, Union

from atlassian import Jira
from pydantic import BaseModel, Field

from codemie_tools.base.codemie_tool import CodeMieTool
from codemie_tools.base.file_tool_mixin import FileToolMixin
from codemie_tools.core.project_management.jira.models import JiraConfig
from codemie_tools.core.project_management.jira.tools_vars import (
    GENERIC_JIRA_TOOL,
    get_jira_tool_description,
)
from codemie_tools.core.project_management.jira.utils import (
    validate_jira_creds,
    parse_payload_params,
    process_search_response,
)

logger = logging.getLogger(__name__)

JIRA_TEST_URL: str = "/rest/api/2/myself"
JIRA_ERROR_MSG: str = "Access denied"

IMAGE_MIME_TYPES: set[str] = {"image/png", "image/jpeg", "image/jpg", "image/gif", "image/webp"}
MAX_IMAGE_SIZE_BYTES: int = 5 * 1024 * 1024  # 5 MB
MAX_IMAGES_PER_RESPONSE: int = 5
SINGLE_ISSUE_PATTERN: str = r"/rest/api/\d+/issue/[A-Za-z]+-\d+$"


class JiraMultimodalResponse:
    """Container for Jira responses that include downloadable image attachments.

    Carries the textual API response alongside attachment metadata so that
    ``_post_process_output_content`` can lazily download and analyze images
    via a multimodal LLM, returning the analysis as text.
    """

    def __init__(self, text: str, image_attachments: list[dict]) -> None:
        self.text = text
        self.image_attachments = image_attachments

    def __str__(self) -> str:
        return self.text


class JiraInput(BaseModel):
    method: str = Field(
        ...,
        description="The HTTP method to use for the request (GET, POST, PUT, DELETE, etc.). Required parameter.",
    )
    relative_url: str = Field(
        ...,
        description="""
        Required parameter: The relative URI for JIRA REST API V2.
        URI must start with a forward slash and '/rest/api/2/...'.
        Do not include query parameters in the URL, they must be provided separately in 'params'.
        For search/read operations, you MUST always get "key", "summary", "status", "assignee", "issuetype" and
        set maxResult, until users ask explicitly for more fields.
        For single-issue GET requests, ALWAYS also include "attachment" in fields.
        """,
    )
    params: Union[str, Dict[str, Any], None] = Field(
        default=None,
        description="""
        Optional parameters to be sent in request body or query params.

        RECOMMENDED: Provide as a dictionary (dict) - this avoids JSON escaping issues with quotes, newlines, and HTML.
        LEGACY: Can also accept a JSON string, but this is error-prone for complex content.

        For search/read operations, you MUST always get "key", "summary", "status", "assignee", "issuetype" and
        set maxResult, until users ask explicitly for more fields.
        For single-issue GET requests, ALWAYS also include "attachment" in fields.
        For file attachments, specify the file name(s) to attach: {"file": "filename.ext"} for single file
        or {"files": ["file1.ext", "file2.ext"]} for multiple files.

        Dict format examples (RECOMMENDED):
        - Simple search: params={"jql": "project = PROJ", "fields": ["key", "summary"], "maxResults": 10}
        - Issue creation: params={"fields": {"project": {"key": "PROJ"}, "summary": "Title", "description": "Multi-line\\ntext"}}
        - With special fields: params={"jql": "project = X AND \\"epic link\\" = EPIC-123", "fields": ["key"]}

        JSON string format examples (LEGACY - only use if dict not available):
        - Status change by user: {"jql": "status CHANGED TO \\"Ready for Testing\\" BY \\"user@example.com\\" DURING (startOfMonth(-1), endOfMonth(-1))"}
        - Specific transition (only when user asks about FROM/TO): {"jql": "status CHANGED FROM \\"Open\\" TO \\"In Progress\\" BY \\"user@example.com\\" DURING (startOfWeek(), endOfWeek())"}
        - Date periods: this month (startOfMonth(), endOfMonth()), last month (startOfMonth(-1), endOfMonth(-1)), this week (startOfWeek(), endOfWeek()), last 2 weeks (startOfWeek(-1), endOfWeek()), specific dates with time ('2025/10/01 00:00', '2025/10/20 23:59')
        - Completed/done/developed/implemented tickets (CRITICAL - each status needs own BY, use THREE approaches): {"jql": "((status CHANGED TO \\"Closed\\" BY \\"user@example.com\\" DURING (startOfWeek(-1), endOfWeek(-1)) OR status CHANGED TO \\"Done\\" BY \\"user@example.com\\" DURING (startOfWeek(-1), endOfWeek(-1))) OR (assignee WAS \\"user@example.com\\" AND status IN (\\"Closed\\", \\"Done\\") AND updated >= -7d) OR (assignee = \\"user@example.com\\" AND status IN (\\"Closed\\", \\"Done\\") AND updated >= -7d)) AND project = PROJECTKEY"}
        - This captures: tickets user closed, tickets user worked on (was assignee), tickets user is responsible for (current assignee)
        - NOTE: Status names are case-sensitive. Use "Closed", "Done" (not "CLOSED", "DONE"). For updated field use relative dates like -7d, -30d
        """,
    )


class GenericJiraIssueTool(CodeMieTool, FileToolMixin):
    config: JiraConfig
    jira: Optional[Jira] = None
    name: str = GENERIC_JIRA_TOOL.name
    description: str = GENERIC_JIRA_TOOL.description or ""
    args_schema: Type[BaseModel] = JiraInput
    issue_search_pattern: str = r"/rest/api/\d+/search"
    response_format: str = "content_and_artifact"

    def __init__(self, config: JiraConfig):
        super().__init__(config=config)
        if self.config.cloud:
            self.issue_search_pattern = r"/rest/api/3/search/jql"
            self.description = get_jira_tool_description(api_version=3)

        self.jira = Jira(
            url=self.config.url,
            username=self.config.username if self.config.username else None,
            token=self.config.token if not self.config.cloud else None,
            password=self.config.token if self.config.cloud else None,
            cloud=self.config.cloud,
        )
        validate_jira_creds(self.jira)

    def execute(self, method: str, relative_url: str, params: Optional[str] = "", *args):
        if self._is_attachment_operation(relative_url):
            all_files = self._resolve_files()
            if all_files:
                payload_params = parse_payload_params(params)
                requested_files = self._filter_requested_files(all_files, payload_params)
                if requested_files:
                    return self._handle_file_attachments(relative_url, params, requested_files)

        payload_params = parse_payload_params(params)

        if method == "GET":
            # Convert fields from list to comma-separated string for GET query params
            payload_params = self._normalize_fields_param(payload_params)
            response_text, response = self._handle_get_request(relative_url, payload_params)
        else:
            # For POST/PUT/DELETE, keep fields as array in JSON body (Jira expects ArrayList)
            response_text, response = self._handle_non_get_request(method, relative_url, payload_params)

        response_string = f"HTTP: {method} {relative_url} -> {response.status_code} {response.reason} {response_text}"
        logger.debug(response_string)

        if method == "GET" and self._is_single_issue_request(relative_url):
            image_attachments = self._extract_image_attachments(response)
            if image_attachments:
                return JiraMultimodalResponse(text=response_string, image_attachments=image_attachments)

        return response_string

    def _handle_get_request(self, relative_url, payload_params):
        response = self.jira.request(
            method="GET",
            path=relative_url,
            params=payload_params,
            advanced_mode=True,
            headers={"content-type": "application/json"},
        )
        self.jira.raise_for_status(response)
        if re.match(self.issue_search_pattern, relative_url):
            response_text = process_search_response(self.jira.url, response, payload_params)
        else:
            response_text = response.text
        return response_text, response

    def _handle_non_get_request(self, method, relative_url, payload_params):
        response = self.jira.request(method=method, path=relative_url, data=payload_params, advanced_mode=True)
        self.jira.raise_for_status(response)
        return response.text, response

    def _healthcheck(self):
        response = self.jira.request(
            method="GET",
            path=JIRA_TEST_URL,
            params={},
            advanced_mode=True,
            headers={"content-type": "application/json"},
        )
        if response.status_code != 200:
            raise AssertionError(JIRA_ERROR_MSG)
        try:
            data = json.loads(response.text)
        except (json.JSONDecodeError, TypeError):
            raise AssertionError(JIRA_ERROR_MSG)
        if "displayName" not in data:
            raise AssertionError(JIRA_ERROR_MSG)

    def _normalize_fields_param(self, payload_params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Normalize the 'fields' parameter to ensure it's in the format expected by Jira API.

        The Jira API expects 'fields' as a comma-separated string, but agents may pass it as a list.
        This method converts list format to string format while preserving string format.

        Args:
            payload_params: Dictionary of parameters that may contain 'fields'

        Returns:
            Dictionary with normalized 'fields' parameter
        """
        if "fields" in payload_params:
            fields = payload_params["fields"]

            # Convert list to comma-separated string
            if isinstance(fields, list):
                # Filter out non-string elements and strip whitespace
                field_strings = [str(field).strip() for field in fields if field]
                payload_params["fields"] = ",".join(field_strings)

            # Ensure string format is clean (strip whitespace from comma-separated values)
            elif isinstance(fields, str):
                # Split, strip, and rejoin to normalize whitespace
                field_list = [field.strip() for field in fields.split(",") if field.strip()]
                payload_params["fields"] = ",".join(field_list)

        return payload_params

    def _is_attachment_operation(self, relative_url: str) -> bool:
        """Check if the operation is for file attachments."""
        return "/attachments" in relative_url or "/attachment" in relative_url

    def _handle_file_attachments(
        self, relative_url: str, params: Optional[str], files_content: Dict[str, tuple]
    ) -> str:
        """
        Handle file attachment operations for Jira issues.

        Args:
            relative_url: The relative URL (used to extract issue key)
            params: Optional JSON params (can contain issue key)
            files_content: Dictionary mapping file names to (content, mime_type) tuples

        Returns:
            str: Response message indicating success or failure

        Raises:
            ToolException: If files cannot be loaded or attachment fails
        """
        from langchain_core.tools import ToolException
        import io

        issue_key = self._extract_issue_key(relative_url, params)

        try:
            results = []
            for file_name, (content, mime_type) in files_content.items():
                file_content = io.BytesIO(content)
                file_content.name = file_name

                self.jira.add_attachment_object(issue_key, file_content)

                results.append(f"Successfully attached '{file_name}' to issue {issue_key}")

            return "\n".join(results)

        except Exception as e:
            raise ToolException(f"Failed to attach files to issue {issue_key}: {str(e)}")

    def _extract_issue_key(self, relative_url: str, params: Optional[str]) -> str:
        """
        Extract issue key from relative_url or params.

        Args:
            relative_url: The relative URL (may contain issue key)
            params: Optional JSON params (may contain issue key)

        Returns:
            str: The issue key (e.g., "PROJ-123")

        Raises:
            ToolException: If issue key cannot be determined
        """
        from langchain_core.tools import ToolException

        match = re.search(r"/issue/([A-Z]+-\d+|\d+)", relative_url)
        if match:
            return match.group(1)

        if params:
            payload_params = parse_payload_params(params)
            if "issue_key" in payload_params:
                return payload_params["issue_key"]
            if "issueKey" in payload_params:
                return payload_params["issueKey"]

        raise ToolException(
            "issue_key is required for file attachment. "
            "Provide it either in the relative_url (e.g., /rest/api/{version}/issue/{issueKey}/attachments) "
            "or in params as 'issue_key'"
        )

    def _is_single_issue_request(self, relative_url: str) -> bool:
        """Check whether the URL targets a single Jira issue (not search / bulk)."""
        return bool(re.match(SINGLE_ISSUE_PATTERN, relative_url))

    def _extract_image_attachments(self, response) -> list[dict]:
        """Extract image attachment metadata from a single-issue Jira response."""
        try:
            response_json = response.json()
        except Exception as e:
            logger.warning(f"Failed to parse Jira issue response as JSON: {e}")
            return []

        fields = response_json.get("fields", {})
        if "attachment" not in fields:
            return []

        attachments = fields.get("attachment") or []
        if not attachments:
            return []

        image_attachments = []
        for att in attachments:
            mime_type = (att.get("mimeType") or "").lower()
            size = att.get("size", 0)
            content_url = att.get("content")

            if mime_type in IMAGE_MIME_TYPES and content_url and size <= MAX_IMAGE_SIZE_BYTES:
                image_attachments.append(
                    {
                        "filename": att.get("filename", "image"),
                        "content_url": content_url,
                        "mime_type": mime_type,
                        "size": size,
                    }
                )

        return image_attachments[:MAX_IMAGES_PER_RESPONSE]

    def _download_attachment_as_base64(self, content_url: str) -> str | None:
        """Download a Jira attachment using the authenticated session and return base64 data."""
        try:
            response = self.jira.request(
                method="GET",
                path=content_url,
                advanced_mode=True,
                absolute=True,
            )
            if response.status_code == 200 and response.content:
                return base64.b64encode(response.content).decode("utf-8")
        except Exception as e:
            logger.warning(f"Failed to download Jira attachment {content_url}: {e}")
        return None

    def _download_image_artifacts(self, image_attachments: list[dict]) -> list[dict] | None:
        """Download image attachments and return them as artifact dicts.

        Each artifact dict contains ``filename``, ``data`` (base64) and
        ``mime_type`` — ready to be stored on ``ToolMessage.artifact`` and
        later injected into the LLM context via a ``pre_model_hook``.
        """
        artifacts: list[dict] = []
        for att in image_attachments:
            base64_data = self._download_attachment_as_base64(att["content_url"])
            if base64_data:
                artifacts.append(
                    {
                        "filename": att["filename"],
                        "data": base64_data,
                        "mime_type": att["mime_type"],
                    }
                )
                logger.info("Downloaded Jira image artifact: %s (%d bytes)", att["filename"], att["size"])
            else:
                logger.warning("Skipping image %s — download failed", att["filename"])
        return artifacts or None

    def _limit_output_content(self, output: Any) -> Any:
        """Token-limit only the text portion; image metadata is lightweight."""
        if isinstance(output, JiraMultimodalResponse):
            limited_text, token_count = super()._limit_output_content(output.text)
            return JiraMultimodalResponse(
                text=limited_text if isinstance(limited_text, str) else str(limited_text),
                image_attachments=output.image_attachments,
            ), token_count
        return super()._limit_output_content(output)

    def _post_process_output_content(self, output: Any, *args, **kwargs) -> Any:
        """Return a ``(content, artifact)`` tuple for ``content_and_artifact``.

        When image attachments are present the artifact carries the
        downloaded base64 images; a ``pre_model_hook`` on the agent graph
        will inject them into the LLM context as ``HumanMessage`` content
        blocks.
        """
        if isinstance(output, JiraMultimodalResponse) and output.image_attachments:
            text = super()._post_process_output_content(output.text, *args, **kwargs)
            artifacts = self._download_image_artifacts(output.image_attachments)
            return text, artifacts
        if isinstance(output, JiraMultimodalResponse):
            text = super()._post_process_output_content(output.text, *args, **kwargs)
            return text, None
        text = super()._post_process_output_content(output, *args, **kwargs)
        return text, None
