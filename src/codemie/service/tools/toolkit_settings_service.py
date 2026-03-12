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

from typing import Any, List, Optional

from codemie_tools.base.file_object import FileObject
from codemie_tools.base.models import ToolKit, ToolSet
from codemie_tools.data_management.file_system.toolkit import FileSystemToolkit
from codemie_tools.git.toolkit import GitToolkit

from codemie.agents.tools.code.code_toolkit import CodeToolkit
from codemie.configs import config, logger
from codemie.core.dependecies import get_indexed_repo, get_llm_by_credentials
from codemie.core.models import CodeFields, CodeRepoType, GitRepo, ToolConfig
from codemie.repository.repository_factory import FileRepositoryFactory
from codemie.rest_api.models.assistant import Assistant, ContextType
from codemie.rest_api.models.index import CodeIndexInfo
from codemie.rest_api.security.user import User


class ToolkitSettingService:
    @classmethod
    def get_git_tools_with_creds(
        cls,
        code_fields: CodeFields,
        project_name: str,
        user_id: str,
        llm_model: Any,
        request_uuid: str,
        assistant_id: Optional[str] = None,
        is_react: bool = True,
        tools_config: Optional[List[ToolConfig]] = None,
    ):
        from codemie.service.settings.settings import SettingsService

        indexed_repo: GitRepo = get_indexed_repo(code_fields)
        creds = SettingsService.get_git_creds(
            project_name=project_name,
            user_id=user_id,
            repo_link=indexed_repo.link,
            assistant_id=assistant_id,
            tool_config=cls._find_tool_config_by_name(tools_config, ToolSet.GIT.name),
        )

        azure_devops_organization_url = None
        azure_devops_project = None
        if indexed_repo.get_type() == CodeRepoType.AZURE_DEVOPS_REPOS:
            azure_devops_creds = SettingsService.get_azure_devops_creds(
                project_name=project_name,
                user_id=user_id,
                assistant_id=assistant_id,
                creds=creds,
                indexed_repo=indexed_repo,
                tool_config=cls._find_tool_config_by_name(tools_config, "AzureDevOps"),
            )
            azure_devops_organization_url = f"{azure_devops_creds.base_url}/{azure_devops_creds.organization}"
            azure_devops_project = azure_devops_creds.project

        chat_model = get_llm_by_credentials(llm_model=llm_model, request_id=request_uuid)
        configs = {
            "repo_type": indexed_repo.get_type(),
            "base_branch": indexed_repo.branch,
            "repo_link": indexed_repo.link,
            "organization_url": azure_devops_organization_url,
            "project": azure_devops_project,
        }

        # Add authentication fields from credentials (using GIT_FIELDS mapping)
        for field in SettingsService.GIT_FIELDS.values():
            value = getattr(creds, field, None)
            if value is not None:
                configs[field] = value

        toolkit = GitToolkit.get_toolkit(configs=configs, llm_model=chat_model)
        tools = toolkit.get_tools()

        for tool in tools:
            tool.name = CodeToolkit._tool_name(tool, code_fields)
            tool.description = CodeToolkit._tool_description(tool, code_fields, is_react)

        return tools

    @classmethod
    def get_file_system_toolkit(
        cls,
        assistant: Assistant,
        project_name: str,
        user: User,
        llm_model: Any,
        request_uuid: str,
        tools_config: Optional[List[ToolConfig]] = None,
        input_files: Optional[List[FileObject]] = None,
    ):
        from codemie.service.settings.settings import SettingsService

        configs = {
            "user_id": user.id,
        }

        # Only add DALL-E config if it's properly configured
        if config.DALLE_API_URL and config.DALLE_API_KEY:
            configs["azure_dalle_config"] = {
                "api_version": config.OPENAI_API_VERSION,
                "azure_endpoint": config.DALLE_API_URL,
                "api_key": config.DALLE_API_KEY,
            }
        if assistant.context:
            for context in assistant.context:
                if context.context_type == ContextType.CODE:
                    code_index = ToolkitSettingService._find_code_index(assistant.project, context.name)
                    code_fields = CodeFields(
                        app_name=assistant.project, repo_name=context.name, index_type=code_index.index_type
                    )
                    configs["code_fields"] = code_fields.model_dump()

        file_config = SettingsService.get_file_system_config(
            project_name=project_name, user_id=user.id, assistant_id=assistant.id
        )
        if file_config:
            configs["root_directory"] = file_config.root_directory
            configs["activate_command"] = file_config.activate_command
        chat_model = get_llm_by_credentials(llm_model=llm_model, request_id=request_uuid)
        return FileSystemToolkit.get_toolkit(
            configs=configs,
            file_repository=FileRepositoryFactory.get_current_repository(),
            chat_model=chat_model,
            input_files=input_files,
        ).get_tools()

    @staticmethod
    def _has_assistant_toolkit_tool_by_name(assistant_toolkits: List[ToolKit], tool_name: str):
        for toolkit in assistant_toolkits:
            for tool in toolkit.tools:
                if tool.name == tool_name:
                    return True
        return False

    @classmethod
    def _find_tool_config_by_name(cls, tools_config: List[ToolConfig], name: str) -> Optional[ToolConfig]:
        """
        Find a specific tool configuration by name from a list of tool configurations.

        Args:
            tools_config: List of tool configurations
            name: Name of the tool to find

        Returns:
            The found tool configuration or None if not found
        """

        if tools_config:
            return next((tc for tc in tools_config if tc.name == name), None)
        return None

    @classmethod
    def _find_code_index(cls, project_name: str, repo_name: str):
        index_search_result = CodeIndexInfo.filter_by_project_and_repo(project_name=project_name, repo_name=repo_name)

        if len(index_search_result) < 1:
            logger.error(f"Code index not found for project {project_name} and repo {repo_name}")
            return None

        return index_search_result[0]
