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

import unittest
from unittest.mock import patch, MagicMock

import pytest

from codemie_tools.data_management.sql.models import SQLConfig, SQLDialect
from codemie_tools.data_management.sql.tools import SQLTool


class TestSQLTool(unittest.TestCase):
    def setUp(self):
        self.config = SQLConfig(
            dialect=SQLDialect.POSTGRES.value,
            host="localhost",
            port="5432",
            username="user",
            password="pass",
            database_name="test_db",
        )
        self.sql_tool = SQLTool(config=self.config)

    def setup_influxdb_config(self):
        return SQLConfig(
            dialect=SQLDialect.INFLUXDB.value,
            host="localhost",
            port="8086",
            token="test-token",
            org="test-org",
            bucket="test-bucket",
        )

    @patch("codemie_tools.data_management.sql.tools.create_engine")
    def test_create_db_connection_postgres(self, mock_create_engine):
        self.sql_tool.create_db_connection()
        mock_create_engine.assert_called_with("postgresql+psycopg://user:pass@localhost:5432/test_db")

    @patch("codemie_tools.data_management.sql.tools.create_engine")
    def test_create_db_connection_mysql(self, mock_create_engine):
        self.sql_tool.config.dialect = SQLDialect.MYSQL.value
        self.sql_tool.create_db_connection()
        mock_create_engine.assert_called_with("mysql+pymysql://user:pass@localhost:5432/test_db")

    @patch("influxdb_client.InfluxDBClient")
    def test_create_db_connection_influxdb(self, mock_influxdb_client):
        self.sql_tool.config = self.setup_influxdb_config()
        self.sql_tool.create_db_connection()

        mock_influxdb_client.assert_called_with(
            url="http://localhost:8086",
            token="test-token",
            org="test-org",
            verify_ssl=False,
        )

    def test_create_db_connection_invalid_dialect(self):
        self.sql_tool.config.dialect = "invalid_dialect"
        with self.assertRaises(ValueError) as context:
            self.sql_tool.create_db_connection()
        self.assertEqual(
            str(context.exception),
            "Unsupported database type. Supported types are: ['mysql', 'postgres', 'influxdb', 'mssql']",
        )

    @patch("codemie_tools.data_management.sql.tools.create_engine")
    @patch("codemie_tools.data_management.sql.tools.inspect")
    def test_list_tables_and_columns(self, mock_inspect, mock_create_engine):
        mock_engine = mock_create_engine.return_value
        mock_inspector = mock_inspect.return_value
        mock_inspector.get_table_names.return_value = ["table1"]
        mock_inspector.get_columns.return_value = [
            {"name": "column1", "type": "String"},
            {"name": "column2", "type": "Integer"},
        ]

        data = self.sql_tool.list_tables_and_columns(mock_engine)
        expected_data = {
            "table1": {
                "table_name": "table1",
                "table_columns": [
                    {"name": "column1", "type": "String"},
                    {"name": "column2", "type": "Integer"},
                ],
            }
        }
        self.assertEqual(data, expected_data)

    @patch("influxdb_client.InfluxDBClient")
    def test_list_tables_and_columns_influxdb(self, mock_influxdb_client):
        self.sql_tool.config = self.setup_influxdb_config()

        # Mock query API
        mock_query_api = MagicMock()
        mock_influxdb_client.query_api.return_value = mock_query_api

        # Mock measurements query result
        measurements_result = MagicMock()
        measurements_record = MagicMock()
        measurements_record.values = {"_value": "temperature"}
        measurements_result.records = [measurements_record]

        # Mock fields query result
        fields_result = MagicMock()
        fields_record = MagicMock()
        fields_record.values = {"_value": "value"}
        fields_result.records = [fields_record]

        # Set up query_api returns
        mock_query_api.query.side_effect = [[measurements_result], [fields_result]]

        # Execute the method
        data = self.sql_tool.list_tables_and_columns(mock_influxdb_client)

        # Verify the results
        expected_data = {
            "temperature": {
                "measurement_name": "temperature",
                "fields": [{"name": "value", "type": "field"}],
            }
        }
        self.assertEqual(data, expected_data)

    @patch("codemie_tools.data_management.sql.tools.create_engine")
    def test_execute_sql_query(self, mock_create_engine):
        # Import text inside the test function to avoid import errors

        # Mock engine and connection
        mock_engine = mock_create_engine.return_value
        mock_connection = MagicMock()
        mock_engine.connect.return_value.__enter__.return_value = mock_connection

        # Mock transaction context manager
        mock_transaction = MagicMock()
        mock_connection.begin.return_value.__enter__.return_value = mock_transaction

        # Mock query execution result
        mock_result = MagicMock()
        mock_result.returns_rows = True
        mock_result.keys.return_value = ["column1", "column2"]

        # Since we're now iterating directly over the result rather than using fetchall(),
        # we need to make the mock_result iterable
        mock_result.__iter__.return_value = [("value1", "value2")]

        mock_connection.execute.return_value = mock_result

        # Execute the function
        query = "SELECT * FROM table1"
        data = self.sql_tool.execute_sql(mock_engine, query)

        # Assert the results
        expected_data = [{"column1": "value1", "column2": "value2"}]
        self.assertEqual(data, expected_data)

        # Verify the mocks were called correctly
        mock_engine.connect.assert_called_once()
        mock_connection.begin.assert_called_once()

    @patch("influxdb_client.InfluxDBClient")
    def test_execute_influxdb_query(self, mock_influxdb_client):
        self.sql_tool.config = self.setup_influxdb_config()

        # Mock query API
        mock_query_api = MagicMock()
        mock_influxdb_client.return_value.query_api.return_value = mock_query_api

        # Mock query result
        table = MagicMock()
        record = MagicMock()
        record.values = {"_time": "2023-01-01T00:00:00Z", "_value": 23.5}
        table.records = [record]
        mock_query_api.query.return_value = [table]

        flux_query = """
        from(bucket: "test-bucket")
          |> range(start: -1h)
          |> filter(fn: (r) => r["_measurement"] == "temperature")
        """

        data = self.sql_tool.execute(flux_query)
        expected_data = [{"_time": "2023-01-01T00:00:00Z", "_value": 23.5}]
        self.assertEqual(data, expected_data)

    @patch("codemie_tools.data_management.sql.tools.create_engine")
    def test_execute_sql_non_select_query(self, mock_create_engine):
        # Mock engine and connection
        mock_engine = mock_create_engine.return_value
        mock_connection = MagicMock()
        mock_engine.connect.return_value.__enter__.return_value = mock_connection

        # Mock transaction context manager
        mock_transaction = MagicMock()
        mock_connection.begin.return_value.__enter__.return_value = mock_transaction

        # Mock query execution result
        mock_result = MagicMock()
        mock_result.returns_rows = False
        mock_result.rowcount = 5  # Simulate 5 rows affected
        mock_connection.execute.return_value = mock_result

        # Execute the function
        query = "UPDATE table1 SET column1 = value1"
        data = self.sql_tool.execute_sql(mock_engine, query)

        # Assert the results
        expected_data = "Query executed successfully. Rows affected: 5"
        self.assertEqual(data, expected_data)

        # Verify the mocks were called correctly
        mock_engine.connect.assert_called_once()
        mock_connection.execute.assert_called_once()
        mock_connection.begin.assert_called_once()

    @patch("codemie_tools.data_management.sql.tools.create_engine")
    def test_execute_sql_exception(self, mock_create_engine):
        # Import text inside the test function to avoid import errors

        # Mock engine and connection
        mock_engine = mock_create_engine.return_value
        mock_connection = MagicMock()
        mock_engine.connect.return_value.__enter__.return_value = mock_connection

        # Mock transaction context manager
        mock_transaction = MagicMock()
        mock_connection.begin.return_value.__enter__.return_value = mock_transaction

        # Set up the exception
        mock_connection.execute.side_effect = Exception("Some error")

        # Test that the exception is raised
        with self.assertRaises(Exception) as context:
            self.sql_tool.execute_sql(mock_engine, "SELECT * FROM table1")

        # Verify the exception message
        self.assertEqual(str(context.exception), "Some error")

        # Verify connect was called
        mock_engine.connect.assert_called_once()

    @patch("influxdb_client.InfluxDBClient")
    def test_execute_influxdb_exception(self, mock_influxdb_client):
        self.sql_tool.config = self.setup_influxdb_config()

        # Mock query API
        mock_query_api = MagicMock()
        mock_influxdb_client.return_value.query_api.return_value = mock_query_api

        # Mock query exception
        mock_query_api.query.side_effect = Exception("InfluxDB error")

        flux_query = """
        from(bucket: "test-bucket")
          |> range(start: -1h)
          |> filter(fn: (r) => r["_measurement"] == "temperature")
        """

        data = self.sql_tool.execute(flux_query)
        self.assertIn("There is an error: InfluxDB error", data)
        self.assertIn("test-bucket", data)  # Should mention bucket name in error

    @patch("codemie_tools.data_management.sql.tools.create_engine")
    @patch("codemie_tools.data_management.sql.tools.SQLTool.list_tables_and_columns")
    @patch("codemie_tools.data_management.sql.tools.SQLTool.execute_sql")
    def test_execute(self, mock_execute_sql, mock_list_tables_and_columns, mock_create_engine):
        mock_engine = mock_create_engine.return_value
        mock_list_tables_and_columns.return_value = {
            "table1": {
                "table_name": "table1",
                "table_columns": [
                    {"name": "column1", "type": "String"},
                    {"name": "column2", "type": "Integer"},
                ],
            }
        }

        # Test successful execution
        mock_execute_sql.return_value = [{"column1": "value1", "column2": "value2"}]

        data = self.sql_tool.execute("SELECT * FROM table1")
        expected_data = [{"column1": "value1", "column2": "value2"}]
        self.assertEqual(data, expected_data)

        # Test execution with exception
        mock_execute_sql.side_effect = Exception("Some error")
        data = self.sql_tool.execute("SELECT * FROM table1")
        assert mock_engine is not None
        assert "There is an error: Some error" in data


# Parametrized tests for URL encoding - grouped by functionality
@pytest.mark.parametrize(
    "dialect,username,password,expected_connection_string",
    [
        # PostgreSQL tests
        (
            SQLDialect.POSTGRES.value,
            "user@domain.com",
            "p@ssw0rd!#$%",
            "postgresql+psycopg://user%40domain.com:p%40ssw0rd%21%23%24%25@localhost:5432/test_db",
        ),
        (
            SQLDialect.POSTGRES.value,
            "用户",  # Unicode characters
            "пароль",
            "postgresql+psycopg://%E7%94%A8%E6%88%B7:%D0%BF%D0%B0%D1%80%D0%BE%D0%BB%D1%8C@localhost:5432/test_db",
        ),
        (
            SQLDialect.POSTGRES.value,
            "user123",  # Safe characters
            "password123",
            "postgresql+psycopg://user123:password123@localhost:5432/test_db",
        ),
        (
            SQLDialect.POSTGRES.value,
            "test user",  # Spaces (quote_plus behavior)
            "test password",
            "postgresql+psycopg://test+user:test+password@localhost:5432/test_db",
        ),
        # MySQL tests
        (
            SQLDialect.MYSQL.value,
            "user+name",
            "pass word",
            "mysql+pymysql://user%2Bname:pass+word@localhost:5432/test_db",
        ),
        # MSSQL tests
        (
            SQLDialect.MSSQL.value,
            "domain\\user",
            "p&ssw=rd",
            "mssql+pymssql://domain%5Cuser:p%26ssw%3Drd@localhost:5432/test_db",
        ),
    ],
)
@patch("codemie_tools.data_management.sql.tools.create_engine")
def test_create_db_connection_url_encoding_by_dialect(
    mock_create_engine, dialect, username, password, expected_connection_string
):
    """Test URL encoding for different database dialects with various special characters"""
    config = SQLConfig(
        dialect=dialect, host="localhost", port="5432", username=username, password=password, database_name="test_db"
    )
    sql_tool = SQLTool(config=config)

    sql_tool.create_db_connection()

    mock_create_engine.assert_called_with(expected_connection_string)


@pytest.mark.parametrize(
    "username,password,expected_connection_string",
    [
        # Colon and @ characters
        ("user:admin", "pass@word", "postgresql+psycopg://user%3Aadmin:pass%40word@localhost:5432/test_db"),
        # Percent characters
        ("user%name", "pass%word", "postgresql+psycopg://user%25name:pass%25word@localhost:5432/test_db"),
        # Forward and backward slashes
        ("user/name", "pass\\word", "postgresql+psycopg://user%2Fname:pass%5Cword@localhost:5432/test_db"),
    ],
)
@patch("codemie_tools.data_management.sql.tools.create_engine")
def test_create_db_connection_url_encoding_edge_cases(
    mock_create_engine, username, password, expected_connection_string
):
    """Test URL encoding for edge cases with special characters"""
    config = SQLConfig(
        dialect=SQLDialect.POSTGRES.value,
        host="localhost",
        port="5432",
        username=username,
        password=password,
        database_name="test_db",
    )
    sql_tool = SQLTool(config=config)

    sql_tool.create_db_connection()

    mock_create_engine.assert_called_with(expected_connection_string)


@patch("codemie_tools.data_management.sql.tools.create_engine")
def test_create_db_connection_preserves_config_params(mock_create_engine):
    """Test that URL encoding doesn't affect other configuration parameters"""
    config = SQLConfig(
        dialect=SQLDialect.POSTGRES.value,
        host="custom.host.com",
        port="3306",
        username="user@domain",
        password="pass!word",
        database_name="custom_db",
    )
    sql_tool = SQLTool(config=config)

    sql_tool.create_db_connection()

    expected_connection_string = "postgresql+psycopg://user%40domain:pass%21word@custom.host.com:3306/custom_db"
    mock_create_engine.assert_called_with(expected_connection_string)


@patch("codemie_tools.data_management.sql.tools.create_engine")
def test_create_db_connection_empty_credentials_bypass_validation(mock_create_engine):
    """Test URL encoding with empty credentials by bypassing pydantic validation"""
    # Create config with valid values first
    config = SQLConfig(
        dialect=SQLDialect.POSTGRES.value,
        host="localhost",
        port="5432",
        username="temp",
        password="temp",
        database_name="test_db",
    )
    sql_tool = SQLTool(config=config)

    # Then manually set empty credentials to test the encoding
    sql_tool.config.username = ""
    sql_tool.config.password = ""

    sql_tool.create_db_connection()

    expected_connection_string = "postgresql+psycopg://:@localhost:5432/test_db"
    mock_create_engine.assert_called_with(expected_connection_string)
