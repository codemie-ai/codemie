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

from typing import Any, List, Optional, Type

from codemie_tools.base.file_object import FileObject
from codemie_tools.base.models import Tool, ToolKit, ToolSet
from codemie_tools.file_analysis.toolkit import FileAnalysisToolkit
from codemie_tools.research.toolkit import ResearchConfig, ResearchToolkit
from codemie_tools.vision.toolkit import VisionToolkit
from langchain_core.tools import BaseTool, ToolException

from codemie.agents.tools import CodeToolkit, KBToolkit
from codemie.agents.tools.code.tools_vars import (
    CODE_SEARCH_BY_PATHS_TOOL,
    CODE_SEARCH_TOOL_V2,
    READ_FILES_TOOL,
    READ_FILES_WITH_SUMMARY_TOOL,
    REPO_TREE_TOOL_V2,
)
from codemie.agents.tools.ide.ide_toolkit import IdeToolkit
from codemie.agents.tools.platform import PlatformToolkit
from codemie_tools.research.tools_vars import (
    GOOGLE_SEARCH_RESULTS_TOOL,
    TAVILY_SEARCH_TOOL,
    WEB_SCRAPPER_TOOL,
)
from codemie_tools.data_management.code_executor.tools_vars import (
    PYTHON_RUN_CODE_TOOL,
    CODE_EXECUTOR_TOOL,
)
from codemie.configs import config
from codemie.configs.logger import logger
from codemie.core.constants import CodeIndexType, ToolType
from codemie.core.dependecies import get_llm_by_credentials
from codemie.core.models import AssistantChatRequest, CodeFields, IdeChatRequest, ToolConfig
from codemie.core.thread import MessageQueue
from codemie.core.utils import build_unique_file_objects_list
from codemie.rest_api.models.assistant import (
    Assistant,
    Context,
    ContextType,
    ToolKitDetails,
    VirtualAssistant,
)
from codemie.rest_api.models.conversation import Conversation
from codemie.rest_api.models.index import (
    CodeIndexInfo,
    FilteredIndexInfo,
    IndexInfo,
    KnowledgeBaseIndexInfo,
    ProviderIndexInfo,
)
from codemie.rest_api.security.user import User
from codemie.service.llm_service.llm_service import llm_service
from codemie.service.mcp.toolkit_service import MCPToolkitService
from codemie.service.provider import ProviderToolkitsFactory
from codemie.service.tools.plugin_tools_delegate import PluginToolsDelegate
from codemie.service.tools.plugin_utils import cleanup_plugin_tool_name
from codemie.service.tools.toolkit_lookup_service import ToolkitLookupService
from codemie.repository.skill_repository import SkillRepository
from codemie.service.tools.toolkit_settings_service import ToolkitSettingService
from codemie.service.tools.tools_preprocessing import ToolsPreprocessorFactory
from codemie.agents.tools.skill.skill_tool import create_skill_tool_if_needed


class ToolkitService:
    """Service for managing and collecting tools for assistants.

    This service is responsible for:
    - Collecting tools from various sources (IDE, context, files, MCP, assistants)
    - Managing toolkit methods and provider toolkits
    - Filtering and preprocessing tools
    - Adding context-specific tools (KB, Provider, Code, Git)
    """

    @staticmethod
    def _get_file_objects_from_request(request: AssistantChatRequest | None) -> list[FileObject] | None:
        """
        Extract file objects from request with proper conversation and history context.

        Args:
            request: The assistant chat request, or None

        Returns:
            List of FileObject instances if request has files, None otherwise
        """
        if not request or not request.file_names:
            return None

        conversation_id = request.conversation_id if request else None
        history_index = request.history_index if request else None

        return build_unique_file_objects_list(request.file_names, conversation_id, history_index)

    @classmethod
    def get_toolkit_methods(cls):
        """Get mapping of toolkit types to their factory methods.

        Returns:
            Dictionary mapping ToolSet enum values to lambda functions that create toolkit instances
        """
        return {
            ToolSet.PLUGIN: lambda assistant, user, llm_model, request_uuid, request: cls._get_plugin_tools_delegate(
                assistant, user, request
            ),
            ToolSet.RESEARCH: lambda assistant, user, llm_model, request_uuid, request: ResearchToolkit.get_toolkit(
                configs=ResearchConfig(
                    google_search_api_key=config.GOOGLE_SEARCH_API_KEY,
                    google_search_cde_id=config.GOOGLE_SEARCH_CSE_ID,
                    tavily_search_key=config.TAVILY_API_KEY,
                ).model_dump()
            ).get_tools(),
            ToolSet.PLATFORM_TOOLS: lambda assistant, user, llm_model, request_uuid, request: PlatformToolkit(
                user=user
            ).get_tools(),
            ToolSet.FILE_SYSTEM: lambda assistant,
            user,
            llm_model,
            request_uuid,
            request: ToolkitSettingService.get_file_system_toolkit(
                assistant,
                assistant.project,
                user,
                llm_model,
                request_uuid,
                request.tools_config if request else None,
                cls._get_file_objects_from_request(request),
            ),
            **cls.get_provider_toolkits_methods(),
        }

    @classmethod
    def _augment_toolkits_with_feature_flags(
        cls,
        selected_toolkits: list[ToolKit],
        request: AssistantChatRequest,
        assistant: Assistant,
        user: User,
        llm_model: str,
        request_uuid: str,
    ) -> list[ToolKit]:
        """
        Augment toolkits based on feature flags in request.

        Dynamically adds Research or FileSystem toolkits if requested via flags
        and not already present.

        Args:
            selected_toolkits: Current list of toolkits
            request: The assistant chat request with feature flags
            assistant: The assistant configuration
            user: The user making the request
            llm_model: The LLM model being used
            request_uuid: Unique request identifier

        Returns:
            Updated list of toolkits with dynamically added ones
        """
        from codemie.configs.customer_config import customer_config
        from codemie_tools.base.models import Tool

        if not request:
            return selected_toolkits

        # Make a copy to avoid mutating the original
        toolkits = list(selected_toolkits)

        # Helper to check if toolkit already exists
        def has_toolkit(toolkit_name: str) -> bool:
            return any(tk.toolkit == toolkit_name or str(tk.toolkit) == toolkit_name for tk in toolkits)

        # Helper to check if any tools from a list are already configured
        def has_any_tool(tool_names: list[str]) -> bool:
            configured_tools = {tool.name for tk in toolkits for tool in tk.tools}
            return any(tool_name in configured_tools for tool_name in tool_names)

        # Web Search feature flag
        # Only add if explicitly requested in the API request AND customer config allows it
        enable_web_search = request.enable_web_search is True and customer_config.is_feature_enabled("webSearch")

        if (
            enable_web_search
            and not has_toolkit(ToolSet.RESEARCH)
            and not has_any_tool(config.DYNAMIC_WEB_SEARCH_TOOLS)
        ):
            logger.debug(f"Dynamically adding Research toolkit for assistant '{assistant.name}' based on feature flag")
            research_toolkit = ToolKit(
                toolkit=ToolSet.RESEARCH,
                tools=[
                    Tool.from_metadata(GOOGLE_SEARCH_RESULTS_TOOL),
                    Tool.from_metadata(TAVILY_SEARCH_TOOL),
                    Tool.from_metadata(WEB_SCRAPPER_TOOL),
                ],
            )
            toolkits.append(research_toolkit)

        # Code Interpreter feature flag
        # Only add if explicitly requested in the API request AND customer config allows it
        enable_code_interpreter = request.enable_code_interpreter is True and customer_config.is_feature_enabled(
            "dynamicCodeInterpreter"
        )

        if (
            enable_code_interpreter
            and not has_toolkit(ToolSet.FILE_SYSTEM)
            and not has_any_tool(config.DYNAMIC_CODE_INTERPRETER_TOOLS)
        ):
            logger.debug(
                f"Dynamically adding FileSystem toolkit (code interpreter) for assistant "
                f"'{assistant.name}' based on feature flag"
            )
            # Add FileSystem toolkit with code interpreter tools
            file_system_toolkit = ToolKit(
                toolkit=ToolSet.FILE_SYSTEM,
                tools=[
                    Tool.from_metadata(PYTHON_RUN_CODE_TOOL),
                    Tool.from_metadata(CODE_EXECUTOR_TOOL),
                ],
            )
            toolkits.append(file_system_toolkit)

        return toolkits

    @classmethod
    def get_provider_toolkits_methods(cls):
        """Get provider toolkit methods.

        Returns:
            Dictionary mapping provider toolkit names to their factory methods
        """
        provider_toolkits = ProviderToolkitsFactory.get_toolkits()
        toolkit_methods = {}

        for toolkit in provider_toolkits:
            toolkit_name = toolkit.get_tools_ui_info()['toolkit']

            toolkit_methods[toolkit_name] = (
                lambda assistant, user, llm_model, request_uuid, request, _toolkit=toolkit: [
                    tool(
                        project_id=assistant.project,
                        user=user,
                        request_uuid=request_uuid,
                        tool_config=request.tools_config,
                    )
                    for tool in _toolkit.get_toolkit().get_tools()
                ]
            )
        return toolkit_methods

    @classmethod
    def _get_plugin_tools_delegate(
        cls,
        assistant: Assistant,
        user: User,
        request: Optional[AssistantChatRequest],
    ) -> list[BaseTool]:
        """Delegate plugin tools retrieval to PluginToolsDelegate.

        Args:
            assistant: The assistant to get plugin tools for
            user: The user making the request
            request: The assistant chat request

        Returns:
            List of plugin tools (LangChain BaseTool instances)
        """
        return PluginToolsDelegate.get_plugin_tools(assistant, user, request)

    @classmethod
    def _merge_skill_toolkits(cls, assistant: Assistant) -> list[ToolKitDetails]:
        """Merge assistant toolkits with required toolkits from attached skills.

        Fetches skills referenced by assistant.skill_ids, collects their toolkits,
        and unions them with the assistant's own toolkits. De-duplicates by toolkit name
        so that if a toolkit is already present on the assistant it is not added twice.

        Args:
            assistant: The assistant whose toolset is being assembled

        Returns:
            Merged list of ToolKitDetails with unique toolkit entries
        """
        if not assistant.skill_ids:
            return list(assistant.toolkits or [])

        skills = SkillRepository.get_by_ids(assistant.skill_ids)

        existing_toolkit_names = {tk.toolkit for tk in (assistant.toolkits or [])}
        merged = list(assistant.toolkits or [])

        for skill in skills:
            for toolkit in skill.toolkits or []:
                if toolkit.toolkit not in existing_toolkit_names:
                    merged.append(toolkit)
                    existing_toolkit_names.add(toolkit.toolkit)
                    logger.debug(
                        f"Skill '{skill.name}' contributed toolkit '{toolkit.toolkit}' "
                        f"to assistant '{assistant.name}'"
                    )

        return merged

    @classmethod
    def _merge_skill_mcp_servers(cls, assistant: Assistant) -> list:
        """Merge assistant MCP servers with required MCP servers from attached skills.

        Fetches skills referenced by assistant.skill_ids, collects their mcp_servers,
        and unions them with the assistant's own mcp_servers. De-duplicates by server name
        so that if a server is already present on the assistant it is not added twice.

        Args:
            assistant: The assistant whose MCP server list is being assembled

        Returns:
            Merged list of MCPServerDetails with unique entries
        """
        if not assistant.skill_ids:
            return list(assistant.mcp_servers or [])

        skills = SkillRepository.get_by_ids(assistant.skill_ids)

        existing_server_names = {s.name for s in (assistant.mcp_servers or [])}
        merged = list(assistant.mcp_servers or [])

        for skill in skills:
            for mcp_server in skill.mcp_servers or []:
                if mcp_server.name not in existing_server_names:
                    merged.append(mcp_server)
                    existing_server_names.add(mcp_server.name)
                    logger.debug(
                        f"Skill '{skill.name}' contributed MCP server '{mcp_server.name}' "
                        f"to assistant '{assistant.name}'"
                    )

        return merged

    @classmethod
    def get_tools(
        cls,
        assistant: Assistant,
        request: AssistantChatRequest,
        user: User,
        llm_model: str,
        request_uuid: str,
        is_react: bool = True,
        thread_generator: MessageQueue = None,
        exclude_extra_context_tools: bool = False,
        file_objects=None,
        mcp_server_args_preprocessor: Optional[callable] = None,
        smart_tool_selection_enabled: Optional[bool] = False,
        request_headers: dict[str, str] | None = None,
    ) -> list[BaseTool]:
        """Main method to collect all tools for an assistant.

        When smart_tool_selection_enabled=True (or TOOL_SELECTION_ENABLED=True):
        Uses semantic search with request.text to find relevant tools when no toolkits configured

        Args:
            assistant: The assistant to collect tools for
            request: The assistant chat request (request.text used for semantic search query)
            user: The user making the request
            llm_model: The LLM model being used
            request_uuid: Unique request identifier
            is_react: Whether the agent uses ReAct pattern
            thread_generator: Thread generator for streaming
            exclude_extra_context_tools: Whether to exclude extra context tools
            file_objects: List of file objects to process
            mcp_server_args_preprocessor: Optional function to preprocess MCP server arguments
            smart_tool_selection_enabled: Whether to enable smart tool lookup (defaults to TOOL_SELECTION_ENABLED)

        Returns:
            List of tools for the assistant
        """
        if not file_objects:
            file_objects = []

        tools = []
        selected_toolkits = cls._merge_skill_toolkits(assistant)

        logger.debug(f"Initializing toolkits for assistant `{assistant.name}. Selected toolkits: {selected_toolkits}")
        # Try to lookup tools for user request if assistant doesn't have configured tools
        if not selected_toolkits and smart_tool_selection_enabled:
            # Smart tool lookup flow: use semantic search to find relevant tools when no toolkits configured
            # Build context-aware query from current request and recent chat history
            query = ToolkitLookupService.build_search_query_with_history(request)
            if query:
                selected_toolkits = ToolkitLookupService.get_tools_by_query(query=query)
                logger.info(
                    f"SmartToolLookup: Semantic search selected {len(selected_toolkits)} toolkits "
                    f"with {sum(len(tk.tools) for tk in selected_toolkits)} total tools"
                )

        # Dynamically add toolkits based on feature flags in request
        selected_toolkits = cls._augment_toolkits_with_feature_flags(
            selected_toolkits, request, assistant, user, llm_model, request_uuid
        )

        tools.extend(
            cls.get_core_tools(
                assistant_toolkits=selected_toolkits,
                user_id=user.id,
                project_name=assistant.project,
                assistant_id=assistant.id,
                tools_config=request.tools_config if request else None,
                file_objects=file_objects,
                is_admin=user.is_admin,
            )
        )
        logger.debug(f"Initialized core tools for assistant `{assistant.name}`. Total tools: {len(tools)}")

        # Context tools
        tools.extend(
            cls.add_context_tools(
                assistant,
                request,
                llm_model,
                user,
                request_uuid,
                is_react,
                exclude_extra_context_tools,
            )
        )
        logger.debug(f"Initialized context tools for assistant `{assistant.name}`. Total tools: {len(tools)}")

        # Skill tool - added only if assistant has attached skills
        skill_tool = create_skill_tool_if_needed(
            assistant_config=assistant,
            user=user,
        )
        if skill_tool:
            tools.append(skill_tool)
            logger.debug(f"Initialized skill tool for assistant `{assistant.name}`. Total tools: {len(tools)}")

        # File tools
        if file_objects:
            tools.extend(cls.add_file_tools(assistant, file_objects, request_uuid))
            logger.debug(f"Initialized file tools for assistant `{assistant.name}`. Total tools: {len(tools)}")

        tools.extend(
            cls._get_tools(
                assistant=assistant,
                request=request,
                user=user,
                llm_model=llm_model,
                request_uuid=request_uuid,
                thread_generator=thread_generator,
                mcp_server_args_preprocessor=mcp_server_args_preprocessor,
                request_headers=request_headers,
                augmented_toolkits=selected_toolkits,
            )
        )
        return tools

    @classmethod
    def get_core_tools(
        cls,
        assistant_toolkits: List[ToolKit],
        user_id: str,
        project_name: str,
        assistant_id: Optional[str],
        tools_config: Optional[List[ToolConfig]],
        file_objects=None,
        is_admin: bool = False,
    ) -> list[BaseTool]:
        tools = []
        for assistant_toolkit in assistant_toolkits:
            logger.debug(f"Processing toolkit: {assistant_toolkit.toolkit}")
            toolkit_tools = cls._initialize_toolkit_tools(
                assistant_toolkit.toolkit,
                assistant_toolkit.tools,
                user_id,
                project_name,
                assistant_id,
                tools_config,
                file_objects,
                is_admin,
            )
            tools.extend(toolkit_tools)
        return tools

    @classmethod
    def _initialize_toolkit_tools(
        cls,
        assistant_toolkit: str,
        assistant_tools: List[Tool],
        user_id: str,
        project_name: str,
        assistant_id: Optional[str],
        tools_config: Optional[List[ToolConfig]],
        file_objects=None,
        is_admin: bool = False,
    ) -> list[BaseTool]:
        """Process tools from a single toolkit and initialize them.

        Args:
            assistant_toolkit: The toolkit to process
            user_id: User ID for retrieving stored configurations
            project_name: Project name for configuration lookup
            assistant_id: Assistant ID for configuration lookup
            tools_config: Optional list of tool configurations from request

        Returns:
            List of initialized tool instances from this toolkit
        """
        toolkit_tools = []
        for assistant_tool in assistant_tools:
            tool = cls._initialize_tool(
                assistant_toolkit,
                assistant_tool,
                user_id,
                project_name,
                assistant_id,
                tools_config,
                file_objects,
                is_admin,
            )
            if tool:
                toolkit_tools.append(tool)
        return toolkit_tools

    @classmethod
    def _initialize_tool(
        cls,
        assistant_toolkit: str,
        assistant_tool: Tool,
        user_id: str,
        project_name: str,
        assistant_id: Optional[str],
        tools_config: Optional[List[ToolConfig]],
        file_objects=None,
        is_admin: bool = False,
    ) -> Optional[BaseTool]:
        """Initialize a single tool if its configuration is available.

        This method handles tool initialization with or without configuration
        using ToolMetadataService for consistency with validation.

        Args:
            assistant_toolkit: The toolkit containing the tool
            assistant_tool: The tool metadata to initialize
            user_id: User ID for retrieving stored configurations
            project_name: Project name for configuration lookup
            assistant_id: Assistant ID for configuration lookup
            tools_config: Optional list of tool configurations from request
            file_objects: Optional file objects for FileConfigMixin tools
            is_admin: Whether this is an admin request

        Returns:
            Initialized tool instance or None if initialization failed
        """
        from codemie.service.tools.tool_metadata_service import ToolMetadataService

        logger.debug(f"Initializing tool for toolkit: {assistant_toolkit}. Tool: {assistant_tool.name}")

        # Get tool and toolkit definitions (single call to avoid duplication)
        tool_definition, toolkit_definition = ToolMetadataService._get_tool_and_toolkit_definitions(
            assistant_tool.name, assistant_toolkit
        )

        if tool_definition is None or toolkit_definition is None:
            return None

        if not tool_definition.tool_class:
            logger.error(f"Cannot initialize tool: '{assistant_tool.name}'. No tool class available: {tool_definition}")
            return None

        # Check if tool requires credentials using already fetched definitions
        tool_requires = getattr(tool_definition, 'settings_config', False)
        toolkit_requires = getattr(toolkit_definition, 'settings_config', False)

        if not (tool_requires or toolkit_requires):
            # Tool can be used without config
            logger.debug(
                f"Initializing tool without config: '{assistant_tool.name}'. ToolClass: {tool_definition.tool_class}"
            )
            return tool_definition.tool_class()

        # Use SettingsService.get_config directly (similar to ToolConfigResolver but with is_admin support)
        from codemie.service.settings.settings import SettingsService

        stored_config = SettingsService.get_config(
            user_id=user_id,
            project_name=project_name,
            assistant_id=assistant_id,
            tool_config=cls._find_tool_config_by_name(tools_config, tool_definition.name),
            config_class=tool_definition.config_class,
            is_admin=is_admin,
        )

        if not stored_config:
            logger.info(f"Skipping tool: '{assistant_tool.name}'. No config found in database.")
            return None

        from codemie_tools.base.models import FileConfigMixin

        if isinstance(stored_config, FileConfigMixin) and file_objects:
            stored_config.input_files = file_objects
            logger.debug("Adding input files: %s", len(file_objects))

        return tool_definition.tool_class(config=stored_config)

    @classmethod
    def _find_tool_config_by_name(cls, tools_config: Optional[List[ToolConfig]], name: str) -> Optional[ToolConfig]:
        """Find a specific tool configuration by name from a list of tool configurations.

        Args:
            tools_config: List of tool configurations (may be None)
            name: Name of the tool to find

        Returns:
            The found tool configuration or None if not found
        """
        if tools_config:
            return next((tc for tc in tools_config if tc.name == name), None)
        return None

    @classmethod
    def _process_final_tools_search(
        cls,
        tools: list[BaseTool],
        llm_model: str,
        assistant: Assistant,
        request_uuid: str,
    ) -> list[BaseTool]:
        """Process final tools for semantic search flow.

        Args:
            tools: List of collected tools
            llm_model: The LLM model being used
            assistant: The assistant configuration
            request_uuid: Unique request identifier

        Returns:
            Processed list of tools
        """
        # Deduplicate tools by name
        unique_tools = {tool.name: tool for tool in tools}.values()
        unique_tools_list = list(unique_tools)

        # Apply tool preprocessors based on the LLM model
        preprocessor_chain = ToolsPreprocessorFactory.create_preprocessor_chain(llm_model)
        processed_tools = unique_tools_list
        for preprocessor in preprocessor_chain:
            processed_tools = preprocessor.process(processed_tools)

        # Log tool information
        tool_names = [tool.name for tool in processed_tools]
        logger.info(
            f"ToolSearch: Built agent with {len(processed_tools)} tools: {tool_names}. "
            f"AssistantId={assistant.id}, "
            f"AssistantName={assistant.name}, "
            f"request_uuid={request_uuid}"
        )

        return processed_tools

    @classmethod
    def _determine_mcp_server_lifecycle(cls, request: AssistantChatRequest) -> bool:
        """Determine effective MCP server lifecycle from request or conversation.

        Args:
            request: The assistant chat request

        Returns:
            Boolean indicating whether MCP server should be single-use
        """
        if hasattr(request, 'mcp_server_single_usage') and request.mcp_server_single_usage:
            return request.mcp_server_single_usage

        try:
            conversation = Conversation.get_by_id(request.conversation_id)
            if conversation and hasattr(conversation, 'mcp_server_single_usage'):
                return conversation.mcp_server_single_usage
        except Exception as e:
            logger.warning(f"Failed to determine MCP server lifecycle from conversation: {str(e)}")

        return False

    @classmethod
    def _get_tools(
        cls,
        assistant: Assistant,
        request: AssistantChatRequest,
        user: User,
        llm_model: str,
        request_uuid: str,
        thread_generator: MessageQueue,
        mcp_server_args_preprocessor: Optional[callable],
        request_headers: dict[str, str] | None = None,
        augmented_toolkits: Optional[list] = None,
    ) -> list[BaseTool]:
        """Traditional tool collection flow (used when tool search is disabled or fails).

        Args:
            assistant: The assistant to collect tools for
            request: The assistant chat request
            user: The user making the request
            llm_model: The LLM model being used
            request_uuid: Unique request identifier
            thread_generator: Thread generator for streaming
            mcp_server_args_preprocessor: Optional function to preprocess MCP server arguments

        Returns:
            List of filtered tools using traditional logic
        """
        tools = []

        # Collect all tools from different sources
        # IDE tools
        if isinstance(request, IdeChatRequest):
            tools.extend(cls.add_ide_tools(request, user))

        # Tools with credentials
        tools.extend(
            cls.add_tools_with_creds(
                assistant, user, llm_model, request_uuid, request, augmented_toolkits=augmented_toolkits
            )
        )

        # MCP tools (includes MCP servers contributed by attached skills)
        if config.MCP_CONNECT_ENABLED:
            effective_mcp_server_single_usage = cls._determine_mcp_server_lifecycle(request)
            tools.extend(
                MCPToolkitService.get_mcp_server_tools(
                    cls._merge_skill_mcp_servers(assistant),
                    user.id if user else None,
                    assistant.project,
                    request.conversation_id,
                    request.tools_config,
                    mcp_server_args_preprocessor,
                    effective_mcp_server_single_usage,
                    assistant_id=assistant.id,
                    workflow_execution_id=getattr(request, 'workflow_execution_id', None),
                    request_headers=request_headers,
                )
            )
        return cls._process_final_tools_traditional(tools, llm_model, assistant, request_uuid)

    @classmethod
    def _process_final_tools_traditional(
        cls,
        tools: list[BaseTool],
        llm_model: str,
        assistant: Assistant,
        request_uuid: str,
    ) -> list[BaseTool]:
        """Process final tools using traditional logic (no tool search).

        Args:
            tools: List of collected tools
            llm_model: The LLM model being used
            assistant: The assistant configuration
            request_uuid: Unique request identifier

        Returns:
            Processed list of tools
        """
        # Deduplicate tools by name
        unique_tools = {tool.name: tool for tool in tools}.values()
        unique_tools_list = list(unique_tools)

        # Apply tool preprocessors based on the LLM model
        preprocessor_chain = ToolsPreprocessorFactory.create_preprocessor_chain(llm_model)
        processed_tools = unique_tools_list
        for preprocessor in preprocessor_chain:
            processed_tools = preprocessor.process(processed_tools)

        # Log tool information
        cls._log_tool_information(processed_tools, assistant, request_uuid)

        return processed_tools

    @classmethod
    def _log_tool_information(cls, processed_tools: list[BaseTool], assistant: Assistant, request_uuid: str) -> None:
        """Log information about tools being used.

        Args:
            processed_tools: List of processed tools
            assistant: The assistant configuration
            request_uuid: Unique request identifier
        """
        tool_names = [tool.name for tool in processed_tools]
        initial_tool_names = [tool.name for toolkit in assistant.toolkits for tool in toolkit.tools]

        logger.info(
            f"Building agent with initial tools={initial_tool_names}. "
            f"Added tools={tool_names}. "
            f"AssistantId={assistant.id}, "
            f"AssistantName={assistant.name}, "
            f"User={assistant.name if hasattr(assistant, 'name') else 'Unknown'}, "
            f"request_uuid: {request_uuid}. "
        )

    @classmethod
    def add_tools_with_creds(
        cls,
        assistant: Assistant,
        user: User,
        llm_model: str,
        request_uuid: str,
        request: AssistantChatRequest = None,
        skip_filtering: bool = False,
        augmented_toolkits: Optional[list] = None,
    ):
        """Add tools that require credentials from various toolkits.

        Args:
            assistant: The assistant configuration
            user: The user making the request
            llm_model: The LLM model being used
            request_uuid: Unique request identifier
            request: The assistant chat request (optional)
            skip_filtering: If True, return all tools without filtering (for tool search flow)
            augmented_toolkits: Optional augmented toolkits list (includes dynamic tools)

        Returns:
            List of credential-based tools
        """
        tools = []
        toolkit_methods = cls.get_toolkit_methods()
        # Use augmented toolkits if provided, otherwise use assistant's configured toolkits
        toolkits_to_process = augmented_toolkits if augmented_toolkits is not None else assistant.toolkits
        for toolkit in toolkits_to_process:
            toolkit_method = toolkit_methods.get(
                toolkit.toolkit if isinstance(toolkit.toolkit, ToolSet) else str(toolkit.toolkit)
            )
            if toolkit_method:
                all_toolkit_tools = toolkit_method(assistant, user, llm_model, request_uuid, request)
                if skip_filtering:
                    # Tool search flow: add ALL tools without filtering
                    tools.extend(all_toolkit_tools)
                else:
                    # Traditional flow: filter tools based on assistant configuration
                    # Don't include internal tools for Virtual assistants unless explicitly specified
                    include_internal = not isinstance(assistant, VirtualAssistant)
                    tools.extend(
                        cls.filter_tools(
                            toolkits_to_process,
                            toolkit.toolkit,
                            all_toolkit_tools,
                            include_internal,
                        )
                    )
        return tools

    @classmethod
    def add_context_tools(
        cls,
        assistant: Assistant,
        request: AssistantChatRequest,
        llm_model: str,
        user: User,
        request_uuid: str,
        is_react: bool = True,
        exclude_extra_context_tools: bool = False,
    ):
        """Adds tools based on the context of the assistant.

        Args:
            assistant: The assistant configuration
            request: The assistant chat request
            llm_model: The LLM model being used
            user: The user making the request
            request_uuid: Unique request identifier
            is_react: Whether the agent uses ReAct pattern
            exclude_extra_context_tools: Whether to exclude extra context tools

        Returns:
            List of context-based tools
        """
        tools = []

        for context in assistant.context:
            if context.context_type == ContextType.KNOWLEDGE_BASE:
                cls._add_kb_tools(tools, context, assistant, llm_model)

            if context.context_type == ContextType.PROVIDER:
                cls._add_provider_context_tools(tools, assistant, context, user, request_uuid)

            if context.context_type == ContextType.CODE:
                cls._add_code_tools(tools, context, assistant, request, is_react, exclude_extra_context_tools)

                cls._add_git_related_tools(
                    tools=tools,
                    context=context,
                    assistant=assistant,
                    user_id=user.id,
                    llm_model=llm_model,
                    is_react=is_react,
                    request_uuid=request_uuid,
                )

        return tools

    @classmethod
    def add_ide_tools(cls, request: IdeChatRequest, user: User):
        """Add IDE integration tools.

        Args:
            request: The IDE chat request
            user: The user making the request

        Returns:
            List of IDE tools

        Raises:
            ToolException: If IDE settings are invalid
        """
        from codemie.service.settings.settings import SettingsService

        settings = SettingsService.get_ide_settings(user.id, request.ide_installation_id)
        if not settings:
            raise ToolException("Invalid IDE request")

        plugin_key = settings.credential(SettingsService.PLUGIN_KEY)
        prefix = f"{plugin_key}."
        filtered_tools = []
        for tool in request.tools:
            if tool.subject.startswith(prefix):
                filtered_tools.append(tool)

        return IdeToolkit(tool_definitions=filtered_tools, request_id=request.ide_request_id).get_tools()

    @classmethod
    def add_file_tools(cls, assistant: Assistant, file_objects: list[FileObject], request_uuid: str):
        """Add file processing tools for images and documents.

        Args:
            assistant: The assistant configuration
            file_objects: List of file objects to process
            request_uuid: Unique request identifier

        Returns:
            List of file analysis tools
        """
        logger.debug(f"Adding file tools for file_objects: {file_objects}")
        llm, multimodal_llm = cls._initialize_llm_for_files(assistant, request_uuid)

        # Separate files by type
        non_image_files = cls._get_non_image_files(file_objects)
        tools = []

        # Process image files if needed
        if not multimodal_llm and llm:
            tools.extend(cls._process_image_files(file_objects, llm))

        # Process non-image files with FileAnalysisToolkit
        if non_image_files:
            tools.extend(FileAnalysisToolkit.get_toolkit(files=non_image_files, chat_model=llm).get_tools())

        return tools

    @classmethod
    def _initialize_llm_for_files(cls, assistant: Assistant, request_uuid: str):
        """Initialize LLM for file processing.

        Args:
            assistant: The assistant configuration
            request_uuid: Unique request identifier

        Returns:
            Tuple of (llm, is_multimodal_llm)
        """
        llm_deployment_name = llm_service.get_llm_deployment_name(assistant.llm_model_type)
        llm = None
        multimodal_llm = True

        multimodal_llms = llm_service.get_multimodal_llms()
        if llm_deployment_name not in multimodal_llms:
            multimodal_llm = False
            if multimodal_llms:
                llm = get_llm_by_credentials(llm_model=multimodal_llms[0], streaming=False, request_id=request_uuid)
        else:
            llm = get_llm_by_credentials(llm_model=llm_deployment_name, streaming=False, request_id=request_uuid)

        return llm, multimodal_llm

    @classmethod
    def _get_non_image_files(cls, file_objects: list[FileObject]) -> list:
        """Extract non-image files from file objects.

        Args:
            file_objects: List of file objects to process

        Returns:
            List of non-image file objects
        """
        non_image_files = []
        for file_object in file_objects:
            if not file_object.is_image():
                non_image_files.append(file_object)
        return non_image_files

    @classmethod
    def _process_image_files(cls, file_objects: list[FileObject], llm) -> list:
        """Process image files using Vision toolkit.

        Args:
            file_objects: List of file objects to process
            llm: Language model to use for vision processing

        Returns:
            List of vision tools
        """
        tools = []
        image_files = []

        for file_object in file_objects:
            if file_object.is_image():
                image_files.append(file_object)

        if image_files:
            tools.extend(
                VisionToolkit.get_toolkit(
                    files=image_files,
                    chat_model=llm,
                ).get_tools()
            )

        return tools

    @classmethod
    def filter_tools(
        cls,
        assistant_toolkits: list[ToolKitDetails],
        toolkit_type: str,
        agent_tools: list[Any],
        include_internal_tools=True,
    ):
        """Filters out tools that are not selected in assistant configuration.

        Args:
            assistant_toolkits: List of toolkits configured in the assistant
            toolkit_type: The type of toolkit to filter
            agent_tools: List of available tools from the agent
            include_internal_tools: Whether to include internal tools (starting with '_')

        Returns:
            List of filtered tools
        """
        assistant_tools = []
        for assistant_toolkit in assistant_toolkits:
            if assistant_toolkit.toolkit == toolkit_type:
                assistant_tools.extend(assistant_toolkit.tools)
        assistant_tools_names = [tool.name for tool in assistant_tools]
        filtered_tools = []
        for agent_tool in agent_tools:
            is_plugin_tool = agent_tool.metadata and agent_tool.metadata.get('tool_type') == ToolType.PLUGIN
            has_global_plugin_tool = is_plugin_tool and ToolType.PLUGIN.value.capitalize() in assistant_tools_names

            tool_name = cleanup_plugin_tool_name(agent_tool.name) if is_plugin_tool else agent_tool.name
            tool_enabled = tool_name in assistant_tools_names
            tool_is_internal = tool_name.startswith('_')
            tool_enabled_but_with_suffix = next(
                filter(lambda x, tool_name=tool_name: tool_name.startswith(x + '_'), assistant_tools_names), None
            )

            if (
                tool_enabled
                or has_global_plugin_tool
                or (tool_is_internal and include_internal_tools)
                or tool_enabled_but_with_suffix
            ):
                filtered_tools.append(agent_tool)

        return filtered_tools

    @classmethod
    def _add_kb_tools(cls, tools: list[BaseTool], context: Context, assistant: Assistant, llm_model: str):
        """Adds Knowledge Base tools to the list of tools.

        Args:
            tools: List to append KB tools to
            context: The context configuration
            assistant: The assistant configuration
            llm_model: The LLM model being used
        """
        kb_index = cls._find_index(
            klass=KnowledgeBaseIndexInfo,
            project_name=assistant.project,
            repo_name=context.name,
        )

        if kb_index is None:
            return

        tools.extend(KBToolkit.get_tools(kb_index=kb_index, llm_model=llm_model))

    @classmethod
    def _add_provider_context_tools(
        cls, tools: list[BaseTool], assistant: Assistant, context: Context, user: User, request_uuid: str
    ):
        """Add provider context tools.

        Args:
            tools: List to append provider tools to
            assistant: The assistant configuration
            context: The context configuration
            user: The user making the request
            request_uuid: Unique request identifier
        """
        index_info = cls._find_index(
            klass=ProviderIndexInfo,
            project_name=assistant.project,
            repo_name=context.name,
        )

        if index_info is None:
            return

        provider_id = index_info.provider_fields.provider_id
        provider_toolkits = ProviderToolkitsFactory.get_toolkits_for_provider(provider_id)
        tool_names = [tool.name for toolkit in assistant.toolkits for tool in toolkit.tools]

        for toolkit in provider_toolkits:
            context_tools = toolkit().get_datasource_tools(datasource=index_info)

            tools.extend(
                [
                    tool(
                        project_id=assistant.project,
                        user=user,
                        request_uuid=request_uuid,
                        datasource=index_info,
                    )
                    for tool in context_tools
                    if tool.base_name in tool_names
                ]
            )

    @classmethod
    def _add_code_tools(
        cls,
        tools: list[BaseTool],
        context: Context,
        assistant: Assistant,
        request: AssistantChatRequest,
        is_react: bool = True,
        exclude_extra_context_tools: bool = False,
    ):
        """Adds Code tools to the list of tools.

        Args:
            tools: List to append code tools to
            context: The context configuration
            assistant: The assistant configuration
            request: The assistant chat request
            is_react: Whether the agent uses ReAct pattern
            exclude_extra_context_tools: Whether to exclude extra context tools
        """

        def tool_exists(tool_name: str) -> bool:
            return any(tool_name == tool.name for toolkit in assistant.toolkits for tool in toolkit.tools)

        code_fields = cls._get_code_fields(assistant, context)
        repo_tree_with_filtering = tool_exists(REPO_TREE_TOOL_V2.name)
        code_search_with_filtering = tool_exists(CODE_SEARCH_TOOL_V2.name)

        if tool_exists(CODE_SEARCH_BY_PATHS_TOOL.name):
            tools.append(
                CodeToolkit.search_code_by_path_tool(
                    code_fields=code_fields, top_k=request.top_k, is_react=is_react, user_input=request.text
                )
            )
        if tool_exists(READ_FILES_TOOL.name):
            tools.append(
                CodeToolkit.read_files_tool(
                    code_fields=code_fields,
                    is_react=is_react,
                )
            )
        if tool_exists(READ_FILES_WITH_SUMMARY_TOOL.name):
            tools.append(
                CodeToolkit.read_files_with_summary_tool(
                    code_fields=code_fields,
                    is_react=is_react,
                )
            )

        if repo_tree_with_filtering or not exclude_extra_context_tools:
            tools.append(
                CodeToolkit.get_repo_tree_tool(
                    code_fields=code_fields,
                    is_react=is_react,
                    user_input=request.text,
                    with_filtering=repo_tree_with_filtering,
                )
            )
        if code_search_with_filtering or not exclude_extra_context_tools:
            tools.append(
                CodeToolkit.search_code_tool(
                    code_fields=code_fields,
                    top_k=request.top_k,
                    is_react=is_react,
                    user_input=request.text,
                    with_filtering=code_search_with_filtering,
                )
            )

    @classmethod
    def _add_git_related_tools(
        cls,
        tools: list[BaseTool],
        context: Context,
        assistant: Assistant,
        user_id: str,
        request_uuid: str,
        llm_model: str,
        is_react: bool = True,
    ):
        """Adds Git tools to the list of tools using repo context.

        Args:
            tools: List to append Git tools to
            context: The context configuration
            assistant: The assistant configuration
            user_id: The user ID
            request_uuid: Unique request identifier
            llm_model: The LLM model being used
            is_react: Whether the agent uses ReAct pattern
        """
        if context.context_type != ContextType.CODE:
            return

        code_fields = cls._get_code_fields(assistant, context)
        assistant_toolkits = [toolkit.toolkit for toolkit in assistant.toolkits]

        if ToolSet.GIT in assistant_toolkits:
            tools.extend(
                cls.filter_tools(
                    assistant.toolkits,
                    ToolSet.GIT,
                    ToolkitSettingService.get_git_tools_with_creds(
                        code_fields=code_fields,
                        project_name=assistant.project,
                        user_id=user_id,
                        llm_model=llm_model,
                        assistant_id=assistant.id,
                        request_uuid=request_uuid,
                        is_react=is_react,
                    ),
                )
            )

    @classmethod
    def _find_code_index(cls, project_name: str, repo_name: str):
        """Find code index by project and repo name.

        Args:
            project_name: The project name
            repo_name: The repository name

        Returns:
            CodeIndexInfo or None if not found
        """
        index_search_result = CodeIndexInfo.filter_by_project_and_repo(project_name=project_name, repo_name=repo_name)

        if len(index_search_result) < 1:
            logger.error(f"Code index not found for project {project_name} and repo {repo_name}")
            return None

        return index_search_result[0]

    @classmethod
    def _get_code_fields(cls, assistant: Assistant, context: Context) -> Optional[CodeFields]:
        """Returns CodeFields object based on the assistant and context.

        Args:
            assistant: The assistant configuration
            context: The context configuration

        Returns:
            CodeFields object

        Raises:
            ToolException: If code index is not found
        """
        code_index = cls._find_code_index(project_name=assistant.project, repo_name=context.name)

        if code_index is None:
            created_by_user_log = f" User: {assistant.created_by.model_dump_json()}. " if assistant.created_by else ""
            logger.error(
                f"Unable to get CodeFields because code index "
                f"is None for project '{assistant.project}' and repo '{context.name}'."
                f"{created_by_user_log}"
            )
            raise ToolException(
                f"Repository: {context.name} is not found. "
                "Please verify data source project name, try to re-index, or delete and re-create the data source. \n"
                f"Project '{assistant.project}'."
            )

        return CodeFields(
            app_name=assistant.project, repo_name=context.name, index_type=CodeIndexType(code_index.index_type)
        )

    @classmethod
    def _find_index(cls, klass: Type[FilteredIndexInfo], project_name: str, repo_name: str) -> IndexInfo | None:
        """Finds the index of the given class inherited from IndexInfo by project and repo name.

        Args:
            klass: The IndexInfo subclass to search for
            project_name: The project name
            repo_name: The repository name

        Returns:
            IndexInfo instance or None if not found
        """
        search_result = klass.filter_by_project_and_repo(
            project_name=project_name,
            repo_name=repo_name,
        )

        if len(search_result) < 1:
            logger.error(f"{klass.__name__} datasource index '{repo_name}' not found for project '{project_name}'")
            return None

        return search_result[0]
