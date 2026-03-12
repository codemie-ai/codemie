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

from enum import Enum
from typing import Optional, Dict, Any, TYPE_CHECKING

from pydantic import AliasChoices, BaseModel, model_validator, field_validator, Field
from codemie_tools.base.models import CodeMieToolConfig, CredentialTypes

if TYPE_CHECKING:
    from influxdb_client import InfluxDBClient


class SQLDialect(str, Enum):
    MYSQL = "mysql"
    POSTGRES = "postgres"
    INFLUXDB = "influxdb"
    MSSQL = "mssql"


class SQLToolInput(BaseModel):
    sql_query: str = Field(
        title="SQL Query",
        description="The SQL query to execute. For InfluxDB, use Flux query language.",
    )


class SQLConfig(CodeMieToolConfig):
    credential_type: CredentialTypes = Field(default=CredentialTypes.SQL, exclude=True, frozen=True)
    # Common fields
    dialect: str
    host: str = Field(validation_alias=AliasChoices("host", "url"))
    port: str

    # Authentication fields - Optional for InfluxDB 2.x
    username: Optional[str] = None
    password: Optional[str] = None
    database_name: Optional[str] = None

    # InfluxDB specific fields
    token: Optional[str] = None
    org: Optional[str] = None
    bucket: Optional[str] = None
    verify_ssl: Optional[bool] = Field(default=False, description="SSL verification for InfluxDB")

    @classmethod
    @field_validator("dialect")
    def validate_dialect(cls, v):
        if isinstance(v, str):
            return SQLDialect(v)
        return v

    @model_validator(mode="before")
    def validate_config(cls, values: Dict[str, Any]) -> Dict[str, Any]:
        if "url" in values and "host" not in values:
            values["host"] = values["url"]

        dialect = values.get("dialect")

        # Common validation for all dialects
        if not all(values.get(field) for field in ["host", "port", "dialect"]):
            raise ValueError("host, port, and dialect are required fields")

        if dialect == SQLDialect.INFLUXDB:
            # InfluxDB validation
            if not values.get("token"):
                raise ValueError("token is required for InfluxDB connections")
            if not values.get("org"):
                raise ValueError("org is required for InfluxDB connections")
            if not values.get("bucket"):
                raise ValueError("bucket is required for InfluxDB connections")
        else:
            # SQL database validation
            required_sql_fields = ["username", "password", "database_name"]
            missing_fields = [field for field in required_sql_fields if not values.get(field)]
            if missing_fields:
                raise ValueError(f"For SQL databases, {', '.join(missing_fields)} are required fields")

        return values

    def get_influxdb_client(self) -> "InfluxDBClient":
        """Creates and returns InfluxDB client instance"""
        if self.dialect != SQLDialect.INFLUXDB:
            raise ValueError("This method is only for InfluxDB configurations")

        from influxdb_client import InfluxDBClient

        return InfluxDBClient(
            url=f"http{'s' if self.verify_ssl else ''}://{self.host}:{self.port}",
            token=self.token,
            org=self.org,
            verify_ssl=self.verify_ssl,
        )
