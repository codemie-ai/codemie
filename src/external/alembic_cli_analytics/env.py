# Copyright 2026 EPAM Systems, Inc. ("EPAM")
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

"""Alembic environment of the CLI Analytics PostgreSQL schema.

`run_migrations()` passes an open connection (holding the migration advisory lock, with
search_path set to the analytics schema) and the schema name through
`config.attributes`. Migrations use unqualified names, so they apply to whichever schema
CLI_ANALYTICS_PG_SCHEMA names. The version table lives in that schema too.
"""

from alembic import context

VERSION_TABLE = "cli_analytics_alembic_version"

if context.is_offline_mode():
    raise RuntimeError("CLI Analytics migrations run online only, through run_migrations()")

_connection = context.config.attributes["connection"]
_schema = context.config.attributes["schema"]

context.configure(
    connection=_connection,
    target_metadata=None,
    version_table=VERSION_TABLE,
    version_table_schema=_schema,
    transactional_ddl=True,
)

with context.begin_transaction():
    context.run_migrations()
