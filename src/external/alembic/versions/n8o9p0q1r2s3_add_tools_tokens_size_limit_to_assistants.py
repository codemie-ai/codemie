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

"""add_tools_tokens_size_limit_to_assistants

Revision ID: n8o9p0q1r2s3
Revises: m7n8o9p0q1r2
Create Date: 2026-06-05 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'n8o9p0q1r2s3'
down_revision: Union[str, None] = 'm7n8o9p0q1r2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add per-assistant tool output token limit to assistants and assistant configurations."""
    op.add_column('assistants', sa.Column('tools_tokens_size_limit', sa.Integer(), nullable=True))
    op.add_column('assistant_configurations', sa.Column('tools_tokens_size_limit', sa.Integer(), nullable=True))


def downgrade() -> None:
    """Remove per-assistant tool output token limit from assistants and assistant configurations."""
    op.drop_column('assistant_configurations', 'tools_tokens_size_limit')
    op.drop_column('assistants', 'tools_tokens_size_limit')
