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

"""An in-memory sqlite engine that enforces foreign keys and supports real SAVEPOINTs."""

from __future__ import annotations

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool


@compiles(JSONB, "sqlite")
@compiles(ARRAY, "sqlite")
def _as_json(_type, compiler, **_kw):
    return "JSON"


def constrained_sqlite_engine() -> Engine:
    """Return a single-connection sqlite engine with foreign keys on and SQLAlchemy-owned transactions.

    pysqlite's own transaction handling breaks SAVEPOINT, so the driver's BEGIN is turned off and
    SQLAlchemy emits it instead (SQLAlchemy's pysqlite serializable/savepoint recipe).
    """
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_connection, _connection_record):
        dbapi_connection.isolation_level = None
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    @event.listens_for(engine, "begin")
    def _on_begin(connection):
        connection.exec_driver_sql("BEGIN")

    return engine
