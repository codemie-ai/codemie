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

from typing import Any, Optional

NOT_FOUND_MESSAGE = "Not Found"


class ValidationException(ValueError):
    """Raised when request or domain validation fails."""


class MissingPlaceholderVariablesException(ValidationException):
    """Raised when materialization request omits required template variables."""

    def __init__(self, missing_variables: list[str]):
        self.missing_variables = missing_variables
        super().__init__("Required placeholder variables are missing or blank.")


class MaterializationFailedException(ValidationException):
    """Raised when a template cannot be materialized into a valid workflow seed."""


class NotFoundException(Exception):
    """Raised when a requested resource does not exist."""


class ExtendedHTTPException(Exception):
    """
    A custom exception class that extends the built-in Exception class to provide more detailed HTTP error information.

    This class is designed to be used with FastAPI, to provide rich error responses that include not just an error
    code and message, but also optional details and help text.

    Attributes:
        code (int): The HTTP status code associated with this error.
        message (str): A brief, human-readable description of the error.
        details (str, optional): Additional information about the error, such as the specific
                                 reason it occurred or the exact field that caused the issue.
        help (str, optional): Guidance on how to resolve the error or what steps the user should take next.

    Example:
        raise ExtendedHTTPException(
            code=400,
            message="Invalid input",
            details="The 'email' field must be a valid email address.",
            help="Please check the format of your email and try again."
        )
    """

    def __init__(self, code: int, message: str, details: str | dict = None, help: str = None):
        self.code = code
        self.message = message
        self.details = details
        self.help = help


class GitLabOAuthNotConnected(ExtendedHTTPException):
    """Raised when the acting user has no OAuth token row for a GitLab integration.

    Signals that the user must complete OAuth under their own GitLab account before the
    shared integration can be used on their behalf; surfaced to the user as an actionable
    connect prompt rather than a generic failure.
    """

    def __init__(self, setting_id: str = "", details: str = ""):
        super().__init__(
            code=400,
            message="Connect your GitLab account to use this integration.",
            details=details or f"No GitLab OAuth token for the current user on setting '{setting_id}'.",
        )


class TaskException(Exception):
    original_exc: Optional[Any] = None

    def __init__(self, *args: object, **kwargs: dict) -> None:
        self.original_exc = kwargs.pop("original_exc", None)
        super().__init__(*args)


class InterruptedException(Exception):
    def __init__(self, message: str, interrupted_state: str, checkpoint_state: Optional[dict] = None) -> None:
        self.message = message
        self.interrupted_state = interrupted_state
        self.checkpoint_state = checkpoint_state
        super().__init__(message)


class MCPAuthenticationRequiredException(Exception):
    """Raised when an MCP server requires user authentication before tools can be used.

    Defined here as infrastructure for core to catch. Bridge-layer code
    translates enterprise resolver exceptions into this type before raising
    into the core workflow path.

    Attributes:
        payload: Structured auth context. BaseNode enriches this with
            `node_name` and serializes it into workflow execution output.
    """

    def __init__(self, payload: dict) -> None:
        self.payload = payload
        super().__init__(str(payload))


class GitLabAuthRequiredException(MCPAuthenticationRequiredException):
    """Raised when a shared GitLab integration is used but the acting user has no token.

    Subclasses MCPAuthenticationRequiredException so it rides the same "authentication
    required" propagation the agent/stream layers already implement (tool assembly raises,
    the run surfaces the payload to the client instead of a swallowed tool error). The
    payload carries a distinct ``error`` value so the client renders a GitLab connect gate.
    """

    def __init__(self, setting_id: str, integration_name: str = "") -> None:
        super().__init__(
            {
                "error": "gitlab_auth_required",
                "setting_id": setting_id,
                "integration_name": integration_name or "GitLab integration",
            }
        )


class JiraAuthRequiredException(MCPAuthenticationRequiredException):
    """Raised when a shared Jira OAuth integration is used but the acting user has no token.

    Mirrors GitLabAuthRequiredException: subclasses MCPAuthenticationRequiredException so it rides
    the existing auth-required propagation, and carries a distinct ``error`` value so the client
    renders a Jira connect gate.
    """

    def __init__(self, setting_id: str, integration_name: str = "") -> None:
        super().__init__(
            {
                "error": "jira_auth_required",
                "setting_id": setting_id,
                "integration_name": integration_name or "Jira integration",
            }
        )


class ConfluenceAuthRequiredException(MCPAuthenticationRequiredException):
    """Raised when a shared Confluence OAuth integration is used but the acting user has no token."""

    def __init__(self, setting_id: str, integration_name: str = "") -> None:
        super().__init__(
            {
                "error": "confluence_auth_required",
                "setting_id": setting_id,
                "integration_name": integration_name or "Confluence integration",
            }
        )


class OAuthConnectRequiredException(MCPAuthenticationRequiredException):
    """Raised when one or more shared per-user OAuth integrations the assistant uses are not
    connected by the acting user.

    Mirrors the MCP ``authentication_required`` aggregate: instead of prompting for one provider at a
    time, all unconnected GitLab/Jira/Confluence integrations are surfaced together so the user can
    connect them in a single step and resend once. Each entry in ``providers`` reuses the
    per-provider ``error`` value (gitlab_auth_required / jira_auth_required / confluence_auth_required)
    so the client can render the existing per-provider connect prompts.
    """

    def __init__(self, providers: list[dict]) -> None:
        super().__init__({"error": "oauth_connect_required", "providers": providers})


class LiteLLMException(Exception):
    """
    Base exception for LiteLLM service errors.

    This exception is raised when LiteLLM API operations fail.
    """

    def __init__(self, message: str, details: Optional[str] = None, original_exc: Optional[Exception] = None):
        self.message = message
        self.details = details
        self.original_exc = original_exc
        super().__init__(message)


class LiteLLMBudgetException(LiteLLMException):
    """
    Exception raised when LiteLLM budget operations fail.

    This includes budget creation, retrieval, or validation errors.
    """

    pass


class LiteLLMCustomerException(LiteLLMException):
    """
    Exception raised when LiteLLM customer operations fail.

    This includes customer creation, retrieval, update, or deletion errors.
    """

    pass


class PlatformToolError(Exception):
    """Base exception for platform tools."""

    def __init__(self, message: str, details: Optional[str] = None):
        self.message = message
        self.details = details
        super().__init__(message)


class UnauthorizedPlatformAccessError(PlatformToolError):
    """Raised when non-admin user attempts to use platform tools."""

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class InvalidFilterCombinationError(PlatformToolError):
    """Raised when invalid filter combination is provided."""

    def __init__(self, message: str):
        super().__init__(message)


class ServiceUnavailableError(PlatformToolError):
    """Raised when an enterprise service is not available."""

    def __init__(self, message: str, service_name: str = "Enterprise service"):
        self.service_name = service_name
        details = (
            f"{service_name} is not available. "
            "Please ensure the codemie-enterprise package is installed and properly configured."
        )
        super().__init__(message, details=details)


class WorkflowGenerationError(Exception):
    """Raised by the workflow service layer when generation or refinement fails."""

    def __init__(self, message: str, details: str = None, help: str = None):
        self.message = message
        self.details = details
        self.help = help
        super().__init__(message)


class TokenLimitExceededException(ValueError):
    """
    Raised when LLM response is truncated due to max_tokens limit during tool calling.

    This exception indicates that the model's output was cut off before completing
    the tool call arguments, making them incomplete and unusable.

    Attributes:
        message (str): Detailed error message with fix instructions
        model (str): The LLM model that hit the token limit
        truncation_reason (str): The API response reason (e.g., 'finish_reason=length')

    Example:
        raise TokenLimitExceededException(
            "Response truncated. Increase max_output_tokens.",
            model="claude-3-7",
            truncation_reason="finish_reason=length"
        )
    """

    def __init__(
        self,
        message: str,
        model: Optional[str] = None,
        truncation_reason: Optional[str] = None,
    ):
        self.message = message
        self.model = model
        self.truncation_reason = truncation_reason
        super().__init__(message)
