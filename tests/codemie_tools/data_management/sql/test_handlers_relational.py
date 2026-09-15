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

import unittest
from unittest.mock import patch, MagicMock, Mock

from sqlalchemy.exc import OperationalError as SQLOperationalError

from codemie_tools.data_management.sql.handlers.relational import RelationalDialectHandler
from codemie_tools.data_management.sql.models import SQLConfig, SQLDialect


class TestRelationalDialectHandlerHealthcheck(unittest.TestCase):
    def setUp(self):
        self.handler = RelationalDialectHandler()
        self.config_postgres = SQLConfig(
            dialect=SQLDialect.POSTGRES.value,
            host="localhost",
            port="5432",
            username="user",
            password="pass",
            database_name="test_db",
        )

    @patch("codemie_tools.data_management.sql.handlers.relational.create_engine")
    def test_healthcheck_success_postgres(self, mock_create_engine):
        mock_engine = MagicMock()
        mock_create_engine.return_value = mock_engine
        mock_connection = MagicMock()
        mock_engine.connect.return_value.__enter__.return_value = mock_connection

        self.handler.healthcheck(self.config_postgres)

        mock_engine.dispose.assert_called_once()

    @patch("codemie_tools.data_management.sql.handlers.relational.create_engine")
    def test_healthcheck_connection_error(self, mock_create_engine):
        mock_engine = MagicMock()
        mock_create_engine.return_value = mock_engine
        mock_engine.connect.return_value.__enter__.side_effect = SQLOperationalError("Connection refused", None, None)

        with self.assertRaises(ConnectionError) as context:
            self.handler.healthcheck(self.config_postgres)

        self.assertIn("Cannot connect to database", str(context.exception))

    @patch("codemie_tools.data_management.sql.handlers.relational.create_engine")
    def test_healthcheck_disposes_engine_on_success(self, mock_create_engine):
        mock_engine = MagicMock()
        mock_create_engine.return_value = mock_engine
        mock_connection = MagicMock()
        mock_engine.connect.return_value.__enter__.return_value = mock_connection

        self.handler.healthcheck(self.config_postgres)

        mock_engine.dispose.assert_called_once()

    @patch("codemie_tools.data_management.sql.handlers.relational.create_engine")
    def test_healthcheck_disposes_engine_on_error(self, mock_create_engine):
        mock_engine = MagicMock()
        mock_create_engine.return_value = mock_engine
        mock_engine.connect.return_value.__enter__.side_effect = SQLOperationalError("Error", None, None)

        with self.assertRaises(ConnectionError):
            self.handler.healthcheck(self.config_postgres)

        mock_engine.dispose.assert_called_once()

    @patch("codemie_tools.data_management.sql.handlers.relational.create_engine")
    def test_healthcheck_create_connection_failure_wrapped_in_connection_error(self, mock_create_engine):
        mock_create_engine.side_effect = ValueError("malformed connection string")

        with self.assertRaises(ConnectionError) as context:
            self.handler.healthcheck(self.config_postgres)

        self.assertIn("Cannot connect to database", str(context.exception))
        self.assertIn("malformed connection string", str(context.exception))

    @patch("codemie_tools.data_management.sql.handlers.relational.create_engine")
    def test_healthcheck_no_dispose_when_create_connection_fails(self, mock_create_engine):
        mock_engine = MagicMock()
        mock_create_engine.side_effect = ValueError("bad URL")

        with self.assertRaises(ConnectionError):
            self.handler.healthcheck(self.config_postgres)

        mock_engine.dispose.assert_not_called()


class TestRelationalDialectHandlerCreateConnection(unittest.TestCase):
    def setUp(self):
        self.handler = RelationalDialectHandler()

    @patch("codemie_tools.data_management.sql.handlers.relational.create_engine")
    def test_create_connection_postgres(self, mock_create_engine):
        config = SQLConfig(
            dialect=SQLDialect.POSTGRES.value,
            host="localhost",
            port="5432",
            username="user",
            password="pass",
            database_name="test_db",
        )

        self.handler.create_connection(config)

        mock_create_engine.assert_called_with("postgresql+psycopg://user:pass@localhost:5432/test_db")

    @patch("codemie_tools.data_management.sql.handlers.relational.create_engine")
    def test_create_connection_mysql(self, mock_create_engine):
        config = SQLConfig(
            dialect=SQLDialect.MYSQL.value,
            host="localhost",
            port="3306",
            username="root",
            password="secret",
            database_name="mydb",
        )

        self.handler.create_connection(config)

        mock_create_engine.assert_called_with("mysql+pymysql://root:secret@localhost:3306/mydb")

    @patch("codemie_tools.data_management.sql.handlers.relational.create_engine")
    def test_create_connection_mssql(self, mock_create_engine):
        config = SQLConfig(
            dialect=SQLDialect.MSSQL.value,
            host="localhost",
            port="1433",
            username="sa",
            password="Password1!",
            database_name="master",
        )

        self.handler.create_connection(config)

        mock_create_engine.assert_called_with("mssql+pymssql://sa:Password1%21@localhost:1433/master")

    @patch("codemie_tools.data_management.sql.handlers.relational.create_engine")
    def test_create_connection_special_chars_in_password(self, mock_create_engine):
        config = SQLConfig(
            dialect=SQLDialect.POSTGRES.value,
            host="localhost",
            port="5432",
            username="user",
            password="p@ss!word#123",
            database_name="test_db",
        )

        self.handler.create_connection(config)

        mock_create_engine.assert_called_with("postgresql+psycopg://user:p%40ss%21word%23123@localhost:5432/test_db")

    def test_create_connection_unsupported_dialect(self):
        config = SQLConfig(
            dialect="unsupported",
            host="localhost",
            port="5432",
            username="user",
            password="pass",
            database_name="test_db",
        )

        with self.assertRaises(ValueError) as context:
            self.handler.create_connection(config)

        self.assertIn("Unsupported database type", str(context.exception))


class TestRelationalDialectHandlerExecute(unittest.TestCase):
    def setUp(self):
        self.handler = RelationalDialectHandler()

    @patch("codemie_tools.data_management.sql.handlers.relational.text")
    def test_execute_select_query(self, mock_text):
        mock_engine = MagicMock()
        mock_connection = MagicMock()
        mock_engine.connect.return_value.__enter__.return_value = mock_connection
        mock_connection.begin.return_value.__enter__.return_value = None
        mock_result = MagicMock()
        mock_result.returns_rows = True
        mock_result.keys.return_value = ["id", "name"]
        mock_result.__iter__.return_value = [(1, "Alice"), (2, "Bob")]
        mock_connection.execute.return_value = mock_result

        result = self.handler.execute(mock_engine, "SELECT * FROM users")

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0], {"id": 1, "name": "Alice"})
        self.assertEqual(result[1], {"id": 2, "name": "Bob"})

    @patch("codemie_tools.data_management.sql.handlers.relational.text")
    def test_execute_insert_query(self, mock_text):
        mock_engine = MagicMock()
        mock_connection = MagicMock()
        mock_engine.connect.return_value.__enter__.return_value = mock_connection
        mock_connection.begin.return_value.__enter__.return_value = None
        mock_result = MagicMock()
        mock_result.returns_rows = False
        mock_result.rowcount = 1
        mock_connection.execute.return_value = mock_result

        result = self.handler.execute(mock_engine, "INSERT INTO users (name) VALUES ('Alice')")

        self.assertIn("Query executed successfully", result)
        self.assertIn("Rows affected: 1", result)

    @patch("codemie_tools.data_management.sql.handlers.relational.text")
    def test_execute_query_exception(self, mock_text):
        mock_engine = MagicMock()
        mock_connection = MagicMock()
        mock_engine.connect.return_value.__enter__.return_value = mock_connection
        mock_connection.begin.return_value.__enter__.side_effect = ValueError("Query error")

        with self.assertRaises(ValueError):
            self.handler.execute(mock_engine, "SELECT * FROM users")


class TestRelationalDialectHandlerListSchema(unittest.TestCase):
    def setUp(self):
        self.handler = RelationalDialectHandler()

    @patch("codemie_tools.data_management.sql.handlers.relational.inspect")
    def test_list_schema_single_table(self, mock_inspect):
        mock_engine = MagicMock()
        mock_inspector = MagicMock()
        mock_inspect.return_value = mock_inspector
        mock_inspector.get_table_names.return_value = ["users"]
        mock_inspector.get_columns.return_value = [
            {"name": "id", "type": "INTEGER"},
            {"name": "name", "type": "VARCHAR"},
        ]

        config = SQLConfig(
            dialect=SQLDialect.POSTGRES.value,
            host="localhost",
            port="5432",
            username="user",
            password="pass",
            database_name="test_db",
        )

        result = self.handler.list_schema(mock_engine, config)

        self.assertIn("users", result)
        self.assertEqual(result["users"]["table_name"], "users")
        self.assertEqual(len(result["users"]["table_columns"]), 2)

    @patch("codemie_tools.data_management.sql.handlers.relational.inspect")
    def test_list_schema_multiple_tables(self, mock_inspect):
        mock_engine = MagicMock()
        mock_inspector = MagicMock()
        mock_inspect.return_value = mock_inspector
        mock_inspector.get_table_names.return_value = ["users", "products"]
        mock_inspector.get_columns.side_effect = [
            [{"name": "id", "type": "INTEGER"}],
            [{"name": "id", "type": "INTEGER"}, {"name": "title", "type": "VARCHAR"}],
        ]

        config = SQLConfig(
            dialect=SQLDialect.POSTGRES.value,
            host="localhost",
            port="5432",
            username="user",
            password="pass",
            database_name="test_db",
        )

        result = self.handler.list_schema(mock_engine, config)

        self.assertEqual(len(result), 2)
        self.assertIn("users", result)
        self.assertIn("products", result)


class TestRelationalDialectHandlerErrorHint(unittest.TestCase):
    def setUp(self):
        self.handler = RelationalDialectHandler()

    @patch("codemie_tools.data_management.sql.handlers.relational.inspect")
    @patch("codemie_tools.data_management.sql.handlers.relational.create_engine")
    def test_error_hint(self, mock_create_engine, mock_inspect):
        mock_engine = MagicMock()
        mock_create_engine.return_value = mock_engine
        mock_inspector = MagicMock()
        mock_inspect.return_value = mock_inspector
        mock_inspector.get_table_names.return_value = ["users"]
        mock_inspector.get_columns.return_value = [{"name": "id", "type": "INTEGER"}]

        config = SQLConfig(
            dialect=SQLDialect.POSTGRES.value,
            host="localhost",
            port="5432",
            username="user",
            password="pass",
            database_name="test_db",
        )

        exc = Exception("Column 'invalid_col' not found")
        result = self.handler.error_hint(config, exc)

        self.assertIn("There is an error", result)
        self.assertIn("Available tables with columns", result)
        self.assertIn("users", result)


class TestRelationalDialectHandlerExtractConnectionError(unittest.TestCase):
    def setUp(self):
        self.handler = RelationalDialectHandler()

    def test_extract_connection_error_simple_message(self):
        exc = Mock()
        exc.orig = None
        exc.__str__ = Mock(return_value="Connection refused\nLine 2")

        result = self.handler._extract_connection_error(exc)

        self.assertEqual(result, "Connection refused")

    def test_extract_connection_error_with_original_bytes(self):
        # When orig is a tuple (pymssql case), the bytes are decoded properly
        exc = Mock()
        exc.orig = (1234, b"Database connection error")

        result = self.handler._extract_connection_error(exc)

        self.assertEqual(result, "Database connection error")

    def test_extract_connection_error_pymssql_tuple(self):
        exc = Mock()
        exc.orig = (1234, b"DB-Lib error message 1, severity 1: Connection timeout\nActual error message")

        result = self.handler._extract_connection_error(exc)

        self.assertEqual(result, "Actual error message")
