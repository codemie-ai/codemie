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

"""Shared mixin for Azure DevOps attachment operations."""

from urllib.parse import quote

import httpx
from langchain_core.tools import ToolException

from codemie_tools.base.codemie_tool import logger


class AzureDevOpsAttachmentMixin:
    """Mixin providing shared attachment upload/download functionality for Azure DevOps tools."""

    def _upload_attachment(self, filename: str, content: bytes) -> str:
        """
        Upload a file as an attachment using Azure DevOps Attachments API.

        Args:
            filename: Name of the file to upload
            content: File content as bytes

        Returns:
            str: The attachment URL that can be linked to work items or wikis

        Raises:
            ToolException: If upload fails
        """
        try:
            # Construct the API URL for attachments
            api_url = (
                f"{self.config.organization_url}/{self.config.project}"
                f"/_apis/wit/attachments?fileName={quote(filename)}&api-version=7.1"
            )

            # Upload the attachment
            with httpx.Client(timeout=120.0) as client:
                response = client.post(
                    api_url,
                    content=content,
                    headers={"Content-Type": "application/octet-stream"},
                    auth=("", self.config.token),  # Basic auth with empty username
                )
                response.raise_for_status()
                result = response.json()

                # Extract the attachment URL from the response
                attachment_url = result.get("url")
                if not attachment_url:
                    raise ToolException("No URL returned from attachment upload")

                logger.info(f"Uploaded attachment '{filename}' successfully: {attachment_url}")
                return attachment_url

        except httpx.HTTPStatusError as e:
            error_msg = f"Failed to upload attachment '{filename}': HTTP {e.response.status_code} - {e.response.text}"
            logger.error(error_msg)
            raise ToolException(error_msg)
        except Exception as e:
            error_msg = f"Failed to upload attachment '{filename}': {str(e)}"
            logger.error(error_msg)
            raise ToolException(error_msg)

    def _download_attachment(self, attachment_url: str, filename: str) -> bytes:
        """
        Download attachment content from Azure DevOps.

        Args:
            attachment_url: Full URL to the attachment
            filename: Name of the file (for logging)

        Returns:
            bytes: Attachment content

        Raises:
            ToolException: If download fails
        """
        try:
            logger.info(f"Downloading attachment: {filename}")

            with httpx.Client(timeout=120.0) as client:
                response = client.get(
                    attachment_url,
                    auth=("", self.config.token),  # Basic auth with empty username
                )
                response.raise_for_status()

                logger.info(f"Successfully downloaded attachment: {filename} ({len(response.content)} bytes)")
                return response.content

        except httpx.HTTPStatusError as e:
            error_msg = f"Failed to download attachment '{filename}': HTTP {e.response.status_code}"
            logger.error(error_msg)
            raise ToolException(error_msg)
        except Exception as e:
            error_msg = f"Failed to download attachment '{filename}': {str(e)}"
            logger.error(error_msg)
            raise ToolException(error_msg)
