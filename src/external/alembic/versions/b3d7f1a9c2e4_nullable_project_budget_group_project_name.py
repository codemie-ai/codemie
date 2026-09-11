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

"""nullable_project_budget_group_project_name

Revision ID: b3d7f1a9c2e4
Revises: a8032409ff23
Create Date: 2026-09-10 13:10:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "b3d7f1a9c2e4"
down_revision: Union[str, Sequence[str], None] = "a8032409ff23"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "project_budget_groups",
        "project_name",
        existing_type=sa.String(length=100),
        nullable=True,
    )


def downgrade() -> None:
    op.execute("DELETE FROM project_budget_groups WHERE project_name IS NULL")
    op.alter_column(
        "project_budget_groups",
        "project_name",
        existing_type=sa.String(length=100),
        nullable=False,
    )
