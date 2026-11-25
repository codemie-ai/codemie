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

"""Index active application lookups and unassigned hourly project spend.

Keep the index builds in Alembic's transaction so its migration lock remains held.
Bound the time spent waiting for writers and running each DDL statement.
"""

import sqlalchemy as sa
from alembic import op

revision = "b4e17194c0de"
down_revision = "6158f5209f50"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '30s'")
    op.create_index(
        "ix_spend_hourly_project_residue",
        "litellm_spend_hourly",
        ["attr_source", "hour"],
        postgresql_include=["spend", "row_count"],
        postgresql_where=sa.text("attr_project IS NULL"),
    )
    op.create_index(
        "ix_applications_active_name_lower",
        "applications",
        [sa.text("lower(name)")],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '30s'")
    op.drop_index("ix_applications_active_name_lower", table_name="applications")
    op.drop_index("ix_spend_hourly_project_residue", table_name="litellm_spend_hourly")
