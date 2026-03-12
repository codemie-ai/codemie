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
Assistant Health Check Service

Provides health check functionality for assistants, validating both configuration
and actual functionality by testing the assistant with a simple message.
"""

from codemie.configs import logger
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import AssistantChatRequest
from codemie.rest_api.models.assistant import (
    Assistant,
    AssistantHealthCheckRequest,
    AssistantHealthCheckResponse,
    AssistantHealthCheckError,
)
from codemie.rest_api.security.user import User
from codemie.service.assistant_service import AssistantService


class AssistantHealthCheckService:
    """Service for performing health checks on assistants"""

    @classmethod
    def health_check_assistant(
        cls, assistant: Assistant, request: AssistantHealthCheckRequest, user: User, raw_request=None
    ) -> AssistantHealthCheckResponse:
        """
        Perform a comprehensive health check on an assistant.

        This method checks:
        1. Configuration is valid (tools, context, etc.)
        2. Assistant can execute and generate a response from the LLM

        Note: Assistant existence and user access checks are performed in the router
        before calling this method (similar to /model endpoint pattern).

        Args:
            assistant: Assistant to check (already validated in router)
            request: Health check request with optional test message and parameters
            user: User performing the health check
            raw_request: Optional raw FastAPI request object for context

        Returns:
            AssistantHealthCheckResponse with health check results
        """
        assistant_id = assistant.id
        assistant_name = assistant.name

        try:
            # Step 1: Validate configuration
            config_valid, config_error = cls._validate_assistant_configuration(assistant)
            if not config_valid:
                return AssistantHealthCheckResponse(
                    is_healthy=False,
                    assistant_id=assistant_id,
                    assistant_name=assistant_name,
                    configuration_valid=False,
                    execution_successful=False,
                    error=config_error,
                )

            # Step 2: Test the assistant by calling it with "show tools"
            (
                execution_successful,
                tools_available,
                tools_available_count,
                tools_misconfigured,
                tools_misconfigured_count,
                execution_error,
            ) = cls._test_assistant_execution(assistant, user, request.version, raw_request)

            if not execution_successful:
                # If tools are misconfigured, it's a configuration issue
                has_tool_misconfiguration = bool(tools_misconfigured)

                return AssistantHealthCheckResponse(
                    is_healthy=False,
                    assistant_id=assistant_id,
                    assistant_name=assistant_name,
                    configuration_valid=not has_tool_misconfiguration,
                    execution_successful=False,
                    tools_available=tools_available or None,
                    tools_available_count=tools_available_count or None,
                    tools_misconfigured=tools_misconfigured or None,
                    tools_misconfigured_count=tools_misconfigured_count or None,
                    error=execution_error,
                )

            # All checks passed
            return AssistantHealthCheckResponse(
                is_healthy=True,
                assistant_id=assistant_id,
                assistant_name=assistant_name,
                configuration_valid=True,
                execution_successful=True,
                tools_available=tools_available or None,
                tools_available_count=tools_available_count or None,
                tools_misconfigured=None,  # No misconfigured tools on success
                tools_misconfigured_count=None,  # No misconfigured tools on success
            )

        except Exception as e:
            logger.error(f"Unexpected error during assistant health check: {str(e)}", exc_info=True)
            return AssistantHealthCheckResponse(
                is_healthy=False,
                assistant_id=assistant_id,
                assistant_name=assistant_name,
                configuration_valid=False,
                execution_successful=False,
                error=AssistantHealthCheckError(
                    message="Health check failed",
                    details=f"An unexpected error occurred: {str(e)}",
                    help="Please check the logs or contact support",
                    error_type="unexpected_error",
                ),
            )

    @classmethod
    def _validate_assistant_configuration(cls, assistant: Assistant) -> tuple[bool, AssistantHealthCheckError | None]:
        """
        Validate assistant configuration (context, sub-assistants, etc.)

        Args:
            assistant: Assistant to validate

        Returns:
            Tuple of (is_valid, error_or_none)
        """
        try:
            # Check context if configured
            if assistant.context:
                for context_item in assistant.context:
                    # Validate context exists (knowledge base or code repo)
                    from codemie.rest_api.models.index import IndexInfo

                    index = IndexInfo.get_by_fields({"repo_name.keyword": context_item.name})
                    if not index:
                        return (
                            False,
                            AssistantHealthCheckError(
                                message="Context not found",
                                details=f"Context '{context_item.name}' does not exist",
                                help="Please verify the context/datasource exists and is accessible",
                                error_type="context_error",
                            ),
                        )

            # Validate nested assistants if present
            if assistant.assistant_ids:
                for nested_id in assistant.assistant_ids:
                    nested_assistant = Assistant.find_by_id(nested_id)
                    if not nested_assistant:
                        return (
                            False,
                            AssistantHealthCheckError(
                                message="Nested assistant not found",
                                details=f"Nested assistant '{nested_id}' does not exist",
                                help="Please verify all nested assistants exist",
                                error_type="nested_assistant_error",
                            ),
                        )

            return True, None

        except Exception as e:
            logger.error(f"Error validating assistant configuration: {str(e)}", exc_info=True)
            return (
                False,
                AssistantHealthCheckError(
                    message="Configuration validation failed",
                    details=f"Failed to validate configuration: {str(e)}",
                    help="Please check the assistant configuration",
                    error_type="validation_error",
                ),
            )

    @classmethod
    def _extract_expected_tool_names(cls, assistant: Assistant) -> set[str]:
        """Extract expected tool names from assistant configuration.

        Args:
            assistant: Assistant to extract tool names from

        Returns:
            Set of expected tool names
        """
        expected_tool_names = set()
        if assistant.toolkits:
            for toolkit in assistant.toolkits:
                for tool in toolkit.tools:
                    expected_tool_names.add(tool.name)
        return expected_tool_names

    @classmethod
    def _extract_actual_tool_names(cls, agent) -> tuple[set[str], int]:
        """Extract actual tool names from the built agent.

        Args:
            agent: The built agent

        Returns:
            Tuple of (set of actual tool names, count of tools)
        """
        actual_tool_names = set()
        if hasattr(agent, "tools") and agent.tools:
            for tool in agent.tools:
                # Get the tool name from the tool instance
                tool_name = getattr(tool, "name", None) or tool.__class__.__name__
                actual_tool_names.add(tool_name)

        return actual_tool_names, len(actual_tool_names)

    @classmethod
    def _validate_tools_match(
        cls, expected_tool_names: set[str], actual_tool_names: set[str], tools_count: int
    ) -> tuple[bool, set[str], AssistantHealthCheckError | None]:
        """Validate that all expected tools are present in actual tools.

        Args:
            expected_tool_names: Set of expected tool names
            actual_tool_names: Set of actual tool names
            tools_count: Count of actual tools

        Returns:
            Tuple of (tools_match, misconfigured_tools_set, error_or_none)
        """
        misconfigured_tools = expected_tool_names - actual_tool_names if expected_tool_names else set()

        if misconfigured_tools:
            return (
                False,
                misconfigured_tools,
                AssistantHealthCheckError(
                    message="Tool misconfiguration detected",
                    details=f"Expected tools failed to load in agent: {', '.join(sorted(misconfigured_tools))}. "
                    f"Expected: {', '.join(sorted(expected_tool_names))}. "
                    f"Actual: {', '.join(sorted(actual_tool_names))}",
                    help="Please check the assistant's tool configuration and ensure all tools are properly configured",
                    error_type="tool_misconfiguration_error",
                ),
            )

        return True, misconfigured_tools, None

    @classmethod
    def _test_assistant_execution(
        cls,
        assistant: Assistant,
        user: User,
        version: int | None,
        raw_request,
    ) -> tuple[bool, set[str], int, set[str], int, AssistantHealthCheckError | None]:
        """Test the assistant's ability to execute and generate a response.

        This method builds the agent, validates tools are loaded correctly,
        and calls the LLM with "show tools" to verify end-to-end execution.

        Args:
            assistant: Assistant to test
            user: User performing the test
            version: Optional version to test
            raw_request: Optional raw request for context

        Returns:
            Tuple of (execution_successful, tools_available, tools_available_count,
                     tools_misconfigured, tools_misconfigured_count, error_or_none)
        """
        tools_available = set()
        tools_available_count = 0
        tools_misconfigured = set()
        tools_misconfigured_count = 0
        try:
            # Extract expected tool names from assistant configuration
            expected_tool_names = cls._extract_expected_tool_names(assistant)

            # Build the test request - non-streaming with "show tools" message
            test_request = AssistantChatRequest(
                text="show tools",
                stream=False,  # Non-streaming for health check
                version=version,
            )

            # Extract headers if needed
            from codemie.rest_api.utils.request_utils import extract_custom_headers

            request_headers = extract_custom_headers(raw_request, test_request.propagate_headers) if raw_request else {}

            try:
                # Build and execute the agent with the test message
                agent = AssistantService.build_agent(
                    assistant=assistant,
                    request=test_request,
                    user=user,
                    request_uuid=raw_request.state.uuid if raw_request and hasattr(raw_request.state, "uuid") else None,
                    request_headers=request_headers,
                )

                # Extract actual tool names and count from the built agent
                tools_available, tools_available_count = cls._extract_actual_tool_names(agent)

                # Validate tools match: all expected tools must be present in actual tools
                tools_match, tools_misconfigured, error = cls._validate_tools_match(
                    expected_tool_names, tools_available, tools_available_count
                )
                tools_misconfigured_count = len(tools_misconfigured)

                if not tools_match:
                    return (
                        False,
                        tools_available,
                        tools_available_count,
                        tools_misconfigured,
                        tools_misconfigured_count,
                        error,
                    )

                # Actually call the model to verify it works
                _ = agent.generate()

                # If we got here, the agent was able to generate a response
                logger.info(
                    f"Assistant health check passed for assistant '{assistant.name}' ({assistant.id}) - "
                    f"successfully generated response with {tools_available_count} tools"
                )

                return (
                    True,
                    tools_available,
                    tools_available_count,
                    tools_misconfigured,
                    tools_misconfigured_count,
                    None,
                )

            except ExtendedHTTPException as e:
                return (
                    False,
                    tools_available,
                    tools_available_count,
                    tools_misconfigured,
                    tools_misconfigured_count,
                    AssistantHealthCheckError(
                        message="Assistant execution failed",
                        details=f"{e.message}: {e.details}",
                        help=e.help or "Please check the assistant configuration",
                        error_type="execution_failed",
                    ),
                )
            except Exception as e:
                logger.error(f"Failed to execute assistant: {str(e)}", exc_info=True)
                return (
                    False,
                    tools_available,
                    tools_available_count,
                    tools_misconfigured,
                    tools_misconfigured_count,
                    AssistantHealthCheckError(
                        message="Failed to execute assistant",
                        details=f"Could not execute assistant: {str(e)}",
                        help="Please check the assistant configuration, LLM connectivity, and tool settings",
                        error_type="execution_error",
                    ),
                )

        except Exception as e:
            logger.error(f"Error testing assistant execution: {str(e)}", exc_info=True)
            return (
                False,
                set(),
                0,
                set(),
                0,
                AssistantHealthCheckError(
                    message="Execution test failed",
                    details=f"Failed to test assistant execution: {str(e)}",
                    help="Please check the logs for more details",
                    error_type="execution_test_error",
                ),
            )
