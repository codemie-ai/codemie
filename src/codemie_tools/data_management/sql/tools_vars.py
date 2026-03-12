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
from codemie_tools.data_management.sql.models import SQLConfig

SQL_TOOL = ToolMetadata(
    name="sql",
    description="""
    Converts natural language to SQL queries and executes them.
    If you do not know exact table name and columns, you must fetch them first.
    """.strip(),
    label="SQL",
    user_description="""
    Enables the AI assistant to execute SQL queries on supported database systems. This tool allows for data retrieval, manipulation, and analysis using SQL commands on MySQL, PostgreSQL, or Microsoft SQL Server databases.
    Before using it, it is necessary to add a new integration for the tool by providing:
    1. Alias (A friendly name for the database connection)
    2. Database Dialect (MySQL, PostgreSQL, MSSql)
    3. URL (Database server address)
    4. PORT (Database server port)
    5. Database or schema name
    6. Username
    7. Password
    """.strip(),
    settings_config=True,
    config_class=SQLConfig,
)

INFLUXDB_TOOL = ToolMetadata(
    name="influxdb",
    description="Executes Flux queries on InfluxDB time-series database.",
    label="InfluxDB",
    user_description="""
    Enables the AI assistant to execute Flux queries on InfluxDB time-series database.
    Before using it, it is necessary to add a new integration by providing:
    1. URL (e.g., http://localhost:8086)
    2. Authentication Token
    3. Organization name
    4. Bucket name
    """,
)
