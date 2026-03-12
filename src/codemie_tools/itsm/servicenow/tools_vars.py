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
from .models import ServiceNowConfig

SNOW_TABLE_TOOL = ToolMetadata(
    name="servicenow_table_tool",
    description="""
    ServiceNow Tool for Official ServiceNow Table REST API call, searching, creating, updating table, etc.
    You must provide the following args: relative_url, method, params.
    1. 'method': The HTTP method, e.g. 'GET', 'POST', 'PUT', 'DELETE' etc. Some of them might be turned off in configuration.
    2. 'table': The name of the table to work with
    3. 'sys_id': Optional, used when working with a specific record, rather than entire table. In this case the api in use
    will be /api/now/table/{tableName}/{sys_id}
    4. 'query': Optional set of query parameters to be used if supported by API. f.ex: `sysparm_query`, `sysparm_offset`, `sysparm_limit`, etc. In a form of JSON.
    """,
    label="ServiceNow Table API",
    user_description="""
    Provides access to the ServiceNow Table API.
    """.strip(),
    settings_config=True,
    config_class=ServiceNowConfig,
)
