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

from codemie.configs import llm_config
from codemie.service.llm_service.llm_service import LLMService

DEMO_PROJECT = "demo"
APP_DESCRIPTION = "Smart AI assistant 'CodeMie'"

# Attribute names for metrics and logging
REQUEST_ID = "request_id"
SESSION_ID = "session_id"
CLIENT_TYPE = "client_type"
USER_AGENT = "user_agent"
USER_ID = "user_id"
USER_NAME = "user_name"
USER_EMAIL = "user_email"
TOOL_TYPE = "tool_type"
LLM_MODEL = "llm_model"
AGENT_NAME = "agent_name"
ASSISTANT_ID = "assistant_id"
PROJECT = "project"
OUTPUT_FORMAT = "output_format"
CODEMIE_CLI = "codemie_cli"
BRANCH = "branch"
REPOSITORY = "repository"

# HTTP Header names
HEADER_CODEMIE_CLI = "X-CodeMie-CLI"
HEADER_CODEMIE_CLIENT = "X-CodeMie-Client"
HEADER_CODEMIE_SESSION_ID = "X-CodeMie-Session-ID"
HEADER_CODEMIE_REQUEST_ID = "X-CodeMie-Request-ID"
HEADER_CODEMIE_CLI_MODEL = "X-CodeMie-CLI-Model"
HEADER_CODEMIE_INTEGRATION = "X-CodeMie-Integration"
HEADER_CODEMIE_CLI_BRANCH = "X-CodeMie-Branch"
HEADER_CODEMIE_CLI_REPOSITORY = "X-CodeMie-Repository"
HEADER_CODEMIE_CLI_PROJECT = "X-CodeMie-Project"

DEFAULT_MAX_OUTPUT_TOKENS_4K = 4096
DEFAULT_MAX_OUTPUT_TOKENS_8K = 8192

# Sensitive value masking
SENSITIVE_VALUE_MASK = "********"


METADATA_TITLE = "metadata.title"
METADATA_CHUNK_NUM = "metadata.chunk_num"
METADATA_FILE_NAME = "metadata.file_name"
METADATA_FILE_PATH = "metadata.file_path"
METADATA_SOURCE = "metadata.source"

# MCP image storage
MCP_IMAGES_SUBDIR = "mcp_images"

# Supervisor handoff tool prefix for LangGraph agents
SUPERVISOR_HANDOFF_TOOL_PREFIX = "transfer_to"


ModelTypes = LLMService(llm_config).create_model_types_enum()


class CodeIndexType(str, Enum):
    CODE = "code"
    SUMMARY = "summary"
    CHUNK_SUMMARY = "chunk-summary"


class DatasourceTypes(str, Enum):
    GIT = "git"
    CONFLUENCE = "confluence"
    JIRA = "jira"
    FILE = "file"
    JSON = "json"
    GOOGLE = "google"
    AZURE_DEVOPS_WIKI = "azure_devops_wiki"
    AZURE_DEVOPS_WORK_ITEM = "azure_devops_work_item"
    XRAY = "xray"


class ProviderIndexType(str, Enum):
    PROVIDER = "provider"


class BackgroundTaskStatus(str, Enum):
    STARTED = "STARTED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ChatRole(str, Enum):
    ASSISTANT = "Assistant"
    USER = "User"


class Environment(Enum):
    DEV = "dev"
    LOCAL = "local"
    PROD = "prod"


class IdentityProvider:
    LOCAL = "local"


class ToolType(str, Enum):
    INTERNAL = "internal"
    PLUGIN = "plugin"
    MCP = "mcp"


class ToolNamePrefix(str, Enum):
    AGENT = "_asst_tool"


class UniqueThoughtParentIds(str, Enum):
    LATEST = "latest"


class MermaidMimeType(str, Enum):
    PNG = "image/png"
    SVG = "image/svg+xml"


class MermaidContentType(str, Enum):
    PNG = "png"
    SVG = "svg"


class MermaidResponseType(str, Enum):
    FILE = "file"
    RAW = "raw"


class LLMProxyMode(str, Enum):
    lite_llm = "lite_llm"
    internal = "internal"
