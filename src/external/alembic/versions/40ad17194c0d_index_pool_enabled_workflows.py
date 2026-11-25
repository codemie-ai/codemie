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

"""Index the workflows eligible for pool warmup.

Bound lock waits and statement runtime while preserving the migration transaction.
"""

import sqlalchemy as sa
from alembic import op

revision = "40ad17194c0d"
down_revision = "b4e17194c0de"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '30s'")
    op.create_index(
        "ix_workflows_pool_enabled",
        "workflows",
        ["id"],
        postgresql_where=sa.text(
            "(lower(pool_config ->> 'enabled') IN ('1', 'on', 't', 'true', 'y', 'yes') "
            "OR pool_config -> 'enabled' = '1'::jsonb)"
        ),
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '30s'")
    op.drop_index("ix_workflows_pool_enabled", table_name="workflows")
