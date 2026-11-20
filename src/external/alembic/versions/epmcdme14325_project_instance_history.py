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

"""add project-instance identity to projects and activity events

Revision ID: epmcdme14325
Revises: 223b83f7458b
Create Date: 2026-11-18

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "epmcdme14325"
down_revision: Union[str, None] = "223b83f7458b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "applications",
        sa.Column(
            "project_instance_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.execute("UPDATE applications SET project_instance_id = gen_random_uuid() WHERE project_instance_id IS NULL")
    op.alter_column(
        "applications",
        "project_instance_id",
        nullable=False,
        server_default=sa.text("gen_random_uuid()"),
    )
    op.create_unique_constraint(
        "uq_applications_project_instance_id",
        "applications",
        ["project_instance_id"],
    )

    op.add_column(
        "activity_events",
        sa.Column("project_instance_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        "ix_activity_events_project_instance_id_created_at",
        "activity_events",
        ["project_instance_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_activity_events_project_instance_id_created_at", table_name="activity_events")
    op.drop_column("activity_events", "project_instance_id")
    op.drop_constraint("uq_applications_project_instance_id", "applications", type_="unique")
    op.drop_column("applications", "project_instance_id")
