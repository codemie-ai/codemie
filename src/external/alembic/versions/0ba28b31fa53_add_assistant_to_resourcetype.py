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

"""add assistant to resourcetype enum

Revision ID: 0ba28b31fa53
Revises: b4c8e1f2a9d3
Create Date: 2026-10-27 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
from alembic_postgresql_enum import TableReference

revision: str = "0ba28b31fa53"
down_revision: Union[str, None] = "b4c8e1f2a9d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.sync_enum_values(
        enum_schema="codemie",
        enum_name="resourcetype",
        new_values=["DATASOURCE", "ASSISTANT"],
        affected_columns=[
            TableReference(table_schema="codemie", table_name="permissions", column_name="resource_type")
        ],
    )


def downgrade() -> None:
    op.execute("DELETE FROM codemie.permissions WHERE resource_type = 'ASSISTANT'")
    op.sync_enum_values(
        enum_schema="codemie",
        enum_name="resourcetype",
        new_values=["DATASOURCE"],
        affected_columns=[
            TableReference(table_schema="codemie", table_name="permissions", column_name="resource_type")
        ],
    )
