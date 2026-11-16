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

"""Mark existing IDE work for one-time adoption of its unrecorded model.

Revision ID: d4f8a2c691be
Revises: ab20f38d912e

The backfill is the rollout boundary: later inserts, including history imports,
default to ineligible. Run this migration with the release that persists IDE models.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "d4f8a2c691be"
down_revision: str = "ab20f38d912e"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("legacy_ide_model_selection_pending", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    # Freeze the historical prefix here; migrations must not import runtime settings.
    ide_prefix = "__internal__IDE_"
    conversations = sa.table(
        "conversations",
        sa.column("initial_assistant_id", sa.String()),
        sa.column("llm_model", sa.String()),
        sa.column("history", postgresql.JSONB()),
        sa.column("import_source", sa.String()),
        sa.column("legacy_ide_model_selection_pending", sa.Boolean()),
    )
    op.execute(
        conversations.update()
        .where(
            sa.func.left(conversations.c.initial_assistant_id, len(ide_prefix)) == ide_prefix,
            conversations.c.llm_model.is_(None),
            conversations.c.import_source.is_(None),
            sa.case(
                (
                    sa.func.jsonb_typeof(conversations.c.history) == "array",
                    sa.func.jsonb_array_length(conversations.c.history) > 0,
                ),
                else_=sa.false(),
            ),
        )
        .values(legacy_ide_model_selection_pending=True)
    )


def downgrade() -> None:
    op.drop_column("conversations", "legacy_ide_model_selection_pending")
