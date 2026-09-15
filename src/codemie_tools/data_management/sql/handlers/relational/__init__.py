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

from typing import Dict, List, Union
from urllib.parse import quote_plus

from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.exc import OperationalError as SQLOperationalError

from codemie_tools.base.codemie_tool import logger
from codemie_tools.data_management.sql.handlers import DialectHandler
from codemie_tools.data_management.sql.models import SQLConfig, SQLDialect


class RelationalDialectHandler(DialectHandler):
    """Handler for relational databases: PostgreSQL, MySQL, and MSSQL."""

    _CONNECTION_PREFIXES: Dict[SQLDialect, str] = {
        SQLDialect.POSTGRES: "postgresql+psycopg",
        SQLDialect.MYSQL: "mysql+pymysql",
        SQLDialect.MSSQL: "mssql+pymssql",
    }

    @staticmethod
    def _extract_connection_error(e: SQLOperationalError) -> str:
        """Extract a clean, single-line message from a SQLAlchemy OperationalError."""
        orig = e.orig
        if orig is None:
            return str(e).split("\n")[0].strip()

        # pymssql wraps errors as a (code, bytes_message) tuple
        is_pymssql_error_tuple = isinstance(orig, tuple) and len(orig) >= 2
        raw = orig[1] if is_pymssql_error_tuple else str(orig)
        message = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)

        # pymssql prepends "DB-Lib error message N, severity N:" header lines
        # before the actual human-readable message — filter those out.
        lines = [
            line.strip()
            for line in message.split("\n")
            if line.strip() and not line.strip().startswith("DB-Lib error message")
        ]
        return lines[0] if lines else message.split("\n")[0].strip()

    def healthcheck(self, config: SQLConfig) -> None:
        engine = None
        try:
            engine = self.create_connection(config)
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        except SQLOperationalError as e:
            msg = self._extract_connection_error(e)
            raise ConnectionError(f"Cannot connect to database: {msg}") from None
        except Exception as e:
            raise ConnectionError(f"Cannot connect to database: {e}") from None
        finally:
            if engine is not None:
                engine.dispose()

    def create_connection(self, config: SQLConfig) -> Engine:
        prefix = self._CONNECTION_PREFIXES.get(config.dialect)
        if prefix is None:
            raise ValueError(f"Unsupported database type. Supported types are: {[e.value for e in SQLDialect]}")

        username = quote_plus(config.username)
        password = quote_plus(config.password)
        return create_engine(f"{prefix}://{username}:{password}@{config.host}:{config.port}/{config.database_name}")

    def execute(self, engine: Engine, query: str) -> Union[List[Dict], str]:
        with engine.connect() as connection:
            try:
                with connection.begin():
                    result = connection.execute(text(query))

                    if result.returns_rows:
                        columns = result.keys()
                        return [dict(zip(columns, row)) for row in result]
                    else:
                        affected_rows = result.rowcount if hasattr(result, "rowcount") else "unknown"
                        return f"Query executed successfully. Rows affected: {affected_rows}"

            except Exception as e:
                logger.error(f"SQL error: {str(e)} in query: {query[:100]}...")
                raise

    def list_schema(self, engine: Engine, config: SQLConfig) -> Dict:
        inspector = inspect(engine)
        data = {}
        for table in inspector.get_table_names():
            columns = inspector.get_columns(table)
            data[table] = {
                "table_name": table,
                "table_columns": [{"name": col["name"], "type": col["type"]} for col in columns],
            }
        return data

    def error_hint(self, config: SQLConfig, exc: Exception) -> str:
        try:
            engine = self.create_connection(config)
            try:
                schema = self.list_schema(engine, config)
            finally:
                engine.dispose()
        except Exception as schema_exc:
            logger.error(f"Failed to retrieve schema for error hint: {str(schema_exc)}")
            return (
                f"There is an error: {exc}.\n"
                f"Try to change your query to get the desired result.\n"
                f"Could not retrieve schema information for suggestions.\n"
            )

        return (
            f"There is an error: {exc}.\n"
            f"Try to change your query to get the desired result according available details. \n"
            f"Available tables with columns: {schema}. \n"
        )
