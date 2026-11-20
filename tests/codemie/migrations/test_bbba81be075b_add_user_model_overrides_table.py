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

"""The user_model_overrides migration: the foreign keys that tie an override to its user's membership.

The behaviour of these keys (cascade on membership removal and rename, deferred checks for the user id
remap) was run against PostgreSQL 17 when they were introduced; these tests pin the DDL the migration emits
and keep the model in step with it.
"""

from __future__ import annotations

import io

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext

from codemie.rest_api.models.user_management import UserProject  # noqa: F401  (registers user_projects)
from codemie.rest_api.models.user_model_override import UserModelOverride


def _module():
    from external.alembic.versions import bbba81be075b_add_user_model_overrides_table as module

    return module


def _emitted_sql(direction: str) -> str:
    buffer = io.StringIO()
    context = MigrationContext.configure(dialect_name="postgresql", opts={"as_sql": True, "output_buffer": buffer})
    with Operations.context(context):
        getattr(_module(), direction)()
    return " ".join(buffer.getvalue().split())


def _constraint(sql: str, name: str) -> str:
    """The clause of one named constraint inside the emitted CREATE TABLE."""
    marker = f"CONSTRAINT {name} "
    assert marker in sql, f"{name} not found in: {sql}"
    clause = sql.split(marker, 1)[1]
    for terminator in (", CONSTRAINT ", ", PRIMARY KEY", ", UNIQUE", ", CHECK"):
        clause = clause.split(terminator, 1)[0]
    return clause


def test_override_is_tied_to_the_users_membership_of_the_project():
    clause = _constraint(_emitted_sql("upgrade"), "fk_user_model_overrides_membership")

    assert "FOREIGN KEY(user_id, project_name) REFERENCES user_projects (user_id, project_name)" in clause
    assert "ON DELETE CASCADE" in clause
    assert "ON UPDATE CASCADE" in clause
    assert "DEFERRABLE INITIALLY DEFERRED" in clause


def test_user_foreign_key_is_deferrable_for_the_user_id_remap():
    """UserRemapService moves users.id and its children in one transaction, which needs deferred checks."""
    clause = _constraint(_emitted_sql("upgrade"), "fk_user_model_overrides_user_id")

    assert "REFERENCES users (id)" in clause
    assert "ON DELETE CASCADE" in clause
    assert "DEFERRABLE INITIALLY DEFERRED" in clause


def test_override_is_not_tied_to_the_applications_row():
    """A rename moves user_projects.project_name but not applications.id, so a key to applications would block it."""
    assert "applications" not in _emitted_sql("upgrade")


def test_downgrade_drops_the_table():
    assert "DROP TABLE user_model_overrides" in _emitted_sql("downgrade")


@pytest.mark.parametrize(
    "name, columns, target, on_update, on_delete",
    [
        ("fk_user_model_overrides_user_id", ["user_id"], ["users.id"], None, "CASCADE"),
        (
            "fk_user_model_overrides_membership",
            ["user_id", "project_name"],
            ["user_projects.user_id", "user_projects.project_name"],
            "CASCADE",
            "CASCADE",
        ),
    ],
)
def test_model_declares_the_same_keys_as_the_migration(name, columns, target, on_update, on_delete):
    constraint = next(c for c in UserModelOverride.__table__.foreign_key_constraints if c.name == name)

    assert [column.name for column in constraint.columns] == columns
    assert [element.target_fullname for element in constraint.elements] == target
    assert (constraint.onupdate, constraint.ondelete) == (on_update, on_delete)
    assert constraint.deferrable is True
    assert constraint.initially == "DEFERRED"


def test_model_has_no_other_foreign_key():
    names = {c.name for c in UserModelOverride.__table__.foreign_key_constraints}

    assert names == {"fk_user_model_overrides_user_id", "fk_user_model_overrides_membership"}
