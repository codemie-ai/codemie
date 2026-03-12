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

"""
LangChain callback for capturing tool errors before LLM processes them.

This callback automatically intercepts tool execution failures and creates
structured error details, ensuring errors are not absorbed by the model's
generated text.
"""

import re
from typing import Any, Optional
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler

from codemie.configs import logger
from codemie.core.errors import ErrorCode, ToolErrorDetails, classify_http_error


class ToolErrorCaptureCallback(BaseCallbackHandler):
    """
    LangChain callback to capture tool errors during agent execution.

    Automatically invoked by LangChain when tool execution fails. Captures
    comprehensive error details including HTTP status codes, error messages,
    and classification for structured error handling.

    Usage:
        callback = ToolErrorCaptureCallback(agent_name="MyAgent")
        config = {"callbacks": [callback, ...]}
        agent.invoke(inputs, config=config)

        # After execution, check for errors
        if callback.has_errors():
            tool_errors = callback.tool_errors
    """

    def __init__(self, agent_name: str = "unknown"):
        """
        Initialize the tool error capture callback.

        Args:
            agent_name: Name of the agent using this callback (for error attribution)
        """
        super().__init__()
        self.agent_name = agent_name
        self.tool_errors: list[ToolErrorDetails] = []

    def on_tool_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        parent_run_id: Optional[UUID] = None,
        **kwargs: Any,
    ) -> None:
        """
        Invoked automatically by LangChain when tool execution fails.

        Captures comprehensive error details including:
        - Tool name and call ID
        - HTTP status code (if present in error message)
        - Classified error code (TOOL_AUTHENTICATION, TOOL_TIMEOUT, etc.)
        - Original error message
        - Agent context

        Args:
            error: The exception raised during tool execution
            run_id: Unique identifier for this tool invocation
            parent_run_id: ID of the parent run (if nested)
            **kwargs: Additional context (name, serialized, etc.)
        """
        # Extract tool name from kwargs or use default
        tool_name = kwargs.get("name", "unknown_tool")
        error_message = str(error)

        # Extract HTTP status code if present in error message
        http_status = self._extract_http_status(error_message)

        # Classify error based on HTTP status and/or message content
        error_code = classify_http_error(http_status, error_message) if http_status else ErrorCode.TOOL_EXECUTION_FAILED

        # Create structured error details
        tool_error = ToolErrorDetails(
            tool_name=tool_name,
            tool_call_id=str(run_id),
            error_code=error_code,
            message=error_message,
            http_status=http_status,
            details={
                "agent": self.agent_name,
                "error_type": error.__class__.__name__,
                "parent_run_id": str(parent_run_id) if parent_run_id else None,
            },
        )

        # Store error for later retrieval
        self.tool_errors.append(tool_error)

        # Log for debugging (but don't fail execution)
        logger.error(
            f"Tool error captured by callback: agent={self.agent_name} "
            f"tool={tool_name} error_code={error_code.value} "
            f"status={http_status} message={error_message}"
        )

    @staticmethod
    def _extract_http_status(error_content: str) -> Optional[int]:
        """
        Extract HTTP status code from error message if present.

        Looks for patterns like "401:", "HTTP 403", "status 404", etc.

        Args:
            error_content: Error message text

        Returns:
            HTTP status code if found, None otherwise

        Examples:
            >>> ToolErrorCaptureCallback._extract_http_status("401: Unauthorized")
            401
            >>> ToolErrorCaptureCallback._extract_http_status("HTTP 403 Forbidden")
            403
            >>> ToolErrorCaptureCallback._extract_http_status("status: 404")
            404
            >>> ToolErrorCaptureCallback._extract_http_status("Connection failed")
            None
        """
        # Match patterns: "401:", "HTTP 403", "status 404", "Status: 500"
        patterns = [
            r'\b(\d{3})\s*:',  # "401:", "403:"
            r'HTTP\s+(\d{3})',  # "HTTP 401"
            r'status\s*:?\s*(\d{3})',  # "status: 404", "status 500"
        ]

        for pattern in patterns:
            match = re.search(pattern, error_content, re.IGNORECASE)
            if match:
                try:
                    return int(match.group(1))
                except (ValueError, IndexError):
                    continue

        return None

    def has_errors(self) -> bool:
        """
        Check if any tool errors were captured during execution.

        Returns:
            True if one or more tool errors were captured, False otherwise
        """
        return len(self.tool_errors) > 0

    def clear(self) -> None:
        """
        Clear all captured errors.

        Useful for resetting the callback between agent invocations
        when reusing the same callback instance.
        """
        self.tool_errors.clear()
        logger.debug(f"Cleared tool errors for agent: {self.agent_name}")

    def get_error_summary(self) -> str:
        """
        Get a human-readable summary of all captured errors.

        Returns:
            Summary string listing all tool errors

        Examples:
            >>> callback.get_error_summary()
            '3 tool errors: jira_search (401), confluence_get (404), slack_send (500)'
        """
        if not self.has_errors():
            return "No tool errors"

        error_list = [f"{err.tool_name} ({err.http_status or err.error_code.value})" for err in self.tool_errors]
        return f"{len(self.tool_errors)} tool error{'s' if len(self.tool_errors) > 1 else ''}: {', '.join(error_list)}"
