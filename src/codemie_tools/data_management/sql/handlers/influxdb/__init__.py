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

import re
from typing import Dict, List, Union

from influxdb_client import InfluxDBClient

from codemie_tools.data_management.sql.handlers import DialectHandler
from codemie_tools.data_management.sql.models import SQLConfig


class InfluxDBDialectHandler(DialectHandler):
    """Handler for InfluxDB using the Flux query language."""

    @staticmethod
    def _escape_flux_string(value: str) -> str:
        """Escape a value for safe interpolation inside a Flux double-quoted string literal."""
        # Order matters: backslash must be escaped first
        value = value.replace("\\", "\\\\")
        value = value.replace('"', '\\"')
        value = value.replace("\n", "\\n")
        value = value.replace("\r", "\\r")
        value = value.replace("\t", "\\t")
        # Strip remaining control characters (U+0000–U+001F, U+007F)
        value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", value)
        return value

    def healthcheck(self, config: SQLConfig) -> None:
        client = self.create_connection(config)
        try:
            # health() is unauthenticated — use buckets_api to validate token + org
            buckets = client.buckets_api().find_buckets(name=config.bucket, org=config.org)
            bucket = next(
                (b for b in (buckets.buckets or []) if b.name == config.bucket),
                None,
            )
            if bucket is None:
                raise ConnectionError(
                    f"Bucket '{config.bucket}' not found in org '{config.org}'. "
                    "Check that the bucket name and org are correct."
                )
        except ConnectionError:
            raise
        except Exception as e:
            raise ConnectionError(f"Cannot connect to InfluxDB at {config.host}:{config.port}: {e}") from None
        finally:
            client.close()

    def create_connection(self, config: SQLConfig) -> InfluxDBClient:
        return config.get_influxdb_client()

    def execute(self, client: InfluxDBClient, query: str) -> Union[List[Dict], str]:
        try:
            query_api = client.query_api()
            result = query_api.query(query=query, org=client.org)
            records = []
            for table in result:
                for record in table.records:
                    records.append(record.values)
            return records
        finally:
            client.close()

    def list_schema(self, client: InfluxDBClient, config: SQLConfig) -> Dict:
        query_api = client.query_api()
        measurements_query = f"""
            import "influxdata/influxdb/schema"
            schema.measurements(bucket: "{self._escape_flux_string(config.bucket)}")
            """
        measurements = query_api.query(query=measurements_query, org=config.org)

        data = {}
        for table in measurements:
            for record in table.records:
                measurement = record.values.get("_value")
                if measurement is None:
                    continue

                fields_query = f"""
                    import "influxdata/influxdb/schema"
                    schema.measurementFieldKeys(
                        bucket: "{self._escape_flux_string(config.bucket)}",
                        measurement: "{self._escape_flux_string(measurement)}"
                    )
                    """
                fields = query_api.query(query=fields_query, org=config.org)
                fields_list = [
                    {"name": field_record.values.get("_value"), "type": "field"}
                    for field_table in fields
                    for field_record in field_table.records
                ]
                data[measurement] = {"measurement_name": measurement, "fields": fields_list}
        return data

    def error_hint(self, config: SQLConfig, exc: Exception) -> str:
        return (
            f"There is an error: {exc}.\n"
            f"Try to change your Flux query to get the desired result.\n"
            f"Make sure you're using correct bucket name: {config.bucket}\n"
        )
