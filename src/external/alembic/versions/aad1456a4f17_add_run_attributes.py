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

"""add_run_attributes

Revision ID: aad1456a4f17
Revises: b5c6d7e8f901
Create Date: 2026-10-30 00:00:00.000000

EPMCDME-16484: immutable string attributes attached to a conversation or workflow execution.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "aad1456a4f17"
down_revision: Union[str, None] = "b5c6d7e8f901"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("conversations", sa.Column("attributes", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column(
        "workflow_executions", sa.Column("attributes", postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("workflow_executions", "attributes")
    op.drop_column("conversations", "attributes")
