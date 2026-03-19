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

from enum import Enum
from typing import List, Optional, Any

from pydantic import BaseModel, model_validator, Field


def RequiredField(default="", **kwargs):
    """
    Field that must be provided at runtime (lazy validation).

    This helper marks a field as required for tool execution, even though
    it has a default value to allow initialization with empty config.

    Args:
        default: Default value (typically "" for lazy validation pattern)
        **kwargs: Additional Field parameters

    Returns:
        Field: Pydantic Field with required_at_runtime metadata

    Example:
        url: Optional[str] = RequiredField(
            description="Service URL",
            json_schema_extra={"placeholder": "https://example.com"}
        )
    """
    json_schema_extra = kwargs.get('json_schema_extra', {})
    json_schema_extra['required_at_runtime'] = True
    kwargs['json_schema_extra'] = json_schema_extra
    return Field(default=default, **kwargs)


class ToolMetadata(BaseModel):
    name: str
    description: Optional[str] = ''
    label: Optional[str] = ''
    react_description: Optional[str] = ''
    user_description: Optional[str] = ''
    settings_config: Optional[bool] = False
    config_class: Optional[Any] = Field(default=None, exclude=True)

    @model_validator(mode='after')
    def validate_settings_config(self) -> 'ToolMetadata':
        if self.settings_config and self.config_class is None:
            raise ValueError("config_class must be provided when settings_config is True")
        return self


class CredentialTypes(str, Enum):
    """
    Credential types for tool configurations.
    This enum defines all supported credential/authentication types for tools.
    """

    # User settings
    JIRA = "Jira"
    CONFLUENCE = "Confluence"
    GIT = "Git"
    KUBERNETES = "Kubernetes"
    AWS = "AWS"
    GCP = "GCP"
    KEYCLOAK = "Keycloak"
    AZURE = "Azure"
    ELASTIC = "Elastic"
    OPEN_API = "OpenAPI"
    PLUGIN = "Plugin"
    FILE_SYSTEM = "FileSystem"
    SCHEDULER = "Scheduler"
    WEBHOOK = "Webhook"
    EMAIL = "Email"
    AZURE_DEVOPS = "AzureDevOps"
    SONAR = "Sonar"
    SQL = "SQL"
    TELEGRAM = "Telegram"
    ZEPHYR_SCALE = "ZephyrScale"
    _ZEPHYR_CLOUD = "ZephyrCloud"  # Deprecated
    ZEPHYR_SQUAD = "ZephyrSquad"
    XRAY = "Xray"
    SERVICENOW = "ServiceNow"
    REPORT_PORTAL = "ReportPortal"
    ENVIRONMENT_VARS = "MCP"
    AUTH_TOKEN = "AuthToken"
    A2A = "A2A"  # Assistant-to-Assistant integration
    LITE_LLM = "LiteLLM"
    SHAREPOINT = "SharePoint"

    # Project settings
    DIAL = "DIAL"


class ToolSet(str, Enum):
    GIT = "Git"
    VCS = "VCS"
    CODEBASE_TOOLS = "Codebase Tools"
    KB_TOOLS = "Knowledge Base"
    CODE_PLAN = "Code plan"
    GENERAL = "General"
    RESEARCH = "Research"
    CLOUD = "Cloud"
    PLUGIN = "Plugin"
    ADMIN = "CodeMie admin"
    ACCESS_MANAGEMENT = "Access Management"
    PROJECT_MANAGEMENT = "Project Management"
    OPEN_API = "OpenAPI"
    DATA_MANAGEMENT = "Data Management"
    VISION = "Vision"
    FILE_SYSTEM = "FileSystem"
    PANDAS = "Pandas"
    FILE_ANALYSIS = "File Analysis"
    NOTIFICATION = "Notification"
    CODE_QUALITY = "Code Quality"
    OPEN_API_LABEL = "Open API"
    FILE_SYSTEM_LABEL = "File System"
    FILE_MANAGEMENT_LABEL = "File Management"
    QUALITY_ASSURANCE = "Quality Assurance"
    AZURE_DEVOPS_WIKI = "Azure DevOps Wiki"
    AZURE_DEVOPS_WORK_ITEM = "Azure DevOps Work Item"
    AZURE_DEVOPS_TEST_PLAN = "Azure DevOps Test Plan"
    ITSM = "IT Service Management"
    REPORT_PORTAL = "Report Portal"
    PLATFORM_TOOLS = "Platform Tools"


class Tool(BaseModel):
    name: str
    label: Optional[str] = None
    settings_config: Optional[bool] = False
    description: Optional[str] = None
    user_description: Optional[str] = None
    config_class: Optional[Any] = Field(default=None, exclude=True)
    tool_class: Optional[Any] = Field(default=None, exclude=True)

    @model_validator(mode="before")
    def set_label(cls, values):
        name = values.get('name', '')
        label = values.get('label', '')
        if not label:
            values['label'] = ' '.join(word.capitalize() for word in name.split('_'))
        else:
            values['label'] = label
        return values

    @classmethod
    def from_metadata(cls, metadata: 'ToolMetadata', **kwargs):
        config_class = _clazz if (_clazz := kwargs.get('config_class')) else metadata.config_class
        settings_config = (
            _settings_config if (_settings_config := kwargs.get('settings_config')) else metadata.settings_config
        )
        tool_class = kwargs.get('tool_class')
        return cls(
            name=metadata.name,
            label=metadata.label or None,
            description=metadata.description or None,
            user_description=metadata.user_description or None,
            config_class=config_class,
            settings_config=settings_config,
            tool_class=tool_class,
        )


class ToolKit(BaseModel):
    toolkit: str
    tools: List[Tool]
    label: Optional[str] = ""
    settings_config: Optional[bool] = False
    is_external: bool = False
    config_class: Optional[Any] = None


class ToolOutputFormat(str, Enum):
    TEXT = "text"
    MARKDOWN = "markdown"


class FileConfigMixin(BaseModel):
    """Mixin for tool configs that support file operations."""

    input_files: Optional[List[Any]] = Field(default=None, exclude=True)


class CodeMieToolConfig(BaseModel):
    """
    Base Pydantic class for CodeMie tool configuration.

    All tool configs should inherit from this class and define their credential_type.
    The credential_type field should be overridden in subclasses with frozen=True to prevent modification.
    """

    credential_type: Optional[CredentialTypes] = Field(
        default=None, description="Credential type for this configuration", exclude=True, frozen=True
    )
