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

"""add hide_tool_outputs to assistants and assistant_configurations

EPMCDME-15555: assistant-level flag that hides tool outputs from end users.
"""

import sqlalchemy as sa
from alembic import op

revision = 'a4c9e1b7d2f3'
down_revision = 'a9c4e7b2d1f3'
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ('assistants', 'assistant_configurations'):
        op.add_column(
            table,
            sa.Column('hide_tool_outputs', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        )


def downgrade() -> None:
    for table in ('assistant_configurations', 'assistants'):
        op.drop_column(table, 'hide_tool_outputs')
