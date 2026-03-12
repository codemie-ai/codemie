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

from codemie_tools.base.models import ToolMetadata
from codemie_tools.code.models import SonarConfig

SONAR_TOOL = ToolMetadata(
    name="Sonar",
    description="""
    SonarQube Tool for interacting with the SonarQube REST API
    You must provide the following args: relative_url, params
    1. 'relative_url': Required parameter. The relative URI for SONAR REST API
    URI must start with a forward slash and '/api/issues/search..'
    DO NOT include query parameters in the URL, they must be provided in 'params'
    2. 'params': Optional JSON string of parameters to be sent in query params
    Must be a valid JSON string.
    For search/read operations, include the following fields in your params:
    - "cleanCodeAttributeCategories" (consistent, intentional, adaptable, responsible)
    - "severities" (MINOR, MAJOR, INFO etc)
    - "issueStatuses" (OPEN , ACCEPTED, FIXED etc)
    - "types" (CODE_SMELL, VULNERABILITY, BUG)
    - "ps" (page size, to set maxResults)
    Do not include additional fields if they not requested by the user
    If some information is not provided by the user, attempt to use default values or ask the user for clarification
    """,
    label="Sonar",
    user_description="""
    Provides access to SonarQube API, enabling retrieval of code quality metrics, issues, and analysis data.
    Before using it, it is necessary to add a new integration for the tool by providing:
    1. Alias (A friendly name for the SonarQube integration)
    2. SonarQube Server URL
    3. SonarQube authentication token
    4. Project key of the desired repository
    Usage Note:
    Use this tool when you need to retrieve code quality metrics, issues, or analysis data from SonarQube.
    Example user query: "Show me first 10 open major code smells"
    """.strip(),
    settings_config=True,
    config_class=SonarConfig,
)
