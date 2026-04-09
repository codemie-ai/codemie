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

import json
from typing import List, Optional, Any
from copy import deepcopy

from elasticsearch import NotFoundError
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.tools import BaseTool, ToolException
from langgraph.graph.state import CompiledStateGraph

from codemie.agents.assistant_agent import AIToolsAgent
from codemie.agents.langgraph_agent import LangGraphAgent
from codemie.agents.utils import validate_json_schema
from codemie.configs import config
from codemie.configs.logger import logger
from codemie.core.dependecies import get_disable_prompt_cache, set_disable_prompt_cache
from codemie.core.models import AssistantChatRequest, IdeChatRequest, ToolConfig
from codemie.core.template_security import render_system_prompt_template, TemplateSecurityError
from codemie.core.thread import MessageQueue
from codemie.core.utils import build_unique_file_objects, build_unique_file_objects_list, append_random_suffix
from codemie.core.workflow_models import WorkflowAssistant, WorkflowState
from codemie.rest_api.models.assistant import (
    Assistant,
    MissingContextException,
    AssistantBase,
    PromptVariable,
    ToolKitDetails,
    get_current_time,
)
from codemie.rest_api.security.user import User
from codemie.service.assistant import VirtualAssistantService
from codemie.service.assistant.assistant_user_mapping_service import assistant_user_mapping_service
from codemie.service.aws_bedrock.bedrock_orchestration_service import BedrockOrchestratorService
from codemie.service.llm_service.llm_service import llm_service
from codemie.service.llm_service.utils import set_llm_context
from codemie.service.tools.tools_info_service import ToolsInfoService
from codemie.service.tools.toolkit_service import ToolkitService


class AssistantService:
    json_prompt = """

**IMPORTANT** Your response MUST be a valid JSON document that strictly adheres to the following JSON Schema:
{schema}
**IMPORTANT** DO NOT include any additional text, markdown formatting, or commentary outside of the JSON structure!
**IMPORTANT** If your output does not match the schema, it will be considered invalid!
**IMPORTANT** DO NOT add any additional fields to the JSON, only those defined in the JSON Schema!
**IMPORTANT** DO NOT add any additional text before or after the JSON!
**IMPORTANT** DO NOT return anything other than a JSON document that strictly adheres to the provided JSON Schema!
"""

    suggested_json_prompt = """
Here is the JSON schema/example:
{schema}
Use its fields to optimize tool usage and data gathering as needed. Don't return JSON documents;
Instead, leverage the schema's data to generate deeper insights and improve tool calls.
"""

    _skills_suffix = (
        "\n\n## This assistant has available skills that provide specialized domain/task/purpose knowledge. "
        "Use the `skill` tool to load a required skill by name whenever you need domain-specific guidance, "
        "best practices, or step-by-step instructions for relevant task/ask. "
        "For complex tasks that span multiple/complex tasks/asks, load each relevant skill one by one as needed."
    )

    @classmethod
    def get_tools_info(cls, user: User, show_for_ui: bool = False):
        return ToolsInfoService.get_tools_info(show_for_ui=show_for_ui, user=user)

    @staticmethod
    def ensure_unique_slug(slug: str) -> str:
        """Return a unique slug for an assistant, generating a suffixed variant if the slug is already taken."""
        if Assistant.get_by_fields({"slug.keyword": slug}):
            unique_slug = append_random_suffix(slug)
            return unique_slug
        return slug

    @staticmethod
    # System defined built-in variables
    def _get_default_prompt_variables(current_user=""):
        """Get system-defined default prompt variables

        Args:
            current_user: If provided, will override the default empty value for current_user
        """
        return [
            PromptVariable(key="current_user", description="Current username", default_value=current_user),
            PromptVariable(key="date", description="Current date and time", default_value=get_current_time()),
        ]

    @staticmethod
    def render_system_prompt_with_vars(
        system_prompt_template: str,
        prompt_vars: dict[str, str],
        assistant_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> str:
        """Render a system prompt template with provided variables using secure template rendering.

        This method uses sandboxed Jinja2 template rendering to prevent
        Server-Side Template Injection (SSTI) attacks.

        Args:
            system_prompt_template: The Jinja2 template string for the system prompt
            prompt_vars: Dictionary of variable key-value pairs to use in template rendering
            assistant_id: Optional assistant ID (for logging and fetching user-specific variables)
            user_id: Optional user ID (for logging and fetching user-specific variables)

        Returns:
            str: The rendered system prompt

        Raises:
            TemplateSecurityError: If the template contains security violations
        """
        logger.info(
            f"Rendering system prompt template. "
            f"AssistantId={assistant_id or 'N/A'}, "
            f"UserId={user_id or 'N/A'}, "
            f"VariableCount={len(prompt_vars)}"
        )

        # Combine all variables - provided vars + defaults + user-specific overrides
        all_variables = {}

        # First, add default system variables (current_user and date)
        current_user = prompt_vars.get("current_user", "")
        default_vars = AssistantService._get_default_prompt_variables(current_user=current_user)
        logger.debug(f"Default prompt variables: {[var.model_dump() for var in default_vars]}")
        for var in default_vars:
            all_variables[var.key] = var.default_value

        # Then add provided prompt variables (will override defaults if keys overlap)
        all_variables.update(prompt_vars)

        # If assistant_id is provided, fetch user-specific variable overrides
        if assistant_id and user_id:
            try:
                # Lazy import to avoid circular imports
                from codemie.service.assistant.assistant_prompt_variable_mapping_service import (
                    assistant_prompt_variable_mapping_service,
                )

                logger.debug(
                    f"Fetching user-specific variable values for assistant_id={assistant_id}, user_id={user_id}"
                )

                user_variable_values = assistant_prompt_variable_mapping_service.get_user_variable_values(
                    assistant_id, user_id
                )

                if user_variable_values:
                    logger.debug(f"Found user variables (count: {len(user_variable_values)})")
                    all_variables.update(user_variable_values)
                else:
                    logger.debug("No user-specific variable values found")
            except Exception as e:
                logger.warning(
                    f"Failed to fetch user-specific variables for assistant_id={assistant_id}, user_id={user_id}: {e}"
                )

        logger.debug(f"Final template variables for rendering: keys={list(all_variables.keys())}")

        # Use secure template rendering with sandboxing and validation
        try:
            rendered_prompt = render_system_prompt_template(
                template_str=system_prompt_template,
                context=all_variables,
                allow_custom_variables=True,  # Allow custom prompt variables defined by users
            )
            return rendered_prompt

        except TemplateSecurityError as e:
            logger.error(
                f"Security violation detected in system prompt template. "
                f"AssistantId={assistant_id or 'N/A'}, "
                f"UserId={user_id or 'N/A'}, "
                f"Error={str(e)}",
                exc_info=True,
            )
            raise

    @staticmethod
    def get_system_prompt(assistant, user_id, **data):
        """Return prompt with evaluated variables using secure template rendering.

        This method uses sandboxed Jinja2 template rendering to prevent
        Server-Side Template Injection (SSTI) attacks.

        Args:
            assistant: Assistant object to get the prompt from
            user_id: user ID to get personalized variable values
            **data: Override variable values passed at runtime

        Returns:
            str: The rendered system prompt

        Raises:
            TemplateSecurityError: If the template contains security violations
        """
        logger.info(
            f"Generating system prompt for assistant. AssistantId={getattr(assistant, 'id', 'virtual')}, "
            f"UserId={user_id}"
        )

        # Decrypt sensitive default values if present
        if assistant.prompt_variables:
            AssistantService._decrypt_assistant_prompt_variables(assistant)

        # Combine all variables - custom variables from the assistant model + runtime data
        all_variables = {}

        # Add custom variables from the assistant model
        if assistant.prompt_variables:
            sanitized = [
                {
                    **{k: v for k, v in var.model_dump().items() if k != "default_value"},
                    "has_value": bool(var.default_value),
                }
                for var in assistant.prompt_variables
            ]
            logger.debug(f"Assistant prompt variables: {sanitized}")
            for var in assistant.prompt_variables:
                all_variables[var.key] = var.default_value

        # Override with any user-provided data which takes highest precedence
        if data:
            logger.debug(f"Overriding with runtime data: {data}")
            all_variables.update(data)

        # Use the refactored method to render the template
        # This method will handle adding default variables, user-specific variables, and secure rendering
        return AssistantService.render_system_prompt_with_vars(
            system_prompt_template=assistant.system_prompt,
            prompt_vars=all_variables,
            assistant_id=getattr(assistant, 'id', None),
            user_id=user_id,
        )

    @staticmethod
    def _create_subagent_executors(
        assistant: Assistant,
        user: User,
        request: AssistantChatRequest,
        request_uuid: str,
        thread_generator: MessageQueue,
        llm_model: str,
    ) -> list[CompiledStateGraph[Any, Any, Any, Any]]:
        """Create subagent executors for assistants with sub-assistants.

        Args:
            assistant: The parent assistant with sub-assistant IDs
            user: Current user
            request: Chat request
            request_uuid: Unique request identifier
            thread_generator: Message queue for streaming
            llm_model: LLM model to use for subagents

        Returns:
            dict: Dictionary of subagent executors keyed by assistant ID
        """
        if not assistant.assistant_ids:
            return []

        from codemie.service.tools.assistant_factory import create_assistant_executors

        logger.debug(f"Creating subagent executors for {len(assistant.assistant_ids)} sub-assistants")
        subagents = create_assistant_executors(
            assistant_ids=assistant.assistant_ids,
            user=user,
            request=request,
            request_uuid=request_uuid,
            thread_generator=thread_generator,
            llm_model=llm_model,
            parent_assistant=assistant,
        )
        logger.debug(f"Created {len(subagents)} subagent executors")
        return subagents

    @staticmethod
    def _get_subagent_descriptions(assistant: Assistant, user: User) -> dict[str, str]:
        """Fetch assistant descriptions for handoff tool creation.

        Args:
            assistant: The parent assistant with sub-assistant IDs
            user: Current user

        Returns:
            Dictionary mapping assistant names to descriptions
        """
        if not assistant.assistant_ids:
            return {}

        try:
            sub_assistants = Assistant.get_by_ids(user, assistant.assistant_ids, parent_assistant=assistant)
            descriptions = {
                sub_assistant.name: sub_assistant.description or f"Assistant {sub_assistant.name}"
                for sub_assistant in sub_assistants
            }
            logger.debug(f"Fetched descriptions for {len(descriptions)} subagents")
            return descriptions
        except Exception as e:
            logger.error(f"Failed to fetch subagent descriptions: {str(e)}")
            return {}

    @classmethod
    def _build_bedrock_agent(
        cls,
        assistant: Assistant,
        request: AssistantChatRequest,
        user: User,
        request_uuid: str,
        thread_generator: MessageQueue = None,
        tool_callbacks: list[BaseCallbackHandler] = None,
    ) -> AIToolsAgent:
        """Build a Bedrock agent (both Agents and AgentCore Runtime)."""
        logger.info(f"Building Bedrock agent for AssistantId={assistant.id}")
        return AIToolsAgent(
            agent_name=assistant.name,
            description=assistant.description,
            tools=[],  # Bedrock agents don't use tools
            request=request,
            system_prompt=cls.get_system_prompt(assistant, user.id),
            request_uuid=request_uuid,
            user=user,
            is_react=False,  # Bedrock assistants are not reactive
            llm_model="",  # Bedrock assistants don't use standard LLM models
            temperature=assistant.temperature,
            top_p=assistant.top_p,
            thread_generator=thread_generator,
            stream_steps=request.stream,
            callbacks=tool_callbacks or [],
            assistant=assistant,
        )

    @classmethod
    def _apply_marketplace_tool_mappings(
        cls,
        assistant: Assistant,
        user: User,
        request: AssistantChatRequest,
    ) -> None:
        """Apply user-specific tool mappings for marketplace assistants."""
        if not assistant.is_global:
            return

        logger.debug(f"Loading user-specific tool mappings for marketplace assistant {assistant.id} and user {user.id}")

        if assistant.mcp_servers:
            mcp_server_names = [s.name for s in assistant.mcp_servers]
            logger.debug(f"Assistant has {len(assistant.mcp_servers)} MCP servers: {mcp_server_names}")

        mapping = assistant_user_mapping_service.get_mapping(assistant_id=assistant.id, user_id=user.id)

        if not mapping or not mapping.tools_config:
            logger.debug(f"No tool mappings found for marketplace assistant {assistant.id} and user {user.id}")
            return

        logger.debug(f"Found {len(mapping.tools_config)} tool mappings for user {user.id}")
        logger.debug(f"Tool mapping names: {[tc.name for tc in mapping.tools_config]}")

        local_tools_config = [
            ToolConfig(
                name=tc.name,
                integration_id=tc.integration_id,
            )
            for tc in mapping.tools_config
        ]

        # Merge with existing tools_config from request
        if request.tools_config:
            logger.debug(
                f"Merging {len(local_tools_config)} mapped tools with {len(request.tools_config)} request tools"
            )
            request.tools_config.extend(local_tools_config)
        else:
            logger.debug(f"Setting {len(local_tools_config)} mapped tools as request tools_config")
            request.tools_config = local_tools_config

    @classmethod
    def _prepare_system_prompt(
        cls,
        assistant: Assistant,
        user: User,
        request: AssistantChatRequest,
    ) -> str:
        """Prepare system prompt with optional IDE decorations and output schema."""
        system_prompt = cls.get_system_prompt(assistant, user_id=user.id, current_user=user.full_name)

        if isinstance(request, IdeChatRequest):
            system_prompt = cls.decorate_system_prompt(system_prompt, request)

        if getattr(assistant, "skill_ids", None):
            system_prompt = f"{system_prompt}{cls._skills_suffix}"

        if request.output_schema:
            schema = json.dumps(request.output_schema)
            output_schema_prompt = cls.suggested_json_prompt.format(schema=schema)
            system_prompt = f"{system_prompt}\n{output_schema_prompt}"

        return system_prompt

    @classmethod
    def _configure_langgraph_agent(
        cls,
        agent_kwargs: dict[str, Any],
        assistant: Assistant,
        user: User,
        request: AssistantChatRequest,
        request_uuid: str,
        thread_generator: MessageQueue,
        llm_model: str,
        smart_tool_selection_enabled: bool,
    ) -> None:
        """Configure LangGraph-specific agent parameters (modifies agent_kwargs in place)."""
        agent_kwargs["smart_tool_selection_enabled"] = smart_tool_selection_enabled

        # Create subagent executors if assistant has sub-assistants
        subagents = cls._create_subagent_executors(
            assistant=assistant,
            user=user,
            request=request,
            request_uuid=request_uuid,
            thread_generator=thread_generator,
            llm_model=llm_model,
        )
        if subagents:
            agent_kwargs["subagents"] = subagents
            agent_kwargs["subagent_descriptions"] = cls._get_subagent_descriptions(assistant, user)

    @classmethod
    def build_agent(
        cls,
        assistant: Assistant,
        request: AssistantChatRequest,
        user: User,
        request_uuid: str,
        thread_generator: MessageQueue = None,
        tool_callbacks: list[BaseCallbackHandler] = None,
        request_headers: dict[str, str] | None = None,
    ):
        cache_disabled = get_disable_prompt_cache()
        logger.info(
            f"Building agent. Started. "
            f"AssistantId={assistant.id}, "
            f"AssistantName={assistant.name}, "
            f"AssistantType={assistant.type}, "
            f"AssistantContext={assistant.context}, "
            f"AssistantToolkits={len(assistant.toolkits)}, "
            f"User={user.name}, "
            f"request_uuid: {request_uuid}, "
            f"request_model={request.llm_model}, "
            f"assistant_model={assistant.llm_model_type}, "
            f"cache_disabled={cache_disabled}"
        )
        set_llm_context(assistant, None, user)

        # Because we do inplace changes in following lines, do a copy to keep external object safe
        request = deepcopy(request)

        file_objects = build_unique_file_objects(
            file_names=request.file_names, conversation_id=request.conversation_id, history_index=request.history_index
        )
        request.file_names = list(file_objects.keys())

        # Handle Bedrock assistants (both Agents and AgentCore Runtime) - early return
        if BedrockOrchestratorService.is_bedrock_assistant(assistant):
            return cls._build_bedrock_agent(assistant, request, user, request_uuid, thread_generator, tool_callbacks)

        cls.check_context(assistant)

        # Determine LLM model and agent type
        llm_model = request.llm_model or assistant.llm_model_type
        if request.llm_model:
            assistant.llm_model_type = request.llm_model

        is_react = llm_model in llm_service.get_react_llms()

        # Determine if smart tools should be enabled (BOTH global flag AND assistant setting)
        smart_tool_selection_enabled = config.TOOL_SELECTION_ENABLED and assistant.smart_tool_selection_enabled

        # Apply marketplace tool mappings if needed
        cls._apply_marketplace_tool_mappings(assistant, user, request)

        # Get tools for the agent
        tools = ToolkitService.get_tools(
            assistant,
            request,
            user,
            llm_model,
            request_uuid,
            is_react,
            thread_generator,
            file_objects=list(file_objects.values()),
            smart_tool_selection_enabled=smart_tool_selection_enabled,
            request_headers=request_headers,
        )

        # Prepare system prompt with decorations and schema
        system_prompt = cls._prepare_system_prompt(assistant, user, request)

        # Select agent class based on configuration
        agent_class = LangGraphAgent if config.ENABLE_LANGGRAPH_AITOOLS_AGENT and not is_react else AIToolsAgent

        # Build common agent initialization parameters
        agent_kwargs = {
            "agent_name": assistant.name,
            "description": assistant.description,
            "tools": tools,
            "request": request,
            "system_prompt": system_prompt,
            "request_uuid": request_uuid,
            "user": user,
            "is_react": is_react,
            "llm_model": llm_model,
            "output_schema": request.output_schema,
            "temperature": assistant.temperature,
            "top_p": assistant.top_p,
            "thread_generator": thread_generator,
            "stream_steps": request.stream,
            "callbacks": tool_callbacks or [],
            "assistant": assistant,
        }

        # Configure LangGraph-specific parameters if needed
        if agent_class == LangGraphAgent:
            cls._configure_langgraph_agent(
                agent_kwargs,
                assistant,
                user,
                request,
                request_uuid,
                thread_generator,
                llm_model,
                smart_tool_selection_enabled,
            )

        agent = agent_class(**agent_kwargs)
        logger.info(
            f"Building agent. Finished. "
            f"AssistantId={assistant.id}, "
            f"AssistantName={assistant.name}, "
            f"AssistantContext={assistant.context}, "
            f"User={user.name}, "
            f"request_uuid: {request_uuid}. "
            f"Result=["
            f"agent_name={agent.agent_name},"
            f"llm_model={agent.llm_model},"
            f"temperature={agent.temperature},"
            f"top_p={agent.top_p},"
            f"recursion_limit={agent.recursion_limit},"
            f"agent_request={agent.request.text}]"
        )
        return agent

    @classmethod
    def decorate_system_prompt(cls, prompt: str, request: IdeChatRequest):
        return '\n---\n'.join(filter(lambda part: part, [request.prompt_header, prompt, request.prompt_footer])).strip()

    @classmethod
    def propagate_token_limit(cls, assistant: AssistantBase, tools: list[BaseTool], token_limit: int):
        for tool in tools:
            if hasattr(tool, 'tokens_size_limit'):
                tool.tokens_size_limit = token_limit
                logger.debug(
                    f"Setting tokens limit for tool. "
                    f"Assistant={assistant.name}. "
                    f"Tool={tool.name}. "
                    f"Limit={tool.tokens_size_limit}."
                )

    @classmethod
    def prepare_tools_config_from_toolkits(cls, toolkits: List[ToolKitDetails]) -> Optional[List[ToolConfig]]:
        if not toolkits:
            return None

        tools_config = []
        for toolkit in toolkits:
            tools_config.extend(toolkit.get_tool_configs())

        return tools_config if tools_config else None

    @staticmethod
    def load_and_validate_schema(schema: str) -> dict | None:
        try:
            schema: dict = json.loads(schema)
            is_valid_json_schema = validate_json_schema(schema)
            output_schema = schema if is_valid_json_schema else None
            return output_schema
        except json.JSONDecodeError:
            pass

    @classmethod
    def _load_and_configure_workflow_assistant(
        cls,
        workflow_assistant: WorkflowAssistant,
        user: User,
        project_name: str,
        execution_id: str,
    ) -> Assistant:
        """Load assistant (database or virtual) and configure for workflow execution."""
        try:
            if workflow_assistant.assistant_id:
                assistant = Assistant.get_by_id(workflow_assistant.assistant_id)
                # Ensure version is set for database assistants
                if not hasattr(assistant, "version") or assistant.version is None:
                    assistant.version = assistant.version_count if hasattr(assistant, "version_count") else 1
            else:
                assistant = VirtualAssistantService.create_from_virtual_asst_config(
                    user=user, config=workflow_assistant, project_name=project_name, execution_id=execution_id
                )
                # Virtual assistants already have version set to 1 in VirtualAssistantService.create()
        except (KeyError, NotFoundError):
            raise ValueError(f"Assistant wasn't found in database. AssistantId: {workflow_assistant.assistant_id}")

        # Apply workflow-specific overrides
        if workflow_assistant.model:
            assistant.llm_model_type = workflow_assistant.model

        if workflow_assistant.temperature is not None:
            assistant.temperature = workflow_assistant.temperature

        if not assistant.llm_model_type:
            assistant.llm_model_type = llm_service.default_llm_model

        return assistant

    @classmethod
    def _prepare_workflow_system_prompt(
        cls,
        workflow_assistant: WorkflowAssistant,
        assistant: Assistant,
        user: User,
        workflow_state: WorkflowState,
        mcp_server_args_preprocessor: Optional[callable],
    ) -> tuple[str, dict | None]:
        """Prepare system prompt with output schema for workflow execution.

        Returns:
            Tuple of (system_prompt, output_schema)
        """
        system_prompt = (
            workflow_assistant.system_prompt
            if workflow_assistant.system_prompt
            else cls.get_system_prompt(assistant, user_id=user.id, current_user=user.full_name)
        )

        if getattr(assistant, "skill_ids", None):
            system_prompt = f"{system_prompt}{cls._skills_suffix}"

        output_schema = None
        if workflow_state and workflow_state.output_schema:
            schema = str(workflow_state.output_schema)
            if mcp_server_args_preprocessor:
                schema = mcp_server_args_preprocessor(schema, None)

            # If output_schema is simple json example, we use it as example
            # but for real JSON schemas we enforce structured outputs
            output_schema = cls.load_and_validate_schema(schema)

            output_schema_prompt = (
                cls.suggested_json_prompt.format(schema=schema)
                if output_schema
                else cls.json_prompt.format(schema=schema)
            )
            system_prompt = f"{system_prompt}\n{output_schema_prompt}"

        return system_prompt, output_schema

    @classmethod
    def _select_agent_class_for_workflow(cls, assistant: Assistant, llm_model: str) -> type:
        """Select appropriate agent class for workflow execution."""
        condition = (
            config.ENABLE_LANGGRAPH_AITOOLS_AGENT
            and not BedrockOrchestratorService.is_bedrock_assistant(assistant)  # type: ignore
            and llm_model not in llm_service.get_react_llms()
        )
        return LangGraphAgent if condition else AIToolsAgent

    @classmethod
    def build_agent_for_workflow(
        cls,
        user_input: str,
        user: User,
        request_uuid: str,
        workflow_assistant: WorkflowAssistant,
        workflow_state: WorkflowState = None,
        tool_callbacks: list[BaseCallbackHandler] = None,
        thread_generator: MessageQueue = None,
        file_names: Optional[list[str]] = None,
        resume_execution: bool = False,
        execution_id: str = None,
        project_name: str = None,
        mcp_server_args_preprocessor: Optional[callable] = None,
        request_headers: dict[str, str] | None = None,
        trace_context=None,  # For workflow trace unification
        disable_cache: Optional[bool] = False,
    ):
        # Load and configure assistant for workflow execution
        assistant = cls._load_and_configure_workflow_assistant(workflow_assistant, user, project_name, execution_id)

        llm_model = assistant.llm_model_type
        is_react = assistant.is_react

        logger.info(
            f"Building agent for Workflow. Started. "
            f"AssistantId={assistant.id}, "
            f"AssistantName={assistant.name}, "
            f"LLM={llm_model}, "
            f"AssistantContext={assistant.context}, "
            f"User={user.name}, "
            f"execution_id: {execution_id}, "
            f"request_uuid: {request_uuid}"
        )

        set_llm_context(assistant, None, user)
        set_disable_prompt_cache(disable_cache)

        # Build request with workflow context
        request = AssistantChatRequest(
            conversation_id=execution_id,
            text=user_input,
            file_names=file_names,
            resume_execution=resume_execution,
            # Pass workflow execution_id as context for MCP tools
            # This enables MCP servers to track tool invocations within workflow executions
            workflow_execution_id=execution_id,
            tools_config=cls.prepare_tools_config_from_toolkits(assistant.toolkits),
            disable_cache=disable_cache,
        )

        file_objects = build_unique_file_objects_list(
            file_names=request.file_names, conversation_id=request.conversation_id, history_index=request.history_index
        )

        # Get tools for workflow execution
        try:
            tools = ToolkitService.get_tools(
                assistant=assistant,
                request=request,
                user=user,
                llm_model=llm_model,
                request_uuid=request_uuid,
                is_react=is_react,
                thread_generator=thread_generator,
                exclude_extra_context_tools=workflow_assistant.exclude_extra_context_tools,
                mcp_server_args_preprocessor=mcp_server_args_preprocessor,
                file_objects=file_objects,
                request_headers=request_headers,
            )
        except ToolException as exc:
            raise ValueError(exc)

        # Apply tool output token limits if specified
        if workflow_assistant.limit_tool_output_tokens:
            cls.propagate_token_limit(assistant, tools, workflow_assistant.limit_tool_output_tokens)

        # Prepare system prompt with output schema
        system_prompt, output_schema = cls._prepare_workflow_system_prompt(
            workflow_assistant, assistant, user, workflow_state, mcp_server_args_preprocessor
        )

        # Select agent class based on configuration
        agent_class = cls._select_agent_class_for_workflow(assistant, llm_model)

        # Build common agent initialization parameters
        agent_kwargs = {
            "agent_name": assistant.name,
            "description": assistant.description,
            "tools": tools,
            "request": request,
            "system_prompt": system_prompt,
            "request_uuid": request_uuid,
            "user": user,
            "is_react": is_react,
            "llm_model": llm_model,
            "output_schema": output_schema,
            "temperature": assistant.temperature,
            "thread_generator": thread_generator,
            "stream_steps": False,
            "callbacks": tool_callbacks or [],
            "assistant": assistant,
            "trace_context": trace_context,  # For workflow trace unification
        }

        # Configure LangGraph-specific parameters if needed (reusing helper from build_agent)
        if agent_class == LangGraphAgent:
            cls._configure_langgraph_agent(
                agent_kwargs, assistant, user, request, request_uuid, thread_generator, llm_model, False
            )

        agent = agent_class(**agent_kwargs)

        logger.info(
            f"Building agent. Finished. "
            f"AssistantId={assistant.id}, "
            f"AssistantName={assistant.name}, "
            f"AssistantContext={assistant.context}, "
            f"User={user.name}, "
            f"request_uuid: {request_uuid}. "
            f"Result=["
            f"agent_name={agent.agent_name},"
            f"llm_model={agent.llm_model},"
            f"agent_request={agent.request.text}]"
        )
        return agent

    @staticmethod
    def _decrypt_assistant_prompt_variables(assistant: Assistant):
        """
        Decrypt sensitive prompt variable default values in the assistant definition.
        Modifies the assistant object in place.

        Args:
            assistant: The assistant object whose prompt variables should be decrypted
        """
        from codemie.service.encryption.encryption_factory import EncryptionFactory

        if not assistant.prompt_variables:
            return

        encryption_service = EncryptionFactory().get_current_encryption_service()

        for var in assistant.prompt_variables:
            if getattr(var, 'is_sensitive', False) and var.default_value:
                try:
                    logger.debug(f"Decrypting sensitive prompt variable: {var.key}")
                    var.default_value = encryption_service.decrypt(var.default_value)
                except Exception as e:
                    logger.error(f"Failed to decrypt sensitive prompt variable '{var.key}': {str(e)}", exc_info=True)
                    # Keep encrypted value to avoid breaking the prompt entirely
                    # The user will see an error in logs but the assistant will still function

    @classmethod
    def check_context(cls, assistant: Assistant):
        if not assistant.context:
            return
        missed_context = assistant.get_deleted_context()
        if missed_context:
            logger.error(f"Not all context are present in system, missed: {missed_context}")
            raise MissingContextException(
                f"Cannot initialize assistant, missed datasource context in "
                f"system: \n{'\n'.join(f' - Datasource name: **{ctx}**' for ctx in missed_context)}"
            )
