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

"""IdP-sync reads of the user repository: deactivated users included, stable order (EPMCDME-16895)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.compiler import compiles
from sqlmodel import Session, SQLModel

from codemie.core.models import Application
from codemie.repository.user_repository import MembershipRow, user_repository
from codemie.rest_api.models.user_management import UserDB, UserProject


@compiles(ARRAY, "sqlite")
@compiles(JSONB, "sqlite")
def _as_json(_type, _compiler, **_kw):  # the Application model has PostgreSQL ARRAY and JSONB columns
    return "JSON"


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine, tables=[UserDB.__table__, UserProject.__table__, Application.__table__])
    with engine.begin() as connection:  # SQLite ignores the PostgreSQL partial predicate of this index
        connection.exec_driver_sql("DROP INDEX uix_user_projects_one_default")
    with Session(engine) as db:
        yield db


def _user(user_id: str, deleted: bool = False) -> UserDB:
    return UserDB(
        id=user_id,
        email=f"{user_id}@example.com",
        username=user_id,
        name=user_id,
        is_active=not deleted,
        deleted_at=datetime.now(UTC) if deleted else None,
    )


def test_deactivated_user_is_returned_by_get_by_id(session):
    session.add(_user("u1", deleted=True))
    session.commit()

    assert user_repository.get_by_id(session, "u1").id == "u1"
    assert user_repository.get_by_id(session, "missing") is None


def test_page_includes_deactivated_users_in_stable_id_order(session):
    for user_id in ["c", "a", "e", "b", "d"]:
        session.add(_user(user_id, deleted=user_id == "b"))
    session.commit()

    first, total = user_repository.list_page_including_deleted(session, 0, 2)
    second, _ = user_repository.list_page_including_deleted(session, 1, 2)
    third, _ = user_repository.list_page_including_deleted(session, 2, 2)

    assert total == 5
    assert [u.id for u in first + second + third] == ["a", "b", "c", "d", "e"]


def test_memberships_carry_personal_and_admin_flags_for_all_users(session):
    session.add_all([_user("u1"), _user("u2"), _user("u3")])
    session.add_all(
        [
            Application(id="shared-a", name="shared-a", project_type="shared"),
            Application(id="u1@example.com", name="u1@example.com", project_type="personal"),
            UserProject(user_id="u1", project_name="shared-a", is_project_admin=True),
            UserProject(user_id="u1", project_name="u1@example.com"),
            UserProject(user_id="u2", project_name="shared-a"),
        ]
    )
    session.commit()

    result = user_repository.fetch_memberships_with_personal(session, ["u1", "u2", "u3"])

    assert result["u1"] == [
        MembershipRow("shared-a", True, False),
        MembershipRow("u1@example.com", False, True),
    ]
    assert result["u2"] == [MembershipRow("shared-a", False, False)]
    assert result["u3"] == []


def test_memberships_for_no_users_is_empty(session):
    assert user_repository.fetch_memberships_with_personal(session, []) == {}


def test_lock_and_count_counts_only_active_non_deleted_admins(session):
    admin = _user("admin")
    admin.is_admin = True
    inactive = _user("inactive-admin")
    inactive.is_admin = True
    inactive.is_active = False
    deleted = _user("deleted-admin", deleted=True)
    deleted.is_admin = True
    session.add_all([admin, inactive, deleted, _user("plain")])
    session.commit()

    assert user_repository.lock_and_count_active_admins(session) == 1


def test_lock_and_count_statement_takes_row_locks_on_postgresql(session):
    statements = []
    original = session.exec

    def _spy(statement, *args, **kwargs):
        statements.append(statement)
        return original(statement, *args, **kwargs)

    session.exec = _spy
    user_repository.lock_and_count_active_admins(session)

    compiled = str(statements[0].compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE" in compiled
    assert "is_admin" in compiled and "is_active" in compiled and "deleted_at IS NULL" in compiled
