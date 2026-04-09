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

from uuid import uuid4
from typing import List, Optional

from codemie.rest_api.models.assistant import (
    VirtualAssistant,
    ToolKitDetails,
    Context,
    ContextType,
    MCPServerDetails,
)

from codemie.rest_api.models.index import IndexInfo
from codemie.core.workflow_models import WorkflowAssistant, WorkflowTool
from codemie.rest_api.security.user import User
from codemie.service.tools.tool_service import ToolsService


VIRTUAL_ASSISTANT_PREFIX = "Virtual"
DATASOURCE_NOT_FOUND_ERROR = "Datasource with ID {id} not found"


class VirtualAssistantService:
    """Stores virtual assistants in memory"""

    assistants: dict[str, VirtualAssistant] = {}

    @classmethod
    def create_from_virtual_asst_config(
        cls,
        config: WorkflowAssistant,
        user: User,
        project_name: str,
        execution_id: str,
    ):
        """Create from WorkflowAssistant config"""
        return cls.create(
            name=config.name,
            toolkits=ToolsService.get_toolkits_from_assistant_tool_config(config, user, project_name),
            project=project_name,
            execution_id=execution_id,
            datasource_ids=config.datasource_ids,
            mcp_servers=config.mcp_servers,
            skill_ids=config.skill_ids,
        )

    @classmethod
    def create_from_tool_config(cls, tool_config: WorkflowTool, user: User, project_name: str, execution_id: str):
        toolkit_details = ToolsService.get_toolkit_from_workflow_tool_config(tool_config, user, project_name)

        return VirtualAssistantService.create(
            toolkits=[toolkit_details], project=project_name, execution_id=execution_id
        )

    @classmethod
    def create_from_tool_invocation(
        cls,
        tool_name: str,
        user: User,
        project_name: str,
        integration_alias: Optional[str] = None,
        datasource_id: Optional[str] = None,
    ):
        toolkit_details = ToolsService.get_toolkit(tool_name, user, project_name, integration_alias)
        datasource_ids = [datasource_id] if datasource_id else None

        return VirtualAssistantService.create(
            toolkits=[toolkit_details], project=project_name, execution_id=str(uuid4()), datasource_ids=datasource_ids
        )

    @classmethod
    def create(
        cls,
        toolkits: List[ToolKitDetails],
        project: str,
        name: Optional[str] = None,
        execution_id: Optional[str] = None,
        datasource_ids: Optional[List[str]] = None,
        mcp_servers: Optional[List[MCPServerDetails]] = None,
        skill_ids: Optional[List[str]] = None,
    ) -> VirtualAssistant:
        """Create assistant instance and save in memory"""
        uuid = uuid4()
        context = []

        if datasource_ids:
            for datasource_id in datasource_ids:
                datasource = IndexInfo.find_by_id(id_=datasource_id)

                if not datasource:
                    raise ValueError(DATASOURCE_NOT_FOUND_ERROR.format(id=datasource_id))

                context_type = ContextType.CODE if datasource.is_code_index() else ContextType.KNOWLEDGE_BASE
                context.append(Context(name=datasource.repo_name, context_type=context_type))

        assistant = VirtualAssistant(
            id=f"{VIRTUAL_ASSISTANT_PREFIX}_{uuid}",
            name=name if name else f"Virtual Assistant {uuid}",
            description="",
            system_prompt="",
            project=project,
            toolkits=toolkits,
            execution_id=execution_id,
            context=context,
            mcp_servers=mcp_servers or [],
            skill_ids=skill_ids or [],
        )

        # Set version to 1 for virtual assistants (they don't have versioning)
        assistant.version = 1

        cls.assistants[assistant.id] = assistant
        return assistant

    @classmethod
    def get(cls, assistant_id: str) -> VirtualAssistant:
        return cls.assistants[assistant_id]

    @classmethod
    def delete(cls, assistant_id: str) -> None:
        """Deletes from memory"""
        cls.assistants.pop(assistant_id, None)

    @classmethod
    def delete_by_execution_id(cls, execution_id: str) -> None:
        """Delete assistants by execution_id"""
        keys = set(cls.assistants.keys())

        for key in keys:
            if cls.assistants[key].execution_id == execution_id:
                del cls.assistants[key]
