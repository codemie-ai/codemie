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

from typing import Any, Dict

from codemie_tools.base.models import ToolSet
from codemie_tools.file_analysis.toolkit import FileAnalysisToolkit
from codemie_tools.git.toolkit import GitToolkit, CustomGitHubToolkit, CustomGitLabToolkit, CustomBitbucketToolkit
from codemie_tools.vision.tool_vars import IMAGE_TOOL
from codemie_tools.vision.toolkit import VisionToolkit
from langchain_core.tools import BaseTool
from pydantic import ValidationError

from codemie.agents.tools import BaseToolkit
from codemie.agents.tools.code.code_toolkit import CodeToolkit
from codemie.agents.tools.code.tools_vars import (
    CODE_SEARCH_TOOL_V2,
    REPO_TREE_TOOL_V2,
)
from codemie.agents.tools.kb.search_kb import SearchKBTool

# Plugin toolkit now provided via enterprise package
from codemie.configs import logger
from codemie.core.dependecies import get_llm_by_credentials
from codemie.core.models import CodeFields
from codemie.core.utils import build_unique_file_objects_list
from codemie.rest_api.models.assistant import Assistant, Context, ContextType
from codemie.rest_api.models.index import IndexInfo, KnowledgeBaseIndexInfo
from codemie.rest_api.models.tool import (
    ToolInvokeRequest,
    DatasourceSearchInvokeRequest,
    CodeDatasourceSearchParams,
)
from codemie.rest_api.security.user import User
from codemie.service.assistant import VirtualAssistantService
from codemie.service.assistant_service import AssistantService
from codemie.service.tools.discovery import ToolDiscoveryService, ToolInfo
from codemie.service.tools.tool_service import ToolsService
from codemie.service.tools.toolkit_service import ToolkitService
from codemie.service.tools.toolkit_settings_service import ToolkitSettingService

INVALID_SIGNATURE_ERROR = "Tool method arguments are not correct. Expected: {args_desc}"
INVALID_ATTRIBUTES_ERROR = "Tool attributes are not correct. Expected: {args_desc}"


class ToolExecutionService:
    @classmethod
    def validate_tool_args(cls, tool: BaseTool, tool_args: Dict[str, Any]) -> BaseTool:
        default_tool_input = {key: None for key in tool.args_schema.__annotations__ if key not in tool_args}
        try:
            tool.args_schema(**tool_args, **default_tool_input)
        except (ValidationError, TypeError):
            signature = {k: v.__name__ for k, v in tool.args_schema.__annotations__.items()}
            raise ValueError(INVALID_SIGNATURE_ERROR.format(args_desc=signature))
        return tool

    @classmethod
    def validate_tool_attributes(cls, tool: BaseTool, tool_attributes: Dict[str, Any]) -> BaseTool:
        try:
            current_state = tool.__dict__
            updated_state = {**current_state, **tool_attributes}
            return tool.model_validate(updated_state)
        except (ValidationError, TypeError) as e:
            signature = {k: v.__name__ for k, v in tool.__annotations__.items()}
            logger.error(f"Validation error: {e}", exc_info=True)
            raise ValueError(INVALID_ATTRIBUTES_ERROR.format(args_desc=signature))

    @classmethod
    def update_tool_attributes(cls, tool: BaseTool, tool_attributes: Dict[str, Any]) -> BaseTool:
        valid_tool_attributes = dict(filter(lambda item: item[0] in tool.__annotations__, tool_attributes.items()))

        if not valid_tool_attributes:
            return tool

        cls.validate_tool_attributes(tool, valid_tool_attributes)
        for key, value in valid_tool_attributes.items():
            setattr(tool, key, value)
        logger.debug(
            f"Updated tool attributes: {
                [(key, value) for key, value in tool.model_dump().items() if key in valid_tool_attributes]
            }"
        )
        return tool

    @classmethod
    def invoke(cls, request: ToolInvokeRequest, tool_name: str, user: User):
        # Route file-dependent tools to a dedicated method
        if cls._is_file_dependent_tool(tool_name):
            return cls.invoke_file_analysis_tool(request, tool_name)

        if request.tool_creds and "integration_alias" not in request.tool_creds:
            return cls.invoke_tool_with_direct_creds(request, tool_name)

        return cls.invoke_tool_with_system_integration(request, tool_name, user)

    @classmethod
    def invoke_datasource_search(cls, datasource: IndexInfo, request: DatasourceSearchInvokeRequest):
        """
        Invoke a search operation on a specific datasource.
        """
        search_tool = cls.get_search_tool(datasource, request)
        search_tool.metadata = {'llm_model': request.llm_model}
        return search_tool.execute(query=request.query)

    @classmethod
    def invoke_tool_with_direct_creds(cls, request: ToolInvokeRequest, tool_name: str):
        tool = cls.get_tool_with_direct_creds(request, tool_name)
        try:
            cls.validate_tool_args(tool, request.tool_args)
            logger.info(f"Tool `{tool_name}` executed with args: {request.tool_args}")
            if request.tool_attributes:
                tool = cls.update_tool_attributes(tool, request.tool_attributes)
            return tool.execute(**request.tool_args)
        except Exception as e:
            logger.error(f"Error occurred on tool invocation: {str(e)}", exc_info=True)
            raise e

    @classmethod
    def get_tool_with_direct_creds(cls, request: ToolInvokeRequest, tool_name: str) -> BaseTool:
        """
        Get tool instance using provided credentials.
        """
        try:
            if tool_name.startswith("_"):
                return cls._get_plugin_tools(tool_name, request.tool_creds)

            tool_info = ToolDiscoveryService.find_tool_by_name(tool_name)
            if not tool_info:
                raise ValueError(f"Tool not found: {tool_name}")

            cls._validate_credentials(tool_info, request.tool_creds)

            toolkit = cls._create_toolkit_instance(tool_info, request)
            tools = toolkit.get_tools()
            return ToolsService.find_tool(tool_name, tools)

        except Exception as e:
            logger.error(f"Error getting tool with direct credentials: {str(e)}", exc_info=True)
            raise e

    @classmethod
    def _validate_credentials(cls, tool_info: ToolInfo, creds: Dict[str, Any]) -> None:
        """
        Validate credentials against the schema from tool_info.
        """
        if not creds:
            raise ValueError("Tool credentials are required")

        config_schema = tool_info.config_schema

        missing_required = [
            field_name
            for field_name, field_info in config_schema.items()
            if field_info.get('required', False) and field_name not in creds
        ]

        if missing_required:
            raise ValueError(f"Missing required credential fields: {', '.join(missing_required)}")

    @classmethod
    def _create_toolkit_instance(cls, tool_info: ToolInfo, request: ToolInvokeRequest) -> BaseToolkit:
        """
        Create toolkit instance based on tool info and request credentials.
        """
        creds = request.tool_creds

        if issubclass(tool_info.toolkit_class, (CustomGitHubToolkit, CustomGitLabToolkit, CustomBitbucketToolkit)):
            llm = get_llm_by_credentials(llm_model=request.llm_model)
            git_toolkit = GitToolkit.get_toolkit(creds, llm)
            return GitToolkit(git_toolkit=git_toolkit)

        return ToolDiscoveryService.create_toolkit_instance(tool_info, creds)

    @classmethod
    def get_tool_with_system_integration(
        cls, request: ToolInvokeRequest, tool_name: str, assistant: Assistant, user: User
    ):
        toolkits = ToolkitService.get_toolkit_methods()

        tool = cls._get_context_tools(assistant, request, tool_name, user)
        if tool:
            tool.metadata = {'llm_model': request.llm_model}
        else:
            tool = ToolsService.find_tool_by_invoke_request(tool_name, toolkits, assistant, user, request.project)
        return tool

    @classmethod
    def invoke_tool_with_system_integration(cls, request: ToolInvokeRequest, tool_name: str, user: User):
        assistant = None
        try:
            assistant = VirtualAssistantService.create_from_tool_invocation(
                tool_name=tool_name,
                user=user,
                project_name=request.project,
                integration_alias=request.tool_creds.get("integration_alias") if request.tool_creds else None,
                datasource_id=request.datasource_id,
            )
            if not assistant:
                raise ValueError(f"Failed to create virtual assistant for tool `{tool_name}` execution")

            tool = cls.get_tool_with_system_integration(request, tool_name, assistant, user)
            cls.validate_tool_args(tool, request.tool_args)
            logger.info(f"Tool `{tool_name}` executed with args: {request.tool_args}")
            if request.tool_attributes:
                tool = cls.update_tool_attributes(tool, request.tool_attributes)
            return tool.execute(**request.tool_args)
        except Exception as e:
            logger.error(f"Error occurred on tool invocation: {str(e)}", exc_info=True)
            raise e
        finally:
            if assistant:
                VirtualAssistantService.delete(assistant.id)

    @classmethod
    def get_search_tool(cls, datasource: IndexInfo, request: DatasourceSearchInvokeRequest):
        if not datasource.is_code_index():
            return SearchKBTool(
                kb_index=datasource,
                llm_model=request.llm_model,
            )
        if not request.code_search_params:
            request.code_search_params = CodeDatasourceSearchParams()
        user_input = request.code_search_params.user_input or request.query
        return CodeToolkit.search_code_tool(
            code_fields=CodeFields(
                app_name=datasource.project_name, repo_name=datasource.repo_name, index_type=datasource.index_type
            ),
            user_input=user_input,
            top_k=request.code_search_params.top_k,
            with_filtering=request.code_search_params.with_filtering,
        )

    @classmethod
    def _get_context_tools(cls, assistant: Assistant, request: ToolInvokeRequest, tool_name: str, user: User):
        toolkit = ToolsService.find_toolkit_for_tool(user, tool_name)
        toolkit_name = toolkit.get("toolkit")
        if toolkit_name == ToolSet.VCS:
            return cls._get_vcs_tools(assistant, request, tool_name, user)

        if not assistant.context:
            return

        context = next(iter(assistant.context))
        if context.context_type == ContextType.KNOWLEDGE_BASE:
            return cls._get_kb_tools(context, request)

        if context.context_type == ContextType.CODE:
            code_fields = AssistantService._get_code_fields(assistant, context)
            if toolkit_name == ToolSet.CODEBASE_TOOLS:
                return cls._get_code_tools(code_fields, request, tool_name)
            if toolkit_name == ToolSet.GIT:
                return cls._get_git_tools(code_fields, assistant, request, tool_name, user)
            if toolkit_name == ToolSet.KB_TOOLS:
                raise ValueError(f"Invalid tool: {tool_name} for given datasource type: {context.context_type}")

    @classmethod
    def _get_plugin_tools(cls, tool_name: str, creds: dict) -> BaseTool:
        """Get plugin tool by name using direct credentials.

        This method delegates to enterprise plugin implementation if available.
        It requires user_id, project_name, and assistant_id in the creds dictionary.

        Args:
            tool_name: Name of the plugin tool to retrieve (starts with '_')
            creds: Dictionary containing plugin credentials and context:
                   - plugin_key: The plugin authentication key
                   - user_id: User ID (required for enterprise)
                   - project_name: Project name (required for enterprise)
                   - assistant_id: Assistant ID (required for enterprise)

        Returns:
            BaseTool instance if found

        Raises:
            ValueError: If required credentials are missing or tool not found
        """
        from codemie.enterprise.plugin import is_plugin_enabled, get_plugin_tools_for_assistant

        # Validate credentials dictionary
        if not creds:
            raise ValueError("Plugin credentials are required")

        # Try enterprise implementation first
        if is_plugin_enabled():
            try:
                # Extract required parameters from creds
                user_id = creds.get('user_id')
                project_name = creds.get('project_name')
                assistant_id = creds.get('assistant_id')

                if not all([user_id, project_name, assistant_id]):
                    logger.warning(
                        f"Missing required fields in plugin creds. "
                        f"Required: user_id, project_name, assistant_id. "
                        f"Got: {', '.join(k for k in ['user_id', 'project_name', 'assistant_id'] if k in creds)}"
                    )
                    raise ValueError("Missing user_id, project_name, or assistant_id in plugin credentials")

                logger.info(f"Using enterprise plugin tools for tool {tool_name}")
                tools = get_plugin_tools_for_assistant(
                    user_id=user_id,
                    project_name=project_name,
                    assistant_id=assistant_id,
                )

                # Find the specific tool by name
                if tool := ToolsService.find_tool(tool_name, tools):
                    logger.info(f"Retrieved plugin tool {tool_name} from enterprise")
                    return tool

                logger.warning(f"Plugin tool {tool_name} not found in enterprise tools")
                raise ValueError(f"Plugin tool not found: {tool_name}")

            except Exception as e:
                logger.error(f"Error getting plugin tool from enterprise: {e}", exc_info=True)
                raise

        raise RuntimeError("Enterprise plugin system is not available or enabled.")

    @classmethod
    def _get_kb_tools(
        cls,
        context: Context,
        request: ToolInvokeRequest,
    ):
        """Returns tool to work with datasource"""
        kb_search_result = KnowledgeBaseIndexInfo.filter_by_project_and_repo(
            project_name=request.project, repo_name=context.name
        )

        if len(kb_search_result) < 1:
            message = f"Knowledge Base index not found for project '{request.project}' and repo '{context.name}'"
            logger.error(message)
            raise ValueError(message)

        kb_index = next(iter(kb_search_result))
        return SearchKBTool(
            kb_index=kb_index,
            llm_model=request.llm_model,
        )

    @classmethod
    def map_params_to_method_signature(
        cls, method_params: Dict[str, Any], input_params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Maps input parameters to a method's signature parameters.
        """
        mapped_params = method_params.copy()
        for param_name in mapped_params:
            if param_name in input_params and input_params[param_name] is not None:
                mapped_params[param_name] = input_params[param_name]

        return mapped_params

    @classmethod
    def _get_code_tools(
        cls,
        code_fields: CodeFields,
        request: ToolInvokeRequest,
        tool_name: str,
    ):
        """Returns tool to work with code datasource"""
        tool_config = {
            "code_fields": code_fields,
            "is_react": request.tool_attributes.get("is_react", True),
            "user_input": request.tool_attributes.get("user_input", "") or request.tool_args.get("query", ""),
            "top_k": request.tool_attributes.get("top_k", 10),
        }

        if tool_name in (CODE_SEARCH_TOOL_V2.name, REPO_TREE_TOOL_V2.name):
            tool_config["with_filtering"] = True

        result = ToolDiscoveryService.get_toolkit_method_for_tool(tool_name, CodeToolkit)
        if not result:
            return None

        method, params = result
        mapped_params = cls.map_params_to_method_signature(params, tool_config)
        return method(**mapped_params)

    @classmethod
    def _get_git_tools(
        cls,
        code_fields: CodeFields,
        assistant: Assistant,
        request: ToolInvokeRequest,
        tool_name: str,
        user: User,
    ):
        tools = ToolkitSettingService.get_git_tools_with_creds(
            code_fields=code_fields,
            project_name=request.project,
            user_id=user.id,
            llm_model=request.llm_model,
            request_uuid="",
            assistant_id=assistant.id,
            is_react=assistant.is_react,
        )
        return ToolsService.find_tool(tool_name, tools)

    @classmethod
    def _get_vcs_tools(
        cls,
        assistant: Assistant,
        request: ToolInvokeRequest,
        tool_name: str,
        user: User,
    ):
        tools = ToolkitService.get_core_tools(
            assistant_toolkits=assistant.toolkits,
            project_name=request.project,
            user_id=user.id,
            assistant_id=assistant.id,
        )
        return ToolsService.find_tool(tool_name, tools)

    @classmethod
    def invoke_file_analysis_tool(cls, request: ToolInvokeRequest, tool_name: str):
        """
        Invoke file analysis or vision tools that require file objects.
        This method handles tools that depend on uploaded files:
        - FileAnalysisToolkit tools (pdf_tool, docx_tool, pptx_tool, excel_tool, csv_tool, file_analysis)
        - VisionToolkit tools (image_tool)

        Args:
            request: The tool invocation request
            tool_name: Name of the tool to invoke
        Returns:
            Result of tool execution
        """
        try:
            file_urls = request.tool_args.pop("file_names", [])
            if not file_urls:
                error_msg = """
                    "Tool requires uploaded file. Supported formats: PPTX, DOCX, XLSX, PDF, CSV, JPEG, PNG,
                    JPG, GIF, HTML, ZIP archives. Other files types will be treated as plain text files.
                """
                raise ValueError(error_msg)

            file_objects = build_unique_file_objects_list(file_urls)
            llm = get_llm_by_credentials(llm_model=request.llm_model)
            if tool_name == IMAGE_TOOL.name:
                toolkit = VisionToolkit.get_toolkit(files=file_objects, chat_model=llm)
            else:
                toolkit = FileAnalysisToolkit.get_toolkit(files=file_objects, chat_model=llm)

            tools = toolkit.get_tools()
            tool = ToolsService.find_tool(tool_name, tools)

            cls.validate_tool_args(tool, request.tool_args)
            logger.debug(f"Tool `{tool_name}` executed with args: {request.tool_args}")
            if request.tool_attributes:
                tool = cls.update_tool_attributes(tool, request.tool_attributes)
            return tool.execute(**request.tool_args)
        except Exception as e:
            logger.error(f"Error occurred on tool invocation: {str(e)}", exc_info=True)
            raise e

    @classmethod
    def _is_file_dependent_tool(cls, tool_name: str) -> bool:
        """
        Check if a tool belongs to FileAnalysisToolkit or VisionToolkit.
        """
        tool_info = ToolDiscoveryService.find_tool_by_name(tool_name)
        if not tool_info:
            return False

        return tool_info.toolkit_class in (FileAnalysisToolkit, VisionToolkit)
