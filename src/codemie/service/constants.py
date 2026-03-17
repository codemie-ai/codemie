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

from codemie.core.constants import ProviderIndexType


class FullDatasourceTypes(str, Enum):
    GIT = "code"
    CONFLUENCE = "knowledge_base_confluence"
    JIRA = "knowledge_base_jira"
    FILE = "knowledge_base_file"
    GOOGLE = "llm_routing_google"
    AZURE_DEVOPS_WIKI = "knowledge_base_azure_devops_wiki"
    AZURE_DEVOPS_WORK_ITEM = "knowledge_base_azure_devops_work_item"
    PROVIDER = ProviderIndexType.PROVIDER.value

    # Platform/marketplace system datasources (not visible in GET /index)
    PLATFORM_ASSISTANT = "platform_marketplace_assistant"


# System datasource types that should be hidden from regular datasource lists
SYSTEM_DATASOURCE_TYPES = {
    FullDatasourceTypes.PLATFORM_ASSISTANT.value,
}

# Pagination limits
DEFAULT_PAGE = 0  # 0-based pagination
MAX_CONVERSATIONS_PER_PAGE = 200
MAX_HISTORY_ITEMS_PER_PAGE = 500
DEFAULT_CONVERSATIONS_PER_PAGE = 20
DEFAULT_HISTORY_ITEMS_PER_PAGE = 20
