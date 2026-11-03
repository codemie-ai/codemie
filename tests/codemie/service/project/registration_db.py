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

"""A disposable database holding the project and registration tables (EPMCDME-16440 registration tests)."""

from datetime import datetime

from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.compiler import compiles
from sqlmodel import Session, SQLModel

from codemie.core.models import Application
from codemie.rest_api.models.repository_project_registration import RepositoryProjectRegistration


@compiles(JSONB, "sqlite")
@compiles(ARRAY, "sqlite")
def _as_json(_type, compiler, **_kw):
    return "JSON"


TABLES = [Application.__table__, RepositoryProjectRegistration.__table__]


def create(engine) -> list:
    """Create both tables without their schema (a Postgres test creates it under the default schema too)."""
    saved = [(t, t.schema) for t in TABLES]
    for t in TABLES:
        t.schema = None
    SQLModel.metadata.create_all(engine, tables=TABLES)
    return saved


def restore(saved) -> None:
    for t, schema in saved:
        t.schema = schema


def add_project(session: Session, name: str, project_type: str = "shared", deleted: bool = False) -> None:
    now = datetime.now()
    session.add(
        Application(
            id=name,
            name=name,
            project_type=project_type,
            date=now,
            update_date=now,
            deleted_at=now if deleted else None,
        )
    )
    session.commit()
