# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
#
# Licensed under the Apache License, Version 2.0 (the “License”);
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an “AS IS” BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import logging
from typing import Any, Dict, Optional, Type

from pydantic import BaseModel, Field

from codemie_tools.base.codemie_tool import CodeMieTool
from codemie_tools.base.errors import InvalidCredentialsError
from codemie_tools.data_management.sql.handlers import DialectHandler
from codemie_tools.data_management.sql.handlers.influxdb import InfluxDBDialectHandler
from codemie_tools.data_management.sql.handlers.relational import RelationalDialectHandler
from codemie_tools.data_management.sql.models import SQLConfig, SQLDialect, SQLToolInput
from codemie_tools.data_management.sql.tools_vars import SQL_TOOL

_HANDLERS: Dict[SQLDialect, DialectHandler] = {
    SQLDialect.POSTGRES: RelationalDialectHandler(),
    SQLDialect.MYSQL: RelationalDialectHandler(),
    SQLDialect.MSSQL: RelationalDialectHandler(),
    SQLDialect.INFLUXDB: InfluxDBDialectHandler(),
}


class SQLTool(CodeMieTool):
    name: str = SQL_TOOL.name
    description: str = SQL_TOOL.description
    args_schema: Type[BaseModel] = SQLToolInput
    config: Optional[SQLConfig] = Field(exclude=True, default=None)

    def _get_handler(self) -> DialectHandler:
        handler = _HANDLERS.get(self.config.dialect)
        if handler is None:
            raise ValueError(f"Unsupported database type. Supported types are: {[e.value for e in SQLDialect]}")
        return handler

    def _healthcheck(self) -> None:
        config = self._validate_base_healthcheck_config()
        self._get_handler().healthcheck(config)

    def _validate_base_healthcheck_config(self) -> SQLConfig:
        if not self.config:
            raise InvalidCredentialsError(
                "SQL configuration is not provided. " "You should provide SQL credentials in 'Integrations'."
            )

        return self.config

    def execute(self, sql_query: str):
        if self.config is None:
            return "SQL configuration is not provided. Please provide in 'Integrations'."

        try:
            handler = self._get_handler()
            client = self.create_db_connection()
            try:
                return handler.execute(client, sql_query)
            finally:
                if hasattr(client, 'dispose'):
                    client.dispose()
                elif hasattr(client, 'close'):
                    client.close()

        except Exception as exc:
            logging.error(f"Error executing SQL query: {str(exc)}")
            try:
                return self._get_handler().error_hint(self.config, exc).strip()
            except Exception as exc:
                return f"Error during executing SQL: {str(exc)}"

    def list_tables_and_columns(self, client: Any) -> Dict:
        if self.config is None:
            raise ValueError("SQL configuration is not provided. Please provide in 'Integrations'.")
        return self._get_handler().list_schema(client, self.config)

    def create_db_connection(self) -> Any:
        return self._get_handler().create_connection(self.config)
