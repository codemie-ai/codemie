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
from contextlib import suppress
from unittest.mock import patch, MagicMock

from codemie_tools.data_management.sql.handlers.influxdb import InfluxDBDialectHandler
from codemie_tools.data_management.sql.models import SQLConfig, SQLDialect


class TestInfluxDBDialectHandlerHealthcheck(unittest.TestCase):
    def setUp(self):
        self.handler = InfluxDBDialectHandler()
        self.config = SQLConfig(
            dialect=SQLDialect.INFLUXDB.value,
            host="localhost",
            port="8086",
            token="test-token",
            org="test-org",
            bucket="test-bucket",
        )

    @patch("codemie_tools.data_management.sql.handlers.influxdb.InfluxDBDialectHandler.create_connection")
    def test_healthcheck_success(self, mock_create_connection):
        mock_client = MagicMock()
        mock_create_connection.return_value = mock_client
        mock_buckets_api = MagicMock()
        mock_client.buckets_api.return_value = mock_buckets_api
        mock_bucket = MagicMock()
        mock_bucket.name = "test-bucket"
        mock_buckets_result = MagicMock()
        mock_buckets_result.buckets = [mock_bucket]
        mock_buckets_api.find_buckets.return_value = mock_buckets_result

        self.handler.healthcheck(self.config)

        mock_buckets_api.find_buckets.assert_called_once_with(name="test-bucket", org="test-org")
        mock_client.close.assert_called_once()

    @patch("codemie_tools.data_management.sql.handlers.influxdb.InfluxDBDialectHandler.create_connection")
    def test_healthcheck_bucket_not_found(self, mock_create_connection):
        mock_client = MagicMock()
        mock_create_connection.return_value = mock_client
        mock_buckets_api = MagicMock()
        mock_client.buckets_api.return_value = mock_buckets_api
        mock_buckets_result = MagicMock()
        mock_buckets_result.buckets = []
        mock_buckets_api.find_buckets.return_value = mock_buckets_result

        with self.assertRaises(ConnectionError) as context:
            self.handler.healthcheck(self.config)

        self.assertIn("Bucket 'test-bucket' not found", str(context.exception))
        mock_client.close.assert_called_once()

    @patch("codemie_tools.data_management.sql.handlers.influxdb.InfluxDBDialectHandler.create_connection")
    def test_healthcheck_bucket_filtered_by_org(self, mock_create_connection):
        mock_client = MagicMock()
        mock_create_connection.return_value = mock_client
        mock_buckets_api = MagicMock()
        mock_client.buckets_api.return_value = mock_buckets_api
        # A bucket with the right name exists but in a different org — API returns empty for this org
        mock_buckets_result = MagicMock()
        mock_buckets_result.buckets = []
        mock_buckets_api.find_buckets.return_value = mock_buckets_result

        with self.assertRaises(ConnectionError) as context:
            self.handler.healthcheck(self.config)

        self.assertIn("test-org", str(context.exception))
        mock_buckets_api.find_buckets.assert_called_once_with(name="test-bucket", org="test-org")
        mock_client.close.assert_called_once()

    @patch("codemie_tools.data_management.sql.handlers.influxdb.InfluxDBDialectHandler.create_connection")
    def test_healthcheck_connection_error(self, mock_create_connection):
        mock_client = MagicMock()
        mock_create_connection.return_value = mock_client
        mock_buckets_api = MagicMock()
        mock_client.buckets_api.return_value = mock_buckets_api
        mock_buckets_api.find_buckets.side_effect = Exception("Connection failed")

        with self.assertRaises(ConnectionError) as context:
            self.handler.healthcheck(self.config)

        self.assertIn("Cannot connect to InfluxDB", str(context.exception))
        mock_client.close.assert_called_once()

    @patch("codemie_tools.data_management.sql.handlers.influxdb.InfluxDBDialectHandler.create_connection")
    def test_healthcheck_closes_client_on_error(self, mock_create_connection):
        mock_client = MagicMock()
        mock_create_connection.return_value = mock_client
        mock_buckets_api = MagicMock()
        mock_client.buckets_api.return_value = mock_buckets_api
        mock_buckets_result = MagicMock()
        mock_buckets_result.buckets = []
        mock_buckets_api.find_buckets.return_value = mock_buckets_result

        with suppress(ConnectionError):
            self.handler.healthcheck(self.config)

        mock_client.close.assert_called_once()


class TestInfluxDBDialectHandlerCreateConnection(unittest.TestCase):
    def setUp(self):
        self.handler = InfluxDBDialectHandler()

    @patch("codemie_tools.data_management.sql.models.SQLConfig.get_influxdb_client")
    def test_create_connection(self, mock_get_client):
        config = SQLConfig(
            dialect=SQLDialect.INFLUXDB.value,
            host="localhost",
            port="8086",
            token="test-token",
            org="test-org",
            bucket="test-bucket",
        )

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        result = self.handler.create_connection(config)

        self.assertEqual(result, mock_client)


class TestInfluxDBDialectHandlerExecute(unittest.TestCase):
    def setUp(self):
        self.handler = InfluxDBDialectHandler()

    def test_execute_flux_query(self):
        mock_client = MagicMock()
        mock_query_api = MagicMock()
        mock_client.query_api.return_value = mock_query_api

        mock_table = MagicMock()
        mock_record = MagicMock()
        mock_record.values = {"_measurement": "cpu", "_field": "usage", "_value": 85.5}
        mock_table.records = [mock_record]
        mock_query_api.query.return_value = [mock_table]

        mock_client.org = "test-org"

        result = self.handler.execute(mock_client, "from(bucket: \"test\") |> range(start: -1h)")

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], {"_measurement": "cpu", "_field": "usage", "_value": 85.5})

    def test_execute_flux_query_multiple_records(self):
        mock_client = MagicMock()
        mock_query_api = MagicMock()
        mock_client.query_api.return_value = mock_query_api

        mock_table = MagicMock()
        mock_record1 = MagicMock()
        mock_record1.values = {"_measurement": "cpu", "_field": "usage", "_value": 85.5}
        mock_record2 = MagicMock()
        mock_record2.values = {"_measurement": "cpu", "_field": "usage", "_value": 92.3}
        mock_table.records = [mock_record1, mock_record2]
        mock_query_api.query.return_value = [mock_table]

        mock_client.org = "test-org"

        result = self.handler.execute(mock_client, "from(bucket: \"test\") |> range(start: -1h)")

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["_value"], 85.5)
        self.assertEqual(result[1]["_value"], 92.3)

    def test_execute_flux_query_multiple_tables(self):
        mock_client = MagicMock()
        mock_query_api = MagicMock()
        mock_client.query_api.return_value = mock_query_api

        mock_table1 = MagicMock()
        mock_record1 = MagicMock()
        mock_record1.values = {"_measurement": "cpu", "_value": 85.5}
        mock_table1.records = [mock_record1]

        mock_table2 = MagicMock()
        mock_record2 = MagicMock()
        mock_record2.values = {"_measurement": "memory", "_value": 45.2}
        mock_table2.records = [mock_record2]

        mock_query_api.query.return_value = [mock_table1, mock_table2]
        mock_client.org = "test-org"

        result = self.handler.execute(mock_client, "from(bucket: \"test\") |> range(start: -1h)")

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["_measurement"], "cpu")
        self.assertEqual(result[1]["_measurement"], "memory")

    def test_execute_empty_result(self):
        mock_client = MagicMock()
        mock_query_api = MagicMock()
        mock_client.query_api.return_value = mock_query_api
        mock_query_api.query.return_value = []

        result = self.handler.execute(mock_client, "from(bucket: \"test\") |> range(start: -1h)")

        self.assertEqual(result, [])


class TestInfluxDBDialectHandlerListSchema(unittest.TestCase):
    def setUp(self):
        self.handler = InfluxDBDialectHandler()

    def test_list_schema_single_measurement(self):
        mock_client = MagicMock()
        mock_query_api = MagicMock()
        mock_client.query_api.return_value = mock_query_api

        # Mock measurements query
        mock_measurement_table = MagicMock()
        mock_measurement_record = MagicMock()
        mock_measurement_record.values = {"_value": "cpu"}
        mock_measurement_table.records = [mock_measurement_record]

        # Mock fields query
        mock_fields_table = MagicMock()
        mock_field_record = MagicMock()
        mock_field_record.values = {"_value": "usage"}
        mock_fields_table.records = [mock_field_record]

        config = SQLConfig(
            dialect=SQLDialect.INFLUXDB.value,
            host="localhost",
            port="8086",
            token="test-token",
            org="test-org",
            bucket="test-bucket",
        )

        # First call for measurements, second for fields
        mock_query_api.query.side_effect = [[mock_measurement_table], [mock_fields_table]]

        result = self.handler.list_schema(mock_client, config)

        self.assertIn("cpu", result)
        self.assertEqual(result["cpu"]["measurement_name"], "cpu")
        self.assertEqual(len(result["cpu"]["fields"]), 1)
        self.assertEqual(result["cpu"]["fields"][0]["name"], "usage")

    def test_list_schema_multiple_fields(self):
        mock_client = MagicMock()
        mock_query_api = MagicMock()
        mock_client.query_api.return_value = mock_query_api

        # Mock measurements query
        mock_measurement_table = MagicMock()
        mock_measurement_record = MagicMock()
        mock_measurement_record.values = {"_value": "cpu"}
        mock_measurement_table.records = [mock_measurement_record]

        # Mock fields query with multiple fields
        mock_fields_table = MagicMock()
        mock_field_record1 = MagicMock()
        mock_field_record1.values = {"_value": "usage"}
        mock_field_record2 = MagicMock()
        mock_field_record2.values = {"_value": "temperature"}
        mock_fields_table.records = [mock_field_record1, mock_field_record2]

        config = SQLConfig(
            dialect=SQLDialect.INFLUXDB.value,
            host="localhost",
            port="8086",
            token="test-token",
            org="test-org",
            bucket="test-bucket",
        )

        mock_query_api.query.side_effect = [[mock_measurement_table], [mock_fields_table]]

        result = self.handler.list_schema(mock_client, config)

        self.assertIn("cpu", result)
        self.assertEqual(len(result["cpu"]["fields"]), 2)


class TestInfluxDBDialectHandlerErrorHint(unittest.TestCase):
    def setUp(self):
        self.handler = InfluxDBDialectHandler()

    def test_error_hint(self):
        config = SQLConfig(
            dialect=SQLDialect.INFLUXDB.value,
            host="localhost",
            port="8086",
            token="test-token",
            org="test-org",
            bucket="test-bucket",
        )

        exc = Exception("Field 'invalid_field' not found")
        result = self.handler.error_hint(config, exc)

        self.assertIn("There is an error", result)
        self.assertIn("Try to change your Flux query", result)
        self.assertIn("test-bucket", result)


class TestInfluxDBDialectHandlerEscapeFluxString(unittest.TestCase):
    def setUp(self):
        self.handler = InfluxDBDialectHandler()

    def test_no_escaping_needed(self):
        result = self.handler._escape_flux_string("normal-bucket-name")
        self.assertEqual(result, "normal-bucket-name")

    def test_escape_backslash(self):
        result = self.handler._escape_flux_string("path\\to\\file")
        self.assertEqual(result, "path\\\\to\\\\file")

    def test_escape_double_quote(self):
        result = self.handler._escape_flux_string('bucket"name')
        self.assertEqual(result, 'bucket\\"name')

    def test_escape_newline(self):
        result = self.handler._escape_flux_string("bucket\nname")
        self.assertEqual(result, "bucket\\nname")

    def test_escape_carriage_return(self):
        result = self.handler._escape_flux_string("bucket\rname")
        self.assertEqual(result, "bucket\\rname")

    def test_escape_tab(self):
        result = self.handler._escape_flux_string("bucket\tname")
        self.assertEqual(result, "bucket\\tname")

    def test_strip_null_byte(self):
        result = self.handler._escape_flux_string("bucket\x00name")
        self.assertEqual(result, "bucketname")

    def test_strip_other_control_chars(self):
        result = self.handler._escape_flux_string("bucket\x01\x08name\x0b\x0c\x0e\x1fname")
        self.assertEqual(result, "bucketnamename")

    def test_escape_backslash_before_quote(self):
        # Backslash must be escaped before quote to avoid double-escaping
        result = self.handler._escape_flux_string('path\\"quoted')
        self.assertEqual(result, 'path\\\\\\"quoted')
