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

from codemie.datasource.platform.base_platform_processor import BasePlatformDatasourceProcessor
from codemie.datasource.loader.platform.assistant_loader import AssistantLoader
from codemie.service.constants import FullDatasourceTypes


class AssistantDatasourceProcessor(BasePlatformDatasourceProcessor):
    """
    Processor for indexing assistants into marketplace datasource.

    This processor:
    - Uses AssistantLoader to fetch and sanitize published assistants
    - Indexes them into Elasticsearch for marketplace search
    - Supports full sync (all assistants) and incremental updates (single assistant)
    """

    INDEX_TYPE = FullDatasourceTypes.PLATFORM_ASSISTANT.value

    def _init_loader(self):
        """Initialize AssistantLoader."""
        return AssistantLoader()
